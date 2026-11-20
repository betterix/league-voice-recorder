"""Tests for CloudSyncClient and remote scrim server integration."""

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from app.cloud_sync import CloudSyncClient


class TestCloudSyncClient(unittest.TestCase):
    def setUp(self):
        self.server_url = "https://scrims.example.com:8443"
        self.api_key = "lvr_upload_test_secret_123456"
        self.client = CloudSyncClient(server_url=self.server_url, api_key=self.api_key)

    def test_configuration_check(self):
        self.assertTrue(self.client.is_configured())

        unconfigured1 = CloudSyncClient(server_url="", api_key=self.api_key)
        self.assertFalse(unconfigured1.is_configured())

        unconfigured2 = CloudSyncClient(server_url=self.server_url, api_key="")
        self.assertFalse(unconfigured2.is_configured())

    @patch("requests.Session.get")
    def test_check_health_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "ok",
            "version": "1.0.0",
            "authenticated_role": "upload",
            "recording_count": 5,
        }
        mock_get.return_value = mock_resp

        success, data = self.client.check_health()
        self.assertTrue(success)
        self.assertIsInstance(data, dict)
        self.assertEqual(data["authenticated_role"], "upload")
        self.assertEqual(data["recording_count"], 5)

    @patch("requests.Session.get")
    def test_check_health_unauthorized(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_get.return_value = mock_resp

        success, msg = self.client.check_health()
        self.assertFalse(success)
        self.assertIn("401", msg)

    @patch("requests.Session.get")
    def test_list_recordings(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "items": [
                {
                    "game_id": "8018190134",
                    "filename": "8018190134.mp3",
                    "file_size": 24000000,
                    "duration": 1845.2,
                    "team_tag": "T1",
                }
            ],
            "total": 1,
            "skip": 0,
            "limit": 50,
        }
        mock_get.return_value = mock_resp

        items = self.client.list_recordings()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["game_id"], "8018190134")
        self.assertEqual(items[0]["team_tag"], "T1")

    @patch("requests.Session.get")
    def test_download_recording_success(self, mock_get):
        chunks = [b"ID3\x03\x00\x00\x00\x00\x00\x00", b"\xff\xfb\x90d" + b"\x00" * 100]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Length": str(sum(len(c) for c in chunks))}
        mock_resp.iter_content.return_value = chunks
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None
        mock_get.return_value = mock_resp

        with tempfile.TemporaryDirectory() as tmp_dir:
            dest = Path(tmp_dir) / "8018190134.mp3"
            progress_calls = []

            def on_progress(downloaded, total):
                progress_calls.append((downloaded, total))

            success = self.client.download_recording("8018190134", dest, progress_callback=on_progress)
            self.assertTrue(success)
            self.assertTrue(dest.exists())
            self.assertEqual(dest.read_bytes(), b"".join(chunks))
            self.assertTrue(len(progress_calls) > 0)

    @patch("requests.Session.post")
    def test_upload_recording_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {
            "game_id": "8018190134",
            "filename": "8018190134.mp3",
            "file_size": 1024,
            "duration": 60.0,
            "sha256": "abcdef123456",
        }
        mock_post.return_value = mock_resp

        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "8018190134.mp3"
            content = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90d" * 50
            file_path.write_bytes(content)

            expected_sha = hashlib.sha256(content).hexdigest()

            success, data = self.client.upload_recording("8018190134", file_path, team_tag="G2")
            self.assertTrue(success)
            self.assertIsInstance(data, dict)
            self.assertEqual(data["game_id"], "8018190134")

            # Verify sha256 header was dispatched in request
            _, kwargs = mock_post.call_args
            self.assertEqual(kwargs["headers"]["X-Checksum-SHA256"], expected_sha)
            self.assertEqual(kwargs["data"]["team_tag"], "G2")

    @patch("requests.Session.get")
    def test_check_recording_exists(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp
        self.assertTrue(self.client.check_recording_exists("8018190134"))

        mock_resp.status_code = 404
        self.assertFalse(self.client.check_recording_exists("8018190134"))

    @patch("requests.Session.post")
    @patch.object(CloudSyncClient, "check_recording_exists", return_value=True)
    def test_upload_skips_if_already_on_server(self, mock_check, mock_post):
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "8018190134.mp3"
            file_path.write_bytes(b"dummy audio content")

            success, data = self.client.upload_recording("8018190134", file_path, check_first=True)
            self.assertTrue(success)
            self.assertIsInstance(data, dict)
            self.assertTrue(data.get("already_exists"))
            mock_post.assert_not_called()

    @patch("requests.Session.delete")
    def test_delete_recording_success(self, mock_delete):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_delete.return_value = mock_resp

        success, msg = self.client.delete_recording("8018190134")
        self.assertTrue(success)
        self.assertIn("deleted", msg.lower())


if __name__ == "__main__":
    unittest.main()
