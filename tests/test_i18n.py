import json
import os
import unittest

from app.i18n import (
    LOCALES_DIR,
    get_available_languages,
    get_language,
    on_language_change,
    set_language,
    t,
)


class TestI18nModule(unittest.TestCase):
    def setUp(self):
        set_language("en")

    def test_available_languages(self):
        langs = get_available_languages()
        self.assertIn("en", langs)
        self.assertIn("hu", langs)
        self.assertEqual(langs["en"], "English")
        self.assertEqual(langs["hu"], "Magyar")

    def test_key_parity_between_en_and_hu(self):
        with open(os.path.join(LOCALES_DIR, "en.json"), "r", encoding="utf-8") as f:
            en_data = json.load(f)
        with open(os.path.join(LOCALES_DIR, "hu.json"), "r", encoding="utf-8") as f:
            hu_data = json.load(f)

        def extract_keys(d, prefix=""):
            keys = set()
            for k, v in d.items():
                full = f"{prefix}{k}"
                if isinstance(v, dict):
                    keys.update(extract_keys(v, full + "."))
                else:
                    keys.add(full)
            return keys

        en_keys = extract_keys(en_data)
        hu_keys = extract_keys(hu_data)
        self.assertEqual(en_keys - hu_keys, set(), "Keys present in EN but missing in HU")
        self.assertEqual(hu_keys - en_keys, set(), "Keys present in HU but missing in EN")

    def test_translations_en(self):
        set_language("en")
        self.assertEqual(get_language(), "en")
        self.assertEqual(t("record.start_btn"), "🔴 Start Recording Watcher")
        self.assertEqual(t("replay.start_btn"), "▶ Start Replay Auto-Sync")
        self.assertEqual(t("record.waiting_for_match"), "Waiting for match...")
        self.assertIn("Discord Only", t("record.target_scene", scene="Discord Only"))

    def test_translations_hu(self):
        set_language("hu")
        self.assertEqual(get_language(), "hu")
        self.assertEqual(t("record.start_btn"), "🔴 Felvétel figyelő indítása")
        self.assertEqual(t("replay.start_btn"), "▶ Visszajátszás szinkronizáló indítása")
        self.assertEqual(t("record.waiting_for_match"), "Várakozás meccsre...")
        self.assertIn("Discord Only", t("record.target_scene", scene="Discord Only"))

    def test_fallback_to_english(self):
        set_language("hu")
        # Non-existent key falls back to raw key
        self.assertEqual(t("non.existent.key"), "non.existent.key")

    def test_listener_callback(self):
        records = []

        def callback():
            records.append(get_language())

        on_language_change(callback)
        set_language("hu")
        self.assertIn("hu", records)
        set_language("en")
        self.assertIn("en", records)


if __name__ == "__main__":
    unittest.main()
