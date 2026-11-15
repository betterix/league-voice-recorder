use std::fs::File;
use std::io::Write;
use std::path::Path;
use rusty_mp3::{Mp3Encoder, Mp3EncoderConfig};

pub struct AudioFinalizer;

impl AudioFinalizer {
    /// Mixes two 48kHz 16-bit stereo PCM streams with sample-accurate game time offset
    /// and encodes directly to an MP3 file in pure Rust without FFmpeg.
    pub fn finalize_to_mp3(
        discord_pcm: &[u8],
        mic_pcm: &[u8],
        sample_rate: u32,
        channels: u16,
        first_game_time: f64,
        output_path: &Path,
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        let bytes_per_sample = 2usize; // 16-bit PCM

        // Parse samples as signed 16-bit integers
        let parse_samples = |raw: &[u8]| -> Vec<i16> {
            raw.chunks_exact(2)
                .map(|b| i16::from_le_bytes([b[0], b[1]]))
                .collect()
        };

        let mut discord_samples = parse_samples(discord_pcm);
        let mut mic_samples = parse_samples(mic_pcm);

        // Account for match clock offset
        if first_game_time > 0.0 {
            // Match started after 00:00: prepend silence
            let silence_frames = (first_game_time * sample_rate as f64) as usize;
            let silence_samples = silence_frames * (channels as usize);
            let mut prepended = vec![0i16; silence_samples];
            prepended.extend(discord_samples);
            discord_samples = prepended;

            let mut prepended_mic = vec![0i16; silence_samples];
            prepended_mic.extend(mic_samples);
            mic_samples = prepended_mic;
        } else if first_game_time < 0.0 {
            // Match recorded before 00:00 (countdown): trim pre-game audio
            let trim_frames = (first_game_time.abs() * sample_rate as f64) as usize;
            let trim_samples = trim_frames * (channels as usize);
            if discord_samples.len() > trim_samples {
                discord_samples = discord_samples[trim_samples..].to_vec();
            } else {
                discord_samples.clear();
            }
            if mic_samples.len() > trim_samples {
                mic_samples = mic_samples[trim_samples..].to_vec();
            } else {
                mic_samples.clear();
            }
        }

        // Mix both streams (sample-by-sample with clamping)
        let total_samples = discord_samples.len().max(mic_samples.len());
        let mut mixed_s16le: Vec<u8> = Vec::with_capacity(total_samples * bytes_per_sample);

        for i in 0..total_samples {
            let s_disc = discord_samples.get(i).copied().unwrap_or(0) as i32;
            let s_mic = mic_samples.get(i).copied().unwrap_or(0) as i32;
            let mixed = (s_disc + s_mic).clamp(i16::MIN as i32, i16::MAX as i32) as i16;
            mixed_s16le.extend_from_slice(&mixed.to_le_bytes());
        }

        // Configure pure-Rust MP3 encoder
        let mut config = Mp3EncoderConfig::default();
        config.bitrate_kbps = 160;

        let mut encoder = Mp3Encoder::new(config);
        let _ = encoder.push_pcm_s16le(&mixed_s16le, channels, sample_rate);

        let mut out_file = File::create(output_path)?;

        // Pull encoded MP3 packets
        while let Ok(packet) = encoder.next_packet() {
            if packet.is_empty() {
                break;
            }
            out_file.write_all(&packet)?;
        }

        encoder.finish();

        while let Ok(packet) = encoder.next_packet() {
            if packet.is_empty() {
                break;
            }
            out_file.write_all(&packet)?;
        }

        out_file.flush()?;
        Ok(())
    }
}
