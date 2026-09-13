import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from iop_sync import manifest,compare

class MapExclusion(unittest.TestCase):
    def test_remote_legacy_map_entries_are_ignored_but_data_checked(self):
        local={'protocol':1,'files':{'iop.exe':'same','map/multi/a.map':'local'}}
        remote={'protocol':1,'files':{'iop.exe':'same','MAP\\MULTI\\b.map':'remote'}}
        self.assertEqual(compare(local,remote),1)
        remote['files']['data/gweapon.res']='different'
        with self.assertRaisesRegex(ValueError,'gweapon.res'):compare(local,remote)

    def test_manifest_omits_multi_but_keeps_campaign(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ('iop.exe','data/gweapon.res','map/multi/custom.map','map/single/campaign.map'):
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'test')
            with patch('iop_network.patched_bytes',side_effect=lambda b,ip:b),patch('iop_hotkeys.normalize_exe',side_effect=lambda b:b):
                result=manifest(root)
            self.assertNotIn('map/multi/custom.map',result['files'])
            self.assertIn('map/single/campaign.map',result['files'])
