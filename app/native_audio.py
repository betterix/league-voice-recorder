"""
Native WASAPI Audio Recorder module for Discord and Player Microphone.
Captures Discord audio directly from the Discord process tree via Windows WASAPI
Process Loopback (no OBS Studio required) and combines it with the player's microphone.
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


def find_discord_root_pid():
    """
    Finds the root/parent process ID for Discord.
    Scans running processes for 'discord.exe' (case-insensitive) and identifies
    the root process whose parent is not also a Discord process.
    """
    discord_pids = set()
    pid_to_ppid = {}

    for proc in psutil.process_iter(["pid", "name", "ppid"]):
        try:
            name = proc.info.get("name") or ""
            if "discord" in name.lower() and name.lower().endswith(".exe"):
                pid = proc.info["pid"]
                ppid = proc.info.get("ppid")
                discord_pids.add(pid)
                pid_to_ppid[pid] = ppid
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if not discord_pids:
        return None

    # Find root Discord PID whose parent is not in discord_pids
    for pid, ppid in pid_to_ppid.items():
        if ppid not in discord_pids:
            return pid

    # Fallback to the first found PID
    return next(iter(discord_pids))


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
    Zero-latency background audio recorder for Discord + Microphone.
    Directly attaches to Discord.exe via Windows WASAPI Process Loopback
    and captures default microphone input into synchronized WAV buffers.
    """

    def __init__(self, record_folder=None, log_callback=None):
        self.record_folder = record_folder or os.path.abspath("recordings")
        self.log_callback = log_callback

        self.is_recording = False
        self.is_paused = False
        self.stop_event = threading.Event()

        self.discord_wav_path = None
        self.mic_wav_path = None

        self._discord_thread = None
        self._mic_stream = None
        self._mic_queue = queue.Queue()
        self._mic_writer_thread = None

        self.discord_pid = None
        self.mic_channels = 1

    def log(self, message):
        print(f"[NativeRecorder] {message}")
        if self.log_callback:
            try:
                self.log_callback(message)
            except Exception:
                pass

    def start_recording(self, session_id):
        """Starts asynchronous capture of Discord process audio and player microphone."""
        if self.is_recording:
            return

        self.stop_event.clear()
        self.is_paused = False
        self.discord_pid = find_discord_root_pid()

        temp_dir = os.path.join(self.record_folder, ".temp_audio")
        os.makedirs(temp_dir, exist_ok=True)

        self.discord_wav_path = os.path.join(temp_dir, f"{session_id}_discord.wav")
        self.mic_wav_path = os.path.join(temp_dir, f"{session_id}_mic.wav")

        # 1. Start Discord capture thread if Discord is running
        if self.discord_pid:
            self.log(f"Found Discord root process (PID: {self.discord_pid}). Hooking WASAPI loopback...")
            self._discord_thread = threading.Thread(
                target=self._capture_discord_loop,
                args=(self.discord_pid, self.discord_wav_path),
                daemon=True,
            )
            self._discord_thread.start()
        else:
            self.log("Discord is not running. Recording microphone only.")
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

            self._mic_stream = sd.RawInputStream(
                samplerate=rate,
                channels=channels,
                dtype="float32",
                device=dev_idx,
                callback=mic_callback,
            )
            self._mic_stream.start()
            self.log(f"Microphone recording started (WASAPI default input, {channels}ch @ {rate}Hz).")
        except Exception as e:
            self.log(f"Warning: Could not start microphone capture: {e}")
            self.mic_wav_path = None

        self.is_recording = True

    def _capture_discord_loop(self, pid, wav_path):
        """Polls Discord audio frames from WASAPI ProcessLoopback and writes to WAV."""
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
                        ints = np.clip(floats * 32767.0, -32768, 32767).astype(np.int16)
                        wf.writeframes(ints.tobytes())
                    time.sleep(0.015)

            loop.stop()
        except Exception as e:
            self.log(f"Discord capture error: {e}")

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

        discord_ok = self.discord_wav_path and os.path.exists(self.discord_wav_path) and os.path.getsize(self.discord_wav_path) > 100
        mic_ok = self.mic_wav_path and os.path.exists(self.mic_wav_path) and os.path.getsize(self.mic_wav_path) > 100

        if not discord_ok and not mic_ok:
            self.log("⚠️ No audio data captured (neither Discord nor Microphone had data).")
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
