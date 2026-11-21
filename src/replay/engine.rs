use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tokio::time::{sleep, Duration};
use tracing::{info, warn};

use crate::config::AppConfig;
use crate::replay::{ReplayApiClient, RodioPlayer, RoflProcessDetector};

/// Finds the best matching MP3 audio file for a given Game ID in the recordings folder.
/// Matches `<game_id>.mp3`, `<game_id>_1.mp3`, or files containing the numeric game ID.
pub fn find_matching_audio_file(folder: &Path, game_id: &str) -> Option<PathBuf> {
    if !folder.exists() || game_id.is_empty() {
        return None;
    }
    let mut candidates: Vec<(PathBuf, u64, std::time::SystemTime)> = Vec::new();
    if let Ok(entries) = std::fs::read_dir(folder) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().and_then(|s| s.to_str()).map(|s| s.eq_ignore_ascii_case("mp3")).unwrap_or(false) {
                let fname = path.file_stem().unwrap_or_default().to_string_lossy();
                if fname == game_id || fname.starts_with(game_id) || fname.contains(game_id) {
                    if let Ok(meta) = entry.metadata() {
                        let size = meta.len();
                        let mtime = meta.modified().unwrap_or(std::time::SystemTime::UNIX_EPOCH);
                        candidates.push((path, size, mtime));
                    }
                }
            }
        }
    }
    if candidates.is_empty() {
        return None;
    }
    // Filter out empty/broken stubs (< 4 KB)
    let valid: Vec<_> = candidates.iter().filter(|c| c.1 > 4096).cloned().collect();
    let pool = if !valid.is_empty() { valid } else { candidates };
    let best = pool.into_iter().max_by_key(|c| (c.2, c.1))?;
    Some(best.0)
}

