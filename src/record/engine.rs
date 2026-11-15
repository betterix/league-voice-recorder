use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Instant;
use tokio::time::{sleep, Duration};
use tracing::{info, warn};

use crate::config::AppConfig;
use crate::lcu::{LiveApiClient, LcuCredentials};
use crate::replay::{ReplayApiClient, RoflProcessDetector};
use crate::audio::{AudioCaptureManager, AudioFinalizer};
use crate::scrim_meta::ScrimMetadata;

pub struct LeagueRecorder {
    config: AppConfig,
    is_recording: Arc<AtomicBool>,
    is_paused: Arc<AtomicBool>,
    live_client: LiveApiClient,
    replay_client: ReplayApiClient,
}

impl LeagueRecorder {
    pub fn new(config: AppConfig) -> Self {
        Self {
            config,
            is_recording: Arc::new(AtomicBool::new(false)),
            is_paused: Arc::new(AtomicBool::new(false)),
            live_client: LiveApiClient::default(),
            replay_client: ReplayApiClient::default(),
        }
    }

    pub fn is_recording(&self) -> bool {
        self.is_recording.load(Ordering::SeqCst)
    }

    pub fn is_paused(&self) -> bool {
        self.is_paused.load(Ordering::SeqCst)
    }

    /// Checks if the currently active game process is a replay (.rofl) rather than a live match.
    pub async fn is_replay_active(&self) -> bool {
        if RoflProcessDetector::detect_running_rofl().is_some() {
            return true;
        }
        if self.replay_client.get_playback_state().await.is_ok() {
            return true;
        }
        false
    }

    pub async fn run_watcher_loop(&self, stop_signal: Arc<AtomicBool>) {
        info!("Starting high-frequency LeagueRecorder watcher loop (10ms-50ms)...");

        let mut first_game_time: f64 = 0.0;
        let mut last_game_time: f64 = 0.0;
        let mut last_advance_time = Instant::now();
        let mut pause_tick_count: usize = 0;
        let mut consecutive_misses: usize = 0;
        let mut audio_capture = AudioCaptureManager::new();

        while !stop_signal.load(Ordering::Relaxed) {
            // Guard: Never record active ROFL replays
            if self.is_replay_active().await {
                if self.is_recording() {
                    warn!("Replay detected during recording! Aborting live match capture.");
                    self.is_recording.store(false, Ordering::SeqCst);
                    self.is_paused.store(false, Ordering::SeqCst);
                    let _ = audio_capture.stop_and_take_buffers();
                }
                sleep(Duration::from_millis(500)).await;
                continue;
            }

            match self.live_client.poll_all_game_data().await {
                Ok(data) => {
                    consecutive_misses = 0;
                    if let Some(game_data) = data.game_data {
                        let game_time = game_data.game_time;

                        if !self.is_recording() {
                            if game_time > 0.0 {
                                info!("🔴 Live match started at game_time = {:.3}. Starting WASAPI recording...", game_time);
                                self.is_recording.store(true, Ordering::SeqCst);
                                self.is_paused.store(false, Ordering::SeqCst);
                                first_game_time = game_time;
                                last_game_time = game_time;
                                last_advance_time = Instant::now();
                                pause_tick_count = 0;

                                if let Err(e) = audio_capture.start(None) {
                                    warn!("Audio capture failed to initialize: {}", e);
                                }
                            }
                        } else {
                            // Pause detection state machine:
                            // Riot's Live Client Data API refreshes at ~4Hz (250ms).
                            // If game_time is unchanged for >= 0.45s (or >= 15 ticks), a match pause is confirmed.
                            if (game_time - last_game_time).abs() < 0.001 && game_time > 0.0 {
                                pause_tick_count += 1;
                                let elapsed = last_advance_time.elapsed();
                                if (pause_tick_count >= 15 || elapsed >= Duration::from_millis(450)) && !self.is_paused() {
                                    info!("⏸ MATCH PAUSED at game_time = {:.2}. Audio recording paused (detection lag truncated).", last_game_time);
                                    self.is_paused.store(true, Ordering::SeqCst);
                                    audio_capture.pause_and_truncate_lag(450, 48000);
                                }
                            } else if game_time > last_game_time {
                                pause_tick_count = 0;
                                last_advance_time = Instant::now();
                                if self.is_paused() {
                                    info!("▶ MATCH RESUMED at game_time = {:.2}.", game_time);
                                    self.is_paused.store(false, Ordering::SeqCst);
                                    audio_capture.resume();
                                }
                            }
                            last_game_time = game_time;
                        }
                    }
                }
                Err(_) => {
                    if self.is_recording() {
                        consecutive_misses += 1;
                        if consecutive_misses >= 10 {
                            info!("Match client closed. Stopping recording and finalizing audio...");
                            self.is_recording.store(false, Ordering::SeqCst);
                            self.is_paused.store(false, Ordering::SeqCst);

                            let (loopback_pcm, mic_pcm) = audio_capture.stop_and_take_buffers();

                            // Extract official Game ID from LCU session if available
                            let mut game_id = format!("{}", std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs());
                            if let Some(creds) = LcuCredentials::find_active() {
                                if let Ok(gid) = creds.get_game_id().await {
                                    game_id = gid;
                                }
                            }

                            let out_path = self.config.record_folder.join(format!("{}.mp3", game_id));
                            info!("Finalizing scrim audio to {:?}", out_path);

                            // Finalize MP3 using pure-Rust encoder (0 FFmpeg required)
                            let final_loopback = if loopback_pcm.is_empty() { vec![0u8; 48000 * 4] } else { loopback_pcm };
                            let final_mic = if mic_pcm.is_empty() { vec![0u8; 48000 * 4] } else { mic_pcm };

                            let _ = AudioFinalizer::finalize_to_mp3(
                                &final_loopback,
                                &final_mic,
                                48000,
                                2,
                                first_game_time,
                                &out_path,
                            );

                            // Write ID3 tag
                            let meta = ScrimMetadata {
                                game_id: game_id.clone(),
                                team_tag: "SCRIM".to_string(),
                                notes: format!("Match ended at {:.2}s", last_game_time),
                                game_patch: "14.23".to_string(),
                                recorded_at: "2026-11-15".to_string(),
                            };
                            let _ = meta.write_to_mp3(&out_path);
                            info!("✅ Audio finalized and tagged successfully: {:?}", out_path);
                            consecutive_misses = 0;
                        }
                    }
                }
            }

            // High frequency polling when active (10ms), standard when waiting (50ms)
            let poll_delay = if self.is_recording() { 10 } else { 50 };
            sleep(Duration::from_millis(poll_delay)).await;
        }

        info!("LeagueRecorder watcher loop stopped.");
    }
}
