from pathlib import Path
import shutil
import tempfile
import unittest
import iop_hotkeys as h
from iop_sync import manifest,compare
from iop_network import patched_bytes
ROOT=Path(r'C:\Users\seo\Downloads\DGGL\Games\IOP_Win')
PROFILE={'0a000003:05000002':'K'}
class HotkeyTests(unittest.TestCase):
    def test_duplicate_and_reserved_rejected(self):
        h.validate({});h.validate(PROFILE)
        for profile in ({'0a000003:05000002':'F'},{'0a000003:05000002':'J'},{'unknown':'K'}):
            with self.assertRaises(ValueError):h.validate(profile)
    def test_exe_roundtrip_and_foreign_changes_rejected(self):
        original=(ROOT/'iop.exe').read_bytes();new=h.patch_exe(original,PROFILE)
        self.assertEqual(h.normalize_exe(new),original)
        self.assertEqual(h.patch_exe(new,{}),original)
        self.assertEqual(h.patch_exe(new,PROFILE),new)
        changed=bytearray(new);changed[h.START+4]^=1
        with self.assertRaises(ValueError):h.normalize_exe(bytes(changed))
        self.assertEqual(h.normalize_exe(patched_bytes(new,'26.1.2.3')),patched_bytes(original,'26.1.2.3'))
    def test_label_only_patch_and_personal_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data').mkdir()
            for name in ('iop.exe','data/Button.res','data/Kbutton.res','data/Gweapon.res'):shutil.copy2(ROOT/name,root/name)
            before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()};base=manifest(root)
            self.assertEqual(h.apply(root,PROFILE),3)
            self.assertEqual(compare(base,manifest(root)),4)
            self.assertEqual(h.apply(root,PROFILE),0)
            for p,old in before.items():
                if p.name in ('Button.res','Kbutton.res'):
                    diffs=[i for i,(a,b) in enumerate(zip(old,p.read_bytes())) if a!=b]
                    self.assertEqual(len(diffs),1)
                    self.assertEqual(p.read_bytes()[diffs[0]],ord('K'))
            p=root/'data/Gweapon.res';p.write_bytes(p.read_bytes()+b'x')
            with self.assertRaises(ValueError):compare(base,manifest(root))
            h.apply(root,{})
            self.assertEqual((root/'iop.exe').read_bytes(),before[root/'iop.exe'])
            self.assertGreaterEqual(len(list(root.rglob('*.bak'))),3)
    def test_all_single_valid_changes_fit_and_normalize(self):
        original=(ROOT/'iop.exe').read_bytes();tested=0
        for ident in h.DEFAULTS:
            for key in h.KEYS:
                profile={ident:key}
                try:h.validate(profile)
                except ValueError:continue
                self.assertEqual(h.normalize_exe(h.patch_exe(original,profile)),original)
                tested+=1
        self.assertGreater(tested,400)
