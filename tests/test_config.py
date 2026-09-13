import json
from pathlib import Path
import tempfile
import unittest
from iop_config import load,save

class ConfigTests(unittest.TestCase):
    def test_new_version_retains_profile_and_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'v0.016';new=root/'v0.017';old.mkdir();new.mkdir()
            (old/'iop_launcher_config.json').write_text(json.dumps({'production_hotkeys':{'unit':'Q'},'mapping_ip':'26.1.2.3'}))
            shared=root/'user/config.json'
            self.assertEqual(load(shared,new)['production_hotkeys'],{'unit':'Q'})
            data=load(shared,old);data['production_hotkeys']={};save(shared,data)
            self.assertEqual(load(shared,new)['production_hotkeys'],{})
            self.assertEqual(load(shared,new)['mapping_ip'],'26.1.2.3')

    def test_corrupted_primary_recovers_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'config.json'
            save(path,{'production_hotkeys':{'unit':'Q'}})
            save(path,{'production_hotkeys':{'unit':'W'}})
            path.write_text('corrupt')
            self.assertEqual(load(path,root)['production_hotkeys'],{'unit':'Q'})
