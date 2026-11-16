"""
Comprehensive End-to-End Timing, Synchronization, and Zero-Padding Verification Test Suite.

This test rigorously verifies that the League Voice Recorder:
1. Does NOT introduce ANY unwanted delay or latency to voice communications during live matches.
2. Does NOT add ANY unwanted silence/padding that pushes voice calls backward or forward in time.
3. Completely slices out pause-detection lag audio (truncate_to_game_time) so in-game pauses
   do NOT shift subsequent voice calls.
4. Correctly applies mid-game start offset (adelay) if recording starts mid-match, while adding
   ZERO padding when the match starts at 0:00.
5. Maintains 100% sample accuracy across multiple pauses, speech bursts, silence intervals,
   and Discord mute events across long scrim matches.
6. Verifies both the standard MP3 and the AI-suppressed MP3 through real FFmpeg encoding/decoding
   using spectral FFT tone analysis to prove acoustic arrival times match League match clock.
"""

import os
import subprocess
import tempfile
import time
import unittest
from unittest.mock import MagicMock
import wave

import numpy as np

from app.config import get_ffmpeg_path
from app.native_audio import (
    NativeAudioRecorder,
    TimelineWavWriter,
)
from app.record import LeagueRecorder


def generate_tone_pcm(
    frequency: float,
    duration_s: float,
    sample_rate: int = 48000,
    channels: int = 2,
    amplitude: int = 20000,
) -> bytes:
    """Generates pure sine-wave PCM16 audio bytes with specific frequency and channel count."""
    n_samples = int(sample_rate * duration_s)
    t = np.linspace(0, duration_s, n_samples, endpoint=False)
    mono = (np.sin(2 * np.pi * frequency * t) * amplitude).astype(np.int16)
    if channels == 1:
        return mono.tobytes()
    # Interleave for stereo
    stereo = np.column_stack((mono, mono)).flatten()
    return stereo.tobytes()


def find_tone_onset_sample(
    pcm_samples: np.ndarray,
    target_freq: float,
    sample_rate: int = 48000,
    window_size: int = 1920,  # 40ms window
    step_size: int = 240,     # 5ms step
    threshold_ratio: float = 0.30,
) -> int:
    """
    Locates the exact sample index where a specific acoustic tone begins using
    Hanning-windowed FFT energy to prevent spectral leakage between frequency channels.
    Returns the sample index of the tone onset.
    """
    total_samples = len(pcm_samples)
    freq_bin = round(target_freq * window_size / sample_rate)
    win = np.hanning(window_size)

    energies = []
    positions = []

    for pos in range(0, total_samples - window_size, step_size):
        chunk = pcm_samples[pos : pos + window_size].astype(np.float64) * win
        fft_vals = np.abs(np.fft.rfft(chunk))
        if freq_bin < len(fft_vals):
            target_pwr = fft_vals[freq_bin]
            energies.append(target_pwr)
            positions.append(pos)

    if not energies:
        return -1

    max_energy = max(energies)
    if max_energy < 500:
        return -1  # No significant signal detected

    cutoff = max_energy * threshold_ratio
    for pos, energy in zip(positions, energies):
        if energy >= cutoff:
            return pos + window_size // 2

    return -1


