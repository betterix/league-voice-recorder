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
        info!("Starting LeagueRecorder watcher loop...");

        let mut first_game_time: f64 = 0.0;
        let mut last_game_time: f64 = 0.0;
        let mut last_advance_time = Instant::now();
        let mut pause_tick_count: usize = 0;
        let mut consecutive_misses: usize = 0;
        let mut real_game_id: Option<String> = None;
        let mut in_match = false;

        let mut audio_capture = AudioCaptureManager::new();
        // Immediately start audio capture so both mic and loopback are armed and recording
        let mic_target = if self.config.mic_device_name.is_empty() || self.config.mic_device_name == "default" {
            None
        } else {
            Some(self.config.mic_device_name.as_str())
        };

        if let Err(e) = audio_capture.start(mic_target) {
            warn!("Audio capture start warning: {}", e);
        } else {
            info!("🎙️ WASAPI Audio capture active (Microphone + Loopback recording).");
            self.is_recording.store(true, Ordering::SeqCst);
        }

        while !stop_signal.load(Ordering::Relaxed) {
            // Guard: Never record active ROFL replays
            if self.is_replay_active().await {
                if in_match {
                    warn!("Replay detected during recording! Aborting live match capture.");
                    self.is_recording.store(false, Ordering::SeqCst);
                    self.is_paused.store(false, Ordering::SeqCst);
                    let _ = audio_capture.stop_and_take_buffers();
                    return;
                }
                sleep(Duration::from_millis(500)).await;
                continue;
            }

            match self.live_client.poll_game_time().await {
                Ok(Some(game_time)) => {
                    consecutive_misses = 0;
                    if !in_match {
                        in_match = true;
                        info!("🔴 Live match detected at game_time = {:.3}.", game_time);
                        first_game_time = game_time;
                        last_game_time = game_time;
                        last_advance_time = Instant::now();
                        pause_tick_count = 0;

                        // Background Game ID fetch
                        if real_game_id.is_none() {
                            if let Some(creds) = LcuCredentials::find_active() {
                                if let Ok(gid) = creds.get_game_id().await {
                                    info!("🎮 Retrieved official Game ID from LCU session: {}", gid);
                                    real_game_id = Some(gid);
                                }
                            }
                        }
                    } else {
                        // Pause detection state machine
                        if (game_time - last_game_time).abs() < 0.001 && game_time > 0.0 {
                            pause_tick_count += 1;
                            let elapsed = last_advance_time.elapsed();
                            if (pause_tick_count >= 15 || elapsed >= Duration::from_millis(450)) && !self.is_paused() {
                                info!("⏸ MATCH PAUSED at game_time = {:.2}. Truncating detection lag...", last_game_time);
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
                _ => {
                    if in_match {
                        consecutive_misses += 1;
                        if consecutive_misses >= 10 {
                            info!("Match client closed. Stopping recording and finalizing audio...");
                            break;
                        }
                    }
                }
            }

            sleep(Duration::from_millis(50)).await;
        }

        // Finalize and save MP3 (whether triggered by match end or user clicking Stop)
        self.is_recording.store(false, Ordering::SeqCst);
        self.is_paused.store(false, Ordering::SeqCst);

        let (loopback_pcm, mic_pcm) = audio_capture.stop_and_take_buffers();

        // Check if we captured audio (at least 100ms)
        let sample_count = loopback_pcm.len().max(mic_pcm.len());
        if sample_count > 48000 / 10 * 4 {
            // Ensure target directory exists
            let _ = std::fs::create_dir_all(&self.config.record_folder);

            // Fetch real Game ID if not yet retrieved
            if real_game_id.is_none() {
                if let Some(creds) = LcuCredentials::find_active() {
                    if let Ok(gid) = creds.get_game_id().await {
                        real_game_id = Some(gid);
                    }
                }
            }

            let base_id = real_game_id.unwrap_or_else(|| {
                format!("{}", std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs())
            });

            let mut out_path = self.config.record_folder.join(format!("{}.mp3", base_id));
            let mut counter = 1;
            while out_path.exists() {
                out_path = self.config.record_folder.join(format!("{}_{}.mp3", base_id, counter));
                counter += 1;
            }

            info!("Saving scrim audio to {:?}", out_path);

            let res = AudioFinalizer::finalize_to_mp3(
                &loopback_pcm,
                &mic_pcm,
                48000,
                2,
                first_game_time,
                &out_path,
            );

            match res {
                Ok(_) => {
                    let meta = ScrimMetadata {
                        game_id: base_id.clone(),
                        team_tag: "SCRIM".to_string(),
                        notes: format!("Recorded match length: {:.2}s", last_game_time),
                        game_patch: "14.23".to_string(),
                        recorded_at: "2026-11-15".to_string(),
                    };
                    let _ = meta.write_to_mp3(&out_path);
                    info!("✅ Scrim recording saved successfully: {:?}", out_path);
                }
                Err(e) => {
                    warn!("Failed to encode MP3 file: {}", e);
                }
            }
        } else {
            info!("Watcher stopped without audio data (< 0.1s). No file written.");
        }

        info!("LeagueRecorder watcher loop stopped.");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_recorder_saves_real_mp3() {
        let temp_dir = std::env::temp_dir().join("lvr_test_recs");
        let _ = std::fs::create_dir_all(&temp_dir);

        let mut cfg = AppConfig::default();
        cfg.record_folder = temp_dir.clone();

        let recorder = LeagueRecorder::new(cfg);
        let stop_sig = Arc::new(AtomicBool::new(false));

        let stop_clone = stop_sig.clone();
        let handle = tokio::spawn(async move {
            recorder.run_watcher_loop(stop_clone).await;
        });

        // Let it record for 350ms
        tokio::time::sleep(tokio::time::Duration::from_millis(350)).await;
        stop_sig.store(true, Ordering::SeqCst);

        let _ = handle.await;

        let entries: Vec<_> = std::fs::read_dir(&temp_dir)
            .unwrap()
            .flatten()
            .filter(|e| e.path().extension().and_then(|s| s.to_str()) == Some("mp3"))
            .collect();

        assert!(!entries.is_empty(), "No MP3 file was created in target directory!");
        let mp3_path = entries[0].path();
        let size = std::fs::metadata(&mp3_path).unwrap().len();
        println!("Generated MP3: {:?}, size: {} bytes", mp3_path, size);
        assert!(size > 1000, "MP3 file is too small: {}", size);

        let _ = std::fs::remove_dir_all(&temp_dir);
    }
}
