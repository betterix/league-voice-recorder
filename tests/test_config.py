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
        self.assertIn("obs_host", cfg)
        self.assertIn("obs_port", cfg)
        self.assertIn("obs_password", cfg)
        self.assertIn("scene_name", cfg)
        self.assertIn("record_folder", cfg)
        self.assertIn("vlc_dir", cfg)
        self.assertIn("check_time", cfg)
        self.assertIn("replay_volume", cfg)
        self.assertIn("language", cfg)

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
        rec_folder = cfg.get("record_folder")
        self.assertTrue(os.path.isdir(rec_folder))


if __name__ == "__main__":
    unittest.main()
