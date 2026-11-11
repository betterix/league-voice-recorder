"""Unit tests for System Tray manager and window minimize behavior."""

import unittest
from unittest.mock import MagicMock, patch

from app.config import get_clean_default_config, get_default_config
from app.i18n import t
from app.tray import TrayManager, _load_tray_image


class TestTrayManager(unittest.TestCase):
    """Tests for the TrayManager standalone component."""

    def test_tray_manager_initialization(self):
        restore_cb = MagicMock()
        exit_cb = MagicMock()
        status_cb = MagicMock(return_value="Test Status")

        tm = TrayManager(on_restore=restore_cb, on_exit=exit_cb, get_status_text=status_cb)
        self.assertEqual(tm.on_restore, restore_cb)
        self.assertEqual(tm.on_exit, exit_cb)
        self.assertEqual(tm.get_status_text, status_cb)
        self.assertEqual(tm._dynamic_status(), "Test Status")

    def test_dynamic_status_fallback(self):
        tm = TrayManager(on_restore=MagicMock(), on_exit=MagicMock(), get_status_text=None)
        self.assertEqual(tm._dynamic_status(), t("tray.status_idle"))

        # Test when get_status_text raises an exception
        err_cb = MagicMock(side_effect=RuntimeError("Status error"))
        tm_err = TrayManager(on_restore=MagicMock(), on_exit=MagicMock(), get_status_text=err_cb)
        self.assertEqual(tm_err._dynamic_status(), t("tray.status_idle"))

    def test_menu_clicks_dispatch(self):
        restore_cb = MagicMock()
        exit_cb = MagicMock()
        tm = TrayManager(on_restore=restore_cb, on_exit=exit_cb)

        tm._menu_open_clicked()
        restore_cb.assert_called_once()

        tm._menu_exit_clicked()
        exit_cb.assert_called_once()

    def test_build_menu_structure(self):
        tm = TrayManager(on_restore=MagicMock(), on_exit=MagicMock())
        menu = tm._build_menu()
        self.assertIsNotNone(menu)
        # Menu should have items: Open, Separator, Status, Separator, Exit
        items = list(menu.items)
        self.assertTrue(len(items) >= 3)
        # First item should be default (Open)
        self.assertTrue(getattr(items[0], "default", False))

    def test_load_tray_image(self):
        img = _load_tray_image()
        self.assertIsNotNone(img)
        self.assertTrue(img.size[0] > 0 and img.size[1] > 0)

    @patch("pystray.Icon")
    def test_start_show_hide_stop_lifecycle(self, mock_icon_cls):
        mock_icon = MagicMock()
        mock_icon_cls.return_value = mock_icon

        tm = TrayManager(on_restore=MagicMock(), on_exit=MagicMock())

        # Start
        self.assertTrue(tm.start())
        self.assertTrue(tm._is_running)
        mock_icon.run_detached.assert_called_once()

        # Update tooltip
        tm.update_tooltip("New Tooltip")
        self.assertEqual(mock_icon.title, "New Tooltip")
        self.assertTrue(mock_icon.update_menu.called)

        # Show & Hide
        tm.show()
        self.assertTrue(mock_icon.visible)

        tm.hide()
        self.assertFalse(mock_icon.visible)

        # Notify
        tm.notify("Message", "Title")
        mock_icon.notify.assert_called_once_with("Message", "Title")

        # Stop
        tm.stop()
        mock_icon.stop.assert_called_once()
        self.assertFalse(tm._is_running)
        self.assertIsNone(tm._icon)


class TestTrayConfigAndUI(unittest.TestCase):
    """Tests config integration and window minimize interception."""

    def test_config_defaults_include_minimize_to_tray(self):
        clean_cfg = get_clean_default_config()
        self.assertIn("minimize_to_tray", clean_cfg)
        self.assertTrue(clean_cfg["minimize_to_tray"])

        default_cfg = get_default_config()
        self.assertIn("minimize_to_tray", default_cfg)
        self.assertTrue(default_cfg["minimize_to_tray"])

    def test_tray_dynamic_status_resolution(self):
        from main import LeagueVoiceRecorderApp

        app = LeagueVoiceRecorderApp()
        try:
            # Idle
            self.assertEqual(app._get_tray_status_text(), t("tray.status_idle"))

            # Recording active
            app.last_rec_status = ("RECORDING", "12:34")
            self.assertEqual(app._get_tray_status_text(), t("tray.status_recording"))

            # Replay syncing
            app.last_rec_status = ("IDLE", "")
            app.last_replay_status = ("SYNCING", "Game 123")
            self.assertEqual(app._get_tray_status_text(), t("tray.status_syncing"))

            # Watching match
            app.last_replay_status = ("IDLE", "")
            app.recorder_worker = MagicMock()
            app.recorder_worker.stop_event.is_set.return_value = False
            app.recorder_worker.recording = False
            self.assertEqual(app._get_tray_status_text(), t("tray.status_watching_rec"))
        finally:
            app.destroy()


if __name__ == "__main__":
    unittest.main()
