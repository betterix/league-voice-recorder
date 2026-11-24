#![windows_subsystem = "windows"]

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use serde::{Deserialize, Serialize};
use tracing::info;
use tracing_subscriber::layer::SubscriberExt;
use tracing_subscriber::util::SubscriberInitExt;

use league_voice_recorder::{
    audio::AudioCaptureManager,
    config::AppConfig,
    discord_ipc::DiscordVoiceState,
    lcu::LcuCredentials,
    record::LeagueRecorder,
    replay::{ReplayConfigManager, ReplaySynchronizer, RodioPlayer, RoflProcessDetector},
    scrim_meta::ScrimMetadata,
};

async fn blocking<T: Send + 'static>(f: impl FnOnce() -> T + Send + 'static) -> Option<T> {
    tauri::async_runtime::spawn_blocking(f).await.ok()
}

#[cfg(windows)]
unsafe extern "system" {
    fn AttachConsole(dwProcessId: u32) -> i32;
}

fn get_timestamp() -> String {
    use std::time::SystemTime;
    let dur = SystemTime::now().duration_since(SystemTime::UNIX_EPOCH).unwrap_or_default();
    let secs = dur.as_secs() % 86400;
    let hours = (secs / 3600 + 1) % 24; // Local CET offset approx
    let mins = (secs % 3600) / 60;
    let s = secs % 60;
    format!("{:02}:{:02}:{:02}", hours, mins, s)
}

/// Custom Tracing Layer that pipes info!, warn!, and error! events
/// from application modules directly into the thread-safe UI log queue in real time,
/// strictly filtering out internal connection pool spam from hyper, reqwest, tao, etc.
struct AppLogLayer {
    queue: Arc<Mutex<Vec<String>>>,
}

impl<S: tracing::Subscriber> tracing_subscriber::Layer<S> for AppLogLayer {
    fn on_event(&self, event: &tracing::Event<'_>, _ctx: tracing_subscriber::layer::Context<'_, S>) {
        let target = event.metadata().target();
        // Strictly filter to only application-level log events (matches Python logging)
        if !target.starts_with("league_voice_recorder") && !target.starts_with("main") {
            return;
        }

        struct MessageVisitor(String);
        impl tracing::field::Visit for MessageVisitor {
            fn record_debug(&mut self, field: &tracing::field::Field, value: &dyn std::fmt::Debug) {
                if field.name() == "message" {
                    let s = format!("{:?}", value);
                    self.0 = s.trim_matches('"').to_string();
                }
            }
        }
        let mut visitor = MessageVisitor(String::new());
        event.record(&mut visitor);
        if !visitor.0.is_empty() {
            let timestamp = get_timestamp();
            let entry = format!("[{}] {}", timestamp, visitor.0);
            if let Ok(mut q) = self.queue.lock() {
                q.push(entry);
            }
        }
    }
}

pub struct AppState {
    pub rec_stop: Arc<AtomicBool>,
    pub rep_stop: Arc<AtomicBool>,
    pub rec_active: Arc<AtomicBool>,
    pub rep_active: Arc<AtomicBool>,
    pub rec_status: Arc<Mutex<(String, String)>>,
    pub rep_status: Arc<Mutex<(String, String)>>,
    pub match_time: Arc<Mutex<Option<String>>>,
    pub log_queue: Arc<Mutex<Vec<String>>>,
    pub current_player: Arc<Mutex<Option<RodioPlayer>>>,
    pub replay_volume: Arc<Mutex<f32>>,
    pub replay_time: Arc<Mutex<(f64, f64, f64)>>,
    pub audio_levels: Arc<Mutex<(f32, f32)>>,
    pub discord_voice: Arc<DiscordVoiceState>,
    pub preview_capture: Arc<Mutex<AudioCaptureManager>>,
    pub app_handle: Arc<Mutex<Option<tauri::AppHandle>>>,
    pub hud_preview_mode: Arc<Mutex<Option<String>>>,
    pub hud_locked: Arc<AtomicBool>,
    pub hud_overlay_enabled: Arc<AtomicBool>,
    pub base_game_time: Arc<Mutex<Option<String>>>,
    pub active_game_patch: Arc<Mutex<Option<String>>>,
}

impl Default for AppState {
    fn default() -> Self {
        let initial_logs = vec![
            format!("[{}] 🎮 League Voice Recorder v{} initialized.", get_timestamp(), env!("CARGO_PKG_VERSION")),
            format!("[{}] 🎙️ WASAPI Audio Engine ready: High-resolution hardware loopback active.", get_timestamp()),
            format!("[{}] 🎬 Replay Synchronizer ready: Watching for League replay API (:2999).", get_timestamp()),
            format!("[{}] ⚡ Auto-Detect Monitor active: Monitoring LCU gameflow phases.", get_timestamp()),
        ];
        let discord_voice = Arc::new(DiscordVoiceState::new());
        let ipc_stop = Arc::new(AtomicBool::new(false));
        discord_voice.clone().spawn_watcher(ipc_stop.clone());

        let preview_capture = Arc::new(Mutex::new(AudioCaptureManager::new()));
        let initial_cfg = AppConfig::load().unwrap_or_default();

        let prev_cap_startup = preview_capture.clone();
        tokio::task::spawn_blocking(move || {
            std::thread::sleep(std::time::Duration::from_millis(60));
            let cfg = AppConfig::load().unwrap_or_default();
            let mic_target = if cfg.mic_device_name.is_empty() || cfg.mic_device_name == "default" {
                None
            } else {
                Some(cfg.mic_device_name)
            };
            if let Ok(mut cap) = prev_cap_startup.lock() {
                let _ = cap.start_preview(mic_target.as_deref());
            }
        });

        let audio_levels = Arc::new(Mutex::new((0.0f32, 0.0f32)));
        let rec_active = Arc::new(AtomicBool::new(false));

        // Spawn background audio preview monitor loop to keep VU meter responsive when idle
        let prev_cap_clone = preview_capture.clone();
        let levels_clone = audio_levels.clone();
        let rec_active_clone = rec_active.clone();
        let stop_preview_loop = ipc_stop.clone();

        tokio::spawn(async move {
            while !stop_preview_loop.load(Ordering::Relaxed) {
                if !rec_active_clone.load(Ordering::Relaxed) {
                    let peaks = {
                        if let Ok(cap) = prev_cap_clone.lock() {
                            cap.get_peaks()
                        } else {
                            (0.0, 0.0)
                        }
                    };
                    if let Ok(mut lk) = levels_clone.lock() {
                        *lk = peaks;
                    }
                }
                tokio::time::sleep(tokio::time::Duration::from_millis(40)).await;
            }
        });

        Self {
            rec_stop: Arc::new(AtomicBool::new(false)),
            rep_stop: Arc::new(AtomicBool::new(false)),
            rec_active,
            rep_active: Arc::new(AtomicBool::new(false)),
            rec_status: Arc::new(Mutex::new(("IDLE".to_string(), "".to_string()))),
            rep_status: Arc::new(Mutex::new(("IDLE".to_string(), "".to_string()))),
            match_time: Arc::new(Mutex::new(None)),
            log_queue: Arc::new(Mutex::new(initial_logs)),
            current_player: Arc::new(Mutex::new(None)),
            replay_volume: Arc::new(Mutex::new(1.0)),
            replay_time: Arc::new(Mutex::new((0.0, 1800.0, 1.0))),
            audio_levels,
            discord_voice,
            preview_capture,
            app_handle: Arc::new(Mutex::new(None)),
            hud_preview_mode: Arc::new(Mutex::new(None)),
            hud_locked: Arc::new(AtomicBool::new(initial_cfg.hud_locked)),
            hud_overlay_enabled: Arc::new(AtomicBool::new(initial_cfg.hud_overlay_enabled)),
            base_game_time: Arc::new(Mutex::new(None)),
            active_game_patch: Arc::new(Mutex::new(None)),
        }
    }
}

impl AppState {
    pub fn log(&self, msg: &str) {
        info!("{}", msg);
    }

    pub fn start_recording(&self) {
        if self.rec_active.load(Ordering::SeqCst) {
            return;
        }
        self.rec_stop.store(false, Ordering::SeqCst);
        self.rec_active.store(true, Ordering::SeqCst);

        // Stop preview capture while recording is active to yield CPAL stream
        if let Ok(mut prev) = self.preview_capture.lock() {
            prev.stop_preview();
        }

        if let Ok(mut st) = self.rec_status.lock() {
            *st = ("WATCHING".to_string(), "Waiting for match...".to_string());
        }
        if let Ok(mut mt) = self.match_time.lock() {
            *mt = None;
        }
        if let Ok(mut bt) = self.base_game_time.lock() {
            *bt = None;
        }
        self.log("🔴 Recording Watcher started. Polling League client LiveData (:2999)...");
        update_tray_status(self);

        let stop_sig = self.rec_stop.clone();
        let active_sig = self.rec_active.clone();
        let levels_sig = self.audio_levels.clone();
        let rec_status_sig = self.rec_status.clone();
        let clock_sig = self.match_time.clone();
        let muted_sig = self.discord_voice.is_muted.clone();
        let prev_cap_sig = self.preview_capture.clone();
        let base_time_sig = self.base_game_time.clone();
        let patch_sig = self.active_game_patch.clone();

        let cfg_opt = AppConfig::load().ok();
        if let Some(cfg) = cfg_opt {
            tokio::spawn(async move {
                let recorder = LeagueRecorder::new(cfg);
                recorder.run_watcher_loop(
                    stop_sig,
                    Some(levels_sig),
                    Some(rec_status_sig.clone()),
                    Some(clock_sig.clone()),
                    Some(muted_sig),
                    Some(base_time_sig),
                    Some(patch_sig),
                ).await;
                active_sig.store(false, Ordering::SeqCst);
                if let Ok(mut st) = rec_status_sig.lock() {
                    *st = ("IDLE".to_string(), "".to_string());
                }
                if let Ok(mut mt) = clock_sig.lock() {
                    *mt = None;
                }
                // Resume preview monitor when recording stops
                let mic_name = AppConfig::load().ok().map(|c| c.mic_device_name).unwrap_or_default();
                let target = if mic_name.is_empty() || mic_name == "default" { None } else { Some(mic_name) };
                if let Ok(mut prev) = prev_cap_sig.lock() {
                    let _ = prev.start_preview(target.as_deref());
                }
                info!("⏹ Recording Watcher stopped.");
            });
        }
    }

