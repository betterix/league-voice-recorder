use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tokio::time::{sleep, Duration};
use tracing::info;

use crate::config::AppConfig;
use crate::replay::{ReplayApiClient, RoflProcessDetector};

#[allow(dead_code)]
pub struct ReplaySynchronizer {
    config: AppConfig,
    api_client: ReplayApiClient,
}

impl ReplaySynchronizer {
    pub fn new(config: AppConfig) -> Self {
        Self {
            config,
            api_client: ReplayApiClient::default(),
        }
    }

    pub async fn run_sync_watcher(&self, stop_signal: Arc<AtomicBool>) {
        info!("Starting ReplaySynchronizer watcher loop...");

        while !stop_signal.load(Ordering::Relaxed) {
            if let Some((game_id, rofl_path)) = RoflProcessDetector::detect_running_rofl() {
                info!("Active replay detected: Game ID {} ({:?})", game_id, rofl_path);
                
                // Locate matching <GameID>.mp3
                let target_audio = self.config.record_folder.join(format!("{}.mp3", game_id));
                if target_audio.exists() {
                    info!("Matching scrim audio found: {:?}", target_audio);
                    // Sync loop runs here polling api_client.get_playback_state() every 50ms
                }
            }

            sleep(Duration::from_millis(1000)).await;
        }

        info!("ReplaySynchronizer watcher loop stopped.");
    }
}
