import base64
import os
from pathlib import Path
import tempfile
import unittest

import iop_map_web as web
import iop_stamps as st
from test_map_web import game_dir

REAL_GAME = Path(os.environ.get('IOP_GAME_DIR', r'C:/Users/seo/Downloads/DGGL/Games/IOP_Win'))


def tile(flags, colour=0):
    return bytes((flags & 255, flags >> 8)) + bytes([colour]) * 1024


def grid(rows):
    """rows of '#'(cliff) '.'(ground) -> (w, h, flags) column-major."""
    h, w = len(rows), len(rows[0])
    return w, h, [8 if rows[y][x] == '#' else 1 for x in range(w) for y in range(h)]


def at(w, h, mask):
    return [''.join('x' if mask[x * h + y] else '.' for x in range(w)) for y in range(h)]


class Masks(unittest.TestCase):
    def test_closed_hill_top_is_found(self):
        w, h, flags = grid(['.......',
                            '.#####.',
                            '.#...#.',
                            '.#...#.',
                            '.#####.',
                            '.......'])
        mask = st.plateau_mask(w, h, flags, st.ramp_mask(w, h, flags))
        self.assertEqual(at(w, h, mask), ['.......', '.......', '..xxx..', '..xxx..', '.......', '.......'])

    def test_open_plateau_above_cliff_faces_is_found(self):
        # cliff faces hang below the top, as in the game's art
        w, h, flags = grid(['....#.',
                            '....#.',
                            '....#.',
                            '#####.',
                            '......'])
        mask = st.plateau_mask(w, h, flags, [False] * (w * h))
        self.assertEqual(at(w, h, mask)[:3], ['xxxx..'] * 3)
        self.assertFalse(any(mask[x * h + 4] for x in range(w)))

    def test_gap_in_cliff_is_a_ramp_and_plain_ground_is_skipped(self):
        w, h, flags = grid(['........',
                            '###..###',
                            '........',
                            '........',
                            '........'])
        ramp = st.ramp_mask(w, h, flags)
        self.assertTrue(ramp[3 * h + 1] and ramp[4 * h + 1])
        skip = st.skip_mask(w, h, flags, ramp)
        self.assertTrue(skip[0 * h + 4])           # far from the cliff
        self.assertFalse(skip[0 * h + 2])          # cliff foot stays


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = game_dir(self.tmp.name + '/game')
        self.api = web.Api(lambda: str(self.root), stamp_dir=self.tmp.name + '/stamps')

    def tearDown(self):
        self.tmp.cleanup()

    def stamp(self, **extra):
        tiles = tile(1, 3) + tile(8, 4)
        data = {'name': '테스트', 'kind': 'high', 'theme': 0, 'w': 3, 'h': 3,
                'ids': [1, 1, 1, 1, 0, 1, 1, 1, 1], 'tiles': base64.b64encode(tiles).decode()}
        data.update(extra)
        return data

    def test_save_marks_closed_top_high_lists_and_deletes(self):
        saved = self.api.stamps.save(self.stamp())
        raw = base64.b64decode(saved['tiles'])
        centre = saved['ids'][4]
        self.assertTrue(raw[centre * 1026] & st.HIGH_GROUND_FLAG)
        listed = self.api.stamps.list(0)['stamps']
        self.assertEqual([s['id'] for s in listed if not s['builtin']], [saved['id']])
        self.assertEqual(self.api.stamps.list(1)['stamps'], [])
        self.api.stamps.delete(saved['id'])
        self.assertEqual([s for s in self.api.stamps.list(0)['stamps'] if not s['builtin']], [])

    def test_low_stamp_is_not_marked_high(self):
        saved = self.api.stamps.save(self.stamp(kind='low'))
        raw = base64.b64decode(saved['tiles'])
        self.assertFalse(any(raw[i * 1026] & st.HIGH_GROUND_FLAG for i in saved['ids']))

    def test_invalid_stamps_and_ids_are_rejected(self):
        for bad in ({'kind': 'x'}, {'name': ''}, {'w': 65}, {'ids': [5] * 9}, {'tiles': 'AAAA'}):
            with self.assertRaises((ValueError, KeyError), msg=bad):
                self.api.stamps.save(self.stamp(**bad))
        for bad in ('../x', 'b-0123456789', 'u-zz'):
            with self.assertRaises(ValueError):
                self.api.stamps.delete(bad)


@unittest.skipUnless((REAL_GAME / 'map/multi/treebrownhill.map').is_file(), 'installed game maps not available')
class RealBuiltins(unittest.TestCase):
    def test_builtin_base_has_plateau_cliffs_and_ramp(self):
        api = web.Api(lambda: str(REAL_GAME))
        stamps = {s['name']: s for s in api.stamps.list(0)['stamps']}
        base = stamps['모서리 기지 (오른쪽·아래 절벽)']
        raw = base64.b64decode(base['tiles'])
        flags = [raw[i * 1026] | raw[i * 1026 + 1] << 8 for i in base['ids']]
        self.assertTrue(flags[0] & st.HIGH_GROUND_FLAG)                     # top-left: plateau
        self.assertTrue(any(f & 8 for f in flags))                          # cliffs
        self.assertTrue(any(base['ramp']))                                  # corner ramp
        self.assertFalse(flags[base['w'] * base['h'] - 1] & st.HIGH_GROUND_FLAG)  # low ground corner
        self.assertTrue(all(s['theme'] == 0 for s in stamps.values()))


if __name__ == '__main__':
    unittest.main()
