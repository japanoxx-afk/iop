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

    def test_wrapper_repushes_fog_arguments_and_pops_them(self):
        # The legacy wrapper called 0x42D940 without re-pushing its three
        # arguments, which turned tile y into the sight index (BBSIM #847234).
        self.assertEqual(hill.WRAPPER.count(bytes.fromhex('ff74240c')), 3)
        self.assertTrue(hill.WRAPPER.endswith(bytes.fromhex('c20c00')))

    def test_legacy_hooks_are_replaced(self):
        import subprocess
        for commit in ('2daad4c', 'c722ce6'):
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
