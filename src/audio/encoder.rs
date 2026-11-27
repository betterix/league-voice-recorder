use std::collections::BTreeMap;
use std::fs::File;
use std::io::{BufReader, BufWriter, Read, Seek, SeekFrom, Write};
use std::path::Path;
use std::sync::{mpsc, Arc, Mutex};
use rusty_mp3::{Mp3Encoder, Mp3EncoderConfig};

const MPEG1_SAMPLES_PER_FRAME: usize = 1152;
const SEGMENT_MP3_FRAMES: usize = 500; // 500 * 1152 = 576,000 samples (12.0s @ 48kHz)
const WARMUP_MP3_FRAMES: usize = 2; // 2 frames (48ms @ 48kHz) overlap for seamless MDCT boundary

/// Returns the byte length of an MPEG-1 Layer III frame from its 4-byte header.
fn mpeg1_layer3_frame_len(hdr: [u8; 4]) -> Option<usize> {
    // Sync word: 11 bits of 1s (0xFF, top 3 bits 0xE0)
    if hdr[0] != 0xFF || (hdr[1] & 0xE0) != 0xE0 {
        return None;
    }
    let version_bits = (hdr[1] >> 3) & 0x03; // 3 = MPEG-1
    let layer_bits = (hdr[1] >> 1) & 0x03;   // 1 = Layer III
    if version_bits != 3 || layer_bits != 1 {
        return None;
    }
    let bitrate_idx = ((hdr[2] >> 4) & 0x0F) as usize;
    let sr_idx = ((hdr[2] >> 2) & 0x03) as usize;
    let padding = ((hdr[2] >> 1) & 0x01) as usize;

    const BITRATES: [usize; 16] = [
        0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0,
    ];
    const SAMPLE_RATES: [usize; 4] = [44100, 48000, 32000, 0];

    let bitrate = BITRATES.get(bitrate_idx).copied().unwrap_or(0) * 1000;
    let sr = SAMPLE_RATES.get(sr_idx).copied().unwrap_or(0);
    if bitrate == 0 || sr == 0 {
        return None;
    }
    Some((144 * bitrate) / sr + padding)
}

