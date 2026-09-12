import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import iop_diagnostics as diag

class DiagnosticsTests(unittest.TestCase):
    def tearDown(self): diag._directory=None
    def test_report_contains_config_and_error_without_accounts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); game=root/'game'; game.mkdir()
            (game/'ddraw.ini').write_text('[iop]\nwindowed=true\n')
            (game/'accounts.sqlite3').write_bytes(b'not-for-report')
            directory=diag.configure(root)
            diag.event('launch_error',traceback='test error')
            with patch.object(diag,'powershell',return_value={'exit_code':0,'output':'test','error':''}):
                report=diag.report(game)
            with zipfile.ZipFile(report) as z:
                self.assertNotIn('accounts.sqlite3',z.namelist())
                records=[json.loads(x) for x in z.read('launcher.jsonl').decode('utf-8').splitlines()]
            env=next(r for r in records if r['event']=='game_environment')
            self.assertIn('windowed=true',env['configs']['ddraw.ini'])
            self.assertTrue(any(r['event']=='launch_error' for r in records))
    def test_old_fullscreen_setting_does_not_select_fullscreen(self):
        import iop_launcher
        with patch.object(iop_launcher,'load_cfg',return_value={'mode':'full','game_dir':''}), patch.object(diag,'configure',return_value=Path(tempfile.gettempdir())):
            app=iop_launcher.Launcher()
            try:
                app.withdraw(); app.update_idletasks()
                self.assertEqual(app.mode_var.get(),'window')
            finally:
                app.destroy()
                del app
                import gc
                gc.collect()
