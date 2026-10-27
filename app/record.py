import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

# Ensure project root is in sys.path when run directly
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import psutil
import requests
import urllib3

from app.config import get_ffmpeg_path, load_config
from app.i18n import t
from app.native_audio import NativeAudioRecorder

urllib3.disable_warnings()


def find_league_install_dir():
    """Finds the League of Legends installation directory containing lockfile."""
    # 1. Fast lookup via psutil
    try:
        for proc in psutil.process_iter(["name", "exe"]):
            try:
                name = proc.info.get("name") or ""
                if "LeagueClientUx" in name or "LeagueClient" in name:
                    exe = proc.info.get("exe")
                    if exe:
                        d = os.path.dirname(exe)
                        if os.path.exists(os.path.join(d, "lockfile")):
                            return d
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        pass

    # 2. PowerShell fallback (original logic)
    try:
        cmd = [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-Process -Name 'League*' -ErrorAction SilentlyContinue | Select-Object Name,Path | ConvertTo-Json -Compress",
        ]
        out = (
            subprocess.check_output(cmd, creationflags=subprocess.CREATE_NO_WINDOW, timeout=5)
            .decode("utf-8", errors="ignore")
            .strip()
        )
        if out:
            parsed = json.loads(out)
            if not isinstance(parsed, list):
                parsed = [parsed]

            parsed.sort(
                key=lambda x: 0 if x.get("Name") and "LeagueClientUx" in x.get("Name") else 1
            )

            for proc in parsed:
                p_path = proc.get("Path")
                if p_path:
                    d = os.path.dirname(p_path)
                    if os.path.exists(os.path.join(d, "lockfile")):
                        return d
    except Exception:
        pass

    # 3. Known drive search fallback
    drives = list("CDEFGHIJ")
    subpaths = [
        r"Riot Games\League of Legends",
        r"Program Files\Riot Games\League of Legends",
        r"Program Files (x86)\Riot Games\League of Legends",
    ]
    for drive in drives:
        for sub in subpaths:
            candidate = f"{drive}:\\{sub}"
            if os.path.exists(os.path.join(candidate, "lockfile")):
                return candidate
    return None


