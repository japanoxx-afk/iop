import hashlib
from pathlib import Path
import shutil
import struct
import tempfile
import time
import unittest
from unittest.mock import patch
from iop_network import ServerController, prepare_private_exe, ADDRESS_OFFSET
from iop_sync import fix_flame,manifest,compare,fetch_manifest,display_mode

REFERENCE=Path(r'C:\Users\seo\Downloads\DGGL\Games\IOP_Win')
class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        (self.root/'data').mkdir()
        # Synthetic one-record weapon resource exercises the real bounds/flag code.
        raw=bytearray(12+0x30); struct.pack_into('<IHHI',raw,0,1,7,0x700,12)
        (self.root/'data/Gweapon.res').write_bytes(raw)
        if not (REFERENCE/'iop.exe').exists(): self.skipTest('reference client unavailable')
        shutil.copy2(REFERENCE/'iop.exe',self.root/'iop.exe')
    def tearDown(self): self.temp.cleanup()
    def test_flame_backup_idempotence_and_malformed_rejection(self):
        p=self.root/'data/Gweapon.res'; before=p.read_bytes()
        self.assertTrue(fix_flame(self.root)); self.assertFalse(fix_flame(self.root))
        backup=list(p.parent.glob('*.bak')); self.assertEqual(len(backup),1)
        self.assertEqual(backup[0].read_bytes(),before)
        self.assertEqual(struct.unpack_from('<I',p.read_bytes(),12+0x28)[0],0x20000)
        p.write_bytes(b'bad')
        with self.assertRaises(ValueError): fix_flame(self.root)
        self.assertEqual(p.read_bytes(),b'bad')
    def test_same_filename_backup_and_normalized_hash(self):
        before=(self.root/'iop.exe').read_bytes(); baseline=manifest(self.root)
        exe=prepare_private_exe(self.root,'26.1.2.3')
        self.assertEqual(exe.name,'iop.exe'); self.assertFalse((self.root/'iop_private.exe').exists())
        self.assertEqual((self.root/'iop.exe.before-server.bak').read_bytes(),before)
        self.assertEqual(exe.read_bytes()[:ADDRESS_OFFSET],before[:ADDRESS_OFFSET])
        self.assertEqual(exe.read_bytes()[ADDRESS_OFFSET+16:],before[ADDRESS_OFFSET+16:])
        prepare_private_exe(self.root,'26.4.5.6')
        self.assertEqual(compare(baseline,manifest(self.root)),2)
        self.assertEqual((self.root/'iop.exe.before-server.bak').read_bytes(),before)
    def test_live_metadata_detects_changes_and_stops(self):
        controller=ServerController()
        try:
            fix_flame(self.root)
            controller.start('127.0.0.1',self.root/'accounts',ports=(0,0,0),game_dir=self.root,sync_port=0)
            for _ in range(100):
                if controller.state=='ON': break
                time.sleep(.02)
            self.assertEqual(controller.state,'ON')
            local=manifest(self.root); self.assertEqual(compare(local,fetch_manifest('127.0.0.1',controller.sync_port)),2)
            (self.root/'data/new.res').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'new.res'):
                compare(local,fetch_manifest('127.0.0.1',controller.sync_port))
        finally:
            controller.stop()
            if controller.thread: controller.thread.join(5)
        self.assertFalse(controller.alive)
    def test_display_preserves_compatibility_and_other_sections(self):
        ini=self.root/'ddraw.ini'; ini.write_bytes(b'[ddraw]\r\nrenderer=auto\r\n[iop]\r\nrenderer=direct3d9\r\n[other]\r\nwindowed=false\r\n')
        display_mode(ini,'window'); text=ini.read_text()
        self.assertIn('renderer=direct3d9\nwidth=1024\nheight=768\nmaintas=true\naspect_ratio=4:3\nwindowed=true\nfullscreen=false',text)
        self.assertIn('[other]\nwindowed=false',text)
        display_mode(ini,'full','16:9','1920x1080'); text=ini.read_text()
        self.assertIn('width=1920\nheight=1080\nmaintas=true\naspect_ratio=16:9\nwindowed=false\nfullscreen=true',text)
        with self.assertRaisesRegex(ValueError,'16:9'):
            display_mode(ini,'window','16:9','1024x768')

class LaunchTests(unittest.TestCase):
    def test_hosts_cancel_prevents_patch_and_spawn(self):
        from iop_server_tab import ServerTab
        class Value:
            def __init__(self,value): self.value=value
            def get(self): return self.value
        class Fake:
            game_dir='fake'; _launch_pending=False; _game_process=None; cfg={}
            mapping_ip=Value('26.1.2.3'); connect_to=Value('iop.server'); mode_var=Value('window')
            def _need_game_dir(self): return False
            def _save_network(self): pass
            def _log_server(self,text): pass
            def _admin_action(self,*args,**kw): raise OSError('cancelled')
            def _job(self,fn,done):
                try: result=fn()
                except Exception as exc: done(None,str(exc))
                else: done(result,None)
        app=Fake()
        with patch('iop_assets.ensure_game_files',return_value={'iop_restored':False,'graphics_installed':[]}), patch('iop_hotkeys.apply'), patch('iop_sync.fix_flame'), patch('iop_server_tab.hosts_mapping',return_value=None), patch('iop_server_tab.resolve_server',return_value='26.1.2.3'), patch('iop_server_tab.prepare_private_exe') as prepare, patch('iop_server_tab.subprocess.Popen') as spawn, patch('iop_server_tab.messagebox.showerror') as error:
            ServerTab.launch_private_game(app)
            prepare.assert_not_called(); spawn.assert_not_called(); error.assert_called_once()
            self.assertFalse(app._launch_pending)
