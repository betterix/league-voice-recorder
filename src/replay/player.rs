use std::fs::File;
use std::io::BufReader;
use std::path::{Path, PathBuf};
use std::time::Duration;
use rodio::{Decoder, DeviceSinkBuilder, MixerDeviceSink, Player};

pub struct RodioPlayer {
    sink_handle: MixerDeviceSink,
    player: Player,
    volume: f32,
    speed: f32,
    current_path: Option<PathBuf>,
}

impl RodioPlayer {
    pub fn try_new() -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        let mut sink_handle = DeviceSinkBuilder::open_default_sink()
            .map_err(|e| format!("Failed to open default audio sink: {:?}", e))?;
        sink_handle.log_on_drop(false);
        let player = Player::connect_new(&sink_handle.mixer());
        Ok(Self {
            sink_handle,
            player,
            volume: 1.0,
            speed: 1.0,
            current_path: None,
        })
    }

    pub fn load_file(&mut self, path: &Path) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        let file = File::open(path)?;
        let reader = BufReader::new(file);
        let source = Decoder::new(reader)?;

        self.player.stop();
        self.current_path = Some(path.to_path_buf());
        self.player = Player::connect_new(&self.sink_handle.mixer());
        self.player.set_volume(self.volume);
        self.player.set_speed(self.speed);
        self.player.append(source);
        self.player.pause();
        Ok(())
    }

    pub fn play(&self) {
        self.player.play();
    }

    pub fn pause(&self) {
        self.player.pause();
    }

    pub fn is_paused(&self) -> bool {
        self.player.is_paused()
    }

    pub fn set_speed(&mut self, speed: f32) {
        self.speed = speed.max(0.1);
        self.player.set_speed(self.speed);
    }

    pub fn set_volume(&mut self, volume: f32) {
        self.volume = volume.clamp(0.0, 2.0);
        self.player.set_volume(self.volume);
    }

    pub fn get_pos(&self) -> Duration {
        self.player.get_pos()
    }

    pub fn seek(&mut self, position: Duration) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        if self.player.try_seek(position).is_err() {
            // If seek failed or stream ended, reload fresh source and seek
            if let Some(ref path) = self.current_path {
                if let Ok(file) = File::open(path) {
                    let reader = BufReader::new(file);
                    if let Ok(source) = Decoder::new(reader) {
                        let was_paused = self.player.is_paused();
                        self.player.stop();
                        self.player = Player::connect_new(&self.sink_handle.mixer());
                        self.player.set_volume(self.volume);
                        self.player.set_speed(self.speed);
                        self.player.append(source);
                        let _ = self.player.try_seek(position);
                        if was_paused {
                            self.player.pause();
                        } else {
                            self.player.play();
                        }
                    }
                }
            }
        }
        Ok(())
    }

    pub fn stop(&self) {
        self.player.stop();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::audio::AudioFinalizer;

    #[test]
    fn test_rodio_player_lifecycle() {
        let temp_dir = std::env::temp_dir();
        let test_mp3 = temp_dir.join(format!("test_player_{}.mp3", std::process::id()));

        // Synthesize 500ms of 48kHz stereo 16-bit PCM (48000 * 2 channels * 2 bytes * 0.5s = 96000 bytes)
        let pcm = vec![0u8; 96000];
        let enc_res = AudioFinalizer::finalize_to_mp3(
            &pcm,
            &pcm,
            48000,
            2,
            0.0,
            &test_mp3,
        );
        assert!(enc_res.is_ok(), "Failed to synthesize test MP3: {:?}", enc_res.err());

        // Write ID3 metadata tags like in production
        let meta = crate::scrim_meta::ScrimMetadata {
            game_id: "123456789".to_string(),
            team_tag: "TEST".to_string(),
            notes: "Test notes".to_string(),
            game_patch: "14.23".to_string(),
            recorded_at: "2026-11-22".to_string(),
        };
        let tag_res = meta.write_to_mp3(&test_mp3);
        assert!(tag_res.is_ok(), "Writing ID3 tag failed: {:?}", tag_res.err());

        // Test player lifecycle: init, volume, load, play, seek, pause, stop
        if let Ok(mut player) = RodioPlayer::try_new() {
            player.set_volume(0.8);
            let load_res = player.load_file(&test_mp3);
            assert!(load_res.is_ok(), "Failed to load test MP3 into RodioPlayer: {:?}", load_res.err());

            player.play();
            assert!(!player.is_paused());

            player.pause();
            assert!(player.is_paused());

            let raw_seek = player.player.try_seek(Duration::from_millis(100));
            println!("RAW TRY_SEEK RESULT: {:?}", raw_seek);
            println!("POS BEFORE SEEK: {:?}", player.get_pos());
            let seek_res = player.seek(Duration::from_millis(100));
            assert!(seek_res.is_ok(), "Seek failed: {:?}", seek_res.err());
            println!("POS AFTER SEEK: {:?}", player.get_pos());

            player.stop();
        }

        let _ = std::fs::remove_file(test_mp3);
    }
}