class LeagueRecorder:
    def __init__(self, config=None, log_callback=None, status_callback=None):
        self.config = config or load_config()
        self.log_callback = log_callback
        self.status_callback = status_callback

        self.record_folder = self.config.get("record_folder", os.path.abspath("recordings"))
        self.native_recorder = NativeAudioRecorder(
            record_folder=self.record_folder, log_callback=self.log
        )
        self.native_recorder.set_target_process(
            pid=int(self.config.get("target_audio_pid", -1)),
            name=self.config.get("target_audio_name", "Discord"),
        )
        self.check_time = float(self.config.get("check_time", 0.2))

        os.makedirs(self.record_folder, exist_ok=True)

        self.recording = False
        self.is_paused = False
        self.real_game_id = None
        self.last_game_time = -1
        self.first_game_time = 0.0
        self.stop_event = threading.Event()

        # Replay detection cache
        self._cached_game_pid = None
        self._cached_is_replay = False

        # Persistent HTTP session for live API polling
        self.session = requests.Session()
        self.session.verify = False

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

    def fetch_game_id_background(self):
        self.log(t("logs.query_game_id"))
        while self.recording and not self.real_game_id and not self.stop_event.is_set():
            install_dir = find_league_install_dir()
            if install_dir:
                lockfile_path = os.path.join(install_dir, "lockfile")
                if os.path.exists(lockfile_path):
                    try:
                        with open(lockfile_path, "r") as f:
                            data = f.read().split(":")
                        port = data[2]
                        password = data[3]
                        auth = base64.b64encode(f"riot:{password}".encode()).decode()

                        r = requests.get(
                            f"https://127.0.0.1:{port}/lol-gameflow/v1/session",
                            headers={"Authorization": f"Basic {auth}"},
                            verify=False,
                            timeout=2,
                        )
                        gid = r.json().get("gameData", {}).get("gameId")
                        if gid and gid > 0:
                            self.real_game_id = str(gid)
                            self.log(t("logs.game_id_found", game_id=self.real_game_id))
                            cur_time = max(0.0, self.last_game_time)
                            time_str = f"{int(cur_time // 60):02d}:{int(cur_time % 60):02d}"
                            self.update_status(
                                "RECORDING",
                                t("record.match_time", game_id=self.real_game_id, time=time_str),
                            )
                            break
                    except Exception:
                        pass
            # Sleep with cancellation check
            for _ in range(15):
                if not self.recording or self.stop_event.is_set() or self.real_game_id:
                    break
                time.sleep(0.2)

    def check_if_game_is_replay(self):
        """Checks if the currently running game client is a replay (.rofl) rather than a live match."""
        # Check cached verdict for active game PID
        if self._cached_game_pid:
            try:
                p = psutil.Process(self._cached_game_pid)
                if p.is_running() and "League of Legends.exe" in p.name():
                    return self._cached_is_replay
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                self._cached_game_pid = None
                self._cached_is_replay = False

        # 1. Fast process inspection for .rofl in League command-line arguments
        try:
            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                name = proc.info.get("name") or ""
                if "League of Legends.exe" in name:
                    pid = proc.info.get("pid")
                    cmdline = proc.info.get("cmdline") or []
                    is_rofl = any(arg.lower().endswith(".rofl") for arg in cmdline)
                    self._cached_game_pid = pid
                    self._cached_is_replay = is_rofl
                    if is_rofl:
                        return True
        except Exception:
            pass

        # 2. League Replay Playback API (:2999/replay/playback)
        try:
            r = self.session.get(
                "https://127.0.0.1:2999/replay/playback",
                timeout=0.1,
            )
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict) and "time" in data:
                    self._cached_is_replay = True
                    return True
        except Exception:
            pass

        return False

    def is_league_game_running(self):
        """Checks if the League of Legends 3D game client process is currently running."""
        try:
            for proc in psutil.process_iter(["name"]):
                try:
                    if "League of Legends.exe" in (proc.info["name"] or ""):
                        return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception:
            pass
        return False

    def abort_recording(self):
        """Immediately aborts recording if started by mistake (e.g., during replay)."""
        if not self.recording:
            return
        self.recording = False
        try:
            self.native_recorder.stop_and_finalize("aborted")
        except Exception:
            pass
        self.log(t("logs.replay_abort"))

    def close(self):
        """Closes all recording and monitoring resources."""
        self.abort_recording()
        if hasattr(self, "native_recorder") and self.native_recorder:
            try:
                self.native_recorder.close()
            except Exception:
                pass

    def start_recording(self, initial_game_time=0.0, t_poll_start=None):
        """
        Starts audio recording immediately with zero latency.
        Accurately accounts for invocation latency to ensure sample-accurate alignment.
        """
        if self.recording:
            return

        if self.check_if_game_is_replay():
            self.log(t("logs.replay_guard"))
            return

        t_start = time.perf_counter()

        session_id = str(int(time.time()))
        self.native_recorder.start_recording(session_id)
        self.recording = True
        self.is_paused = False

        t_now = time.perf_counter()
        base_time = float(initial_game_time) if initial_game_time is not None else 0.0
        # If match just started (<= 2.0s), 0:00 alignment is exactly 0.0 (NO silence prepended!)
        if base_time <= 2.0:
            self.first_game_time = 0.0
            self.last_game_time = max(0.0, base_time)
        else:
            ref_time = t_poll_start if t_poll_start is not None else t_start
            delay = max(0.0, t_now - ref_time)
            self.first_game_time = base_time + delay
            self.last_game_time = self.first_game_time
        self.real_game_id = None
        self.log(t("logs.match_detected", time=self.first_game_time))
        init_time = max(0.0, self.first_game_time)
        init_time_str = f"{int(init_time // 60):02d}:{int(init_time % 60):02d}"
        self.update_status("RECORDING", t("record.live_match_time", time=init_time_str))
        threading.Thread(target=self.fetch_game_id_background, daemon=True).start()

    def convert_to_mp3(self, video_path, base_name, initial_offset=0.0):
        """
        Converts recording to high quality MP3 with sample-accurate alignment.
        """
        self.log(t("logs.converting_mp3", name=base_name))
        self.update_status("CONVERTING", f"{base_name}...")
        mp3_path = os.path.join(self.record_folder, f"{base_name}.mp3")

        counter = 1
        while os.path.exists(mp3_path):
            mp3_path = os.path.join(self.record_folder, f"{base_name}_{counter}.mp3")
            counter += 1

        ffmpeg_cmd = get_ffmpeg_path()
        try:
            cmd = [ffmpeg_cmd, "-y", "-i", video_path]
            if initial_offset < -0.005:
                cmd.extend(["-ss", f"{abs(initial_offset):.3f}"])
            elif initial_offset > 0.005:
                delay_ms = int(initial_offset * 1000)
                cmd.extend(["-af", f"adelay=delays={delay_ms}:all=1"])

            cmd.extend(["-q:a", "0", "-map", "a", mp3_path])

            subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            os.remove(video_path)

            if os.path.exists(mp3_path) and os.path.getsize(mp3_path) < 10 * 1024:
                try:
                    os.remove(mp3_path)
                except Exception:
                    pass
                self.log(f"⚠️ Discarded tiny stub recording (<10KB): {os.path.basename(mp3_path)}")
                self.update_status("WATCHING", t("record.waiting_for_match"))
                return

            self.log(t("logs.saved_mp3", path=mp3_path))
            self.update_status("SAVED", f"{os.path.basename(mp3_path)}")
        except FileNotFoundError:
            self.log(t("logs.ffmpeg_missing", path=video_path))
        except Exception as e:
            self.log(t("logs.convert_error", error=e))

    def stop_recording_and_rename(self):
        if not self.recording:
            return

        self.log(t("logs.rec_stop"))
        self.update_status("SAVING", t("record.saving_audio"))

        self.recording = False
        # Quick final attempt to retrieve real Game ID if not found yet
        if not self.real_game_id:
            try:
                install_dir = find_league_install_dir()
                if install_dir:
                    lockfile_path = os.path.join(install_dir, "lockfile")
                    if os.path.exists(lockfile_path):
                        with open(lockfile_path, "r") as f:
                            data = f.read().split(":")
                        port = data[2]
                        password = data[3]
                        auth = base64.b64encode(f"riot:{password}".encode()).decode()
                        r = requests.get(
                            f"https://127.0.0.1:{port}/lol-gameflow/v1/session",
                            headers={"Authorization": f"Basic {auth}"},
                            verify=False,
                            timeout=1.5,
                        )
                        gid = r.json().get("gameData", {}).get("gameId")
                        if gid and gid > 0:
                            self.real_game_id = str(gid)
                            self.log(t("logs.game_id_found", game_id=self.real_game_id))
            except Exception:
                pass

        base_name = (
            self.real_game_id if self.real_game_id else f"Scrim_{time.strftime('%Y%m%d_%H%M%S')}"
        )
        final_mp3 = self.native_recorder.stop_and_finalize(
            base_name, initial_offset=self.first_game_time
        )
        if final_mp3:
            self.log(t("logs.saved_mp3", path=final_mp3))
            self.update_status("SAVED", f"{os.path.basename(final_mp3)}")
            # Match recording completed: automatically stop listening
            self.stop()
        else:
            self.update_status("WATCHING", t("record.waiting_for_match"))
        self.first_game_time = 0.0

    def run_loop(self):
        self.stop_event.clear()
        self.log(t("logs.recorder_started"))
        self.update_status("WATCHING", t("record.waiting_for_match"))

        consecutive_misses = 0
        pause_tick_count = 0

        while not self.stop_event.is_set():
            t_poll_start = time.perf_counter()
            game_time = None
            try:
                # 1. Try lightweight gamestats endpoint (avoids 500 errors during loading screen)
                try:
                    r = self.session.get(
                        "https://127.0.0.1:2999/liveclientdata/gamestats",
                        timeout=0.15,
                    )
                    if r.status_code == 200:
                        data = r.json()
                        if isinstance(data, dict):
                            game_time = data.get("gameTime")
                except Exception:
                    pass

                # 2. Fallback to allgamedata if needed
                if game_time is None:
                    try:
                        r = self.session.get(
                            "https://127.0.0.1:2999/liveclientdata/allgamedata",
                            timeout=0.15,
                        )
                        if r.status_code == 200:
                            data = r.json()
                            if isinstance(data, dict):
                                game_time = data.get("gameData", {}).get("gameTime")
                    except Exception:
                        pass

                if game_time is not None:
                    consecutive_misses = 0

                    # CRITICAL REPLAY CHECK: If a replay is running, do NOT record!
                    if self.check_if_game_is_replay():
                        if self.recording:
                            self.abort_recording()
                        self.update_status("WATCHING", t("replay.waiting_for_replay"))
                        for _ in range(10):
                            if self.stop_event.is_set():
                                break
                            time.sleep(0.1)
                        continue

                    # Only start recording once the match clock starts (> 0.0).
                    # Do NOT start recording during loading screen (game_time == 0.0) or countdown (< 0.0).
                    if not self.recording:
                        if game_time > 0.0:
                            self.start_recording(
                                initial_game_time=game_time, t_poll_start=t_poll_start
                            )
                        elif game_time == 0.0:
                            self.update_status("WATCHING", t("record.waiting_for_match"))
                        else:
                            # Pre-game countdown in progress — monitor and wait for 0:00
                            cd_str = f"-{abs(game_time):.1f}s"
                            self.update_status("WATCHING", t("record.match_countdown", time=cd_str))

                    if self.recording:
                        # Fast pause detection: require ~300ms (15 polls @ 20ms) of unchanged game_time.
                        # Detects match pauses 3x faster while staying safely clear of normal ~200ms API intervals.
                        if game_time == self.last_game_time and game_time > 0:
                            pause_tick_count += 1
                            if pause_tick_count >= 15 and not self.is_paused:
                                self.native_recorder.pause()
                                self.is_paused = True
                                self.log(t("logs.pause"))
                                p_time_val = max(0.0, game_time)
                                p_time_str = (
                                    f"{int(p_time_val // 60):02d}:{int(p_time_val % 60):02d}"
                                )
                                self.update_status(
                                    "PAUSED", t("record.paused_time", time=p_time_str)
                                )

                        elif game_time > self.last_game_time:
                            pause_tick_count = 0
                            time_val = max(0.0, game_time)
                            time_str = f"{int(time_val // 60):02d}:{int(time_val % 60):02d}"
                            if self.real_game_id:
                                detail_text = t(
                                    "record.match_time",
                                    game_id=self.real_game_id,
                                    time=time_str,
                                )
                            else:
                                detail_text = t("record.live_match_time", time=time_str)

                            if self.is_paused:
                                self.native_recorder.resume()
                                self.is_paused = False
                                self.log(t("logs.unpause"))
                                self.update_status("RECORDING", detail_text)
                            else:
                                if int(game_time) != int(self.last_game_time):
                                    self.update_status("RECORDING", detail_text)

                        self.last_game_time = game_time

                else:
                    # game_time is None (League API stopped responding or connection dropped)
                    if self.recording:
                        consecutive_misses += 1
                        if consecutive_misses >= 3:
                            league_alive = self.is_league_game_running()
                            if not league_alive or consecutive_misses >= 15:
                                self.stop_recording_and_rename()
                                consecutive_misses = 0
                                break
                    else:
                        consecutive_misses = 0

            except requests.exceptions.RequestException:
                if self.recording:
                    self.stop_recording_and_rename()
                    break
                else:
                    self.update_status("WATCHING", t("record.waiting_for_match"))

            is_high_frequency = self.recording or (game_time is not None and game_time < 0.0)
            poll_interval = 0.02 if is_high_frequency else min(0.1, self.check_time)
            slice_count = max(1, int(poll_interval / 0.02))
            for _ in range(slice_count):
                if self.stop_event.is_set():
                    break
                time.sleep(0.02)

        # Clean shutdown if stopped while recording
        if self.recording:
            self.stop_recording_and_rename()

        self.log(t("logs.recorder_stopped"))
        self.update_status("STOPPED", "")

    def stop(self):
        self.stop_event.set()


def main():
    rec = LeagueRecorder()
    rec.run_loop()


if __name__ == "__main__":
    main()