/// Finds the most recent valid MP3 recording in the recordings folder.
pub fn find_latest_audio_file(folder: &Path) -> Option<(String, PathBuf)> {
    if !folder.exists() {
        return None;
    }
    let mut candidates: Vec<(PathBuf, u64, std::time::SystemTime)> = Vec::new();
    if let Ok(entries) = std::fs::read_dir(folder) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().and_then(|s| s.to_str()).map(|s| s.eq_ignore_ascii_case("mp3")).unwrap_or(false) {
                if let Ok(meta) = entry.metadata() {
                    let size = meta.len();
                    if size > 4096 {
                        let mtime = meta.modified().unwrap_or(std::time::SystemTime::UNIX_EPOCH);
                        candidates.push((path, size, mtime));
                    }
                }
            }
        }
    }
    let best = candidates.into_iter().max_by_key(|c| (c.2, c.1))?;
    let stem = best.0.file_stem()?.to_string_lossy().to_string();
    Some((stem, best.0))
}

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

    pub async fn run_sync_watcher(
        &self,
        stop_signal: Arc<AtomicBool>,
        status_sink: Option<Arc<std::sync::Mutex<(String, String)>>>,
        clock_sink: Option<Arc<std::sync::Mutex<Option<String>>>>,
        player_sink: Option<Arc<std::sync::Mutex<Option<RodioPlayer>>>>,
        volume_sink: Option<Arc<std::sync::Mutex<f32>>>,
        time_sink: Option<Arc<std::sync::Mutex<(f64, f64, f64)>>>,
    ) {
        info!("Starting ReplaySynchronizer watcher loop...");

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

        let fallback_player = Arc::new(std::sync::Mutex::new(None));
        let p_sink = player_sink.unwrap_or(fallback_player);

        let fallback_vol = Arc::new(std::sync::Mutex::new((self.config.replay_volume as f32 / 100.0).clamp(0.0, 1.0)));
        let v_sink = volume_sink.unwrap_or(fallback_vol);

        update_status("WATCHING", "Waiting for replay...");
        update_clock(None);

        let mut current_game_id: Option<String> = None;
        let mut last_seek_inst = std::time::Instant::now().checked_sub(Duration::from_secs(10)).unwrap_or_else(std::time::Instant::now);
        let mut process_running_since = std::time::Instant::now();
        let mut last_paused_state = true;

        while !stop_signal.load(Ordering::Relaxed) {
            // Dual Detection Strategy:
            // 1. Process command line inspection for .rofl path and Game ID
            let rofl_opt = RoflProcessDetector::detect_running_rofl();

            // 2. Direct Replay API inspection (:2999/replay/playback)
            // Even if the command line is protected by Windows or Vanguard, the Replay API on port 2999
            // only responds when a replay is active in League of Legends.
            let api_playback_opt = self.api_client.get_playback_state().await.ok().flatten();

            let is_replay_active = rofl_opt.is_some() || api_playback_opt.is_some();

            if is_replay_active {
                // Determine target Game ID: from .rofl argument, or existing session, or latest scrim recording
                let target_game_id = if let Some((ref gid, _)) = rofl_opt {
                    Some(gid.clone())
                } else if let Some(ref cur) = current_game_id {
                    Some(cur.clone())
                } else {
                    find_latest_audio_file(&self.config.record_folder).map(|(gid, _)| gid)
                };

                let active_game_id = target_game_id.unwrap_or_else(|| "Replay".to_string());

                let needs_init = current_game_id.as_deref() != Some(&active_game_id)
                    || p_sink.lock().map(|lk| lk.is_none()).unwrap_or(true);

                if needs_init {
                    info!("🎬 Active League replay confirmed! Game ID: {}", active_game_id);
                    current_game_id = Some(active_game_id.clone());
                    process_running_since = std::time::Instant::now();

                    let audio_file_opt = find_matching_audio_file(&self.config.record_folder, &active_game_id)
                        .or_else(|| find_latest_audio_file(&self.config.record_folder).map(|(_, p)| p));

                    if let Some(target_audio) = audio_file_opt {
                        info!("▶ Matching scrim recording found: {:?}. Synchronizing playback...", target_audio);
                        match RodioPlayer::try_new() {
                            Ok(mut p) => {
                                let vol = v_sink.lock().map(|v| *v).unwrap_or(1.0);
                                p.set_volume(vol);
                                if let Err(e) = p.load_file(&target_audio) {
                                    warn!("⚠️ Failed to load audio into RodioPlayer: {}", e);
                                    if let Ok(mut lk) = p_sink.lock() {
                                        *lk = None;
                                    }
                                } else {
                                    info!("▶ Audio successfully loaded for Game ID {}. Sync ready.", active_game_id);
                                    if let Ok(mut lk) = p_sink.lock() {
                                        *lk = Some(p);
                                    }
                                }
                            }
                            Err(e) => {
                                warn!("⚠️ Failed to initialize audio stream: {}", e);
                                if let Ok(mut lk) = p_sink.lock() {
                                    *lk = None;
                                }
                            }
                        }
                    } else {
                        info!("⚠️ No matching recording found in {:?} for Game ID {}", self.config.record_folder, active_game_id);
                    }
                }

                // Keep volume synchronized with any live slider updates
                if let Ok(v) = v_sink.lock() {
                    let vol = *v;
                    if let Ok(mut lk) = p_sink.lock() {
                        if let Some(ref mut p) = *lk {
                            p.set_volume(vol);
                        }
                    }
                }

                // Process live playback state
                if let Some(state) = api_playback_opt {
                    let time_val = state.time.max(0.0);
                    let duration_val = state.length.unwrap_or(1800.0).max(1.0);
                    let speed = (state.speed as f32).max(0.1);

                    if let Some(ref sink) = time_sink {
                        if let Ok(mut lk) = sink.lock() {
                            *lk = (time_val, duration_val, speed as f64);
                        }
                    }

                    let mins = (time_val / 60.0).floor() as u32;
                    let secs = (time_val % 60.0).floor() as u32;
                    let time_str = format!("{:02}:{:02}", mins, secs);

                    if state.time > 0.0 {
                        update_clock(Some(time_str.clone()));
                        if state.paused {
                            update_status("PAUSED", &format!("Replay #{} ({})", active_game_id, time_str));
                        } else {
                            update_status("SYNCING", &format!("Replay #{} ({})", active_game_id, time_str));
                        }
                    } else {
                        update_clock(None);
                        update_status("WATCHING", &format!("Replay #{} (Loading...)", active_game_id));
                    }

                    let target_time = state.time.max(0.0);

                    if let Ok(mut lk) = p_sink.lock() {
                        if let Some(ref mut p) = *lk {
                            p.set_speed(speed);

                            if state.paused {
                                if !p.is_paused() {
                                    p.pause();
                                }
                                last_paused_state = true;
                                // Scrubbing while paused: jump immediately if moved > 0.5s
                                let cur_pos = p.get_pos().as_secs_f64();
                                if (target_time - cur_pos).abs() > 0.5 {
                                    let _ = p.seek(Duration::from_secs_f64(target_time));
                                    last_seek_inst = std::time::Instant::now();
                                }
                            } else {
                                // Replay is playing!
                                let cur_pos = p.get_pos().as_secs_f64();
                                let drift = (target_time - cur_pos).abs();

                                if p.is_paused() || last_paused_state {
                                    // Resume from pause: seek to target time and engage playback
                                    if drift > 0.5 {
                                        let _ = p.seek(Duration::from_secs_f64(target_time));
                                    }
                                    p.play();
                                    last_paused_state = false;
                                    last_seek_inst = std::time::Instant::now();
                                } else {
                                    // Actively playing: ONLY seek on genuine scrubs or large drift (> 1.2s) with cooldown
                                    if drift > 1.2 && last_seek_inst.elapsed() >= Duration::from_millis(800) {
                                        let _ = p.seek(Duration::from_secs_f64(target_time));
                                        last_seek_inst = std::time::Instant::now();
                                    }
                                }
                            }
                        }
                    }
                } else {
                    // Replay API not yet connected (game is loading in or starting)
                    // Keep player alive as long as .rofl process is running!
                    if rofl_opt.is_some() {
                        update_status("WATCHING", &format!("Replay #{} (Connecting to Replay API...)", active_game_id));
                        if process_running_since.elapsed() > Duration::from_secs(3) {
                            if let Ok(mut lk) = p_sink.lock() {
                                if let Some(ref mut p) = *lk {
                                    if p.is_paused() {
                                        info!("▶ Replay process running: initiating audio playback (process sync fallback)");
                                        p.play();
                                        last_paused_state = false;
                                        update_status("SYNCING", &format!("Replay #{} (Playing)", active_game_id));
                                    }
                                }
                            }
                        }
                    }
                }

                sleep(Duration::from_millis(50)).await;
                continue;
            } else {
                // Neither process nor Replay API is active: stop playback and reset
                if let Ok(mut lk) = p_sink.lock() {
                    if let Some(p) = lk.take() {
                        info!("⏹ Replay process closed. Stopping audio.");
                        p.stop();
                    }
                }
                if let Some(ref sink) = time_sink {
                    if let Ok(mut lk) = sink.lock() {
                        *lk = (0.0, 1800.0, 1.0);
                    }
                }
                current_game_id = None;
                last_paused_state = true;
                update_status("WATCHING", "Waiting for replay...");
                update_clock(None);
            }

            sleep(Duration::from_millis(400)).await;
        }

        if let Ok(mut lk) = p_sink.lock() {
            if let Some(p) = lk.take() {
                p.stop();
            }
        }
        if let Some(ref sink) = time_sink {
            if let Ok(mut lk) = sink.lock() {
                *lk = (0.0, 1800.0, 1.0);
            }
        }
        update_status("IDLE", "");
        update_clock(None);
        info!("ReplaySynchronizer watcher loop stopped.");
    }
}
