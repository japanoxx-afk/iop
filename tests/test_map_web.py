import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error
import urllib.request

import iop_map_web as web
from iop_map_format import MapDocument
from test_map_format import fixture

REAL_GAME = Path(os.environ.get('IOP_GAME_DIR', r'C:/Users/seo/Downloads/DGGL/Games/IOP_Win'))


def game_dir(folder):
    root = Path(folder)
    (root / 'map' / 'multi').mkdir(parents=True)
    (root / 'map' / 'mappal').mkdir(parents=True)
    (root / 'map' / 'mappal' / 'a.pal').write_bytes(bytes(range(256)) * 3)
    (root / 'map' / 'multi' / 'base.map').write_bytes(fixture())
    return root


class MapWebApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = game_dir(self.tmp.name)
        self.saved = []
        self.categories = {}
        self.api = web.Api(lambda: str(self.root), self.categories, lambda: self.saved.append(dict(self.categories)))

    def tearDown(self):
        self.tmp.cleanup()

    def test_unchanged_round_trip_is_byte_identical(self):
        data = self.api.get_map('base.map')
        self.assertEqual(data['sprites'], {})  # no data/Rsi.res: page falls back to simple marks
        self.api.put_map('copy.map', data)
        self.assertEqual((self.root / 'map/multi/copy.map').read_bytes(), fixture())

    def test_overwrite_needs_flag_keeps_backup_and_detects_external_change(self):
        data = self.api.get_map('base.map')
        with self.assertRaises(FileExistsError):
            self.api.put_map('base.map', data)
        data['starts'][0] = [1, 1]
        result = self.api.put_map('base.map', data, overwrite=True)
        self.assertEqual((self.root / 'map/multi/base.map.bak').read_bytes(), fixture())
        self.assertEqual(MapDocument.load(self.root / 'map/multi/base.map').starts[0], (1, 1))
        self.assertEqual(result['backup'], 'base.map.bak')
        stale = dict(data)  # still carries the hash from before the first save
        with self.assertRaisesRegex(ValueError, '다른 프로그램'):
            self.api.put_map('base.map', stale, overwrite=True)

    def test_names_outside_map_multi_are_rejected(self):
        for name in ('../x.map', '..\\x.map', 'C:/x.map', 'sub/x.map', 'x.txt', '.map', ''):
            with self.assertRaises(ValueError, msg=name):
                self.api.get_map(name)

    def test_invalid_edits_are_rejected_before_touching_disk(self):
        data = self.api.get_map('base.map')
        data['resources'].append([99, 99])
        with self.assertRaises(ValueError):
            self.api.put_map('bad.map', data)
        self.assertFalse((self.root / 'map/multi/bad.map').exists())

    def test_new_map_size_players_and_starts(self):
        data = self.api.new({'base': 'base.map', 'width': 64, 'height': 48, 'players': 4})
        self.assertEqual((data['width'], data['height'], data['players'], len(data['starts'])), (64, 48, 4, 4))
        self.assertTrue(all(0 <= x < 64 and 0 <= y < 48 for x, y in data['starts']))
        self.api.put_map('fresh.map', data)
        doc = MapDocument.load(self.root / 'map/multi/fresh.map')
        self.assertEqual((doc.width, doc.height, doc.players, doc.resources), (64, 48, 4, []))
        with self.assertRaises(ValueError):
            self.api.new({'base': 'base.map', 'width': 300, 'height': 64, 'players': 2})

    def test_new_map_by_terrain_takes_tiles_from_that_terrain(self):
        terrain = self.api.list_maps()['maps'][0]['terrain']
        data = self.api.new({'terrain': terrain, 'width': 64, 'height': 64, 'players': 2})
        self.assertEqual(data['terrain'], terrain)
        doc = MapDocument.load(self.root / 'map/multi/base.map')
        tiles = web.unb64(data['tiles'])
        grid = set(web.struct.unpack(f'<{64 * 64}H', web.unb64(data['grid'])))
        self.assertEqual(grid, {0})  # one plain ground everywhere
        self.assertEqual(tiles[:1026], doc.tiles[doc.ground_tile()])
        self.assertEqual(set(tiles[i:i + 1026] for i in range(0, len(tiles), 1026)), set(doc.tiles))
        with self.assertRaises(ValueError):
            self.api.new({'terrain': 'moon', 'width': 64, 'height': 64, 'players': 2})
        other = next(k for k in web.TERRAINS if k != terrain)
        with self.assertRaises(ValueError):
            self.api.new({'terrain': other, 'width': 64, 'height': 64, 'players': 2})

    def test_category_is_remembered_per_tile_graphic(self):
        data = self.api.get_map('base.map')
        tile = web.b64(web.unb64(data['tiles'])[:1026])
        self.api.set_category({'theme': data['theme'], 'tile': tile, 'category': '언덕'})
        self.assertEqual(self.api.get_map('base.map')['categories'][0], '언덕')
        self.assertEqual(len(self.saved), 1)
        with self.assertRaises(ValueError):
            self.api.set_category({'theme': 0, 'tile': tile, 'category': 'x'})


class MapWebHttp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = game_dir(self.tmp.name)
        self.server = web.EditorServer(web.Api(lambda: str(root)), port=47290)
        self.url = self.server.start().rsplit('/', 1)[0]

    def tearDown(self):
        self.server.stop()
        self.tmp.cleanup()

    def request(self, path, token=None, host=None):
        req = urllib.request.Request(self.url + path)
        if token:
            req.add_header('X-IOP-Token', token)
        if host:
            req.add_header('Host', host)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_page_carries_token_and_api_requires_it(self):
        status, page = self.request('/' + web.PAGE)
        self.assertEqual(status, 200)
        self.assertIn(self.server.token.encode(), page)
        self.assertNotIn(web.TOKEN_MARK.encode(), page)
        self.assertEqual(self.request('/api/maps')[0], 403)
        self.assertEqual(self.request('/api/maps', 'wrong')[0], 403)
        status, body = self.request('/api/maps', self.server.token)
        self.assertEqual(status, 200)
        self.assertEqual([m['name'] for m in json.loads(body)['maps']], ['base.map'])

    def test_foreign_host_header_is_refused(self):
        # DNS-rebinding style request: right port, other host name
        self.assertEqual(self.request('/api/maps', self.server.token, host='evil.example')[0], 403)
        self.assertEqual(self.request('/' + web.PAGE, host='evil.example')[0], 403)


@unittest.skipUnless((REAL_GAME / 'map/multi').is_dir(), 'installed game maps not available')
class RealMaps(unittest.TestCase):
    def test_every_installed_map_round_trips(self):
        api = web.Api(lambda: str(REAL_GAME))
        for item in api.list_maps()['maps']:
            with self.subTest(item['name']):
                data = api.get_map(item['name'])
                doc = web.json_to_doc(data)
                self.assertEqual(doc.encode(), (REAL_GAME / 'map/multi' / item['name']).read_bytes())


if __name__ == '__main__':
    unittest.main()
