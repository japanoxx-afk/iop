import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from iop_recording import Recorder,command

class RecordingTests(unittest.TestCase):
    def test_command_only_captures_game_window_and_finalizes_mp4(self):
        args=command('ffmpeg.exe',123,'video.mp4')
        self.assertEqual(args[args.index('-i')+1],'hwnd=123')
        self.assertIn('-an',args)
        self.assertIn('+frag_keyframe+empty_moov',args)
        self.assertNotIn('desktop',args)

    def test_game_exit_before_window_does_not_start_encoder(self):
        game=Mock();game.poll.return_value=0
        recorder=Recorder()
        with tempfile.TemporaryDirectory() as tmp,patch('iop_recording.subprocess.Popen') as popen:
            recorder._run(game,'ffmpeg',Path(tmp))
            popen.assert_not_called()

    def test_encoder_failure_reports_error(self):
        game=Mock();game.poll.return_value=None;game.pid=123
        recorder=Recorder()
        with tempfile.TemporaryDirectory() as tmp,patch('iop_recording.game_window',return_value=42),patch('iop_recording.subprocess.Popen',side_effect=OSError('test failure')):
            recorder._run(game,'ffmpeg',Path(tmp))
        self.assertTrue(any('test failure' in s for s in list(recorder.events.queue)))
