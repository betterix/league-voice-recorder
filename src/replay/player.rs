use std::fs::File;
use std::io::BufReader;
use std::path::Path;
use std::time::Duration;
use rodio::{Decoder, DeviceSinkBuilder, MixerDeviceSink, Player};

pub struct RodioPlayer {
    sink_handle: MixerDeviceSink,
    player: Player,
    volume: f32,
}

impl RodioPlayer {
    pub fn try_new() -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        let sink_handle = DeviceSinkBuilder::open_default_sink()
            .map_err(|e| format!("Failed to open default audio sink: {:?}", e))?;
        let player = Player::connect_new(&sink_handle.mixer());
        Ok(Self {
            sink_handle,
            player,
            volume: 1.0,
        })
    }

    pub fn load_file(&mut self, path: &Path) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        let file = File::open(path)?;
        let reader = BufReader::new(file);
        let source = Decoder::new(reader)?;

        self.player = Player::connect_new(&self.sink_handle.mixer());
        self.player.set_volume(self.volume);
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

    pub fn set_speed(&self, speed: f32) {
        self.player.set_speed(speed);
    }

    pub fn set_volume(&mut self, volume: f32) {
        self.volume = volume.clamp(0.0, 1.0);
        self.player.set_volume(self.volume);
    }

    pub fn seek(&self, position: Duration) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        let _ = self.player.try_seek(position);
        Ok(())
    }

    pub fn stop(&self) {
        self.player.stop();
    }
}
