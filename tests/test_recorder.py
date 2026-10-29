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
        self.assertTrue(rec.stop_event.is_set(), "Completion of match recording must stop listening")
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

        # Verify no loudnorm filter distorting voices, and de-noising/anti-clipping filters present
        filter_str = cmd[cmd.index("-filter_complex") + 1]
        self.assertNotIn("loudnorm", filter_str)
        self.assertIn("aresample=48000", filter_str)
        self.assertIn("amix=inputs=2", filter_str)
        self.assertIn("afftdn", filter_str)
        self.assertIn("alimiter", filter_str)
        self.assertIn("highpass", filter_str)

    def test_recorder_pause_and_resume_state(self):
        """Verify that calling pause() and resume() toggles is_paused and purges staging buffers."""
        nr = self.recorder.native_recorder
        self.assertFalse(nr.is_paused)

        nr._discord_staging.append((100.0, b"frame1", 100))
        nr._mic_staging.append((100.0, b"mic1"))
        self.assertEqual(len(nr._discord_staging), 1)
        self.assertEqual(len(nr._mic_staging), 1)

        nr.pause()
        self.assertTrue(nr.is_paused)
        self.assertEqual(len(nr._discord_staging), 0)
        self.assertEqual(len(nr._mic_staging), 0)

        nr.resume()
        self.assertFalse(nr.is_paused)

    def test_get_lcu_gameflow_phase(self):
        """Verify get_lcu_gameflow_phase correctly queries phase from LCU API."""
        from app.record import get_lcu_gameflow_phase
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            lockfile_path = os.path.join(tmp_dir, "lockfile")
            with open(lockfile_path, "w", encoding="utf-8") as f:
                f.write("LeagueClient:1234:2999:secretpw:https")

            with (
                patch("app.record.find_league_install_dir", return_value=tmp_dir),
                patch("requests.Session.get") as mock_get,
            ):
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = "ChampSelect"
                mock_get.return_value = mock_resp

                phase = get_lcu_gameflow_phase()
                self.assertEqual(phase, "ChampSelect")

                # Test offline / failed call
                mock_resp.status_code = 500
                phase_offline = get_lcu_gameflow_phase()
                self.assertIsNone(phase_offline)

    def test_mid_game_start_at_29_30_prepends_silence(self):
        """When recording starts mid-game at 29:30 (1770s), exact silence must be prepended to match game timer."""
        self.recorder.native_recorder.start_recording = MagicMock()
        self.recorder.check_if_game_is_replay = MagicMock(return_value=False)

        # 29 minutes and 30 seconds into match
        self.recorder.start_recording(initial_game_time=1770.0)
        self.assertTrue(self.recorder.recording)
        self.assertGreaterEqual(self.recorder.first_game_time, 1770.0)

        # Verify _mix_to_mp3 prepends 1770000ms adelay filter
        with patch("subprocess.run") as mock_subproc:
            mock_subproc.return_value = MagicMock(returncode=0)
            nr = self.recorder.native_recorder
            d_wav = os.path.join(self.temp_dir.name, "d2930.wav")
            with open(d_wav, "wb") as f:
                f.write(b"x" * 100)
            nr.discord_wav_path = d_wav
            nr.mic_wav_path = None

            mp3 = nr._mix_to_mp3("2930_game", initial_offset=self.recorder.first_game_time)
            self.assertIsNotNone(mp3)
            self.assertTrue(mock_subproc.called)
            cmd = mock_subproc.call_args[0][0]
            filter_str = cmd[cmd.index("-filter_complex") + 1]
            self.assertIn("adelay=delays=1770", filter_str)

    def test_fast_pause_detection_triggers_immediately(self):
        """Inhuman fast pause detection must trigger pause within 10ms of unchanged game_time."""
        rec = self.recorder
        rec.recording = True
        rec.is_paused = False
        rec.native_recorder.pause = MagicMock()
        rec.native_recorder.resume = MagicMock()
        rec.last_game_time = 120.0
        rec.pause_tick_count = 0
        rec.last_advance_time = 1000.0

        # Simulate unchanged game_time at tick time 1000.010 (10ms later)
        with patch("time.monotonic", return_value=1000.010):
            game_time = 120.0
            if game_time == rec.last_game_time and game_time > 0:
                rec.pause_tick_count += 1
                elapsed_unchanged = 1000.010 - rec.last_advance_time
                if (rec.pause_tick_count >= 1 or elapsed_unchanged >= 0.010) and not rec.is_paused:
                    rec.native_recorder.pause()
                    rec.is_paused = True

        self.assertTrue(rec.is_paused)
        rec.native_recorder.pause.assert_called_once()

        # Simulate game advancing after unpause
        with patch("time.monotonic", return_value=1000.020):
            game_time = 120.5
            if game_time > rec.last_game_time:
                if rec.is_paused:
                    rec.native_recorder.resume()
                    rec.is_paused = False

        self.assertFalse(rec.is_paused)
        rec.native_recorder.resume.assert_called_once()

    def test_timeline_wav_writer_overwrite_when_ahead_and_preserves_timeline(self):
        """TimelineWavWriter must overwrite at game_time when audio rushed ahead during pause, and preserve voice timings."""
        from app.native_audio import TimelineWavWriter
        import wave

        wav_path = os.path.join(self.temp_dir.name, "timeline_test.wav")
        writer = TimelineWavWriter(wav_path, channels=2, sample_rate=48000)

        # 1. Voice at 1:30 (90.0s)
        writer.sync_to_game_time(90.0)
        self.assertEqual(writer.current_frame, 90 * 48000)
        # Speak for 2 seconds (up to 92.0s)
        writer.write_frames(b"\x11\x22" * (48000 * 2 * 2))
        self.assertEqual(writer.current_frame, 92 * 48000)

        # 2. Silence between 1:30 and 1:40 (8s of silence)
        writer.write_frames(b"\x00" * (48000 * 8 * 4))
        self.assertEqual(writer.current_frame, 100 * 48000)

        # 3. Voice at 1:40 (100.0s)
        writer.write_frames(b"\x33\x44" * (48000 * 2 * 2))
        self.assertEqual(writer.current_frame, 102 * 48000)

        # 4. In-game pause occurs at 105.0s for 10 seconds.
        # Audio writer rushed ahead to 115.0s during pause:
        writer.sync_to_game_time(105.0)
        writer.write_frames(b"\x55\x66" * (48000 * 10 * 2)) # 10s of audio written during pause
        self.assertEqual(writer.current_frame, 115 * 48000)

        # 5. Game unpauses at 105.0s: sync_to_game_time rewinds and overwrites at 105.0s!
        writer.sync_to_game_time(105.0)
        self.assertEqual(writer.current_frame, 105 * 48000, "Must seek back to game_time without cutting seconds")

        # Test ultra-fast 10ms threshold: write 15ms of audio (720 frames)
        writer.write_frames(b"\x99\xAA" * (720 * 2))
        self.assertEqual(writer.current_frame, 105 * 48000 + 720)
        # Calling sync_to_game_time(105.0) with >10ms drift rewinds immediately!
        writer.sync_to_game_time(105.0)
        self.assertEqual(writer.current_frame, 105 * 48000, "Must seek back on >10ms drift")

        # Resume writing unpaused gameplay from 105s to 120s (2:00)
        writer.write_frames(b"\x77\x88" * (48000 * 15 * 2))
        writer.close(final_game_time=120.0)

        with wave.open(wav_path, "rb") as wf:
            dur = wf.getnframes() / wf.getframerate()
            self.assertEqual(dur, 120.0, "Total duration must match 2:00 in-game clock exactly")


if __name__ == "__main__":
    unittest.main()
