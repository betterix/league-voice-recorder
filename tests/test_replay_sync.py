import unittest

from app.play_replay import ReplaySynchronizer, parse_rofl_game_id


class TestReplaySyncModule(unittest.TestCase):
    def test_parse_rofl_game_id_standard(self):
        self.assertEqual(parse_rofl_game_id("EUW1-8018190134.rofl"), "8018190134")
        self.assertEqual(parse_rofl_game_id("EUN1-3456789012.rofl"), "3456789012")
        self.assertEqual(parse_rofl_game_id("NA1-9876543210.rofl"), "9876543210")
        self.assertEqual(parse_rofl_game_id("KR-5555555555.rofl"), "5555555555")

    def test_parse_rofl_game_id_numeric_only(self):
        self.assertEqual(parse_rofl_game_id("8018190134.rofl"), "8018190134")

    def test_parse_rofl_game_id_full_path(self):
        path = r"C:\Users\User\Documents\League of Legends\Replays\EUW1-8018190134.rofl"
        self.assertEqual(parse_rofl_game_id(path), "8018190134")

    def test_parse_rofl_game_id_invalid(self):
        self.assertIsNone(parse_rofl_game_id("not_a_rofl.txt"))
        self.assertIsNone(parse_rofl_game_id("invalid-name.rofl"))
        self.assertIsNone(parse_rofl_game_id(""))

    def test_volume_clamping(self):
        sync = ReplaySynchronizer()
        sync.set_volume(50)
        self.assertEqual(sync.volume, 50)

        sync.set_volume(-20)
        self.assertEqual(sync.volume, 0)

        sync.set_volume(150)
        self.assertEqual(sync.volume, 100)


if __name__ == "__main__":
    unittest.main()