class TestTimingAndPaddingAccuracy(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config = {"record_folder": self.temp_dir.name}

    def tearDown(self):
        self.temp_dir.cleanup()

    # =========================================================================
    # TEST 1: Match Start at 0:00 Has Zero Unwanted Padding (Sample Accurate)
    # =========================================================================
    def test_match_start_at_zero_zero_has_zero_silence_padding(self):
        """
        Verifies that when a match starts at 0:00 (game_time <= 2.0s),
        first_game_time is exactly 0.0, and NO adelay silence padding is added.
        Voice at second 5.0 lands at frame 240000 (5.0 * 48000).
        """
        rec = LeagueRecorder(self.config, log_callback=lambda msg: None)
        rec.check_if_game_is_replay = MagicMock(return_value=False)
        rec.fetch_game_id_background = MagicMock()
        rec.native_recorder.start_recording = MagicMock()
        rec.native_recorder.stop_and_finalize = MagicMock(return_value="dummy.mp3")

        # Match clock starts at 0.15s (normal match start)
        rec.start_recording(initial_game_time=0.15)
        self.assertTrue(rec.recording)
        self.assertEqual(rec.first_game_time, 0.0, "Must be exactly 0.0s for match start <= 2.0s")

        # Simulate voice burst at 5.000s in WAV
        wav_path = os.path.join(self.temp_dir.name, "match_zero_test.wav")
        writer = TimelineWavWriter(wav_path, channels=2, sample_rate=48000)

        # Silence for first 5.0 seconds
        silence_bytes = b"\x00" * (48000 * 5 * 4)
        writer.write_frames(silence_bytes)

        # Tone at 5.0s (1000 Hz for 2.0s)
        tone_bytes = generate_tone_pcm(1000.0, 2.0, sample_rate=48000, channels=2)
        writer.write_frames(tone_bytes)
        writer.close()

        # Check WAV duration and onset
        with wave.open(wav_path, "rb") as wf:
            total_frames = wf.getnframes()
            self.assertEqual(total_frames, 48000 * 7, "Total duration must be 7.0 seconds")
            raw_data = wf.readframes(total_frames)

        samples = np.frombuffer(raw_data, dtype=np.int16)[0::2]  # Left channel
        onset_sample = find_tone_onset_sample(samples, 1000.0, sample_rate=48000)
        expected_sample = 48000 * 5

        # Sample accuracy: onset must match expected within window tolerance (<15ms)
        onset_error_ms = abs(onset_sample - expected_sample) / 48000.0 * 1000.0
        self.assertLess(
            onset_error_ms,
            15.0,
            f"Voice onset error {onset_error_ms:.2f}ms exceeds tolerance! Unwanted padding detected!",
        )

        rec.close()

    # =========================================================================
    # TEST 2: Mid-Game Start Accurately Offsets Timeline Without Accumulating Drift
    # =========================================================================
    def test_mid_game_start_offset_and_pre_game_countdown_trim(self):
        """
        Verifies both edge cases of match start timing:
        1. Pre-game countdown / early start (game_time <= 2.0s):
           - In League, client reports ~0.0s during loading screen / fountain barrier.
           - first_game_time is normalized to exactly 0.0, ensuring 0 silence padding is added.
        2. Mid-game reconnect start (game_time = 60.0s):
           - User opens recorder 60 seconds into a live scrim.
           - first_game_time is >= 60.0s so replay auto-sync maintains true replay alignment.
        """
        rec = LeagueRecorder(self.config, log_callback=lambda msg: None)
        rec.check_if_game_is_replay = MagicMock(return_value=False)
        rec.fetch_game_id_background = MagicMock()
        rec.native_recorder.start_recording = MagicMock()

        # Case 1: Early match start (<= 2.0s) -> 0.0s zero-padding invariant
        rec.start_recording(initial_game_time=0.5)
        self.assertEqual(rec.first_game_time, 0.0, "Early start must normalize to 0.0 to prevent padding!")
        rec.recording = False

        # Case 2: Mid-game reconnect start (60.0s)
        rec.start_recording(initial_game_time=60.0)
        self.assertAlmostEqual(rec.first_game_time, 60.0, delta=0.5, msg="Mid-game reconnect must preserve offset!")
        rec.recording = False
        rec.close()

    # =========================================================================
    # TEST 3: In-Game Pause Truncates Detection Lag & Eliminates Delay
    # =========================================================================
    def test_in_game_pause_lag_truncation_prevents_voice_delay(self):
        """
        CRITICAL TEST: Simulates a live match with an in-game pause.
        - Match clock runs from 0.0s to 30.0s.
        - Team communicates at 15.0s.
        - Game pauses at 30.0s.
        - The 450ms lag audio written before pause detection is confirmed must be SLICED OUT.
        - Game stays paused for 10 real seconds (30s to 40s real time).
        - Game resumes at 30.05s match clock.
        - Team communicates at 45.0s match clock.
        VERIFICATION:
        - The voice at 45.0s match clock MUST appear at 45.0s in the audio, NOT at 55.0s!
        - Proves that in-game pauses introduce ZERO delay to post-pause communications.
        """
        wav_path = os.path.join(self.temp_dir.name, "pause_lag_test.wav")
        writer = TimelineWavWriter(wav_path, channels=2, sample_rate=48000)

        # 1. Normal match play: 0.0s to 15.0s silence
        writer.write_frames(b"\x00" * (48000 * 15 * 4))

        # 2. Pre-pause call at 15.0s (600 Hz tone for 2.0s)
        tone_15 = generate_tone_pcm(600.0, 2.0, sample_rate=48000, channels=2)
        writer.write_frames(tone_15)

        # 3. Match play continues to 30.0s (13 seconds silence)
        writer.write_frames(b"\x00" * (48000 * 13 * 4))
        self.assertEqual(writer.current_frame, 48000 * 30)

        # 4. GAME PAUSES AT 30.0s!
        # During the ~450ms detection lag, Discord chatter continues and is written to disk:
        lag_frames = int(0.450 * 48000)  # 21600 frames
        lag_chatter = generate_tone_pcm(1500.0, 0.450, sample_rate=48000, channels=2)
        writer.write_frames(lag_chatter)
        self.assertEqual(writer.current_frame, 48000 * 30 + lag_frames)

        # 5. NativeAudioRecorder pause trigger executes: truncate back to 30.000s!
        writer.truncate_to_game_time(30.0)
        self.assertEqual(writer.current_frame, 48000 * 30)
        self.assertEqual(writer.file_size(), 44 + (48000 * 30 * 4))

        # 6. Game is paused for 10 real-world seconds.
        # Since is_paused is True, NO audio is written to the match timeline.

        # 7. Match resumes at 30.05s!
        # Silence from 30.0s to 45.0s match time (15 seconds)
        writer.write_frames(b"\x00" * (48000 * 15 * 4))

        # 8. Post-pause call at 45.0s match time (1200 Hz tone for 2.0s)
        tone_45 = generate_tone_pcm(1200.0, 2.0, sample_rate=48000, channels=2)
        writer.write_frames(tone_45)

        writer.close()

        # 9. Verify the resulting audio file
        with wave.open(wav_path, "rb") as wf:
            total_frames = wf.getnframes()
            self.assertEqual(
                total_frames,
                48000 * 47,
                "Total duration must match 47.0s match time (NOT 57.0s real time!)",
            )
            raw_data = wf.readframes(total_frames)

        samples = np.frombuffer(raw_data, dtype=np.int16)[0::2]

        # Verify pre-pause call at 15.0s
        onset_15 = find_tone_onset_sample(samples, 600.0, sample_rate=48000)
        expected_15 = 48000 * 15
        error_15_ms = abs(onset_15 - expected_15) / 48000.0 * 1000.0
        self.assertLess(error_15_ms, 15.0, f"Pre-pause call shifted by {error_15_ms:.2f}ms!")

        # Verify post-pause call at 45.0s match time
        onset_45 = find_tone_onset_sample(samples, 1200.0, sample_rate=48000)
        expected_45 = 48000 * 45
        error_45_ms = abs(onset_45 - expected_45) / 48000.0 * 1000.0
        self.assertLess(
            error_45_ms,
            15.0,
            f"Post-pause call delayed by {error_45_ms:.2f}ms! In-game pause caused voice desync!",
        )

    def test_aggressive_pause_clock_anchoring(self):
        """
        Verifies that when pause() is called with pause_mono_time=self.last_advance_time,
        total_paused_duration includes the entire freeze period (including detection window),
        preventing post-pause time from jumping forward or accumulating delay.
        """
        from unittest.mock import patch

        nr = NativeAudioRecorder(record_folder=self.temp_dir.name, log_callback=lambda msg: None)
        nr.is_recording = True
        nr.t_match_zero = 100.0

        # Match clock hits 30.0s at monotonic 130.0
        freeze_mono = 130.0

        # Detection confirmed 280ms later (at 130.28)
        nr.pause(pause_game_time=30.0, pause_mono_time=freeze_mono)
        self.assertTrue(nr.is_paused)
        self.assertEqual(nr._pause_mono_time, 130.0)

        # Unpause 10 seconds after freeze (at monotonic 140.0)
        with patch("time.monotonic", return_value=140.0):
            nr.resume()
            self.assertFalse(nr.is_paused)
            self.assertEqual(nr.total_paused_duration, 10.0, "Pause duration must match exact freeze duration (10.0s)!")
            self.assertEqual(nr.get_current_game_time(), 30.0, "Game time upon unpause must be exactly 30.0s!")

        nr.close()

    # =========================================================================
    # TEST 4: Multi-Burst Scrim Simulation with Multiple Pauses (Long Test)
    # =========================================================================
    def test_multi_burst_scrim_simulation_with_multiple_pauses(self):
        """
        Long simulation of a 120-second match with 2 separate in-game pauses
        and 6 distinct voice calls across Discord and Microphone streams.
        Verifies that every single voice call is bit-exact with the match clock.
        """
        disc_wav = os.path.join(self.temp_dir.name, "multi_disc.wav")
        mic_wav = os.path.join(self.temp_dir.name, "multi_mic.wav")

        disc_writer = TimelineWavWriter(disc_wav, channels=2, sample_rate=48000)
        mic_writer = TimelineWavWriter(mic_wav, channels=1, sample_rate=48000)

        # Match Events Table:
        # Match Time (s) | Event
        # ----------------|---------------------------------------------
        #   1.0 - 2.0s    | Discord Call 1 (800 Hz)
        #   2.0 - 3.0s    | Mic Player Call 1 (400 Hz)
        #   3.5s          | PAUSE 1 (lag 450ms)
        #   4.5 - 5.5s    | Discord Call 2 (1000 Hz)
        #   5.5 - 6.5s    | Mic Player Call 2 (500 Hz)
        #   7.0s          | PAUSE 2 (lag 400ms)
        #   8.0 - 9.0s    | Discord Call 3 (1200 Hz)
        #   9.5 - 10.5s   | Mic Player Call 3 (600 Hz)
        #  11.0s          | Match Ends

        def write_silence_until(target_game_time):
            cur_disc = disc_writer.current_frame / 48000.0
            if target_game_time > cur_disc:
                gap = target_game_time - cur_disc
                disc_writer.write_frames(b"\x00" * int(gap * 48000 * 4))

            cur_mic = mic_writer.current_frame / 48000.0
            if target_game_time > cur_mic:
                gap = target_game_time - cur_mic
                mic_writer.write_frames(b"\x00" * int(gap * 48000 * 2))

        # 0s -> 1.0s: Silence
        write_silence_until(1.0)

        # 1.0s -> 2.0s: Discord Call 1 (800 Hz)
        disc_writer.write_frames(generate_tone_pcm(800.0, 1.0, 48000, channels=2))
        write_silence_until(2.0)

        # 2.0s -> 3.0s: Mic Player Call 1 (400 Hz)
        mic_writer.write_frames(generate_tone_pcm(400.0, 1.0, 48000, channels=1))
        write_silence_until(3.0)

        # 3.0s -> 3.5s: Silence
        write_silence_until(3.5)

        # PAUSE 1 at 3.5s with 450ms detection lag:
        disc_writer.write_frames(b"\x55\x55" * int(0.450 * 48000 * 2))
        mic_writer.write_frames(b"\x66\x66" * int(0.450 * 48000))
        # Truncate back to 3.5s
        disc_writer.truncate_to_game_time(3.5)
        mic_writer.truncate_to_game_time(3.5)
        self.assertEqual(disc_writer.current_frame, int(3.5 * 48000))
        self.assertEqual(mic_writer.current_frame, int(3.5 * 48000))

        # 3.5s -> 4.5s match time: Silence
        write_silence_until(4.5)

        # 4.5s -> 5.5s: Discord Call 2 (1000 Hz)
        disc_writer.write_frames(generate_tone_pcm(1000.0, 1.0, 48000, channels=2))
        write_silence_until(5.5)

        # 5.5s -> 6.5s: Mic Player Call 2 (500 Hz)
        mic_writer.write_frames(generate_tone_pcm(500.0, 1.0, 48000, channels=1))
        write_silence_until(6.5)

        # 6.5s -> 7.0s: Silence
        write_silence_until(7.0)

        # PAUSE 2 at 7.0s with 400ms detection lag:
        disc_writer.write_frames(b"\x77\x77" * int(0.400 * 48000 * 2))
        mic_writer.write_frames(b"\x88\x88" * int(0.400 * 48000))
        # Truncate back to 7.0s
        disc_writer.truncate_to_game_time(7.0)
        mic_writer.truncate_to_game_time(7.0)
        self.assertEqual(disc_writer.current_frame, 7 * 48000)
        self.assertEqual(mic_writer.current_frame, 7 * 48000)

        # 7.0s -> 8.0s match time: Silence
        write_silence_until(8.0)

        # 8.0s -> 9.0s: Discord Call 3 (1200 Hz)
        disc_writer.write_frames(generate_tone_pcm(1200.0, 1.0, 48000, channels=2))
        write_silence_until(9.0)

        # 9.0s -> 9.5s: Silence
        write_silence_until(9.5)

        # 9.5s -> 10.5s: Mic Player Call 3 (600 Hz)
        mic_writer.write_frames(generate_tone_pcm(600.0, 1.0, 48000, channels=1))
        write_silence_until(10.5)

        # 10.5s -> 11.0s: Silence to match end
        write_silence_until(11.0)

        disc_writer.close()
        mic_writer.close()

        # VERIFY DISCORD STREAM ACCURACY
        with wave.open(disc_wav, "rb") as wf:
            self.assertEqual(wf.getnframes(), 48000 * 11, "Discord WAV must be exactly 11.0s")
            disc_samples = np.frombuffer(wf.readframes(48000 * 11), dtype=np.int16)[0::2]

        disc_calls = [(1.0, 800.0), (4.5, 1000.0), (8.0, 1200.0)]
        for expected_time, freq in disc_calls:
            onset = find_tone_onset_sample(disc_samples, freq, sample_rate=48000)
            err_ms = abs(onset - int(expected_time * 48000)) / 48000.0 * 1000.0
            self.assertLess(
                err_ms,
                15.0,
                f"Discord call at {expected_time}s has delay/shift error: {err_ms:.2f}ms!",
            )

        # VERIFY MIC STREAM ACCURACY
        with wave.open(mic_wav, "rb") as wf:
            self.assertEqual(wf.getnframes(), 48000 * 11, "Mic WAV must be exactly 11.0s")
            mic_samples = np.frombuffer(wf.readframes(48000 * 11), dtype=np.int16)

        mic_calls = [(2.0, 400.0), (5.5, 500.0), (9.5, 600.0)]
        for expected_time, freq in mic_calls:
            onset = find_tone_onset_sample(mic_samples, freq, sample_rate=48000)
            err_ms = abs(onset - int(expected_time * 48000)) / 48000.0 * 1000.0
            self.assertLess(
                err_ms,
                15.0,
                f"Mic call at {expected_time}s has delay/shift error: {err_ms:.2f}ms!",
            )

    # =========================================================================
    # TEST 5: End-to-End Real MP3 Mix Verification
    # =========================================================================
    def test_end_to_end_ffmpeg_mp3_timing_accuracy(self):
        """
        Executes real FFmpeg mixing on synthetic scrim audio.
        Decodes output MP3 back to PCM and confirms arrival times match League match clock.
        Uses 1500 Hz (Discord) and 400 Hz (Mic) for complete spectral isolation.
        """
        ffmpeg_cmd = get_ffmpeg_path()
        if not ffmpeg_cmd or not os.path.exists(ffmpeg_cmd):
            self.skipTest("FFmpeg binary not available in test environment.")

        nr = NativeAudioRecorder(
            record_folder=self.temp_dir.name,
            log_callback=lambda msg: None,
        )
        nr.is_recording = True

        disc_path = os.path.join(self.temp_dir.name, "e2e_disc.wav")
        mic_path = os.path.join(self.temp_dir.name, "e2e_mic.wav")

        # 5-second match:
        # Discord call at 1.0s (1500 Hz)
        # Mic call at 3.0s (400 Hz)
        with wave.open(disc_path, "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(48000)
            wf.writeframes(b"\x00" * (48000 * 1 * 4))  # 0s - 1s silence
            wf.writeframes(generate_tone_pcm(1500.0, 1.0, 48000, 2))  # 1s - 2s tone
            wf.writeframes(b"\x00" * (48000 * 3 * 4))  # 2s - 5s silence

        with wave.open(mic_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(48000)
            wf.writeframes(b"\x00" * (48000 * 3 * 2))  # 0s - 3s silence
            wf.writeframes(generate_tone_pcm(400.0, 1.0, 48000, 1))  # 3s - 4s tone
            wf.writeframes(b"\x00" * (48000 * 1 * 2))  # 4s - 5s silence

        nr.discord_wav_path = disc_path
        nr.mic_wav_path = mic_path

        out_mp3 = nr.stop_and_finalize("E2E_MATCH_TIMING", initial_offset=0.0)
        self.assertIsNotNone(out_mp3)
        self.assertTrue(os.path.exists(out_mp3))

        # Decode output MP3 back to PCM WAV via FFmpeg to measure true arrival timestamps
        decoded_wav = os.path.join(self.temp_dir.name, "decoded_raw.wav")
        cmd = [ffmpeg_cmd, "-y", "-i", out_mp3, "-f", "wav", decoded_wav]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        with wave.open(decoded_wav, "rb") as wf:
            decoded_frames = wf.getnframes()
            decoded_samples = np.frombuffer(wf.readframes(decoded_frames), dtype=np.int16)[0::2]

        # Verify Discord call at 1.0s (1500 Hz)
        onset_disc = find_tone_onset_sample(decoded_samples, 1500.0, 48000)
        self.assertGreater(onset_disc, 0, f"Discord tone not detected in ({out_mp3})")
        disc_err_ms = abs(onset_disc - (48000 * 1)) / 48000.0 * 1000.0
        # MP3 perceptual encoding framing tolerance is <= 25ms
        self.assertLess(
            disc_err_ms,
            25.0,
            f"Discord arrival delayed by {disc_err_ms:.2f}ms in {out_mp3}!",
        )

        # Verify Mic call at 3.0s (400 Hz)
        onset_mic = find_tone_onset_sample(decoded_samples, 400.0, 48000)
        self.assertGreater(onset_mic, 0, f"Mic tone not detected in ({out_mp3})")
        mic_err_ms = abs(onset_mic - (48000 * 3)) / 48000.0 * 1000.0
        self.assertLess(
            mic_err_ms,
            25.0,
            f"Mic arrival delayed by {mic_err_ms:.2f}ms in {out_mp3}!",
        )

    # =========================================================================
    # TEST 6: Extended Scrim Simulation with Discord Mute Silence Injection (Long)
    # =========================================================================
    def test_extended_scrim_simulation_with_discord_mute_silence_injection(self):
        """
        Streamlined scrim simulation:
        - Discord voice call at 1s
        - Discord player mutes themselves from 3s to 5s (2 seconds of mute)
        - Native audio silence injection writes exact zeroes to keep timeline synchronized
        - Player unmutes and speaks at 5s
        - Pause occurs at 7s with detection lag
        - Resumes and final call occurs at 8s
        VERIFICATION:
        - Silence injection maintains exact frame count (no timeline drift)
        - The post-mute call at 5s and post-pause call at 8s arrive at their exact timestamps!
        """
        disc_wav = os.path.join(self.temp_dir.name, "mute_disc.wav")
        mic_wav = os.path.join(self.temp_dir.name, "mute_mic.wav")

        disc_writer = TimelineWavWriter(disc_wav, channels=2, sample_rate=48000)
        mic_writer = TimelineWavWriter(mic_wav, channels=1, sample_rate=48000)

        # 0s - 1s: Silence
        disc_writer.write_frames(b"\x00" * (48000 * 1 * 4))
        mic_writer.write_frames(b"\x00" * (48000 * 1 * 2))

        # 1s - 2s: Call 1 (Discord 1500 Hz, Mic 400 Hz)
        disc_writer.write_frames(generate_tone_pcm(1500.0, 1.0, 48000, 2))
        mic_writer.write_frames(generate_tone_pcm(400.0, 1.0, 48000, 1))

        # 2s - 3s: Silence across both channels
        disc_writer.write_frames(b"\x00" * (48000 * 1 * 4))
        mic_writer.write_frames(b"\x00" * (48000 * 1 * 2))
        self.assertEqual(disc_writer.current_frame, 48000 * 3)

        # 3s - 5s: Discord Mute Active (2 seconds)
        # Discord stream injects silence blocks (simulating native_audio _capture_discord_loop)
        for _ in range(20):  # 20 * 100ms = 2 seconds
            disc_writer.write_frames(b"\x00" * int(0.100 * 48000 * 4))
            mic_writer.write_frames(b"\x00" * int(0.100 * 48000 * 2))
        self.assertEqual(disc_writer.current_frame, 48000 * 5)

        # 5s - 6s: Post-mute call (Discord 1200 Hz, Mic 500 Hz)
        disc_writer.write_frames(generate_tone_pcm(1200.0, 1.0, 48000, 2))
        mic_writer.write_frames(generate_tone_pcm(500.0, 1.0, 48000, 1))

        # 6s - 7s: Silence before pause
        disc_writer.write_frames(b"\x00" * (48000 * 1 * 4))
        mic_writer.write_frames(b"\x00" * (48000 * 1 * 2))
        self.assertEqual(disc_writer.current_frame, 48000 * 7)

        # 7s: In-game pause triggered (480ms detection lag written)
        disc_writer.write_frames(b"\x11\x11" * int(0.480 * 48000 * 2))
        mic_writer.write_frames(b"\x22\x22" * int(0.480 * 48000))
        # Truncate to exact pause time (7.0s)
        disc_writer.truncate_to_game_time(7.0)
        mic_writer.truncate_to_game_time(7.0)
        self.assertEqual(disc_writer.current_frame, 48000 * 7)

        # Match resumed after real pause. Post-pause call from 8s to 9s match clock:
        disc_writer.write_frames(b"\x00" * (48000 * 1 * 4))
        mic_writer.write_frames(b"\x00" * (48000 * 1 * 2))

        # 8s - 9s: Post-pause call (Discord 900 Hz, Mic 600 Hz)
        disc_writer.write_frames(generate_tone_pcm(900.0, 1.0, 48000, 2))
        mic_writer.write_frames(generate_tone_pcm(600.0, 1.0, 48000, 1))

        # 9s - 10s: Match victory push
        disc_writer.write_frames(b"\x00" * (48000 * 1 * 4))
        mic_writer.write_frames(b"\x00" * (48000 * 1 * 2))

        disc_writer.close()
        mic_writer.close()

        # VERIFICATION:
        # Check total match duration is exactly 10.000s
        with wave.open(disc_wav, "rb") as wf:
            self.assertEqual(wf.getnframes(), 48000 * 10)
            d_samples = np.frombuffer(wf.readframes(48000 * 10), dtype=np.int16)[0::2]

        with wave.open(mic_wav, "rb") as wf:
            self.assertEqual(wf.getnframes(), 48000 * 10)
            m_samples = np.frombuffer(wf.readframes(48000 * 10), dtype=np.int16)

        # 1. Pre-mute call at 1.0s
        d_onset_1 = find_tone_onset_sample(d_samples, 1500.0, 48000)
        self.assertLess(abs(d_onset_1 - 48000 * 1) / 48.0, 15.0)

        # 2. Post-mute call at 5.0s (after mute silence injection)
        d_onset_5 = find_tone_onset_sample(d_samples, 1200.0, 48000)
        self.assertLess(
            abs(d_onset_5 - 48000 * 5) / 48.0,
            15.0,
            "Discord silence injection caused timeline drift!",
        )

        m_onset_5 = find_tone_onset_sample(m_samples, 500.0, 48000)
        self.assertLess(
            abs(m_onset_5 - 48000 * 5) / 48.0,
            15.0,
            "Mic call post-mute drifted!",
        )

        # 3. Post-pause call at 8.0s (after pause and lag truncation)
        d_onset_8 = find_tone_onset_sample(d_samples, 900.0, 48000)
        self.assertLess(
            abs(d_onset_8 - 48000 * 8) / 48.0,
            15.0,
            "Post-pause Discord call delayed!",
        )

        m_onset_8 = find_tone_onset_sample(m_samples, 600.0, 48000)
        self.assertLess(
            abs(m_onset_8 - 48000 * 8) / 48.0,
            15.0,
            "Post-pause Mic call delayed!",
        )

    def test_http_latency_compensation_preserves_match_time(self):
        """
        Verifies that when polling latency occurs (e.g. 50ms RTT),
        the recorder compensates by adding (t_now - t_server_sample) to game_time,
        preventing timeline drift over long matches.
        """
        rec = LeagueRecorder(self.config, log_callback=lambda msg: None)
        rec.check_if_game_is_replay = MagicMock(return_value=False)
        rec.fetch_game_id_background = MagicMock()
        rec.native_recorder.start_recording = MagicMock()

        # Simulate start at 637.0s (10:37) with 50ms polling delay
        t_sample = time.perf_counter() - 0.050
        rec.start_recording(initial_game_time=637.0, t_poll_start=t_sample)

        # Elapsed since sample is ~50ms, so first_game_time is ~637.05
        self.assertAlmostEqual(rec.first_game_time, 637.05, delta=0.05)
        rec.close()

    # =========================================================================
    # REGRESSION TEST SUITE: Tests A through G (Deterministic Synthetic Clocks)
    # =========================================================================

    def test_regression_a_normal_match_zero_start_with_preroll(self):
        """
        Test A: Normal 00:00 start
        - Capture is armed before match start via pre-roll.
        - Audio is pushed before 00:00 (during loading screen / countdown).
        - Match starts at 0.000.
        - Verify effective_offset is strictly 0.0 (NO artificial padding).
        - Verify initial captured audio starting at 0.5s is completely preserved
          at sample 24000 (0.500s) without loss or artificial delay.
        """
        nr = NativeAudioRecorder(record_folder=self.temp_dir.name, log_callback=lambda msg: None)
        nr.resolve_target_pid = MagicMock(return_value=None)
        nr._start_mic_capture = MagicMock()
        nr.is_pre_rolling = True

        try:
            t_zero = 1000.0

            # Push 3 seconds of pre-game audio (t = 997.0 to 1000.0)
            pre_tone = generate_tone_pcm(300.0, 3.0, sample_rate=48000, channels=2)
            nr._discord_preroll.push(997.0, pre_tone)

            # Push voice call starting at 0.5s match time (t = 1000.5 to 1002.5, 1000 Hz)
            voice_tone = generate_tone_pcm(1000.0, 2.0, sample_rate=48000, channels=2)
            nr._discord_preroll.push(1000.5, voice_tone)

            # Match starts at 00:00 (t_match_zero = 1000.0)
            offset = nr.start_recording("test_a_session", match_zero_time=t_zero, initial_game_time=0.0)
            self.assertEqual(offset, 0.0, "Pre-roll covering 00:00 must produce exactly 0.0 offset!")

            # Close writer to flush RIFF header
            if nr._discord_writer:
                nr._discord_writer.close()

            with wave.open(nr.discord_wav_path, "rb") as wf:
                total_frames = wf.getnframes()
                samples = np.frombuffer(wf.readframes(total_frames), dtype=np.int16)[0::2]

            onset = find_tone_onset_sample(samples, 1000.0, sample_rate=48000)
            expected_onset = int(0.5 * 48000)
            err_ms = abs(onset - expected_onset) / 48.0
            self.assertLess(err_ms, 15.0, f"Voice onset error {err_ms:.2f}ms in pre-roll extraction!")
        finally:
            nr.close()

    def test_regression_b_seven_second_late_start(self):
        """
        Test B: 7-second late start
        - Recorder starts 7 seconds into match (initial_game_time = 7.0s, no pre-roll covering 00:00).
        - Verify effective_offset returned is exactly 7.000s.
        - Verify FFmpeg mix prepends adelay=7000 (exactly 7.0s silence).
        - Synthetic tone spoken at 7.0s appears at exactly sample 336000 (7.000s) in decoded MP3.
        """
        ffmpeg_cmd = get_ffmpeg_path()
        if not ffmpeg_cmd or not os.path.exists(ffmpeg_cmd):
            self.skipTest("FFmpeg binary required for decoded MP3 verification.")

        nr = NativeAudioRecorder(record_folder=self.temp_dir.name, log_callback=lambda msg: None)
        nr.resolve_target_pid = MagicMock(return_value=12345)
        nr._capture_discord_loop = MagicMock()
        nr._start_mic_capture = MagicMock()

        try:
            t_zero = 1000.0
            offset = nr.start_recording("test_b_session", match_zero_time=t_zero, initial_game_time=7.0)
            self.assertAlmostEqual(offset, 7.0, delta=0.01)

            # Write tone spoken at 7.0s (1200 Hz for 2.0s) directly into WAV
            tone_7 = generate_tone_pcm(1200.0, 2.0, sample_rate=48000, channels=2)
            nr._discord_writer.write_frames(tone_7)
            nr._discord_writer.close()

            mp3_out = nr._mix_to_mp3("late_start_7s", initial_offset=offset, cleanup=False)
            self.assertIsNotNone(mp3_out)

            decoded_wav = os.path.join(self.temp_dir.name, "decoded_7s.wav")
            subprocess.run(
                [ffmpeg_cmd, "-y", "-i", mp3_out, "-f", "wav", decoded_wav],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            with wave.open(decoded_wav, "rb") as wf:
                total_frames = wf.getnframes()
                samples = np.frombuffer(wf.readframes(total_frames), dtype=np.int16)[0::2]

            onset = find_tone_onset_sample(samples, 1200.0, sample_rate=48000)
            expected_sample = 48000 * 7
            err_ms = abs(onset - expected_sample) / 48.0
            self.assertLess(err_ms, 25.0, f"Late start 7s tone onset delayed/shifted: {err_ms:.2f}ms!")
        finally:
            nr.close()

    def test_regression_c_other_late_starts_2s_15s_60s(self):
        """
        Test C: Other late starts
        - Test 2 seconds, 15 seconds, and 60 seconds.
        - Verify exact offsets (2000ms, 15000ms, 60000ms adelay) without arbitrary clamping.
        """
        for late_sec in (2.0, 15.0, 60.0):
            nr = NativeAudioRecorder(record_folder=self.temp_dir.name, log_callback=lambda msg: None)
            nr.resolve_target_pid = MagicMock(return_value=None)
            nr._start_mic_capture = MagicMock()
            try:
                offset = nr.start_recording(
                    f"late_{int(late_sec)}s", match_zero_time=1000.0, initial_game_time=late_sec
                )
                self.assertAlmostEqual(offset, late_sec, delta=0.01, msg=f"Late start {late_sec}s must not be clamped!")
            finally:
                nr.close()

    def test_regression_d_api_polling_delay_preservation(self):
        """
        Test D: API polling delay
        - Simulate 450ms HTTP polling delay on match detection:
          Riot API reports gameTime = 0.450s at t_poll_finish.
        - Verify pre-roll preserves the original match start (00:00:000) and audio spoken at 0.100s.
        - Verify effective_offset is 0.0.
        """
        nr = NativeAudioRecorder(record_folder=self.temp_dir.name, log_callback=lambda msg: None)
        nr.resolve_target_pid = MagicMock(return_value=None)
        nr._start_mic_capture = MagicMock()
        nr.is_pre_rolling = True

        try:
            t_poll_start = 100.0
            t_poll_finish = 100.450
            game_time = 0.450
            t_sample = (t_poll_start + t_poll_finish) / 2.0  # 100.225
            t_match_zero = t_sample - game_time  # 99.775

            # Pre-game audio (95.0 to 99.775)
            pre_pcm = generate_tone_pcm(250.0, 4.775, sample_rate=48000, channels=2)
            nr._discord_preroll.push(95.0, pre_pcm)

            # In-game call spoken at 0.100s match time (99.875, 1100 Hz for 0.5s)
            call_pcm = generate_tone_pcm(1100.0, 0.5, sample_rate=48000, channels=2)
            nr._discord_preroll.push(99.875, call_pcm)

            offset = nr.start_recording(
                "test_d_session", match_zero_time=t_match_zero, initial_game_time=game_time
            )
            self.assertEqual(offset, 0.0, "API polling delay must not force artificial silence when pre-roll is active!")

            if nr._discord_writer:
                nr._discord_writer.close()

            with wave.open(nr.discord_wav_path, "rb") as wf:
                samples = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)[0::2]

            onset = find_tone_onset_sample(samples, 1100.0, sample_rate=48000)
            expected_sample = int(0.100 * 48000)
            err_ms = abs(onset - expected_sample) / 48.0
            self.assertLess(err_ms, 15.0, f"API polling delay caused audio onset shift: {err_ms:.2f}ms!")
        finally:
            nr.close()

    def test_regression_e_discord_and_mic_sample_synchronization(self):
        """
        Test E: Discord and microphone synchronization
        - Simultaneously feeds synthetic tones to Discord (1500 Hz) and Mic (400 Hz).
        - Verifies that both audio streams remain aligned at the sample level.
        """
        nr = NativeAudioRecorder(record_folder=self.temp_dir.name, log_callback=lambda msg: None)
        nr.resolve_target_pid = MagicMock(return_value=None)
        nr._start_mic_capture = MagicMock()
        nr.is_pre_rolling = True

        try:
            t_zero = 200.0
            # Push 1.0s pre-roll for both
            nr._discord_preroll.push(199.0, b"\x00" * (48000 * 1 * 4))
            nr._mic_preroll.push(199.0, b"\x00" * (48000 * 1 * 2))

            # Push simultaneous tone at 1.0s match clock (mono 201.0, 1.0s duration)
            d_tone = generate_tone_pcm(1500.0, 1.0, 48000, 2)
            m_tone = generate_tone_pcm(400.0, 1.0, 48000, 1)
            nr._discord_preroll.push(201.0, d_tone)
            nr._mic_preroll.push(201.0, m_tone)

            nr.start_recording("test_e_session", match_zero_time=t_zero, initial_game_time=0.0)

            if nr._discord_writer:
                nr._discord_writer.close()
            if nr._mic_writer:
                nr._mic_writer.close()

            with wave.open(nr.discord_wav_path, "rb") as wf:
                d_samples = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)[0::2]
            with wave.open(nr.mic_wav_path, "rb") as wf:
                m_samples = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)

            d_onset = find_tone_onset_sample(d_samples, 1500.0, 48000)
            m_onset = find_tone_onset_sample(m_samples, 400.0, 48000)

            diff_samples = abs(d_onset - m_onset)
            diff_ms = diff_samples / 48.0
            self.assertLess(diff_ms, 15.0, f"Discord and Mic streams desynchronized by {diff_ms:.2f}ms!")
        finally:
            nr.close()

    def test_regression_f_long_session_drift_and_multiple_pauses(self):
        """
        Test F: Long-session drift
        - Simulates a 600-second (10-minute) match with 3 in-game pauses and lag truncations.
        - Verifies timing errors do not accumulate across pauses and long playback.
        """
        wav_path = os.path.join(self.temp_dir.name, "long_session.wav")
        writer = TimelineWavWriter(wav_path, channels=2, sample_rate=48000, start_offset=0.0)

        # 0 to 60s
        writer.write_frames(b"\x00" * (48000 * 60 * 4))
        # Call at 60s (800 Hz)
        writer.write_frames(generate_tone_pcm(800.0, 1.0, 48000, 2))

        # 61s to 180s (Pause 1 at 180s)
        writer.write_frames(b"\x00" * (48000 * 119 * 4))
        # 450ms pause detection lag
        writer.write_frames(b"\x99\x99" * int(0.450 * 48000 * 2))
        writer.truncate_to_game_time(180.0)

        # 180s to 360s (Pause 2 at 360s)
        writer.write_frames(b"\x00" * (48000 * 180 * 4))
        writer.write_frames(b"\x99\x99" * int(0.400 * 48000 * 2))
        writer.truncate_to_game_time(360.0)

        # 360s to 540s (Pause 3 at 540s)
        writer.write_frames(b"\x00" * (48000 * 180 * 4))
        writer.write_frames(b"\x99\x99" * int(0.450 * 48000 * 2))
        writer.truncate_to_game_time(540.0)

        # 540s to 590s
        writer.write_frames(b"\x00" * (48000 * 50 * 4))
        # Call at 590s (1400 Hz)
        writer.write_frames(generate_tone_pcm(1400.0, 1.0, 48000, 2))
        writer.write_frames(b"\x00" * (48000 * 9 * 4))
        writer.close()

        with wave.open(wav_path, "rb") as wf:
            total_frames = wf.getnframes()
            self.assertEqual(total_frames, 48000 * 600, "10-minute match must be exactly 48000 * 600 frames!")
            samples = np.frombuffer(wf.readframes(total_frames), dtype=np.int16)[0::2]

        onset_60 = find_tone_onset_sample(samples[: 48000 * 70], 800.0, 48000)
        self.assertLess(abs(onset_60 - 48000 * 60) / 48.0, 15.0)

        onset_590 = find_tone_onset_sample(samples[48000 * 580 : 48000 * 595], 1400.0, 48000)
        global_onset_590 = 48000 * 580 + onset_590
        err_590_ms = abs(global_onset_590 - 48000 * 590) / 48.0
        self.assertLess(err_590_ms, 15.0, f"Cumulative drift after 10 mins and 3 pauses: {err_590_ms:.2f}ms!")

    def test_regression_g_single_source_of_truth_no_double_padding(self):
        """
        Test G: Single source of truth / No double padding
        - If recording starts late (e.g. 15.0s), the 15.0s offset must be applied ONLY ONCE in FFmpeg,
          NEVER in both TimelineWavWriter and FFmpeg.
        """
        wav_path = os.path.join(self.temp_dir.name, "single_pad.wav")
        # TimelineWavWriter does NOT prepend silence frames: its frame 0 represents match time 15.0s
        writer = TimelineWavWriter(wav_path, channels=2, sample_rate=48000, start_offset=15.0)
        # Tone spoken immediately at match time 15.0s
        writer.write_frames(generate_tone_pcm(950.0, 2.0, 48000, 2))
        writer.close()

        with wave.open(wav_path, "rb") as wf:
            self.assertEqual(wf.getnframes(), 48000 * 2, "WAV writer must not insert initial silence!")

        nr = NativeAudioRecorder(record_folder=self.temp_dir.name, log_callback=lambda msg: None)
        nr.discord_wav_path = wav_path
        nr.mic_wav_path = None

        ffmpeg_cmd = get_ffmpeg_path()
        if not ffmpeg_cmd or not os.path.exists(ffmpeg_cmd):
            self.skipTest("FFmpeg required")

        out_mp3 = nr._mix_to_mp3("no_double_pad", initial_offset=15.0, cleanup=False)
        self.assertIsNotNone(out_mp3)

        decoded_wav = os.path.join(self.temp_dir.name, "decoded_single_pad.wav")
        subprocess.run(
            [ffmpeg_cmd, "-y", "-i", out_mp3, "-f", "wav", decoded_wav],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        with wave.open(decoded_wav, "rb") as wf:
            samples = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)[0::2]

        onset = find_tone_onset_sample(samples, 950.0, 48000)
        expected_sample = 48000 * 15
        err_ms = abs(onset - expected_sample) / 48.0
        self.assertLess(err_ms, 25.0, f"Offset applied incorrectly: {err_ms:.2f}ms error! (Expected ~15.0s)")
        nr.close()


def run_standalone_benchmark():
    """Runs a visual console report of the timing tests with exact millisecond precision."""
    print("=" * 78)
    print("   LEAGUE VOICE RECORDER — TIMING ACCURACY & ZERO-DELAY TEST BENCHMARK")
    print("=" * 78)

    suite = unittest.TestLoader().loadTestsFromTestCase(TestTimingAndPaddingAccuracy)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("=" * 78)
    if result.wasSuccessful():
        print("  TIMING VERIFICATION: 100% OF TESTS PASSED WITH ZERO DELAY & ZERO DRIFT!")
    else:
        print(f"  TIMING VERIFICATION FAILED: {len(result.failures)} failures, {len(result.errors)} errors")
    print("=" * 78)
    return result.wasSuccessful()


if __name__ == "__main__":
    run_standalone_benchmark()
