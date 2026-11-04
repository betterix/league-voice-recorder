import unittest
from unittest.mock import MagicMock, patch

from app import __version__
from app.i18n import reset_listeners, set_language, t
from main import LeagueVoiceRecorderApp


class TestUIApp(unittest.TestCase):
    def setUp(self):
        # Ensure default English for predictable baseline
        reset_listeners()
        set_language("en")
        with (
            patch("main.LeagueVoiceRecorderApp._check_system_health"),
            patch("main.LeagueVoiceRecorderApp._refresh_audio_targets"),
            patch("main.LeagueVoiceRecorderApp._start_audio_meter_loop"),
            patch("main.LeagueVoiceRecorderApp._start_auto_detect_monitor"),
        ):
            self.app = LeagueVoiceRecorderApp()
        self.app._in_test_suite = True
        self.app.withdraw()  # Prevent window flashing during automated tests

    def tearDown(self):
        try:
            self.app.eval("foreach i [after info] { after cancel $i }")
            self.app.destroy()
        except Exception:
            pass
        reset_listeners()
        set_language("en")

    def test_initial_window_state(self):
        """Verify initial window properties and button texts."""
        self.assertIn("League Voice Recorder", self.app.title())
        self.assertIn(f"v{__version__}", self.app.title())
        self.assertEqual(self.app.rec_btn.cget("text"), t("record.start_btn"))
        self.assertEqual(self.app.rep_btn.cget("text"), t("replay.start_btn"))
        self.assertEqual(self.app.rec_status_pill.cget("text"), t("record.status_idle"))
        self.assertEqual(self.app.rep_status_pill.cget("text"), t("replay.status_idle"))

    def test_rec_status_pill_rendering(self):
        """Verify recording status pill displays proper text without empty colon."""
        # 1. Recording with active game time
        self.app._render_rec_status("RECORDING", "01:23")
        rec_text = self.app.rec_status_pill.cget("text")
        self.assertIn("01:23", rec_text)
        self.assertFalse(rec_text.strip().endswith(":"))

        # 2. Recording with empty detail fallback
        self.app._render_rec_status("RECORDING", "")
        rec_text_fallback = self.app.rec_status_pill.cget("text")
        self.assertIn(t("record.recording_active"), rec_text_fallback)

        # 3. Paused state
        self.app._render_rec_status("PAUSED", "02:15")
        paused_text = self.app.rec_status_pill.cget("text")
        self.assertIn("02:15", paused_text)

        # 4. Idle state
        self.app._render_rec_status("IDLE", "")
        idle_text = self.app.rec_status_pill.cget("text")
        self.assertEqual(idle_text, t("record.status_idle"))

    def test_replay_status_pill_rendering(self):
        """Verify replay status pill displays proper text without empty colon."""
        # 1. Syncing with time and rate
        self.app._render_replay_status("SYNCING", "05:12 (2.0x)")
        rep_text = self.app.rep_status_pill.cget("text")
        self.assertIn("05:12 (2.0x)", rep_text)

        # 2. Watching state with default fallback
        self.app._render_replay_status("WATCHING", "")
        watching_text = self.app.rep_status_pill.cget("text")
        self.assertIn(t("replay.waiting_for_replay"), watching_text)

    def test_dynamic_language_switching(self):
        """Switching language dynamically translates UI widgets in real-time."""
        # Switch to Hungarian
        set_language("hu")
        self.assertEqual(self.app.rec_btn.cget("text"), t("record.start_btn"))
        self.assertEqual(self.app.rep_btn.cget("text"), t("replay.start_btn"))
        self.assertEqual(
            self.app.tabview._segmented_button._buttons_dict["log"].cget("text"),
            t("tabs.log"),
        )

        # Switch back to English
        set_language("en")
        self.assertEqual(self.app.rec_btn.cget("text"), t("record.start_btn"))
        self.assertEqual(self.app.rep_btn.cget("text"), t("replay.start_btn"))
        self.assertEqual(
            self.app.tabview._segmented_button._buttons_dict["log"].cget("text"),
            t("tabs.log"),
        )


    def test_msg_queue_processing(self):
        """Verify messages queued in worker threads are correctly rendered on main thread."""
        self.app.msg_queue.put(("LOG", "Automated UI Test Log Message"))
        self.app.msg_queue.put(("STATUS_REC", "RECORDING", "04:56"))
        self.app._process_queue()

        # Check status pill update
        self.assertIn("04:56", self.app.rec_status_pill.cget("text"))

        # Check log textbox content
        self.app.log_textbox.configure(state="normal")
        log_content = self.app.log_textbox.get("1.0", "end")
        self.app.log_textbox.configure(state="disabled")
        self.assertIn("Automated UI Test Log Message", log_content)

    def test_recording_saved_automatically_stops_listening(self):
        """When recording is saved, the button resets to Start and listening is stopped."""
        # Simulate active recording state
        self.app.recorder_worker = MagicMock()
        self.app.rec_btn.configure(text=t("record.stop_btn"))

        # Message arrives that recording is saved
        self.app.msg_queue.put(("STATUS_REC", "SAVED", "8018190134.mp3"))
        self.app._process_queue()

        # Button must be reset to Start
        self.assertEqual(self.app.rec_btn.cget("text"), t("record.start_btn"))
        self.assertIsNone(self.app.recorder_worker)
        self.assertIn("SAVED: 8018190134.mp3", self.app.rec_status_pill.cget("text"))

        # Subsequent STOPPED signal should not overwrite the green SAVED message
        self.app.msg_queue.put(("STATUS_REC", "STOPPED", ""))
        self.app._process_queue()
        self.assertIn("SAVED: 8018190134.mp3", self.app.rec_status_pill.cget("text"))

    def test_ui_replay_mute_toggle_and_display(self):
        """Toggling mute in UI updates volume label and state."""
        self.assertFalse(self.app._replay_muted)

        # Toggle mute ON
        self.app._toggle_replay_mute()
        self.assertTrue(self.app._replay_muted)
        self.assertIn(t("replay.muted_label"), self.app.vol_label.cget("text"))

        # Toggle mute OFF
        self.app._toggle_replay_mute()
        self.assertFalse(self.app._replay_muted)
        self.assertIn("%", self.app.vol_label.cget("text"))

    def test_ui_volume_slider_unmutes(self):
        """Adjusting volume slider while muted automatically unmutes."""
        self.app._toggle_replay_mute()
        self.assertTrue(self.app._replay_muted)

        # Adjust volume slider
        self.app.vol_slider.set(75)
        self.app._on_replay_volume_slider_changed(75)
        self.assertFalse(self.app._replay_muted)
        self.assertIn("75%", self.app.vol_label.cget("text"))

    def test_ui_key_m_handler(self):
        """Pressing M key toggles mute unless an entry field is focused."""
        self.assertFalse(self.app._replay_muted)

        # Normal key press
        self.app._on_key_m()
        self.assertTrue(self.app._replay_muted)

        # When an Entry has focus, typing 'm' must not toggle mute
        from tkinter import Entry
        mock_entry = MagicMock(spec=Entry)
        with patch.object(self.app, "focus_get", return_value=mock_entry):
            self.app._on_key_m()
            # Still True (no toggle)
            self.assertTrue(self.app._replay_muted)


    def test_ui_theme_toggle_and_selection(self):
        """Theme toggle and selection switch appearance mode and persist config."""
        # Baseline is dark
        self.app._set_theme("dark")
        self.assertEqual(self.app.current_theme, "dark")
        self.assertEqual(self.app.theme_toggle_btn.cget("text"), "🌙")

        # Toggle to light
        self.app._toggle_theme()
        self.assertEqual(self.app.current_theme, "light")
        self.assertEqual(self.app.theme_toggle_btn.cget("text"), "☀️")
        self.assertEqual(self.app.config.get("theme"), "light")

        # Toggle back to dark
        self.app._toggle_theme()
        self.assertEqual(self.app.current_theme, "dark")
        self.assertEqual(self.app.theme_toggle_btn.cget("text"), "🌙")
        self.assertEqual(self.app.config.get("theme"), "dark")

        # Select via options menu
        self.app._on_theme_selected(t("settings.theme_light"))
        self.assertEqual(self.app.current_theme, "light")
        self.app._on_theme_selected(t("settings.theme_dark"))
        self.assertEqual(self.app.current_theme, "dark")

    def test_ui_hud_settings_and_preview(self):
        """HUD overlay toggle, opacity, preview modes, and volume synchronization."""
        self.assertIsNotNone(self.app.hud)

        # 1. Preview Recorder HUD
        self.app._preview_rec_hud()
        self.assertTrue(self.app._is_hud_previewing)
        self.assertEqual(self.app.hud.mode, "RECORD")
        self.assertTrue(self.app.hud.is_visible)

        # 2. Preview Replay HUD
        self.app._preview_replay_hud()
        self.assertTrue(self.app._is_hud_previewing)
        self.assertEqual(self.app.hud.mode, "REPLAY")
        self.assertTrue(self.app.hud.is_visible)

        # 3. Hide Preview
        self.app._hide_hud_preview()
        self.assertFalse(self.app._is_hud_previewing)
        self.assertFalse(self.app.hud.is_visible)

        # 4. Opacity slider change
        self.app.hud_opacity_slider.set(0.65)
        self.app._on_hud_opacity_slider_changed(0.65)
        self.assertEqual(self.app.config.get("hud_opacity"), 0.65)
        self.assertEqual(self.app.hud.opacity, 0.65)

        # 5. Click-through toggle
        self.app.hud_click_through_var.set(True)
        self.app._on_hud_click_through_toggled()
        self.assertTrue(self.app.config.get("hud_click_through"))
        self.assertTrue(self.app.hud.click_through)

        # 6. Volume changed callback from HUD slider
        self.app._on_hud_volume_changed(92)
        self.assertEqual(self.app.config.get("replay_volume"), 92)
        self.assertEqual(self.app.vol_slider.get(), 92)
        self.assertIn("92%", self.app.vol_label.cget("text"))


if __name__ == "__main__":
    unittest.main()
