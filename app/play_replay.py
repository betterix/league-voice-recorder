import functools
import os
from pathlib import Path
import sys
import time

# Ensure project root is in sys.path when run directly
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import psutil
import requests

from app.config import get_current_user_documents_recordings_dir, load_config
from app.i18n import t

# Setup VLC DLL path from config or default
_cfg = load_config()
VLC_DIR = _cfg.get("vlc_dir", r"C:\Program Files\VideoLAN\VLC")

if os.path.exists(VLC_DIR):
    os.environ["PATH"] = VLC_DIR + os.pathsep + os.environ["PATH"]
    if hasattr(os, "add_dll_directory"):
        try:
            os.add_dll_directory(VLC_DIR)
        except Exception:
            pass

try:
    import vlc
except Exception:
    vlc = None

requests.packages.urllib3.disable_warnings()

DEFAULT_AUDIO_DIRECTORY = get_current_user_documents_recordings_dir()


@functools.lru_cache(maxsize=256)
def parse_rofl_game_id(rofl_path):
    """Extracts the Game ID from the .rofl replay filename."""
    try:
        filename = os.path.basename(rofl_path)
        if filename.lower().endswith(".rofl"):
            filename = filename[:-5]
        if "-" in filename:
            raw_id = filename.split("-")[-1]
            if raw_id.isdigit():
                return raw_id
        elif filename.isdigit():
            return filename
    except Exception as e:
        print(f"Error parsing replay filename: {e}")
    return None


def detect_running_rofl_process():
    """Finds the running League replay process and returns (rofl_path, pid)."""
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = proc.info.get("name") or ""
            if "League of Legends.exe" in name:
                cmdline = proc.info.get("cmdline")
                if cmdline is None and hasattr(proc, "cmdline"):
                    try:
                        cmdline = proc.cmdline()
                    except Exception:
                        cmdline = []
                cmdline = cmdline or []
                for raw_arg in cmdline:
                    arg = str(raw_arg).strip("'\"")
                    if arg.lower().endswith(".rofl"):
                        return arg, proc.info.get("pid")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None, None


def detect_running_rofl_file():
    """Finds the running League replay process and returns the .rofl file path."""
    rofl_path, _ = detect_running_rofl_process()
    return rofl_path


def is_process_running(pid):
    """Fast check whether a process with the given PID is still active."""
    if not pid:
        return False
    try:
        p = psutil.Process(pid)
        return p.is_running() and "League of Legends" in (p.name() or "")
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def find_matching_audio_file(audio_directory, game_id, prefer_suppressed=False):
    """
    Finds the best matching audio file for the game ID.
    Supports <game_id>.mp3, <game_id>_suppressed.mp3, <game_id>_1.mp3, etc.
    Prioritizes valid non-empty files (>10 KB) and honors prefer_suppressed preference with fallback.
    """
    if not game_id or not audio_directory or not os.path.exists(audio_directory):
        return None

    prefix = str(game_id)
    candidates: list[tuple[str, int, float]] = []
    try:
        with os.scandir(audio_directory) as it:
            for entry in it:
                lower = entry.name.lower()
                if entry.name.startswith(prefix) and lower.endswith((".opus", ".mp3")):
                    try:
                        stat = entry.stat()
                        candidates.append((entry.path, stat.st_size, stat.st_mtime))
                    except OSError:
                        pass
    except OSError:
        return None

    if not candidates:
        return None

    # Filter out empty or tiny stubs (< 4 KB, typically failed recordings < 1 sec)
    valid_candidates = [c for c in candidates if c[1] > 4 * 1024]
    pool = valid_candidates if valid_candidates else candidates

    # Separate into suppressed and raw pools
    suppressed_pool = [c for c in pool if "_suppressed" in os.path.basename(c[0]).lower()]
    raw_pool = [c for c in pool if "_suppressed" not in os.path.basename(c[0]).lower()]

    # Sort each pool prioritizing Opus (.opus) first, then modification time, then file size
    def _rank_candidate(c):
        is_opus = 1 if c[0].lower().endswith(".opus") else 0
        return (is_opus, c[2], c[1])

    suppressed_pool.sort(key=_rank_candidate, reverse=True)
    raw_pool.sort(key=_rank_candidate, reverse=True)

    if prefer_suppressed:
        if suppressed_pool:
            return suppressed_pool[0][0]
        if raw_pool:
            return raw_pool[0][0]
    else:
        if raw_pool:
            return raw_pool[0][0]
        if suppressed_pool:
            return suppressed_pool[0][0]

    return None