    pub fn stop_recording(&self) {
        self.rec_stop.store(true, Ordering::SeqCst);
        self.rec_active.store(false, Ordering::SeqCst);
        if let Ok(mut st) = self.rec_status.lock() {
            *st = ("IDLE".to_string(), "".to_string());
        }
        if let Ok(mut mt) = self.match_time.lock() {
            *mt = None;
        }
        if let Ok(mut bt) = self.base_game_time.lock() {
            *bt = None;
        }
        // Resume preview monitor
        let mic_name = AppConfig::load().ok().map(|c| c.mic_device_name).unwrap_or_default();
        let target = if mic_name.is_empty() || mic_name == "default" { None } else { Some(mic_name) };
        if let Ok(mut prev) = self.preview_capture.lock() {
            let _ = prev.start_preview(target.as_deref());
        }
        self.log("⏹ Stopping Recording Watcher...");
        update_tray_status(self);
    }

    pub fn start_replay(&self) {
        if self.rep_active.load(Ordering::SeqCst) {
            return;
        }
        self.rep_stop.store(false, Ordering::SeqCst);
        self.rep_active.store(true, Ordering::SeqCst);
        if let Ok(mut st) = self.rep_status.lock() {
            *st = ("WATCHING".to_string(), "Waiting for replay...".to_string());
        }
        if let Ok(mut mt) = self.match_time.lock() {
            *mt = None;
        }
        if let Ok(mut bt) = self.base_game_time.lock() {
            *bt = None;
        }
        self.log("▶ Replay Auto-Sync started. Monitoring running .rofl processes...");
        update_tray_status(self);

        let stop_sig = self.rep_stop.clone();
        let active_sig = self.rep_active.clone();
        let rep_status_sig = self.rep_status.clone();
        let clock_sig = self.match_time.clone();
        let player_sink = self.current_player.clone();
        let vol_sink = self.replay_volume.clone();
        let time_sink = self.replay_time.clone();
        let app_handle_sig = self.app_handle.clone();

        let cfg_opt = AppConfig::load().ok();
        if let Some(cfg) = cfg_opt {
            tokio::spawn(async move {
                let synchronizer = ReplaySynchronizer::new(cfg);
                synchronizer.run_sync_watcher(
                    stop_sig,
                    Some(rep_status_sig.clone()),
                    Some(clock_sig.clone()),
                    Some(player_sink),
                    Some(vol_sink),
                    Some(time_sink.clone()),
                ).await;
                active_sig.store(false, Ordering::SeqCst);
                if let Ok(mut st) = rep_status_sig.lock() {
                    *st = ("IDLE".to_string(), "".to_string());
                }
                if let Ok(mut mt) = clock_sig.lock() {
                    *mt = None;
                }
                if let Ok(mut t) = time_sink.lock() {
                    *t = (0.0, 1800.0, 1.0);
                }
                use tauri::Manager;
                if let Ok(handle_guard) = app_handle_sig.lock() {
                    if let Some(ref handle) = *handle_guard {
                        if let Some(win) = handle.get_webview_window("main") {
                            let _ = win.eval("if (window.onReplayStopped) window.onReplayStopped();");
                        }
                    }
                }
                info!("⏹ Replay Auto-Sync stopped.");
            });
        }
    }

    pub fn stop_replay(&self) {
        self.rep_stop.store(true, Ordering::SeqCst);
        self.rep_active.store(false, Ordering::SeqCst);
        if let Ok(mut st) = self.rep_status.lock() {
            *st = ("IDLE".to_string(), "".to_string());
        }
        if let Ok(mut mt) = self.match_time.lock() {
            *mt = None;
        }
        if let Ok(mut bt) = self.base_game_time.lock() {
            *bt = None;
        }
        if let Ok(mut t) = self.replay_time.lock() {
            *t = (0.0, 1800.0, 1.0);
        }
        use tauri::Manager;
        if let Ok(handle_guard) = self.app_handle.lock() {
            if let Some(ref handle) = *handle_guard {
                if let Some(win) = handle.get_webview_window("main") {
                    let _ = win.eval("if (window.onReplayStopped) window.onReplayStopped();");
                }
            }
        }
        self.log("⏹ Stopping Replay Auto-Sync...");
        update_tray_status(self);
    }
}

