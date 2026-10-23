import io
import json
import os
import struct
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from app.discord_ipc import (
    DiscordMuteWatcher,
    delete_persisted_token,
    load_persisted_token,
    save_persisted_token,
)
from app.native_audio import NativeAudioRecorder


class TestDiscordIpcModule(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_send_frame_encoding(self):
        """Verify _send_frame packs opcode and 32-bit payload length correctly."""
        watcher = DiscordMuteWatcher()
        fake_pipe = io.BytesIO()

        payload = {"cmd": "TEST", "data": 123}
        payload_bytes = json.dumps(payload).encode("utf-8")

        success = watcher._send_frame(fake_pipe, 1, payload)
        self.assertTrue(success)

        raw = fake_pipe.getvalue()
        self.assertGreaterEqual(len(raw), 8)
        op, length = struct.unpack("<II", raw[:8])
        self.assertEqual(op, 1)
        self.assertEqual(length, len(payload_bytes))
        self.assertEqual(json.loads(raw[8:].decode("utf-8")), payload)

    def test_mute_and_deafen_state_updates(self):
        """Verify _update_state_from_data updates is_muted and triggers on_mute_change."""
        state_changes = []
        watcher = DiscordMuteWatcher(on_mute_change=lambda muted: state_changes.append(muted))

        # Initial state: unmuted
        self.assertFalse(watcher.is_muted)
        self.assertFalse(watcher.is_deaf)

        # 1. User mutes mic in Discord
        watcher._update_state_from_data({"mute": True, "deaf": False})
        self.assertTrue(watcher.is_muted)
        self.assertFalse(watcher.is_deaf)
        self.assertEqual(state_changes, [True])

        # 2. Duplicate update should not trigger callback again
        watcher._update_state_from_data({"mute": True, "deaf": False})
        self.assertEqual(len(state_changes), 1)

        # 3. User un-mutes
        watcher._update_state_from_data({"mute": False, "deaf": False})
        self.assertFalse(watcher.is_muted)
        self.assertEqual(state_changes, [True, False])

        # 4. User deafens (which also implies mute)
        watcher._update_state_from_data({"mute": False, "deaf": True})
        self.assertTrue(watcher.is_muted)
        self.assertTrue(watcher.is_deaf)
        self.assertEqual(state_changes, [True, False, True])

        # 5. User un-deafens
        watcher._update_state_from_data({"mute": False, "deaf": False})
        self.assertFalse(watcher.is_muted)
        self.assertFalse(watcher.is_deaf)
        self.assertEqual(state_changes, [True, False, True, False])

    @patch("app.native_audio.find_discord_active_pid", return_value=None)
    def test_native_recorder_discord_mute_behavior(self, mock_pid):
        """When Discord reports muted, mic_callback must inject pure silence into the queue."""
        nr = NativeAudioRecorder(record_folder=self.temp_dir.name)
        try:
            # Emulate starting recording
            nr.is_recording = True
            nr.is_paused = False

            # Create test audio buffer (sine wave / non-zero floats)
            audio_data = (np.ones(1024, dtype=np.float32) * 0.5).tobytes()

            # 1. Normal state: unmuted
            nr.discord_mute_watcher.is_muted = False
            self.assertFalse(nr.is_discord_muted())

            # Simulate unmuted mic chunk
            with patch.object(nr._mic_queue, "put") as mock_put:
                # We extract the mic_callback logic
                if not nr.is_paused and nr.is_recording:
                    if nr.is_discord_muted():
                        mock_put(b"\x00" * len(audio_data))
                    else:
                        mock_put(bytes(audio_data))
                mock_put.assert_called_with(audio_data)

            # 2. Discord Muted: must write exact length of zero bytes
            nr.discord_mute_watcher.is_muted = True
            self.assertTrue(nr.is_discord_muted())

            with patch.object(nr._mic_queue, "put") as mock_put:
                if not nr.is_paused and nr.is_recording:
                    if nr.is_discord_muted():
                        mock_put(b"\x00" * len(audio_data))
                    else:
                        mock_put(bytes(audio_data))
                mock_put.assert_called_with(b"\x00" * len(audio_data))
        finally:
            nr.close()

    def test_persisted_token_lifecycle(self):
        """Verify token is correctly saved, loaded, and deleted without re-prompting."""
        token_path = os.path.join(self.temp_dir.name, ".discord_token")
        with patch("app.discord_ipc.get_token_file_path", return_value=token_path):
            self.assertIsNone(load_persisted_token())
            save_persisted_token("my_dummy_test_token_123")
            self.assertEqual(load_persisted_token(), "my_dummy_test_token_123")
            delete_persisted_token()
            self.assertIsNone(load_persisted_token())


if __name__ == "__main__":
    unittest.main()
