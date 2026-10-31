"""
Discord Local IPC client for monitoring voice mute and deafen status.
Uses Discord's local IPC named pipe (\\\\.\\pipe\\discord-ipc-*) and StreamKit RPC
to track real-time microphone mute and deafen toggles without third-party audio drivers.
"""

import ctypes
import json
import logging
import os
import struct
import sys
import threading
import time

import requests

if sys.platform.startswith("win"):
    import msvcrt
else:
    msvcrt = None

logger = logging.getLogger(__name__)

STREAMKIT_CLIENT_ID = "207646673902501888"
TOKEN_URL = "https://streamkit.discord.com/overlay/token"

_shared_watcher = None
_shared_watcher_lock = threading.Lock()


def get_shared_discord_mute_watcher(on_mute_change=None, log_callback=None):
    """Returns or creates the application-wide singleton DiscordMuteWatcher instance."""
    global _shared_watcher
    with _shared_watcher_lock:
        if _shared_watcher is None:
            _shared_watcher = DiscordMuteWatcher(
                on_mute_change=on_mute_change,
                log_callback=log_callback,
            )
            _shared_watcher.start()
        else:
            if on_mute_change:
                _shared_watcher.on_mute_change = on_mute_change
            if log_callback:
                _shared_watcher.log_callback = log_callback
        return _shared_watcher



def get_global_token_file_path():
    """Returns the persistent user-level token path in Documents/League Voice Recorder."""
    try:
        from app.config import get_current_user_documents_recordings_dir

        rec_dir = get_current_user_documents_recordings_dir()
        base_dir = os.path.dirname(rec_dir)
        return os.path.join(base_dir, ".discord_token")
    except Exception:
        return None


def get_token_file_path():
    """Returns the legacy path to the .discord_token file for migration/cleanup."""
    try:
        from app.config import PROJECT_ROOT

        return os.path.join(PROJECT_ROOT, ".discord_token")
    except Exception:
        return os.path.abspath(".discord_token")


def load_persisted_token():
    """Loads saved Discord IPC access token from config.json, user Documents, or legacy file."""
    # 1. Primary: load from config.json
    try:
        from app.config import load_config

        cfg = load_config()
        tok = cfg.get("discord_ipc_token")
        if tok and str(tok).strip():
            # Clean up legacy .discord_token file in project root if still present
            legacy_path = get_token_file_path()
            if os.path.exists(legacy_path):
                try:
                    os.remove(legacy_path)
                except Exception:
                    pass
            return str(tok).strip()
    except Exception:
        pass

    # 2. Secondary: check persistent user Documents directory (.discord_token)
    global_path = get_global_token_file_path()
    try:
        if global_path and os.path.exists(global_path):
            with open(global_path, "r", encoding="utf-8") as f:
                tok = f.read().strip()
            if tok:
                save_persisted_token(tok)
                return tok
    except Exception:
        pass

    # 3. Legacy fallback: check .discord_token in project root, migrate, and delete file
    legacy_path = get_token_file_path()
    try:
        if os.path.exists(legacy_path):
            with open(legacy_path, "r", encoding="utf-8") as f:
                tok = f.read().strip()
            if tok:
                save_persisted_token(tok)
                return tok
    except Exception:
        pass

    return None


def save_persisted_token(token):
    """Saves Discord IPC access token directly into config.json and user Documents."""
    if not token or not str(token).strip():
        return
    clean_token = str(token).strip()

    # 1. Clean up legacy .discord_token file if present
    legacy_path = get_token_file_path()
    try:
        if os.path.exists(legacy_path):
            os.remove(legacy_path)
    except Exception:
        pass

    # 2. Save to persistent user Documents directory
    global_path = get_global_token_file_path()
    try:
        if global_path:
            os.makedirs(os.path.dirname(global_path), exist_ok=True)
            with open(global_path, "w", encoding="utf-8") as f:
                f.write(clean_token)
    except Exception:
        pass

    # 3. Save directly to config.json
    try:
        from app.config import load_config, save_config

        cfg = load_config()
        cfg["discord_ipc_token"] = clean_token
        save_config(cfg)
    except Exception:
        pass


