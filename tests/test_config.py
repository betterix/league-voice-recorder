import os
from pathlib import Path
import unittest

from app.config import (
    get_current_user_documents_recordings_dir,
    get_default_config,
    get_ffmpeg_path,
    load_config,
    resolve_path,
)


class TestConfigModule(unittest.TestCase):
    def test_default_config_fields(self):
        cfg = get_default_config()
        self.assertIn("target_audio_pid", cfg)
        self.assertIn("target_audio_name", cfg)
        self.assertIn("mic_device_index", cfg)
        self.assertIn("mic_device_name", cfg)
        self.assertIn("record_folder", cfg)
        self.assertIn("vlc_dir", cfg)
        self.assertIn("replay_volume", cfg)
        self.assertIn("language", cfg)
        self.assertIn("auto_start_champ_select", cfg)
        self.assertIn("auto_start_replay", cfg)
        self.assertIn("hud_overlay_enabled", cfg)
        self.assertIn("hud_click_through", cfg)
        self.assertIn("hud_opacity", cfg)
        self.assertIn("audio_format", cfg)
        self.assertIn(cfg["audio_format"], ("opus", "mp3"))

    def test_recordings_dir_in_user_documents(self):
        rec_dir = get_current_user_documents_recordings_dir()
        self.assertTrue(os.path.isdir(rec_dir))
        home = str(Path.home())
        self.assertTrue(rec_dir.startswith(home))
        self.assertTrue(rec_dir.endswith(os.path.join("League Voice Recorder", "recordings")))

    def test_resolve_path(self):
        # Empty input defaults to documents folder
        self.assertTrue(os.path.isdir(resolve_path("")))
        self.assertTrue(os.path.isdir(resolve_path(None)))

        # %USERPROFILE% expansion
        resolved = resolve_path("%USERPROFILE%")
        self.assertEqual(resolved, os.path.abspath(os.path.expandvars("%USERPROFILE%")))

    def test_ffmpeg_path_finder(self):
        ffmpeg = get_ffmpeg_path()
        self.assertTrue(isinstance(ffmpeg, str))
        self.assertTrue(len(ffmpeg) > 0)

    def test_load_config_ensures_record_folder_exists(self):
        cfg = load_config()
        self.assertTrue(os.path.isdir(cfg["record_folder"]))

    def test_config_file_location_in_documents(self):
        from app.config import get_user_documents_app_dir

        app_dir = get_user_documents_app_dir()
        rec_dir = get_current_user_documents_recordings_dir()

        self.assertTrue(os.path.isdir(app_dir))
        self.assertEqual(os.path.dirname(rec_dir), app_dir)
        self.assertEqual(os.path.basename(rec_dir), "recordings")

    def test_save_config_partial_merge_preserves_settings(self):
        from app.config import save_config

        # 1. Save full initial setting with custom opacity
        initial = load_config()
        initial["hud_opacity"] = 0.55
        initial["hud_locked"] = True
        save_config(initial)

        # 2. Perform a partial update (e.g. moving overlay position)
        save_config({"hud_x": 500, "hud_y": 300})

        # 3. Reload config and verify opacity and other fields were NOT lost
        reloaded = load_config()
        self.assertEqual(reloaded["hud_x"], 500)
        self.assertEqual(reloaded["hud_y"], 300)
        self.assertEqual(reloaded["hud_opacity"], 0.55)
        self.assertTrue(reloaded["hud_locked"])
        self.assertIn("record_folder", reloaded)
        self.assertIn("vlc_dir", reloaded)

    def test_secret_dpapi_encryption_and_safe_storage(self):
        import json
        from app.config import (
            decrypt_secret,
            encrypt_secret,
            get_config_file_path,
            load_config,
            save_config,
        )

        # 1. Test DPAPI round-trip
        secret_sample = "lvr_upload_sample_token_xyz987"
        encrypted = encrypt_secret(secret_sample)
        self.assertNotEqual(secret_sample, encrypted)
        self.assertTrue(len(encrypted) > 10)
        decrypted = decrypt_secret(encrypted)
        self.assertEqual(secret_sample, decrypted)

        # 2. Test saving plaintext key is automatically encrypted on disk
        save_config({
            "remote_server_url": "https://scrims.example.com:8443",
            "remote_api_key": secret_sample,
            "remote_auto_upload": True,
        })

        # Inspect on-disk file directly
        cfg_path = get_config_file_path()
        with open(cfg_path, "r", encoding="utf-8") as f:
            disk_content = json.load(f)

        self.assertNotIn("remote_api_key", disk_content, "Plaintext API key must NEVER be saved to disk!")
        self.assertIn("remote_api_key_encrypted", disk_content)
        self.assertNotEqual(disk_content["remote_api_key_encrypted"], secret_sample)

        # 3. Test load_config provides decrypted key in memory
        loaded = load_config()
        self.assertEqual(loaded.get("remote_api_key"), secret_sample)
        self.assertEqual(loaded.get("remote_server_url"), "https://scrims.example.com:8443")
        self.assertTrue(loaded.get("remote_auto_upload"))


if __name__ == "__main__":
    unittest.main()
