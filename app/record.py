import base64
import glob
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

# Ensure project root is in sys.path when run directly
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import obsws_python as obs
import psutil
import requests
import urllib3

from app.config import get_ffmpeg_path, load_config
from app.i18n import t

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
                key=lambda x: (0 if x.get("Name") and "LeagueClientUx" in x.get("Name") else 1)
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

        self.obs_host = self.config.get("obs_host", "localhost")
        self.obs_port = int(self.config.get("obs_port", 4455))
        self.obs_password = self.config.get("obs_password", "")
        self.scene_name = self.config.get("scene_name", "Discord Only")
        self.record_folder = self.config.get("record_folder", os.path.abspath("recordings"))
        self.check_time = float(self.config.get("check_time", 0.2))

        os.makedirs(self.record_folder, exist_ok=True)

        self.obs_client = None
        self.recording = False
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

    def connect_obs(self):
        try:
            self.obs_client = obs.ReqClient(
                host=self.obs_host, port=self.obs_port, password=self.obs_password
            )
            self.log(t("logs.obs_connected", host=self.obs_host, port=self.obs_port))
            self.auto_configure_obs()
            self.set_scene()
            return True
        except Exception as e:
            self.log(
                t(
                    "logs.obs_connect_failed",
                    host=self.obs_host,
                    port=self.obs_port,
                    error=e,
                )
            )
            self.obs_client = None
            return False

    def auto_configure_obs(self):
        """
        Automatically sets up and configures the OBS scene ('Discord Only'):
        - Creates the scene if it does not exist.
        - Ensures Discord Audio capture is added with priority 'Window title must match'.
        - Ensures Player Microphone is added to the scene and unmuted.
        - Mutes global desktop audio so only Discord voice + player microphone are recorded.
        """
        if not self.obs_client:
            return False

        try:
            # 1. Ensure target scene exists
            scene_list_resp = self.obs_client.get_scene_list()
            scenes = [s["sceneName"] for s in scene_list_resp.scenes]
            if self.scene_name not in scenes:
                self.obs_client.create_scene(self.scene_name)
                self.log(t("logs.obs_scene_created", scene=self.scene_name))

            # 2. Get existing items in the target scene
            scene_items_resp = self.obs_client.get_scene_item_list(self.scene_name)
            current_scene_sources = {
                item["sourceName"]: item for item in scene_items_resp.scene_items
            }

            # 3. Categorize all OBS audio inputs
            inputs_resp = self.obs_client.get_input_list()
            discord_input = None
            mic_input = None
            desktop_inputs = []

            for inp in inputs_resp.inputs:
                kind = inp.get("inputKind")
                name = inp.get("inputName")
                if kind == "wasapi_process_output_capture":
                    if "discord" in name.lower() or discord_input is None:
                        discord_input = name
                elif kind == "wasapi_input_capture":
                    if "mic" in name.lower() or "mikrofon" in name.lower() or mic_input is None:
                        mic_input = name
                elif kind == "wasapi_output_capture":
                    desktop_inputs.append(name)

            # --- Configure Discord Audio ('Match title, otherwise find window of same executable') ---
            if discord_input is None or "discord" not in discord_input.lower():
                discord_input = "Discord Audio"
                try:
                    self.obs_client.create_input(
                        self.scene_name,
                        discord_input,
                        "wasapi_process_output_capture",
                        {"priority": 2, "window": ":Chrome_WidgetWin_1:Discord.exe"},
                        True,
                    )
                    self.log(t("logs.obs_discord_added", source=discord_input))
                except Exception:
                    pass

            if discord_input not in current_scene_sources:
                try:
                    self.obs_client.create_scene_item(self.scene_name, discord_input)
                except Exception:
                    pass

            # Resolve Priority Value: 2 = "Match title, otherwise find window of same executable"
            # This ensures Discord is ALWAYS captured even when switching channels, servers, or voice calls!
            priority_val = 2
            try:
                p_items = self.obs_client.get_input_properties_list_property_items(
                    discord_input, "priority"
                )
                for item in p_items.property_items:
                    p_name = (item.get("itemName") or "").lower()
                    if "executable" in p_name or "végrehajtható" in p_name:
                        priority_val = item.get("itemValue")
                        break
            except Exception:
                pass

            # Resolve Active Discord Window from OBS window list
            target_window = None
            try:
                w_items = self.obs_client.get_input_properties_list_property_items(
                    discord_input, "window"
                )
                for item in w_items.property_items:
                    val = item.get("itemValue") or ""
                    w_name = item.get("itemName") or ""
                    if "discord.exe" in val.lower() or "discord.exe" in w_name.lower():
                        target_window = val
                        break
            except Exception:
                pass

            if not target_window:
                target_window = ":Chrome_WidgetWin_1:Discord.exe"

            # Apply settings (executable matching priority + active Discord window)
            new_discord_settings = {
                "priority": priority_val,
                "window": target_window,
            }
            try:
                self.obs_client.set_input_settings(
                    discord_input, new_discord_settings, overlay=True
                )
            except Exception:
                pass

            try:
                self.obs_client.set_input_mute(discord_input, False)
            except Exception:
                pass

            # --- Configure Player Microphone ---
            if mic_input is not None:
                if mic_input not in current_scene_sources:
                    try:
                        self.obs_client.create_scene_item(self.scene_name, mic_input)
                    except Exception:
                        pass
                try:
                    self.obs_client.set_input_mute(mic_input, False)
                except Exception:
                    pass
            else:
                mic_name = "Microphone"
                try:
                    self.obs_client.create_input(
                        self.scene_name,
                        mic_name,
                        "wasapi_input_capture",
                        {"device_id": "default"},
                        True,
                    )
                    self.obs_client.set_input_mute(mic_name, False)
                except Exception:
                    pass

            # --- Mute Global Desktop Audio (No Game Audio Bleed) ---
            for d_name in desktop_inputs:
                try:
                    self.obs_client.set_input_mute(d_name, True)
                except Exception:
                    pass

            # --- Switch to Scene in OBS ---
            try:
                self.obs_client.set_current_program_scene(self.scene_name)
            except Exception:
                pass

            self.log(t("logs.obs_scene_configured", scene=self.scene_name))
            return True
        except Exception as e:
            self.log(t("logs.obs_config_failed", error=e))
            return False

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

    def get_obs_status(self):
        if not self.obs_client:
            return False, False
        try:
            st = self.obs_client.get_record_status()
            return st.output_active, st.output_paused
        except Exception:
            return False, False

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

    def abort_recording(self):
        """Immediately aborts recording if started by mistake (e.g., during replay)."""
        if not self.recording:
            return
        self.recording = False
        if self.obs_client:
            try:
                self.obs_client.stop_record()
            except Exception:
                pass
        self.log(t("logs.replay_abort"))

    def set_scene(self):
        if not self.obs_client:
            return
        try:
            self.obs_client.set_current_program_scene(self.scene_name)
            self.log(t("logs.obs_scene_switched", scene=self.scene_name))
        except Exception as e:
            self.log(t("logs.obs_scene_switch_failed", error=e))

    def start_recording(self, initial_game_time=0.0):
        """
        Starts OBS recording immediately with zero latency.
        Scene and inputs are already pre-configured to avoid delay.
        """
        if self.recording:
            return

        if self.check_if_game_is_replay():
            self.log(t("logs.replay_guard"))
            return

        if not self.obs_client and not self.connect_obs():
            self.log(
                t(
                    "logs.obs_connect_failed",
                    host=self.obs_host,
                    port=self.obs_port,
                    error="Not connected",
                )
            )
            return

        # Immediate record call to eliminate startup lag
        try:
            self.obs_client.start_record()
            self.recording = True
            self.first_game_time = (
                float(initial_game_time) if initial_game_time is not None else 0.0
            )
            self.last_game_time = self.first_game_time
            self.real_game_id = None
            self.log(t("logs.match_detected", time=self.first_game_time))
            init_time = max(0.0, self.first_game_time)
            init_time_str = f"{int(init_time // 60):02d}:{int(init_time % 60):02d}"
            self.update_status("RECORDING", t("record.live_match_time", time=init_time_str))
            threading.Thread(target=self.fetch_game_id_background, daemon=True).start()
        except Exception as e:
            self.log(t("logs.rec_start_error", error=e))
            self.update_status("ERROR", f"{e}")

    def convert_to_mp3(self, video_path, base_name, initial_offset=0.0):
        """
        Converts the OBS recording to high quality MP3.
        Applies sample-accurate time alignment:
        - If recording started after 0:00 (initial_offset > 0), prepends exact silence (adelay).
        - If recording started before 0:00 (initial_offset < 0), trims pre-game countdown (-ss).
        This guarantees that 00:00:00 in the MP3 matches game_time = 0.000 in League.
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
            cmd = [ffmpeg_cmd, "-y"]
            # If recording started before 0:00 (e.g. -5s), trim pre-game countdown
            if initial_offset < -0.05:
                cmd.extend(["-ss", f"{abs(initial_offset):.3f}"])

            cmd.extend(["-i", video_path])

            # If recording started after 0:00 (e.g. 0.8s), prepend silence so 00:00 aligns with match start
            if initial_offset > 0.05:
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
                self.log(
                    f"⚠️ Discarded tiny stub recording (<10KB): {os.path.basename(mp3_path)}"
                )
                self.update_status("IDLE", "")
                return

            self.log(t("logs.saved_mp3", path=mp3_path))
            self.update_status("IDLE", f"{os.path.basename(mp3_path)}")
        except FileNotFoundError:
            self.log(t("logs.ffmpeg_missing", path=video_path))
        except Exception as e:
            self.log(t("logs.convert_error", error=e))

    def stop_recording_and_rename(self):
        if not self.recording:
            return

        self.log(t("logs.rec_stop"))
        self.update_status("SAVING", t("record.saving_audio"))
        output_path = None

        if self.obs_client:
            try:
                resp = self.obs_client.stop_record()
                if hasattr(resp, "output_path"):
                    output_path = resp.output_path
            except Exception as e:
                self.log(t("logs.rec_stop_error", error=e))

        self.recording = False
        time.sleep(2)

        if not output_path or not os.path.exists(output_path):
            if self.obs_client:
                try:
                    obs_dir_resp = self.obs_client.get_record_directory()
                    obs_dir = obs_dir_resp.record_directory
                    files = glob.glob(os.path.join(obs_dir, "*.mkv")) + glob.glob(
                        os.path.join(obs_dir, "*.mp4")
                    )
                    if files:
                        output_path = max(files, key=os.path.getctime)
                except Exception as e:
                    self.log(t("logs.obs_dir_failed", error=e))

        if output_path and os.path.exists(output_path):
            final_name = (
                self.real_game_id if self.real_game_id else f"game_unknown_{int(time.time())}"
            )
            ext = os.path.splitext(output_path)[1]
            new_path = os.path.join(self.record_folder, f"{final_name}{ext}")

            counter = 1
            while os.path.exists(new_path):
                new_path = os.path.join(self.record_folder, f"{final_name}_{counter}{ext}")
                counter += 1

            try:
                shutil.move(output_path, new_path)
                final_base_name = os.path.splitext(os.path.basename(new_path))[0]
                self.convert_to_mp3(
                    new_path, final_base_name, initial_offset=self.first_game_time
                )
            except Exception as e:
                self.log(t("logs.move_error", error=e))
        else:
            self.log(t("logs.obs_file_not_found"))
            self.update_status("WATCHING", t("record.waiting_for_match"))

        self.first_game_time = 0.0

    def run_loop(self):
        self.stop_event.clear()
        self.log(t("logs.recorder_started"))
        self.update_status("WATCHING", t("record.waiting_for_match"))

        # Initial OBS connection & pre-configuration
        if not self.obs_client:
            self.connect_obs()
        else:
            self.auto_configure_obs()
            self.set_scene()

        while not self.stop_event.is_set():
            try:
                r = self.session.get(
                    "https://127.0.0.1:2999/liveclientdata/allgamedata",
                    timeout=0.15,
                )
                data = r.json() if r.status_code == 200 else {}
                game_time = (
                    data.get("gameData", {}).get("gameTime")
                    if isinstance(data, dict)
                    else None
                )

                if game_time is not None:
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

                    # Game detected: start recording immediately
                    if not self.recording and game_time >= -30:
                        self.start_recording(initial_game_time=game_time)

                    if self.recording:
                        active, is_paused = self.get_obs_status()

                        # Instant pause if game time paused
                        if game_time == self.last_game_time and game_time > 0:
                            if active and not is_paused:
                                try:
                                    self.obs_client.pause_record()
                                    self.log(t("logs.pause"))
                                    p_time_val = max(0.0, game_time)
                                    p_time_str = (
                                        f"{int(p_time_val // 60):02d}:{int(p_time_val % 60):02d}"
                                    )
                                    self.update_status(
                                        "PAUSED",
                                        t("record.paused_time", time=p_time_str),
                                    )
                                except Exception as e:
                                    self.log(t("logs.pause_failed", error=e))

                        elif game_time > self.last_game_time:
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

                            if active and is_paused:
                                try:
                                    self.obs_client.resume_record()
                                    self.log(t("logs.unpause"))
                                    self.update_status("RECORDING", detail_text)
                                except Exception as e:
                                    self.log(t("logs.unpause_failed", error=e))
                            else:
                                if int(game_time) != int(self.last_game_time):
                                    self.update_status("RECORDING", detail_text)

                        self.last_game_time = game_time

            except requests.exceptions.RequestException:
                if self.recording:
                    self.stop_recording_and_rename()
                else:
                    self.update_status("WATCHING", t("record.waiting_for_match"))

            # When recording or match is active, poll at 0.05s. Otherwise use check_time.
            poll_interval = 0.05 if self.recording else self.check_time
            sleep_slices = max(1, int(poll_interval / 0.05))
            for _ in range(sleep_slices):
                if self.stop_event.is_set():
                    break
                time.sleep(0.05)

        # Clean shutdown if stopped while recording
        if self.recording:
            self.stop_recording_and_rename()

        self.log(t("logs.recorder_stopped"))
        self.update_status("STOPPED", "")

    def stop(self):
        self.stop_event.set()


def main():
    rec = LeagueRecorder()
    if not rec.connect_obs():
        sys.exit(1)
    rec.run_loop()


if __name__ == "__main__":
    main()
