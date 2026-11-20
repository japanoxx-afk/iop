import os
from pathlib import Path
import struct
import tempfile
import unittest

import iop_sprites as sprites

REAL_GAME = Path(os.environ.get('IOP_GAME_DIR', r'C:/Users/seo/Downloads/DGGL/Games/IOP_Win'))


def sprite_record(sprite_id, box, frame_xy, rows, prefix=b'\x82\x00\x00\x00\x00\xff'):
    """Build a one-frame Rsi.res record in the game's layout."""
    w = len(rows[0])
    rle = bytearray()
    for row in rows:
        x = 0
        while x < w:
            if row[x] is None:
                n = 1
                while x + n < w and row[x + n] is None:
                    n += 1
                rle.append(n)
            else:
                n = 1
                while x + n < w and row[x + n] is not None:
                    n += 1
                rle.append(0x80 | n)
                rle += bytes(row[x:x + n])
            x += n
    head = struct.pack('<IIBIIIII', sprite_id, 6, 1, box[0], box[1], 1, 1, 1) + prefix
    frame = struct.pack('<I4HI', len(rle), frame_xy[0], frame_xy[1], w, len(rows), 0) + bytes(rle)
    return head + frame


def write_game(folder, records, visuals):
    data = Path(folder) / 'data'
    data.mkdir()
    blob = struct.pack('<I', len(records))
    offset = 4 + 8 * len(records)
    body = b''
    for sid, rec in records.items():
        blob += struct.pack('<II', sid, offset + len(body))
        body += rec
    (data / 'Rsi.res').write_bytes(blob + body)
    gv = struct.pack('<I', len(visuals))
    offset = 4 + 8 * len(visuals)
    body = b''
    for obj, sid in visuals.items():
        gv += struct.pack('<II', obj, offset + len(body))
        body += struct.pack('<IIII', obj, sid, 3, 0)
    (data / 'Gvisual.res').write_bytes(gv + body)


class SpriteDecoding(unittest.TestCase):
    rows = [[None, 5, 6, None], [7, None, None, 8], [None, None, 9, None]]

    def test_first_frame_round_trip(self):
        frame = sprites.decode_first_frame(sprite_record(0x01000001, (32, 32), (3, 4), self.rows))
        self.assertEqual((frame['boxW'], frame['boxH'], frame['x'], frame['y'], frame['w'], frame['h']), (32, 32, 3, 4, 4, 3))
        self.assertEqual(frame['pixels'], bytes([0, 5, 6, 0, 7, 0, 0, 8, 0, 0, 9, 0]))
        self.assertEqual(frame['mask'], bytes([0, 1, 1, 0, 1, 0, 0, 1, 0, 0, 1, 0]))

    def test_garbage_is_rejected(self):
        with self.assertRaises(ValueError):
            sprites.decode_first_frame(bytes(64))

    def test_library_maps_objects_and_tolerates_missing_files(self):
        with tempfile.TemporaryDirectory() as folder:
            write_game(folder, {0x01000367: sprite_record(0x01000367, (96, 96), (1, 15), self.rows)},
                       {sprites.RESOURCE_OBJECT: 0x01000367})
            lib = sprites.SpriteLibrary(folder)
            out = lib.as_json([sprites.RESOURCE_OBJECT, 0x1B000001])
            self.assertEqual(list(out), ['0A200031'])
            self.assertEqual(out['0A200031']['boxW'], 96)
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(sprites.SpriteLibrary(folder).as_json([sprites.RESOURCE_OBJECT]), {})


@unittest.skipUnless((REAL_GAME / 'data/Rsi.res').is_file(), 'installed game data not available')
class RealSprites(unittest.TestCase):
    def test_resource_and_tree_sprites_decode(self):
        lib = sprites.SpriteLibrary(REAL_GAME)
        res = lib.frame(sprites.RESOURCE_OBJECT)
        self.assertEqual((res['boxW'], res['boxH'], res['w'], res['h']), (96, 96, 94, 66))
        for n in range(1, 0x16):
            self.assertIsNotNone(lib.frame(0x1B000000 | n), hex(n))


if __name__ == '__main__':
    unittest.main()
