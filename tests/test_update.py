import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

import iop_update


class Response(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


class UpdateTests(unittest.TestCase):
    @staticmethod
    def pe(size=1_000_000):
        data = bytearray(size)
        data[:2] = b"MZ"
        struct.pack_into("<I", data, 0x3C, 0x80)
        data[0x80:0x84] = b"PE\0\0"
        return bytes(data)

    def test_manifest_restricts_origin_and_validates_fields(self):
        payload = {"version": "0.006", "url": iop_update.ALLOWED_PREFIX + "main/release/IOPLauncher.exe",
                   "sha256": "a" * 64, "size": 1_000_000}
        opener = lambda *a, **k: Response(json.dumps(payload).encode())
        self.assertEqual(iop_update.fetch_manifest(opener)["version"], "0.006")
        payload["url"] = "https://example.com/launcher.exe"
        with self.assertRaises(ValueError): iop_update.fetch_manifest(opener)
        with self.assertRaises(ValueError): iop_update.version_tuple("1.x")

    def test_download_hash_size_and_pe_validation(self):
        executable = self.pe()
        manifest = {"url": iop_update.ALLOWED_PREFIX + "main/release/IOPLauncher.exe",
                    "size": len(executable), "sha256": hashlib.sha256(executable).hexdigest()}
        with tempfile.TemporaryDirectory() as directory:
            path = iop_update.download(manifest, lambda *a, **k: Response(executable), directory)
            self.assertEqual(path.read_bytes(), executable)
            path.unlink()
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                iop_update.download(dict(manifest, sha256="0" * 64), lambda *a, **k: Response(executable), directory)
            self.assertFalse(list(Path(directory).iterdir()))

    def test_update_result_is_consumed_once(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "iop_update_result.json"
            marker.write_text('{"ok":true,"version":"0.006"}', encoding="utf-8")
            self.assertTrue(iop_update.consume_result(directory)["ok"])
            self.assertIsNone(iop_update.consume_result(directory))

    def test_pending_update_result_is_not_consumed(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "iop_update_result.json"
            marker.write_text('{"ok":null,"state":"pending"}', encoding="utf-8")
            self.assertIsNone(iop_update.consume_result(directory))
            self.assertTrue(marker.exists())

    def test_updater_uses_hidden_process_instead_of_detached(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch("iop_update.subprocess.Popen") as popen:
            root = Path(directory); downloaded = root / "new.exe"; destination = root / "launcher.exe"
            downloaded.write_bytes(b"new"); destination.write_bytes(b"old")
            def start(args,**kwargs):
                Path(args[args.index('-Ready')+1]).touch()
                process=mock.Mock();process.poll.return_value=None;return process
            popen.side_effect=start
            iop_update.schedule_replace(downloaded, destination, "0.008")
            flags = popen.call_args.kwargs["creationflags"]
            self.assertTrue(flags & iop_update.subprocess.CREATE_NO_WINDOW)
            self.assertFalse(flags & iop_update.subprocess.DETACHED_PROCESS)
            self.assertEqual(json.loads((root / "iop_update_result.json").read_text())["state"], "pending")
