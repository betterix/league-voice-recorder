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

    def test_ui_hud_exclusive_to_league_game_foreground(self):
        """HUD only displays when League of Legends.exe is in foreground; 100% hidden in lobby/desktop/Alt-Tab."""
        self.assertIsNotNone(self.app.hud)
        self.app.hud.hide()
        self.assertFalse(self.app.hud.is_visible)

        self.app.recorder_worker = MagicMock()
        self.app.last_rec_status = ("WATCHING", "Waiting for match...")

        # 1. When in ChampSelect/lobby/desktop (_is_league_game_foreground False) -> HUD stays HIDDEN
        with patch.object(self.app, "_is_league_game_foreground", return_value=False):
            self.app._sync_hud_visibility_and_state()
            self.assertFalse(self.app.hud.is_visible, "Must be hidden during champ select / lobby / desktop")

        # 2. When League of Legends.exe starts (loading screen or game) -> HUD appears in RECORD mode
        with patch.object(self.app, "_is_league_game_foreground", return_value=True):
            self.app._sync_hud_visibility_and_state()
            self.assertTrue(self.app.hud.is_visible, "Must be visible in League of Legends.exe")
            self.assertEqual(self.app.hud.mode, "RECORD")

        # 3. Alt-Tabbing away from League -> HUD immediately hides
        with patch.object(self.app, "_is_league_game_foreground", return_value=False):
            self.app._sync_hud_visibility_and_state()
            self.assertFalse(self.app.hud.is_visible, "Must immediately hide when Alt-Tabbing away from game")

        # 4. Focusing back to League -> HUD restores
        with patch.object(self.app, "_is_league_game_foreground", return_value=True):
            self.app._sync_hud_visibility_and_state()
            self.assertTrue(self.app.hud.is_visible, "Must restore when focusing back to game")

        # 5. Stop recording -> HUD hides
        self.app.recorder_worker = None
        self.app._sync_hud_visibility_and_state()
        self.assertFalse(self.app.hud.is_visible)

        # 6. Replay in League of Legends.exe -> HUD appears in REPLAY mode
        self.app.replay_worker = MagicMock()
        self.app.last_replay_status = ("SYNCING", "04:12")
        with patch.object(self.app, "_is_league_game_foreground", return_value=True):
            self.app._sync_hud_visibility_and_state()
            self.assertTrue(self.app.hud.is_visible)
            self.assertEqual(self.app.hud.mode, "REPLAY")

        # 7. Stop replay -> HUD hides
        self.app.replay_worker = None
        self.app._sync_hud_visibility_and_state()
        self.assertFalse(self.app.hud.is_visible)

    def test_hud_hit_window_clicking_does_not_hide_hud(self):
        """Interacting with track_hit_win or lock_hit_win counts as active foreground and never hides HUD."""
        self.app.hud.show()
        self.app.hud.set_mode("REPLAY")
        self.app.hud.set_locked(True)

        hwnds = self.app.hud.get_all_hwnds()
        self.assertTrue(len(hwnds) > 0)
        track_wid = self.app.hud.track_hit_win.winfo_id()
        self.assertIn(track_wid, hwnds)

        # Mock foreground window being the track_hit_win
        with patch("ctypes.windll.user32.GetForegroundWindow", return_value=track_wid):
            self.assertTrue(self.app._is_league_game_foreground())

        lock_wid = self.app.hud.lock_hit_win.winfo_id()
        with patch("ctypes.windll.user32.GetForegroundWindow", return_value=lock_wid):
            self.assertTrue(self.app._is_league_game_foreground())

    def test_replay_api_button_click(self):
        """Clicking enable replay api calls enable_replay_api and logs message without error."""
        with (
            patch("app.replay_config.enable_replay_api", return_value=(True, "Replay API enabled")),
            patch("tkinter.messagebox.showinfo"),
        ):
            self.app._on_enable_replay_api_clicked()

        items = []
        while not self.app.msg_queue.empty():
            items.append(self.app.msg_queue.get_nowait())
        self.assertTrue(any("Replay API enabled" in str(item) for item in items))

    def test_auto_switches_toggle_and_flush(self):
        """Toggling settings switches immediately updates config and flushes cleanly."""
        self.app.auto_champ_select_var.set(False)
        self.app._on_auto_champ_select_toggled()
        self.assertFalse(self.app.config["auto_start_champ_select"])

        self.app.auto_replay_var.set(False)
        self.app._on_auto_replay_toggled()
        self.assertFalse(self.app.config["auto_start_replay"])

        self.app.hud_opacity_slider.set(0.60)
        self.app._flush_pending_settings_to_disk()
        self.assertEqual(self.app.config["hud_opacity"], 0.60)

    def test_settings_entry_instant_save(self):
        """Editing setting entries immediately updates config upon change."""
        new_test_folder = r"C:\UniqueTestRecordings_" + str(id(self))
        new_test_vol = 42 if self.app.config.get("replay_volume") != 42 else 55

        self.app.rec_folder_entry.delete(0, "end")
        self.app.rec_folder_entry.insert(0, new_test_folder)
        self.app.replay_vol_entry.delete(0, "end")
        self.app.replay_vol_entry.insert(0, str(new_test_vol))

        with patch("main.save_config", return_value=True) as mock_save:
            self.app._on_settings_entry_changed()
            self.assertEqual(self.app.config["record_folder"], new_test_folder)
            self.assertEqual(self.app.config["replay_volume"], new_test_vol)
            mock_save.assert_called()

    def test_save_settings_silent_and_prompt_free(self):
        """Save settings updates config and logs without popup dialogs."""
        with (
            patch("tkinter.messagebox.showinfo") as mock_info,
            patch("main.save_config", return_value=True),
        ):
            self.app._save_settings()
            mock_info.assert_not_called()

        items = []
        while not self.app.msg_queue.empty():
            items.append(self.app.msg_queue.get_nowait())
        self.assertTrue(any("SETTINGS" in str(item) for item in items))

    def test_replay_voice_mode_toggle_before_and_during_replay(self):
        """Voice track button allows toggling between raw and AI suppressed audio before and during replay."""
        self.assertTrue(hasattr(self.app, "track_toggle_btn"))
        self.assertTrue(hasattr(self.app, "track_label"))

        # 1. Before replay starts: toggle from raw to suppressed
        self.app.config["prefer_suppressed_replay"] = False
        self.app._update_voice_mode_button_visuals(False)
        self.assertIn("Standard", self.app.track_toggle_btn.cget("text"))

        # Click the button
        self.app._toggle_replay_voice_mode()
        self.assertTrue(self.app.config["prefer_suppressed_replay"])
        self.assertIn("AI", self.app.track_toggle_btn.cget("text"))
        self.assertTrue(self.app.hud.prefer_suppressed)

        # 2. During replay: mock active worker and toggle
        mock_worker = MagicMock()
        self.app.replay_worker = mock_worker
        self.app._toggle_replay_voice_mode()

        self.assertFalse(self.app.config["prefer_suppressed_replay"])
        self.assertIn("Standard", self.app.track_toggle_btn.cget("text"))
        self.assertFalse(self.app.hud.prefer_suppressed)
        mock_worker.set_voice_mode.assert_called_with(False)

        # 3. Callback from worker updates UI and HUD
        self.app._on_replay_voice_mode_changed_from_worker(True)
        self.assertTrue(self.app.config["prefer_suppressed_replay"])
        self.assertIn("AI", self.app.track_toggle_btn.cget("text"))
        self.assertTrue(self.app.hud.prefer_suppressed)


if __name__ == "__main__":
    unittest.main()