/// Encodes a PCM s16le segment and slices `[skip_frames .. skip_frames + keep_frames]` MP3 frames.
fn encode_pcm_segment(
    pcm_s16le: &[u8],
    channels: u16,
    sample_rate: u32,
    skip_frames: usize,
    keep_frames: Option<usize>,
) -> Vec<u8> {
    let mut config = Mp3EncoderConfig::default();
    config.bitrate_kbps = 160;
    let mut encoder = Mp3Encoder::new(config);

    let mut raw_mp3 = Vec::with_capacity(pcm_s16le.len() / 8 + 4096);
    let _ = encoder.push_pcm_s16le(pcm_s16le, channels, sample_rate);
    while let Ok(packet) = encoder.next_packet() {
        if packet.is_empty() {
            break;
        }
        raw_mp3.extend_from_slice(&packet);
    }
    encoder.finish();
    while let Ok(packet) = encoder.next_packet() {
        if packet.is_empty() {
            break;
        }
        raw_mp3.extend_from_slice(&packet);
    }

    if skip_frames == 0 && keep_frames.is_none() {
        return raw_mp3;
    }

    // Slice exact MP3 frame range to eliminate warmup/encoder-delay drift
    let mut out = Vec::with_capacity(raw_mp3.len());
    let mut pos = 0usize;
    let mut frame_idx = 0usize;
    while pos + 4 <= raw_mp3.len() {
        let hdr = [raw_mp3[pos], raw_mp3[pos + 1], raw_mp3[pos + 2], raw_mp3[pos + 3]];
        let Some(flen) = mpeg1_layer3_frame_len(hdr) else {
            pos += 1;
            continue;
        };
        if pos + flen > raw_mp3.len() {
            break;
        }
        if frame_idx >= skip_frames {
            if let Some(max_keep) = keep_frames {
                if frame_idx - skip_frames >= max_keep {
                    break;
                }
            }
            out.extend_from_slice(&raw_mp3[pos..pos + flen]);
        }
        frame_idx += 1;
        pos += flen;
    }
    out
}

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

    /// Streams and mixes two raw PCM audio files from disk in bounded segments,
    /// applies sample-accurate match clock offset (prepend silence or trim),
    /// and encodes across all available CPU cores without loading the full match into RAM.
    pub fn finalize_from_files(
        discord_path: &Path,
        mic_path: &Path,
        sample_rate: u32,
        channels: u16,
        first_game_time: f64,
        output_path: &Path,
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        let result = (|| -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
            let mut disc_reader = BufReader::with_capacity(512 * 1024, File::open(discord_path)?);
            let mut mic_reader = BufReader::with_capacity(512 * 1024, File::open(mic_path)?);
            let mut out_file = BufWriter::with_capacity(256 * 1024, File::create(output_path)?);

            let bytes_per_frame = (channels as usize) * 2;
            let segment_bytes = SEGMENT_MP3_FRAMES * MPEG1_SAMPLES_PER_FRAME * bytes_per_frame;
            let warmup_bytes = WARMUP_MP3_FRAMES * MPEG1_SAMPLES_PER_FRAME * bytes_per_frame;

            let mut silence_remaining_bytes: usize = if first_game_time > 0.0 {
                let silence_frames = (first_game_time * sample_rate as f64).round() as usize;
                silence_frames * bytes_per_frame
            } else {
                0
            };

            if first_game_time < 0.0 {
                let trim_frames = (first_game_time.abs() * sample_rate as f64).round() as u64;
                let trim_bytes = trim_frames * bytes_per_frame as u64;
                let disc_len = disc_reader.get_ref().metadata().map(|m| m.len()).unwrap_or(0);
                let mic_len = mic_reader.get_ref().metadata().map(|m| m.len()).unwrap_or(0);
                let _ = disc_reader.seek(SeekFrom::Start(trim_bytes.min(disc_len)));
                let _ = mic_reader.seek(SeekFrom::Start(trim_bytes.min(mic_len)));
            }

            struct SegmentJob {
                idx: usize,
                pcm: Vec<u8>,
                skip_frames: usize,
                keep_frames: Option<usize>,
            }

            let num_workers = std::thread::available_parallelism()
                .map(|n| n.get())
                .unwrap_or(4)
                .clamp(2, 16);

            let (job_tx, job_rx) = mpsc::sync_channel::<SegmentJob>(num_workers);
            let job_rx = Arc::new(Mutex::new(job_rx));
            let (out_tx, out_rx) = mpsc::channel::<(usize, Vec<u8>)>();

            let read_exact_or_eof = |reader: &mut BufReader<File>, buf: &mut [u8]| -> std::io::Result<usize> {
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

            let mut producer_err: Option<std::io::Error> = None;

            std::thread::scope(|scope| {
                // Spawn worker threads utilizing all CPU cores for parallel MP3 encoding
                for _ in 0..num_workers {
                    let rx = Arc::clone(&job_rx);
                    let tx = out_tx.clone();
                    scope.spawn(move || loop {
                        let job = {
                            let Ok(guard) = rx.lock() else { break };
                            guard.recv()
                        };
                        let Ok(job) = job else { break };
                        let encoded = encode_pcm_segment(
                            &job.pcm,
                            channels,
                            sample_rate,
                            job.skip_frames,
                            job.keep_frames,
                        );
                        if tx.send((job.idx, encoded)).is_err() {
                            break;
                        }
                    });
                }
                drop(out_tx);

                // Producer thread: stream & mix raw PCM from disk in bounded segments
                let err_slot = &mut producer_err;
                scope.spawn(move || {
                    let mut disc_buf = vec![0u8; segment_bytes];
                    let mut mic_buf = vec![0u8; segment_bytes];
                    let mut prev_tail: Vec<u8> = Vec::new();
                    let mut seg_idx: usize = 0;

                    loop {
                        let mut seg_pcm = Vec::with_capacity(prev_tail.len() + segment_bytes);
                        if !prev_tail.is_empty() {
                            seg_pcm.extend_from_slice(&prev_tail);
                        }

                        let mut filled = 0usize;
                        if silence_remaining_bytes > 0 {
                            let s_bytes = silence_remaining_bytes.min(segment_bytes);
                            seg_pcm.resize(seg_pcm.len() + s_bytes, 0);
                            silence_remaining_bytes -= s_bytes;
                            filled += s_bytes;
                        }

                        let mut reached_eof = false;
                        while filled < segment_bytes {
                            let rem = segment_bytes - filled;
                            let n_disc = match read_exact_or_eof(&mut disc_reader, &mut disc_buf[..rem]) {
                                Ok(n) => n,
                                Err(e) => {
                                    *err_slot = Some(e);
                                    return;
                                }
                            };
                            let n_mic = match read_exact_or_eof(&mut mic_reader, &mut mic_buf[..rem]) {
                                Ok(n) => n,
                                Err(e) => {
                                    *err_slot = Some(e);
                                    return;
                                }
                            };
                            if n_disc == 0 && n_mic == 0 {
                                reached_eof = true;
                                break;
                            }

                            let max_bytes = n_disc.max(n_mic);
                            let samples = max_bytes / 2;
                            let base_offset = seg_pcm.len();
                            seg_pcm.resize(base_offset + max_bytes, 0);

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
                                seg_pcm[base_offset + i * 2] = b[0];
                                seg_pcm[base_offset + i * 2 + 1] = b[1];
                            }

                            filled += max_bytes;
                            if max_bytes < rem {
                                reached_eof = true;
                                break;
                            }
                        }

                        if filled == 0 {
                            break;
                        }

                        let next_tail = if !reached_eof && seg_pcm.len() >= warmup_bytes {
                            seg_pcm[seg_pcm.len() - warmup_bytes..].to_vec()
                        } else {
                            Vec::new()
                        };

                        let skip_frames = if seg_idx == 0 { 0 } else { WARMUP_MP3_FRAMES };
                        let keep_frames = if reached_eof { None } else { Some(SEGMENT_MP3_FRAMES) };

                        if job_tx
                            .send(SegmentJob {
                                idx: seg_idx,
                                pcm: seg_pcm,
                                skip_frames,
                                keep_frames,
                            })
                            .is_err()
                        {
                            break;
                        }

                        prev_tail = next_tail;
                        seg_idx += 1;
                        if reached_eof {
                            break;
                        }
                    }
                    drop(job_tx);
                });

                // Writer (current thread): re-order encoded segments and flush to disk
                let mut pending: BTreeMap<usize, Vec<u8>> = BTreeMap::new();
                let mut next_idx: usize = 0;
                for (idx, data) in out_rx {
                    pending.insert(idx, data);
                    while let Some(buf) = pending.remove(&next_idx) {
                        if let Err(e) = out_file.write_all(&buf) {
                            let _ = e;
                            break;
                        }
                        next_idx += 1;
                    }
                }
            });

            if let Some(e) = producer_err {
                return Err(Box::new(e));
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
