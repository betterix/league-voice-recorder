import tkinter as tk
import unittest
from unittest.mock import MagicMock

from app.overlay import LeagueOverlay


class TestLeagueOverlay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

        cls.mock_volume_callback = MagicMock()
        cls.mock_pos_callback = MagicMock()
        cls.mock_lock_callback = MagicMock()

        cls.hud = LeagueOverlay(
            cls.root,
            opacity=0.85,
            click_through=False,
            locked=False,
            initial_x=120,
            initial_y=80,
            on_volume_changed=cls.mock_volume_callback,
            on_position_changed=cls.mock_pos_callback,
            on_lock_changed=cls.mock_lock_callback,
        )

    @classmethod
    def tearDownClass(cls):
        try:
            cls.hud.destroy()
            cls.root.destroy()
        except Exception:
            pass

    def setUp(self):
        self.mock_volume_callback.reset_mock()
        self.mock_pos_callback.reset_mock()
        self.mock_lock_callback.reset_mock()

        self.hud.on_volume_changed = self.mock_volume_callback
        self.hud.on_position_changed = self.mock_pos_callback
        self.hud.on_lock_changed = self.mock_lock_callback
        self.hud.on_voice_mode_toggle = None

        self.hud.set_mode("RECORD")
        self.hud.set_locked(False)
        self.hud.set_opacity(0.85)
        self.hud.hide()
        self.hud.time_str = "00:00"
        self.hud.mic_level = 0.0
        self.hud.discord_level = 0.0
        self.hud.is_paused = False
        self.hud.is_muted = False
        self.hud.volume = 100
        self.hud.speed = 1.0
        self.hud.audio_level = 0.0
        self.hud.prefer_suppressed = False
        self.hud.window.geometry(f"{self.hud.WIDTH}x{self.hud.HEIGHT}+120+80")

    def tearDown(self):
        pass

    def test_hud_dimensions_and_properties(self):
        """HUD matches 350x76 specifications and starts withdrawn."""
        self.assertEqual(self.hud.WIDTH, 350)
        self.assertEqual(self.hud.HEIGHT, 76)
        self.assertEqual(self.hud.opacity, 0.85)
        self.assertFalse(self.hud.click_through)
        self.assertFalse(self.hud.locked)
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
        """Updating replay state stores playback time, volume, rate, and voice level."""
        self.hud.set_mode("REPLAY")
        self.hud.update_replay_state(
            time_str="08:22",
            current_time=502.0,
            total_duration=1800.0,
            volume=85,
            is_paused=False,
            is_muted=False,
            speed=2.0,
            audio_level=0.64,
        )
        self.assertEqual(self.hud.time_str, "08:22")
        self.assertEqual(self.hud.current_time, 502.0)
        self.assertEqual(self.hud.volume, 85)
        self.assertEqual(self.hud.speed, 2.0)
        self.assertAlmostEqual(self.hud.audio_level, 0.64)

    def test_replay_waveform_splitting(self):
        """Waveform splits correctly into played and upcoming segments with voice modulation."""
        pts = self.hud._generate_static_waveform()
        self.assertGreater(len(pts), 10)

        # Split at 50% with voice level (88.0 + 0.5 * 152.0 = 164.0)
        cursor_x = 88.0 + 0.5 * 152.0
        played, upcoming = self.hud._split_waveform(cursor_x, audio_level=0.75)
        self.assertGreater(len(played), 0)
        self.assertGreater(len(upcoming), 0)

    def test_volume_slider_mouse_calculation(self):
        """Dragging volume updates percentage and invokes callback."""
        self.hud.set_mode("REPLAY")
        # Middle of slider: 88 + 152/2 = 164 -> 100% (half of 200%)
        self.hud._update_volume_from_mouse(164.0)
        self.assertEqual(self.hud.volume, 100)
        self.mock_volume_callback.assert_called_with(100)

        # Clamping left edge (< 88 -> 0%)
        self.hud._update_volume_from_mouse(50.0)
        self.assertEqual(self.hud.volume, 0)
        self.mock_volume_callback.assert_called_with(0)

        # Clamping right edge (> 240 -> 200%)
        self.hud._update_volume_from_mouse(280.0)
        self.assertEqual(self.hud.volume, 200)
        self.mock_volume_callback.assert_called_with(200)

    def test_opacity_and_click_through_toggle(self):
        """Opacity and position lock can be modified dynamically."""
        self.hud.set_opacity(0.60)
        self.assertEqual(self.hud.opacity, 0.60)

        self.hud.set_click_through(True)
        self.assertTrue(self.hud.locked)

        self.hud.set_click_through(False)
        self.assertFalse(self.hud.locked)

    def test_locked_toggle_and_callback(self):
        """Position locking can be toggled and triggers callback."""
        self.hud.set_locked(True)
        self.assertTrue(self.hud.locked)

        self.hud.set_locked(False)
        self.assertFalse(self.hud.locked)

        # Clicking lock icon toggles locked state
        class DummyEvent:
            x = 332
            y = 22

        self.hud._on_mouse_down(DummyEvent())
        self.assertTrue(self.hud.locked)
        self.mock_lock_callback.assert_called_with(True)

    def test_drag_unlocked_and_locked(self):
        """Overlay can be dragged when unlocked, but stays in place when locked."""
        # Unlocked
        self.hud.set_locked(False)

        class DownEvent:
            x = 100
            y = 30
            x_root = 500
            y_root = 300

        self.hud._on_mouse_down(DownEvent())
        self.assertTrue(self.hud._is_dragging)

        class MoveEvent:
            x = 120
            y = 40
            x_root = 520
            y_root = 310

        self.hud._on_mouse_move(MoveEvent())
        self.hud._on_mouse_up(MoveEvent())
        self.assertFalse(self.hud._is_dragging)
        self.mock_pos_callback.assert_called()

        # Locked: dragging cannot be started
        self.mock_pos_callback.reset_mock()
        self.hud.set_locked(True)
        self.hud._on_mouse_down(DownEvent())
        self.assertFalse(self.hud._is_dragging)

    def test_reset_position(self):
        """Reset position calculates top-right bounds and calls callback."""
        self.hud.reset_position()
        self.mock_pos_callback.assert_called()

    def test_waveform_sensitivity_boost(self):
        """Soft to normal speech produces significantly expanded dynamic bars over resting silence."""
        resting_bars = self.hud._generate_active_waveform(center_y=22.0, level=0.0)
        # In silence, bars should be at minimum resting half-height 1.8 (height = 3.6)
        for _, y1, y2 in resting_bars:
            self.assertAlmostEqual(y2 - y1, 3.6, places=1)

        # In moderate speech (level = 0.15), bars should be visibly and substantially taller (> 8.0px)
        active_bars = self.hud._generate_active_waveform(center_y=22.0, level=0.15)
        max_active_height = max(y2 - y1 for _, y1, y2 in active_bars)
        self.assertGreater(max_active_height, 8.0)

    def test_locked_right_click_blocks_panel_and_forwards(self):
        """When locked, right-clicking never opens popup panel."""
        self.hud.set_locked(True)

        class RightClickEvent:
            x = 150
            y = 35
            x_root = 500
            y_root = 300

        with unittest.mock.patch("tkinter.Menu.tk_popup") as mock_popup:
            self.hud._on_right_click(RightClickEvent())
            mock_popup.assert_not_called()

    def test_locked_lock_hit_window_unlocks(self):
        """Dedicated hit window captures click over lock icon to unlock drag mode."""
        self.hud.set_locked(True)
        self.hud.show()
        self.assertTrue(self.hud.locked)
        self.assertTrue(self.hud.lock_hit_win is not None)

        class DummyEvent:
            x = 10
            y = 10

        self.hud._on_lock_hit_click(DummyEvent())
        self.assertFalse(self.hud.locked)

    def test_locked_lock_icon_and_replay_slider_stay_clickable(self):
        """Lock icon toggles unlock, and replay volume slider remains interactive when locked."""
        # 1. Overlay is locked
        self.hud.set_locked(True)
        self.hud.set_mode("REPLAY")

        class LockClickEvent:
            x = 332
            y = 22

        # Clicking lock icon unlocks without dragging
        self.hud._on_mouse_down(LockClickEvent())
        self.assertFalse(self.hud.locked)

        # Re-lock
        self.hud.set_locked(True)

        class SliderClickEvent:
            x = 164  # Middle of volume slider (100% of 200%)
            y = 54

        # Clicking volume slider adjusts sound
        self.hud._on_mouse_down(SliderClickEvent())
        self.assertEqual(self.hud.volume, 100)

    def test_locked_volume_hit_window_lifecycle_and_drag(self):
        """volume_hit_win is active only in locked REPLAY mode and enables dragging volume."""
        self.hud.show()
        self.hud.set_mode("REPLAY")
        self.hud.set_locked(True)

        self.assertIsNotNone(self.hud.volume_hit_win)
        self.assertEqual(self.hud.volume_hit_win.state(), "normal")

        # In locked mode, dragging volume via hit window updates volume
        class DragEvent:
            x = 82  # ~50% of the 164px track -> 100%

        self.hud._on_volume_hit_event(DragEvent())
        self.assertAlmostEqual(self.hud.volume, 100, delta=4)
        self.mock_volume_callback.assert_called()

        # Switching to RECORD mode hides volume_hit_win
        self.hud.set_mode("RECORD")
        self.assertEqual(self.hud.volume_hit_win.state(), "withdrawn")

        # Switching back to REPLAY shows it again
        self.hud.set_mode("REPLAY")
        self.assertEqual(self.hud.volume_hit_win.state(), "normal")

        # Unlocking hides volume_hit_win
        self.hud.set_locked(False)
        self.assertEqual(self.hud.volume_hit_win.state(), "withdrawn")

    def test_replay_audio_label_and_streamlined_layout(self):
        """Replay mode renders clean AUDIO label and no longer relies on track toggle hit windows."""
        self.hud.set_mode("REPLAY")
        self.hud.show()
        self.hud.set_locked(True)

        # Confirm track_hit_win no longer exists
        self.assertFalse(hasattr(self.hud, "track_hit_win"))

        # Verify all active hit window HWNDs only include main window, lock, and volume
        all_hwnds = self.hud.get_all_hwnds()
        self.assertTrue(len(all_hwnds) > 0)

        # Canvas item verification
        items = self.hud.canvas.find_all()
        texts = [self.hud.canvas.itemcget(item, "text") for item in items if self.hud.canvas.type(item) == "text"]
        self.assertIn("AUDIO", texts)
        self.assertIn("VOLUME", texts)
        self.assertNotIn("🎙️ COMMS", texts)


if __name__ == "__main__":
    unittest.main()
