use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use serde::{Deserialize, Serialize};
use tracing::info;

use league_voice_recorder::{
    audio::AudioCaptureManager,
    config::AppConfig,
    lcu::LcuCredentials,
    record::LeagueRecorder,
    replay::{ReplayConfigManager, ReplaySynchronizer, RodioPlayer, RoflProcessDetector},
};

fn get_timestamp() -> String {
    use std::time::SystemTime;
    let dur = SystemTime::now().duration_since(SystemTime::UNIX_EPOCH).unwrap_or_default();
    let secs = dur.as_secs() % 86400;
    let hours = (secs / 3600 + 1) % 24; // Local/CET offset approx or UTC
    let mins = (secs % 3600) / 60;
    let s = secs % 60;
    format!("{:02}:{:02}:{:02}", hours, mins, s)
}

pub struct AppState {
    pub rec_stop: Arc<AtomicBool>,
    pub rep_stop: Arc<AtomicBool>,
    pub rec_active: Arc<AtomicBool>,
    pub rep_active: Arc<AtomicBool>,
    pub log_queue: Arc<Mutex<Vec<String>>>,
    pub current_player: Arc<Mutex<Option<RodioPlayer>>>,
    pub replay_volume: Arc<Mutex<f32>>,
}

impl Default for AppState {
    fn default() -> Self {
        Self {
            rec_stop: Arc::new(AtomicBool::new(false)),
            rep_stop: Arc::new(AtomicBool::new(false)),
            rec_active: Arc::new(AtomicBool::new(false)),
            rep_active: Arc::new(AtomicBool::new(false)),
            log_queue: Arc::new(Mutex::new(Vec::new())),
            current_player: Arc::new(Mutex::new(None)),
            replay_volume: Arc::new(Mutex::new(1.0)),
        }
    }
}

impl AppState {
    pub fn log(&self, msg: &str) {
        let entry = format!("[{}] {}", get_timestamp(), msg);
        if let Ok(mut q) = self.log_queue.lock() {
            q.push(entry);
        }
        info!("{}", msg);
    }

    pub fn start_recording(&self) {
        if self.rec_active.load(Ordering::SeqCst) {
            return;
        }
        self.rec_stop.store(false, Ordering::SeqCst);
        self.rec_active.store(true, Ordering::SeqCst);
        self.log("🔴 Recording Watcher started. Polling League client LiveData (:2999)...");

        let stop_sig = self.rec_stop.clone();
        let active_sig = self.rec_active.clone();
        let log_q = self.log_queue.clone();

        let cfg_opt = AppConfig::load().ok();
        if let Some(cfg) = cfg_opt {
            tokio::spawn(async move {
                let recorder = LeagueRecorder::new(cfg);
                recorder.run_watcher_loop(stop_sig).await;
                active_sig.store(false, Ordering::SeqCst);
                let entry = format!("[{}] ⏹ Recording Watcher stopped.", get_timestamp());
                if let Ok(mut q) = log_q.lock() {
                    q.push(entry);
                }
            });
        }
    }

    pub fn stop_recording(&self) {
        self.rec_stop.store(true, Ordering::SeqCst);
        self.rec_active.store(false, Ordering::SeqCst);
        self.log("⏹ Stopping Recording Watcher...");
    }

    pub fn start_replay(&self) {
        if self.rep_active.load(Ordering::SeqCst) {
            return;
        }
        self.rep_stop.store(false, Ordering::SeqCst);
        self.rep_active.store(true, Ordering::SeqCst);
        self.log("▶ Replay Auto-Sync started. Monitoring running .rofl processes...");

        let stop_sig = self.rep_stop.clone();
        let active_sig = self.rep_active.clone();
        let log_q = self.log_queue.clone();

        let cfg_opt = AppConfig::load().ok();
        if let Some(cfg) = cfg_opt {
            tokio::spawn(async move {
                let synchronizer = ReplaySynchronizer::new(cfg);
                synchronizer.run_sync_watcher(stop_sig).await;
                active_sig.store(false, Ordering::SeqCst);
                let entry = format!("[{}] ⏹ Replay Auto-Sync stopped.", get_timestamp());
                if let Ok(mut q) = log_q.lock() {
                    q.push(entry);
                }
            });
        }
    }

    pub fn stop_replay(&self) {
        self.rep_stop.store(true, Ordering::SeqCst);
        self.rep_active.store(false, Ordering::SeqCst);
        self.log("⏹ Stopping Replay Auto-Sync...");
    }
}

#[derive(Serialize, Deserialize)]
struct StatusDto {
    league_running: bool,
    in_game: bool,
    phase: String,
    is_recording_watcher: bool,
    is_replay_watcher: bool,
    match_clock: Option<String>,
}

#[derive(Serialize, Deserialize)]
struct RecordingItem {
    filename: String,
    size_str: String,
    date_str: String,
}

