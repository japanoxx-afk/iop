import unittest
from pathlib import Path
import tempfile

import iop_hill_vision as hill
import iop_hotkeys
from iop_network import patched_bytes as network_bytes


class HillVisionPatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = Path('game-assets/v0.019/iop.exe').read_bytes()

    def test_apply_normalize_and_idempotence(self):
        patched = hill.patched_bytes(self.original)
        self.assertNotEqual(patched, self.original)
        self.assertEqual(hill.patched_bytes(patched), patched)
        self.assertEqual(hill.normalize(patched), self.original)

    def test_calls_and_cave_are_in_range(self):
        patched = hill.patched_bytes(self.original)
        self.assertEqual(patched[hill.CAVE_OFFSET:hill.CAVE_OFFSET+hill.CAVE_SIZE], hill.CAVE_PATCH)
        for offset, expected in zip(hill.CALL_SITES, hill.PATCHED_CALLS):
            self.assertEqual(patched[offset:offset+5], expected)

    def test_state_byte_is_unused_writable_memory(self):
        # v0.026 wrote to read-only .text; v0.027-v0.029 used 0x4E2FF0 inside .data.
        import struct
        pe = struct.unpack_from('<I', self.original, 0x3C)[0]
        count = struct.unpack_from('<H', self.original, pe + 6)[0]
        table = pe + 24 + struct.unpack_from('<H', self.original, pe + 20)[0]
        sections = {self.original[o:o + 8].rstrip(bytes(1)): struct.unpack_from('<II', self.original, o + 8)
                    for o in range(table, table + 40 * count, 40)}
        size, rva = sections[b'.data']
        end = 0x400000 + rva + size
        self.assertTrue(end <= hill.STATE_VA < (end + 0xFFF) & ~0xFFF)
        self.assertNotIn((hill.STATE_VA).to_bytes(4, 'little'), self.original[0x1000:0xC8000])

    def _target(self, data, va):
        """Destination of the rel32 call/jmp at `va` in patched bytes."""
        import struct
        offset = va - 0x400000
        return va + 5 + struct.unpack_from('<i', data, offset + 1)[0]

    def test_fog_entries_tail_jump_with_original_arguments(self):
        # v0.026-v0.029 called 0x42D940 from inside the wrapper without
        # re-pushing x, y, sight, which tripped BBSIM ERROR #847234.
        patched = hill.patched_bytes(self.original)
        for name in ('wrapper', 'fixed'):
            code = hill.CAVE_CODE[hill.ENTRY[name]:]
            jmp = code.index(bytes([0xE9]))
            while self._target(patched, hill.CAVE_VA + hill.ENTRY[name] + jmp) != 0x42D940:
                jmp = code.index(bytes([0xE9]), jmp + 1)
            self.assertNotIn(bytes.fromhex('c20c00'), code[:jmp])
        self.assertEqual(self._target(patched, 0x42E0CC), hill.CAVE_VA + hill.ENTRY['fixed'])
        for site in (0x42E003, 0x42E072):
            self.assertEqual(self._target(patched, site), hill.CAVE_VA + hill.ENTRY['wrapper'])

    def test_high_ground_never_reaches_the_game_tile_grid(self):
        # v0.032 kept high ground as grid bit 0x100, which object footprints
        # (resources included) also set through 0x495C90, hiding resources.
        patched = hill.patched_bytes(self.original)
        setcell = hill.CAVE_VA + hill.ENTRY['setcell']
        self.assertEqual(self._target(patched, 0x480A8E), setcell)
        body = hill.CAVE_CODE[hill.ENTRY['setcell']:hill.ENTRY['loader']]
        self.assertIn(bytes.fromhex('8064240dfe'), body)        # and byte [esp+0xd], 0xfe
        self.assertEqual(self._target(patched, setcell + len(body) - 5), 0x495C60)
        for name in ('ishigh', 'reveal', 'wrapper'):
            block = hill.CAVE_CODE[hill.ENTRY[name]:]
            self.assertNotIn((0x495CF0).to_bytes(4, 'little'), block[:0x40])

    def test_bitmap_bounds_match_allocation(self):
        # index (y*19 << 8) | x for x, y < 256 must stay inside the 0x26000-byte block
        self.assertLessEqual((255 * 19 << 8 | 255) // 8, 0x26000 - 1)
        self.assertIn((0x12FFFF).to_bytes(4, 'little'), hill.CAVE_CODE)
        self.assertIn(bytes.fromhex('6800600200'), hill.CAVE_CODE)  # push 0x26000
        self.assertLessEqual(len(hill.CAVE_CODE), hill.CAVE_SIZE)

    def test_legacy_hooks_are_replaced(self):
        import subprocess
        for commit in ('2daad4c', 'c722ce6', '6808d51'):
            source = subprocess.run(['git', 'show', commit + ':iop_hill_vision.py'], capture_output=True).stdout
            if not source:
                self.skipTest('git history unavailable')
            namespace = {}
            exec(compile(source, commit, 'exec'), namespace)
            legacy = namespace['patched_bytes'](self.original)
            self.assertTrue(hill.is_legacy(legacy))
            self.assertEqual(hill.normalize(legacy), self.original)
            self.assertEqual(hill.patched_bytes(legacy), hill.patched_bytes(self.original))

    def test_unknown_reveal_code_is_rejected(self):
        damaged = bytearray(self.original)
        damaged[hill.REVEAL_OFFSET] ^= 1
        with self.assertRaises(ValueError):
            hill.patched_bytes(bytes(damaged))

    def test_patch_composes_with_hotkeys_and_network(self):
        patched = hill.patched_bytes(self.original)
        hotkeys = iop_hotkeys.patch_exe(patched, {})
        networked = network_bytes(hotkeys, '26.1.2.3')
        self.assertTrue(hill._state(networked)[1])

    def test_remove_restores_only_hill_hook(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'iop.exe'
            path.write_bytes(hill.patched_bytes(self.original))
            self.assertTrue(hill.remove(folder))
            self.assertEqual(path.read_bytes(),self.original)
            self.assertFalse(hill.remove(folder))
