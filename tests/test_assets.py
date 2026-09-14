import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import iop_assets


class Response(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


class AssetTests(unittest.TestCase):
    def test_manifest_rejects_foreign_origin_and_wrong_client_hash(self):
        files = {
            name: {"url": iop_assets.ALLOWED_PREFIX + "main/game-assets/" + name,
                   "size": 4, "sha256": hashlib.sha256(b"test").hexdigest()}
            for name in ("iop.exe", "ddraw.dll", "ddraw.ini")
        }
        files["iop.exe"]["sha256"] = iop_assets.CANONICAL_IOP_SHA256
        opener = lambda *a, **k: Response(json.dumps({"protocol": 1, "files": files}).encode())
        self.assertEqual(iop_assets.fetch_manifest(opener)["protocol"], 1)
        files["ddraw.dll"]["url"] = "https://example.com/ddraw.dll"
        with self.assertRaisesRegex(ValueError, "허용되지 않은"):
            iop_assets.fetch_manifest(opener)

    def test_graphics_installs_only_missing_files_and_checks_hash(self):
        dll, ini = b"dll", b"ini"
        manifest = {"files": {
            "ddraw.dll": {"url": iop_assets.ALLOWED_PREFIX + "dll", "size": len(dll), "sha256": hashlib.sha256(dll).hexdigest()},
            "ddraw.ini": {"url": iop_assets.ALLOWED_PREFIX + "ini", "size": len(ini), "sha256": hashlib.sha256(ini).hexdigest()},
        }}
        payloads = {manifest["files"]["ddraw.dll"]["url"]: dll,
                    manifest["files"]["ddraw.ini"]["url"]: ini}
        opener = lambda request, **k: Response(payloads[request.full_url])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ddraw.ini").write_bytes(b"custom")
            self.assertEqual(iop_assets.ensure_graphics(root, manifest, opener), ["ddraw.dll"])
            self.assertEqual((root / "ddraw.dll").read_bytes(), dll)
            self.assertEqual((root / "ddraw.ini").read_bytes(), b"custom")
            self.assertEqual(iop_assets.ensure_graphics(root, manifest, opener), [])

    def test_corrupt_client_is_restored_from_verified_asset(self):
        canonical = (Path(__file__).resolve().parents[1] / "game-assets/v0.019/iop.exe").read_bytes()
        item = {"url": iop_assets.ALLOWED_PREFIX + "main/game-assets/v0.019/iop.exe",
                "size": len(canonical), "sha256": hashlib.sha256(canonical).hexdigest()}
        opener = lambda request, **k: Response(canonical)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "iop.exe"
            damaged = bytearray(canonical); damaged[0x1000] ^= 1; path.write_bytes(damaged)
            self.assertTrue(iop_assets.ensure_iop(directory, {"files": {"iop.exe": item}}, opener))
            self.assertEqual(path.read_bytes(), canonical)
            backups = list(Path(directory).glob("iop.exe.before-github-sync-*.bak"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), damaged)
            self.assertFalse(iop_assets.ensure_iop(directory, {"files": {"iop.exe": item}}, opener))
