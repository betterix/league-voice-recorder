"""Tests for scrim metadata manager and ID3 tag embedder."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from app.config import get_ffmpeg_path
from app.scrim_meta import (
    build_id3_tag,
    embed_mp3_metadata,
    get_metadata_path,
    get_scrim_metadata,
    load_all_scrim_metadata,
    read_mp3_metadata,
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

    def test_build_and_parse_id3_tag(self):
        tag = build_id3_tag(
            game_id="12345",
            team_tag="DEO",
            notes="Nice match draft",
            recorded_at="2026-11-11",
            game_patch="26.12",
        )
        self.assertTrue(tag.startswith(b"ID3\x04\x00\x00"))
        
        mp3_file = self.folder / "12345.mp3"
        mp3_file.write_bytes(tag + b"fake_audio_bytes")
        
        parsed = read_mp3_metadata(mp3_file)
        self.assertEqual(parsed["game_id"], "12345")
        self.assertEqual(parsed["team_tag"], "DEO")
        self.assertEqual(parsed["game_patch"], "26.12")
        self.assertEqual(parsed["recorded_at"], "2026-11-11")
        self.assertEqual(parsed["notes"], "Nice match draft")

    def test_save_and_load_scrim_metadata_directly_in_mp3(self):
        mp3_file = self.folder / "8018190134.mp3"
        mp3_file.write_bytes(b"dummy_audio")

        data = {
            "team_tag": "T1",
            "notes": "Game 1 vs Fnatic - Mid lane draft test",
            "game_patch": "14.22",
            "recorded_at": "2026-11-10 19:00:00",
        }
        success = save_scrim_metadata(self.folder, "8018190134", data, filepath=mp3_file)
        self.assertTrue(success)

        # Ensure NO scrim_metadata.json was created
        meta_json = get_metadata_path(self.folder)
        self.assertFalse(meta_json.exists(), "No JSON file should be created; MP3 is the single source of truth!")

        # All metadata is loaded directly from the MP3 file
        all_meta = load_all_scrim_metadata(self.folder)
        self.assertIn("8018190134", all_meta)
        loaded = all_meta["8018190134"]
        self.assertEqual(loaded["team_tag"], "T1")
        self.assertEqual(loaded["notes"], "Game 1 vs Fnatic - Mid lane draft test")
        self.assertEqual(loaded["game_patch"], "14.22")
        self.assertEqual(loaded["recorded_at"], "2026-11-10")

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

        # Test reading ID3 metadata directly from the MP3
        read_data = read_mp3_metadata(mp3_file)
        self.assertEqual(read_data["game_id"], "8018190134")
        self.assertEqual(read_data["team_tag"], "G2-ESPORTS")
        self.assertEqual(read_data["notes"], "Game 2 - Baron control victory")
        self.assertEqual(read_data["recorded_at"], "2026-11-10")
        self.assertEqual(read_data["game_patch"], "14.22")

    def test_legacy_json_migration_and_cleanup(self):
        mp3_file = self.folder / "99887766.mp3"
        mp3_file.write_bytes(b"audio_bytes")

        # Create a legacy scrim_metadata.json
        meta_json = get_metadata_path(self.folder)
        meta_json.write_text(
            '{"99887766": {"team_tag": "LEGACY-TEAM", "game_patch": "26.12", "notes": "From JSON"}}',
            encoding="utf-8",
        )
        self.assertTrue(meta_json.exists())

        # Loading all metadata migrates legacy JSON data into the MP3 and deletes the obsolete JSON!
        recovered = load_all_scrim_metadata(self.folder)
        self.assertIn("99887766", recovered)
        self.assertEqual(recovered["99887766"]["team_tag"], "LEGACY-TEAM")
        self.assertEqual(recovered["99887766"]["game_patch"], "26.12")
        self.assertEqual(recovered["99887766"]["notes"], "From JSON")

        # Legacy JSON is automatically cleaned up
        self.assertFalse(meta_json.exists(), "Legacy JSON must be deleted once migrated to MP3 ID3 tags!")

        # Verify MP3 itself now has the tags embedded directly
        mp3_tags = read_mp3_metadata(mp3_file)
        self.assertEqual(mp3_tags["team_tag"], "LEGACY-TEAM")
        self.assertEqual(mp3_tags["game_patch"], "26.12")


if __name__ == "__main__":
    unittest.main()