def delete_persisted_token():
    """Removes token from config.json, user Documents, and legacy file."""
    legacy_path = get_token_file_path()
    try:
        if os.path.exists(legacy_path):
            os.remove(legacy_path)
    except Exception:
        pass
    global_path = get_global_token_file_path()
    try:
        if global_path and os.path.exists(global_path):
            os.remove(global_path)
    except Exception:
        pass
    try:
        from app.config import load_config, save_config

        cfg = load_config()
        if "discord_ipc_token" in cfg:
            del cfg["discord_ipc_token"]
            save_config(cfg)
    except Exception:
        pass


def find_discord_ipc_pipe():
    """Scans for available Discord IPC named pipes from 0 to 9."""
    for i in range(10):
        pipe_path = rf"\\.\pipe\discord-ipc-{i}"
        if os.path.exists(pipe_path):
            return pipe_path
    return None


class DiscordMuteWatcher:
    """
    Background worker that monitors Discord's local IPC socket and tracks
    whether the local user is muted or deafened in Discord.
    """

    _startup_auth_done = False

    @classmethod
    def reset_startup_auth(cls):
        """Resets the process-wide startup authorization flag (primarily for tests)."""
        cls._startup_auth_done = False

    def __init__(self, on_mute_change=None, log_callback=None, allow_interactive_auth=None):
        self.on_mute_change = on_mute_change
        self.log_callback = log_callback
        self.allow_interactive_auth = allow_interactive_auth
        self.is_muted = False
        self.is_deaf = False
        self.is_connected = False
        self.is_recording = False

        self._lock = threading.Lock()
        self._pipe = None
        self._stop_event = threading.Event()
        self._thread = None
        self._access_token = load_persisted_token()
        self._last_auth_attempt = 0.0

    def log(self, msg):
        if self.log_callback:
            try:
                self.log_callback(f"[DiscordMute] {msg}")
            except Exception:
                pass

    def start(self):
        """Starts the background Discord IPC monitor thread."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop, daemon=True, name="DiscordMuteWatcher"
            )
            self._thread.start()

    def stop(self):
        """Stops the background Discord IPC monitor thread."""
        self._stop_event.set()
        with self._lock:
            if self._pipe:
                try:
                    self._pipe.close()
                except Exception:
                    pass
                self._pipe = None
        if self._thread and threading.current_thread() != self._thread:
            self._thread.join(timeout=0.1)
            self._thread = None

    def _peek_bytes(self, pipe):
        """Returns number of bytes available in the pipe, or -1 if broken."""
        if msvcrt is None:
            return 0
        try:
            handle = msvcrt.get_osfhandle(pipe.fileno())
            avail = ctypes.c_ulong(0)
            res = ctypes.windll.kernel32.PeekNamedPipe(
                handle, None, 0, None, ctypes.byref(avail), None
            )
            if not res:
                return -1
            return avail.value
        except Exception:
            return -1

    def _read_frame(self, pipe, timeout_sec=3.0):
        """Reads a single IPC frame with a timeout."""
        start_t = time.perf_counter()
        while (time.perf_counter() - start_t) < timeout_sec:
            if self._stop_event.is_set():
                return None, None
            avail = self._peek_bytes(pipe)
            if avail < 0:
                return None, None
            if avail >= 8:
                break
            time.sleep(0.01)
        else:
            return None, None

        try:
            hdr = pipe.read(8)
            if not hdr or len(hdr) < 8:
                return None, None
            op, length = struct.unpack("<II", hdr)
            if length <= 0:
                return op, {}

            start_t = time.perf_counter()
            while (time.perf_counter() - start_t) < timeout_sec:
                if self._stop_event.is_set():
                    return None, None
                avail = self._peek_bytes(pipe)
                if avail < 0:
                    return None, None
                if avail >= length:
                    break
                time.sleep(0.01)
            else:
                return None, None

            data = pipe.read(length)
            if not data or len(data) < length:
                return None, None
            return op, json.loads(data.decode("utf-8"))
        except Exception:
            return None, None

    def _send_frame(self, pipe, opcode, payload_dict):
        """Encodes and writes an IPC frame to the named pipe."""
        try:
            payload_bytes = json.dumps(payload_dict).encode("utf-8")
            hdr = struct.pack("<II", opcode, len(payload_bytes))
            pipe.write(hdr + payload_bytes)
            return True
        except Exception:
            return False

    def _authenticate_and_subscribe(self, pipe):
        """Performs Discord IPC handshake, auth/token exchange, and event subscription."""
        # 1. Opcode 0: Handshake
        if not self._send_frame(pipe, 0, {"v": 1, "client_id": STREAMKIT_CLIENT_ID}):
            return False
        _op, ready = self._read_frame(pipe, timeout_sec=2.0)
        if not ready or ready.get("evt") != "READY":
            return False

        # 2. Try authentication with cached token if available
        authed = False
        token_is_non_functional = False
        if not self._access_token:
            self._access_token = load_persisted_token()

        had_token = bool(self._access_token)
        if had_token:
            if self._send_frame(
                pipe,
                1,
                {
                    "cmd": "AUTHENTICATE",
                    "args": {"access_token": self._access_token},
                    "nonce": str(time.time()),
                },
            ):
                _op, auth_res = self._read_frame(pipe, timeout_sec=2.0)
                if (
                    auth_res
                    and auth_res.get("evt") != "ERROR"
                    and auth_res.get("cmd") == "AUTHENTICATE"
                ):
                    authed = True
                else:
                    if auth_res and auth_res.get("evt") == "ERROR":
                        token_is_non_functional = True
                        self.log("Existing Discord token was rejected (non-functional).")
                    else:
                        self.log("Discord authentication unacknowledged or timed out.")

        # 3. Only ask for permission if:
        # - NOT authenticated
        # - NOT currently recording (never interrupt gameplay/recording with popups)
        # - Only on app startup (or when allow_interactive_auth is True)
        # - AND token is not present OR existing token was proven non-functional
        can_prompt = (
            not authed
            and not self.is_recording
            and (
                self.allow_interactive_auth is True
                or (
                    self.allow_interactive_auth is None
                    and not DiscordMuteWatcher._startup_auth_done
                )
            )
            and (not had_token or token_is_non_functional)
        )

        if not authed:
            if not can_prompt:
                # Do not prompt user for authorization permission
                return False

            # Mark startup auth as attempted so we never prompt again during this application session
            DiscordMuteWatcher._startup_auth_done = True

            # If existing token was non-functional, delete it before requesting a fresh one
            if token_is_non_functional:
                self._access_token = None
                delete_persisted_token()

            # Throttle authorization prompts to avoid spamming the user if rejected
            now = time.monotonic()
            if (now - self._last_auth_attempt) < 15.0:
                return False
            self._last_auth_attempt = now

            self.log("Requesting Discord IPC authorization on startup...")
            if not self._send_frame(
                pipe,
                1,
                {
                    "cmd": "AUTHORIZE",
                    "args": {
                        "client_id": STREAMKIT_CLIENT_ID,
                        "scopes": ["rpc", "rpc.voice.read"],
                    },
                    "nonce": str(time.time()),
                },
            ):
                return False

            _op, auth_resp = self._read_frame(pipe, timeout_sec=60.0)
            code = (auth_resp or {}).get("data", {}).get("code")
            if not code:
                self.log("Discord authorization declined or timed out.")
                return False

            try:
                r = requests.post(TOKEN_URL, json={"code": code}, timeout=10.0)
                if r.status_code == 200:
                    token = r.json().get("access_token")
                    if token:
                        self._access_token = token
                        save_persisted_token(token)
                        self.log("Discord authorization successful, token saved.")
            except Exception as e:
                self.log(f"Error exchanging Discord auth code: {e}")
                return False

            if not self._access_token:
                return False

            # Authenticate with newly obtained token
            if not self._send_frame(
                pipe,
                1,
                {
                    "cmd": "AUTHENTICATE",
                    "args": {"access_token": self._access_token},
                    "nonce": str(time.time()),
                },
            ):
                return False

            _op, auth_res = self._read_frame(pipe, timeout_sec=2.0)
            if not auth_res or auth_res.get("evt") == "ERROR":
                self._access_token = None
                delete_persisted_token()
                return False
            authed = True

        if not authed:
            return False

        # 4. Query current voice settings (initial mute/deaf state)
        if self._send_frame(
            pipe,
            1,
            {
                "cmd": "GET_VOICE_SETTINGS",
                "args": {},
                "nonce": str(time.time()),
            },
        ):
            _op, settings = self._read_frame(pipe, timeout_sec=2.0)
            if settings and "data" in settings:
                self._update_state_from_data(settings["data"])

        # 5. Subscribe to VOICE_SETTINGS_UPDATE events
        if not self._send_frame(
            pipe,
            1,
            {
                "cmd": "SUBSCRIBE",
                "evt": "VOICE_SETTINGS_UPDATE",
                "args": {},
                "nonce": str(time.time()),
            },
        ):
            return False

        _op, sub = self._read_frame(pipe, timeout_sec=2.0)
        return bool(sub and sub.get("evt") != "ERROR")

    def _update_state_from_data(self, data):
        """Updates mute/deaf flags and notifies listener if changed."""
        new_mute = bool(data.get("mute", False))
        new_deaf = bool(data.get("deaf", False))
        effective_muted = new_mute or new_deaf

        changed = (effective_muted != self.is_muted) or (new_deaf != self.is_deaf)
        self.is_muted = effective_muted
        self.is_deaf = new_deaf

        if changed:
            status_text = "MUTED 🔇" if self.is_muted else "UNMUTED 🎙️"
            self.log(f"Discord voice status: {status_text}")
            if self.on_mute_change:
                try:
                    self.on_mute_change(self.is_muted)
                except Exception:
                    pass

    def _run_loop(self):
        """Main IPC loop with automatic reconnection."""
        while not self._stop_event.is_set():
            pipe_path = find_discord_ipc_pipe()
            if not pipe_path:
                self.is_connected = False
                for _ in range(10):
                    if self._stop_event.is_set():
                        break
                    time.sleep(0.1)
                continue

            try:
                with open(pipe_path, "r+b", buffering=0) as pipe:
                    with self._lock:
                        self._pipe = pipe
                    success = self._authenticate_and_subscribe(pipe)
                    if not success:
                        self.is_connected = False
                        time.sleep(1.5)
                        continue

                    self.is_connected = True
                    self.log("Connected to Discord IPC (monitoring mute/deafen status).")

                    # Polling loop for events
                    while not self._stop_event.is_set():
                        avail = self._peek_bytes(pipe)
                        if avail < 0:
                            # Pipe broken (Discord closed or restarted)
                            break
                        if avail < 8:
                            time.sleep(0.04)
                            continue

                        _op, frame = self._read_frame(pipe, timeout_sec=1.0)
                        if frame is None:
                            break

                        if (
                            frame.get("cmd") == "DISPATCH"
                            and frame.get("evt") == "VOICE_SETTINGS_UPDATE"
                        ):
                            self._update_state_from_data(frame.get("data", {}))

            except Exception:
                pass
            finally:
                with self._lock:
                    self._pipe = None
                self.is_connected = False
                time.sleep(1.0)
