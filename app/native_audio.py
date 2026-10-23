"""
Native WASAPI Audio Recorder module for Discord and Player Microphone.
Captures Discord audio directly from the Discord process tree via Windows WASAPI
Process Loopback (no OBS Studio required) and combines it with the player's microphone.
Includes real-time VU audio meter monitoring and application/process selection.
"""

import os
import queue
import subprocess
import threading
import time
import wave

import numpy as np
import psutil

from app.config import get_ffmpeg_path


def get_available_audio_targets():
    """
    Scans running processes and Windows Core Audio sessions to find
    all applications available for process loopback audio capture.
    Returns a sorted list of dicts: [{'pid': int, 'name': str, 'display': str, 'active': bool}]
    """
    targets = []
    seen_pids = set()

    # 1. First add 'Discord (Auto)' default target
    targets.append({
        "pid": -1,
        "name": "discord_auto",
        "display": "Discord (Auto Detect Active Audio) 🎯",
        "active": False,
    })

    # 2. Query Windows Core Audio sessions via pycaw
    try:
        from pycaw.pycaw import AudioUtilities

        for s in AudioUtilities.GetAllSessions():
            if s.Process and s.ProcessId > 0:
                pid = s.ProcessId
                name = s.Process.name()
                if pid not in seen_pids:
                    seen_pids.add(pid)
                    targets.append({
                        "pid": pid,
                        "name": name,
                        "display": f"{name} (PID: {pid}) 🔊",
                        "active": True,
                    })
    except Exception:
        pass

    # 3. Add other common media & communication apps
    common_apps = [
        "discord.exe", "spotify.exe", "chrome.exe", "firefox.exe",
        "msedge.exe", "ts3client_win64.exe", "telegram.exe", "vlc.exe",
        "league of legends.exe"
    ]
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            pid = proc.info["pid"]
            name = proc.info.get("name") or ""
            lower = name.lower()
            if pid not in seen_pids and any(lower == app or lower.startswith(app.split(".")[0]) for app in common_apps):
                if "helper" not in lower and "crash" not in lower:
                    seen_pids.add(pid)
                    targets.append({
                        "pid": pid,
                        "name": name,
                        "display": f"{name} (PID: {pid})",
                        "active": False,
                    })
        except Exception:
            pass

    return targets


