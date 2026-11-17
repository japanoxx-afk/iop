import unittest
from pathlib import Path

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

    def test_runtime_flag_is_in_writable_data_not_code(self):
        # The v0.026 regression wrote this flag to read/execute-only .text.
        self.assertTrue(0x4D1000 <= hill.FLAG_VA < 0x4E3000)
        self.assertNotEqual(hill.FLAG_VA, 0x4C7FF0)

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
