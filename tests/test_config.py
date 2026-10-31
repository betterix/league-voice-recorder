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
        self.assertIn("check_time", cfg)
        self.assertIn("replay_volume", cfg)
        self.assertIn("sync_offset_ms", cfg)
        self.assertIn("language", cfg)
        self.assertIn("auto_start_champ_select", cfg)
        self.assertIn("auto_start_replay", cfg)

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
        from app.config import get_config_file_path, get_user_documents_app_dir

        cfg_path = get_config_file_path()
        app_dir = get_user_documents_app_dir()
        rec_dir = get_current_user_documents_recordings_dir()

        self.assertEqual(os.path.dirname(cfg_path), app_dir)
        self.assertEqual(os.path.basename(cfg_path), "config.json")
        self.assertEqual(os.path.dirname(rec_dir), app_dir)
        self.assertEqual(os.path.basename(rec_dir), "recordings")


if __name__ == "__main__":
    unittest.main()
