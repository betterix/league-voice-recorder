use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tokio::time::{sleep, Duration};
use tracing::info;

use crate::config::AppConfig;
use crate::lcu::LiveApiClient;

#[allow(dead_code)]
pub struct LeagueRecorder {
    config: AppConfig,
    is_recording: Arc<AtomicBool>,
    live_client: LiveApiClient,
}

impl LeagueRecorder {
    pub fn new(config: AppConfig) -> Self {
        Self {
            config,
            is_recording: Arc::new(AtomicBool::new(false)),
            live_client: LiveApiClient::default(),
        }
    }

    pub fn is_recording(&self) -> bool {
        self.is_recording.load(Ordering::SeqCst)
    }

    pub async fn run_watcher_loop(&self, stop_signal: Arc<AtomicBool>) {
        info!("Starting LeagueRecorder watcher loop...");

        while !stop_signal.load(Ordering::Relaxed) {
            match self.live_client.poll_all_game_data().await {
                Ok(data) => {
                    if let Some(game_data) = data.game_data {
                        if !self.is_recording() {
                            info!("Match detected at game_time = {:.3}. Starting recording...", game_data.game_time);
                            self.is_recording.store(true, Ordering::SeqCst);
                        }
                    }
                }
                Err(_) => {
                    if self.is_recording() {
                        info!("Match client closed. Stopping recording and finalizing audio...");
                        self.is_recording.store(false, Ordering::SeqCst);
                    }
                }
            }

            sleep(Duration::from_millis(500)).await;
        }

        info!("LeagueRecorder watcher loop stopped.");
    }
}
