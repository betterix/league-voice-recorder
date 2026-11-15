use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use serde::{Deserialize, Serialize};
use tracing::info;

use league_voice_recorder::{
    config::AppConfig,
    record::LeagueRecorder,
    replay::{ReplaySynchronizer, RodioPlayer},
};

#[derive(Default)]
struct AppState {
    rec_stop: Arc<AtomicBool>,
    rep_stop: Arc<AtomicBool>,
}

#[derive(Serialize, Deserialize)]
struct StatusDto {
    league_running: bool,
    in_game: bool,
    match_clock: Option<String>,
}

#[derive(Serialize, Deserialize)]
struct RecordingItem {
    filename: String,
    size_str: String,
    date_str: String,
}

#[tauri::command]
fn get_status() -> StatusDto {
    StatusDto {
        league_running: false,
        in_game: false,
        match_clock: None,
    }
}

#[tauri::command]
async fn start_recording_watcher(state: tauri::State<'_, Arc<AppState>>) -> Result<(), String> {
    state.rec_stop.store(false, Ordering::SeqCst);
    let stop_sig = state.rec_stop.clone();
    let cfg_opt = AppConfig::load().ok();
    if let Some(cfg) = cfg_opt {
        tokio::spawn(async move {
            let recorder = LeagueRecorder::new(cfg);
            recorder.run_watcher_loop(stop_sig).await;
        });
    }
    Ok(())
}

#[tauri::command]
fn stop_recording_watcher(state: tauri::State<'_, Arc<AppState>>) -> Result<(), String> {
    state.rec_stop.store(true, Ordering::SeqCst);
    Ok(())
}

#[tauri::command]
async fn start_replay_watcher(state: tauri::State<'_, Arc<AppState>>) -> Result<(), String> {
    state.rep_stop.store(false, Ordering::SeqCst);
    let stop_sig = state.rep_stop.clone();
    let cfg_opt = AppConfig::load().ok();
    if let Some(cfg) = cfg_opt {
        tokio::spawn(async move {
            let synchronizer = ReplaySynchronizer::new(cfg);
            synchronizer.run_sync_watcher(stop_sig).await;
        });
    }
    Ok(())
}

#[tauri::command]
fn stop_replay_watcher(state: tauri::State<'_, Arc<AppState>>) -> Result<(), String> {
    state.rep_stop.store(true, Ordering::SeqCst);
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

    let state = Arc::new(AppState {
        rec_stop: Arc::new(AtomicBool::new(false)),
        rep_stop: Arc::new(AtomicBool::new(false)),
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
