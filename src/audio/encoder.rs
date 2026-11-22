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

    /// Streams and mixes two raw PCM audio files from disk in small 16KB buffers,
    /// applies sample-accurate match clock offset (prepend silence or trim),
    /// and writes the final MP3 without loading the audio into RAM.
    pub fn finalize_from_files(
        discord_path: &Path,
        mic_path: &Path,
        sample_rate: u32,
        channels: u16,
        first_game_time: f64,
        output_path: &Path,
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        use std::io::{BufReader, Read, Seek, SeekFrom};

        let result = (|| -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
            let mut disc_reader = BufReader::new(File::open(discord_path)?);
            let mut mic_reader = BufReader::new(File::open(mic_path)?);

            let mut config = Mp3EncoderConfig::default();
            config.bitrate_kbps = 160;
            let mut encoder = Mp3Encoder::new(config);

            let mut out_file = File::create(output_path)?;

            // 1. Account for match clock offset
            if first_game_time > 0.0 {
                // Match started after 00:00: stream exact silence frames
                let silence_frames = (first_game_time * sample_rate as f64).round() as usize;
                let silence_samples = silence_frames * channels as usize;
                let mut silence_remaining = silence_samples;
                let silence_chunk = vec![0u8; 8192 * 4];
                while silence_remaining > 0 {
                    let to_write = silence_remaining.min(8192 * channels as usize);
                    let chunk = &silence_chunk[..to_write * 2];
                    let _ = encoder.push_pcm_s16le(chunk, channels, sample_rate);
                    while let Ok(packet) = encoder.next_packet() {
                        if packet.is_empty() {
                            break;
                        }
                        out_file.write_all(&packet)?;
                    }
                    silence_remaining -= to_write;
                }
            } else if first_game_time < 0.0 {
                // Match recorded before 00:00 (countdown): trim pre-game audio on disk
                let trim_frames = (first_game_time.abs() * sample_rate as f64).round() as u64;
                let trim_bytes = trim_frames * channels as u64 * 2;
                let disc_len = disc_reader.get_ref().metadata().map(|m| m.len()).unwrap_or(0);
                let mic_len = mic_reader.get_ref().metadata().map(|m| m.len()).unwrap_or(0);
                let _ = disc_reader.seek(SeekFrom::Start(trim_bytes.min(disc_len)));
                let _ = mic_reader.seek(SeekFrom::Start(trim_bytes.min(mic_len)));
            }

            // Helper to fill buffer up to chunk size or EOF on 16-bit PCM sample boundary
            let read_chunk = |reader: &mut BufReader<File>, buf: &mut [u8]| -> std::io::Result<usize> {
                let mut total = 0;
                while total < buf.len() {
                    match reader.read(&mut buf[total..]) {
                        Ok(0) => break,
                        Ok(n) => total += n,
                        Err(ref e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
                        Err(e) => return Err(e),
                    }
                }
                total -= total % 2;
                Ok(total)
            };

            // 2. Stream mix both files in small 16KB blocks
            let mut disc_buf = [0u8; 16384];
            let mut mic_buf = [0u8; 16384];
            let mut mixed_buf = [0u8; 16384];

            loop {
                let n_disc = read_chunk(&mut disc_reader, &mut disc_buf)?;
                let n_mic = read_chunk(&mut mic_reader, &mut mic_buf)?;
                if n_disc == 0 && n_mic == 0 {
                    break;
                }

                let max_bytes = n_disc.max(n_mic);
                let samples = max_bytes / 2;

                for i in 0..samples {
                    let d = if i * 2 + 1 < n_disc {
                        i16::from_le_bytes([disc_buf[i * 2], disc_buf[i * 2 + 1]]) as i32
                    } else {
                        0
                    };
                    let m = if i * 2 + 1 < n_mic {
                        i16::from_le_bytes([mic_buf[i * 2], mic_buf[i * 2 + 1]]) as i32
                    } else {
                        0
                    };
                    let mixed = (d + m).clamp(i16::MIN as i32, i16::MAX as i32) as i16;
                    let b = mixed.to_le_bytes();
                    mixed_buf[i * 2] = b[0];
                    mixed_buf[i * 2 + 1] = b[1];
                }

                let _ = encoder.push_pcm_s16le(&mixed_buf[..max_bytes], channels, sample_rate);
                while let Ok(packet) = encoder.next_packet() {
                    if packet.is_empty() {
                        break;
                    }
                    out_file.write_all(&packet)?;
                }
            }

            // 3. Flush encoder
            encoder.finish();
            while let Ok(packet) = encoder.next_packet() {
                if packet.is_empty() {
                    break;
                }
                out_file.write_all(&packet)?;
            }
            out_file.flush()?;

            Ok(())
        })();

        // Always clean up temp files from disk
        let _ = std::fs::remove_file(discord_path);
        let _ = std::fs::remove_file(mic_path);

        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_finalize_creates_valid_mp3() {
        let temp_dir = std::env::temp_dir();
        let out_path = temp_dir.join("test_output.mp3");

        // 1 second of 48kHz stereo 16-bit PCM (48000 * 2 channels * 2 bytes = 192000 bytes)
        let mut pcm = vec![0u8; 48000 * 4];
        // Generate a 440Hz sine wave tone
        for i in 0..48000 {
            let t = i as f64 / 48000.0;
            let sample = (f64::sin(2.0 * std::f64::consts::PI * 440.0 * t) * 10000.0) as i16;
            let bytes = sample.to_le_bytes();
            pcm[i * 4] = bytes[0];
            pcm[i * 4 + 1] = bytes[1];
            pcm[i * 4 + 2] = bytes[0];
            pcm[i * 4 + 3] = bytes[1];
        }

        let res = AudioFinalizer::finalize_to_mp3(
            &pcm,
            &pcm,
            48000,
            2,
            0.0,
            &out_path,
        );

        assert!(res.is_ok(), "Encoding failed: {:?}", res.err());
        assert!(out_path.exists(), "MP3 file was not created");
        let size = std::fs::metadata(&out_path).unwrap().len();
        assert!(size > 5000, "MP3 file is too small: {} bytes", size);
        let _ = std::fs::remove_file(out_path);
    }

    #[test]
    fn test_finalize_from_files_creates_valid_mp3() {
        let temp_dir = std::env::temp_dir();
        let loop_raw = temp_dir.join("test_loop_stream.raw");
        let mic_raw = temp_dir.join("test_mic_stream.raw");
        let out_mp3 = temp_dir.join("test_file_output.mp3");

        // 1 second 48kHz stereo 16-bit PCM (48000 * 2 channels * 2 bytes = 192000 bytes)
        let mut pcm = vec![0u8; 48000 * 4];
        for i in 0..48000 {
            let t = i as f64 / 48000.0;
            let sample = (f64::sin(2.0 * std::f64::consts::PI * 440.0 * t) * 8000.0) as i16;
            let bytes = sample.to_le_bytes();
            pcm[i * 4] = bytes[0];
            pcm[i * 4 + 1] = bytes[1];
            pcm[i * 4 + 2] = bytes[0];
            pcm[i * 4 + 3] = bytes[1];
        }

        std::fs::write(&loop_raw, &pcm).unwrap();
        std::fs::write(&mic_raw, &pcm).unwrap();

        let res = AudioFinalizer::finalize_from_files(
            &loop_raw,
            &mic_raw,
            48000,
            2,
            0.25, // 250ms offset: prepends silence
            &out_mp3,
        );

        assert!(res.is_ok(), "Streaming from files failed: {:?}", res.err());
        assert!(out_mp3.exists(), "MP3 output must exist");
        assert!(!loop_raw.exists(), "Temp raw file should be cleaned up");
        assert!(!mic_raw.exists(), "Temp raw file should be cleaned up");

        let size = std::fs::metadata(&out_mp3).unwrap().len();
        assert!(size > 5000, "MP3 file too small: {} bytes", size);
        let _ = std::fs::remove_file(out_mp3);
    }
}