def create_vlc_instance():
    """
    Tries several VLC audio output candidates:
    Order: default (auto) -> mmdevice (WASAPI) -> directsound.
    """
    if vlc is None:
        raise RuntimeError("VLC (64-bit) is not installed or libvlc.dll was not found.")

    candidates = (
        "--quiet --gain=2.5",
        "--quiet --gain=2.5 --aout=mmdevice",
        "--quiet --gain=2.5 --aout=directsound",
        "--quiet",
        "--quiet --aout=mmdevice",
        "--quiet --aout=directsound",
    )
    for opts in candidates:
        try:
            instance = vlc.Instance(opts)
            if instance is not None:
                return instance
        except Exception:
            pass
    raise RuntimeError("None of the VLC audio output drivers succeeded!")


def is_replay_or_app_focused(replay_pid=None):
    """Checks if foreground window belongs to League replay or our process."""
    if not sys.platform.startswith("win"):
        return True
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return False
        fg_pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(fg_pid))
        target_pid = fg_pid.value
        if not target_pid:
            return False
        if target_pid == os.getpid():
            return True
        if replay_pid and target_pid == replay_pid:
            return True
        try:
            proc = psutil.Process(target_pid)
            if "League of Legends" in (proc.name() or ""):
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        return False
    except Exception:
        return False


