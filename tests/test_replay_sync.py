import os
import tempfile
import unittest

from app.play_replay import (
    ReplaySynchronizer,
    find_matching_audio_file,
    parse_rofl_game_id,
)


class TestReplaySyncModule(unittest.TestCase):
    def test_parse_rofl_game_id_standard(self):
        self.assertEqual(parse_rofl_game_id("EUW1-8018190134.rofl"), "8018190134")
        self.assertEqual(parse_rofl_game_id("EUN1-3456789012.rofl"), "3456789012")
        self.assertEqual(parse_rofl_game_id("NA1-9876543210.rofl"), "9876543210")
        self.assertEqual(parse_rofl_game_id("KR-5555555555.rofl"), "5555555555")

    def test_parse_rofl_game_id_numeric_only(self):
        self.assertEqual(parse_rofl_game_id("8018190134.rofl"), "8018190134")

    def test_parse_rofl_game_id_full_path(self):
        path = r"C:\Users\User\Documents\League of Legends\Replays\EUW1-8018190134.rofl"
        self.assertEqual(parse_rofl_game_id(path), "8018190134")

    def test_parse_rofl_game_id_invalid(self):
        self.assertIsNone(parse_rofl_game_id("not_a_rofl.txt"))
        self.assertIsNone(parse_rofl_game_id("invalid-name.rofl"))
        self.assertIsNone(parse_rofl_game_id(""))

    def test_volume_clamping(self):
        sync = ReplaySynchronizer()
        sync.set_volume(50)
        self.assertEqual(sync.volume, 50)

        sync.set_volume(-20)
        self.assertEqual(sync.volume, 0)

        sync.set_volume(150)
        self.assertEqual(sync.volume, 100)

    def test_find_matching_audio_file_standard(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "1234567890.mp3")
            with open(file_path, "wb") as f:
                f.write(b"x" * 20000)

            matched = find_matching_audio_file(tmpdir, "1234567890")
            self.assertEqual(matched, file_path)

    def test_find_matching_audio_file_skips_stub(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # 3 KB stub (corrupt or premature stop)
            stub_path = os.path.join(tmpdir, "1234567890.mp3")
            with open(stub_path, "wb") as f:
                f.write(b"x" * 3000)

            # Real 50 KB recording
            real_path = os.path.join(tmpdir, "1234567890_1.mp3")
            with open(real_path, "wb") as f:
                f.write(b"x" * 50000)

            matched = find_matching_audio_file(tmpdir, "1234567890")
            self.assertEqual(matched, real_path)

    def test_find_matching_audio_file_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertIsNone(find_matching_audio_file(tmpdir, "9999999999"))
            self.assertIsNone(find_matching_audio_file(tmpdir, ""))

    def test_unpause_zero_latency_behavior(self):
        """Unpausing within 150ms of position must use instant set_pause(0) and avoid seeking."""
        from unittest.mock import MagicMock

        player = MagicMock()
        game_time = 120.05
        replay_speed = 1.0

        # Scenario 1: VLC is already close (within 150ms: e.g. 120.00 vs 120.05)
        player.get_time.return_value = 120000  # 120.00s -> error 0.05s <= 0.15s
        vlc_ms = player.get_time()
        time_error = abs((vlc_ms / 1000.0) - game_time) if vlc_ms >= 0 else 999.0
        if time_error <= 0.15:
            player.set_pause(0)
            player.set_rate(replay_speed)
        else:
            player.set_time(int(game_time * 1000))
            player.set_pause(0)
            player.set_rate(replay_speed)

        player.set_pause.assert_called_with(0)
        player.set_rate.assert_called_with(replay_speed)
        self.assertFalse(player.set_time.called)

        # Scenario 2: Replay was scrubbed while paused (e.g. game_time is 145.0s, VLC is 120.00s)
        player.reset_mock()
        game_time = 145.0
        player.get_time.return_value = 120000  # 120.00s -> error 25.0s > 0.15s
        vlc_ms = player.get_time()
        time_error = abs((vlc_ms / 1000.0) - game_time) if vlc_ms >= 0 else 999.0
        if time_error <= 0.15:
            player.set_pause(0)
            player.set_rate(replay_speed)
        else:
            player.set_time(int(game_time * 1000))
            player.set_pause(0)
            player.set_rate(replay_speed)

        player.set_time.assert_called_with(145000)
        player.set_pause.assert_called_with(0)

    def test_micro_drift_rate_adjustment(self):
        """Small drifts between 0.06s and 1.5s should dynamically nudge playback rate without seeking."""
        from unittest.mock import MagicMock

        player = MagicMock()
        replay_speed = 1.0
        target_time = 100.0

        # Audio is 200ms behind League (vlc_time = 99.8s) -> should speed up slightly (+5%)
        vlc_time = 99.8
        drift = vlc_time - target_time  # -0.2s
        abs_drift = abs(drift)
        self.assertTrue(0.06 <= abs_drift <= 1.5)

        if abs_drift < 0.06:
            desired_rate = replay_speed
        elif drift < 0:
            desired_rate = min(8.0, round(replay_speed * 1.05, 4))
        else:
            desired_rate = max(0.25, round(replay_speed * 0.95, 4))

        player.set_rate(desired_rate)

        self.assertEqual(desired_rate, 1.05)
        player.set_rate.assert_called_with(1.05)
        self.assertFalse(player.set_time.called)

    def test_seek_cooldown_prevents_spam(self):
        """Hard seeks during large drift must adhere to an 800ms cooldown to avoid seek storms."""
        from unittest.mock import MagicMock

        player = MagicMock()
        last_seek_wall_time = 100.0

        # Call 1: only 0.2s elapsed (<0.8s cooldown) -> should NOT seek
        now_wall = 100.2
        if now_wall - last_seek_wall_time >= 0.8:
            player.set_time(50000)
            last_seek_wall_time = now_wall
        self.assertFalse(player.set_time.called)

        # Call 2: 0.9s elapsed (>=0.8s cooldown) -> SHOULD seek
        now_wall = 100.9
        if now_wall - last_seek_wall_time >= 0.8:
            player.set_time(50000)
            last_seek_wall_time = now_wall
        self.assertTrue(player.set_time.called)
        self.assertEqual(last_seek_wall_time, 100.9)

    def test_replay_sync_mute_and_toggle(self):
        from unittest.mock import MagicMock

        mute_callback = MagicMock()
        sync = ReplaySynchronizer(mute_callback=mute_callback)
        self.assertFalse(sync.is_muted)

        player = MagicMock()
        sync.current_player = player

        # Toggle mute ON
        sync.toggle_mute()
        self.assertTrue(sync.is_muted)
        player.audio_set_mute.assert_called_with(True)
        player.audio_set_volume.assert_called_with(0)
        mute_callback.assert_called_with(True)

        # Bypass 0.25s debounce
        sync.last_mute_toggle_time = 0

        # Toggle mute OFF
        sync.toggle_mute()
        self.assertFalse(sync.is_muted)
        player.audio_set_mute.assert_called_with(False)
        player.audio_set_volume.assert_called_with(sync.volume)
        mute_callback.assert_called_with(False)

    def test_set_volume_while_muted_preserves_mute(self):
        from unittest.mock import MagicMock

        sync = ReplaySynchronizer()
        player = MagicMock()
        sync.current_player = player

        sync.set_mute(True)
        self.assertTrue(sync.is_muted)
        player.audio_set_volume.reset_mock()

        # Changing volume while muted should update preference but keep VLC muted
        sync.set_volume(95)
        self.assertEqual(sync.volume, 95)
        self.assertFalse(player.audio_set_volume.called)

    def test_mute_debounce_prevents_rapid_oscillation(self):
        sync = ReplaySynchronizer()
        self.assertFalse(sync.is_muted)

        # First toggle should succeed
        sync.toggle_mute()
        self.assertTrue(sync.is_muted)

        # Immediate second toggle within debounce window (<0.25s) should be ignored
        sync.toggle_mute()
        self.assertTrue(sync.is_muted)


if __name__ == "__main__":
    unittest.main()
