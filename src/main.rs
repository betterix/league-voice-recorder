use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use serde::{Deserialize, Serialize};
use tracing::info;

use league_voice_recorder::{
    config::AppConfig,
    lcu::LcuCredentials,
    record::LeagueRecorder,
    replay::{ReplaySynchronizer, RodioPlayer, RoflProcessDetector},
};

#[derive(Default)]
struct AppState {
    rec_stop: Arc<AtomicBool>,
    rep_stop: Arc<AtomicBool>,
    rec_active: Arc<AtomicBool>,
    rep_active: Arc<AtomicBool>,
}

impl AppState {
    pub fn start_recording(&self) {
        if self.rec_active.load(Ordering::SeqCst) {
            return;
        }
        self.rec_stop.store(false, Ordering::SeqCst);
        self.rec_active.store(true, Ordering::SeqCst);
        let stop_sig = self.rec_stop.clone();
        let active_sig = self.rec_active.clone();
        let cfg_opt = AppConfig::load().ok();
        if let Some(cfg) = cfg_opt {
            tokio::spawn(async move {
                let recorder = LeagueRecorder::new(cfg);
                recorder.run_watcher_loop(stop_sig).await;
                active_sig.store(false, Ordering::SeqCst);
            });
        }
    }

    pub fn stop_recording(&self) {
        self.rec_stop.store(true, Ordering::SeqCst);
        self.rec_active.store(false, Ordering::SeqCst);
    }

    pub fn start_replay(&self) {
        if self.rep_active.load(Ordering::SeqCst) {
            return;
        }
        self.rep_stop.store(false, Ordering::SeqCst);
        self.rep_active.store(true, Ordering::SeqCst);
        let stop_sig = self.rep_stop.clone();
        let active_sig = self.rep_active.clone();
        let cfg_opt = AppConfig::load().ok();
        if let Some(cfg) = cfg_opt {
            tokio::spawn(async move {
                let synchronizer = ReplaySynchronizer::new(cfg);
                synchronizer.run_sync_watcher(stop_sig).await;
                active_sig.store(false, Ordering::SeqCst);
            });
        }
    }

    pub fn stop_replay(&self) {
        self.rep_stop.store(true, Ordering::SeqCst);
        self.rep_active.store(false, Ordering::SeqCst);
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
                    items.push(RecordingItem {
                        filename,
                        size_str,
                        date_str: "Today".to_string(),
                    });
                }
            }
        }
    }
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
fn play_recording(filename: String) {
    if let Ok(cfg) = AppConfig::load() {
        let path = cfg.record_folder.join(filename);
        if path.exists() {
            std::thread::spawn(move || {
                if let Ok(mut player) = RodioPlayer::try_new() {
                    if player.load_file(&path).is_ok() {
                        player.play();
                        std::thread::sleep(std::time::Duration::from_secs(10));
                    }
                }
            });
        }
    }
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

            // 1. Check LCU Gameflow Phase for ChampSelect or InProgress auto-start
            if let Some(creds) = LcuCredentials::find_active() {
                if let Ok(phase) = creds.get_gameflow_phase().await {
                    if (phase == "ChampSelect" || phase == "InProgress") && phase != last_phase {
                        info!("🎮 Auto-detect: League entered {}! Auto-starting recording watcher...", phase);
                        last_phase = phase.clone();
                        auto_state.start_recording();
                    } else if phase == "None" || phase == "Lobby" || phase == "EndOfGame" {
                        last_phase = phase.clone();
                    }
                }
            } else if LcuCredentials::is_game_process_running() {
                if !auto_state.rec_active.load(Ordering::SeqCst) {
                    info!("🎮 Auto-detect: League of Legends.exe running! Auto-starting recording watcher...");
                    auto_state.start_recording();
                }
            }

            // 2. Check for active ROFL replays
            if RoflProcessDetector::detect_running_rofl().is_some() {
                if !auto_state.rep_active.load(Ordering::SeqCst) {
                    info!("▶ Auto-detect: ROFL replay running! Auto-starting replay auto-sync...");
                    auto_state.start_replay();
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
            get_recordings,
            open_recordings_folder,
            play_recording,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");

    Ok(())
}
