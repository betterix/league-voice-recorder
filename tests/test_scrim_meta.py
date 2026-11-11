"""Tests for scrim metadata manager and ID3 tag embedder."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from app.config import get_ffmpeg_path
from app.scrim_meta import (
    embed_mp3_metadata,
    get_metadata_path,
    get_scrim_metadata,
    load_all_scrim_metadata,
    save_scrim_metadata,
)


class TestScrimMeta(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_metadata_path(self):
        path = get_metadata_path(self.folder)
        self.assertEqual(path.name, "scrim_metadata.json")

    def test_load_all_empty(self):
        meta = load_all_scrim_metadata(self.folder)
        self.assertEqual(meta, {})

    def test_get_scrim_metadata_defaults(self):
        dummy_file = self.folder / "8018190134.mp3"
        dummy_file.write_bytes(b"dummy")

        meta = get_scrim_metadata(self.folder, "8018190134", dummy_file)
        self.assertEqual(meta["game_id"], "8018190134")
        self.assertEqual(meta["team_tag"], "")
        self.assertEqual(meta["notes"], "")
        self.assertTrue(len(meta["recorded_at"]) >= 10)

    def test_save_and_load_scrim_metadata(self):
        data = {
            "team_tag": "T1",
            "notes": "Game 1 vs Fnatic - Mid lane draft test",
            "game_patch": "14.22",
            "recorded_at": "2026-11-10 19:00:00",
        }
        success = save_scrim_metadata(self.folder, "8018190134", data, embed_id3=False)
        self.assertTrue(success)

        all_meta = load_all_scrim_metadata(self.folder)
        self.assertIn("8018190134", all_meta)
        loaded = all_meta["8018190134"]
        self.assertEqual(loaded["team_tag"], "T1")
        self.assertEqual(loaded["notes"], "Game 1 vs Fnatic - Mid lane draft test")
        self.assertEqual(loaded["game_patch"], "14.22")
        self.assertEqual(loaded["recorded_at"], "2026-11-10 19:00:00")
        self.assertIn("updated_at", loaded)

    def test_ffmpeg_id3_tag_embedding_and_mtime_preservation(self):
        ffmpeg_cmd = get_ffmpeg_path()
        mp3_file = self.folder / "8018190134.mp3"

        # Generate 0.5s silent MP3
        res = subprocess.run(
            [ffmpeg_cmd, "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "0.5", str(mp3_file)],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.assertEqual(res.returncode, 0)
        self.assertTrue(mp3_file.exists())

        # Set specific past modification time
        past_time = 1700000000.0
        os.utime(mp3_file, (past_time, past_time))
        self.assertEqual(os.path.getmtime(mp3_file), past_time)

        # Embed ID3 tags
        ok = embed_mp3_metadata(
            mp3_path=mp3_file,
            game_id="8018190134",
            team_tag="G2-ESPORTS",
            notes="Game 2 - Baron control victory",
            recorded_at="2026-11-10 19:15:00",
            game_patch="14.22",
        )
        self.assertTrue(ok)
        self.assertTrue(mp3_file.exists())
        # Verify mtime is preserved
        self.assertEqual(os.path.getmtime(mp3_file), past_time)

    def test_corrupted_json_handling(self):
        meta_file = get_metadata_path(self.folder)
        meta_file.write_text("invalid json content {{{", encoding="utf-8")

        all_meta = load_all_scrim_metadata(self.folder)
        self.assertEqual(all_meta, {})

        # Saving over corrupted file should recover cleanly
        ok = save_scrim_metadata(self.folder, "999", {"team_tag": "RECOVERED"}, embed_id3=False)
        self.assertTrue(ok)
        recovered = load_all_scrim_metadata(self.folder)
        self.assertEqual(recovered["999"]["team_tag"], "RECOVERED")


if __name__ == "__main__":
    unittest.main()
