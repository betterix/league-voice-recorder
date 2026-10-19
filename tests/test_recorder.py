import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from app.record import LeagueRecorder


class TestRecorderModule(unittest.TestCase):
    def setUp(self):
        self.print_patch = patch("builtins.print")
        self.print_patch.start()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config = {
            "record_folder": self.temp_dir.name,
            "obs_host": "localhost",
            "obs_port": 4455,
            "obs_password": "",
            "scene_name": "Discord Only",
            "check_time": 0.2,
        }
        self.recorder = LeagueRecorder(self.config, log_callback=lambda msg: None)

    def tearDown(self):
        self.print_patch.stop()
        self.temp_dir.cleanup()

    @patch("subprocess.run")
    def test_convert_to_mp3_positive_offset(self, mock_subproc):
        """When recording starts late (>0s), adelay filter should prepend silence."""
        fake_video = os.path.join(self.temp_dir.name, "fake_recording.mkv")
        with open(fake_video, "w") as f:
            f.write("dummy")

        self.recorder.convert_to_mp3(fake_video, "EUW1-12345678", initial_offset=1.5)

        self.assertTrue(mock_subproc.called)
        cmd = mock_subproc.call_args[0][0]
        self.assertIn("-af", cmd)
        idx = cmd.index("-af")
        self.assertEqual(cmd[idx + 1], "adelay=delays=1500:all=1")
        self.assertNotIn("-ss", cmd)

    @patch("subprocess.run")
    def test_convert_to_mp3_negative_offset(self, mock_subproc):
        """When recording starts early (<0s), -ss should trim pre-game countdown."""
        fake_video = os.path.join(self.temp_dir.name, "fake_recording.mkv")
        with open(fake_video, "w") as f:
            f.write("dummy")

        self.recorder.convert_to_mp3(fake_video, "EUW1-12345678", initial_offset=-2.35)

        self.assertTrue(mock_subproc.called)
        cmd = mock_subproc.call_args[0][0]
        self.assertIn("-ss", cmd)
        idx = cmd.index("-ss")
        self.assertEqual(cmd[idx + 1], "2.350")
        self.assertNotIn("-af", cmd)

    @patch("subprocess.run")
    def test_convert_to_mp3_zero_offset(self, mock_subproc):
        """When recording starts at 0.0s, neither -ss nor adelay should be present."""
        fake_video = os.path.join(self.temp_dir.name, "fake_recording.mkv")
        with open(fake_video, "w") as f:
            f.write("dummy")

        self.recorder.convert_to_mp3(fake_video, "EUW1-12345678", initial_offset=0.0)

        self.assertTrue(mock_subproc.called)
        cmd = mock_subproc.call_args[0][0]
        self.assertNotIn("-ss", cmd)
        self.assertNotIn("-af", cmd)

    @patch("psutil.process_iter")
    def test_check_if_game_is_replay_rofl_process(self, mock_process_iter):
        """Process argument ending in .rofl must be detected as a replay."""
        mock_proc = MagicMock()
        mock_proc.info = {
            "pid": 9999,
            "name": "League of Legends.exe",
            "cmdline": [
                "League of Legends.exe",
                "C:\\Riot Games\\League of Legends\\Replays\\EUW1-8018190134.rofl",
            ],
        }
        mock_process_iter.return_value = [mock_proc]

        self.recorder._cached_game_pid = None
        self.recorder._cached_is_replay = False
        is_replay = self.recorder.check_if_game_is_replay()
        self.assertTrue(is_replay)

    @patch("psutil.process_iter")
    def test_check_if_game_is_replay_normal_game(self, mock_process_iter):
        """Normal live game command line without .rofl should not be flagged as replay."""
        mock_proc = MagicMock()
        mock_proc.info = {
            "pid": 8888,
            "name": "League of Legends.exe",
            "cmdline": [
                "League of Legends.exe",
                "127.0.0.1",
                "5119",
                "token",
            ],
        }
        mock_process_iter.return_value = [mock_proc]

        # Also mock session get replay/playback to fail (404)
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        self.recorder.session.get = MagicMock(return_value=mock_resp)

        self.recorder._cached_game_pid = None
        self.recorder._cached_is_replay = False
        is_replay = self.recorder.check_if_game_is_replay()
        self.assertFalse(is_replay)


if __name__ == "__main__":
    unittest.main()
