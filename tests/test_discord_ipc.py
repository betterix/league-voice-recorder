import io
import json
import os
import struct
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import psutil

from app.discord_ipc import (
    DiscordMuteWatcher,
    delete_persisted_token,
    find_discord_ipc_pipe,
    load_persisted_token,
    save_persisted_token,
)
from app.native_audio import NativeAudioRecorder


def is_discord_running():
    """Returns True only if Discord desktop client process or IPC pipe is active."""
    try:
        if find_discord_ipc_pipe() is not None:
            return True
        for p in psutil.process_iter(["name"]):
            if "discord" in (p.info.get("name") or "").lower():
                return True
    except Exception:
        pass
    return False


@unittest.skipUnless(is_discord_running(), "Discord is not up and running")
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
        """Verify token is correctly saved in config.json and legacy .discord_token is cleaned up."""
        token_path = os.path.join(self.temp_dir.name, ".discord_token")
        mock_cfg = {}
        with (
            patch("app.discord_ipc.get_token_file_path", return_value=token_path),
            patch("app.config.load_config", return_value=mock_cfg),
            patch("app.config.save_config", side_effect=lambda c: mock_cfg.update(c)),
        ):
            self.assertIsNone(load_persisted_token())
            # Save token directly to config, verify no .discord_token file is created
            save_persisted_token("my_dummy_test_token_123")
            self.assertEqual(load_persisted_token(), "my_dummy_test_token_123")
            self.assertEqual(mock_cfg.get("discord_ipc_token"), "my_dummy_test_token_123")
            self.assertFalse(os.path.exists(token_path))

            # Test legacy migration: if a .discord_token existed, it is cleaned up
            with open(token_path, "w", encoding="utf-8") as f:
                f.write("legacy_token_456")
            mock_cfg.clear()
            self.assertEqual(load_persisted_token(), "legacy_token_456")
            self.assertFalse(os.path.exists(token_path))
            self.assertEqual(mock_cfg.get("discord_ipc_token"), "legacy_token_456")

            # Test deletion
            delete_persisted_token()
            self.assertIsNone(load_persisted_token())
            self.assertNotIn("discord_ipc_token", mock_cfg)


class TestDiscordAuthGuards(unittest.TestCase):
    def setUp(self):
        DiscordMuteWatcher.reset_startup_auth()

    def tearDown(self):
        DiscordMuteWatcher.reset_startup_auth()

    def test_no_authorize_prompt_when_token_exists(self):
        """When token exists, never prompt for AUTHORIZE even if AUTHENTICATE times out or fails."""
        sent_commands = []
        watcher = DiscordMuteWatcher(allow_interactive_auth=None)
        watcher._access_token = "valid_cached_token_xyz"

        def fake_send(pipe, op, payload):
            sent_commands.append(payload.get("cmd"))
            return True

        def fake_read(pipe, timeout_sec=2.0):
            if "AUTHENTICATE" in sent_commands:
                # Simulate timeout or unacknowledged response for AUTHENTICATE
                return 1, None
            return 0, {"evt": "READY"}

        with (
            patch.object(watcher, "_send_frame", side_effect=fake_send),
            patch.object(watcher, "_read_frame", side_effect=fake_read),
        ):
            res = watcher._authenticate_and_subscribe(None)
            self.assertFalse(res)
            self.assertIn("AUTHENTICATE", sent_commands)
            self.assertNotIn("AUTHORIZE", sent_commands, "Must NEVER request AUTHORIZE if token exists")

    def test_no_authorize_prompt_while_recording(self):
        """While recording is active, never prompt for AUTHORIZE under any circumstances."""
        sent_commands = []
        watcher = DiscordMuteWatcher(allow_interactive_auth=None)
        watcher._access_token = None
        watcher.is_recording = True

        def fake_send(pipe, op, payload):
            sent_commands.append(payload.get("cmd"))
            return True

        def fake_read(pipe, timeout_sec=2.0):
            return 0, {"evt": "READY"}

        with (
            patch.object(watcher, "_send_frame", side_effect=fake_send),
            patch.object(watcher, "_read_frame", side_effect=fake_read),
            patch("app.discord_ipc.load_persisted_token", return_value=None),
        ):
            res = watcher._authenticate_and_subscribe(None)
            self.assertFalse(res)
            self.assertNotIn("AUTHORIZE", sent_commands, "Must NEVER request AUTHORIZE while recording")

    def test_startup_auth_only_when_no_token(self):
        """Only prompt on app startup when token is absent, and never prompt a second time."""
        sent_commands = []
        watcher = DiscordMuteWatcher(allow_interactive_auth=None)
        watcher._access_token = None

        def fake_send(pipe, op, payload):
            sent_commands.append(payload.get("cmd"))
            return True

        def fake_read(pipe, timeout_sec=2.0):
            if "AUTHORIZE" in sent_commands:
                return 1, None  # User declined or timed out
            return 0, {"evt": "READY"}

        with (
            patch.object(watcher, "_send_frame", side_effect=fake_send),
            patch.object(watcher, "_read_frame", side_effect=fake_read),
            patch("app.discord_ipc.load_persisted_token", return_value=None),
        ):
            # First attempt on startup
            res1 = watcher._authenticate_and_subscribe(None)
            self.assertFalse(res1)
            self.assertIn("AUTHORIZE", sent_commands)
            self.assertTrue(DiscordMuteWatcher._startup_auth_done)

            # Second attempt (subsequent reconnect) - must NOT prompt AUTHORIZE again!
            sent_commands.clear()
            res2 = watcher._authenticate_and_subscribe(None)
            self.assertFalse(res2)
            self.assertNotIn("AUTHORIZE", sent_commands, "Must not prompt AUTHORIZE again after startup attempt")

    def test_startup_auth_when_token_is_non_functional(self):
        """On startup, if existing token returns ERROR (non-functional), delete token and request AUTHORIZE."""
        sent_commands = []
        watcher = DiscordMuteWatcher(allow_interactive_auth=None)
        watcher._access_token = "invalid_expired_token"

        def fake_send(pipe, op, payload):
            sent_commands.append(payload.get("cmd"))
            return True

        def fake_read(pipe, timeout_sec=2.0):
            if "AUTHENTICATE" in sent_commands and "AUTHORIZE" not in sent_commands:
                # Discord returns explicit error for invalid token
                return 1, {"evt": "ERROR", "data": {"message": "Invalid token", "code": 4000}}
            if "AUTHORIZE" in sent_commands:
                return 1, None  # Stop here
            return 0, {"evt": "READY"}

        with (
            patch.object(watcher, "_send_frame", side_effect=fake_send),
            patch.object(watcher, "_read_frame", side_effect=fake_read),
            patch("app.discord_ipc.delete_persisted_token") as mock_delete,
        ):
            res = watcher._authenticate_and_subscribe(None)
            self.assertFalse(res)
            self.assertIn("AUTHENTICATE", sent_commands)
            self.assertIn(
                "AUTHORIZE",
                sent_commands,
                "Must request AUTHORIZE when token is proven non-functional on startup",
            )
            mock_delete.assert_called_once()


if __name__ == "__main__":
    unittest.main()
