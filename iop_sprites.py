"""Read object sprites from the game's data/Rsi.res for the map editor.

Formats (reverse engineered from the installed game files):

* data/Gvisual.res: u32 count, then `count` x (u32 object id, u32 offset).
  Each offset points at a record whose second u32 is the sprite id
  (0x01xxxxxx) in Rsi.res.  The resource object 0x0A200031 maps to sprite 871.
* data/Rsi.res: u32 count, then `count` x (u32 sprite id, u32 offset).
  A sprite starts with u32 id, u32 kind, u8, u32 box width, u32 box height,
  u32 directions, u32 frames, u32 total frames, a variable-length frame
  table, and then the frames.  A frame is u32 data size, u16 x, u16 y,
  u16 w, u16 h (position inside the box), u32, followed by row-wise RLE:
  a byte >= 0x80 copies (b & 0x7f) palette indices, a byte < 0x80 skips
  that many transparent pixels.

Only the first frame is decoded; the editor draws it as a still image.
"""
from pathlib import Path
import base64
import struct

RESOURCE_OBJECT = 0x0A200031
MAX_SIDE = 512


def _directory(data):
    count = struct.unpack_from('<I', data, 0)[0]
    if not 0 < count < 100000 or 4 + 8 * count > len(data):
        raise ValueError('directory header is invalid')
    return {struct.unpack_from('<I', data, 4 + 8 * i)[0]: struct.unpack_from('<I', data, 8 + 8 * i)[0]
            for i in range(count)}


def _rle(data, pos, w, h):
    pixels = bytearray(w * h)
    mask = bytearray(w * h)
    for y in range(h):
        x = 0
        row = y * w
        while x < w:
            if pos >= len(data):
                return None
            b = data[pos]
            pos += 1
            if b & 0x80:
                n = b & 0x7F
                if n == 0 or x + n > w or pos + n > len(data):
                    return None
                pixels[row + x:row + x + n] = data[pos:pos + n]
                mask[row + x:row + x + n] = b'\x01' * n
                pos += n
                x += n
            else:
                if b == 0 or x + b > w:
                    return None
                x += b
    return pixels, mask, pos


def decode_first_frame(blob):
    """Decode the first frame of one sprite record (bytes from its offset)."""
    if len(blob) < 29:
        raise ValueError('sprite record is too short')
    box_w, box_h = struct.unpack_from('<II', blob, 9)
    if not (0 < box_w <= MAX_SIDE and 0 < box_h <= MAX_SIDE):
        raise ValueError('sprite box is invalid')
    for pos in range(29, min(len(blob) - 16, 29 + 1024)):
        size = struct.unpack_from('<I', blob, pos)[0]
        x, y, w, h = struct.unpack_from('<4H', blob, pos + 4)
        if not (0 < w <= box_w and 0 < h <= box_h and x + w <= box_w and y + h <= box_h and size > h):
            continue
        decoded = _rle(blob, pos + 16, w, h)
        if decoded and decoded[2] - pos - 16 == size:
            return {'boxW': box_w, 'boxH': box_h, 'x': x, 'y': y, 'w': w, 'h': h,
                    'pixels': bytes(decoded[0]), 'mask': bytes(decoded[1])}
    raise ValueError('no decodable frame')


class SpriteLibrary:
    """Lazily maps object ids to decoded sprites for one game folder."""

    def __init__(self, game_dir):
        self.root = Path(game_dir) / 'data'
        self._visual = None
        self._rsi = None
        self._cache = {}

    def _visuals(self):
        if self._visual is None:
            data = (self.root / 'Gvisual.res').read_bytes()
            self._visual = {}
            for obj, offset in _directory(data).items():
                if offset + 8 <= len(data):
                    sprite = struct.unpack_from('<I', data, offset + 4)[0]
                    if sprite >> 24 == 1:
                        self._visual[obj] = sprite
        return self._visual

    def _sprite_blob(self, sprite):
        path = self.root / 'Rsi.res'
        with path.open('rb') as handle:
            if self._rsi is None:
                head = handle.read(4)
                count = struct.unpack('<I', head)[0]
                if not 0 < count < 100000:
                    raise ValueError('Rsi.res header is invalid')
                table = _directory(head + handle.read(8 * count))
                ends = sorted(set(table.values())) + [path.stat().st_size]
                self._rsi = {sid: (off, ends[ends.index(off) + 1]) for sid, off in table.items()}
            start, end = self._rsi[sprite]
            handle.seek(start)
            return handle.read(min(end - start, 4 * 1024 * 1024))

    def frame(self, obj):
        """Decoded first frame for an object id, or None if unavailable."""
        if obj not in self._cache:
            try:
                sprite = self._visuals().get(obj)
                self._cache[obj] = decode_first_frame(self._sprite_blob(sprite)) if sprite else None
            except (OSError, KeyError, ValueError, struct.error):
                self._cache[obj] = None
        return self._cache[obj]

    def as_json(self, objects):
        result = {}
        for obj in sorted(set(objects)):
            frame = self.frame(obj)
            if frame:
                result['%08X' % obj] = {k: (base64.b64encode(v).decode('ascii') if isinstance(v, bytes) else v)
                                        for k, v in frame.items()}
        return result
