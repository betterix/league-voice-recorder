use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tokio::time::{sleep, Duration};
use tracing::{info, warn};

use crate::config::AppConfig;
use crate::replay::{ReplayApiClient, RodioPlayer, RoflProcessDetector};

/// Finds the best matching MP3 audio file for a given Game ID in the recordings folder.
/// Matches `<game_id>.mp3`, `<game_id>_1.mp3`, or files containing the numeric game ID,
/// as well as embedded ID3 metadata tags.
pub fn find_matching_audio_file(folder: &Path, game_id: &str) -> Option<PathBuf> {
    if !folder.exists() || game_id.is_empty() {
        return None;
    }
    let clean_digits: String = game_id.chars().filter(|c| c.is_ascii_digit()).collect();
    let mut candidates: Vec<(PathBuf, u64, std::time::SystemTime)> = Vec::new();
    if let Ok(entries) = std::fs::read_dir(folder) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().and_then(|s| s.to_str()).map(|s| s.eq_ignore_ascii_case("mp3")).unwrap_or(false) {
                let fname = path.file_stem().unwrap_or_default().to_string_lossy();
                let fname_clean_digits: String = fname.chars().filter(|c| c.is_ascii_digit()).collect();

                let mut matched = fname == game_id
                    || fname.starts_with(game_id)
                    || fname.contains(game_id)
                    || game_id.contains(&*fname);

                if !matched && !clean_digits.is_empty() && !fname_clean_digits.is_empty() {
                    matched = clean_digits == fname_clean_digits
                        || clean_digits.contains(&fname_clean_digits)
                        || fname_clean_digits.contains(&clean_digits);
                }

                // Also check ID3 metadata tag if filename did not match
                if !matched {
                    if let Ok(meta) = crate::scrim_meta::ScrimMetadata::read_from_mp3(&path) {
                        let meta_digits: String = meta.game_id.chars().filter(|c| c.is_ascii_digit()).collect();
                        if !meta.game_id.is_empty() && (meta.game_id == game_id || (!clean_digits.is_empty() && meta_digits == clean_digits)) {
                            matched = true;
                        }
                    }
                }

                if matched {
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

        let fallback_vol = Arc::new(std::sync::Mutex::new((self.config.replay_volume as f32 / 100.0).clamp(0.0, 2.0)));
        let v_sink = volume_sink.unwrap_or(fallback_vol);

        update_status("WATCHING", "Waiting for replay...");
        update_clock(None);

        let mut current_game_id: Option<String> = None;
        let mut last_seek_inst = std::time::Instant::now().checked_sub(Duration::from_secs(10)).unwrap_or_else(std::time::Instant::now);
        let mut last_paused_state = true;
        let mut consecutive_inactive = 0;
        let mut had_active_replay = false;

        while !stop_signal.load(Ordering::Relaxed) {
            // Dual Detection Strategy:
            // 1. Process command line inspection for .rofl path and Game ID
            let rofl_opt = RoflProcessDetector::detect_running_rofl();

            // 2. Direct Replay API inspection (:2999/replay/playback)
            let api_playback_opt = self.api_client.get_playback_state().await.ok().flatten();

            let is_game_running = crate::lcu::LcuCredentials::is_game_process_running();

            // Once a replay was actively syncing, it remains active ONLY while the Replay API responds.
            // When the user exits the replay, API stops responding immediately.
            let is_replay_active = if had_active_replay {
                api_playback_opt.is_some()
            } else {
                rofl_opt.is_some()
                    || api_playback_opt.is_some()
                    || (is_game_running && current_game_id.is_some())
            };

            if is_replay_active {
                consecutive_inactive = 0;
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
                    had_active_replay = true;
                    let time_val = state.time.max(0.0);
                    let duration_val = state.length.unwrap_or(1800.0).max(1.0);
                    let speed = (state.speed as f32).max(0.1);

                    // Auto-stop if replay reached the end of the timeline
                    if duration_val > 5.0 && time_val >= (duration_val - 0.5) {
                        info!("🎬 Replay reached the end of the match timeline ({:.1}s / {:.1}s). Auto-stopping to IDLE...", time_val, duration_val);
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
                        stop_signal.store(true, Ordering::SeqCst);
                        break;
                    }

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

                    let target_time = (state.time + 1.0).max(0.0);

                    if let Ok(mut lk) = p_sink.lock() {
                        if let Some(ref mut p) = *lk {
                            let speed_changed = (p.get_speed() - speed).abs() > 0.02;
                            if speed_changed {
                                p.set_speed(speed);
                                let _ = p.seek(Duration::from_secs_f64(target_time));
                                last_seek_inst = std::time::Instant::now();
                            }

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
                                    // Actively playing: ONLY seek on genuine scrubs or large drift with cooldown
                                    let max_allowed_drift = (1.2 * speed as f64).max(1.2);
                                    if drift > max_allowed_drift && last_seek_inst.elapsed() >= Duration::from_millis(800) {
                                        let _ = p.seek(Duration::from_secs_f64(target_time));
                                        last_seek_inst = std::time::Instant::now();
                                    }
                                }
                            }
                        }
                    }
                } else {
                    // Replay API not yet connected (game is loading in or starting)
                    // Keep player alive as long as .rofl process or League 3D game engine is running!
                    if is_game_running || rofl_opt.is_some() {
                        update_status("WATCHING", &format!("Replay #{} (Connecting to Replay API...)", active_game_id));
                    }
                }

                sleep(Duration::from_millis(50)).await;
                continue;
            } else {
                // Not immediately active: increment consecutive inactive ticks
                consecutive_inactive += 1;

                // Hands-free fast auto-stop: As soon as an active replay finishes or closes (2 checks ~250ms),
                // stop listening immediately and return to IDLE just like match recording!
                if had_active_replay && consecutive_inactive >= 2 {
                    info!("🎬 Replay session closed. Auto-stopping replay watcher immediately back to IDLE...");
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
                    update_status("IDLE", "");
                    update_clock(None);
                    stop_signal.store(true, Ordering::SeqCst);
                    break;
                }

                if consecutive_inactive >= 6 {
                    if let Ok(mut lk) = p_sink.lock() {
                        if let Some(p) = lk.take() {
                            p.stop();
                        }
                    }
                    current_game_id = None;
                    last_paused_state = true;
                    update_status("IDLE", "");
                    update_clock(None);
                }
            }

            sleep(Duration::from_millis(150)).await;
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_find_matching_audio_file_exact_and_cross_format() {
        let temp_dir = std::env::temp_dir().join(format!("test_match_audio_{}", std::process::id()));
        let _ = std::fs::create_dir_all(&temp_dir);

        // Create dummy valid MP3 (> 4KB)
        let dummy_mp3_data = vec![0u8; 5000];
        let file_numeric = temp_dir.join("8018190134.mp3");
        std::fs::write(&file_numeric, &dummy_mp3_data).unwrap();

        // 1. Exact match with numeric ID
        let matched = find_matching_audio_file(&temp_dir, "8018190134");
        assert_eq!(matched, Some(file_numeric.clone()));

        // 2. Cross-match: replay is EUW1-8018190134, file is 8018190134.mp3
        let matched_prefix = find_matching_audio_file(&temp_dir, "EUW1-8018190134");
        assert_eq!(matched_prefix, Some(file_numeric.clone()));

        // 3. Reverse cross-match: file is EUW1_99887766.mp3, replay is 99887766
        let file_prefixed = temp_dir.join("EUW1_99887766.mp3");
        std::fs::write(&file_prefixed, &dummy_mp3_data).unwrap();
        let matched_reverse = find_matching_audio_file(&temp_dir, "99887766");
        assert_eq!(matched_reverse, Some(file_prefixed.clone()));

        // 4. Non-matching ID returns None
        let unmatched = find_matching_audio_file(&temp_dir, "1122334455");
        assert_eq!(unmatched, None);

        let _ = std::fs::remove_dir_all(temp_dir);
    }
}
