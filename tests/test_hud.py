import tkinter as tk
import unittest
from unittest.mock import MagicMock

from app.hud_widget import LeagueHUDWidget


class TestLeagueHUDWidget(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

        self.mock_volume_callback = MagicMock()
        self.mock_pos_callback = MagicMock()

        self.hud = LeagueHUDWidget(
            self.root,
            opacity=0.85,
            click_through=False,
            initial_x=120,
            initial_y=80,
            on_volume_changed=self.mock_volume_callback,
            on_position_changed=self.mock_pos_callback,
        )

    def tearDown(self):
        try:
            self.hud.destroy()
            self.root.destroy()
        except Exception:
            pass

    def test_hud_dimensions_and_properties(self):
        """HUD matches 280x76 specifications and starts withdrawn."""
        self.assertEqual(self.hud.WIDTH, 280)
        self.assertEqual(self.hud.HEIGHT, 76)
        self.assertEqual(self.hud.opacity, 0.85)
        self.assertFalse(self.hud.click_through)
        self.assertFalse(self.hud.is_visible)

    def test_hud_show_and_hide(self):
        """Show and hide toggle visibility state."""
        self.hud.show()
        self.assertTrue(self.hud.is_visible)
        self.hud.hide()
        self.assertFalse(self.hud.is_visible)

    def test_mode_switching(self):
        """Switching between RECORD and REPLAY mode updates mode property."""
        self.hud.set_mode("RECORD")
        self.assertEqual(self.hud.mode, "RECORD")

        self.hud.set_mode("REPLAY")
        self.assertEqual(self.hud.mode, "REPLAY")

    def test_recorder_state_update(self):
        """Updating recorder state stores values and advances animation phase."""
        initial_phase = self.hud._wave_phase
        self.hud.update_recorder_state(
            time_str="14:35",
            mic_level=0.72,
            discord_level=0.48,
            is_paused=False,
            is_muted=False,
        )
        self.assertEqual(self.hud.time_str, "14:35")
        self.assertAlmostEqual(self.hud.mic_level, 0.72)
        self.assertAlmostEqual(self.hud.discord_level, 0.48)
        self.assertFalse(self.hud.is_paused)
        self.assertFalse(self.hud.is_muted)
        self.assertGreater(self.hud._wave_phase, initial_phase)

    def test_replay_state_update(self):
        """Updating replay state stores playback time, volume, and rate."""
        self.hud.set_mode("REPLAY")
        self.hud.update_replay_state(
            time_str="08:22",
            current_time=502.0,
            total_duration=1800.0,
            volume=85,
            is_paused=False,
            is_muted=False,
            speed=2.0,
        )
        self.assertEqual(self.hud.time_str, "08:22")
        self.assertEqual(self.hud.current_time, 502.0)
        self.assertEqual(self.hud.volume, 85)
        self.assertEqual(self.hud.speed, 2.0)

    def test_replay_waveform_splitting(self):
        """Waveform splits correctly into played and upcoming segments."""
        pts = self.hud._generate_static_waveform()
        self.assertGreater(len(pts), 10)

        # Split at 50%
        cursor_x = 68.0 + 0.5 * 126.0
        played, upcoming = self.hud._split_waveform(cursor_x)
        self.assertGreater(len(played), 0)
        self.assertGreater(len(upcoming), 0)

    def test_volume_slider_mouse_calculation(self):
        """Dragging volume updates percentage and invokes callback."""
        self.hud.set_mode("REPLAY")
        # Middle of slider: 68 + 126/2 = 131 -> 50%
        self.hud._update_volume_from_mouse(131.0)
        self.assertEqual(self.hud.volume, 50)
        self.mock_volume_callback.assert_called_with(50)

        # Clamping left edge (< 68 -> 0%)
        self.hud._update_volume_from_mouse(40.0)
        self.assertEqual(self.hud.volume, 0)
        self.mock_volume_callback.assert_called_with(0)

        # Clamping right edge (> 194 -> 100%)
        self.hud._update_volume_from_mouse(250.0)
        self.assertEqual(self.hud.volume, 100)
        self.mock_volume_callback.assert_called_with(100)

    def test_opacity_and_click_through_toggle(self):
        """Opacity and click-through can be modified dynamically."""
        self.hud.set_opacity(0.60)
        self.assertEqual(self.hud.opacity, 0.60)

        self.hud.set_click_through(True)
        self.assertTrue(self.hud.click_through)

        self.hud.set_click_through(False)
        self.assertFalse(self.hud.click_through)

    def test_reset_position(self):
        """Reset position calculates top-right bounds and calls callback."""
        self.hud.reset_position()
        self.mock_pos_callback.assert_called()


if __name__ == "__main__":
    unittest.main()
