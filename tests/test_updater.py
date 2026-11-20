import io
import os
import tempfile
import unittest
import zipfile
from unittest.mock import MagicMock, patch

from app.updater import (
    clean_old_executables,
    check_for_update,
    download_and_install_update,
    is_running_as_binary,
    parse_version_tuple,
)


class TestUpdaterModule(unittest.TestCase):
    def test_parse_version_tuple(self):
        self.assertEqual(parse_version_tuple("v0.3.1"), (0, 3, 1))
        self.assertEqual(parse_version_tuple("0.3.2"), (0, 3, 2))
        self.assertEqual(parse_version_tuple("v1.0.0-rc1"), (1, 0, 0))
        self.assertEqual(parse_version_tuple(""), (0, 0, 0))
        self.assertGreater(parse_version_tuple("v0.3.2"), parse_version_tuple("v0.3.1"))
        self.assertGreater(parse_version_tuple("v1.0.0"), parse_version_tuple("v0.9.9"))

    def test_is_running_as_binary(self):
        with patch("sys.executable", "C:\\Python314\\python.exe"):
            self.assertFalse(is_running_as_binary())
        with patch("sys.executable", "C:\\LeagueVoiceRecorder\\LeagueVoiceRecorder.exe"):
            self.assertTrue(is_running_as_binary())

    @patch("requests.get")
    def test_check_for_update_newer_available(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "tag_name": "v0.3.2",
            "html_url": "https://github.com/betterix/league-voice-recorder/releases/tag/v0.3.2",
            "body": "Bug fixes and improvements",
            "assets": [
                {
                    "name": "LeagueVoiceRecorder.exe",
                    "browser_download_url": "https://github.com/betterix/league-voice-recorder/releases/download/v0.3.2/LeagueVoiceRecorder.exe",
                    "size": 15000000,
                }
            ],
        }
        mock_get.return_value = mock_resp

        res = check_for_update(current_version="0.3.1")
        self.assertTrue(res["available"])
        self.assertEqual(res["latest_version"], "0.3.2")
        self.assertEqual(res["asset_name"], "LeagueVoiceRecorder.exe")
        self.assertFalse(res["is_zip"])
        self.assertIn("LeagueVoiceRecorder.exe", res["download_url"])

    @patch("requests.get")
    def test_check_for_update_prefers_zip_asset(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "tag_name": "v0.3.2",
            "html_url": "https://github.com/betterix/league-voice-recorder/releases/tag/v0.3.2",
            "body": "Release with zip archive",
            "assets": [
                {
                    "name": "LeagueVoiceRecorder-Windows-x64.zip",
                    "browser_download_url": "https://github.com/betterix/league-voice-recorder/releases/download/v0.3.2/LeagueVoiceRecorder-Windows-x64.zip",
                    "size": 45000000,
                },
                {
                    "name": "LeagueVoiceRecorder.exe",
                    "browser_download_url": "https://github.com/betterix/league-voice-recorder/releases/download/v0.3.2/LeagueVoiceRecorder.exe",
                    "size": 15000000,
                },
            ],
        }
        mock_get.return_value = mock_resp

        res = check_for_update(current_version="0.3.1")
        self.assertTrue(res["available"])
        self.assertEqual(res["latest_version"], "0.3.2")
        self.assertEqual(res["asset_name"], "LeagueVoiceRecorder-Windows-x64.zip")
        self.assertTrue(res["is_zip"])
        self.assertIn(".zip", res["download_url"])

    @patch("requests.get")
    def test_check_for_update_up_to_date(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "tag_name": "v0.3.1",
            "assets": [],
        }
        mock_get.return_value = mock_resp

        res = check_for_update(current_version="0.3.1")
        self.assertFalse(res["available"])
        self.assertEqual(res["latest_version"], "0.3.1")

    @patch("requests.get")
    def test_check_for_update_network_error(self, mock_get):
        mock_get.side_effect = Exception("Connection timed out")
        res = check_for_update(current_version="0.3.1")
        self.assertFalse(res["available"])
        self.assertIn("Connection timed out", res["error"])

    def test_clean_old_executables(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            fake_exe = os.path.join(tmp_dir, "LeagueVoiceRecorder.exe")
            old_file = os.path.join(tmp_dir, "LeagueVoiceRecorder.exe.old")
            with open(fake_exe, "w") as f:
                f.write("active")
            with open(old_file, "w") as f:
                f.write("old")

            with (
                patch("sys.executable", fake_exe),
                patch("app.updater.is_running_as_binary", return_value=True),
            ):
                clean_old_executables()
                self.assertFalse(os.path.exists(old_file))
                self.assertTrue(os.path.exists(fake_exe))

    @patch("requests.get")
    def test_download_and_install_update_dev_mode_exe(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-length": "100"}
        mock_resp.iter_content.return_value = [b"x" * 50, b"y" * 50]
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None
        mock_get.return_value = mock_resp

        progress_records = []

        def on_prog(pct):
            progress_records.append(pct)

        with tempfile.TemporaryDirectory() as tmp_dir:
            fake_exe = os.path.join(tmp_dir, "python.exe")
            with patch("sys.executable", fake_exe):
                res_path = download_and_install_update("http://fake.url/update.exe", progress_callback=on_prog)
                self.assertIsNotNone(res_path)
                self.assertTrue(os.path.exists(res_path))
                self.assertIn(100, progress_records)

    @patch("requests.get")
    def test_download_and_install_update_dev_mode_zip(self, mock_get):
        # Create a real in-memory zip archive containing a mock app
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w") as z:
            z.writestr("LeagueVoiceRecorder/LeagueVoiceRecorder.exe", b"fake_binary_data")
            z.writestr("LeagueVoiceRecorder/locales/en.json", b'{"key": "val"}')
        zip_data = zip_buf.getvalue()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-length": str(len(zip_data))}
        mock_resp.iter_content.return_value = [zip_data]
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None
        mock_get.return_value = mock_resp

        with tempfile.TemporaryDirectory() as tmp_dir:
            fake_exe = os.path.join(tmp_dir, "python.exe")
            with patch("sys.executable", fake_exe):
                res_extracted = download_and_install_update(
                    "http://fake.url/LeagueVoiceRecorder-Windows-x64.zip"
                )
                self.assertIsNotNone(res_extracted)
                self.assertTrue(os.path.exists(res_extracted))
                # Must contain the extracted LeagueVoiceRecorder.exe
                self.assertTrue(
                    os.path.exists(os.path.join(res_extracted, "LeagueVoiceRecorder.exe"))
                )

    @patch("requests.get")
    @patch("subprocess.Popen")
    @patch("os._exit")
    def test_download_and_install_update_binary_mode_triggers_batch(
        self, mock_exit, mock_popen, mock_get
    ):
        # Create a zip with LeagueVoiceRecorder.exe
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w") as z:
            z.writestr("LeagueVoiceRecorder/LeagueVoiceRecorder.exe", b"new_binary_data")
        zip_data = zip_buf.getvalue()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-length": str(len(zip_data))}
        mock_resp.iter_content.return_value = [zip_data]
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None
        mock_get.return_value = mock_resp

        with tempfile.TemporaryDirectory() as tmp_dir:
            fake_exe = os.path.join(tmp_dir, "LeagueVoiceRecorder.exe")
            with open(fake_exe, "w") as f:
                f.write("old_binary")

            with (
                patch("sys.executable", fake_exe),
                patch("app.updater.is_running_as_binary", return_value=True),
            ):
                download_and_install_update("http://fake.url/update.zip")
                self.assertTrue(mock_popen.called)
                self.assertTrue(mock_exit.called)
                # Verify that batch file command was executed
                args, _kwargs = mock_popen.call_args
                self.assertEqual(args[0][0], "cmd.exe")
                self.assertEqual(args[0][1], "/c")
                bat_path = args[0][2]
                self.assertTrue(bat_path.endswith(".bat"))
                self.assertTrue(os.path.exists(bat_path))
                with open(bat_path, "r", encoding="utf-8") as bf:
                    content = bf.read()
                    self.assertIn("robocopy", content)
                    self.assertIn("LeagueVoiceRecorder.exe", content)


if __name__ == "__main__":
    unittest.main()
