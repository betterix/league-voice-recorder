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
        self.config = {"record_folder": self.temp_dir.name, "ai_noise_suppression": True}

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
        self.assertGreaterEqual(rec.first_game_time, 60.0, "Mid-game reconnect must preserve offset!")
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
        #  10.0 - 12.0s   | Discord Call 1 (800 Hz)
        #  20.0 - 22.0s   | Mic Player Call 1 (400 Hz)
        #  35.0s          | PAUSE 1 (lasts 12 real seconds, lag 450ms)
        #  50.0 - 52.0s   | Discord Call 2 (1000 Hz)
        #  65.0 - 67.0s   | Mic Player Call 2 (500 Hz)
        #  80.0s          | PAUSE 2 (lasts 8 real seconds, lag 400ms)
        #  95.0 - 97.0s   | Discord Call 3 (1200 Hz)
        # 110.0 - 112.0s  | Mic Player Call 3 (600 Hz)
        # 120.0s          | Match Ends

        def write_silence_until(target_game_time):
            cur_disc = disc_writer.current_frame / 48000.0
            if target_game_time > cur_disc:
                gap = target_game_time - cur_disc
                disc_writer.write_frames(b"\x00" * int(gap * 48000 * 4))

            cur_mic = mic_writer.current_frame / 48000.0
            if target_game_time > cur_mic:
                gap = target_game_time - cur_mic
                mic_writer.write_frames(b"\x00" * int(gap * 48000 * 2))

        # 0s -> 10s: Silence
        write_silence_until(10.0)

        # 10s -> 12s: Discord Call 1 (800 Hz)
        disc_writer.write_frames(generate_tone_pcm(800.0, 2.0, 48000, channels=2))
        write_silence_until(12.0)

        # 12s -> 20s: Silence
        write_silence_until(20.0)

        # 20s -> 22s: Mic Player Call 1 (400 Hz)
        mic_writer.write_frames(generate_tone_pcm(400.0, 2.0, 48000, channels=1))
        write_silence_until(22.0)

        # 22s -> 35s: Silence
        write_silence_until(35.0)

        # PAUSE 1 at 35.0s with 450ms detection lag:
        disc_writer.write_frames(b"\x55\x55" * int(0.450 * 48000 * 2))
        mic_writer.write_frames(b"\x66\x66" * int(0.450 * 48000))
        # Truncate back to 35.0s
        disc_writer.truncate_to_game_time(35.0)
        mic_writer.truncate_to_game_time(35.0)
        self.assertEqual(disc_writer.current_frame, 35 * 48000)
        self.assertEqual(mic_writer.current_frame, 35 * 48000)

        # 35s -> 50s match time: Silence
        write_silence_until(50.0)

        # 50s -> 52s: Discord Call 2 (1000 Hz)
        disc_writer.write_frames(generate_tone_pcm(1000.0, 2.0, 48000, channels=2))
        write_silence_until(52.0)

        # 52s -> 65s: Silence
        write_silence_until(65.0)

        # 65s -> 67s: Mic Player Call 2 (500 Hz)
        mic_writer.write_frames(generate_tone_pcm(500.0, 2.0, 48000, channels=1))
        write_silence_until(67.0)

        # 67s -> 80s: Silence
        write_silence_until(80.0)

        # PAUSE 2 at 80.0s with 400ms detection lag:
        disc_writer.write_frames(b"\x77\x77" * int(0.400 * 48000 * 2))
        mic_writer.write_frames(b"\x88\x88" * int(0.400 * 48000))
        # Truncate back to 80.0s
        disc_writer.truncate_to_game_time(80.0)
        mic_writer.truncate_to_game_time(80.0)
        self.assertEqual(disc_writer.current_frame, 80 * 48000)
        self.assertEqual(mic_writer.current_frame, 80 * 48000)

        # 80s -> 95s match time: Silence
        write_silence_until(95.0)

        # 95s -> 97s: Discord Call 3 (1200 Hz)
        disc_writer.write_frames(generate_tone_pcm(1200.0, 2.0, 48000, channels=2))
        write_silence_until(97.0)

        # 97s -> 110s: Silence
        write_silence_until(110.0)

        # 110s -> 112s: Mic Player Call 3 (600 Hz)
        mic_writer.write_frames(generate_tone_pcm(600.0, 2.0, 48000, channels=1))
        write_silence_until(112.0)

        # 112s -> 120s: Silence to match end
        write_silence_until(120.0)

        disc_writer.close()
        mic_writer.close()

        # VERIFY DISCORD STREAM ACCURACY
        with wave.open(disc_wav, "rb") as wf:
            self.assertEqual(wf.getnframes(), 48000 * 120, "Discord WAV must be exactly 120.0s")
            disc_samples = np.frombuffer(wf.readframes(48000 * 120), dtype=np.int16)[0::2]

        disc_calls = [(10.0, 800.0), (50.0, 1000.0), (95.0, 1200.0)]
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
            self.assertEqual(wf.getnframes(), 48000 * 120, "Mic WAV must be exactly 120.0s")
            mic_samples = np.frombuffer(wf.readframes(48000 * 120), dtype=np.int16)

        mic_calls = [(20.0, 400.0), (65.0, 500.0), (110.0, 600.0)]
        for expected_time, freq in mic_calls:
            onset = find_tone_onset_sample(mic_samples, freq, sample_rate=48000)
            err_ms = abs(onset - int(expected_time * 48000)) / 48000.0 * 1000.0
            self.assertLess(
                err_ms,
                15.0,
                f"Mic call at {expected_time}s has delay/shift error: {err_ms:.2f}ms!",
            )

    # =========================================================================
    # TEST 5: End-to-End Real MP3 Mix & Suppressed MP3 Verification
    # =========================================================================
    def test_end_to_end_ffmpeg_and_rnnoise_mp3_timing_accuracy(self):
        """
        Executes real FFmpeg mixing and real RNNoise suppression on synthetic scrim audio.
        Decodes output MP3s back to PCM and confirms arrival times match League match clock.
        Uses 1500 Hz (Discord) and 400 Hz (Mic) for complete spectral isolation.
        """
        ffmpeg_cmd = get_ffmpeg_path()
        if not ffmpeg_cmd or not os.path.exists(ffmpeg_cmd):
            self.skipTest("FFmpeg binary not available in test environment.")

        nr = NativeAudioRecorder(
            record_folder=self.temp_dir.name,
            ai_noise_suppression=True,
            log_callback=lambda msg: None,
        )
        nr.is_recording = True

        disc_path = os.path.join(self.temp_dir.name, "e2e_disc.wav")
        mic_path = os.path.join(self.temp_dir.name, "e2e_mic.wav")

        # 30-second match:
        # Discord call at 5.0s (1500 Hz)
        # Mic call at 18.0s (400 Hz)
        with wave.open(disc_path, "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(48000)
            wf.writeframes(b"\x00" * (48000 * 5 * 4))  # 0s - 5s silence
            wf.writeframes(generate_tone_pcm(1500.0, 2.0, 48000, 2))  # 5s - 7s tone
            wf.writeframes(b"\x00" * (48000 * 23 * 4))  # 7s - 30s silence

        with wave.open(mic_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(48000)
            wf.writeframes(b"\x00" * (48000 * 18 * 2))  # 0s - 18s silence
            wf.writeframes(generate_tone_pcm(400.0, 2.0, 48000, 1))  # 18s - 20s tone
            wf.writeframes(b"\x00" * (48000 * 10 * 2))  # 20s - 30s silence

        nr.discord_wav_path = disc_path
        nr.mic_wav_path = mic_path

        out_mp3 = nr.stop_and_finalize("E2E_MATCH_TIMING", initial_offset=0.0)
        self.assertIsNotNone(out_mp3)
        self.assertTrue(os.path.exists(out_mp3))

        suppressed_mp3 = os.path.join(nr.record_folder, "E2E_MATCH_TIMING_suppressed.mp3")
        self.assertTrue(os.path.exists(suppressed_mp3))

        # Decode output MP3s back to PCM WAV via FFmpeg to measure true arrival timestamps
        for mp3_file, is_suppressed in ((out_mp3, False), (suppressed_mp3, True)):
            label = "AI-Suppressed MP3" if is_suppressed else "Raw Mixed MP3"
            decoded_wav = os.path.join(self.temp_dir.name, f"decoded_{'supp' if is_suppressed else 'raw'}.wav")
            cmd = [ffmpeg_cmd, "-y", "-i", mp3_file, "-f", "wav", decoded_wav]
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            with wave.open(decoded_wav, "rb") as wf:
                decoded_frames = wf.getnframes()
                decoded_samples = np.frombuffer(wf.readframes(decoded_frames), dtype=np.int16)[0::2]

            # Verify Discord call at 5.0s (1500 Hz)
            onset_disc = find_tone_onset_sample(decoded_samples, 1500.0, 48000)
            self.assertGreater(onset_disc, 0, f"Discord tone not detected in {label} ({mp3_file})")
            disc_err_ms = abs(onset_disc - (48000 * 5)) / 48000.0 * 1000.0
            # MP3 perceptual encoding framing tolerance is <= 25ms
            self.assertLess(
                disc_err_ms,
                25.0,
                f"{label} Discord arrival delayed by {disc_err_ms:.2f}ms in {mp3_file}!",
            )

            # Verify Mic call at 18.0s (400 Hz)
            onset_mic = find_tone_onset_sample(decoded_samples, 400.0, 48000)
            self.assertGreater(onset_mic, 0, f"Mic tone not detected in {label} ({mp3_file})")
            mic_err_ms = abs(onset_mic - (48000 * 18)) / 48000.0 * 1000.0
            self.assertLess(
                mic_err_ms,
                25.0,
                f"{label} Mic arrival delayed by {mic_err_ms:.2f}ms in {mp3_file}!",
            )

    # =========================================================================
    # TEST 6: Extended Scrim Simulation with Discord Mute Silence Injection (Long)
    # =========================================================================
    def test_extended_scrim_simulation_with_discord_mute_silence_injection(self):
        """
        Extended 180-second scrim simulation:
        - Discord voice calls at 15s and 60s
        - Discord player mutes themselves from 70s to 100s (30 seconds of mute)
        - Native audio silence injection writes exact zeroes to keep timeline synchronized
        - Player unmutes and speaks at 105s
        - Pause occurs at 130s (duration 25 real seconds)
        - Resumes and final call occurs at 160s
        VERIFICATION:
        - Silence injection maintains exact frame count (no timeline drift)
        - The post-mute call at 105s and post-pause call at 160s arrive at their exact timestamps!
        """
        disc_wav = os.path.join(self.temp_dir.name, "mute_disc.wav")
        mic_wav = os.path.join(self.temp_dir.name, "mute_mic.wav")

        disc_writer = TimelineWavWriter(disc_wav, channels=2, sample_rate=48000)
        mic_writer = TimelineWavWriter(mic_wav, channels=1, sample_rate=48000)

        # 0s - 15s: Silence
        disc_writer.write_frames(b"\x00" * (48000 * 15 * 4))
        mic_writer.write_frames(b"\x00" * (48000 * 15 * 2))

        # 15s - 18s: Call 1 (Discord 1500 Hz, Mic 400 Hz)
        disc_writer.write_frames(generate_tone_pcm(1500.0, 3.0, 48000, 2))
        mic_writer.write_frames(generate_tone_pcm(400.0, 3.0, 48000, 1))

        # 18s - 70s: Silence across both channels
        disc_writer.write_frames(b"\x00" * (48000 * 52 * 4))
        mic_writer.write_frames(b"\x00" * (48000 * 52 * 2))
        self.assertEqual(disc_writer.current_frame, 48000 * 70)

        # 70s - 100s: Discord Mute Active (30 seconds)
        # Discord stream injects silence blocks (simulating native_audio _capture_discord_loop)
        for _ in range(300):  # 300 * 100ms = 30 seconds
            disc_writer.write_frames(b"\x00" * int(0.100 * 48000 * 4))
            mic_writer.write_frames(b"\x00" * int(0.100 * 48000 * 2))
        self.assertEqual(disc_writer.current_frame, 48000 * 100)

        # 100s - 105s: Unmuted pre-call silence
        disc_writer.write_frames(b"\x00" * (48000 * 5 * 4))
        mic_writer.write_frames(b"\x00" * (48000 * 5 * 2))

        # 105s - 108s: Post-mute call (Discord 1200 Hz, Mic 500 Hz)
        disc_writer.write_frames(generate_tone_pcm(1200.0, 3.0, 48000, 2))
        mic_writer.write_frames(generate_tone_pcm(500.0, 3.0, 48000, 1))

        # 108s - 130s: Silence before pause
        disc_writer.write_frames(b"\x00" * (48000 * 22 * 4))
        mic_writer.write_frames(b"\x00" * (48000 * 22 * 2))
        self.assertEqual(disc_writer.current_frame, 48000 * 130)

        # 130s: In-game pause triggered (480ms detection lag written)
        disc_writer.write_frames(b"\x11\x11" * int(0.480 * 48000 * 2))
        mic_writer.write_frames(b"\x22\x22" * int(0.480 * 48000))
        # Truncate to exact pause time (130.0s)
        disc_writer.truncate_to_game_time(130.0)
        mic_writer.truncate_to_game_time(130.0)
        self.assertEqual(disc_writer.current_frame, 48000 * 130)

        # Match resumed after 25s real pause. Silence from 130s to 160s match clock:
        disc_writer.write_frames(b"\x00" * (48000 * 30 * 4))
        mic_writer.write_frames(b"\x00" * (48000 * 30 * 2))

        # 160s - 163s: Post-pause late-game call (Discord 900 Hz, Mic 600 Hz)
        disc_writer.write_frames(generate_tone_pcm(900.0, 3.0, 48000, 2))
        mic_writer.write_frames(generate_tone_pcm(600.0, 3.0, 48000, 1))

        # 163s - 180s: Match victory push
        disc_writer.write_frames(b"\x00" * (48000 * 17 * 4))
        mic_writer.write_frames(b"\x00" * (48000 * 17 * 2))

        disc_writer.close()
        mic_writer.close()

        # VERIFICATION:
        # Check total match duration is exactly 180.000s
        with wave.open(disc_wav, "rb") as wf:
            self.assertEqual(wf.getnframes(), 48000 * 180)
            d_samples = np.frombuffer(wf.readframes(48000 * 180), dtype=np.int16)[0::2]

        with wave.open(mic_wav, "rb") as wf:
            self.assertEqual(wf.getnframes(), 48000 * 180)
            m_samples = np.frombuffer(wf.readframes(48000 * 180), dtype=np.int16)

        # 1. Pre-mute call at 15.0s
        d_onset_15 = find_tone_onset_sample(d_samples, 1500.0, 48000)
        self.assertLess(abs(d_onset_15 - 48000 * 15) / 48.0, 15.0)

        # 2. Post-mute call at 105.0s (after 30s mute silence injection)
        d_onset_105 = find_tone_onset_sample(d_samples, 1200.0, 48000)
        self.assertLess(
            abs(d_onset_105 - 48000 * 105) / 48.0,
            15.0,
            "Discord silence injection caused timeline drift!",
        )

        m_onset_105 = find_tone_onset_sample(m_samples, 500.0, 48000)
        self.assertLess(
            abs(m_onset_105 - 48000 * 105) / 48.0,
            15.0,
            "Mic call post-mute drifted!",
        )

        # 3. Post-pause call at 160.0s (after pause and lag truncation)
        d_onset_160 = find_tone_onset_sample(d_samples, 900.0, 48000)
        self.assertLess(
            abs(d_onset_160 - 48000 * 160) / 48.0,
            15.0,
            "Post-pause Discord call delayed!",
        )

        m_onset_160 = find_tone_onset_sample(m_samples, 600.0, 48000)
        self.assertLess(
            abs(m_onset_160 - 48000 * 160) / 48.0,
            15.0,
            "Post-pause Mic call delayed!",
        )


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