#[derive(Serialize, Deserialize)]
struct StatusDto {
    league_running: bool,
    in_game: bool,
    phase: String,
    is_recording_watcher: bool,
    is_replay_watcher: bool,
    rec_status: String,
    rec_detail: String,
    rep_status: String,
    rep_detail: String,
    match_clock: Option<String>,
    replay_volume: u32,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
struct RecordingItem {
    filename: String,
    size_str: String,
    date_str: String,
    game_id: String,
    team_tag: String,
    notes: String,
    game_patch: String,
    recorded_at: String,
}

#[tauri::command]
async fn get_status(state: tauri::State<'_, Arc<AppState>>) -> Result<StatusDto, String> {
    let (client_running, game_running) = blocking(|| (
        LcuCredentials::is_client_running(),
        LcuCredentials::is_game_process_running(),
    )).await.unwrap_or((false, false));

    let mut phase = "Offline".to_string();
    if client_running {
        if let Some(creds) = LcuCredentials::find_active() {
            if let Ok(p) = creds.get_gameflow_phase().await {
                phase = p;
            } else {
                phase = "Client Active".to_string();
            }
        } else {
            phase = "Client Active".to_string();
        }
    } else if game_running {
        phase = "InProgress".to_string();
    }

    let rec_stat = state.rec_status.lock().map(|g| g.clone()).unwrap_or_else(|_| ("IDLE".to_string(), "".to_string()));
    let rep_stat = state.rep_status.lock().map(|g| g.clone()).unwrap_or_else(|_| ("IDLE".to_string(), "".to_string()));
    let clk = state.match_time.lock().map(|g| g.clone()).unwrap_or(None);

    let vol_norm = state.replay_volume.lock().map(|v| *v).unwrap_or(0.8);
    let vol_int = (vol_norm * 100.0).round() as u32;

    Ok(StatusDto {
        league_running: client_running || game_running,
        in_game: game_running || phase == "InProgress",
        phase,
        is_recording_watcher: state.rec_active.load(Ordering::SeqCst),
        is_replay_watcher: state.rep_active.load(Ordering::SeqCst),
        rec_status: rec_stat.0,
        rec_detail: rec_stat.1,
        rep_status: rep_stat.0,
        rep_detail: rep_stat.1,
        match_clock: clk,
        replay_volume: vol_int,
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
async fn get_recordings() -> Vec<RecordingItem> {
    blocking(get_recordings_sync).await.unwrap_or_default()
}

fn get_recordings_sync() -> Vec<RecordingItem> {
    let mut items = Vec::new();
    if let Ok(cfg) = AppConfig::load() {
        if let Ok(entries) = std::fs::read_dir(&cfg.record_folder) {
            for entry in entries.flatten() {
                let path = entry.path();
                if path.extension().and_then(|s| s.to_str()).map(|ext| ext.eq_ignore_ascii_case("mp3")).unwrap_or(false) {
                    let filename = path.file_name().unwrap_or_default().to_string_lossy().to_string();
                    let metadata = entry.metadata().ok();
                    let size = metadata.as_ref().map(|m| m.len()).unwrap_or(0);
                    let size_str = format!("{:.1} MB", size as f64 / 1_048_576.0);

                    let mtime_str = metadata
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

                    let mut meta = ScrimMetadata::read_from_mp3(&path).unwrap_or_else(|_| {
                        let stem = path.file_stem().unwrap_or_default().to_string_lossy().to_string();
                        ScrimMetadata {
                            game_id: stem,
                            team_tag: String::new(),
                            notes: String::new(),
                            game_patch: String::new(),
                            recorded_at: String::new(),
                        }
                    });

                    // Auto-clean legacy "match length" comment tags if present
                    let lower_notes = meta.notes.to_lowercase();
                    if lower_notes.contains("match length") || lower_notes.starts_with("match ended at") {
                        meta.notes.clear();
                        let _ = meta.write_to_mp3(&path);
                    }

                    let date_display = if !meta.recorded_at.is_empty() {
                        meta.recorded_at.clone()
                    } else {
                        mtime_str
                    };

                    items.push(RecordingItem {
                        filename,
                        size_str,
                        date_str: date_display,
                        game_id: meta.game_id,
                        team_tag: meta.team_tag,
                        notes: meta.notes,
                        game_patch: meta.game_patch,
                        recorded_at: meta.recorded_at,
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
fn save_recording_metadata(filename: String, metadata: ScrimMetadata, state: tauri::State<'_, Arc<AppState>>) -> Result<(), String> {
    if let Ok(cfg) = AppConfig::load() {
        let path = cfg.record_folder.join(&filename);
        if path.exists() {
            metadata.write_to_mp3(&path).map_err(|e| format!("Failed to write ID3 metadata: {}", e))?;
            state.log(&format!("📝 Updated scrim details for {}", filename));
            return Ok(());
        }
    }
    Err("Recording file not found on disk".to_string())
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
            state.log(&format!("▶ Playing scrimmage audio: {}", filename));
            let player_lock = state.current_player.clone();
            let vol = *state.replay_volume.lock().unwrap();

            // Stop any existing playback first
            if let Ok(mut lk) = player_lock.lock() {
                if let Some(ref p) = *lk {
                    p.stop();
                }
                *lk = None;
            }

            match RodioPlayer::try_new() {
                Ok(mut player) => {
                    player.set_volume(vol);
                    match player.load_file(&path) {
                        Ok(_) => {
                            player.play();
                            if let Ok(mut lk) = player_lock.lock() {
                                *lk = Some(player);
                            }
                        }
                        Err(e) => {
                            state.log(&format!("⚠️ Failed to decode audio {}: {}", filename, e));
                        }
                    }
                }
                Err(e) => {
                    state.log(&format!("⚠️ Failed to initialize audio output device: {}", e));
                }
            }
        } else {
            state.log(&format!("⚠️ Recording file not found on disk: {}", filename));
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
async fn get_audio_devices() -> Vec<String> {
    blocking(AudioCaptureManager::get_available_microphones).await.unwrap_or_default()
}

#[tauri::command]
fn get_config() -> Result<AppConfig, String> {
    AppConfig::load().map_err(|e| e.to_string())
}

#[tauri::command]
fn save_config(config: AppConfig, state: tauri::State<'_, Arc<AppState>>) -> Result<(), String> {
    state.hud_locked.store(config.hud_locked, Ordering::SeqCst);
    state.hud_overlay_enabled.store(config.hud_overlay_enabled, Ordering::SeqCst);
    let mic_name = config.mic_device_name.clone();
    config.save().map_err(|e| e.to_string())?;
    state.log("⚙️ Settings saved successfully.");

    // Update live preview monitor with new microphone selection if recording is idle
    if !state.rec_active.load(Ordering::Relaxed) {
        let target = if mic_name.is_empty() || mic_name == "default" {
            None
        } else {
            Some(mic_name)
        };
        if let Ok(mut prev) = state.preview_capture.lock() {
            let _ = prev.start_preview(target.as_deref());
        }
    }

    Ok(())
}

#[tauri::command]
fn set_replay_volume(volume: u32, state: tauri::State<'_, Arc<AppState>>, app: tauri::AppHandle) {
    let vol_norm = (volume as f32 / 100.0).clamp(0.0, 2.0);
    *state.replay_volume.lock().unwrap() = vol_norm;
    if let Some(ref mut p) = *state.current_player.lock().unwrap() {
        p.set_volume(vol_norm);
    }
    if let Ok(mut cfg) = AppConfig::load() {
        if cfg.replay_volume != volume {
            cfg.replay_volume = volume;
            let _ = cfg.save();
        }
    }
    use tauri::Manager;
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.eval(&format!("if (window.onVolumeChanged) window.onVolumeChanged({});", volume));
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

#[derive(Serialize, Deserialize)]
struct AudioLevelsDto {
    app_level: f32,
    mic_level: f32,
    is_discord_muted: bool,
}

#[tauri::command]
fn get_audio_levels(state: tauri::State<'_, Arc<AppState>>) -> AudioLevelsDto {
    let (mic, app) = *state.audio_levels.lock().unwrap();
    let is_muted = state.discord_voice.is_muted();
    AudioLevelsDto {
        app_level: app,
        mic_level: mic,
        is_discord_muted: is_muted,
    }
}

#[tauri::command]
async fn check_replay_api_status() -> bool {
    blocking(|| ReplayConfigManager::check_replay_api_status().0).await.unwrap_or(false)
}

#[derive(Serialize, Deserialize)]
struct SystemHealthDto {
    discord_running: bool,
    league_running: bool,
    replay_api_enabled: bool,
}

#[tauri::command]
async fn get_system_health() -> SystemHealthDto {
    blocking(get_system_health_sync).await.unwrap_or(SystemHealthDto {
        discord_running: false,
        league_running: false,
        replay_api_enabled: false,
    })
}

fn get_system_health_sync() -> SystemHealthDto {
    let mut discord_running = false;
    let mut league_running = false;

    if let Ok(mut sys) = sysinfo::System::new() {
        sys.refresh_processes_specifics(
            sysinfo::ProcessesToUpdate::All,
            true,
            sysinfo::ProcessRefreshKind::nothing(),
        );

        for (_pid, proc) in sys.processes() {
            let name = proc.name().to_string_lossy().to_lowercase();
            if name.contains("discord") {
                discord_running = true;
            }
            if name.contains("leagueclient") || name.contains("league of legends") {
                league_running = true;
            }
            if discord_running && league_running {
                break;
            }
        }
    }

    let replay_api_enabled = ReplayConfigManager::check_replay_api_status().0;

    SystemHealthDto {
        discord_running,
        league_running,
        replay_api_enabled,
    }
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct TelemetryDto {
    // Record Section (Match & Recorder parameters)
    pub match_state: String,
    pub game_time: String,
    pub base_time: String,
    pub audio_offset: String,
    pub game_id: String,
    pub game_patch: String,

    // Replay Section (Replay parameters)
    pub replay_file: String,
    pub replay_time: String,
    pub player_time: String,
    pub replay_drift: String,
    pub replay_speed: String,
    pub replay_audio: String,

    // System Usage & Version
    pub ram_usage: String,
    pub cpu_usage: String,
    pub app_version: String,
}

#[tauri::command]
fn get_telemetry(state: tauri::State<'_, Arc<AppState>>) -> TelemetryDto {
    let rec_active = state.rec_active.load(Ordering::SeqCst);
    let rep_active = state.rep_active.load(Ordering::SeqCst);
    let rec_stat = state.rec_status.lock().map(|g| g.clone()).unwrap_or_else(|_| ("IDLE".to_string(), "".to_string()));
    let rep_stat = state.rep_status.lock().map(|g| g.clone()).unwrap_or_else(|_| ("IDLE".to_string(), "".to_string()));
    let clk = state.match_time.lock().map(|g| g.clone()).unwrap_or(None);
    let base_time_val = state.base_game_time.lock().map(|g| g.clone()).unwrap_or(None).unwrap_or_else(|| "00:00".to_string());
    let patch_val = state.active_game_patch.lock().map(|g| g.clone()).unwrap_or(None).unwrap_or_else(|| "—".to_string());

    // 1. Record Section (Match & Recorder parameters)
    let (match_state, game_time, base_time, audio_offset, game_id, game_patch) = if rec_active {
        match rec_stat.0.as_str() {
            "RECORDING" => {
                let time_display = clk.clone().unwrap_or_else(|| "00:00".to_string());
                let gid = if !rec_stat.1.is_empty() && rec_stat.1.contains('#') {
                    rec_stat.1.split('#').nth(1).and_then(|s| s.split(' ').next()).unwrap_or("Detecting...").to_string()
                } else {
                    "Detecting...".to_string()
                };
                (
                    "RECORDING 🔴".to_string(),
                    format!("Live ({})", time_display),
                    base_time_val,
                    "—".to_string(),
                    gid,
                    if patch_val != "—" { patch_val } else { "Detecting...".to_string() },
                )
            }
            "PAUSED" => {
                let time_display = clk.clone().unwrap_or_else(|| "00:00".to_string());
                let gid = if !rec_stat.1.is_empty() && rec_stat.1.contains('#') {
                    rec_stat.1.split('#').nth(1).and_then(|s| s.split(' ').next()).unwrap_or("Detecting...").to_string()
                } else {
                    "Detecting...".to_string()
                };
                (
                    "PAUSED ⏸".to_string(),
                    format!("Paused ({})", time_display),
                    base_time_val,
                    "—".to_string(),
                    gid,
                    if patch_val != "—" { patch_val } else { "Detecting...".to_string() },
                )
            }
            _ => (
                "LISTENING ⏳".to_string(),
                "Waiting for match...".to_string(),
                "—".to_string(),
                "—".to_string(),
                "—".to_string(),
                if patch_val != "—" { patch_val } else { "—".to_string() },
            )
        }
    } else {
        (
            "IDLE".to_string(),
            "Inactive".to_string(),
            "—".to_string(),
            "—".to_string(),
            "—".to_string(),
            if patch_val != "—" { patch_val } else { "—".to_string() },
        )
    };

    // 2. Replay Section (Replay parameters)
    let (replay_file, replay_time, player_time, replay_drift, replay_speed, replay_audio) = if rep_active {
        match rep_stat.0.as_str() {
            "SYNCING" => {
                let time_display = clk.unwrap_or_else(|| "00:00".to_string());
                let file = if !rep_stat.1.is_empty() { rep_stat.1.clone() } else { "Active Replay".to_string() };
                (
                    file.clone(),
                    format!("{} ({})", time_display, time_display),
                    time_display,
                    "0.0 ms".to_string(),
                    "1.00x".to_string(),
                    if file.contains(".mp3") { file } else { "Synced".to_string() },
                )
            }
            "PAUSED" => {
                let time_display = clk.unwrap_or_else(|| "00:00".to_string());
                (
                    rep_stat.1.clone(),
                    format!("{} (PAUSED ⏸)", time_display),
                    time_display,
                    "0.0 ms".to_string(),
                    "0.00x".to_string(),
                    "Paused".to_string(),
                )
            }
            _ => (
                "Waiting for .rofl replay...".to_string(),
                "Waiting for Replay API (:2999)...".to_string(),
                "—".to_string(),
                "0.0 ms (Watching)".to_string(),
                "1.00x".to_string(),
                "—".to_string(),
            )
        }
    } else {
        (
            "—".to_string(),
            "Inactive".to_string(),
            "—".to_string(),
            "—".to_string(),
            "—".to_string(),
            "—".to_string(),
        )
    };

    // 3. System Usage & Version
    let mut ram_usage = "0.0 MB".to_string();
    let mut cpu_usage = "0.0%".to_string();

    static SYSTEM_MONITOR: std::sync::Mutex<Option<(sysinfo::System, std::time::Instant)>> = std::sync::Mutex::new(None);

    if let Ok(pid) = sysinfo::get_current_pid() {
        if let Ok(mut lk) = SYSTEM_MONITOR.lock() {
            if lk.is_none() {
                if let Ok(mut sys) = sysinfo::System::new() {
                    sys.refresh_processes_specifics(
                        sysinfo::ProcessesToUpdate::Some(&[pid]),
                        true,
                        sysinfo::ProcessRefreshKind::nothing().with_memory().with_cpu(),
                    );
                    *lk = Some((sys, std::time::Instant::now()));
                }
            } else if let Some((ref mut sys, ref mut last_time)) = *lk {
                if last_time.elapsed() >= std::time::Duration::from_millis(250) {
                    sys.refresh_processes_specifics(
                        sysinfo::ProcessesToUpdate::Some(&[pid]),
                        true,
                        sysinfo::ProcessRefreshKind::nothing().with_memory().with_cpu(),
                    );
                    *last_time = std::time::Instant::now();
                }
                if let Some(proc) = sys.process(pid) {
                    let ram_mb = proc.memory() as f64 / (1024.0 * 1024.0);
                    ram_usage = format!("{:.1} MB", ram_mb);
                    let num_cpus = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(1) as f32;
                    let normalized_cpu = (proc.cpu_usage() / num_cpus).min(100.0);
                    cpu_usage = format!("{:.1}%", normalized_cpu);
                }
            }
        }
    }

    TelemetryDto {
        match_state,
        game_time,
        base_time,
        audio_offset,
        game_id,
        game_patch,
        replay_file,
        replay_time,
        player_time,
        replay_drift,
        replay_speed,
        replay_audio,
        ram_usage,
        cpu_usage,
        app_version: format!("v{}", league_voice_recorder::updater::CURRENT_VERSION),
    }
}

static OVERLAY_HWND: std::sync::atomic::AtomicIsize = std::sync::atomic::AtomicIsize::new(0);

#[cfg(windows)]
fn is_league_foreground() -> bool {
    unsafe extern "system" {
        fn GetForegroundWindow() -> isize;
        fn GetWindowThreadProcessId(hwnd: isize, lpdwProcessId: *mut u32) -> u32;
        fn OpenProcess(dwDesiredAccess: u32, bInheritHandle: i32, dwProcessId: u32) -> isize;
        fn QueryFullProcessImageNameW(hProcess: isize, dwFlags: u32, lpExeName: *mut u16, lpdwSize: *mut u32) -> i32;
        fn CloseHandle(hObject: isize) -> i32;
        fn GetWindowTextW(hWnd: isize, lpString: *mut u16, nMaxCount: i32) -> i32;
        fn GetAncestor(hwnd: isize, gaFlags: u32) -> isize;
    }
    let hwnd = unsafe { GetForegroundWindow() };
    if hwnd == 0 {
        return false;
    }

    // 1. If foreground window is the overlay HUD itself (or any WebView2 child inside it),
    // the user is interacting with / touching the HUD! Keep it visible and never blink or bug.
    let overlay_h = OVERLAY_HWND.load(Ordering::SeqCst);
    if overlay_h != 0 {
        const GA_ROOT: u32 = 2;
        const GA_ROOTOWNER: u32 = 3;
        let root = unsafe { GetAncestor(hwnd, GA_ROOT) };
        let root_owner = unsafe { GetAncestor(hwnd, GA_ROOTOWNER) };
        if hwnd == overlay_h || root == overlay_h || root_owner == overlay_h {
            return true;
        }
    }

    let mut pid: u32 = 0;
    unsafe { GetWindowThreadProcessId(hwnd, &mut pid) };
    if pid == 0 || pid == std::process::id() {
        return false;
    }

    const PROCESS_QUERY_LIMITED_INFORMATION: u32 = 0x1000;
    let h_proc = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
    if h_proc != 0 {
        let mut buf = [0u16; 512];
        let mut size = buf.len() as u32;
        let success = unsafe { QueryFullProcessImageNameW(h_proc, 0, buf.as_mut_ptr(), &mut size) };
        unsafe { CloseHandle(h_proc) };
        if success != 0 && size > 0 {
            let path_os = String::from_utf16_lossy(&buf[..size as usize]).to_lowercase();
            if path_os.ends_with("msedgewebview2.exe") || path_os.ends_with("leaguevoicerecorder.exe") {
                return false;
            }
            if path_os.ends_with("league of legends.exe") {
                return true;
            }
        }
    }

    // Fallback: check window title (for League match window)
    let mut title_buf = [0u16; 256];
    let len = unsafe { GetWindowTextW(hwnd, title_buf.as_mut_ptr(), 256) };
    if len > 0 {
        let title = String::from_utf16_lossy(&title_buf[..len as usize]).to_lowercase();
        if title.starts_with("league of legends") && !title.contains("recorder") && !title.contains("overlay") {
            return true;
        }
    }

    false
}

#[cfg(not(windows))]
fn is_league_foreground() -> bool {
    true
}

#[cfg(windows)]
#[repr(C)]
struct RECT {
    left: i32,
    top: i32,
    right: i32,
    bottom: i32,
}

#[cfg(windows)]
unsafe extern "system" {
    fn GetWindowLongPtrW(hWnd: isize, nIndex: i32) -> isize;
    fn SetWindowLongPtrW(hWnd: isize, nIndex: i32, dwNewLong: isize) -> isize;
    fn CallWindowProcW(
        lpPrevWndFunc: isize,
        hWnd: isize,
        Msg: u32,
        wParam: usize,
        lParam: isize,
    ) -> isize;
    fn GetWindowRect(hWnd: isize, lpRect: *mut RECT) -> i32;
    fn mouse_event(dwFlags: u32, dx: u32, dy: u32, dwData: u32, dwExtraInfo: usize);
    fn MessageBeep(uType: u32) -> i32;
    fn ShowWindow(hWnd: isize, nCmdShow: i32) -> i32;
    fn SetForegroundWindow(hWnd: isize) -> i32;
}

#[cfg(windows)]
static PREV_MAIN_WND_PROC: std::sync::atomic::AtomicIsize = std::sync::atomic::AtomicIsize::new(0);
#[cfg(windows)]
static MAIN_APP_HANDLE: std::sync::Mutex<Option<tauri::AppHandle>> = std::sync::Mutex::new(None);
#[cfg(windows)]
static TITLE_MINIMIZE_CLICKED: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

#[cfg(windows)]
unsafe extern "system" fn main_window_subclass_proc(
    hwnd: isize,
    msg: u32,
    wparam: usize,
    lparam: isize,
) -> isize {
    const WM_NCLBUTTONDOWN: u32 = 0x00A1;
    const WM_NCLBUTTONUP: u32 = 0x00A2;
    const WM_SYSCOMMAND: u32 = 0x0112;
    const HTMINBUTTON: usize = 8;
    const SC_MINIMIZE: usize = 0xF020;

    if msg == WM_NCLBUTTONDOWN || msg == WM_NCLBUTTONUP {
        if wparam == HTMINBUTTON {
            TITLE_MINIMIZE_CLICKED.store(true, Ordering::SeqCst);
        }
    } else if msg == WM_SYSCOMMAND {
        if (wparam & 0xFFF0) == SC_MINIMIZE {
            let was_button_clicked = TITLE_MINIMIZE_CLICKED.swap(false, Ordering::SeqCst);
            let is_from_min_button = was_button_clicked || {
                if lparam != 0 {
                    let x = (lparam & 0xFFFF) as i16 as i32;
                    let y = ((lparam >> 16) & 0xFFFF) as i16 as i32;
                    let mut rect = RECT { left: 0, top: 0, right: 0, bottom: 0 };
                    unsafe { GetWindowRect(hwnd, &mut rect) };
                    x >= rect.left && x <= rect.right && y >= rect.top && y <= (rect.top + 60)
                } else {
                    false
                }
            };

            if is_from_min_button {
                let cfg = AppConfig::load().unwrap_or_default();
                if cfg.minimize_to_tray {
                    unsafe { ShowWindow(hwnd, 0) }; // SW_HIDE
                    if let Ok(guard) = MAIN_APP_HANDLE.lock() {
                        if let Some(ref handle) = *guard {
                            show_toast_announcement(handle);
                        }
                    }
                    return 0; // Handled: avoid standard minimize animation to taskbar
                }
            }
        }
    }

    let prev = PREV_MAIN_WND_PROC.load(Ordering::SeqCst);
    if prev != 0 {
        unsafe { CallWindowProcW(prev, hwnd, msg, wparam, lparam) }
    } else {
        0
    }
}

#[cfg(windows)]
static PREV_OVERLAY_WND_PROC: std::sync::atomic::AtomicIsize = std::sync::atomic::AtomicIsize::new(0);

#[cfg(windows)]
unsafe extern "system" fn overlay_window_subclass_proc(
    hwnd: isize,
    msg: u32,
    wparam: usize,
    lparam: isize,
) -> isize {
    const WM_CONTEXTMENU: u32 = 0x007B;
    const WM_NCRBUTTONDOWN: u32 = 0x00A4;
    const WM_NCRBUTTONUP: u32 = 0x00A5;
    const WM_SYSCOMMAND: u32 = 0x0112;
    const SC_MINIMIZE: usize = 0xF020;
    const SC_MAXIMIZE: usize = 0xF030;

    // 1. Completely suppress right-click context menu and non-client system menu
    if msg == WM_CONTEXTMENU || msg == WM_NCRBUTTONDOWN || msg == WM_NCRBUTTONUP {
        return 0;
    }

    // 2. Reject any attempt to minimize or maximize the overlay window
    if msg == WM_SYSCOMMAND {
        let cmd = wparam & 0xFFF0;
        if cmd == SC_MINIMIZE || cmd == SC_MAXIMIZE {
            return 0;
        }
    }

    let prev = PREV_OVERLAY_WND_PROC.load(Ordering::SeqCst);
    if prev != 0 {
        unsafe { CallWindowProcW(prev, hwnd, msg, wparam, lparam) }
    } else {
        0
    }
}

#[cfg(windows)]
fn restore_main_window_robust(win: &tauri::WebviewWindow) {
    if let Ok(hwnd) = win.hwnd() {
        unsafe {
            ShowWindow(hwnd.0 as isize, 9); // SW_RESTORE
            SetForegroundWindow(hwnd.0 as isize);
        }
    }
    let _ = win.show();
    let _ = win.unminimize();
    let _ = win.set_focus();
}

#[cfg(not(windows))]
fn restore_main_window_robust(win: &tauri::WebviewWindow) {
    let _ = win.show();
    let _ = win.unminimize();
    let _ = win.set_focus();
}

#[cfg(windows)]
const GWL_EXSTYLE: i32 = -20;
#[cfg(windows)]
const WS_EX_TRANSPARENT: isize = 0x00000020;
#[cfg(windows)]
const WS_EX_NOACTIVATE: isize = 0x08000000;
#[cfg(windows)]
const WS_EX_TOOLWINDOW: isize = 0x00000080;

#[cfg(windows)]
const MOUSEEVENTF_LEFTDOWN: u32 = 0x0002;
#[cfg(windows)]
const MOUSEEVENTF_LEFTUP: u32 = 0x0004;
#[cfg(windows)]
const MOUSEEVENTF_RIGHTDOWN: u32 = 0x0008;
#[cfg(windows)]
const MOUSEEVENTF_RIGHTUP: u32 = 0x0010;

#[tauri::command]
fn overlay_forward_click(app: tauri::AppHandle, button: String) -> Result<(), String> {
    #[cfg(windows)]
    {
        use tauri::Manager;
        if let Some(win) = app.get_webview_window("overlay") {
            if let Ok(hwnd) = win.hwnd() {
                let hwnd_val = hwnd.0 as isize;
                unsafe {
                    let cur = GetWindowLongPtrW(hwnd_val, GWL_EXSTYLE);
                    SetWindowLongPtrW(hwnd_val, GWL_EXSTYLE, cur | WS_EX_TRANSPARENT);

                    if button == "right" {
                        mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0);
                        mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0);
                    } else {
                        mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0);
                        mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0);
                    }
                }

                std::thread::spawn(move || {
                    std::thread::sleep(std::time::Duration::from_millis(30));
                    unsafe {
                        let cur_now = GetWindowLongPtrW(hwnd_val, GWL_EXSTYLE);
                        SetWindowLongPtrW(hwnd_val, GWL_EXSTYLE, cur_now & !WS_EX_TRANSPARENT);
                    }
                });
            }
        }
    }
    Ok(())
}

#[tauri::command]
fn show_main_window(app: tauri::AppHandle) -> Result<(), String> {
    use tauri::Manager;
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.show();
        let _ = win.set_focus();
    }
    Ok(())
}

#[tauri::command]
fn restore_main_window(app: tauri::AppHandle) -> Result<(), String> {
    use tauri::Manager;
    if let Some(win) = app.get_webview_window("main") {
        restore_main_window_robust(&win);
    }
    if let Some(toast) = app.get_webview_window("toast") {
        let _ = toast.hide();
    }
    Ok(())
}

static LAST_TRAY_STATUS_KEY: std::sync::Mutex<String> = std::sync::Mutex::new(String::new());

fn update_tray_status(state: &AppState) {
    let maybe_handle = state.app_handle.lock().ok().and_then(|h| h.clone());
    let Some(handle) = maybe_handle else { return };

    let is_rec = state.rec_active.load(Ordering::SeqCst);
    let is_rep = state.rep_active.load(Ordering::SeqCst);
    let clk_str = state.match_time.lock().ok().and_then(|c| c.clone()).unwrap_or_else(|| "00:00".to_string());
    let rec_stat = state.rec_status.lock().map(|s| s.0.clone()).unwrap_or_default();
    let rep_stat = state.rep_status.lock().map(|s| s.0.clone()).unwrap_or_default();

    // 1. Tooltip string (contains live timer if in match/replay)
    let tooltip_str = if is_rec {
        if rec_stat == "PAUSED" {
            format!("League Voice Recorder: ⏸ Paused ({})", clk_str)
        } else if rec_stat == "RECORDING" {
            format!("League Voice Recorder: 🔴 Recording Scrim ({})", clk_str)
        } else {
            "League Voice Recorder: 🔴 Recording Watcher Active".to_string()
        }
    } else if is_rep {
        if rep_stat == "PAUSED" {
            format!("League Voice Recorder: ⏸ Replay Paused ({})", clk_str)
        } else if rep_stat == "SYNCING" {
            format!("League Voice Recorder: ▶ Replay Auto-Sync ({})", clk_str)
        } else {
            "League Voice Recorder: 🎬 Replay Watching".to_string()
        }
    } else {
        "League Voice Recorder: Idle".to_string()
    };

    // 2. Menu status string (tracks operational state without rapid 1-sec ticks so popup menu remains stable)
    let menu_status_str = if is_rec {
        if rec_stat == "PAUSED" {
            "League Voice Recorder: ⏸ Match Paused".to_string()
        } else if rec_stat == "RECORDING" {
            "League Voice Recorder: 🔴 Recording Scrim".to_string()
        } else {
            "League Voice Recorder: 🔴 Recording Watcher Active".to_string()
        }
    } else if is_rep {
        if rep_stat == "PAUSED" {
            "League Voice Recorder: ⏸ Replay Paused".to_string()
        } else if rep_stat == "SYNCING" {
            "League Voice Recorder: ▶ Replay Auto-Sync".to_string()
        } else {
            "League Voice Recorder: 🎬 Replay Watching".to_string()
        }
    } else {
        "League Voice Recorder: Idle".to_string()
    };

    if let Some(tray) = handle.tray_by_id("tray") {
        let _ = tray.set_tooltip(Some(&tooltip_str));

        // Only rebuild menu when menu status string actually transitions to a new state
        let mut should_update_menu = false;
        if let Ok(mut last) = LAST_TRAY_STATUS_KEY.lock() {
            if *last != menu_status_str {
                *last = menu_status_str.clone();
                should_update_menu = true;
            }
        }

        if should_update_menu {
            let app_handle = handle.clone();
            let status_text = menu_status_str.clone();
            let tray_clone = tray.clone();
            let _ = handle.run_on_main_thread(move || {
                use tauri::menu::{Menu, MenuItem};
                if let Ok(open_item) = MenuItem::with_id(&app_handle, "open", "Open", true, None::<&str>) {
                    if let Ok(status_item) = MenuItem::with_id(&app_handle, "status", &status_text, false, None::<&str>) {
                        if let Ok(exit_item) = MenuItem::with_id(&app_handle, "exit", "Exit", true, None::<&str>) {
                            if let Ok(fresh_menu) = Menu::with_items(&app_handle, &[&open_item, &status_item, &exit_item]) {
                                let _ = tray_clone.set_menu(Some(fresh_menu));
                            }
                        }
                    }
                }
            });
        }
    }
}

fn create_aux_windows(app: &tauri::AppHandle) {
    use tauri::Manager;
    for label in ["overlay", "toast"] {
        if app.get_webview_window(label).is_some() {
            continue;
        }
        if let Some(cfg) = app.config().app.windows.iter().find(|w| w.label == label) {
            let _ = tauri::WebviewWindowBuilder::from_config(app, cfg).and_then(|b| b.build());
        }
    }
}

fn setup_overlay(app: &tauri::AppHandle) {
    use tauri::Manager;
    static OVERLAY_SETUP_DONE: AtomicBool = AtomicBool::new(false);
    if OVERLAY_SETUP_DONE.swap(true, Ordering::SeqCst) {
        return;
    }
    let Some(overlay_win) = app.get_webview_window("overlay") else {
        OVERLAY_SETUP_DONE.store(false, Ordering::SeqCst);
        return;
    };
    let cfg = AppConfig::load().unwrap_or_default();
    if let Ok(Some(monitor)) = overlay_win.primary_monitor() {
        let scale = monitor.scale_factor();
        let screen_w = monitor.size().width as f64 / scale;
        let target_x = if cfg.hud_x >= 0 { cfg.hud_x as f64 } else { screen_w - 350.0 - 40.0 };
        let target_y = if cfg.hud_y >= 0 { cfg.hud_y as f64 } else { 40.0 };
        let _ = overlay_win.set_position(tauri::LogicalPosition::new(target_x, target_y));
    }
    let _ = overlay_win.set_background_color(Some(tauri::webview::Color(0, 0, 0, 0)));
    let _ = overlay_win.set_always_on_top(true);
    let _ = overlay_win.set_ignore_cursor_events(false);
    let _ = overlay_win.hide();

    #[cfg(windows)]
    if let Ok(hwnd) = overlay_win.hwnd() {
        let hwnd_val = hwnd.0 as isize;
        OVERLAY_HWND.store(hwnd_val, Ordering::SeqCst);
        unsafe {
            ShowWindow(hwnd_val, 0); // SW_HIDE (ensure overlay starts completely hidden)

            const GWL_STYLE: i32 = -16;
            const WS_MINIMIZEBOX: isize = 0x00020000;
            const WS_MAXIMIZEBOX: isize = 0x00010000;
            const WS_SYSMENU: isize = 0x00080000;

            let cur_style = GetWindowLongPtrW(hwnd_val, GWL_STYLE);
            SetWindowLongPtrW(hwnd_val, GWL_STYLE, cur_style & !WS_MINIMIZEBOX & !WS_MAXIMIZEBOX & !WS_SYSMENU);

            let cur_ex = GetWindowLongPtrW(hwnd_val, GWL_EXSTYLE);
            SetWindowLongPtrW(hwnd_val, GWL_EXSTYLE, cur_ex | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW);

            let prev = SetWindowLongPtrW(hwnd_val, -4, overlay_window_subclass_proc as *const () as isize);
            PREV_OVERLAY_WND_PROC.store(prev, Ordering::SeqCst);
        }
    }

    overlay_win.on_window_event(|event| {
        if let tauri::WindowEvent::Moved(pos) = event {
            if let Ok(mut cfg) = AppConfig::load() {
                cfg.hud_x = pos.x;
                cfg.hud_y = pos.y;
                let _ = cfg.save();
            }
        }
    });
}

fn show_toast_announcement(app: &tauri::AppHandle) {
    create_aux_windows(app);
    #[cfg(windows)]
    {
        unsafe { MessageBeep(0x00000040) }; // MB_ICONASTERISK
    }

    use tauri::Manager;
    if let Some(toast) = app.get_webview_window("toast") {
        if let Ok(Some(mon)) = toast.primary_monitor() {
            let scale = mon.scale_factor();
            let screen_w = mon.size().width as f64 / scale;
            let screen_h = mon.size().height as f64 / scale;
            let toast_x = screen_w - 360.0 - 24.0;
            let toast_y = screen_h - 76.0 - 55.0;
            let _ = toast.set_position(tauri::LogicalPosition::new(toast_x, toast_y));
        }
        let _ = toast.set_always_on_top(true);
        let _ = toast.show();

        let toast_clone = toast.clone();
        std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_millis(3500));
            let _ = toast_clone.hide();
        });
    }
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct HudDataDto {
    pub mode: String,
    pub time_str: String,
    pub mic_level: f32,
    pub discord_level: f32,
    pub is_paused: bool,
    pub is_muted: bool,
    pub is_discord_muted: bool,
    pub current_time: f64,
    pub total_duration: f64,
    pub speed: f32,
    pub volume: u32,
    pub locked: bool,
    pub opacity: f32,
    pub is_preview: bool,
    pub visible: bool,
}

#[tauri::command]
fn get_hud_data(state: tauri::State<'_, Arc<AppState>>) -> HudDataDto {
    let cfg = AppConfig::load().unwrap_or_default();
    let preview_mode = state.hud_preview_mode.lock().map(|m| m.clone()).unwrap_or(None);
    let cur_vol = state.replay_volume.lock().map(|v| (*v * 100.0).round() as u32).unwrap_or(cfg.replay_volume);

    if let Some(prev) = preview_mode {
        if prev == "REPLAY" {
            return HudDataDto {
                mode: "REPLAY".to_string(),
                time_str: "12:48".to_string(),
                mic_level: 0.0,
                discord_level: 0.0,
                is_paused: false,
                is_muted: false,
                is_discord_muted: false,
                current_time: 768.0,
                total_duration: 2250.0,
                speed: 1.0,
                volume: cur_vol,
                locked: cfg.hud_locked,
                opacity: cfg.hud_opacity,
                is_preview: true,
                visible: true,
            };
        } else {
            return HudDataDto {
                mode: "RECORD".to_string(),
                time_str: "12:48".to_string(),
                mic_level: 0.75,
                discord_level: 0.45,
                is_paused: false,
                is_muted: false,
                is_discord_muted: false,
                current_time: 0.0,
                total_duration: 1800.0,
                speed: 1.0,
                volume: cur_vol,
                locked: cfg.hud_locked,
                opacity: cfg.hud_opacity,
                is_preview: true,
                visible: true,
            };
        }
    }

    let rec_active = state.rec_active.load(Ordering::SeqCst);
    let rep_active = state.rep_active.load(Ordering::SeqCst);
    let rec_stat = state.rec_status.lock().map(|g| g.0.clone()).unwrap_or_default();
    let rep_stat = state.rep_status.lock().map(|g| g.0.clone()).unwrap_or_default();
    let clk = state.match_time.lock().map(|g| g.clone()).unwrap_or(None).unwrap_or_else(|| "00:00".to_string());
    let levels = state.audio_levels.lock().map(|g| *g).unwrap_or((0.0, 0.0));
    let is_discord_muted = state.discord_voice.is_muted();
    let vol_norm = state.replay_volume.lock().map(|v| *v).unwrap_or(0.8);
    let vol_int = (vol_norm * 100.0).round() as u32;

    let mode = if rep_active && !rec_active {
        "REPLAY".to_string()
    } else {
        "RECORD".to_string()
    };

    let is_paused = rec_stat == "PAUSED" || rep_stat == "PAUSED";
    let (rep_cur_time, rep_total_dur, rep_speed) = state.replay_time.lock().map(|t| *t).unwrap_or((0.0, 1800.0, 1.0));

    HudDataDto {
        mode,
        time_str: clk,
        mic_level: levels.0,
        discord_level: levels.1,
        is_paused,
        is_muted: false,
        is_discord_muted,
        current_time: rep_cur_time,
        total_duration: rep_total_dur,
        speed: rep_speed as f32,
        volume: vol_int,
        locked: cfg.hud_locked,
        opacity: cfg.hud_opacity,
        is_preview: false,
        visible: rec_active || rep_active,
    }
}

#[tauri::command]
fn overlay_start_dragging(app: tauri::AppHandle) -> Result<(), String> {
    use tauri::Manager;
    if let Some(win) = app.get_webview_window("overlay") {
        let _ = win.start_dragging();
    }
    Ok(())
}

#[tauri::command]
fn overlay_preview_rec(app: tauri::AppHandle, state: tauri::State<'_, Arc<AppState>>) -> Result<(), String> {
    create_aux_windows(&app);
    setup_overlay(&app);
    use tauri::Manager;
    if let Ok(mut mode) = state.hud_preview_mode.lock() {
        *mode = Some("RECORD".to_string());
    }
    if let Some(win) = app.get_webview_window("overlay") {
        #[cfg(windows)]
        if let Ok(hwnd) = win.hwnd() {
            unsafe { ShowWindow(hwnd.0 as isize, 9); } // SW_RESTORE
        }
        let _ = win.unminimize();
        let _ = win.set_ignore_cursor_events(false);
        let _ = win.show();
        let _ = win.set_always_on_top(true);
    }
    Ok(())
}

#[tauri::command]
fn overlay_preview_replay(app: tauri::AppHandle, state: tauri::State<'_, Arc<AppState>>) -> Result<(), String> {
    create_aux_windows(&app);
    setup_overlay(&app);
    use tauri::Manager;
    if let Ok(mut mode) = state.hud_preview_mode.lock() {
        *mode = Some("REPLAY".to_string());
    }
    if let Some(win) = app.get_webview_window("overlay") {
        #[cfg(windows)]
        if let Ok(hwnd) = win.hwnd() {
            unsafe { ShowWindow(hwnd.0 as isize, 9); } // SW_RESTORE
        }
        let _ = win.unminimize();
        let _ = win.set_ignore_cursor_events(false);
        let _ = win.show();
        let _ = win.set_always_on_top(true);
    }
    Ok(())
}

#[tauri::command]
fn overlay_hide_preview(app: tauri::AppHandle, state: tauri::State<'_, Arc<AppState>>) -> Result<(), String> {
    use tauri::Manager;
    if let Ok(mut mode) = state.hud_preview_mode.lock() {
        *mode = None;
    }
    if let Some(win) = app.get_webview_window("overlay") {
        let _ = win.hide();
    }
    Ok(())
}

#[tauri::command]
fn overlay_set_locked(app: tauri::AppHandle, state: tauri::State<'_, Arc<AppState>>, locked: bool) -> Result<(), String> {
    use tauri::Manager;
    state.hud_locked.store(locked, Ordering::SeqCst);
    if let Ok(mut cfg) = AppConfig::load() {
        cfg.hud_locked = locked;
        cfg.hud_click_through = locked;
        let _ = cfg.save();
    }
    #[cfg(windows)]
    if let Some(win) = app.get_webview_window("overlay") {
        let _ = win.set_ignore_cursor_events(false);
        if let Ok(hwnd) = win.hwnd() {
            let hwnd_val = hwnd.0 as isize;
            unsafe {
                let cur = GetWindowLongPtrW(hwnd_val, GWL_EXSTYLE);
                if (cur & WS_EX_TRANSPARENT) != 0 {
                    SetWindowLongPtrW(hwnd_val, GWL_EXSTYLE, cur & !WS_EX_TRANSPARENT);
                }
            }
        }
    }
    Ok(())
}

#[tauri::command]
fn overlay_set_opacity(opacity: f32, app: tauri::AppHandle) -> Result<(), String> {
    if let Ok(mut cfg) = AppConfig::load() {
        cfg.hud_opacity = opacity;
        let _ = cfg.save();
    }
    use tauri::Manager;
    if let Some(win) = app.get_webview_window("overlay") {
        let _ = win.eval(&format!("if (window.setCardOpacity) window.setCardOpacity({});", opacity));
    }
    Ok(())
}

#[tauri::command]
fn overlay_reset_position(app: tauri::AppHandle) -> Result<(), String> {
    use tauri::Manager;
    if let Some(win) = app.get_webview_window("overlay") {
        #[cfg(windows)]
        if let Ok(hwnd) = win.hwnd() {
            unsafe { ShowWindow(hwnd.0 as isize, 9); } // SW_RESTORE (ensures window is unminimized)
        }
        let _ = win.unminimize();
        let _ = win.show();
        if let Ok(Some(mon)) = win.primary_monitor() {
            let scale = mon.scale_factor();
            let screen_w = mon.size().width as f64 / scale;
            let target_x = (screen_w - 350.0 - 40.0) as i32;
            let target_y = 40;
            let _ = win.set_position(tauri::LogicalPosition::new(target_x as f64, target_y as f64));
            if let Ok(mut cfg) = AppConfig::load() {
                cfg.hud_x = -1;
                cfg.hud_y = -1;
                let _ = cfg.save();
            }
        }
    }
    Ok(())
}

#[tauri::command]
async fn test_cloud_connection(
    url: String,
    mut key: String,
    state: tauri::State<'_, Arc<AppState>>,
) -> Result<league_voice_recorder::cloud_sync::CloudHealthStatus, String> {
    if key.is_empty() {
        if let Ok(cfg) = AppConfig::load() {
            if !cfg.remote_api_key.is_empty() {
                key = cfg.remote_api_key;
            }
        }
    }
    state.log(&format!("☁️ Testing remote scrim server connection to {}...", url));
    let client = league_voice_recorder::cloud_sync::CloudSyncClient::new(&url, &key);
    match client.check_health().await {
        Ok(status) => {
            state.log(&format!("✓ Remote scrim backend reachable: 200 OK (Role: {})", status.role));
            Ok(status)
        }
        Err(e) => {
            state.log(&format!("⚠️ Cloud sync connection failed: {}", e));
            Err(e)
        }
    }
}

#[tauri::command]
async fn cloud_get_role() -> Result<String, String> {
    let cfg = AppConfig::load().map_err(|e| e.to_string())?;
    if !cfg.remote_server_url.is_empty() && !cfg.remote_api_key.is_empty() {
        let client = league_voice_recorder::cloud_sync::CloudSyncClient::new(&cfg.remote_server_url, &cfg.remote_api_key);
        if let Ok(health) = client.check_health().await {
            return Ok(health.role);
        }
        return Ok(league_voice_recorder::cloud_sync::CloudSyncClient::determine_role(None, &cfg.remote_api_key));
    }
    Ok("unknown".to_string())
}

#[tauri::command]
async fn cloud_list_recordings(state: tauri::State<'_, Arc<AppState>>) -> Result<Vec<league_voice_recorder::cloud_sync::CloudRecordingDto>, String> {
    let cfg = AppConfig::load().map_err(|e| e.to_string())?;
    if cfg.remote_server_url.is_empty() || cfg.remote_api_key.is_empty() {
        return Err("Server URL or API Key not configured in Settings.".to_string());
    }
    let client = league_voice_recorder::cloud_sync::CloudSyncClient::new(&cfg.remote_server_url, &cfg.remote_api_key);
    state.log("☁️ Fetching scrim recordings index from remote server...");
    let items = client.list_recordings().await?;
    state.log(&format!("✓ Retrieved {} remote recording(s) from server.", items.len()));
    Ok(items)
}

#[tauri::command]
async fn cloud_download_recording(game_id: String, state: tauri::State<'_, Arc<AppState>>) -> Result<String, String> {
    let cfg = AppConfig::load().map_err(|e| e.to_string())?;
    let client = league_voice_recorder::cloud_sync::CloudSyncClient::new(&cfg.remote_server_url, &cfg.remote_api_key);
    let dest_path = cfg.record_folder.join(format!("{}.mp3", game_id));
    state.log(&format!("⬇ Downloading {}.mp3 from remote server...", game_id));
    client.download_recording(&game_id, &dest_path).await?;
    state.log(&format!("✓ Successfully downloaded {}.mp3", game_id));
    Ok(dest_path.to_string_lossy().to_string())
}

#[tauri::command]
async fn cloud_download_all(state: tauri::State<'_, Arc<AppState>>) -> Result<usize, String> {
    let cfg = AppConfig::load().map_err(|e| e.to_string())?;
    let client = league_voice_recorder::cloud_sync::CloudSyncClient::new(&cfg.remote_server_url, &cfg.remote_api_key);
    let items = client.list_recordings().await?;

    let mut count = 0;
    for it in items {
        let dest = cfg.record_folder.join(format!("{}.mp3", it.game_id));
        if !dest.exists() {
            state.log(&format!("⬇ Batch downloading missing scrim: {}.mp3...", it.game_id));
            if client.download_recording(&it.game_id, &dest).await.is_ok() {
                count += 1;
                state.log(&format!("✓ Downloaded {}.mp3", it.game_id));
            }
        }
    }
    state.log(&format!("✓ Batch download complete: {} files downloaded.", count));
    Ok(count)
}

#[tauri::command]
async fn cloud_upload_recording(game_id: String, state: tauri::State<'_, Arc<AppState>>) -> Result<String, String> {
    let cfg = AppConfig::load().map_err(|e| e.to_string())?;
    let client = league_voice_recorder::cloud_sync::CloudSyncClient::new(&cfg.remote_server_url, &cfg.remote_api_key);
    let file_path = cfg.record_folder.join(format!("{}.mp3", game_id));
    if !file_path.exists() {
        return Err(format!("Local file does not exist: {}.mp3", game_id));
    }

    let meta = league_voice_recorder::scrim_meta::ScrimMetadata::read_from_mp3(&file_path).unwrap_or_default();
    let team_tag = if meta.team_tag.is_empty() { None } else { Some(meta.team_tag.as_str()) };
    let notes = if meta.notes.is_empty() { None } else { Some(meta.notes.as_str()) };
    let patch = if meta.game_patch.is_empty() { None } else { Some(meta.game_patch.as_str()) };

    state.log(&format!("⬆ Uploading {}.mp3 to remote server...", game_id));
    let res = client.upload_recording(
        &game_id,
        &file_path,
        team_tag,
        notes,
        patch,
    ).await?;

    let msg = res.get("message").and_then(|m| m.as_str()).unwrap_or("Uploaded successfully");
    state.log(&format!("✓ {}: {}.mp3", msg, game_id));
    Ok(msg.to_string())
}

#[tauri::command]
async fn cloud_delete_recording(game_id: String, state: tauri::State<'_, Arc<AppState>>) -> Result<String, String> {
    let cfg = AppConfig::load().map_err(|e| e.to_string())?;
    let client = league_voice_recorder::cloud_sync::CloudSyncClient::new(&cfg.remote_server_url, &cfg.remote_api_key);
    state.log(&format!("🗑 Requesting remote server deletion of {}.mp3...", game_id));
    let res = client.delete_recording(&game_id).await?;
    state.log(&format!("✓ {}", res));
    Ok(res)
}

#[tauri::command]
fn get_app_version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

#[tauri::command]
async fn get_current_patch(state: tauri::State<'_, Arc<AppState>>) -> Result<String, ()> {
    if let Ok(lk) = state.active_game_patch.lock() {
        if let Some(ref p) = *lk {
            if !p.is_empty() && p != "—" {
                return Ok(p.clone());
            }
        }
    }
    let p = LcuCredentials::fetch_current_patch().await;
    if !p.is_empty() {
        if let Ok(mut lk) = state.active_game_patch.lock() {
            *lk = Some(p.clone());
        }
        return Ok(p);
    }
    Ok(String::new())
}

#[tauri::command]
async fn check_for_updates(state: tauri::State<'_, Arc<AppState>>) -> Result<league_voice_recorder::updater::UpdateInfo, String> {
    state.log("🔄 Checking GitHub Releases for newer versions...");
    match league_voice_recorder::updater::check_for_update(league_voice_recorder::updater::CURRENT_VERSION).await {
        Ok(info) => {
            if info.available {
                state.log(&format!("🌟 New version available: {} (current: {})", info.latest_version, info.current_version));
            } else {
                state.log(&format!("✓ You are using the latest version ({}).", info.current_version));
            }
            Ok(info)
        }
        Err(e) => {
            state.log(&format!("⚠️ Update check failed: {}", e));
            Err(e)
        }
    }
}

#[tauri::command]
async fn apply_update(
    download_url: String,
    state: tauri::State<'_, Arc<AppState>>,
    app: tauri::AppHandle,
) -> Result<(), String> {
    state.log("⬇️ Downloading update and preparing application restart...");
    use tauri::Emitter;
    let app_handle = app.clone();
    let cb = Box::new(move |downloaded: u64, total: u64, pct: u32, status: &str| {
        let _ = app_handle.emit("update-download-progress", serde_json::json!({
            "downloaded": downloaded,
            "total": total,
            "percentage": pct,
            "status": status,
        }));
    });
    league_voice_recorder::updater::download_and_apply_update(&download_url, Some(cb)).await
}

#[tauri::command]
fn get_available_languages() -> std::collections::HashMap<String, String> {
    league_voice_recorder::i18n::get_available_languages()
}

#[tauri::command]
fn get_translations(lang: String) -> Result<serde_json::Value, String> {
    Ok(league_voice_recorder::i18n::get_translations_for(&lang))
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    #[cfg(windows)]
    {
        // Optimize Microsoft Edge WebView2 runtime:
        // Consolidate renderer processes, disable background telemetry/network,
        // disable SmartScreen and media router checks, and minimize memory footprint.
        unsafe {
            std::env::set_var(
                "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
                "--disable-features=RendererCodeIntegrity,EdgeSmartScreen,TranslateUI,OptimizationHints,MediaRouter \
                 --disable-background-networking \
                 --disable-component-update \
                 --renderer-process-limit=1 \
                 --disable-extensions \
                 --disable-default-apps \
                 --disable-sync \
                 --no-default-browser-check \
                 --disable-breakpad",
            );
        }
    }

    let state = Arc::new(AppState::default());

    // Register custom AppLogLayer so application info!, warn!, and error! events
    // are fed into the UI log queue, while external connection pool crates (hyper, reqwest) are silenced.
    let log_layer = AppLogLayer {
        queue: state.log_queue.clone(),
    };
    let env_filter = tracing_subscriber::EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("league_voice_recorder=info,main=info,hyper=warn,reqwest=warn,rustls=warn,h2=warn,tao=warn,winit=warn"));

    let _ = tracing_subscriber::registry()
        .with(env_filter)
        .with(tracing_subscriber::fmt::layer())
        .with(log_layer)
        .try_init();

    info!("🎮 League Voice Recorder v{}", env!("CARGO_PKG_VERSION"));

    let args: Vec<String> = std::env::args().collect();
    if args.iter().any(|arg| arg == "--headless" || arg == "--cli") {
        #[cfg(windows)]
        unsafe {
            AttachConsole(0xFFFFFFFF);
        }

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
            recorder.run_watcher_loop(rec_stop, None, None, None, None, None, None).await;
        });

        let rep_handle = tokio::spawn(async move {
            replay_sync.run_sync_watcher(rep_stop, None, None, None, None, None).await;
        });

        let _ = tokio::join!(rec_handle, rep_handle);
        return Ok(());
    }

    // Dedicated Fast HUD Overlay Visibility Synchronizer (50ms responsive detection)
    let hud_sync_state = state.clone();
    tokio::spawn(async move {
        let mut last_visible: Option<bool> = None;
        loop {
            tokio::time::sleep(tokio::time::Duration::from_millis(50)).await;

            let is_preview = hud_sync_state.hud_preview_mode.lock().map(|m| m.is_some()).unwrap_or(false);
            if is_preview {
                continue;
            }

            let maybe_handle = hud_sync_state.app_handle.lock().ok().and_then(|h| h.clone());
            if let Some(handle) = maybe_handle {
                use tauri::Manager;
                if let Some(win) = handle.get_webview_window("overlay") {
                    #[cfg(windows)]
                    if let Ok(hwnd) = win.hwnd() {
                        OVERLAY_HWND.store(hwnd.0 as isize, Ordering::SeqCst);
                    }

                    let overlay_enabled = hud_sync_state.hud_overlay_enabled.load(Ordering::SeqCst);
                    if !overlay_enabled {
                        if last_visible != Some(false) {
                            let _ = win.hide();
                            last_visible = Some(false);
                        }
                        continue;
                    }

                    let is_rec = hud_sync_state.rec_active.load(Ordering::SeqCst);
                    let is_rep = hud_sync_state.rep_active.load(Ordering::SeqCst);
                    let rec_stat = hud_sync_state.rec_status.lock().map(|s| s.0.clone()).unwrap_or_default();
                    let rep_stat = hud_sync_state.rep_status.lock().map(|s| s.0.clone()).unwrap_or_default();

                    let is_match_live = is_rec && (rec_stat == "RECORDING" || rec_stat == "PAUSED");
                    let is_replay_live = is_rep && (rep_stat == "SYNCING" || rep_stat == "PAUSED" || (rep_stat == "WATCHING" && LcuCredentials::is_game_process_running()));

                    let should_be_active = is_match_live || is_replay_live;

                    if should_be_active && is_league_foreground() {
                        let is_vis = win.is_visible().unwrap_or(false);
                        if !is_vis || last_visible != Some(true) {
                            #[cfg(windows)]
                            if let Ok(hwnd) = win.hwnd() {
                                unsafe { ShowWindow(hwnd.0 as isize, 9); } // SW_RESTORE
                            }
                            let _ = win.unminimize();
                            let _ = win.show();
                            last_visible = Some(true);
                        }
                    } else {
                        let is_vis = win.is_visible().unwrap_or(false);
                        if is_vis || last_visible != Some(false) {
                            let _ = win.hide();
                            #[cfg(windows)]
                            if let Ok(hwnd) = win.hwnd() {
                                unsafe { ShowWindow(hwnd.0 as isize, 0); } // SW_HIDE
                            }
                            last_visible = Some(false);
                        }
                    }
                }
            }
        }
    });

    // Background Auto-Detection Thread (mimicking Python main.py _auto_detect_monitor_loop)
    let auto_state = state.clone();
    tokio::spawn(async move {
        let mut last_phase = String::new();
        loop {
            tokio::time::sleep(tokio::time::Duration::from_millis(1000)).await;

            // 1. Synchronize tray menu item and tooltip on every cycle
            update_tray_status(&auto_state);

            let client_running = LcuCredentials::is_client_running();
            let game_running = LcuCredentials::is_game_process_running();

            // When neither League client nor game engine is running, stay completely idle at ~0% CPU
            if !client_running && !game_running {
                last_phase.clear();
                continue;
            }

            let cfg = AppConfig::load().unwrap_or_default();

            // Check if active session is a Replay (either via .rofl process or Replay API :2999)
            let rofl_detected = RoflProcessDetector::detect_running_rofl().is_some();
            let replay_api_active = if !rofl_detected && game_running {
                let client = league_voice_recorder::replay::ReplayApiClient::default();
                client.get_playback_state().await.ok().flatten().is_some()
            } else {
                false
            };

            let mut lcu_phase = String::new();
            if client_running {
                if auto_state.active_game_patch.lock().map(|p| p.is_none()).unwrap_or(false) {
                    let patch = LcuCredentials::fetch_current_patch().await;
                    if !patch.is_empty() {
                        if let Ok(mut lk) = auto_state.active_game_patch.lock() {
                            *lk = Some(patch);
                        }
                    }
                }
                if let Some(creds) = LcuCredentials::find_active() {
                    if let Ok(p) = creds.get_gameflow_phase().await {
                        lcu_phase = p;
                    }
                }
            }

            let is_replay_mode = rofl_detected || replay_api_active || lcu_phase == "WatchInProgress";

            if is_replay_mode {
                // League is running a replay. Never trigger recording watcher!
                if cfg.auto_start_replay && !auto_state.rep_active.load(Ordering::SeqCst) {
                    auto_state.log("▶ Auto-detect: League replay detected! Auto-starting replay auto-sync...");
                    auto_state.start_replay();
                }
            } else if cfg.auto_start_champ_select {
                // Not a replay: check for live scrimmage match or ChampSelect
                if (lcu_phase == "ChampSelect" || lcu_phase == "InProgress") && lcu_phase != last_phase {
                    auto_state.log(&format!("🎮 Auto-detect: League entered {}! Auto-starting recording watcher...", lcu_phase));
                    last_phase = lcu_phase.clone();
                    auto_state.start_recording();
                } else if lcu_phase == "None" || lcu_phase == "Lobby" || lcu_phase == "EndOfGame" {
                    last_phase = lcu_phase.clone();
                } else if LcuCredentials::is_game_process_running() {
                    if !auto_state.rec_active.load(Ordering::SeqCst) {
                        auto_state.log("🎮 Auto-detect: Live match process running! Auto-starting recording watcher...");
                        auto_state.start_recording();
                    }
                }
            }
        }
    });

    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            use tauri::Manager;
            if let Some(win) = app.get_webview_window("main") {
                restore_main_window_robust(&win);
            }
        }))
        .setup(move |app| {
            use tauri::{
                menu::{Menu, MenuItem},
                tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
                Manager,
            };

            if let Some(window) = app.get_webview_window("main") {
                let _ = window.set_background_color(Some(tauri::webview::Color(11, 17, 32, 255)));
                let fallback_win = window.clone();
                std::thread::spawn(move || {
                    std::thread::sleep(std::time::Duration::from_millis(60));
                    let _ = fallback_win.show();
                });

                #[cfg(windows)]
                if let Ok(hwnd) = window.hwnd() {
                    let hwnd_val = hwnd.0 as isize;
                    if let Ok(mut guard) = MAIN_APP_HANDLE.lock() {
                        *guard = Some(app.handle().clone());
                    }
                    unsafe {
                        let prev = SetWindowLongPtrW(hwnd_val, -4, main_window_subclass_proc as *const () as isize);
                        PREV_MAIN_WND_PROC.store(prev, Ordering::SeqCst);
                    }
                }

                // Handle CloseRequested (exit app)
                let app_handle_clone = app.handle().clone();
                window.on_window_event(move |event| {
                    match event {
                        tauri::WindowEvent::CloseRequested { .. } => {
                            // User clicked 'X': completely terminate and exit the application
                            app_handle_clone.exit(0);
                        }
                        _ => {}
                    }
                });
            }

            // Setup System Tray
            let open_item = MenuItem::with_id(app, "open", "Open", true, None::<&str>)?;
            let status_item = MenuItem::with_id(app, "status", "League Voice Recorder: Idle", false, None::<&str>)?;
            let exit_item = MenuItem::with_id(app, "exit", "Exit", true, None::<&str>)?;
            if let Ok(mut lk) = LAST_TRAY_STATUS_KEY.lock() {
                *lk = "League Voice Recorder: Idle".to_string();
            }
            let tray_menu = Menu::with_items(app, &[&open_item, &status_item, &exit_item])?;

            let tray_icon = tauri::image::Image::from_bytes(include_bytes!("../icons/tray_32.png"))
                .or_else(|_| tauri::image::Image::from_bytes(include_bytes!("../icons/tray_16.png")))
                .ok();

            let mut tray_builder = TrayIconBuilder::with_id("tray")
                .menu(&tray_menu)
                .tooltip("League Voice Recorder: Idle")
                .show_menu_on_left_click(false);

            if let Some(icon) = tray_icon {
                tray_builder = tray_builder.icon(icon);
            } else if let Some(icon) = app.default_window_icon() {
                tray_builder = tray_builder.icon(icon.clone());
            }

            let _tray = tray_builder
                .on_menu_event(|app, event| {
                    match event.id().as_ref() {
                        "open" => {
                            if let Some(win) = app.get_webview_window("main") {
                                restore_main_window_robust(&win);
                            }
                        }
                        "exit" => {
                            app.exit(0);
                        }
                        _ => {}
                    }
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                        let app = tray.app_handle();
                        if let Some(win) = app.get_webview_window("main") {
                            let is_vis = win.is_visible().unwrap_or(true);
                            let is_min = win.is_minimized().unwrap_or(false);
                            if !is_vis || is_min {
                                restore_main_window_robust(&win);
                            } else {
                                let _ = win.hide();
                                show_toast_announcement(&app);
                            }
                        }
                    }
                })
                .build(app)?;

            *app.state::<Arc<AppState>>().app_handle.lock().unwrap() = Some(app.handle().clone());

            let aux_handle = app.handle().clone();
            std::thread::spawn(move || {
                std::thread::sleep(std::time::Duration::from_millis(1500));
                let h2 = aux_handle.clone();
                let _ = aux_handle.run_on_main_thread(move || {
                    create_aux_windows(&h2);
                    setup_overlay(&h2);
                });
            });

            Ok(())
        })
        .manage(state)
        .invoke_handler(tauri::generate_handler![
            get_status,
            get_system_health,
            start_recording_watcher,
            stop_recording_watcher,
            start_replay_watcher,
            stop_replay_watcher,
            get_logs,
            get_recordings,
            save_recording_metadata,
            open_recordings_folder,
            play_recording,
            delete_recording,
            get_audio_devices,
            get_config,
            save_config,
            set_replay_volume,
            enable_replay_api,
            check_replay_api_status,
            get_audio_levels,
            get_telemetry,
            get_hud_data,
            overlay_start_dragging,
            overlay_preview_rec,
            overlay_preview_replay,
            overlay_hide_preview,
            overlay_set_locked,
            overlay_set_opacity,
            overlay_reset_position,
            overlay_forward_click,
            restore_main_window,
            show_main_window,
            test_cloud_connection,
            cloud_get_role,
            cloud_list_recordings,
            cloud_download_recording,
            cloud_download_all,
            cloud_upload_recording,
            cloud_delete_recording,
            check_for_updates,
            apply_update,
            get_available_languages,
            get_translations,
            get_app_version,
            get_current_patch,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");

    Ok(())
}
