import tempfile
import unittest
from pathlib import Path
import shutil
from iop_stability import apply,normalize,OFFSET,ORIGINAL,PATCH
from iop_network import patched_bytes
from iop_hotkeys import patch_exe

class RenderSafety(unittest.TestCase):
    def test_render_patch_preserved_by_network_and_hotkeys(self):
        source=Path(r'C:\Users\seo\Downloads\DGGL\Games\IOP_Win\iop.exe')
        if not source.exists():self.skipTest('reference game unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'iop.exe';shutil.copy2(source,target)
            original=normalize(target.read_bytes());target.write_bytes(original)
            self.assertTrue(apply(tmp));self.assertFalse(apply(tmp))
            fixed=target.read_bytes()
            self.assertEqual(normalize(fixed),original)
            self.assertEqual(patched_bytes(fixed,'26.1.2.3')[OFFSET:OFFSET+10],PATCH+b'\x90'*5)
            self.assertEqual(patch_exe(fixed,{})[OFFSET:OFFSET+10],PATCH+b'\x90'*5)
            self.assertEqual((Path(tmp)/'iop.exe.before-render-fix.bak').read_bytes(),original)