def find_discord_active_pid():
    """
    Finds the specific Discord.exe process ID that owns an active audio session.
    If no active session exists, returns the primary Discord.exe process.
    """
    # 1. Check pycaw audio sessions first
    try:
        from pycaw.pycaw import AudioUtilities

        for s in AudioUtilities.GetAllSessions():
            if s.Process and "discord.exe" in s.Process.name().lower():
                return s.ProcessId
    except Exception:
        pass

    # 2. Find Discord.exe processes from psutil (excluding helpers)
    discord_pids = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name == "discord.exe":
                discord_pids.append(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    if discord_pids:
        # Prefer the last spawned or first valid Discord.exe
        return discord_pids[-1]

    return None


def get_default_microphone_info():
    """
    Queries sounddevice for default input device capabilities.
    Returns (device_index, channels, samplerate).
    """
    try:
        import sounddevice as sd

        default_input = sd.default.device[0]
        if default_input is None or default_input < 0:
            return None, 1, 48000
        info = sd.query_devices(default_input)
        max_channels = int(info.get("max_input_channels", 1))
        channels = 2 if max_channels >= 2 else 1
        return default_input, channels, 48000
    except Exception:
        return None, 1, 48000


class NativeAudioRecorder:
    """
    Zero-latency background audio recorder for Discord/App + Microphone.
    Directly attaches to target process via Windows WASAPI Process Loopback
    and captures default microphone input into synchronized WAV buffers.
    Exposes live audio level metering (VU meter) for UI visualization.
    """

    def __init__(self, record_folder=None, log_callback=None):
        self.record_folder = record_folder or os.path.abspath("recordings")
        self.log_callback = log_callback

        self.target_pid = -1  # -1 means auto-detect active Discord session
        self.target_name = "Discord"

        self.is_recording = False
        self.is_paused = False
        self.stop_event = threading.Event()

        self.discord_wav_path = None
        self.mic_wav_path = None

        self._discord_thread = None
        self._mic_stream = None
        self._mic_queue = queue.Queue()
        self._mic_writer_thread = None

        self.mic_channels = 1
        self.current_app_level = 0.0
        self.current_mic_level = 0.0

        # Background microphone monitoring stream for VU meter (started lazily on demand)
        self._monitor_stream = None

    def log(self, message):
        print(f"[NativeRecorder] {message}")
        if self.log_callback:
            try:
                self.log_callback(message)
            except Exception:
                pass

    def _start_mic_monitor(self):
        """Runs a persistent lightweight microphone input stream for the live VU meter."""
        if self._monitor_stream is not None:
            return
        try:
            import sounddevice as sd

            dev_idx, channels, rate = get_default_microphone_info()

            def monitor_cb(indata, frames, time_info, status):
                try:
                    floats = np.frombuffer(indata, dtype=np.float32)
                    if len(floats) > 0:
                        peak = float(np.max(np.abs(floats)))
                        self.current_mic_level = max(peak, self.current_mic_level * 0.85)
                except Exception:
                    pass

            self._monitor_stream = sd.RawInputStream(
                samplerate=rate,
                channels=channels,
                dtype="float32",
                device=dev_idx,
                callback=monitor_cb,
            )
            self._monitor_stream.start()
        except Exception:
            self._monitor_stream = None

    def _stop_mic_monitor(self):
        """Stops and closes the live microphone monitor stream."""
        if self._monitor_stream is not None:
            try:
                self._monitor_stream.stop()
                self._monitor_stream.close()
            except Exception:
                pass
            finally:
                self._monitor_stream = None

    def close(self):
        self._stop_mic_monitor()
        if self.is_recording:
            try:
                self.stop_and_finalize("closed")
            except Exception:
                pass

    def __del__(self):
        try:
            self._stop_mic_monitor()
        except Exception:
            pass

    def poll_audio_levels(self):
        """
        Returns (app_level, mic_level) as floats between 0.0 and 1.0.
        Uses smooth decay for visual VU meter animations.
        """
        if not self.is_recording and self._monitor_stream is None:
            self._start_mic_monitor()

        # When idle, poll target process audio peak via pycaw
        if not self.is_recording:
            resolved_pid = self.resolve_target_pid()
            if resolved_pid and resolved_pid > 0:
                try:
                    from pycaw.pycaw import AudioUtilities, IAudioMeterInformation

                    found = False
                    for s in AudioUtilities.GetAllSessions():
                        if s.Process and s.ProcessId == resolved_pid:
                            meter = s._ctl.QueryInterface(IAudioMeterInformation)
                            peak = float(meter.GetPeakValue())
                            self.current_app_level = max(peak, self.current_app_level * 0.85)
                            found = True
                            break
                    if not found:
                        self.current_app_level *= 0.85
                except Exception:
                    self.current_app_level *= 0.85
            else:
                self.current_app_level *= 0.85

        return min(1.0, self.current_app_level), min(1.0, self.current_mic_level)

    def resolve_target_pid(self):
        """Resolves target_pid to an actual running Windows process ID."""
        if self.target_pid and self.target_pid > 0:
            if psutil.pid_exists(self.target_pid):
                return self.target_pid

        # Auto-detect Discord
        return find_discord_active_pid()

    def set_target_process(self, pid, name=None):
        """Updates the target application/process to record."""
        self.target_pid = pid
        if name:
            self.target_name = name
        self.log(f"Target process set to: PID {pid} ({name or 'Auto'})")

    def start_recording(self, session_id):
        """Starts asynchronous capture of target process audio and player microphone."""
        if self.is_recording:
            return

        self._stop_mic_monitor()
        self.stop_event.clear()
        self.is_paused = False

        resolved_pid = self.resolve_target_pid()

        temp_dir = os.path.join(self.record_folder, ".temp_audio")
        os.makedirs(temp_dir, exist_ok=True)

        self.discord_wav_path = os.path.join(temp_dir, f"{session_id}_app.wav")
        self.mic_wav_path = os.path.join(temp_dir, f"{session_id}_mic.wav")

        # 1. Start App loopback thread
        if resolved_pid:
            try:
                proc_name = psutil.Process(resolved_pid).name()
            except Exception:
                proc_name = "Target App"
            self.log(f"Hooking WASAPI process loopback for {proc_name} (PID: {resolved_pid})...")
            self._discord_thread = threading.Thread(
                target=self._capture_discord_loop,
                args=(resolved_pid, self.discord_wav_path),
                daemon=True,
            )
            self._discord_thread.start()
        else:
            self.log("⚠️ No target audio process found. Recording microphone only.")
            self.discord_wav_path = None

        # 2. Start Microphone capture
        try:
            import sounddevice as sd

            dev_idx, channels, rate = get_default_microphone_info()
            self.mic_channels = channels

            self._mic_writer_thread = threading.Thread(
                target=self._write_mic_loop,
                args=(self.mic_wav_path, channels, rate),
                daemon=True,
            )
            self._mic_writer_thread.start()

            def mic_callback(indata, frames, time_info, status):
                if not self.is_paused and self.is_recording:
                    self._mic_queue.put(bytes(indata))
                    try:
                        floats = np.frombuffer(indata, dtype=np.float32)
                        if len(floats) > 0:
                            peak = float(np.max(np.abs(floats)))
                            self.current_mic_level = max(peak, self.current_mic_level * 0.85)
                    except Exception:
                        pass

            # Pause monitor stream if active to take exclusive stream
            if self._monitor_stream:
                try:
                    self._monitor_stream.stop()
                except Exception:
                    pass

            self._mic_stream = sd.RawInputStream(
                samplerate=rate,
                channels=channels,
                dtype="float32",
                device=dev_idx,
                callback=mic_callback,
            )
            self._mic_stream.start()
            self.log(f"Microphone recording started ({channels}ch @ {rate}Hz).")
        except Exception as e:
            self.log(f"Warning: Could not start microphone capture: {e}")
            self.mic_wav_path = None

        self.is_recording = True

    def _capture_discord_loop(self, pid, wav_path):
        """Polls process audio frames from WASAPI ProcessLoopback and writes to WAV."""
        try:
            from proctap._native import ProcessLoopback

            loop = ProcessLoopback(pid)
            loop.start()

            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(2)
                wf.setsampwidth(2)  # 16-bit PCM
                wf.setframerate(48000)

                while not self.stop_event.is_set():
                    d = loop.read()
                    if d and not self.is_paused:
                        floats = np.frombuffer(d, dtype=np.float32)
                        if len(floats) > 0:
                            peak = float(np.max(np.abs(floats)))
                            self.current_app_level = max(peak, self.current_app_level * 0.85)
                        ints = np.clip(floats * 32767.0, -32768, 32767).astype(np.int16)
                        wf.writeframes(ints.tobytes())
                    time.sleep(0.015)

            loop.stop()
        except Exception as e:
            self.log(f"Process audio capture error: {e}")

    def _write_mic_loop(self, wav_path, channels, rate):
        """Consumes microphone audio chunks from queue and writes to WAV."""
        try:
            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(channels)
                wf.setsampwidth(2)  # 16-bit PCM
                wf.setframerate(rate)

                while not self.stop_event.is_set() or not self._mic_queue.empty():
                    try:
                        chunk = self._mic_queue.get(timeout=0.05)
                        floats = np.frombuffer(chunk, dtype=np.float32)
                        ints = np.clip(floats * 32767.0, -32768, 32767).astype(np.int16)
                        wf.writeframes(ints.tobytes())
                    except queue.Empty:
                        continue
        except Exception as e:
            self.log(f"Mic writer error: {e}")

    def pause(self):
        """Pauses audio recording (e.g. during match pause)."""
        self.is_paused = True

    def resume(self):
        """Resumes audio recording."""
        self.is_paused = False

    def stop_and_finalize(self, output_base_name, initial_offset=0.0):
        """
        Stops active capture streams, mixes Discord + Microphone via FFmpeg,
        applies sample-accurate time alignment, and outputs <output_base_name>.mp3.
        """
        if not self.is_recording:
            return None

        self.is_recording = False
        self.stop_event.set()

        # Stop microphone stream
        if self._mic_stream:
            try:
                self._mic_stream.stop()
                self._mic_stream.close()
            except Exception:
                pass
            self._mic_stream = None

        # Restart microphone monitor stream for VU meter
        self._start_mic_monitor()

        # Wait for threads to finish writing headers
        if self._discord_thread and self._discord_thread.is_alive():
            self._discord_thread.join(timeout=2.0)
        if self._mic_writer_thread and self._mic_writer_thread.is_alive():
            self._mic_writer_thread.join(timeout=2.0)

        # Mix and encode to MP3
        final_mp3 = self._mix_to_mp3(output_base_name, initial_offset)
        return final_mp3

    def _mix_to_mp3(self, base_name, initial_offset=0.0):
        """Combines temporary Discord and Mic WAV files into final MP3 using FFmpeg."""
        mp3_path = os.path.join(self.record_folder, f"{base_name}.mp3")
        counter = 1
        while os.path.exists(mp3_path):
            mp3_path = os.path.join(self.record_folder, f"{base_name}_{counter}.mp3")
            counter += 1

        discord_ok = self.discord_wav_path and os.path.exists(self.discord_wav_path) and os.path.getsize(self.discord_wav_path) > 44
        mic_ok = self.mic_wav_path and os.path.exists(self.mic_wav_path) and os.path.getsize(self.mic_wav_path) > 44

        if not discord_ok and not mic_ok:
            self.log("⚠️ No audio data captured (neither target app nor Microphone had data).")
            return None

        ffmpeg_cmd = get_ffmpeg_path()
        cmd = [ffmpeg_cmd, "-y"]

        filter_parts = []
        if discord_ok and mic_ok:
            cmd.extend(["-i", self.discord_wav_path, "-i", self.mic_wav_path])
            filter_parts.append("[0:a][1:a]amix=inputs=2:duration=longest:dropout_transition=0")
        elif discord_ok:
            cmd.extend(["-i", self.discord_wav_path])
            filter_parts.append("[0:a]anull")
        else:
            cmd.extend(["-i", self.mic_wav_path])
            filter_parts.append("[0:a]anull")

        # Offset alignment chained directly inside filter_complex
        if initial_offset < -0.005:
            filter_parts.append(f"atrim=start={abs(initial_offset):.3f},asetpts=PTS-STARTPTS")
        elif initial_offset > 0.005:
            delay_ms = int(initial_offset * 1000)
            filter_parts.append(f"adelay=delays={delay_ms}:all=1")

        filter_chain = ",".join(filter_parts) + "[aout]"
        cmd.extend(["-filter_complex", filter_chain, "-map", "[aout]", "-q:a", "0", mp3_path])

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if res.returncode != 0:
                self.log(f"FFmpeg mixing error: {res.stderr.strip()[:300]}")
                return None
            self.log(f"Successfully mixed and saved: {os.path.basename(mp3_path)}")
        except Exception as e:
            self.log(f"FFmpeg execution error: {e}")
            return None
        finally:
            # Clean up temporary WAV files
            for p in (self.discord_wav_path, self.mic_wav_path):
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass

        return mp3_path
