"""Real Windows replacement/restart test using a disposable .NET executable."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

CSC=Path(r'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe')

@unittest.skipUnless(os.name=='nt' and CSC.exists(),'Windows compiler required')
class WindowsUpdate(unittest.TestCase):
    def test_existing_executable_replaced_and_restarted(self):
        with tempfile.TemporaryDirectory(prefix='IOP update 한글 [test] ') as tmp:
            root=Path(tmp);source=root/'new.exe';dest=root/'launcher.exe'
            cs=root/'test.cs'
            cs.write_text('using System; using System.IO; class T { static void Main() { File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"restarted.txt"),"new version"); } }')
            subprocess.run([str(CSC),'/nologo','/target:winexe','/out:'+str(source),str(cs)],check=True,capture_output=True)
            expected=source.read_bytes();dest.write_bytes(b'previous executable')
            code='from iop_update import schedule_replace; import sys; schedule_replace(sys.argv[1],sys.argv[2],"0.017")'
            subprocess.run([sys.executable,'-c',code,str(source),str(dest)],check=True,timeout=20)
            deadline=time.monotonic()+20
            while not (root/'restarted.txt').exists() and time.monotonic()<deadline:time.sleep(.1)
            self.assertTrue((root/'restarted.txt').exists(),(root/'iop_update_result.json').read_text(encoding='utf-8-sig'))
            self.assertEqual(dest.read_bytes(),expected)
            self.assertEqual(next(root.glob('*.update-backup-*')).read_bytes(),b'previous executable')
            self.assertTrue(json.loads((root/'iop_update_result.json').read_text(encoding='utf-8-sig'))['ok'])
            # Let the helper finish its child startup check before deleting the temp directory.
            deadline=time.monotonic()+10
            while source.exists() and time.monotonic()<deadline:time.sleep(.1)
