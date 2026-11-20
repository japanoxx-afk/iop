"""Run the patched fog reveal 0x42D940 in an emulator against a ridge.

Checks the v0.038 line-of-sight rule: a low-ground observer reveals neither
high cells nor cells behind them; with the restriction off everything in
range is revealed as in the original game.
"""
from pathlib import Path
import struct
import unittest

import iop_hill_vision as hill

try:
    import unicorn
    from unicorn import x86_const as X
except ImportError:  # dev-only dependency
    unicorn = None

ORIGINAL = Path(__file__).resolve().parents[1] / 'game-assets/v0.019/iop.exe'
FOG, COLS, CELLS, BITMAP, STACK = 0x10000000, 0x10001000, 0x10010000, 0x10100000, 0x10400000
W = H = 40


@unittest.skipUnless(unicorn and ORIGINAL.is_file(), 'unicorn or original iop.exe not available')
class LineOfSight(unittest.TestCase):
    def run_reveal(self, observer, high, restricted=True, sight=5):
        data = hill.patched_bytes(ORIGINAL.read_bytes())
        uc = unicorn.Uc(unicorn.UC_ARCH_X86, unicorn.UC_MODE_32)
        pe = struct.unpack_from('<I', data, 0x3C)[0]
        count = struct.unpack_from('<H', data, pe + 6)[0]
        table = pe + 24 + struct.unpack_from('<H', data, pe + 20)[0]
        uc.mem_map(0x400000, 0x384000)
        uc.mem_write(0x400000, data[:0x1000])
        for o in range(table, table + 40 * count, 40):
            vsize, rva, rsize, raw = struct.unpack_from('<IIII', data, o + 8)
            uc.mem_write(0x400000 + rva, data[raw:raw + rsize])
        for base in (FOG, BITMAP, STACK - 0x10000):
            uc.mem_map(base, 0x100000 if base != BITMAP else 0x30000)
        uc.mem_write(FOG + 0x20, struct.pack('<III', W, H, COLS))
        uc.mem_write(COLS, b''.join(struct.pack('<I', CELLS + x * H * 19) for x in range(W)))
        bits = bytearray(0x26000)
        for x, y in high:
            i = (y * 19 << 8) | x
            bits[i >> 3] |= 1 << (i & 7)
        uc.mem_write(BITMAP, bytes(bits))
        ox, oy = observer
        uc.mem_write(hill.BITMAP_PTR_VA, struct.pack('<I', BITMAP))
        uc.mem_write(hill.STATE_VA, bytes([1 if restricted else 0]))
        uc.mem_write(hill.OBSERVER_X_VA, struct.pack('<II', ox, oy))
        sp = STACK - 0x100
        uc.mem_write(sp, struct.pack('<IIII', 0xDEAD0000, ox, oy, sight))
        uc.reg_write(X.UC_X86_REG_ESP, sp)
        uc.reg_write(X.UC_X86_REG_ECX, FOG)
        uc.mem_map(0xDEAD0000, 0x1000)
        uc.emu_start(0x42D940, 0xDEAD0000, count=2_000_000)
        cells = uc.mem_read(CELLS, W * H * 19)
        return {(x, y): cells[(x * H + y) * 19] for x in range(W) for y in range(H)}

    # vertical ridge: high cells at x = 22..23
    RIDGE = {(x, y) for x in (22, 23) for y in range(H)}

    def test_unrestricted_reveal_ignores_high_ground(self):
        fog = self.run_reveal((19, 20), self.RIDGE, restricted=False)
        self.assertTrue(fog[(22, 20)] and fog[(25, 20)])

    def test_ridge_hides_its_top_and_what_lies_behind_it(self):
        fog = self.run_reveal((19, 20), self.RIDGE)
        self.assertTrue(fog[(19, 20)] and fog[(21, 20)] and fog[(17, 20)])
        self.assertTrue(fog[(21, 18)])                  # low ground beside the ridge foot
        for cell in ((22, 20), (23, 20), (24, 20), (25, 20), (24, 18)):
            self.assertEqual(fog[cell], 0, cell)

    def test_open_ground_is_unaffected(self):
        fog = self.run_reveal((10, 20), {(30, 5)})
        reference = self.run_reveal((10, 20), {(30, 5)}, restricted=False)
        self.assertEqual(fog, reference)


if __name__ == '__main__':
    unittest.main()
