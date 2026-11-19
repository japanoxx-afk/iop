from pathlib import Path
import tempfile
import unittest

import iop_assets


def broken_opener(*args, **kwargs):
    raise FileNotFoundError(2, "No such file or directory")


class OfflineAssetTests(unittest.TestCase):
    def test_graphics_install_from_bundle_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "iop.exe").write_bytes(b"local")
            result = iop_assets.ensure_game_files(root, broken_opener)
            self.assertEqual(result["graphics_installed"], ["ddraw.dll", "ddraw.ini"])
            self.assertTrue((root / "ddraw.dll").is_file())
            self.assertFalse(result["iop_restored"])
            self.assertTrue(result["errors"])
            self.assertEqual((root / "iop.exe").read_bytes(), b"local")

    def test_missing_iop_without_network_reports_clear_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "iop.exe가 없고"):
                iop_assets.ensure_game_files(Path(directory), broken_opener)


if __name__ == "__main__":
    unittest.main()
