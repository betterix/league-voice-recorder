use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tokio::time::{sleep, Duration};
use tracing::{info, warn};

use crate::config::AppConfig;
use crate::replay::{ReplayApiClient, RodioPlayer, RoflProcessDetector};

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
        info!("Starting ReplaySynchronizer watcher loop (Pure Rust Audio Engine)...");

        let mut current_game_id: Option<String> = None;
        let mut player: Option<RodioPlayer> = None;
        let mut consecutive_failures: usize = 0;
        let mut last_seek_time = std::time::Instant::now();

        while !stop_signal.load(Ordering::Relaxed) {
            if let Some((game_id, rofl_path)) = RoflProcessDetector::detect_running_rofl() {
                if current_game_id.as_deref() != Some(&game_id) {
                    info!("Active ROFL replay detected: Game ID {} ({:?})", game_id, rofl_path);
                    current_game_id = Some(game_id.clone());

                    let target_audio = self.config.record_folder.join(format!("{}.mp3", game_id));
                    if target_audio.exists() {
                        info!("Matching scrim audio found: {:?}. Initializing pure Rust player...", target_audio);
                        match RodioPlayer::try_new() {
                            Ok(mut p) => {
                                if let Err(e) = p.load_file(&target_audio) {
                                    warn!("Failed to load audio into RodioPlayer: {}", e);
                                    player = None;
                                } else {
                                    player = Some(p);
                                    info!("Audio loaded. Starting 50ms Replay API sync loop...");
                                }
                            }
                            Err(e) => {
                                warn!("Failed to initialize Rodio audio stream: {}", e);
                                player = None;
                            }
                        }
                    } else {
                        info!("No matching scrim recording found for Game ID {}", game_id);
                    }
                }

                // If audio player is active, synchronize playback
                if let Some(ref mut p) = player {
                    match self.api_client.get_playback_state().await {
                        Ok(state) => {
                            consecutive_failures = 0;
                            // Synchronize pause state
                            if state.paused {
                                if !p.is_paused() {
                                    p.pause();
                                }
                            } else {
                                if p.is_paused() {
                                    p.play();
                                }
                                // Synchronize playback speed
                                p.set_speed(state.speed as f32);

                                // Check for timeline scrub / large drift (> 1.0s)
                                if last_seek_time.elapsed() >= Duration::from_millis(500) {
                                    let _ = p.seek(Duration::from_secs_f64(state.time.max(0.0)));
                                    last_seek_time = std::time::Instant::now();
                                }
                            }
                        }
                        Err(_) => {
                            consecutive_failures += 1;
                            if consecutive_failures >= 15 {
                                info!("Replay client disconnected. Stopping audio player...");
                                p.stop();
                                player = None;
                                current_game_id = None;
                                consecutive_failures = 0;
                            }
                        }
                    }
                    sleep(Duration::from_millis(50)).await;
                    continue;
                }
            } else {
                if let Some(p) = player.take() {
                    info!("Replay process closed. Stopping audio.");
                    p.stop();
                    current_game_id = None;
                }
            }

            sleep(Duration::from_millis(500)).await;
        }

        info!("ReplaySynchronizer watcher loop stopped.");
    }
}
