import base64
import tempfile
import unittest

import iop_hills as hills
import iop_map_web as web
from test_map_web import game_dir


def tile(flags, colour):
    return bytes((flags & 255, flags >> 8)) + bytes([colour]) * 1024


GROUND, TOP, RIM, FACE, SIDE = range(5)
TILES = [tile(1, 10), tile(1, 20), tile(8, 30), tile(8, 40), tile(8, 50)]


class FakeMap:
    """A plain field with two box hills drawn the way the game draws them:
    one-cell rim above the top, one-cell sides, a three-cell face below."""
    theme = 0

    def __init__(self, boxes, w=48, h=40):
        self.width, self.height, self.tiles = w, h, TILES
        self.grid = [GROUND] * (w * h)
        for x0, y0, x1, y1 in boxes:
            for x in range(x0 - 1, x1 + 2):
                self.grid[x * h + y0 - 1] = RIM
                for y in range(y1 + 1, y1 + 4):
                    self.grid[x * h + y] = FACE
            for y in range(y0, y1 + 1):
                self.grid[(x0 - 1) * h + y] = self.grid[(x1 + 1) * h + y] = SIDE
                for x in range(x0, x1 + 1):
                    self.grid[x * h + y] = TOP


class Learning(unittest.TestCase):
    def setUp(self):
        self.doc = FakeMap([(5, 5, 18, 14), (26, 20, 40, 30)])

    def test_tops_are_found_and_low_ground_is_not(self):
        top, ramp = hills.top_mask(self.doc)
        h = self.doc.height
        self.assertTrue(top[10 * h + 10] and top[30 * h + 25])
        self.assertFalse(top[2 * h + 2] or top[22 * h + 36])
        self.assertFalse(any(top[i] for i, t in enumerate(self.doc.grid) if t != TOP))

    def test_learned_style_redraws_the_same_hill(self):
        result = hills.learn([('a.map', self.doc)])
        self.assertEqual(len(result['styles']), 1)
        style = result['styles'][0]
        raw = base64.b64decode(result['tiles'])
        colour = {i: raw[i * 1026 + 2] for i in range(result['tileCount'])}
        # a new, differently sized hill on an empty field
        w, h = 40, 40
        top = [10 <= x <= 25 and 12 <= y <= 20 for x in range(w) for y in range(h)]
        out = hills.generate(result, style['id'], w, h, top)
        at = lambda x, y: colour[out[x * h + y]]
        self.assertEqual(at(17, 16), 20)    # top
        self.assertEqual(at(17, 11), 30)    # rim above
        self.assertEqual(at(9, 16), 50)     # side
        self.assertEqual(at(17, 22), 40)    # face below
        self.assertIsNone(out[1 * h + 1])   # far away: untouched


class Api(unittest.TestCase):
    def test_map_carries_hill_mask_and_styles_endpoint_answers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = str(game_dir(folder))
            api = web.Api(lambda: root)
            data = api.get_map('base.map')
            mask = base64.b64decode(data['hillTop'])
            self.assertEqual(len(mask), data['width'] * data['height'])
            styles = api.hill_styles(data['theme'])
            self.assertIn('styles', styles)
            self.assertEqual(styles['theme'], data['theme'])


if __name__ == '__main__':
    unittest.main()
