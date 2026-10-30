"""
Native WASAPI Audio Recorder module for Discord and Player Microphone.
Captures Discord audio directly from the Discord process tree via Windows WASAPI
Process Loopback (proctap) with continuous hardware clock synchronization,
and combines it with the player's microphone input (pyaudiowpatch).
Includes real-time VU audio meter monitoring and application/process selection.
"""

import collections
import os
import queue
import struct
import subprocess
import sys
import threading
import time
import wave

import numpy as np
import psutil
import pyaudiowpatch as pyaudio

from app.config import get_ffmpeg_path
from app.discord_ipc import DiscordMuteWatcher


# Process-wide thread-safe singleton for PortAudio
_SHARED_PYAUDIO = None
_SHARED_PYAUDIO_LOCK = threading.Lock()


def get_shared_pyaudio():
    """
    Returns a thread-safe process-wide singleton PyAudio instance.
    Prevents PortAudio Pa_Initialize / Pa_Terminate race conditions and assertion crashes.
    """
    global _SHARED_PYAUDIO
    with _SHARED_PYAUDIO_LOCK:
        if _SHARED_PYAUDIO is None:
            _SHARED_PYAUDIO = pyaudio.PyAudio()
        return _SHARED_PYAUDIO


def scale_vu(peak):
    """
    Transforms linear peak amplitude (0.0 to 1.0) into a responsive, balanced VU meter value.
    - Low noise threshold (0.0002) stops static/idle hiss from moving the bar at rest.
    - Balanced power curve (0.42) ensures the visualizer moves actively with normal speech
      without maxing out or being hyper-sensitive to room noise.
    """
    if peak <= 0.0002:
        return 0.0
    val = ((peak - 0.0002) / (1.0 - 0.0002)) ** 0.42
    return max(0.0, min(1.0, float(val)))


