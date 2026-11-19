import unittest

from iop_sync import compare


class HillVisionSyncTests(unittest.TestCase):
    def test_peer_without_hill_vision_is_blocked_and_marker_not_counted(self):
        files = {'data/gweapon.res': 'a', 'iop.exe': 'b'}
        new = {'protocol': 1, 'files': dict(files, **{'feature/hill_vision': '2'})}
        old = {'protocol': 1, 'files': dict(files)}
        self.assertEqual(compare(new, new), 2)
        with self.assertRaisesRegex(ValueError, 'feature/hill_vision'):
            compare(new, old)
        with self.assertRaisesRegex(ValueError, 'feature/hill_vision'):
            compare(old, new)


if __name__ == '__main__':
    unittest.main()
