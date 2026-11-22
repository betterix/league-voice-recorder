use std::fs::{File, OpenOptions};
use std::io::{Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

pub struct TimelineWavWriter {
    path: PathBuf,
    sample_rate: u32,
    channels: u16,
    bytes_per_sample: u16,
    file: Option<File>,
    total_samples: u64,
}

impl TimelineWavWriter {
    pub fn new<P: AsRef<Path>>(path: P, sample_rate: u32, channels: u16, bytes_per_sample: u16) -> Self {
        Self {
            path: path.as_ref().to_path_buf(),
            sample_rate,
            channels,
            bytes_per_sample,
            file: None,
            total_samples: 0,
        }
    }

    pub fn start(&mut self) -> std::io::Result<()> {
        let mut f = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(true)
            .open(&self.path)?;

        // Write initial dummy 44-byte WAV header
        let dummy_header = [0u8; 44];
        f.write_all(&dummy_header)?;
        self.file = Some(f);
        self.total_samples = 0;
        Ok(())
    }

    pub fn write_frames(&mut self, data: &[u8]) -> std::io::Result<()> {
        if let Some(f) = &mut self.file {
            f.write_all(data)?;
            let frame_size = (self.channels * self.bytes_per_sample) as u64;
            if frame_size > 0 {
                self.total_samples += data.len() as u64 / frame_size;
            }
        }
        Ok(())
    }

    /// Truncates the WAV audio in-place back to the exact game time when pause occurred,
    /// permanently slicing out API polling detection lag.
    pub fn truncate_to_game_time(&mut self, pause_game_time: f64) -> std::io::Result<()> {
        if pause_game_time <= 0.0 {
            return Ok(());
        }

        let target_frame = (pause_game_time * self.sample_rate as f64).round() as u64;
        let frame_size = (self.channels * self.bytes_per_sample) as u64;
        let target_byte_offset = 44 + target_frame * frame_size;

        if let Some(f) = &mut self.file {
            let current_len = f.metadata()?.len();
            if target_byte_offset < current_len {
                f.set_len(target_byte_offset)?;
                f.seek(SeekFrom::Start(target_byte_offset))?;
                self.total_samples = target_frame;
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_pause_lag_truncation() {
        let temp_wav = std::env::temp_dir().join("test_timeline_truncation.wav");
        let sample_rate = 48000;
        let channels = 2;
        let bytes_per_sample = 2; // 16-bit PCM (frame size = 4 bytes)
        let frame_size = (channels * bytes_per_sample) as usize;

        let mut writer = TimelineWavWriter::new(&temp_wav, sample_rate, channels, bytes_per_sample);
        writer.start().unwrap();

        // Write 1.5 seconds of simulated audio (48000 * 1.5 = 72000 frames = 288000 bytes)
        let dummy_audio = vec![0xABu8; 72000 * frame_size];
        writer.write_frames(&dummy_audio).unwrap();

        let initial_len = std::fs::metadata(&temp_wav).unwrap().len();
        assert_eq!(initial_len, 44 + (72000 * frame_size) as u64);

        // Simulate pause detected at game_time = 1.0s (slicing out the 0.5s of detection lag)
        writer.truncate_to_game_time(1.0).unwrap();

        let truncated_len = std::fs::metadata(&temp_wav).unwrap().len();
        let expected_len = 44 + (48000 * frame_size) as u64;
        assert_eq!(truncated_len, expected_len);

        let _ = std::fs::remove_file(&temp_wav);
    }
}
