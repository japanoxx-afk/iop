import unittest
from unittest.mock import Mock, patch
from pathlib import Path
from iop_server_tab import ServerTab


class OfflineLaunch(unittest.TestCase):
    def test_saved_ip_server_off_launches_local_without_hosts_changes(self):
        launcher=Mock()
        launcher._need_game_dir.return_value=False
        launcher._launch_pending=False;launcher._game_process=None
        launcher._hotkeys_dirty=False;launcher.game_dir='game';launcher.cfg={'auto_record_video':False}
        launcher.recording_tab=None
        launcher.mode_var.get.return_value='window'
        launcher.mapping_ip.get.return_value='26.1.2.3'
        result=[]
        launcher._job=lambda work,done:result.append(work())
        with patch('iop_hotkeys.is_applied',return_value=True), patch('iop_sync.fix_flame'), patch('iop_server_tab.resolve_server',return_value='26.1.2.3'), patch('iop_server_tab.socket.create_connection',side_effect=ConnectionRefusedError()), patch('iop_server_tab.diagnostics.event'):
            ServerTab.launch_private_game(launcher)
        self.assertEqual(result,[(str(Path('game')/'iop.exe'),None,0)])
        launcher._admin_action.assert_not_called()
