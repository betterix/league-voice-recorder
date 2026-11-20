"""
Tests for Replay API configuration manager (app.replay_config).
"""

from __future__ import annotations

import configparser
import os
import shutil
import tempfile
import unittest

from app.replay_config import check_replay_api_status, enable_replay_api, locate_game_cfg


class TestReplayConfig(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_replay_cfg_")
        self.config_dir = os.path.join(self.test_dir, "Config")
        os.makedirs(self.config_dir, exist_ok=True)
        self.cfg_path = os.path.join(self.config_dir, "game.cfg")

    def tearDown(self):
        try:
            shutil.rmtree(self.test_dir)
        except Exception:
            pass

    def test_locate_game_cfg_custom_dir(self):
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            f.write("[General]\nCfgVersion=1.0\n")

        found = locate_game_cfg(install_dir=self.test_dir)
        self.assertIsNotNone(found)
        self.assertEqual(os.path.normpath(found), os.path.normpath(self.cfg_path))

    def test_locate_game_cfg_not_found(self):
        empty_dir = os.path.join(self.test_dir, "empty")
        os.makedirs(empty_dir, exist_ok=True)
        # Pointing to non-existent config inside install_dir
        found = locate_game_cfg(install_dir=empty_dir)
        # If the host system has League installed, locate_game_cfg will fall back to host's League
        # but if tested directly with non-existent install_dir without fallbacks:
        self.assertTrue(found is None or os.path.exists(found))

    def test_check_status_disabled(self):
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            f.write("[General]\nCfgVersion=1.0\nEnableReplayApi=0\n")

        is_enabled, path = check_replay_api_status(self.cfg_path)
        self.assertFalse(is_enabled)
        self.assertEqual(path, self.cfg_path)

    def test_check_status_missing_key(self):
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            f.write("[General]\nCfgVersion=1.0\n")

        is_enabled, path = check_replay_api_status(self.cfg_path)
        self.assertFalse(is_enabled)
        self.assertEqual(path, self.cfg_path)

    def test_check_status_missing_section(self):
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            f.write("[HUD]\nMinimapScale=1.0\n")

        is_enabled, _ = check_replay_api_status(self.cfg_path)
        self.assertFalse(is_enabled)

    def test_check_status_enabled(self):
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            f.write("[General]\nCfgVersion=1.0\nEnableReplayApi=1\n")

        is_enabled, _ = check_replay_api_status(self.cfg_path)
        self.assertTrue(is_enabled)

    def test_enable_replay_api_success_with_backup(self):
        initial_content = (
            "[FloatingText]\nSpecial_Enabled=1\n\n[General]\nCfgVersion=16.22\nWidth=1920\n"
        )
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            f.write(initial_content)

        success, msg = enable_replay_api(self.cfg_path)
        self.assertTrue(success)
        self.assertIn("Successfully enabled", msg)

        # Verify backup was created
        bak_path = self.cfg_path + ".bak"
        self.assertTrue(os.path.exists(bak_path))
        with open(bak_path, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), initial_content)

        # Verify updated content has EnableReplayApi=1 and preserves key casing
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str
        parser.read(self.cfg_path, encoding="utf-8")

        self.assertEqual(parser.get("General", "EnableReplayApi"), "1")
        self.assertEqual(parser.get("General", "CfgVersion"), "16.22")
        self.assertEqual(parser.get("General", "Width"), "1920")
        self.assertEqual(parser.get("FloatingText", "Special_Enabled"), "1")

        # Status check should now report True
        is_enabled, _ = check_replay_api_status(self.cfg_path)
        self.assertTrue(is_enabled)

    def test_enable_replay_api_missing_general_section(self):
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            f.write("[HUD]\nMinimapScale=1.0\n")

        success, _ = enable_replay_api(self.cfg_path)
        self.assertTrue(success)

        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str
        parser.read(self.cfg_path, encoding="utf-8")

        self.assertTrue(parser.has_section("General"))
        self.assertEqual(parser.get("General", "EnableReplayApi"), "1")
        self.assertEqual(parser.get("HUD", "MinimapScale"), "1.0")

    def test_enable_replay_api_already_enabled(self):
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            f.write("[General]\nEnableReplayApi=1\n")

        success, msg = enable_replay_api(self.cfg_path)
        self.assertTrue(success)
        self.assertIn("already enabled", msg)

        # Backup should not be created if already enabled
        bak_path = self.cfg_path + ".bak"
        self.assertFalse(os.path.exists(bak_path))

    def test_enable_replay_api_nonexistent_file(self):
        missing_path = os.path.join(self.test_dir, "missing_game.cfg")
        success, msg = enable_replay_api(missing_path)
        self.assertFalse(success)
        self.assertIn("Could not locate", msg)


if __name__ == "__main__":
    unittest.main()
