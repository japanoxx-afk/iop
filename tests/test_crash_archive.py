import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from unittest.mock import Mock
import threading
import zipfile
import iop_diagnostics as diag

class CrashArchive(unittest.TestCase):
    def test_nonzero_exit_automatically_publishes_report(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(diag,'_directory',None),patch.object(diag,'snapshot'),patch.object(diag,'background_snapshot'):
            diag.configure(tmp)
            process=Mock(pid=789,returncode=0xc0000005);process.poll.return_value=0xc0000005
            completed=threading.Event();reports=[]
            def report_ready(path):reports.append(path);completed.set()
            with patch.object(diag.subprocess,'Popen',return_value=process):
                diag.launch('iop.exe',tmp,on_report=report_ready)
                self.assertTrue(completed.wait(10))
            self.assertTrue(reports[0].is_file())

    def test_automatic_archive_contains_only_matching_dump(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dumps=root/'CrashDumps';dumps.mkdir()
            (dumps/'iop.exe.123.dmp').write_bytes(b'matching dump')
            (dumps/'unrelated.exe.456.dmp').write_bytes(b'other application')
            with patch.object(diag,'_directory',None),patch.dict('os.environ',{'LOCALAPPDATA':tmp}):
                diag.configure(root);diag.event('game_exited',exit_code=0xc0000005)
                archive=diag.archive_exit(root,123,0xc0000005)
                with zipfile.ZipFile(archive) as z:
                    self.assertIn('iop.exe.123.dmp',z.namelist())
                    self.assertNotIn('unrelated.exe.456.dmp',z.namelist())
                    self.assertEqual(json.loads(z.read('exit-123.json'))['exit_hex'],'0xC0000005')
