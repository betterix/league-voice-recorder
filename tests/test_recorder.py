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
            "check_time": 0.2,
        }
        self.recorder = LeagueRecorder(self.config, log_callback=lambda msg: None)

    def tearDown(self):
        self.recorder.close()
        self.print_patch.stop()
        self.temp_dir.cleanup()

    @patch("subprocess.run")
    def test_convert_to_mp3_positive_offset(self, mock_subproc):
        """When recording starts late (>0s), adelay filter should prepend silence."""
        fake_video = os.path.join(self.temp_dir.name, "fake_recording.wav")
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
        fake_video = os.path.join(self.temp_dir.name, "fake_recording.wav")
        with open(fake_video, "w") as f:
            f.write("dummy")

        self.recorder.convert_to_mp3(fake_video, "EUW1-12345678", initial_offset=-2.35)

        self.assertTrue(mock_subproc.called)
        cmd = mock_subproc.call_args[0][0]
        self.assertIn("-ss", cmd)
        idx = cmd.index("-ss")
        self.assertEqual(cmd[idx + 1], "2.350")
        self.assertNotIn("-af", cmd)
        # Sample accuracy: -ss must be placed AFTER -i (output seeking)
        self.assertGreater(cmd.index("-ss"), cmd.index("-i"))

    @patch("subprocess.run")
    def test_convert_to_mp3_zero_offset(self, mock_subproc):
        """When recording starts at 0.0s, neither -ss nor adelay should be present."""
        fake_video = os.path.join(self.temp_dir.name, "fake_recording.wav")
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

    def test_start_recording_latency_compensation(self):
        """start_recording must compensate for the time elapsed between polling and record start."""
        import time

        self.recorder.native_recorder.start_recording = MagicMock()
        self.recorder.check_if_game_is_replay = MagicMock(return_value=False)

        # Simulate 50ms polling delay
        t_poll_start = time.perf_counter() - 0.05
        self.recorder.start_recording(initial_game_time=10.0, t_poll_start=t_poll_start)

        self.assertTrue(self.recorder.recording)
        self.assertGreaterEqual(self.recorder.first_game_time, 10.045)
        self.assertTrue(self.recorder.native_recorder.start_recording.called)

    def test_is_league_game_running(self):
        """is_league_game_running should accurately detect League of Legends.exe."""
        with patch("psutil.process_iter") as mock_iter:
            mock_proc = MagicMock()
            mock_proc.info = {"name": "League of Legends.exe"}
            mock_iter.return_value = [mock_proc]
            self.assertTrue(self.recorder.is_league_game_running())

        with patch("psutil.process_iter") as mock_iter:
            mock_proc = MagicMock()
            mock_proc.info = {"name": "chrome.exe"}
            mock_iter.return_value = [mock_proc]
            self.assertFalse(self.recorder.is_league_game_running())

    def test_recording_waits_for_zero_zero_match_start(self):
        """Recording must NEVER start when game_time <= 0.0 (loading screen / pre-game countdown)."""
        self.recorder.start_recording = MagicMock()
        self.recorder.check_if_game_is_replay = MagicMock(return_value=False)
        self.recorder.recording = False

        # Simulate negative countdown (-15.0s)
        game_time = -15.0
        if not self.recorder.recording:
            if game_time > 0.0:
                self.recorder.start_recording(initial_game_time=game_time)
        self.assertFalse(self.recorder.start_recording.called)

        # Simulate loading screen (0.0s static) - must NOT start recording
        game_time = 0.0
        if not self.recorder.recording:
            if game_time > 0.0:
                self.recorder.start_recording(initial_game_time=game_time)
        self.assertFalse(self.recorder.start_recording.called)

        # Simulate match start (0.05s / clock ticking past 0:00)
        game_time = 0.05
        if not self.recorder.recording:
            if game_time > 0.0:
                self.recorder.start_recording(initial_game_time=game_time)
        self.assertTrue(self.recorder.start_recording.called)

    def test_match_start_zero_offset_no_adelay(self):
        """When recording starts at the start of a match (<= 2.0s), first_game_time must be exactly 0.0."""
        self.recorder.native_recorder.start_recording = MagicMock()
        self.recorder.check_if_game_is_replay = MagicMock(return_value=False)
        self.recorder.recording = False

        self.recorder.start_recording(initial_game_time=0.1)
        self.assertEqual(self.recorder.first_game_time, 0.0)

    def test_recording_lifecycle(self):
        """Verify recording lifecycle starts and stops NativeAudioRecorder cleanly."""
        rec = LeagueRecorder(self.config, log_callback=lambda msg: None)
        rec.check_if_game_is_replay = MagicMock(return_value=False)
        rec.native_recorder.start_recording = MagicMock()
        rec.native_recorder.stop_and_finalize = MagicMock(return_value=os.path.join(self.temp_dir.name, "123.mp3"))

        rec.start_recording(initial_game_time=5.0)
        self.assertTrue(rec.recording)
        self.assertTrue(rec.native_recorder.start_recording.called)

        rec.stop_recording_and_rename()
        self.assertFalse(rec.recording)
        self.assertTrue(rec.native_recorder.stop_and_finalize.called)
        rec.close()

    @patch("subprocess.run")
    def test_native_audio_mix_to_mp3_quality_and_filters(self, mock_subproc):
        """Verify _mix_to_mp3 uses highest quality VBR (-q:a 0) and resamples cleanly without loudnorm."""
        mock_subproc.return_value = MagicMock(returncode=0)
        nr = self.recorder.native_recorder
        d_wav = os.path.join(self.temp_dir.name, "d.wav")
        m_wav = os.path.join(self.temp_dir.name, "m.wav")
        with open(d_wav, "wb") as f:
            f.write(b"x" * 100)
        with open(m_wav, "wb") as f:
            f.write(b"x" * 100)
        nr.discord_wav_path = d_wav
        nr.mic_wav_path = m_wav

        mp3 = nr._mix_to_mp3("test_game", initial_offset=0.0)
        self.assertIsNotNone(mp3)
        self.assertTrue(mock_subproc.called)
        cmd = mock_subproc.call_args[0][0]

        # Verify highest quality VBR
        self.assertIn("-q:a", cmd)
        q_idx = cmd.index("-q:a")
        self.assertEqual(cmd[q_idx + 1], "0")

        # Verify no loudnorm filter distorting voices
        filter_str = cmd[cmd.index("-filter_complex") + 1]
        self.assertNotIn("loudnorm", filter_str)
        self.assertIn("aresample=48000", filter_str)
        self.assertIn("amix=inputs=2", filter_str)

    def test_delay_buffer_pause_purges_detection_window(self):
        """Verify that calling pause() purges the delay buffers to cut out detection latency."""
        nr = self.recorder.native_recorder
        nr._discord_buffer.append((100.0, b"frame1"))
        nr._discord_buffer.append((100.1, b"frame2"))
        nr._mic_buffer.append((100.0, b"mic1"))
        self.assertEqual(len(nr._discord_buffer), 2)
        self.assertEqual(len(nr._mic_buffer), 1)

        nr.pause()
        self.assertTrue(nr.is_paused)
        self.assertEqual(len(nr._discord_buffer), 0)
        self.assertEqual(len(nr._mic_buffer), 0)

        nr.resume()
        self.assertFalse(nr.is_paused)


if __name__ == "__main__":
    unittest.main()
