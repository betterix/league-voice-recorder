use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use serde::{Deserialize, Serialize};
use tracing::info;
use tracing_subscriber::layer::SubscriberExt;
use tracing_subscriber::util::SubscriberInitExt;

use crate::{
    audio::AudioCaptureManager,
    config::AppConfig,
    discord_ipc::DiscordVoiceState,
    lcu::LcuCredentials,
    record::LeagueRecorder,
    replay::{ReplayConfigManager, ReplaySynchronizer, RodioPlayer},
    scrim_meta::ScrimMetadata,
};

pub fn get_timestamp() -> String {
    use std::time::SystemTime;
    let dur = SystemTime::now().duration_since(SystemTime::UNIX_EPOCH).unwrap_or_default();
    let secs = dur.as_secs() % 86400;
    let hours = (secs / 3600 + 1) % 24;
    let mins = (secs % 3600) / 60;
    let s = secs % 60;
    format!("{:02}:{:02}:{:02}", hours, mins, s)
}

/// Custom Tracing Layer that pipes info!, warn!, and error! events
/// into the thread-safe UI log queue in real time.
pub struct AppLogLayer {
    pub queue: Arc<Mutex<Vec<String>>>,
}

impl<S: tracing::Subscriber> tracing_subscriber::Layer<S> for AppLogLayer {
    fn on_event(&self, event: &tracing::Event<'_>, _ctx: tracing_subscriber::layer::Context<'_, S>) {
        let target = event.metadata().target();
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
            let entry = format!("[{}] {}", get_timestamp(), visitor.0);
            if let Ok(mut q) = self.queue.lock() {
                q.push(entry);
            }
        }
    }
}

