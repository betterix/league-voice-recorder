import glob
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

from app.config import load_config
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

DEFAULT_AUDIO_DIRECTORY = os.path.join(PROJECT_ROOT, "recordings")


def parse_rofl_game_id(rofl_path):
    """Extracts the Game ID from the .rofl replay filename."""
    try:
        filename = os.path.basename(rofl_path).replace(".rofl", "")
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
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = proc.info.get("name") or ""
            if "League of Legends.exe" in name:
                cmdline = proc.info.get("cmdline") or []
                for raw_arg in cmdline:
                    arg = str(raw_arg).strip('\'"')
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


def find_matching_audio_file(audio_directory, game_id):
    """
    Finds the best matching audio file for the game ID.
    Supports <game_id>.mp3, <game_id>_1.mp3, etc.
    Prioritizes valid non-empty files (>10 KB) and picks the largest/latest recording.
    """
    if not game_id or not audio_directory or not os.path.exists(audio_directory):
        return None

    pattern = os.path.join(audio_directory, f"{game_id}*.mp3")
    candidates = glob.glob(pattern)
    if not candidates:
        return None

    # Filter out empty or tiny stubs (< 10 KB, typically failed recordings < 1 sec)
    valid_candidates = [
        f for f in candidates if os.path.isfile(f) and os.path.getsize(f) > 10 * 1024
    ]

    # If all candidates are tiny, fall back to whatever is available
    pool = valid_candidates if valid_candidates else candidates
    # Sort by file size descending, then modification time descending
    pool.sort(key=lambda p: (os.path.getsize(p), os.path.getmtime(p)), reverse=True)
    return pool[0]


def create_vlc_instance():
    """
    Tries several VLC audio output candidates:
    Order: default (auto) -> mmdevice (WASAPI) -> directsound.
    """
    if vlc is None:
        raise RuntimeError("VLC (64-bit) is not installed or libvlc.dll was not found.")

    candidates = [
        "--quiet",
        "--quiet --aout=mmdevice",
        "--quiet --aout=directsound",
    ]
    for opts in candidates:
        try:
            instance = vlc.Instance(opts)
            if instance is not None:
                return instance
        except Exception:
            pass
    raise RuntimeError("None of the VLC audio output drivers succeeded!")


class ReplaySynchronizer:
    def __init__(self, config=None, log_callback=None, status_callback=None):
        self.config = config or load_config()
        self.log_callback = log_callback
        self.status_callback = status_callback
        self.audio_directory = self.config.get("record_folder", DEFAULT_AUDIO_DIRECTORY)
        self.volume = int(self.config.get("replay_volume", 80))
        self.sync_offset = float(self.config.get("sync_offset_ms", 0)) / 1000.0
        self.current_player = None
        self.running = False
        import threading

        self.stop_event = threading.Event()

    def set_volume(self, volume):
        """Dynamically update playback volume (0 - 100)."""
        self.volume = max(0, min(100, int(volume)))
        if self.current_player:
            try:
                self.current_player.audio_set_volume(self.volume)
            except Exception:
                pass

    def log(self, message):
        print(message)
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

    def run_sync_loop(self, audio_path, replay_pid=None):
        abs_path = os.path.abspath(audio_path)

        try:
            instance = create_vlc_instance()
            player = instance.media_player_new()
            media = instance.media_new(abs_path)
            player.set_media(media)
            player.audio_set_volume(self.volume)
            self.current_player = player
        except Exception as e:
            self.log(t("logs.vlc_init_error", error=e))
            return

        self.log(t("logs.vlc_loaded", file=os.path.basename(abs_path)))
        self.update_status("SYNCING", os.path.basename(abs_path))

        API_URL = "https://127.0.0.1:2999/replay/playback"
        is_playing = False
        last_game_time = -1
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

                    game_time = data.get("time", 0)
                    replay_paused = data.get("paused", False)
                    replay_speed = data.get("speed", 1.0)

                    # Update playback speed if changed
                    if replay_speed != last_speed and replay_speed > 0:
                        player.set_rate(replay_speed)
                        self.log(t("logs.replay_speed", old=last_speed, new=replay_speed))
                        last_speed = replay_speed

                    # 1. Match playing and not paused (game_time > 0.5s)
                    if game_time > 0.5 and not replay_paused:
                        current_state = player.get_state() if vlc else 0
                        ended_state = getattr(vlc.State, "Ended", 6) if vlc else 6
                        if current_state == ended_state:
                            player.stop()
                            player.set_media(media)
                            self.log(t("logs.vlc_end_reload"))

                        vlc_time = (
                            (player.get_time() / 1000.0)
                            if hasattr(player, "get_time")
                            else -1.0
                        )

                        target_time = max(0.0, game_time + self.sync_offset)

                        if not is_playing:
                            # Resuming from pause or starting
                            time_diff = (
                                abs(game_time - paused_game_time)
                                if paused_game_time >= 0
                                else 999.0
                            )
                            if paused_game_time >= 0 and time_diff < 0.25:
                                # Replay was unpaused at the same spot — RESUME PLAYBACK INSTANTLY!
                                # Avoid set_time() which flushes audio pipeline and introduces a ~0.1s lag!
                                player.play()
                                player.set_rate(replay_speed)
                                player.audio_set_volume(self.volume)
                            else:
                                # Timeline jump or first start
                                player.play()
                                player.set_time(int(target_time * 1000))
                                player.set_rate(replay_speed)
                                player.audio_set_volume(self.volume)

                            volume = player.audio_get_volume()
                            self.log(
                                t(
                                    "logs.replay_playing",
                                    time=game_time,
                                    speed=replay_speed,
                                    volume=volume,
                                )
                            )
                            is_playing = True
                        else:
                            # Already playing: check for timeline jump / scrub or drift
                            time_jump = abs(game_time - last_game_time) > 0.5
                            drift = abs(vlc_time - target_time) if vlc_time >= 0 else 0
                            if time_jump or drift > 1.0:
                                player.set_time(int(target_time * 1000))
                                player.set_rate(replay_speed)
                                if time_jump:
                                    volume = player.audio_get_volume()
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
                        if is_playing:
                            player.pause()
                            is_playing = False
                            paused_game_time = game_time
                            self.log(t("logs.replay_paused", time=game_time))
                            self.update_status("PAUSED", f"{game_time:.1f}s")
                        elif paused_game_time >= 0 and abs(game_time - paused_game_time) > 0.5:
                            # User scrubbed while paused in League replay
                            player.set_time(int(game_time * 1000))
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

    def run_loop(self):
        self.stop_event.clear()
        self.running = True
        self.log(t("logs.replay_watcher_started"))
        self.log(t("logs.replay_watching_dir", dir=self.audio_directory))
        self.update_status("WATCHING", t("replay.waiting_for_replay"))

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
                    matched_file = find_matching_audio_file(self.audio_directory, game_id)

                    if matched_file:
                        self.log(
                            t("logs.audio_found", file=os.path.basename(matched_file))
                        )
                        last_handled_path = rofl_path
                        self.run_sync_loop(matched_file, replay_pid=rofl_pid)
                        self.update_status("WATCHING", t("replay.waiting_for_replay"))
                        if not is_process_running(rofl_pid):
                            last_handled_path = None
                    else:
                        self.log(
                            t(
                                "logs.audio_not_found",
                                game_id=game_id,
                                dir=self.audio_directory,
                            )
                        )
                        self.update_status("ERROR", f"{game_id}.mp3")
                        last_handled_path = rofl_path

            # Polling delay
            for _ in range(5):
                if self.stop_event.is_set():
                    break
                time.sleep(0.1)

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
