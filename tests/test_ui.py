import unittest

from app import __version__
from app.i18n import reset_listeners, set_language, t
from main import LeagueVoiceRecorderApp


class TestUIApp(unittest.TestCase):
    def setUp(self):
        # Ensure default English for predictable baseline
        reset_listeners()
        set_language("en")
        self.app = LeagueVoiceRecorderApp()
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


if __name__ == "__main__":
    unittest.main()
