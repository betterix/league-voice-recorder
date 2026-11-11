from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

import customtkinter as ctk

from app import __version__
from app.i18n import reset_listeners, set_language, t
from main import LeagueVoiceRecorderApp


class TestUIApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reset_listeners()
        set_language("en")
        from customtkinter.windows.widgets.core_rendering.draw_engine import DrawEngine

        draw_methods = [
            "draw_background_corners",
            "draw_checkmark",
            "draw_dropdown_arrow",
            "draw_rounded_progress_bar_with_border",
            "draw_rounded_rect_with_border",
            "draw_rounded_rect_with_border_vertical_split",
            "draw_rounded_scrollbar",
            "draw_rounded_slider_with_border_and_button",
        ]
        cls._orig_draw_methods = {m: getattr(DrawEngine, m) for m in draw_methods}
        for m in draw_methods:
            setattr(DrawEngine, m, lambda *args, **kwargs: False)

        with (
            patch("main.LeagueVoiceRecorderApp._check_system_health"),
            patch("main.LeagueVoiceRecorderApp._refresh_audio_targets"),
            patch("main.LeagueVoiceRecorderApp._refresh_microphones"),
            patch("main.LeagueVoiceRecorderApp._start_audio_meter_loop"),
            patch("main.LeagueVoiceRecorderApp._start_auto_detect_monitor"),
            patch("main.LeagueVoiceRecorderApp._check_update_background"),
            patch("main.LeagueVoiceRecorderApp._apply_window_icon"),
            patch("app.replay_config.check_replay_api_status", return_value=(True, "C:/fake/game.cfg")),
        ):
            cls.app = LeagueVoiceRecorderApp()
        cls.app._in_test_suite = True
        cls.app.withdraw()
        try:
            for aid in cls.app.tk.eval("after info").split():
                cls.app.after_cancel(aid)
        except Exception:
            pass

    @classmethod
    def tearDownClass(cls):
        try:
            for aid in cls.app.tk.eval("after info").split():
                cls.app.after_cancel(aid)
            cls.app.destroy()
        except Exception:
            pass
        from customtkinter.windows.widgets.core_rendering.draw_engine import DrawEngine

        if hasattr(cls, "_orig_draw_methods"):
            for m, orig in cls._orig_draw_methods.items():
                setattr(DrawEngine, m, orig)
        reset_listeners()
        set_language("en")

    def setUp(self):
        try:
            for aid in self.app.tk.eval("after info").split():
                self.app.after_cancel(aid)
        except Exception:
            pass
        from app.i18n import get_language

        if get_language() != "en":
            set_language("en")
            self.app._update_ui_texts()
        self.app.is_recording = False
        self.app.is_syncing = False
        self.app._replay_muted = False

    def tearDown(self):
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

    def test_ui_volume_slider_can_reach_200_percent(self):
        """Volume slider and configuration allow boosting volume up to 200%."""
        self.app.vol_slider.set(200)
        self.app._on_replay_volume_slider_changed(200)
        self.assertEqual(self.app.config.get("replay_volume"), 200)
        self.assertIn("200%", self.app.vol_label.cget("text"))

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
        with patch("customtkinter.set_appearance_mode") as mock_set_mode:
            # Baseline is dark
            self.app._set_theme("dark")
            self.assertEqual(self.app.current_theme, "dark")
            self.assertEqual(self.app.theme_toggle_btn.cget("text"), "🌙")
            mock_set_mode.assert_called_with("dark")

            # Toggle to light
            self.app._toggle_theme()
            self.assertEqual(self.app.current_theme, "light")
            self.assertEqual(self.app.theme_toggle_btn.cget("text"), "☀️")
            self.assertEqual(self.app.config.get("theme"), "light")
            mock_set_mode.assert_called_with("light")

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
        """Interacting with volume_hit_win or lock_hit_win counts as active foreground and never hides overlay."""
        self.app.hud.show()
        self.app.hud.set_mode("REPLAY")
        self.app.hud.set_locked(True)

        hwnds = self.app.hud.get_all_hwnds()
        self.assertTrue(len(hwnds) > 0)
        volume_wid = self.app.hud.volume_hit_win.winfo_id()
        self.assertIn(volume_wid, hwnds)

        # Mock foreground window being the volume_hit_win
        with patch("ctypes.windll.user32.GetForegroundWindow", return_value=volume_wid):
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

    def test_force_close_and_sigint_handling(self):
        """Force close bypasses confirmation dialogs and triggers clean teardown."""
        self.app.recorder_worker = MagicMock()
        self.app.recorder_worker.recording = True

        try:
            with (
                patch("tkinter.messagebox.askyesno") as mock_ask,
                patch.object(self.app, "destroy") as mock_destroy,
                patch.object(self.app, "_flush_pending_settings_to_disk"),
            ):
                self.app._on_close(force=True)
                mock_ask.assert_not_called()
                mock_destroy.assert_called_once()
        finally:
            self.app.recorder_worker = None

    def test_report_callback_exception_catches_keyboard_interrupt(self):
        """KeyboardInterrupt inside Tkinter callback triggers force close immediately."""
        with patch.object(self.app, "_on_close") as mock_close:
            self.app.report_callback_exception(KeyboardInterrupt, KeyboardInterrupt(), None)
            mock_close.assert_called_once_with(force=True)

    def test_window_minimize_interception(self):
        """Window minimize intercepts <Unmap> event and moves to system tray."""
        self.assertIsNotNone(self.app.tray_manager)
        self.assertTrue(hasattr(self.app, "minimize_to_tray_switch"))
        self.assertTrue(hasattr(self.app, "minimize_to_tray_var"))

        orig_show = self.app.tray_manager.show
        orig_update = self.app._update_tray_status
        self.app.tray_manager.show = MagicMock()
        self.app._update_tray_status = MagicMock()

        try:
            mock_event = MagicMock()
            mock_event.widget = self.app

            # Case 1: Window iconic and minimize_to_tray is True -> hides to tray
            self.app.config["minimize_to_tray"] = True
            with (
                patch.object(self.app, "state", return_value="iconic"),
                patch.object(self.app, "withdraw") as mock_withdraw,
            ):
                self.app._on_window_unmap(mock_event)
                mock_withdraw.assert_called_once()
                self.app.tray_manager.show.assert_called_once()
                self.app._update_tray_status.assert_called_once()

            # Case 2: Window iconic and minimize_to_tray is False -> does NOT hide to tray
            self.app.config["minimize_to_tray"] = False
            self.app.tray_manager.show.reset_mock()
            with (
                patch.object(self.app, "state", return_value="iconic"),
                patch.object(self.app, "withdraw") as mock_withdraw,
            ):
                self.app._on_window_unmap(mock_event)
                mock_withdraw.assert_not_called()
                self.app.tray_manager.show.assert_not_called()

            # Case 3: Event for child widget, not app -> does nothing
            child_event = MagicMock()
            child_event.widget = MagicMock()
            with patch.object(self.app, "withdraw") as mock_withdraw:
                self.app._on_window_unmap(child_event)
                mock_withdraw.assert_not_called()

            # Test restore from tray
            with (
                patch.object(self.app, "deiconify") as mock_deiconify,
                patch.object(self.app, "state") as mock_state,
                patch.object(self.app, "lift") as mock_lift,
                patch.object(self.app, "focus_force") as mock_focus,
            ):
                self.app._restore_from_tray()
                mock_deiconify.assert_called_once()
                mock_state.assert_called_once_with("normal")
                mock_lift.assert_called_once()
                mock_focus.assert_called_once()

            # Test toggle switch command
            self.app.minimize_to_tray_var.set(False)
            self.app._on_minimize_to_tray_toggled()
            self.assertFalse(self.app.config["minimize_to_tray"])

            self.app.minimize_to_tray_var.set(True)
            self.app._on_minimize_to_tray_toggled()
            self.assertTrue(self.app.config["minimize_to_tray"])
        finally:
            self.app.tray_manager.show = orig_show
            self.app._update_tray_status = orig_update

    def test_cloud_sync_ui_elements_and_interactions(self):
        """Verifies that Cloud Sync widgets exist and respond correctly in Settings and Recordings tabs."""
        # 1. Verify Settings widgets
        self.assertTrue(hasattr(self.app, "cloud_url_entry"))
        self.assertTrue(hasattr(self.app, "cloud_key_entry"))
        self.assertTrue(hasattr(self.app, "cloud_test_btn"))
        self.assertTrue(hasattr(self.app, "cloud_auto_upload_switch"))
        self.assertTrue(hasattr(self.app, "cloud_key_reveal_btn"))

        # Verify default masking is show="*"
        self.assertEqual(self.app.cloud_key_entry.cget("show"), "*")

        # Test eye toggle button reveals and hides key
        self.app._toggle_cloud_key_visibility()
        self.assertEqual(self.app.cloud_key_entry.cget("show"), "")
        self.app._toggle_cloud_key_visibility()
        self.assertEqual(self.app.cloud_key_entry.cget("show"), "*")

        # Test auto-upload switch toggle updates config
        self.app.cloud_auto_upload_var.set(True)
        self.app._on_cloud_auto_upload_toggled()
        self.assertTrue(self.app.config.get("remote_auto_upload"))

        self.app.cloud_auto_upload_var.set(False)
        self.app._on_cloud_auto_upload_toggled()
        self.assertFalse(self.app.config.get("remote_auto_upload"))

        # 2. Verify Scrim Recordings tab widgets
        self.assertTrue(hasattr(self.app, "sync_remote_btn"))
        self.assertTrue(hasattr(self.app, "download_all_btn"))

        # 3. Test list rendering with mock remote recordings
        self.app._remote_recordings = [
            {
                "game_id": "999888777",
                "file_size": 15000000,
                "created_at": "2026-11-10T12:00:00",
                "team_tag": "TEST-TEAM",
            }
        ]
        # Temporarily enable list refresh outside test suite fast-path
        self.app._in_test_suite = False
        try:
            self.app._refresh_recordings_list()
            # Verify batch download button appears when a remote missing file is present
            self.assertIn("1", self.app.download_all_btn.cget("text"))

            # Test upload method exists and handles unconfigured gracefully
            self.assertTrue(hasattr(self.app, "_upload_single_recording"))

            # Test admin delete ability
            self.assertTrue(hasattr(self.app, "_delete_remote_recording"))
            self.app._cloud_role = "admin"
            self.app._refresh_recordings_list()

            # Verify that in admin role, delete button is present in the scroll frame children
            found_delete_btn = False
            for child in self.app.rec_scroll_frame.winfo_children():
                for sub in child.winfo_children():
                    if isinstance(sub, ctk.CTkButton) and "Delete" in str(sub.cget("text")):
                        found_delete_btn = True
                    elif isinstance(sub, ctk.CTkFrame):
                        for btn in sub.winfo_children():
                            if isinstance(btn, ctk.CTkButton) and "Delete" in str(btn.cget("text")):
                                found_delete_btn = True
            self.assertTrue(found_delete_btn, "Admin role should see the Delete button for remote recordings")

            # 4. Test Pull Key Permissions & Upload Button Guard
            self.assertTrue(hasattr(self.app, "_can_upload_to_cloud"))
            # Pull role cannot upload
            self.app._cloud_role = "pull"
            self.assertFalse(self.app._can_upload_to_cloud())

            # Upload & Admin roles can upload
            self.app._cloud_role = "upload"
            self.assertTrue(self.app._can_upload_to_cloud())
            self.app._cloud_role = "admin"
            self.assertTrue(self.app._can_upload_to_cloud())

            # Unknown role with pull key cannot upload
            self.app._cloud_role = "unknown"
            self.app.config["remote_api_key"] = "lvr_pull_readonly_token"
            self.assertFalse(self.app._can_upload_to_cloud())

            # Unknown role with upload key can upload
            self.app.config["remote_api_key"] = "lvr_upload_token"
            self.assertTrue(self.app._can_upload_to_cloud())

            # Test UI rendering with mock local files
            import tempfile
            with tempfile.TemporaryDirectory() as temp_dir:
                # Create a fake local recording file
                fake_mp3 = Path(temp_dir) / "8018190134.mp3"
                fake_mp3.write_bytes(b"dummy audio content")

                orig_folder = self.app.config.get("record_folder")
                self.app.config["record_folder"] = temp_dir
                self.app._remote_recordings = []  # Not on server yet

                try:
                    # Case A: When role is 'pull', Upload button MUST NOT appear
                    self.app._cloud_role = "pull"
                    self.app.config["remote_api_key"] = "lvr_pull_token"
                    self.app._refresh_recordings_list()

                    found_upload_btn_pull = False
                    for child in self.app.rec_scroll_frame.winfo_children():
                        for sub in child.winfo_children():
                            if isinstance(sub, ctk.CTkButton) and "Upload" in str(sub.cget("text")):
                                found_upload_btn_pull = True
                            elif isinstance(sub, ctk.CTkFrame):
                                for btn in sub.winfo_children():
                                    if isinstance(btn, ctk.CTkButton) and "Upload" in str(btn.cget("text")):
                                        found_upload_btn_pull = True
                    self.assertFalse(found_upload_btn_pull, "Pull key must NOT show the Upload button")

                    # Case B: When role is 'upload', Upload and Details buttons MUST appear
                    self.app._cloud_role = "upload"
                    self.app.config["remote_api_key"] = "lvr_upload_token"
                    self.app._refresh_recordings_list()

                    found_upload_btn_upload = False
                    found_details_btn = False
                    for child in self.app.rec_scroll_frame.winfo_children():
                        for sub in child.winfo_children():
                            if isinstance(sub, ctk.CTkButton):
                                if "Upload" in str(sub.cget("text")):
                                    found_upload_btn_upload = True
                                if "Details" in str(sub.cget("text")):
                                    found_details_btn = True
                            elif isinstance(sub, ctk.CTkFrame):
                                for btn in sub.winfo_children():
                                    if isinstance(btn, ctk.CTkButton):
                                        if "Upload" in str(btn.cget("text")):
                                            found_upload_btn_upload = True
                                        if "Details" in str(btn.cget("text")):
                                            found_details_btn = True
                    self.assertTrue(found_upload_btn_upload, "Upload key must show the Upload button")
                    self.assertTrue(found_details_btn, "Upload/Admin key must show the Details button")

                    # Case C: Test opening Details Dialog
                    dialog = self.app._open_edit_details_dialog("8018190134", str(fake_mp3))
                    self.assertIsNotNone(dialog)
                    self.assertTrue(hasattr(dialog, "_date_entry"))
                    self.assertTrue(hasattr(dialog, "_team_entry"))
                    self.assertTrue(hasattr(dialog, "_notes_box"))
                    # Pre-filled recorded date should not be empty
                    self.assertTrue(len(dialog._date_entry.get().strip()) >= 10)
                    dialog.destroy()

                    # Case D: Pull role attempting to open details dialog returns None
                    self.app._cloud_role = "pull"
                    self.app.config["remote_api_key"] = "lvr_pull_token"
                    dialog_pull = self.app._open_edit_details_dialog("8018190134", str(fake_mp3))
                    self.assertIsNone(dialog_pull, "Pull key cannot open or edit details")

                    # Case E: Pull role attempting auto-upload toggle is reverted
                    self.app.cloud_auto_upload_var.set(True)
                    self.app._on_cloud_auto_upload_toggled()
                    self.assertFalse(self.app.cloud_auto_upload_var.get())
                finally:
                    self.app.config["record_folder"] = orig_folder
        finally:
            self.app._in_test_suite = True
            self.app._cloud_role = "unknown"


if __name__ == "__main__":
    unittest.main()