class ReplaySynchronizer:
    def __init__(
        self,
        config=None,
        log_callback=None,
        status_callback=None,
        mute_callback=None,
        voice_mode_callback=None,
    ):
        self.config = config or load_config()
        self.log_callback = log_callback
        self.status_callback = status_callback
        self.mute_callback = mute_callback
        self.voice_mode_callback = voice_mode_callback
        self.audio_directory = self.config.get("record_folder", DEFAULT_AUDIO_DIRECTORY)
        self.volume = int(self.config.get("replay_volume", 80))
        self.prefer_suppressed = False
        self.current_player = None
        self.vlc_instance = None
        self.current_audio_path = None
        self.current_game_id = None
        self._pending_voice_mode = None
        self.running = False
        self.is_muted = False
        self.last_mute_toggle_time = 0.0
        self.current_replay_pid = None
        self._hotkey_thread = None
        self.last_game_time = 0.0
        self.duration = 1800.0
        self.is_paused = False
        self.speed = 1.0
        self._cached_audio_meter = None
        import threading

        self.stop_event = threading.Event()

    def get_audio_level(self):
        """Returns the live peak audio meter value (0.0 to 1.0) of the voice recording playback in VLC."""
        if not self.current_player or self.is_muted or self.is_paused or not self.running:
            return 0.0
        try:
            if self._cached_audio_meter is None and sys.platform.startswith("win"):
                from pycaw.pycaw import AudioUtilities, IAudioMeterInformation

                cur_pid = os.getpid()
                for s in AudioUtilities.GetAllSessions():
                    if s.ProcessId == cur_pid:
                        self._cached_audio_meter = s._ctl.QueryInterface(IAudioMeterInformation)
                        break
            if self._cached_audio_meter:
                raw_val = float(self._cached_audio_meter.GetPeakValue())
                from app.native_audio import scale_vu

                return scale_vu(raw_val)
        except Exception:
            pass
        return 0.0

    def set_volume(self, volume):
        """Dynamically update playback volume (0 - 200)."""
        self.volume = max(0, min(200, int(volume)))
        if self.current_player and not self.is_muted:
            try:
                self.current_player.audio_set_volume(self.volume)
            except Exception:
                pass

    def set_mute(self, muted):
        """Sets audio mute state."""
        self.is_muted = bool(muted)
        if self.current_player:
            try:
                if self.is_muted:
                    self.current_player.audio_set_mute(True)
                    self.current_player.audio_set_volume(0)
                else:
                    self.current_player.audio_set_mute(False)
                    self.current_player.audio_set_volume(self.volume)
            except Exception:
                pass
        if self.is_muted:
            self.log(t("logs.replay_muted"))
        else:
            self.log(t("logs.replay_unmuted"))
        if self.mute_callback:
            try:
                self.mute_callback(self.is_muted)
            except Exception:
                pass
        return self.is_muted

    def toggle_mute(self):
        """Toggles audio mute state with debouncing."""
        now = time.time()
        if now - self.last_mute_toggle_time < 0.25:
            return self.is_muted
        self.last_mute_toggle_time = now
        return self.set_mute(not self.is_muted)

    def set_voice_mode(self, prefer_suppressed):
        """No-op on main branch."""
        return False

    def toggle_voice_mode(self):
        """No-op on main branch."""
        return False

    def _hotkey_listener_loop(self):
        """Background listener for M hotkey (VK_M = 0x4D) to mute/unmute replay audio."""
        if not sys.platform.startswith("win"):
            return
        import ctypes

        m_was_down = False
        while not self.stop_event.is_set():
            try:
                m_down = bool(ctypes.windll.user32.GetAsyncKeyState(0x4D) & 0x8000)
                if m_down and not m_was_down:
                    if is_replay_or_app_focused(self.current_replay_pid):
                        self.toggle_mute()
                m_was_down = m_down
            except Exception:
                pass
            time.sleep(0.03)

    def log(self, message):
        try:
            print(message)
        except UnicodeEncodeError:
            try:
                encoding = sys.stdout.encoding or "utf-8"
                print(str(message).encode(encoding, errors="replace").decode(encoding))
            except Exception:
                pass
        if self.log_callback:
            try:
                self.log_callback(message)
            except Exception:
                pass

    def update_status(self, status, detail=""):
        if self.status_callback:
            try:
                self.status_callback(status, detail)
            except Exception:
                pass

    def run_sync_loop(self, audio_path, replay_pid=None, game_id=None):
        abs_path = os.path.abspath(audio_path)
        self.current_audio_path = abs_path
        self.current_game_id = game_id
        self.current_replay_pid = replay_pid

        try:
            instance = create_vlc_instance()
            player = instance.media_player_new()
            media = instance.media_new(abs_path)
            player.set_media(media)
            if self.is_muted:
                player.audio_set_mute(True)
                player.audio_set_volume(0)
            else:
                player.audio_set_mute(False)
                player.audio_set_volume(self.volume)
            self.current_player = player
            self.vlc_instance = instance
        except Exception as e:
            self.log(t("logs.vlc_init_error", error=e))
            return

        self.log(t("logs.vlc_loaded", file=os.path.basename(abs_path)))
        self.update_status("SYNCING", os.path.basename(abs_path))

        API_URL = "https://127.0.0.1:2999/replay/playback"
        is_playing = False
        last_game_time = -1.0
        last_seek_wall_time = 0.0
        paused_game_time = -1.0
        last_speed = 1.0
        has_connected = False
        consecutive_failures = 0
        last_waiting_log = 0.0

        # Initial grace wait for League engine initialization before polling API
        self.log(t("logs.replay_init_wait"))
        self.update_status(
            "WATCHING", f"{os.path.basename(audio_path)} (initializing League replay...)"
        )
        for _ in range(30):  # 3.0s in 0.1s slices (responsive to stop_event)
            if self.stop_event.is_set():
                break
            time.sleep(0.1)

        session = requests.Session()
        session.verify = False

        while not self.stop_event.is_set():
            try:
                response = session.get(API_URL, timeout=0.25)
                if response.status_code == 200:
                    data = response.json()
                    has_connected = True
                    consecutive_failures = 0

                    game_time = float(data.get("time", 0))
                    replay_paused = bool(data.get("paused", False))
                    replay_speed = min(float(data.get("speed", 1.0)), 4.0)
                    if "length" in data and float(data.get("length", 0)) > 0:
                        self.duration = float(data["length"])
                    self.last_game_time = game_time
                    self.is_paused = replay_paused
                    self.speed = replay_speed
                    now_wall = time.time()

                    # 1. Match playing and not paused (game_time > 0.5s)
                    if game_time > 0.5 and not replay_paused:
                        current_state = player.get_state() if vlc else 0
                        ended_state = getattr(vlc.State, "Ended", 6) if vlc else 6
                        if current_state == ended_state:
                            player.stop()
                            active_file = self.current_audio_path or abs_path
                            player.set_media(instance.media_new(active_file))
                            self.log(t("logs.vlc_end_reload"))

                        vlc_time = (
                            (player.get_time() / 1000.0) if hasattr(player, "get_time") else -1.0
                        )

                        target_time = max(0.0, game_time)

                        if not is_playing:
                            # Resuming from pause or starting
                            time_diff = (
                                abs(game_time - paused_game_time)
                                if paused_game_time >= 0
                                else 999.0
                            )
                            if paused_game_time >= 0 and time_diff < 0.25:
                                # Replay was unpaused at the same spot — RESUME PLAYBACK INSTANTLY!
                                # player.play() starts playback cleanly without seeking pipeline flush.
                                player.play()
                                player.set_rate(replay_speed)
                            else:
                                # Timeline jump, initial start, or scrubbed while paused
                                player.play()
                                player.set_time(int(target_time * 1000))
                                player.set_rate(replay_speed)
                                last_seek_wall_time = now_wall

                            if self.is_muted:
                                player.audio_set_mute(True)
                                player.audio_set_volume(0)
                            else:
                                player.audio_set_mute(False)
                                player.audio_set_volume(self.volume)

                            volume = 0 if self.is_muted else player.audio_get_volume()
                            self.log(
                                t(
                                    "logs.replay_playing",
                                    time=game_time,
                                    speed=replay_speed,
                                    volume=volume,
                                )
                            )
                            is_playing = True
                            last_speed = replay_speed
                        else:
                            # Already playing: check for playback speed change
                            if replay_speed != last_speed and replay_speed > 0:
                                player.set_rate(replay_speed)
                                self.log(t("logs.replay_speed", old=last_speed, new=replay_speed))
                                last_speed = replay_speed

                            time_jump = abs(game_time - last_game_time) > 0.5
                            drift = abs(vlc_time - target_time) if vlc_time >= 0 else 0.0

                            # Only seek if jump or significant drift (>1.0s) AND respected 800ms seek cooldown
                            if (time_jump or drift > 1.0) and (now_wall - last_seek_wall_time >= 0.8):
                                player.set_time(int(target_time * 1000))
                                player.set_rate(replay_speed)
                                last_seek_wall_time = now_wall
                                if time_jump:
                                    volume = 0 if self.is_muted else player.audio_get_volume()
                                    self.log(
                                        t(
                                            "logs.replay_playing",
                                            time=game_time,
                                            speed=replay_speed,
                                            volume=volume,
                                        )
                                    )

                        time_min = int(game_time // 60)
                        time_sec = int(game_time % 60)
                        self.update_status(
                            "SYNCING",
                            f"{os.path.basename(audio_path)} [{time_min:02d}:{time_sec:02d} @ {replay_speed}x]",
                        )
                        last_game_time = game_time

                    # 2. Replay paused or stopped
                    else:
                        self.last_game_time = game_time
                        self.is_paused = True
                        if is_playing:
                            player.pause()
                            is_playing = False
                            paused_game_time = game_time
                            self.log(t("logs.replay_paused", time=game_time))
                            self.update_status("PAUSED", f"{game_time:.1f}s")
                        elif paused_game_time >= 0 and abs(game_time - paused_game_time) > 0.5:
                            # User scrubbed while paused in League replay
                            target_time = max(0.0, game_time)
                            player.set_time(int(target_time * 1000))
                            last_seek_wall_time = now_wall
                            paused_game_time = game_time

                else:
                    consecutive_failures += 1

            except requests.exceptions.RequestException as e:
                consecutive_failures += 1
                now = time.time()

                proc_alive = (
                    is_process_running(replay_pid)
                    if replay_pid
                    else (detect_running_rofl_file() is not None)
                )

                if not has_connected:
                    # Replay is still loading inside League client
                    if now - last_waiting_log >= 4.0:
                        self.log(t("logs.replay_api_waiting", error=type(e).__name__))
                        self.update_status(
                            "WATCHING",
                            f"{os.path.basename(audio_path)} (loading in League...)",
                        )
                        last_waiting_log = now

                    # Check if the League replay process was closed before finished loading
                    if not proc_alive:
                        self.log(t("logs.replay_closed"))
                        break
                else:
                    # Was connected, but connection dropped
                    if (consecutive_failures >= 5 and not proc_alive) or consecutive_failures >= 60:
                        self.log(t("logs.replay_closed"))
                        break

            # Sleep in small slices: 1.0s while loading to avoid spamming the port, 0.03s once live
            sleep_time = 1.0 if not has_connected else 0.03
            slice_count = max(1, int(sleep_time / 0.03))
            for _ in range(slice_count):
                if self.stop_event.is_set():
                    break
                time.sleep(0.03)

        try:
            player.stop()
        except Exception:
            pass
        self.current_player = None
        self.vlc_instance = None
        self.current_audio_path = None
        self.current_game_id = None
        self._pending_voice_mode = None

    def run_loop(self):
        self.stop_event.clear()
        self.running = True
        self.log(t("logs.replay_watcher_started"))
        self.log(t("logs.replay_watching_dir", dir=self.audio_directory))
        self.update_status("WATCHING", t("replay.waiting_for_replay"))

        import threading

        if sys.platform.startswith("win"):
            self._hotkey_thread = threading.Thread(
                target=self._hotkey_listener_loop, daemon=True
            )
            self._hotkey_thread.start()

        last_handled_path = None

        while not self.stop_event.is_set():
            rofl_path, rofl_pid = detect_running_rofl_process()

            if rofl_path is None:
                last_handled_path = None
                self.update_status("WATCHING", t("replay.waiting_for_replay"))
            elif rofl_path != last_handled_path:
                self.log(t("logs.rofl_detected", file=os.path.basename(rofl_path)))
                game_id = parse_rofl_game_id(rofl_path)

                if game_id:
                    self.current_game_id = game_id
                    matched_file = find_matching_audio_file(
                        self.audio_directory, game_id, prefer_suppressed=self.prefer_suppressed
                    )

                    if matched_file:
                        self.log(t("logs.audio_found", file=os.path.basename(matched_file)))
                        last_handled_path = rofl_path
                        self.run_sync_loop(matched_file, replay_pid=rofl_pid, game_id=game_id)
                        if not is_process_running(rofl_pid):
                            break
                        self.update_status("WATCHING", t("replay.waiting_for_replay"))
                    else:
                        self.log(
                            t(
                                "logs.audio_not_found",
                                game_id=game_id,
                                dir=self.audio_directory,
                            )
                        )
                        self.update_status("ERROR", f"{game_id}")
                        last_handled_path = rofl_path

            # Polling delay
            for _ in range(5):
                if self.stop_event.is_set():
                    break
                time.sleep(0.1)

        if self._hotkey_thread and self._hotkey_thread.is_alive():
            try:
                self._hotkey_thread.join(timeout=0.1)
            except Exception:
                pass
            self._hotkey_thread = None

        if self.current_player:
            try:
                self.current_player.stop()
            except Exception:
                pass
            self.current_player = None

        self.running = False
        self.log(t("logs.replay_watcher_stopped"))
        self.update_status("STOPPED", "")

    def stop(self):
        self.stop_event.set()
        if self._hotkey_thread and self._hotkey_thread.is_alive():
            try:
                self._hotkey_thread.join(timeout=0.1)
            except Exception:
                pass
            self._hotkey_thread = None
        if self.current_player:
            try:
                self.current_player.stop()
            except Exception:
                pass
            self.current_player = None


def main():
    sync = ReplaySynchronizer()
    sync.run_loop()


if __name__ == "__main__":
    main()
