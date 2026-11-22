use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
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
        if let Ok(Some(_state)) = self.replay_client.get_playback_state().await {
            return true;
        }
        if let Some(creds) = LcuCredentials::find_active() {
            if let Ok(phase) = creds.get_gameflow_phase().await {
                if phase == "WatchInProgress" {
                    return true;
                }
            }
        }
        false
    }

    pub async fn run_watcher_loop(
        &self,
        stop_signal: Arc<AtomicBool>,
        levels: Option<Arc<Mutex<(f32, f32)>>>,
        status_sink: Option<Arc<Mutex<(String, String)>>>,
        clock_sink: Option<Arc<Mutex<Option<String>>>>,
        discord_muted: Option<Arc<AtomicBool>>,
        base_time_sink: Option<Arc<Mutex<Option<String>>>>,
        patch_sink: Option<Arc<Mutex<Option<String>>>>,
    ) {
        info!("Starting LeagueRecorder watcher loop...");

        let update_status = |status: &str, detail: &str| {
            if let Some(ref sink) = status_sink {
                if let Ok(mut lk) = sink.lock() {
                    *lk = (status.to_string(), detail.to_string());
                }
            }
        };

        let update_clock = |clk: Option<String>| {
            if let Some(ref sink) = clock_sink {
                if let Ok(mut lk) = sink.lock() {
                    *lk = clk;
                }
            }
        };

        let update_base_time = |bt: Option<String>| {
            if let Some(ref sink) = base_time_sink {
                if let Ok(mut lk) = sink.lock() {
                    *lk = bt;
                }
            }
        };

        let update_patch = |p: Option<String>| {
            if let Some(ref sink) = patch_sink {
                if let Ok(mut lk) = sink.lock() {
                    *lk = p;
                }
            }
        };

        update_status("WATCHING", "Waiting for match...");
        update_clock(None);
        update_base_time(None);

        let mut first_game_time: f64 = 0.0;
        let mut last_game_time: f64 = 0.0;
        let mut last_advance_time = Instant::now();
        let mut pause_tick_count: usize = 0;
        let mut consecutive_misses: usize = 0;
        let mut real_game_id: Option<String> = None;
        let mut active_patch = LcuCredentials::fetch_current_patch().await;
        if !active_patch.is_empty() {
            update_patch(Some(active_patch.clone()));
        }
        let mut in_match = false;
        let mut capture_started = false;

        let mut audio_capture = AudioCaptureManager::new();
        let mic_target = if self.config.mic_device_name.is_empty() || self.config.mic_device_name == "default" {
            None
        } else {
            Some(self.config.mic_device_name.as_str())
        };

        // Start live preview for VU meters without creating recording files on disk
        let _ = audio_capture.start_preview(mic_target);

        if let Some(lv) = levels.clone() {
            let mic_pk = audio_capture.mic_peak.clone();
            let loop_pk = audio_capture.loopback_peak.clone();
            let stop_pk = stop_signal.clone();
            tokio::spawn(async move {
                while !stop_pk.load(Ordering::Relaxed) {
                    let mut m = 0.0;
                    let mut l = 0.0;
                    if let Ok(mut pk) = mic_pk.lock() {
                        m = *pk;
                        *pk *= 0.82;
                    }
                    if let Ok(mut pk) = loop_pk.lock() {
                        l = *pk;
                        *pk *= 0.82;
                    }
                    if let Ok(mut lk) = lv.lock() {
                        *lk = (m, l);
                    }
                    tokio::time::sleep(tokio::time::Duration::from_millis(40)).await;
                }
            });
        }

        while !stop_signal.load(Ordering::Relaxed) {

            // Guard 1: In lobby or champ select (game process not running yet)
            if !in_match && !LcuCredentials::is_game_process_running() {
                update_status("WATCHING", "Waiting for match...");
                update_clock(None);
                sleep(Duration::from_millis(500)).await;
                continue;
            }

            // Guard 2: Never record active ROFL replays
            if self.is_replay_active().await {
                if in_match {
                    warn!("Replay detected during recording! Aborting live match capture.");
                    self.is_recording.store(false, Ordering::SeqCst);
                    self.is_paused.store(false, Ordering::SeqCst);
                    update_status("IDLE", "");
                    update_clock(None);
                    let (loop_path, mic_path, _) = audio_capture.stop_and_take_files();
                    let _ = std::fs::remove_file(loop_path);
                    let _ = std::fs::remove_file(mic_path);
                    return;
                }
                update_status("WATCHING", "Replay active (ignoring)...");
                update_clock(None);
                sleep(Duration::from_millis(500)).await;
                continue;
            }

            match self.live_client.poll_game_time().await {
                Ok(Some(game_time)) => {
                    consecutive_misses = 0;
                    if !in_match {
                        // CRITICAL: Only start recording once match clock starts (> 0.0).
                        // Do NOT start recording during loading screen (0.0) or pre-game countdown (< 0.0).
                        if game_time > 0.0 {
                            in_match = true;
                            first_game_time = game_time;
                            last_game_time = game_time;
                            last_advance_time = Instant::now();
                            pause_tick_count = 0;

                            let base_mins = (first_game_time / 60.0).floor() as u32;
                            let base_secs = (first_game_time % 60.0).floor() as u32;
                            update_base_time(Some(format!("{:02}:{:02}", base_mins, base_secs)));

                            // Stop preview and engage WASAPI audio recording buffer on disk
                            audio_capture.stop_preview();
                            if let Err(e) = audio_capture.start(mic_target, discord_muted.clone()) {
                                warn!("Audio capture start warning: {}", e);
                            } else {
                                capture_started = true;
                            }
                            self.is_recording.store(true, Ordering::SeqCst);
                            info!("🎙️ Match clock active (game_time = {:.3}): audio recording buffer engaged.", game_time);

                            let mins = (game_time / 60.0).floor() as u32;
                            let secs = (game_time % 60.0).floor() as u32;
                            let time_str = format!("{:02}:{:02}", mins, secs);
                            info!("🔴 Live match detected at game_time = {:.3}. Recording started!", game_time);
                            update_clock(Some(time_str.clone()));
                            update_status("RECORDING", &format!("Live Match ({})", time_str));

                            // Actively retrieve Game ID and League patch
                            if active_patch.is_empty() {
                                active_patch = LcuCredentials::fetch_current_patch().await;
                            }
                            if real_game_id.is_none() || active_patch.is_empty() {
                                if let Some(creds) = LcuCredentials::find_active() {
                                    if real_game_id.is_none() {
                                        if let Ok(gid) = creds.get_game_id().await {
                                            real_game_id = Some(gid);
                                        }
                                    }
                                    if active_patch.is_empty() {
                                        if let Some(p) = creds.get_game_patch().await {
                                            active_patch = p;
                                        }
                                    }
                                }
                            }
                            if !active_patch.is_empty() {
                                update_patch(Some(active_patch.clone()));
                            }
                        } else if game_time == 0.0 {
                            // Loading screen in progress
                            update_status("WATCHING", "Loading Screen (waiting for 00:00)...");
                            update_clock(None);
                        } else {
                            // Pre-game countdown in progress — monitor and wait for 00:00
                            let cd_str = format!("Starting in -{:.1}s (waiting for 00:00)...", game_time.abs());
                            update_status("WATCHING", &cd_str);
                            update_clock(None);
                        }
                    } else {
                        // In match: live match timer and pause tracking
                        let time_val = game_time.max(0.0);
                        let mins = (time_val / 60.0).floor() as u32;
                        let secs = (time_val % 60.0).floor() as u32;
                        let time_str = format!("{:02}:{:02}", mins, secs);

                        update_clock(Some(time_str.clone()));

                        let detail_text = if let Some(ref gid) = real_game_id {
                            format!("Match #{} ({})", gid, time_str)
                        } else {
                            format!("Live Match ({})", time_str)
                        };

                        // Pause detection state machine
                        if (game_time - last_game_time).abs() < 0.001 && game_time > 0.0 {
                            pause_tick_count += 1;
                            let elapsed = last_advance_time.elapsed();
                            if (pause_tick_count >= 8 || elapsed >= Duration::from_millis(280)) && !self.is_paused() {
                                info!("⏸ MATCH PAUSED at game_time = {:.2}. Truncating detection lag...", last_game_time);
                                self.is_paused.store(true, Ordering::SeqCst);
                                audio_capture.pause_and_truncate_lag(280, 48000);
                                update_status("PAUSED", &format!("Match paused ({})", time_str));
                            }
                        } else if game_time > last_game_time {
                            pause_tick_count = 0;
                            last_advance_time = Instant::now();
                            if self.is_paused() {
                                info!("▶ MATCH RESUMED at game_time = {:.2}.", game_time);
                                self.is_paused.store(false, Ordering::SeqCst);
                                audio_capture.resume();
                            }
                            update_status("RECORDING", &detail_text);
                        }
                        last_game_time = game_time;
                    }
                }
                _ => {
                    if in_match {
                        consecutive_misses += 1;
                        if consecutive_misses >= 3 && !LcuCredentials::is_game_process_running() {
                            info!("⏹ Match client closed. Finalizing scrim audio...");
                            break;
                        } else if consecutive_misses >= 15 {
                            info!("⏹ Match client disconnected. Finalizing scrim audio...");
                            break;
                        }
                    } else {
                        consecutive_misses = 0;
                        update_status("WATCHING", "Waiting for match...");
                        update_clock(None);
                    }
                }
            }

            if in_match {
                sleep(Duration::from_millis(40)).await;
            } else {
                sleep(Duration::from_millis(150)).await;
            }
        }

        // Finalize and save MP3 (whether triggered by match end or user clicking Stop)
        self.is_recording.store(false, Ordering::SeqCst);
        self.is_paused.store(false, Ordering::SeqCst);
        update_clock(None);
        if let Some(ref lv) = levels {
            if let Ok(mut lk) = lv.lock() {
                *lk = (0.0, 0.0);
            }
        }

        if capture_started {
            update_status("SAVING", "Finalizing audio...");
            let (loopback_path, mic_path, max_bytes) = audio_capture.stop_and_take_files();

            // Check if we captured audio (at least 100ms)
            if max_bytes > 48000 / 10 * 4 {
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

                info!("💾 Streaming & encoding scrim audio to {:?} (first_game_time = {:.3})...", out_path, first_game_time);

                let res = AudioFinalizer::finalize_from_files(
                    &loopback_path,
                    &mic_path,
                    48000,
                    2,
                    first_game_time,
                    &out_path,
                );

                if active_patch.is_empty() {
                    active_patch = LcuCredentials::fetch_current_patch().await;
                }
                let now = chrono::Local::now();
                let recorded_dt = now.format("%Y-%m-%d").to_string();

                match res {
                    Ok(_) => {
                        let meta = ScrimMetadata {
                            game_id: base_id.clone(),
                            team_tag: String::new(),
                            notes: String::new(),
                            game_patch: active_patch.clone(),
                            recorded_at: recorded_dt,
                        };
                        let _ = meta.write_to_mp3(&out_path);
                        info!("✅ Scrim recording saved successfully: {:?} (Album: Patch {})", out_path, meta.game_patch);
                        update_status("SAVED", &format!("{}.mp3", base_id));
                    }
                    Err(e) => {
                        warn!("Failed to encode MP3 file: {}", e);
                        update_status("IDLE", "");
                    }
                }
            } else {
                info!("Watcher stopped without audio data (< 0.1s). No file written.");
                let _ = std::fs::remove_file(loopback_path);
                let _ = std::fs::remove_file(mic_path);
                update_status("IDLE", "");
            }
        } else {
            audio_capture.stop_preview();
            update_status("IDLE", "");
        }

        info!("LeagueRecorder watcher loop stopped.");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_recorder_watcher_lifecycle_and_mp3_generation() {
        let temp_dir = std::env::temp_dir().join(format!("lvr_test_recs_{}", std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        let _ = std::fs::create_dir_all(&temp_dir);

        let mut cfg = AppConfig::default();
        cfg.record_folder = temp_dir.clone();

        let recorder = LeagueRecorder::new(cfg);
        assert!(!recorder.is_recording());
        assert!(!recorder.is_paused());

        let stop_sig = Arc::new(AtomicBool::new(false));
        let status_sink = Arc::new(Mutex::new(("IDLE".to_string(), "".to_string())));

        let stop_clone = stop_sig.clone();
        let status_clone = status_sink.clone();
        let handle = tokio::spawn(async move {
            recorder.run_watcher_loop(stop_clone, None, Some(status_clone), None, None, None, None).await;
        });

        // Let watcher start and verify it enters WATCHING state without starting recording
        tokio::time::sleep(tokio::time::Duration::from_millis(150)).await;
        {
            let st = status_sink.lock().unwrap();
            assert_eq!(st.0, "WATCHING");
        }

        // Stop the watcher cleanly
        stop_sig.store(true, Ordering::SeqCst);
        let _ = handle.await;

        // Verify AudioFinalizer synthesis directly into the target folder
        let out_mp3 = temp_dir.join("9999999999.mp3");
        let sample_rate = 48000;
        let pcm_mic = vec![0u8; sample_rate * 4]; // 1 second 16-bit stereo PCM
        let pcm_loop = vec![0u8; sample_rate * 4];
        let res = AudioFinalizer::finalize_to_mp3(&pcm_loop, &pcm_mic, sample_rate as u32, 2, 0.0, &out_mp3);
        assert!(res.is_ok(), "AudioFinalizer must succeed: {:?}", res);

        let meta = ScrimMetadata {
            game_id: "9999999999".to_string(),
            team_tag: "TEST".to_string(),
            notes: "Unit test match".to_string(),
            game_patch: "14.23".to_string(),
            recorded_at: "2026-11-20".to_string(),
        };
        let _ = meta.write_to_mp3(&out_mp3);

        assert!(out_mp3.exists(), "Synthesized MP3 must exist on disk");
        let size = std::fs::metadata(&out_mp3).unwrap().len();
        assert!(size > 5000, "Synthesized MP3 must be valid: {} bytes", size);

        let _ = std::fs::remove_dir_all(&temp_dir);
    }
}
