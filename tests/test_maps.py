import tempfile
import unittest
from pathlib import Path

import iop_maps
from tests.test_map_format import fixture


class MapTests(unittest.TestCase):
    def test_reads_dimensions_players_and_last_terrain_layer(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.map"
            terrain = bytes(range(6))
            path.write_bytes(fixture())
            info = iop_maps.read_map(path)
            self.assertEqual((info.width, info.height, info.players), (3, 2, 2))
            self.assertEqual(info.theme_id, 0)
            self.assertEqual(info.terrain, terrain)

    def test_ppm_preview_nearest_neighbour(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.map"
            info = iop_maps.MapInfo(path,2,1,2,0,'',0,0,bytes((1,2)))
            palette = [(0, 0, 0)] * 256; palette[1] = (10, 20, 30); palette[2] = (40, 50, 60)
            ppm = iop_maps.ppm_preview(info, palette, 2)
            self.assertTrue(ppm.startswith(b"P6\n4 2\n255\n"))
            self.assertEqual(ppm.split(b"\n", 3)[3], bytes((10,20,30))*2 + bytes((40,50,60))*2 + bytes((10,20,30))*2 + bytes((40,50,60))*2)


if __name__ == "__main__": unittest.main()