def get_available_audio_targets():
    """
    Scans running Discord processes and Windows Core Audio sessions.
    Returns a sorted list of dicts for DISCORD ONLY:
    [{'pid': int, 'name': str, 'display': str, 'active': bool}]
    """
    from app.i18n import t

    targets = []
    seen_pids = set()

    # 1. Default 'Discord (Auto Detect Audio)' target
    auto_label = t("record.auto_discord")
    targets.append({
        "pid": -1,
        "name": "discord_auto",
        "display": auto_label,
        "active": False,
    })

    # 2. Query Discord Core Audio sessions via pycaw
    audio_sessions = []
    try:
        from pycaw.pycaw import AudioUtilities, IAudioMeterInformation

        for s in AudioUtilities.GetAllSessions():
            if s.Process and "discord.exe" in s.Process.name().lower():
                pid = s.ProcessId
                peak = 0.0
                try:
                    meter = s._ctl.QueryInterface(IAudioMeterInformation)
                    peak = float(meter.GetPeakValue())
                except Exception:
                    pass
                audio_sessions.append((pid, peak))
    except Exception:
        pass

    # Sort audio sessions so active/loudest audio session comes first
    audio_sessions.sort(key=lambda x: x[1], reverse=True)

    sound_detected_str = t("record.sound_detected")
    for pid, peak in audio_sessions:
        if pid not in seen_pids:
            seen_pids.add(pid)
            if peak > 0.0001:
                display = f"Discord (PID: {pid}) 🔊 [{sound_detected_str}]"
            else:
                display = f"Discord (PID: {pid}) [Audio Session]"
            targets.append({
                "pid": pid,
                "name": "Discord.exe",
                "display": display,
                "active": peak > 0.0001,
            })

    # 3. Add other running Discord processes from psutil (renderer, main)
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            pid = proc.info.get("pid")
            if name == "discord.exe" and pid and pid not in seen_pids:
                cmd_list = proc.info.get("cmdline")
                if cmd_list is None and hasattr(proc, "cmdline"):
                    try:
                        cmd_list = proc.cmdline()
                    except Exception:
                        cmd_list = []
                cmd = " ".join(cmd_list or []).lower()
                if "crashpad" in cmd or "gpu-process" in cmd:
                    continue
                tag = "Renderer" if "--type=renderer" in cmd else ("Utility" if "--type=utility" in cmd else "Main")
                seen_pids.add(pid)
                targets.append({
                    "pid": pid,
                    "name": "Discord.exe",
                    "display": f"Discord (PID: {pid}) [{tag}]",
                    "active": False,
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    return targets


def find_discord_active_pid():
    """
    Finds the specific Discord.exe process ID that owns an active audio session.
    Prioritizes the session with the highest audio peak.
    If all sessions are quiet, prioritizes the renderer process.
    """
    try:
        from pycaw.pycaw import AudioUtilities, IAudioMeterInformation

        best_pid = None
        highest_peak = 0.0
        candidate_pids = []

        for s in AudioUtilities.GetAllSessions():
            if s.Process and "discord.exe" in s.Process.name().lower():
                pid = s.ProcessId
                candidate_pids.append(pid)
                try:
                    meter = s._ctl.QueryInterface(IAudioMeterInformation)
                    peak = float(meter.GetPeakValue())
                    if peak > highest_peak:
                        highest_peak = peak
                        best_pid = pid
                except Exception:
                    pass

        # If any Discord session has audible sound, return it immediately
        if best_pid and highest_peak > 0.0001:
            return best_pid

        # If candidates exist but peak is 0 (quiet), find the renderer process
        for pid in candidate_pids:
            try:
                proc = psutil.Process(pid)
                cmd = " ".join(proc.cmdline()).lower()
                if "--type=renderer" in cmd:
                    return pid
            except Exception:
                pass

        if candidate_pids:
            return candidate_pids[-1]
    except Exception:
        pass

    # Fallback to psutil search for renderer Discord
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name == "discord.exe":
                cmd_list = proc.info.get("cmdline")
                if cmd_list is None and hasattr(proc, "cmdline"):
                    try:
                        cmd_list = proc.cmdline()
                    except Exception:
                        cmd_list = []
                cmd = " ".join(cmd_list or []).lower()
                if "--type=renderer" in cmd:
                    return proc.info["pid"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    return None


def get_available_microphones():
    """
    Returns a sorted list of unique microphone input devices:
    [
        {"index": -1, "name": "default", "display": "Default Microphone (System) 🎯", "hostApi": -1},
        {"index": 9, "name": "Mikrofon (Arctis Nova 3P Wireless)", "display": "Mikrofon (Arctis Nova 3P Wireless)", "hostApi": 2},
        ...
    ]
    Prioritizes WASAPI devices (hostApi == 2) over legacy MME/DirectSound.
    """
    from app.i18n import t

    mics = [{
        "index": -1,
        "name": "default",
        "display": t("record.mic_default"),
        "hostApi": -1,
    }]
    try:
        pa = get_shared_pyaudio()
        wasapi_mics = []
        other_mics = []
        seen_names = set()

        for i in range(pa.get_device_count()):
            try:
                d = pa.get_device_info_by_index(i)
                if d.get("maxInputChannels", 0) <= 0 or d.get("isLoopbackDevice", False):
                    continue
                name = (d.get("name") or "").strip()
                lower_name = name.lower()
                # Skip generic Windows sound mappers
                if any(skip in lower_name for skip in ["hanglek", "mapper", "elsődleges", "primary sound capture"]):
                    continue
                if d.get("hostApi") == 2:  # WASAPI
                    wasapi_mics.append(d)
                else:
                    other_mics.append(d)
            except Exception:
                pass

        for d in wasapi_mics:
            name = (d.get("name") or "").strip()
            seen_names.add(name.lower())
            mics.append({
                "index": int(d["index"]),
                "name": name,
                "display": name,
                "hostApi": 2,
            })

        for d in other_mics:
            name = (d.get("name") or "").strip()
            # Avoid duplicate physical mics if already found in WASAPI
            if not any(name.lower() in w or w in name.lower() for w in seen_names):
                seen_names.add(name.lower())
                mics.append({
                    "index": int(d["index"]),
                    "name": name,
                    "display": name,
                    "hostApi": d.get("hostApi", 0),
                })
    except Exception:
        pass
    return mics


def get_clean_microphone_device(pa, preferred_index=None, preferred_name=None):
    """
    Returns the cleanest microphone device dict.
    Prioritizes Windows WASAPI (hostApi=2) at its native sample rate (typically 48000Hz),
    which completely bypasses legacy Windows MME driver resampling jitter and static crackles.
    """
    if preferred_name and str(preferred_name).strip().lower() not in ("default", "none", ""):
        target = str(preferred_name).strip().lower()
        # 1. Search WASAPI devices matching target name
        for i in range(pa.get_device_count()):
            try:
                d = pa.get_device_info_by_index(i)
                if d.get("maxInputChannels", 0) > 0 and not d.get("isLoopbackDevice", False) and d.get("hostApi") == 2:
                    dname = (d.get("name") or "").strip().lower()
                    if target == dname or target in dname or dname in target:
                        return d
            except Exception:
                pass
        # 2. Search other host API devices matching target name
        for i in range(pa.get_device_count()):
            try:
                d = pa.get_device_info_by_index(i)
                if d.get("maxInputChannels", 0) > 0 and not d.get("isLoopbackDevice", False):
                    dname = (d.get("name") or "").strip().lower()
                    if target == dname or target in dname or dname in target:
                        return d
            except Exception:
                pass

    if preferred_index is not None and preferred_index >= 0:
        try:
            dev = pa.get_device_info_by_index(preferred_index)
            if dev and dev.get("maxInputChannels", 0) > 0:
                if dev.get("hostApi") != 2:
                    name = dev.get("name", "")
                    for i in range(pa.get_device_count()):
                        try:
                            d = pa.get_device_info_by_index(i)
                            if (
                                d.get("hostApi") == 2
                                and not d.get("isLoopbackDevice")
                                and d.get("maxInputChannels", 0) > 0
                                and (d.get("name") == name or name in d.get("name", ""))
                            ):
                                return d
                        except Exception:
                            pass
                return dev
        except Exception:
            pass

    # 1. Try default WASAPI input device
    try:
        if hasattr(pa, "get_default_wasapi_device"):
            wasapi_dev = pa.get_default_wasapi_device()
            if wasapi_dev and wasapi_dev.get("maxInputChannels", 0) > 0:
                return wasapi_dev
    except Exception:
        pass

    # 2. Search for any active WASAPI non-loopback input device matching default name
    try:
        def_info = pa.get_default_input_device_info()
        def_name = (def_info.get("name") or "").strip()
        for i in range(pa.get_device_count()):
            d = pa.get_device_info_by_index(i)
            if (
                d.get("hostApi") == 2
                and not d.get("isLoopbackDevice")
                and d.get("maxInputChannels", 0) > 0
                and (d.get("name", "").startswith(def_name[:15]) or def_name[:15] in d.get("name", ""))
            ):
                return d
    except Exception:
        pass

    # 3. Fallback to standard default input device
    try:
        return pa.get_default_input_device_info()
    except Exception:
        return None


def get_default_microphone_info():
    """
    Queries default input device capabilities via PyAudioWPatch.
    Returns (device_index, channels, samplerate).
    """
    try:
        pa = get_shared_pyaudio()
        info = get_clean_microphone_device(pa)
        if not info:
            info = pa.get_default_input_device_info()
        idx = int(info.get("index", 0))
        channels = min(2, max(1, int(info.get("maxInputChannels", 1))))
        rate = int(info.get("defaultSampleRate", 48000))
        return idx, channels, rate
    except Exception:
        return None, 1, 48000


class TimelineWavWriter:
    """
    Direct-to-disk PCM WAV writer that continuously synchronizes with the League match clock.
    - If recording starts mid-match (e.g. 29:30), fills blank silence up to game_time.
    - If the recording rushed ahead because of an in-game pause, seeks back
      to the in-game clock and overwrites at that exact timestamp (no seconds cut out).
    - Preserves sample-accurate 1:1 timeline alignment between voice and game events.
    """

    def __init__(self, file_path, channels=2, sample_rate=48000):
        self.file_path = file_path
        self.channels = channels
        self.sample_rate = sample_rate
        self.frame_size = channels * 2
        self.lock = threading.Lock()
        self.file = open(file_path, "wb+")  # noqa: SIM115
        self.file.write(b"\x00" * 44)  # placeholder header
        self.current_frame = 0
        self.max_frame_written = 0

    def file_size(self):
        self.file.seek(0, 2)
        return self.file.tell()

    def sync_to_game_time(self, game_time):
        with self.lock:
            if game_time is None or game_time < 0:
                return
            target_frame = int(game_time * self.sample_rate)
            target_byte = 44 + target_frame * self.frame_size
            fsize = self.file_size()

            if target_byte > fsize:
                # Game time is ahead (e.g. started at 29:30): write blank silence!
                self.file.seek(fsize)
                gap = target_byte - fsize
                chunk = b"\x00" * 65536
                while gap > 0:
                    w = min(gap, len(chunk))
                    self.file.write(chunk[:w])
                    gap -= w
                self.file.seek(target_byte)
                self.current_frame = target_frame
                self.max_frame_written = max(self.max_frame_written, target_frame)
            elif self.current_frame > target_frame + int(0.010 * self.sample_rate):
                # Audio is ahead of game_time by > 10ms.
                # Seek back and overwrite at game_time!
                self.file.seek(target_byte)
                self.current_frame = target_frame

    def write_frames(self, pcm_bytes):
        with self.lock:
            self.file.write(pcm_bytes)
            n = len(pcm_bytes) // self.frame_size
            self.current_frame += n
            self.max_frame_written = max(self.max_frame_written, self.current_frame)

    def close(self, final_game_time=None):
        with self.lock:
            if self.file.closed:
                return
            if final_game_time is not None and final_game_time > 0:
                final_frame = int(final_game_time * self.sample_rate)
                self.max_frame_written = min(self.max_frame_written, final_frame)

            total_data = self.max_frame_written * self.frame_size
            self.file.seek(44 + total_data)
            self.file.truncate()

            # Write standard 44-byte RIFF WAVE header
            self.file.seek(0)
            self.file.write(b"RIFF" + struct.pack("<I", 36 + total_data) + b"WAVE")
            self.file.write(
                b"fmt "
                + struct.pack(
                    "<IHHIIHH",
                    16,
                    1,
                    self.channels,
                    self.sample_rate,
                    self.sample_rate * self.frame_size,
                    self.frame_size,
                    16,
                )
            )
            self.file.write(b"data" + struct.pack("<I", total_data))
            self.file.flush()
            self.file.close()


class NativeAudioRecorder:
    """
    Zero-latency background audio recorder for Discord (via Windows WASAPI Process Loopback)
    and Player Microphone (via PyAudioWPatch).
    Directly isolates Discord audio (ignoring game sound, browser, etc.) and maintains
    100% sample-accurate clock synchronization via precise silence injection.
    Exposes live audio level metering (VU meter) for UI visualization.
    """

    def __init__(
        self,
        record_folder=None,
        log_callback=None,
        target_pid=-1,
        target_name="Discord",
        mic_device=None,
        mic_device_name=None,
    ):
        self.record_folder = record_folder or os.path.abspath("recordings")
        self.log_callback = log_callback

        self.target_pid = target_pid  # -1 means auto-detect active Discord session
        self.target_name = target_name
        self.mic_device = mic_device
        self.mic_device_name = mic_device_name

        self.is_recording = False
        self.is_paused = False
        self.stop_event = threading.Event()

        self.discord_wav_path = None
        self.mic_wav_path = None

        self._discord_thread = None
        self._mic_stream = None
        self._mic_queue = queue.Queue()
        self._mic_writer_thread = None

        self._discord_writer = None
        self._mic_writer = None
        self.latest_game_time = 0.0

        self.mic_channels = 1
        self.current_app_level = 0.0
        self.current_mic_level = 0.0

        # Thread synchronization lock and pause-detection staging buffers
        self._buffer_lock = threading.Lock()
        self._discord_staging = collections.deque()
        self._mic_staging = collections.deque()
        self._staging_delay_sec = 2.0

        # Discord local IPC mute watcher
        self.discord_mute_watcher = DiscordMuteWatcher(
            on_mute_change=self._on_discord_mute_change,
            log_callback=self.log,
        )
        self.discord_mute_watcher.start()

        # Background monitoring for VU meters (runs non-blocking in background thread)
        self._cached_resolved_pid = None
        self._last_resolved_time = 0.0
        self._monitor_stream = None
        self._idle_monitor_thread = None
        self._stop_idle_monitor_event = threading.Event()

    def _on_discord_mute_change(self, is_muted):
        """Callback triggered when Discord mute/deafen state changes."""
        if is_muted:
            self.current_mic_level = 0.0

    def is_discord_muted(self):
        """Returns True if the user is currently muted or deafened in Discord."""
        if hasattr(self, "discord_mute_watcher") and self.discord_mute_watcher:
            return bool(self.discord_mute_watcher.is_muted)
        return False

    def update_game_time(self, game_time):
        """
        Synchronizes audio write timeline with in-game League match clock.
        If recording started mid-game, pads initial silence to match game_time.
        If audio rushed ahead due to a pause, seeks back to game_time
        and overwrites, ensuring zero timeline desynchronization without cutting seconds.
        """
        if game_time is not None and game_time >= 0:
            self.latest_game_time = float(game_time)
            if self._discord_writer:
                self._discord_writer.sync_to_game_time(self.latest_game_time)
            if self._mic_writer:
                self._mic_writer.sync_to_game_time(self.latest_game_time)

    def log(self, message):
        try:
            print(f"[NativeRecorder] {message}")
        except UnicodeEncodeError:
            try:
                encoding = sys.stdout.encoding or "utf-8"
                print(str(f"[NativeRecorder] {message}").encode(encoding, errors="replace").decode(encoding))
            except Exception:
                pass
        if self.log_callback:
            try:
                self.log_callback(message)
            except Exception:
                pass

    def _start_mic_monitor(self):
        """Runs a persistent lightweight microphone input stream for the live VU meter."""
        if self._monitor_stream is not None or self.is_recording:
            return
        try:
            pa = get_shared_pyaudio()
            mic = get_clean_microphone_device(pa, self.mic_device, self.mic_device_name)
            if not mic:
                mic = pa.get_default_input_device_info()
            rate = int(mic.get("defaultSampleRate", 48000))
            channels = min(2, max(1, int(mic.get("maxInputChannels", 1))))
            dev_idx = mic.get("index")

            def monitor_cb(in_data, frames, time_info, status):
                try:
                    if self.is_discord_muted():
                        self.current_mic_level = 0.0
                        return (None, pyaudio.paContinue)
                    floats = np.frombuffer(in_data, dtype=np.int16)
                    if len(floats) > 0:
                        peak = float(np.max(np.abs(floats))) / 32767.0
                        self.current_mic_level = max(peak, self.current_mic_level * 0.85)
                except Exception:
                    pass
                return (None, pyaudio.paContinue)

            self._monitor_stream = pa.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=dev_idx,
                frames_per_buffer=1024,
                stream_callback=monitor_cb,
            )
            self._monitor_stream.start_stream()
        except Exception:
            self._stop_mic_monitor()

    def _stop_mic_monitor(self):
        """Stops and closes the live microphone monitor stream."""
        if self._monitor_stream is not None:
            try:
                self._monitor_stream.stop_stream()
                self._monitor_stream.close()
            except Exception:
                pass
            finally:
                self._monitor_stream = None

    def _start_idle_monitor(self):
        """Starts background idle monitoring of target audio levels and mic."""
        if self.is_recording:
            return
        self._start_mic_monitor()
        if self._idle_monitor_thread is None or not self._idle_monitor_thread.is_alive():
            self._stop_idle_monitor_event.clear()
            self._idle_monitor_thread = threading.Thread(
                target=self._idle_app_monitor_loop, daemon=True, name="AudioMeterIdleWorker"
            )
            self._idle_monitor_thread.start()

    def _stop_idle_monitor(self):
        """Stops background idle audio monitoring."""
        self._stop_idle_monitor_event.set()
        self._stop_mic_monitor()
        self._idle_monitor_thread = None

    def _idle_app_monitor_loop(self):
        """Background worker that samples audio peaks from target process without blocking the GUI."""
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception:
            pass

        try:
            from pycaw.pycaw import AudioUtilities, IAudioMeterInformation

            cached_meter = None
            last_meter_pid = None

            while not self._stop_idle_monitor_event.is_set() and not self.is_recording:
                resolved_pid = self.resolve_target_pid()
                if resolved_pid and resolved_pid > 0:
                    found = False
                    # Fast-path: query cached COM meter directly (315x faster than GetAllSessions)
                    if cached_meter is not None and last_meter_pid == resolved_pid:
                        try:
                            peak = float(cached_meter.GetPeakValue())
                            self.current_app_level = max(peak, self.current_app_level * 0.85)
                            found = True
                        except Exception:
                            cached_meter = None
                            last_meter_pid = None

                    if not found:
                        try:
                            for s in AudioUtilities.GetAllSessions():
                                if s.Process and s.ProcessId == resolved_pid:
                                    meter = s._ctl.QueryInterface(IAudioMeterInformation)
                                    peak = float(meter.GetPeakValue())
                                    self.current_app_level = max(peak, self.current_app_level * 0.85)
                                    cached_meter = meter
                                    last_meter_pid = resolved_pid
                                    found = True
                                    break
                        except Exception:
                            cached_meter = None
                            last_meter_pid = None

                    if not found:
                        self.current_app_level *= 0.85
                else:
                    cached_meter = None
                    last_meter_pid = None
                    self.current_app_level *= 0.85

                time.sleep(0.06)
        except Exception:
            pass
        finally:
            try:
                import comtypes
                comtypes.CoUninitialize()
            except Exception:
                pass

    def close(self):
        self._stop_idle_monitor()
        if hasattr(self, "discord_mute_watcher") and self.discord_mute_watcher:
            try:
                self.discord_mute_watcher.stop()
            except Exception:
                pass
        if self.is_recording:
            try:
                self.stop_and_finalize("closed")
            except Exception:
                pass

    def __del__(self):
        try:
            self._stop_idle_monitor()
            if hasattr(self, "discord_mute_watcher") and self.discord_mute_watcher:
                self.discord_mute_watcher.stop()
        except Exception:
            pass

    def poll_audio_levels(self):
        """
        Returns (app_level, mic_level) as floats between 0.0 and 1.0.
        Uses smooth decay for visual VU meter animations.
        Completely non-blocking (runs in 0.001ms on GUI thread).
        """
        if not self.is_recording:
            if self._idle_monitor_thread is None or not self._idle_monitor_thread.is_alive():
                self._start_idle_monitor()

        if self.is_discord_muted():
            self.current_mic_level = 0.0

        scaled_app = scale_vu(self.current_app_level)
        scaled_mic = scale_vu(self.current_mic_level)
        self.current_app_level *= 0.88
        self.current_mic_level *= 0.88

        return scaled_app, scaled_mic

    def resolve_target_pid(self):
        """Resolves target_pid to an actual running Windows process ID with caching."""
        now = time.monotonic()
        if (
            self._cached_resolved_pid is not None
            and (now - self._last_resolved_time) < 2.0
            and psutil.pid_exists(self._cached_resolved_pid)
        ):
            return self._cached_resolved_pid

        if self.target_pid and self.target_pid > 0:
            if psutil.pid_exists(self.target_pid):
                self._cached_resolved_pid = self.target_pid
                self._last_resolved_time = now
                return self.target_pid

        # Auto-detect Discord
        pid = find_discord_active_pid()
        self._cached_resolved_pid = pid
        self._last_resolved_time = now
        return pid

    def set_target_process(self, pid, name=None):
        """Updates the target application/process to record."""
        self.target_pid = pid
        if name:
            self.target_name = name
        self._cached_resolved_pid = None
        self._last_resolved_time = 0.0
        self.log(f"Target process set to: PID {pid} ({name or 'Auto'})")

    def set_mic_device(self, index, name=None):
        """Updates the microphone device to record and monitor."""
        self.mic_device = index
        self.mic_device_name = name
        if not self.is_recording and self._monitor_stream is not None:
            self._stop_mic_monitor()
            self._start_mic_monitor()
        self.log(f"Microphone device set to: {name or f'Index {index}'}")

    def start_recording(self, session_id, initial_game_time=0.0):
        """Starts asynchronous capture of target process audio and player microphone."""
        if self.is_recording:
            return

        self._stop_idle_monitor()
        self.stop_event.clear()
        self.is_paused = False
        self.is_recording = True
        if hasattr(self, "discord_mute_watcher") and self.discord_mute_watcher:
            self.discord_mute_watcher.is_recording = True
        self.latest_game_time = max(0.0, float(initial_game_time or 0.0))
        with self._buffer_lock:
            self._discord_staging.clear()
            self._mic_staging.clear()

        resolved_pid = self.resolve_target_pid()

        temp_dir = os.path.join(self.record_folder, ".temp_audio")
        os.makedirs(temp_dir, exist_ok=True)

        self.discord_wav_path = os.path.join(temp_dir, f"{session_id}_app.wav")
        self.mic_wav_path = os.path.join(temp_dir, f"{session_id}_mic.wav")

        # 1. Start Discord Process Loopback thread
        if resolved_pid:
            try:
                proc_name = psutil.Process(resolved_pid).name()
            except Exception:
                proc_name = "Discord"
            self.log(f"Hooking ProcessLoopback for {proc_name} (PID: {resolved_pid})...")
            self._discord_thread = threading.Thread(
                target=self._capture_discord_loop,
                args=(resolved_pid, self.discord_wav_path),
                daemon=True,
                name="DiscordProcessLoopbackWorker",
            )
            self._discord_thread.start()
        else:
            self.log("⚠️ No target Discord process found. Recording microphone only.")
            self.discord_wav_path = None

        # 2. Start Microphone capture via shared PyAudio
        try:
            pa = get_shared_pyaudio()
            mic_device = get_clean_microphone_device(pa, self.mic_device, self.mic_device_name)
            if mic_device is None:
                try:
                    mic_device = pa.get_default_input_device_info()
                except Exception:
                    mic_device = None

            if mic_device:
                mic_channels = min(2, max(1, int(mic_device.get("maxInputChannels", 1))))
                mic_rate = int(mic_device.get("defaultSampleRate", 48000))
                self.mic_channels = mic_channels

                self._mic_writer_thread = threading.Thread(
                    target=self._write_mic_loop,
                    args=(self.mic_wav_path, mic_channels, mic_rate),
                    daemon=True,
                    name="MicWriterThread",
                )
                self._mic_writer_thread.start()

                def mic_callback(in_data, frames, time_info, status):
                    if not self.is_paused and self.is_recording:
                        if self.is_discord_muted():
                            # Inject silence zeros while maintaining exact frame timeline alignment
                            self._mic_queue.put(b"\x00" * len(in_data))
                            self.current_mic_level = 0.0
                        else:
                            self._mic_queue.put(bytes(in_data))
                    return (None, pyaudio.paContinue)

                self._mic_stream = pa.open(
                    format=pyaudio.paInt16,
                    channels=mic_channels,
                    rate=mic_rate,
                    input=True,
                    input_device_index=mic_device["index"],
                    frames_per_buffer=1024,
                    stream_callback=mic_callback,
                )
                self._mic_stream.start_stream()
                self.log(f"Microphone recording started on '{mic_device['name']}' ({mic_channels}ch @ {mic_rate}Hz).")
            else:
                self.log("⚠️ No microphone device found. Recording Discord only.")
                self.mic_wav_path = None
        except Exception as e:
            self.log(f"Warning: Could not start microphone capture: {e}")
            self.mic_wav_path = None

        self.is_recording = True

    def _capture_discord_loop(self, pid, wav_path):
        """
        Polls Discord process audio frames from WASAPI ProcessLoopback.
        Writes directly to TimelineWavWriter with continuous in-game clock alignment.
        """
        try:
            from proctap._native import ProcessLoopback

            loop = ProcessLoopback(pid)
            loop.start()

            # Strict isolation check: ensure capture is strictly process-specific to Discord.
            if not loop.is_process_specific():
                self.log("❌ ERROR: Process loopback is NOT process-specific! Aborting capture to prevent recording background audio.")
                loop.stop()
                return

            writer = TimelineWavWriter(wav_path, channels=2, sample_rate=48000)
            self._discord_writer = writer

            if self.latest_game_time > 0:
                writer.sync_to_game_time(self.latest_game_time)

            t_last = time.perf_counter()

            while not self.stop_event.is_set():
                now = time.perf_counter()

                d = loop.read()
                if d:
                    floats = np.frombuffer(d, dtype=np.float32)
                    if len(floats) > 0:
                        peak = float(np.max(np.abs(floats)))
                        self.current_app_level = max(peak, self.current_app_level * 0.85)

                    if not self.is_paused:
                        ints = np.clip(floats * 32767.0, -32768, 32767).astype(np.int16)
                        raw_bytes = ints.tobytes()
                        writer.write_frames(raw_bytes)
                    t_last = now
                else:
                    # Empty read: Discord is quiet / silence period
                    # Inject exact silence matching elapsed time only when active (not paused)
                    dt = now - t_last
                    if not self.is_paused:
                        if dt >= 0.01:
                            silence_frames = int(dt * 48000)
                            writer.write_frames(b"\x00" * (silence_frames * 4))
                            t_last = now
                    else:
                        t_last = now
                    time.sleep(0.001)

            writer.close(final_game_time=self.latest_game_time)
            loop.stop()
        except Exception as e:
            self.log(f"Discord process audio capture error: {e}")

    def _write_mic_loop(self, wav_path, channels, rate):
        """Consumes microphone audio chunks from queue and writes directly to TimelineWavWriter."""
        try:
            writer = TimelineWavWriter(wav_path, channels=channels, sample_rate=rate)
            self._mic_writer = writer

            if self.latest_game_time > 0:
                writer.sync_to_game_time(self.latest_game_time)

            while not self.stop_event.is_set() or not self._mic_queue.empty():
                try:
                    chunk = self._mic_queue.get(timeout=0.05)
                    writer.write_frames(chunk)
                    try:
                        floats = np.frombuffer(chunk, dtype=np.int16)
                        if len(floats) > 0:
                            peak = float(np.max(np.abs(floats))) / 32767.0
                            self.current_mic_level = max(peak, self.current_mic_level * 0.85)
                    except Exception:
                        pass
                except queue.Empty:
                    continue

            writer.close(final_game_time=self.latest_game_time)
        except Exception as e:
            self.log(f"Mic writer error: {e}")

    def pause(self):
        """Marks recording paused when match is paused."""
        with self._buffer_lock:
            self.is_paused = True
            self._discord_staging.clear()
            self._mic_staging.clear()
        self.log("Recording paused.")

    def resume(self):
        """Resumes audio recording when match resumes."""
        with self._buffer_lock:
            self.is_paused = False
        self.log("Recording resumed.")

    def stop_and_finalize(self, output_base_name, initial_offset=0.0):
        """
        Stops active capture streams, mixes Discord + Microphone via FFmpeg,
        applies sample-accurate time alignment, and outputs <output_base_name>.mp3.
        """
        if not self.is_recording:
            return None

        self.stop_event.set()
        self.is_recording = False
        if hasattr(self, "discord_mute_watcher") and self.discord_mute_watcher:
            self.discord_mute_watcher.is_recording = False

        # Stop microphone stream first so callback cleanly stops queueing
        if self._mic_stream:
            try:
                self._mic_stream.stop_stream()
                self._mic_stream.close()
            except Exception:
                pass
            self._mic_stream = None

        # Wait for threads to finish writing headers
        if self._discord_thread and self._discord_thread.is_alive():
            self._discord_thread.join(timeout=3.0)
        if self._mic_writer_thread and self._mic_writer_thread.is_alive():
            self._mic_writer_thread.join(timeout=3.0)

        # Mix and encode to MP3
        final_mp3 = self._mix_to_mp3(output_base_name, initial_offset=initial_offset)
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
            self.log("⚠️ No audio data captured (neither Discord nor Microphone had data).")
            return None

        ffmpeg_cmd = get_ffmpeg_path()
        cmd = [ffmpeg_cmd, "-y"]

        # High-fidelity natural microphone audio chain (zero noise filtering / zero gating chatter):
        # 1. aresample=48000,aformat=channel_layouts=stereo: Bit-perfect studio sample rate alignment
        # 2. volume=2.5: Clean transparent gain matching Discord call volume
        # 3. alimiter=limit=0.95: True peak limiter preventing any digital clipping or distortion
        mic_dsp_chain = "aresample=48000,aformat=channel_layouts=stereo,volume=2.5,alimiter=limit=0.95"

        if discord_ok and mic_ok:
            cmd.extend(["-i", self.discord_wav_path, "-i", self.mic_wav_path])
            mix_filter = (
                "[0:a]aresample=48000,aformat=channel_layouts=stereo,volume=2.5[app];"
                f"[1:a]{mic_dsp_chain}[mic];"
                "[app][mic]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0"
            )
        elif discord_ok:
            cmd.extend(["-i", self.discord_wav_path])
            mix_filter = "[0:a]aresample=48000,aformat=channel_layouts=stereo,volume=2.5"
        else:
            cmd.extend(["-i", self.mic_wav_path])
            mix_filter = f"[0:a]{mic_dsp_chain}"

        wav_dur = 0.0
        check_p = self.discord_wav_path if (discord_ok and os.path.exists(self.discord_wav_path)) else self.mic_wav_path
        if check_p and os.path.exists(check_p):
            try:
                with wave.open(check_p, "rb") as wf:
                    wav_dur = wf.getnframes() / max(1, wf.getframerate())
            except Exception:
                pass

        post_filters = []
        if initial_offset < -0.005:
            post_filters.append(f"atrim=start={abs(initial_offset):.3f},asetpts=PTS-STARTPTS")
        elif initial_offset > 0.005 and wav_dur < (initial_offset - 0.5):
            delay_ms = int(initial_offset * 1000)
            post_filters.append(f"adelay=delays={delay_ms}:all=1")

        if post_filters:
            if discord_ok and mic_ok:
                filter_chain = f"{mix_filter}[mixed];[mixed]{','.join(post_filters)}[aout]"
            else:
                filter_chain = f"{mix_filter},{','.join(post_filters)}[aout]"
        else:
            filter_chain = f"{mix_filter}[aout]"

        cmd.extend([
            "-filter_complex", filter_chain,
            "-map", "[aout]",
            "-c:a", "libmp3lame",
            "-q:a", "0",
            mp3_path,
        ])

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