#[tauri::command]
async fn get_status(state: tauri::State<'_, Arc<AppState>>) -> Result<StatusDto, String> {
    let client_running = LcuCredentials::is_client_running();
    let game_running = LcuCredentials::is_game_process_running();

    let mut phase = "Offline".to_string();
    if let Some(creds) = LcuCredentials::find_active() {
        if let Ok(p) = creds.get_gameflow_phase().await {
            phase = p;
        } else if client_running {
            phase = "Client Active".to_string();
        }
    } else if client_running {
        phase = "Client Active".to_string();
    } else if game_running {
        phase = "InProgress".to_string();
    }

    Ok(StatusDto {
        league_running: client_running || game_running,
        in_game: game_running || phase == "InProgress",
        phase,
        is_recording_watcher: state.rec_active.load(Ordering::SeqCst),
        is_replay_watcher: state.rep_active.load(Ordering::SeqCst),
        match_clock: None,
    })
}

#[tauri::command]
fn start_recording_watcher(state: tauri::State<'_, Arc<AppState>>) -> Result<(), String> {
    state.start_recording();
    Ok(())
}

#[tauri::command]
fn stop_recording_watcher(state: tauri::State<'_, Arc<AppState>>) -> Result<(), String> {
    state.stop_recording();
    Ok(())
}

#[tauri::command]
fn start_replay_watcher(state: tauri::State<'_, Arc<AppState>>) -> Result<(), String> {
    state.start_replay();
    Ok(())
}

#[tauri::command]
fn stop_replay_watcher(state: tauri::State<'_, Arc<AppState>>) -> Result<(), String> {
    state.stop_replay();
    Ok(())
}

#[tauri::command]
fn get_logs(state: tauri::State<'_, Arc<AppState>>) -> Vec<String> {
    let mut q = state.log_queue.lock().unwrap();
    let logs = q.clone();
    q.clear();
    logs
}

#[tauri::command]
fn get_recordings() -> Vec<RecordingItem> {
    let mut items = Vec::new();
    if let Ok(cfg) = AppConfig::load() {
        if let Ok(entries) = std::fs::read_dir(&cfg.record_folder) {
            for entry in entries.flatten() {
                let path = entry.path();
                if path.extension().and_then(|s| s.to_str()) == Some("mp3") {
                    let filename = path.file_name().unwrap_or_default().to_string_lossy().to_string();
                    let metadata = entry.metadata().ok();
                    let size = metadata.as_ref().map(|m| m.len()).unwrap_or(0);
                    let size_str = format!("{:.1} MB", size as f64 / 1_048_576.0);

                    let date_str = metadata
                        .and_then(|m| m.modified().ok())
                        .map(|t| {
                            let dur = std::time::SystemTime::now().duration_since(t).unwrap_or_default();
                            let mins = dur.as_secs() / 60;
                            if mins < 60 {
                                format!("{}m ago", mins.max(1))
                            } else if mins < 1440 {
                                format!("{}h ago", mins / 60)
                            } else {
                                format!("{}d ago", mins / 1440)
                            }
                        })
                        .unwrap_or_else(|| "Saved".to_string());

                    items.push(RecordingItem {
                        filename,
                        size_str,
                        date_str,
                    });
                }
            }
        }
    }
    // Reverse order so newest recordings appear on top
    items.reverse();
    items
}

#[tauri::command]
fn open_recordings_folder() {
    if let Ok(cfg) = AppConfig::load() {
        let _ = std::process::Command::new("explorer")
            .arg(&cfg.record_folder)
            .spawn();
    }
}

#[tauri::command]
fn play_recording(filename: String, state: tauri::State<'_, Arc<AppState>>) {
    if let Ok(cfg) = AppConfig::load() {
        let path = cfg.record_folder.join(&filename);
        if path.exists() {
            state.log(&format!("▶ Playing scrimmage audio in pure Rust player: {}", filename));
            let player_lock = state.current_player.clone();
            let vol = *state.replay_volume.lock().unwrap();

            std::thread::spawn(move || {
                if let Ok(mut player) = RodioPlayer::try_new() {
                    player.set_volume(vol);
                    if player.load_file(&path).is_ok() {
                        player.play();
                        *player_lock.lock().unwrap() = Some(player);
                    }
                }
            });
        }
    }
}

#[tauri::command]
fn delete_recording(filename: String, state: tauri::State<'_, Arc<AppState>>) -> Result<(), String> {
    if let Ok(cfg) = AppConfig::load() {
        let path = cfg.record_folder.join(&filename);
        if path.exists() {
            std::fs::remove_file(&path).map_err(|e| e.to_string())?;
            state.log(&format!("🗑 Deleted recording: {}", filename));
        }
    }
    Ok(())
}

#[tauri::command]
fn get_audio_devices() -> Vec<String> {
    AudioCaptureManager::get_available_microphones()
}