pub fn init_logging(log_queue: Arc<Mutex<Vec<String>>>) {
    let log_layer = AppLogLayer { queue: log_queue };
    let env_filter = tracing_subscriber::EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new(
            "league_voice_recorder=info,main=info,hyper=warn,reqwest=warn,rustls=warn,h2=warn,wgpu=warn,naga=warn,iced=warn"
        ));

    let _ = tracing_subscriber::registry()
        .with(env_filter)
        .with(tracing_subscriber::fmt::layer())
        .with(log_layer)
        .try_init();
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DownloadProgress {
    pub downloaded: u64,
    pub total: u64,
    pub percentage: u32,
    pub status: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RecordingItem {
    pub filename: String,
    pub size_str: String,
    pub size_bytes: u64,
    pub date_str: String,
    pub game_id: String,
    pub team_tag: String,
    pub notes: String,
    pub game_patch: String,
    pub recorded_at: String,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct SystemHealthDto {
    pub discord_running: bool,
    pub league_running: bool,
    pub replay_api_enabled: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TelemetryDto {
    pub match_state: String,
    pub game_time: String,
    pub base_time: String,
    pub audio_offset: String,
    pub game_id: String,
    pub game_patch: String,

    pub replay_file: String,
    pub replay_time: String,
    pub player_time: String,
    pub replay_drift: String,
    pub replay_speed: String,
    pub replay_audio: String,

    pub ram_usage: String,
    pub cpu_usage: String,
    pub app_version: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
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
    pub hud_preview_mode: Arc<Mutex<Option<String>>>,
    pub hud_locked: Arc<AtomicBool>,
    pub hud_overlay_enabled: Arc<AtomicBool>,
    pub base_game_time: Arc<Mutex<Option<String>>>,
    pub active_game_patch: Arc<Mutex<Option<String>>>,
    pub update_progress: Arc<Mutex<Option<DownloadProgress>>>,
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

        // Background audio preview monitor loop for VU meters when idle
        let prev_cap_clone = preview_capture.clone();
        let levels_clone = audio_levels.clone();
        let rec_active_clone = rec_active.clone();
        let stop_preview_loop = ipc_stop.clone();

        tokio::spawn(async move {
            while !stop_preview_loop.load(Ordering::Relaxed) {
                if !rec_active_clone.load(Ordering::Relaxed) {
                    let peaks = {
                        if let Ok(cap) = prev_cap_clone.lock() {
                            Some(cap.get_peaks())
                        } else {
                            None
                        }
                    };
                    if let Some((mic_p, app_p)) = peaks {
                        if let Ok(mut lv) = levels_clone.lock() {
                            *lv = (mic_p, app_p);
                        }
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
            replay_volume: Arc::new(Mutex::new((initial_cfg.replay_volume as f32 / 100.0).clamp(0.0, 2.0))),
            replay_time: Arc::new(Mutex::new((0.0, 1800.0, 1.0))),
            audio_levels,
            discord_voice,
            preview_capture,
            hud_preview_mode: Arc::new(Mutex::new(None)),
            hud_locked: Arc::new(AtomicBool::new(initial_cfg.hud_locked)),
            hud_overlay_enabled: Arc::new(AtomicBool::new(initial_cfg.hud_overlay_enabled)),
            base_game_time: Arc::new(Mutex::new(None)),
            active_game_patch: Arc::new(Mutex::new(None)),
            update_progress: Arc::new(Mutex::new(None)),
        }
    }
}

impl AppState {
    pub fn log(&self, msg: &str) {
        info!("{}", msg);
    }

    /// Restarts the idle microphone/loopback preview stream for the selected mic device.
    pub fn restart_preview(&self, mic_device_name: &str) {
        if self.rec_active.load(Ordering::Relaxed) {
            return;
        }
        let target = if mic_device_name.is_empty() || mic_device_name == "default" {
            None
        } else {
            Some(mic_device_name)
        };
        if let Ok(mut prev) = self.preview_capture.lock() {
            let _ = prev.start_preview(target);
        }
    }

    pub fn start_recording(&self) {
        if self.rec_active.load(Ordering::SeqCst) {
            return;
        }
        self.rec_stop.store(false, Ordering::SeqCst);
        self.rec_active.store(true, Ordering::SeqCst);

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

        let stop_sig = self.rec_stop.clone();
        let active_sig = self.rec_active.clone();
        let levels_sig = self.audio_levels.clone();
        let rec_status_sig = self.rec_status.clone();
        let clock_sig = self.match_time.clone();
        let muted_sig = self.discord_voice.is_muted.clone();
        let prev_cap_sig = self.preview_capture.clone();
        let base_time_sig = self.base_game_time.clone();
        let patch_sig = self.active_game_patch.clone();

        if let Ok(cfg) = AppConfig::load() {
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
                    if st.0 != "SAVED" {
                        *st = ("IDLE".to_string(), "".to_string());
                    }
                }
                if let Ok(mut mt) = clock_sig.lock() {
                    *mt = None;
                }
                if let Ok(cfg2) = AppConfig::load() {
                    let mic_target = if cfg2.mic_device_name.is_empty() || cfg2.mic_device_name == "default" {
                        None
                    } else {
                        Some(cfg2.mic_device_name)
                    };
                    if let Ok(mut prev) = prev_cap_sig.lock() {
                        let _ = prev.start_preview(mic_target.as_deref());
                    }
                }
            });
        }
    }

    pub fn stop_recording(&self) {
        if !self.rec_active.load(Ordering::SeqCst) {
            return;
        }
        self.rec_stop.store(true, Ordering::SeqCst);
        self.log("⏹ Recording Watcher stopping...");
    }

    pub fn start_replay(&self) {
        if self.rep_active.load(Ordering::SeqCst) {
            return;
        }
        self.rep_stop.store(false, Ordering::SeqCst);
        self.rep_active.store(true, Ordering::SeqCst);
        if let Ok(mut st) = self.rep_status.lock() {
            *st = ("WATCHING".to_string(), "Waiting for .rofl replay...".to_string());
        }
        if let Ok(mut mt) = self.match_time.lock() {
            *mt = None;
        }
        self.log("🎬 Replay Watcher started. Monitoring League replay process & :2999...");

        let stop_sig = self.rep_stop.clone();
        let active_sig = self.rep_active.clone();
        let rep_status_sig = self.rep_status.clone();
        let clock_sig = self.match_time.clone();
        let player_sig = self.current_player.clone();
        let rep_time_sig = self.replay_time.clone();
        let vol_sig = self.replay_volume.clone();

        if let Ok(cfg) = AppConfig::load() {
            tokio::spawn(async move {
                let sync = ReplaySynchronizer::new(cfg);
                sync.run_sync_watcher(
                    stop_sig,
                    Some(rep_status_sig.clone()),
                    Some(clock_sig.clone()),
                    Some(player_sig),
                    Some(vol_sig),
                    Some(rep_time_sig),
                ).await;
                active_sig.store(false, Ordering::SeqCst);
                if let Ok(mut st) = rep_status_sig.lock() {
                    *st = ("IDLE".to_string(), "".to_string());
                }
                if let Ok(mut mt) = clock_sig.lock() {
                    *mt = None;
                }
            });
        }
    }

    pub fn stop_replay(&self) {
        if !self.rep_active.load(Ordering::SeqCst) {
            return;
        }
        self.rep_stop.store(true, Ordering::SeqCst);
        self.log("⏹ Replay Watcher stopping...");
    }

    pub fn spawn_auto_detect_monitor(self: &Arc<Self>) {
        let auto_state = self.clone();
        tokio::spawn(async move {
            let mut last_phase = String::new();
            let mut last_replay_running = false;

            loop {
                tokio::time::sleep(tokio::time::Duration::from_millis(2500)).await;

                let client_running = LcuCredentials::is_client_running();
                let game_running = LcuCredentials::is_game_process_running();

                if !client_running && !game_running {
                    last_phase.clear();
                    last_replay_running = false;
                    continue;
                }

                if let Some(cfg) = AppConfig::load().ok() {
                    if cfg.auto_start_replay {
                        let replay_running = crate::replay::RoflProcessDetector::detect_running_rofl().is_some();
                        if replay_running && !last_replay_running && !auto_state.rep_active.load(Ordering::SeqCst) {
                            auto_state.log("⚡ Auto-Detect: Active League replay (.rofl) detected! Starting Replay Watcher...");
                            auto_state.start_replay();
                        }
                        last_replay_running = replay_running;
                    }

                    if cfg.auto_start_champ_select {
                        if let Some(creds) = LcuCredentials::find_active() {
                            if let Ok(phase) = creds.get_gameflow_phase().await {
                                if phase != last_phase {
                                    if (phase == "ChampSelect" || phase == "GameStart" || phase == "InProgress")
                                        && !auto_state.rec_active.load(Ordering::SeqCst)
                                    {
                                        auto_state.log(&format!("⚡ Auto-Detect: LCU entered {} phase! Starting Recording Watcher...", phase));
                                        auto_state.start_recording();
                                    }
                                    last_phase = phase;
                                }
                            }
                        } else {
                            last_phase.clear();
                        }
                    }
                }
            }
        });
    }
}

pub fn get_recordings_sync() -> Vec<RecordingItem> {
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
                        size_bytes: size,
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
    items.reverse();
    items
}

pub fn get_system_health_sync() -> SystemHealthDto {
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

pub fn get_telemetry_sync(state: &AppState) -> TelemetryDto {
    let rec_active = state.rec_active.load(Ordering::SeqCst);
    let rep_active = state.rep_active.load(Ordering::SeqCst);
    let rec_stat = state.rec_status.lock().map(|g| g.clone()).unwrap_or_else(|_| ("IDLE".to_string(), "".to_string()));
    let rep_stat = state.rep_status.lock().map(|g| g.clone()).unwrap_or_else(|_| ("IDLE".to_string(), "".to_string()));
    let clk = state.match_time.lock().map(|g| g.clone()).unwrap_or(None);
    let base_time_val = state.base_game_time.lock().map(|g| g.clone()).unwrap_or(None).unwrap_or_else(|| "00:00".to_string());
    let patch_val = state.active_game_patch.lock().map(|g| g.clone()).unwrap_or(None).unwrap_or_else(|| "—".to_string());

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
            ),
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
            ),
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

    let mut ram_usage = "0.0 MB".to_string();
    let mut cpu_usage = "0.0%".to_string();

    static SYSTEM_MONITOR: std::sync::Mutex<Option<(sysinfo::System, std::time::Instant)>> = std::sync::Mutex::new(None);
    if let Ok(mut guard) = SYSTEM_MONITOR.lock() {
        let pid = sysinfo::Pid::from_u32(std::process::id());
        let now = std::time::Instant::now();
        let should_refresh = match &*guard {
            Some((_, last)) => now.duration_since(*last) >= std::time::Duration::from_secs(2),
            None => true,
        };
        if guard.is_none() {
            if let Ok(sys) = sysinfo::System::new() {
                *guard = Some((sys, now - std::time::Duration::from_secs(10)));
            }
        }
        if let Some((ref mut sys, ref mut last)) = *guard {
            if should_refresh {
                sys.refresh_processes_specifics(
                    sysinfo::ProcessesToUpdate::Some(&[pid]),
                    false,
                    sysinfo::ProcessRefreshKind::nothing().with_memory().with_cpu(),
                );
                *last = now;
            }
            if let Some(proc) = sys.process(pid) {
                ram_usage = format!("{:.1} MB", proc.memory() as f64 / 1_048_576.0);
                let cores = sys.cpus().len().max(1) as f32;
                cpu_usage = format!("{:.1}%", proc.cpu_usage() / cores);
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
        app_version: format!("v{}", env!("CARGO_PKG_VERSION")),
    }
}

pub fn get_hud_data_sync(state: &AppState) -> HudDataDto {
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
    let is_saving = rec_stat == "SAVING" || rec_stat == "SAVED";
    let is_live = (rec_active && !is_saving && (rec_stat == "RECORDING" || rec_stat == "PAUSED"))
        || (rep_active && !is_saving && (rep_stat == "SYNCING" || rep_stat == "PAUSED" || (rep_stat == "WATCHING" && LcuCredentials::is_game_process_running())));
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
        visible: is_live,
    }
}

pub fn play_recording_sync(filename: &str, state: &AppState) {
    if let Ok(cfg) = AppConfig::load() {
        let path = cfg.record_folder.join(filename);
        if path.exists() {
            state.log(&format!("▶ Playing scrimmage audio: {}", filename));
            let vol = *state.replay_volume.lock().unwrap();
            if let Ok(mut lk) = state.current_player.lock() {
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
                            if let Ok(mut lk) = state.current_player.lock() {
                                *lk = Some(player);
                            }
                        }
                        Err(e) => state.log(&format!("⚠️ Failed to decode audio {}: {}", filename, e)),
                    }
                }
                Err(e) => state.log(&format!("⚠️ Failed to initialize audio output device: {}", e)),
            }
        } else {
            state.log(&format!("⚠️ Recording file not found on disk: {}", filename));
        }
    }
}

pub fn stop_playback_sync(state: &AppState) {
    if let Ok(mut lk) = state.current_player.lock() {
        if let Some(ref p) = *lk {
            p.stop();
        }
        *lk = None;
    }
    state.log("⏹ Playback stopped.");
}

#[cfg(windows)]
pub fn is_league_foreground(overlay_hwnd: isize) -> bool {
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

    if overlay_hwnd != 0 {
        const GA_ROOT: u32 = 2;
        const GA_ROOTOWNER: u32 = 3;
        let root = unsafe { GetAncestor(hwnd, GA_ROOT) };
        let root_owner = unsafe { GetAncestor(hwnd, GA_ROOTOWNER) };
        if hwnd == overlay_hwnd || root == overlay_hwnd || root_owner == overlay_hwnd {
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
            if path_os.ends_with("league of legends.exe") {
                return true;
            }
        }
    }

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
pub fn is_league_foreground(_overlay_hwnd: isize) -> bool {
    true
}
