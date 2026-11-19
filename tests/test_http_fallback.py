from types import SimpleNamespace
from unittest.mock import patch
import unittest
import urllib.error
import urllib.request

import iop_http

URL = iop_http.ALLOWED_PREFIX + "main/update-manifest.json"


class HttpFallbackTests(unittest.TestCase):
    def test_ssl_context_failure_falls_back_to_windows_fetch(self):
        with patch("iop_http.urllib.request.urlopen", side_effect=FileNotFoundError(2, "No such file or directory")), \
             patch("iop_http.powershell_fetch", return_value=b'{"ok":1}') as fetch:
            with iop_http.urlopen(urllib.request.Request(URL), timeout=10) as response:
                self.assertEqual(response.read(), b'{"ok":1}')
        fetch.assert_called_once_with(URL, 10)

    def test_http_error_is_not_retried(self):
        error = urllib.error.HTTPError(URL, 404, "missing", {}, None)
        with patch("iop_http.urllib.request.urlopen", side_effect=error), \
             patch("iop_http.powershell_fetch") as fetch:
            with self.assertRaises(urllib.error.HTTPError):
                iop_http.urlopen(urllib.request.Request(URL))
        fetch.assert_not_called()

    def test_fallback_refuses_foreign_urls(self):
        with self.assertRaisesRegex(ValueError, "허용되지 않은"):
            iop_http.powershell_fetch("https://example.com/x")

    def test_url_is_passed_by_environment_not_script(self):
        seen = {}
        def runner(args, **kwargs):
            seen.update(kwargs["env"]); seen["args"] = args
            from pathlib import Path
            Path(kwargs["env"]["IOP_FETCH_OUT"]).write_bytes(b"data")
            return SimpleNamespace(returncode=0, stderr=b"")
        self.assertEqual(iop_http.powershell_fetch(URL, runner=runner), b"data")
        self.assertEqual(seen["IOP_FETCH_URL"], URL)
        self.assertNotIn(URL, " ".join(seen["args"]))


if __name__ == "__main__":
    unittest.main()