#[tauri::command]
fn get_config() -> Result<AppConfig, String> {
    AppConfig::load().map_err(|e| e.to_string())
}

#[tauri::command]
fn save_config(config: AppConfig, state: tauri::State<'_, Arc<AppState>>) -> Result<(), String> {
    config.save().map_err(|e| e.to_string())?;
    state.log("⚙️ Settings saved successfully.");
    Ok(())
}

#[tauri::command]
fn set_replay_volume(volume: u32, state: tauri::State<'_, Arc<AppState>>) {
    let vol_norm = (volume as f32 / 100.0).clamp(0.0, 1.0);
    *state.replay_volume.lock().unwrap() = vol_norm;
    if let Some(ref mut p) = *state.current_player.lock().unwrap() {
        p.set_volume(vol_norm);
    }
}

#[tauri::command]
fn enable_replay_api(state: tauri::State<'_, Arc<AppState>>) -> Result<String, String> {
    let res = ReplayConfigManager::enable_replay_api();
    match &res {
        Ok(msg) => state.log(&format!("⚡ {}", msg.lines().next().unwrap_or("Replay API enabled."))),
        Err(e) => state.log(&format!("⚠️ Failed to enable Replay API: {}", e)),
    }
    res
}

#[tauri::command]
fn check_replay_api_status() -> bool {
    ReplayConfigManager::check_replay_api_status().0
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt::init();
    info!("🎮 League Voice Recorder (Rust v0.5.1 Desktop UI)");

    let args: Vec<String> = std::env::args().collect();
    if args.iter().any(|arg| arg == "--headless") {
        info!("Running in headless watcher CLI mode...");
        let config = AppConfig::load()?;
        let stop_signal = Arc::new(AtomicBool::new(false));
        let stop_ctrlc = stop_signal.clone();

        tokio::spawn(async move {
            if tokio::signal::ctrl_c().await.is_ok() {
                info!("Shutdown signal received (Ctrl+C).");
                stop_ctrlc.store(true, Ordering::SeqCst);
            }
        });

        let recorder = LeagueRecorder::new(config.clone());
        let replay_sync = ReplaySynchronizer::new(config);

        let rec_stop = stop_signal.clone();
        let rep_stop = stop_signal.clone();

        let rec_handle = tokio::spawn(async move {
            recorder.run_watcher_loop(rec_stop).await;
        });

        let rep_handle = tokio::spawn(async move {
            replay_sync.run_sync_watcher(rep_stop).await;
        });

        let _ = tokio::join!(rec_handle, rep_handle);
        return Ok(());
    }

    let state = Arc::new(AppState::default());

    // Background Auto-Detection Thread (mimicking Python main.py _auto_detect_monitor_loop)
    let auto_state = state.clone();
    tokio::spawn(async move {
        let mut last_phase = String::new();
        loop {
            tokio::time::sleep(tokio::time::Duration::from_millis(1000)).await;

            let cfg = AppConfig::load().unwrap_or_default();

            // 1. Check LCU Gameflow Phase for ChampSelect or InProgress auto-start
            if cfg.auto_start_champ_select {
                if let Some(creds) = LcuCredentials::find_active() {
                    if let Ok(phase) = creds.get_gameflow_phase().await {
                        if (phase == "ChampSelect" || phase == "InProgress") && phase != last_phase {
                            auto_state.log(&format!("🎮 Auto-detect: League entered {}! Auto-starting recording watcher...", phase));
                            last_phase = phase.clone();
                            auto_state.start_recording();
                        } else if phase == "None" || phase == "Lobby" || phase == "EndOfGame" {
                            last_phase = phase.clone();
                        }
                    }
                } else if LcuCredentials::is_game_process_running() {
                    if !auto_state.rec_active.load(Ordering::SeqCst) {
                        auto_state.log("🎮 Auto-detect: League of Legends.exe running! Auto-starting recording watcher...");
                        auto_state.start_recording();
                    }
                }
            }

            // 2. Check for active ROFL replays
            if cfg.auto_start_replay {
                if RoflProcessDetector::detect_running_rofl().is_some() {
                    if !auto_state.rep_active.load(Ordering::SeqCst) {
                        auto_state.log("▶ Auto-detect: ROFL replay running! Auto-starting replay auto-sync...");
                        auto_state.start_replay();
                    }
                }
            }
        }
    });

    tauri::Builder::default()
        .manage(state)
        .invoke_handler(tauri::generate_handler![
            get_status,
            start_recording_watcher,
            stop_recording_watcher,
            start_replay_watcher,
            stop_replay_watcher,
            get_logs,
            get_recordings,
            open_recordings_folder,
            play_recording,
            delete_recording,
            get_audio_devices,
            get_config,
            save_config,
            set_replay_volume,
            enable_replay_api,
            check_replay_api_status,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");

    Ok(())
}
