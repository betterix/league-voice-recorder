pub mod icons;
pub mod main_view;
pub mod overlay_view;
pub mod theme;

use std::collections::HashSet;
use std::fmt;
use std::sync::atomic::Ordering;
use std::sync::Arc;

use iced::widget::{image, operation, scrollable};
use iced::window::{self, settings::PlatformSpecific, Level, Position};
use iced::{Color, Element, Point, Size, Subscription, Task, Theme};
use tray_icon::menu::{Menu, MenuEvent, MenuItem};
use tray_icon::{MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent};

use crate::app_state::{
    get_hud_data_sync, get_recordings_sync, get_system_health_sync, get_telemetry_sync,
    is_league_foreground, play_recording_sync, stop_playback_sync, AppState, DownloadProgress,
    HudDataDto, RecordingItem, SystemHealthDto, TelemetryDto,
};
use crate::audio::AudioCaptureManager;
use crate::cloud_sync::{CloudRecordingDto, CloudSyncClient};
use crate::config::AppConfig;
use crate::i18n;
use crate::replay::ReplayConfigManager;
use crate::scrim_meta::ScrimMetadata;
use crate::updater::{self, UpdateInfo};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LangOption {
    pub code: String,
    pub name: String,
}

impl fmt::Display for LangOption {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.name)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ThemeOption {
    pub value: String,
    pub label: String,
}

impl fmt::Display for ThemeOption {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.label)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MicOption {
    pub value: String,
    pub label: String,
}

impl fmt::Display for MicOption {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.label)
    }
}

#[derive(Debug, Clone)]
pub struct EditMetaState {
    pub filename: String,
    pub game_id: String,
    pub recorded_at: String,
    pub game_patch: String,
    pub team_tag: String,
    pub notes: String,
}

#[derive(Debug, Clone)]
pub enum ConfirmAction {
    DeleteLocal(String),
    DeleteRemote(String),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Tab {
    Log,
    Recordings,
    Settings,
}

#[derive(Debug, Clone)]
pub enum Message {
    TickFast,
    TickSlow,
    Frame(iced::time::Instant),
    DeferredInit,
    IconDecoded(Option<(Vec<u8>, u32, u32)>),
    WindowOpened(window::Id),
    WindowClosed(window::Id),
    WindowMoved(window::Id, Point),
    WindowCloseRequested(window::Id),
    MainWindowRawId(u64),
    OverlayRawId(u64),
    RestoreFromTray,
    HideToast(u64),
    ExitApp,

    SelectTab(Tab),
    ToggleTelemetry,
    ToggleTheme,
    ToggleRecording,
    ToggleReplay,
    EnableReplayApi,
    RefreshHealth,
    HealthLoaded(SystemHealthDto),
    RefreshMics,
    MicsLoaded(Vec<String>),

    SetReplayVolume(u32),
    ToggleMute,

    ToggleAutoscroll(bool),
    CopyLog,
    ClearLogs,
    RefreshRecordings,
    RecordingsLoaded(Vec<RecordingItem>),
    OpenRecordingsFolder,
    PlayRecording(String),
    StopPlayback,

    StartEditMeta(String),
    EditMetaDate(String),
    EditMetaTeam(String),
    EditMetaPatch(String),
    EditMetaNotes(String),
    CancelEditMeta,
    SaveEditMeta,
    SaveEditMetaAndUpload,

    RequestDeleteLocal(String),
    RequestDeleteRemote(String),
    ConfirmDelete,
    CancelDelete,
    CloudDeleteFinished(String, Result<(), String>),

    SetRecordFolder(String),
    BrowseRecordFolder,
    FolderPicked(Option<String>),
    SetMicDevice(MicOption),
    SetLanguage(LangOption),
    SetTheme(ThemeOption),
    SetAutoChamp(bool),
    SetAutoReplay(bool),
    SetMinimizeTray(bool),
    SetHudEnabled(bool),
    SetHudLocked(bool),
    SetHudOpacity(u32),
    SetCloudUrl(String),
    SetCloudKey(String),

    PreviewRecHud,
    PreviewReplayHud,
    HideHudPreview,
    ResetHudPosition,
    ToggleHudLock,
    DragOverlay,

    TestCloudConnection,
    CloudStatusResult(String, String),
    SyncRemote,
    RemoteSynced(String, Result<Vec<CloudRecordingDto>, String>),
    CloudDownloadSingle(String),
    CloudDownloadSingleFinished(String, Result<(), String>),
    CloudUpload(String),
    CloudUploadFinished(String, Result<(), String>),
    CloudDownloadAll,
    CloudDownloadAllFinished(Result<usize, String>),

    CheckForUpdates(bool),
    UpdateCheckResult(bool, Result<UpdateInfo, String>),
    DismissUpdateBanner,
    ApplyUpdate,
    UpdateApplyFinished(Result<(), String>),
    OpenReleasePage(String),
}

pub struct TrayHandle {
    pub icon: TrayIcon,
    pub open_id: tray_icon::menu::MenuId,
    pub status_item: MenuItem,
    pub exit_id: tray_icon::menu::MenuId,
    pub last_tooltip: String,
    pub last_menu_status: String,
}

pub struct App {
    pub state: Arc<AppState>,
    pub main_id: Option<window::Id>,
    pub main_hwnd: isize,
    pub overlay_id: Option<window::Id>,
    pub overlay_hwnd: isize,
    pub toast_id: Option<window::Id>,
    pub toast_gen: u64,
    pub tray: Option<TrayHandle>,

    pub config: AppConfig,
    pub active_tab: Tab,
    pub show_telemetry: bool,
    pub autoscroll: bool,
    pub log_scroll_id: iced::widget::Id,
    pub health: SystemHealthDto,
    pub telemetry: TelemetryDto,
    pub logs: Vec<String>,
    pub recordings: Vec<RecordingItem>,
    pub remote_recordings: Vec<CloudRecordingDto>,
    pub is_syncing_remote: bool,
    pub downloading_ids: HashSet<String>,
    pub uploading_ids: HashSet<String>,
    pub deleting_ids: HashSet<String>,
    pub current_client_patch: String,
    pub playing_file: Option<String>,
    pub edit_meta: Option<EditMetaState>,
    pub confirm_delete: Option<(ConfirmAction, String)>,
    pub mics: Vec<String>,
    pub available_languages: Vec<LangOption>,

    pub update_info: Option<UpdateInfo>,
    pub update_status_msg: String,
    pub update_downloading: bool,
    pub update_banner_dismissed: bool,
    pub update_progress: Option<DownloadProgress>,

    pub cloud_status_msg: String,
    pub cloud_role: String,

    pub replay_volume_pct: u32,
    pub is_muted: bool,
    pub prev_volume_pct: u32,
    pub mic_level: f32,
    pub discord_level: f32,
    pub mic_smooth: f32,
    pub discord_smooth: f32,
    pub entrance_elapsed_ms: f32,
    pub last_frame: Option<iced::time::Instant>,
    pub is_discord_muted: bool,
    pub hud_data: HudDataDto,
    pub wave_phase: f32,
    pub translations: serde_json::Value,
    pub logo_handle: Option<image::Handle>,
    pub tick_count: u64,
}

impl App {
    pub fn tr(&self, key: &str, fallback: &str) -> String {
        let mut cur = &self.translations;
        for part in key.split('.') {
            if let Some(next) = cur.get(part) {
                cur = next;
            } else {
                return fallback.to_string();
            }
        }
        cur.as_str().unwrap_or(fallback).to_string()
    }

    pub fn tr_fmt(&self, key: &str, fallback: &str, params: &[(&str, &str)]) -> String {
        let mut out = self.tr(key, fallback);
        for (k, v) in params {
            out = out.replace(&format!("{{{}}}", k), v);
        }
        out
    }

    pub fn can_upload_to_cloud(&self) -> bool {
        if self.cloud_role == "pull" {
            return false;
        }
        if self.cloud_role == "upload" || self.cloud_role == "admin" {
            return true;
        }
        let key = self.config.remote_api_key.trim().to_lowercase();
        let url = self.config.remote_server_url.trim();
        if key.is_empty() || url.is_empty() {
            return false;
        }
        if key.contains("pull") {
            return false;
        }
        key.contains("upload") || key.contains("admin") || key.contains("push")
    }

    pub fn is_admin_role(&self) -> bool {
        if self.cloud_role == "admin" {
            return true;
        }
        self.config.remote_api_key.trim().to_lowercase().contains("admin")
    }

    pub fn normalize_patch(p: &str) -> String {
        let mut clean = p.trim().to_lowercase();
        if let Some(rest) = clean.strip_prefix("patch ") {
            clean = rest.trim().to_string();
        }
        let parts: Vec<&str> = clean.split('.').collect();
        if parts.len() >= 2 && parts[0].parse::<u32>().is_ok() && parts[1].parse::<u32>().is_ok() {
            return format!("{}.{}", parts[0], parts[1]);
        }
        clean
    }

    fn save_config_now(&self) {
        let _ = self.config.save();
    }

    pub fn boot(state: Arc<AppState>) -> (Self, Task<Message>) {
        let config = AppConfig::load().unwrap_or_default();
        let translations = i18n::get_translations_for(&config.language);
        let initial_logs = state.log_queue.lock().map(|mut q| {
            let v = q.clone();
            q.clear();
            v
        }).unwrap_or_default();

        let vol_pct = config.replay_volume;
        set_minimize_to_tray_flag(config.minimize_to_tray);

        let telemetry = TelemetryDto {
            match_state: "IDLE".to_string(),
            game_time: "--".to_string(),
            base_time: "--".to_string(),
            audio_offset: "--".to_string(),
            game_id: "--".to_string(),
            game_patch: "--".to_string(),
            replay_file: "--".to_string(),
            replay_time: "--".to_string(),
            player_time: "--".to_string(),
            replay_drift: "--".to_string(),
            replay_speed: "1.0x".to_string(),
            replay_audio: "--".to_string(),
            ram_usage: "--".to_string(),
            cpu_usage: "--".to_string(),
            app_version: format!("v{}", env!("CARGO_PKG_VERSION")),
        };
        let hud_data = HudDataDto {
            mode: "RECORD".to_string(),
            time_str: "00:00".to_string(),
            mic_level: 0.0,
            discord_level: 0.0,
            is_paused: false,
            is_muted: false,
            is_discord_muted: false,
            current_time: 0.0,
            total_duration: 1800.0,
            speed: 1.0,
            volume: vol_pct,
            locked: config.hud_locked,
            opacity: config.hud_opacity,
            is_preview: false,
            visible: false,
        };

        let logo_handle = load_logo_handle();
        let mut available_languages: Vec<LangOption> = i18n::get_available_languages()
            .into_iter()
            .map(|(code, name)| LangOption { code, name })
            .collect();
        available_languages.sort_by(|a, b| a.code.cmp(&b.code));

        let (main_win_id, open_main_task) = open_main_window();

        let mut app = Self {
            state,
            main_id: Some(main_win_id),
            main_hwnd: 0,
            overlay_id: None,
            overlay_hwnd: 0,
            toast_id: None,
            toast_gen: 0,
            tray: None,
            config,
            active_tab: Tab::Log,
            show_telemetry: false,
            autoscroll: true,
            log_scroll_id: iced::widget::Id::unique(),
            health: SystemHealthDto::default(),
            telemetry,
            logs: initial_logs,
            recordings: Vec::new(),
            remote_recordings: Vec::new(),
            is_syncing_remote: false,
            downloading_ids: HashSet::new(),
            uploading_ids: HashSet::new(),
            deleting_ids: HashSet::new(),
            current_client_patch: String::new(),
            playing_file: None,
            edit_meta: None,
            confirm_delete: None,
            mics: vec!["default".to_string()],
            available_languages,
            update_info: None,
            update_status_msg: String::new(),
            update_downloading: false,
            update_banner_dismissed: false,
            update_progress: None,
            cloud_status_msg: String::new(),
            cloud_role: "unknown".to_string(),
            replay_volume_pct: vol_pct,
            is_muted: vol_pct == 0,
            prev_volume_pct: if vol_pct > 0 { vol_pct } else { 80 },
            mic_level: 0.0,
            discord_level: 0.0,
            mic_smooth: 0.0,
            discord_smooth: 0.0,
            entrance_elapsed_ms: 0.0,
            last_frame: None,
            is_discord_muted: false,
            hud_data,
            wave_phase: 0.0,
            translations,
            logo_handle,
            tick_count: 0,
        };

        let init_tasks = Task::batch([
            open_main_task.map(Message::WindowOpened),
            window::raw_id::<Message>(main_win_id).map(Message::MainWindowRawId),
            Task::perform(
                async {
                    tokio::time::sleep(std::time::Duration::from_millis(50)).await;
                },
                |_| Message::DeferredInit,
            ),
        ]);

        if !app.config.remote_server_url.is_empty() {
            app.cloud_role = if app.config.remote_api_key.is_empty() {
                "local".to_string()
            } else {
                "configured".to_string()
            };
        }

        (app, init_tasks)
    }

    pub fn title(&self, window: window::Id) -> String {
        if Some(window) == self.overlay_id {
            "League Overlay".to_string()
        } else if Some(window) == self.toast_id {
            "League Voice Recorder Notification".to_string()
        } else {
            format!(
                "{} v{}",
                self.tr("app.window_title", "League Voice Recorder & Replay Synchronizer"),
                env!("CARGO_PKG_VERSION")
            )
        }
    }

    pub fn theme(&self, _window: window::Id) -> Theme {
        if theme::is_dark_theme(&self.config.theme) {
            Theme::Dark
        } else {
            Theme::Light
        }
    }

    pub fn style(&self, theme: &Theme) -> iced::theme::Style {
        let p = theme::palette(theme::is_dark_theme(&self.config.theme));
        iced::theme::Style {
            background_color: Color::TRANSPARENT,
            text_color: if matches!(theme, Theme::Light) { p.text_main } else { Color::WHITE },
        }
    }

    fn needs_animation_frames(&self) -> bool {
        if self.entrance_elapsed_ms < 450.0 {
            return true;
        }
        if self.state.rec_active.load(Ordering::Relaxed)
            || self.state.rep_active.load(Ordering::Relaxed)
        {
            return true;
        }
        self.state
            .hud_preview_mode
            .lock()
            .map(|m| m.is_some())
            .unwrap_or(false)
    }

    pub fn subscription(&self) -> Subscription<Message> {
        let fast = iced::time::every(std::time::Duration::from_millis(40)).map(|_| Message::TickFast);
        let slow = iced::time::every(std::time::Duration::from_secs(3)).map(|_| Message::TickSlow);
        let closes = window::close_requests().map(Message::WindowCloseRequested);
        let events = window::events().filter_map(|(id, ev)| match ev {
            window::Event::Closed => Some(Message::WindowClosed(id)),
            window::Event::Moved(pt) => Some(Message::WindowMoved(id, pt)),
            _ => None,
        });
        if self.needs_animation_frames() {
            let frames = window::frames().map(Message::Frame);
            Subscription::batch([fast, slow, closes, events, frames])
        } else {
            Subscription::batch([fast, slow, closes, events])
        }
    }

    pub fn update(&mut self, message: Message) -> Task<Message> {
        match message {
            Message::Frame(now) => {
                let dt_ms = if let Some(prev) = self.last_frame {
                    now.saturating_duration_since(prev).as_secs_f32() * 1000.0
                } else {
                    16.67
                }
                .clamp(0.0, 50.0);
                self.last_frame = Some(now);

                if self.entrance_elapsed_ms < 450.0 {
                    self.entrance_elapsed_ms = (self.entrance_elapsed_ms + dt_ms).min(450.0);
                }

                self.wave_phase = (self.wave_phase + (dt_ms / 1000.0) * 4.5) % std::f32::consts::TAU;

                let alpha = 1.0 - (-dt_ms / 55.0).exp();
                self.mic_smooth += (self.mic_level - self.mic_smooth) * alpha;
                self.discord_smooth += (self.discord_level - self.discord_smooth) * alpha;

                if !self.needs_animation_frames() {
                    self.last_frame = None;
                }
                Task::none()
            }

            Message::TickFast => {
                self.tick_count = self.tick_count.wrapping_add(1);

                // Drain log queue
                let mut had_new_logs = false;
                if let Ok(mut q) = self.state.log_queue.lock() {
                    if !q.is_empty() {
                        had_new_logs = true;
                        self.logs.extend(q.drain(..));
                        if self.logs.len() > 1500 {
                            let excess = self.logs.len() - 1500;
                            self.logs.drain(0..excess);
                        }
                    }
                }

                // Audio levels
                if let Ok(lv) = self.state.audio_levels.lock() {
                    self.mic_level = lv.0;
                    self.discord_level = lv.1;
                }
                self.is_discord_muted = self.state.discord_voice.is_muted();

                if !self.needs_animation_frames() {
                    self.last_frame = None;
                    let alpha = 0.45;
                    self.mic_smooth += (self.mic_level - self.mic_smooth) * alpha;
                    self.discord_smooth += (self.discord_level - self.discord_smooth) * alpha;
                    if self.mic_smooth < 0.001 && self.mic_level < 0.001 {
                        self.mic_smooth = 0.0;
                    }
                    if self.discord_smooth < 0.001 && self.discord_level < 0.001 {
                        self.discord_smooth = 0.0;
                    }
                }

                // Update progress polling
                if self.update_downloading {
                    if let Ok(prog) = self.state.update_progress.lock() {
                        if let Some(ref p) = *prog {
                            self.update_progress = Some(p.clone());
                        }
                    }
                }

                // Handle minimize-to-tray triggered from Win32 WM_SYSCOMMAND(SC_MINIMIZE)
                if take_pending_minimize_to_tray() {
                    return self.hide_to_tray_with_toast();
                }

                // Poll Tray events
                let mut tray_toggle = false;
                let mut tray_restore = false;
                let mut tray_exit = false;
                if let Some(ref tray) = self.tray {
                    while let Ok(ev) = TrayIconEvent::receiver().try_recv() {
                        if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = ev {
                            tray_toggle = true;
                        }
                    }
                    while let Ok(ev) = MenuEvent::receiver().try_recv() {
                        if ev.id == tray.open_id {
                            tray_restore = true;
                        } else if ev.id == tray.exit_id {
                            tray_exit = true;
                        }
                    }
                }

                // Update tray tooltip & disabled status menu item with live Recording/Replay clock
                self.sync_tray_status();

                if tray_exit {
                    return iced::exit();
                }
                if tray_restore {
                    return self.restore_from_tray();
                }
                if tray_toggle {
                    return self.toggle_main_visibility();
                }

                // Overlay HUD visibility & data sync
                self.hud_data = get_hud_data_sync(&self.state);
                let is_preview = self.state.hud_preview_mode.lock().map(|m| m.is_some()).unwrap_or(false);
                let should_show_overlay = if is_preview {
                    true
                } else {
                    self.config.hud_overlay_enabled && self.hud_data.visible && is_league_foreground(self.overlay_hwnd)
                };

                if should_show_overlay && self.overlay_id.is_none() {
                    let (id, open_task) = open_overlay_window(&self.config);
                    self.overlay_id = Some(id);
                    let mut tasks = vec![
                        open_task.map(Message::WindowOpened),
                        window::raw_id::<Message>(id).map(Message::OverlayRawId),
                    ];
                    if self.config.hud_locked && !is_preview {
                        tasks.push(window::enable_mouse_passthrough(id));
                    }
                    return Task::batch(tasks);
                } else if !should_show_overlay {
                    if let Some(id) = self.overlay_id.take() {
                        self.overlay_hwnd = 0;
                        return window::close(id);
                    }
                }

                if self.show_telemetry && self.tick_count % 12 == 0 {
                    self.telemetry = get_telemetry_sync(&self.state);
                    let gp = &self.telemetry.game_patch;
                    if !gp.is_empty() && gp != "—" && gp != "Detecting..." {
                        self.current_client_patch = gp.clone();
                    }
                }
                if had_new_logs && self.autoscroll && self.active_tab == Tab::Log {
                    return operation::snap_to(
                        self.log_scroll_id.clone(),
                        scrollable::RelativeOffset::END,
                    );
                }
                Task::none()
            }

            Message::TickSlow => {
                if self.main_id.is_some() && is_main_window_visible_win32(self.main_hwnd) {
                    return Task::perform(
                        async { tokio::task::spawn_blocking(get_system_health_sync).await.unwrap_or_default() },
                        Message::HealthLoaded,
                    );
                }
                Task::none()
            }

            Message::DeferredInit => {
                self.telemetry = get_telemetry_sync(&self.state);
                self.hud_data = get_hud_data_sync(&self.state);
                let cfg_cloud = self.config.clone();
                let mut tasks = vec![
                    Task::perform(
                        async { tokio::task::spawn_blocking(decode_app_icon_rgba).await.unwrap_or(None) },
                        Message::IconDecoded,
                    ),
                    Task::perform(
                        async { tokio::task::spawn_blocking(get_system_health_sync).await.unwrap_or_default() },
                        Message::HealthLoaded,
                    ),
                    Task::perform(
                        async { tokio::task::spawn_blocking(AudioCaptureManager::get_available_microphones).await.unwrap_or_default() },
                        Message::MicsLoaded,
                    ),
                    Task::perform(
                        async { tokio::task::spawn_blocking(get_recordings_sync).await.unwrap_or_default() },
                        Message::RecordingsLoaded,
                    ),
                    Task::perform(
                        async { updater::check_for_update(updater::CURRENT_VERSION).await },
                        |res| Message::UpdateCheckResult(true, res),
                    ),
                ];
                if !cfg_cloud.remote_server_url.is_empty() && !cfg_cloud.remote_api_key.is_empty() {
                    tasks.push(Task::perform(
                        async move {
                            let client = CloudSyncClient::new(&cfg_cloud.remote_server_url, &cfg_cloud.remote_api_key);
                            match client.check_health().await {
                                Ok(st) => (String::new(), st.role),
                                Err(_) => (String::new(), "unknown".to_string()),
                            }
                        },
                        |(msg, role)| Message::CloudStatusResult(msg, role),
                    ));
                }
                Task::batch(tasks)
            }

            Message::IconDecoded(maybe_rgba) => {
                if let Some((rgba, w, h)) = maybe_rgba {
                    if self.tray.is_none() {
                        self.tray = create_tray_icon_from_rgba(&rgba, w, h);
                    }
                    if let Some(mid) = self.main_id {
                        if let Ok(icon) = window::icon::from_rgba(rgba, w, h) {
                            return window::set_icon(mid, icon);
                        }
                    }
                }
                Task::none()
            }

            Message::WindowOpened(_) => Task::none(),

            Message::WindowClosed(id) => {
                if Some(id) == self.main_id {
                    self.main_id = None;
                    self.main_hwnd = 0;
                } else if Some(id) == self.overlay_id {
                    self.overlay_id = None;
                    self.overlay_hwnd = 0;
                } else if Some(id) == self.toast_id {
                    self.toast_id = None;
                }
                Task::none()
            }

            Message::WindowMoved(id, pt) => {
                if Some(id) == self.overlay_id {
                    self.config.hud_x = pt.x as i32;
                    self.config.hud_y = pt.y as i32;
                    let _ = self.config.save();
                }
                Task::none()
            }

            Message::WindowCloseRequested(id) => {
                if Some(id) == self.main_id {
                    // User clicked 'X': completely terminate and exit the application (1-to-1 with Tauri)
                    return iced::exit();
                }
                window::close(id)
            }

            Message::MainWindowRawId(raw) => {
                self.main_hwnd = raw as isize;
                install_main_window_subclass(self.main_hwnd);
                Task::none()
            }

            Message::OverlayRawId(raw) => {
                self.overlay_hwnd = raw as isize;
                Task::none()
            }

            Message::RestoreFromTray => self.restore_from_tray(),

            Message::HideToast(gen_id) => {
                if gen_id == self.toast_gen {
                    if let Some(tid) = self.toast_id.take() {
                        return window::close(tid);
                    }
                }
                Task::none()
            }

            Message::ExitApp => iced::exit(),

            Message::SelectTab(tab) => {
                self.active_tab = tab;
                if tab == Tab::Recordings {
                    return Task::perform(async { get_recordings_sync() }, Message::RecordingsLoaded);
                }
                Task::none()
            }

            Message::ToggleTelemetry => {
                self.show_telemetry = !self.show_telemetry;
                if self.show_telemetry {
                    self.telemetry = get_telemetry_sync(&self.state);
                }
                Task::none()
            }

            Message::ToggleTheme => {
                let is_currently_dark = theme::is_dark_theme(&self.config.theme);
                self.config.theme = if is_currently_dark {
                    "light".to_string()
                } else {
                    "dark".to_string()
                };
                self.save_config_now();
                self.state.log(&format!("🎨 Appearance theme set to: {}", self.config.theme));
                Task::none()
            }

            Message::ToggleRecording => {
                if self.state.rec_active.load(Ordering::SeqCst) {
                    self.state.stop_recording();
                } else {
                    self.state.start_recording();
                }
                Task::none()
            }

            Message::ToggleReplay => {
                if self.state.rep_active.load(Ordering::SeqCst) {
                    self.state.stop_replay();
                } else {
                    self.state.start_replay();
                }
                Task::none()
            }

            Message::EnableReplayApi => {
                match ReplayConfigManager::enable_replay_api() {
                    Ok(msg) => {
                        self.state.log(&format!("⚡ {}", msg.lines().next().unwrap_or("Replay API enabled.")));
                        self.health.replay_api_enabled = true;
                    }
                    Err(e) => self.state.log(&format!("⚠️ Failed to enable Replay API: {}", e)),
                }
                Task::none()
            }

            Message::RefreshHealth => {
                Task::perform(async { get_system_health_sync() }, Message::HealthLoaded)
            }

            Message::HealthLoaded(h) => {
                self.health = h;
                Task::none()
            }

            Message::RefreshMics => {
                self.state.log("🔄 Refreshing audio input devices...");
                Task::perform(
                    async { tokio::task::spawn_blocking(AudioCaptureManager::get_available_microphones).await.unwrap_or_default() },
                    Message::MicsLoaded,
                )
            }

            Message::MicsLoaded(m) => {
                if !m.is_empty() {
                    self.mics = m;
                }
                Task::none()
            }

            Message::SetReplayVolume(v) => {
                self.replay_volume_pct = v;
                self.is_muted = v == 0;
                if v > 0 {
                    self.prev_volume_pct = v;
                }
                let norm = (v as f32 / 100.0).clamp(0.0, 2.0);
                if let Ok(mut lk) = self.state.replay_volume.lock() {
                    *lk = norm;
                }
                if let Ok(mut lk) = self.state.current_player.lock() {
                    if let Some(ref mut p) = *lk {
                        p.set_volume(norm);
                    }
                }
                self.config.replay_volume = v;
                let _ = self.config.save();
                Task::none()
            }

            Message::ToggleMute => {
                let new_v = if self.is_muted {
                    self.prev_volume_pct.max(20)
                } else {
                    if self.replay_volume_pct > 0 {
                        self.prev_volume_pct = self.replay_volume_pct;
                    }
                    0
                };
                self.update(Message::SetReplayVolume(new_v))
            }

            Message::ToggleAutoscroll(enabled) => {
                self.autoscroll = enabled;
                if enabled {
                    return operation::snap_to(
                        self.log_scroll_id.clone(),
                        scrollable::RelativeOffset::END,
                    );
                }
                Task::none()
            }

            Message::CopyLog => {
                let joined = self.logs.join("\n");
                let msg = self.tr("log.copied", "Log copied to clipboard.");
                self.state.log(&msg);
                iced::clipboard::write(joined)
            }

            Message::ClearLogs => {
                self.logs.clear();
                Task::none()
            }

            Message::RefreshRecordings => {
                Task::perform(async { get_recordings_sync() }, Message::RecordingsLoaded)
            }

            Message::RecordingsLoaded(recs) => {
                self.recordings = recs;
                Task::none()
            }

            Message::OpenRecordingsFolder => {
                let _ = std::process::Command::new("explorer")
                    .arg(&self.config.record_folder)
                    .spawn();
                Task::none()
            }

            Message::PlayRecording(name) => {
                play_recording_sync(&name, &self.state);
                self.playing_file = Some(name);
                Task::none()
            }

            Message::StopPlayback => {
                stop_playback_sync(&self.state);
                self.playing_file = None;
                Task::none()
            }

            Message::StartEditMeta(filename) => {
                if self.can_upload_to_cloud() {
                    if let Some(rec) = self.recordings.iter().find(|r| r.filename == filename) {
                        let date_val = if !rec.recorded_at.is_empty() {
                            rec.recorded_at.chars().take(10).collect()
                        } else {
                            rec.date_str.clone()
                        };
                        let mut clean_notes = rec.notes.trim_start_matches('\u{FEFF}').trim().to_string();
                        let lower = clean_notes.to_lowercase();
                        if lower.contains("match length") || lower.starts_with("match ended at") {
                            clean_notes.clear();
                        }
                        self.edit_meta = Some(EditMetaState {
                            filename: rec.filename.clone(),
                            game_id: if rec.game_id.is_empty() {
                                rec.filename.trim_end_matches(".mp3").to_string()
                            } else {
                                rec.game_id.clone()
                            },
                            recorded_at: date_val,
                            game_patch: rec.game_patch.clone(),
                            team_tag: rec.team_tag.clone(),
                            notes: clean_notes,
                        });
                    }
                }
                Task::none()
            }

            Message::EditMetaDate(val) => {
                if let Some(ref mut m) = self.edit_meta { m.recorded_at = val; }
                Task::none()
            }

            Message::EditMetaTeam(val) => {
                if let Some(ref mut m) = self.edit_meta { m.team_tag = val; }
                Task::none()
            }

            Message::EditMetaNotes(val) => {
                if let Some(ref mut m) = self.edit_meta { m.notes = val; }
                Task::none()
            }

            Message::EditMetaPatch(val) => {
                if let Some(ref mut m) = self.edit_meta { m.game_patch = val; }
                Task::none()
            }

            Message::CancelEditMeta => {
                self.edit_meta = None;
                Task::none()
            }

            Message::SaveEditMeta => {
                if let Some(m) = self.edit_meta.take() {
                    let clean_patch = m.game_patch.trim().strip_prefix("patch ").or_else(|| m.game_patch.trim().strip_prefix("Patch "))
                        .unwrap_or(m.game_patch.trim())
                        .to_string();
                    let path = self.config.record_folder.join(&m.filename);
                    if path.exists() {
                        let updated = ScrimMetadata {
                            game_id: m.game_id,
                            team_tag: m.team_tag,
                            notes: m.notes,
                            game_patch: clean_patch,
                            recorded_at: m.recorded_at,
                        };
                        if let Err(e) = updated.write_to_mp3(&path) {
                            self.state.log(&format!("⚠️ Failed to save metadata: {}", e));
                        } else {
                            self.state.log(&format!("📝 Updated metadata for {}", m.filename));
                        }
                    }
                }
                Task::perform(async { get_recordings_sync() }, Message::RecordingsLoaded)
            }

            Message::SaveEditMetaAndUpload => {
                if let Some(m) = self.edit_meta.take() {
                    let filename = m.filename.clone();
                    let game_id = m.game_id.clone();
                    let clean_patch = m.game_patch.trim().strip_prefix("patch ").or_else(|| m.game_patch.trim().strip_prefix("Patch "))
                        .unwrap_or(m.game_patch.trim())
                        .to_string();
                    let path = self.config.record_folder.join(&filename);
                    if path.exists() {
                        let updated = ScrimMetadata {
                            game_id: game_id.clone(),
                            team_tag: m.team_tag,
                            notes: m.notes,
                            game_patch: clean_patch,
                            recorded_at: m.recorded_at,
                        };
                        if let Err(e) = updated.write_to_mp3(&path) {
                            self.state.log(&format!("⚠️ Failed to save metadata: {}", e));
                            return Task::perform(async { get_recordings_sync() }, Message::RecordingsLoaded);
                        }
                    }
                    if self.can_upload_to_cloud() {
                        return self.update(Message::CloudUpload(game_id));
                    }
                }
                Task::perform(async { get_recordings_sync() }, Message::RecordingsLoaded)
            }

            Message::RequestDeleteLocal(filename) => {
                let prompt = self.tr_fmt(
                    "recordings.delete_local_confirm_msg",
                    "Are you sure you want to delete {filename} from your local disk?",
                    &[("filename", &filename)],
                );
                self.confirm_delete = Some((ConfirmAction::DeleteLocal(filename), prompt));
                Task::none()
            }

            Message::RequestDeleteRemote(game_id) => {
                if !self.is_admin_role() {
                    self.state.log("⚠️ Only Admin role can delete recordings from the remote server.");
                    return Task::none();
                }
                let clean_id = game_id.trim_end_matches(".mp3").trim().to_string();
                let fname = format!("{}.mp3", clean_id);
                let prompt = self.tr_fmt(
                    "recordings.delete_remote_confirm_msg",
                    "Are you sure you want to permanently delete {filename} from the remote server?",
                    &[("filename", &fname)],
                );
                self.confirm_delete = Some((ConfirmAction::DeleteRemote(clean_id), prompt));
                Task::none()
            }

            Message::CancelDelete => {
                self.confirm_delete = None;
                Task::none()
            }

            Message::ConfirmDelete => {
                let Some((action, _)) = self.confirm_delete.take() else {
                    return Task::none();
                };
                match action {
                    ConfirmAction::DeleteLocal(filename) => {
                        let path = self.config.record_folder.join(&filename);
                        if path.exists() {
                            if let Err(e) = std::fs::remove_file(&path) {
                                self.state.log(&format!("⚠️ Failed to delete {}: {}", filename, e));
                            } else {
                                self.state.log(&format!("🗑 Deleted local recording: {}", filename));
                            }
                        }
                        Task::perform(async { get_recordings_sync() }, Message::RecordingsLoaded)
                    }
                    ConfirmAction::DeleteRemote(game_id) => {
                        self.deleting_ids.insert(game_id.clone());
                        let cfg = self.config.clone();
                        let gid = game_id.clone();
                        Task::perform(
                            async move {
                                let client = CloudSyncClient::new(&cfg.remote_server_url, &cfg.remote_api_key);
                                client.delete_recording(&gid).await.map(|_| ())
                            },
                            move |res| Message::CloudDeleteFinished(game_id, res),
                        )
                    }
                }
            }

            Message::CloudDeleteFinished(game_id, res) => {
                self.deleting_ids.remove(&game_id);
                match res {
                    Ok(_) => {
                        self.state.log(&format!("✓ Deleted {}.mp3 from remote server.", game_id));
                        self.remote_recordings.retain(|it| it.game_id.trim() != game_id);
                    }
                    Err(e) => {
                        self.state.log(&format!("⚠️ Failed to delete {}.mp3 from remote server: {}", game_id, e));
                    }
                }
                Task::perform(async { get_recordings_sync() }, Message::RecordingsLoaded)
            }

            Message::SetRecordFolder(val) => {
                self.config.record_folder = std::path::PathBuf::from(val);
                self.save_config_now();
                Task::none()
            }

            Message::BrowseRecordFolder => {
                Task::perform(
                    async {
                        rfd::AsyncFileDialog::new()
                            .pick_folder()
                            .await
                            .map(|h| h.path().to_string_lossy().to_string())
                    },
                    Message::FolderPicked,
                )
            }

            Message::FolderPicked(Some(p)) => {
                self.config.record_folder = std::path::PathBuf::from(p);
                self.save_config_now();
                Task::perform(async { get_recordings_sync() }, Message::RecordingsLoaded)
            }
            Message::FolderPicked(None) => Task::none(),

            Message::SetMicDevice(opt) => {
                self.config.mic_device_name = opt.value.clone();
                self.save_config_now();
                let label = if opt.value == "default" {
                    "Default System Microphone".to_string()
                } else {
                    opt.value.clone()
                };
                self.state.log(&format!("🎙️ Microphone target set to: {}", label));
                self.state.restart_preview(&opt.value);
                Task::none()
            }

            Message::SetLanguage(opt) => {
                self.config.language = opt.code.clone();
                i18n::set_language(&opt.code);
                self.translations = i18n::get_translations_for(&opt.code);
                self.save_config_now();
                self.state.log(&format!("🌐 Interface language switched to: {}", opt.name));
                Task::none()
            }

            Message::SetTheme(opt) => {
                self.config.theme = opt.value;
                self.save_config_now();
                self.state.log(&format!("🎨 Appearance theme set to: {}", self.config.theme));
                Task::none()
            }

            Message::SetAutoChamp(v) => {
                self.config.auto_start_champ_select = v;
                self.save_config_now();
                Task::none()
            }
            Message::SetAutoReplay(v) => {
                self.config.auto_start_replay = v;
                self.save_config_now();
                Task::none()
            }
            Message::SetMinimizeTray(v) => {
                self.config.minimize_to_tray = v;
                set_minimize_to_tray_flag(v);
                self.save_config_now();
                Task::none()
            }
            Message::SetHudEnabled(v) => {
                self.config.hud_overlay_enabled = v;
                self.state.hud_overlay_enabled.store(v, Ordering::SeqCst);
                self.save_config_now();
                Task::none()
            }
            Message::SetHudLocked(v) => {
                self.config.hud_locked = v;
                self.state.hud_locked.store(v, Ordering::SeqCst);
                self.save_config_now();
                if let Some(oid) = self.overlay_id {
                    if v {
                        return window::enable_mouse_passthrough(oid);
                    } else {
                        return window::disable_mouse_passthrough(oid);
                    }
                }
                Task::none()
            }
            Message::SetHudOpacity(v) => {
                self.config.hud_opacity = (v as f32 / 100.0).clamp(0.40, 1.0);
                self.save_config_now();
                Task::none()
            }
            Message::SetCloudUrl(v) => {
                self.config.remote_server_url = v;
                self.save_config_now();
                Task::none()
            }
            Message::SetCloudKey(v) => {
                self.config.remote_api_key = v;
                self.save_config_now();
                Task::none()
            }

            Message::PreviewRecHud => {
                if let Ok(mut m) = self.state.hud_preview_mode.lock() {
                    *m = Some("RECORD".to_string());
                }
                Task::none()
            }

            Message::PreviewReplayHud => {
                if let Ok(mut m) = self.state.hud_preview_mode.lock() {
                    *m = Some("REPLAY".to_string());
                }
                Task::none()
            }

            Message::HideHudPreview => {
                if let Ok(mut m) = self.state.hud_preview_mode.lock() {
                    *m = None;
                }
                if let Some(oid) = self.overlay_id.take() {
                    self.overlay_hwnd = 0;
                    return window::close(oid);
                }
                Task::none()
            }

            Message::ResetHudPosition => {
                self.config.hud_x = -1;
                self.config.hud_y = -1;
                let _ = self.config.save();
                self.state.log("🎯 Overlay position reset.");
                if let Some(oid) = self.overlay_id {
                    return window::move_to(oid, Point::new(100.0, 24.0));
                }
                Task::none()
            }

            Message::ToggleHudLock => {
                let next = !self.config.hud_locked;
                self.update(Message::SetHudLocked(next))
            }

            Message::DragOverlay => {
                if let Some(oid) = self.overlay_id {
                    return window::drag(oid);
                }
                Task::none()
            }

            Message::TestCloudConnection => {
                if self.config.remote_server_url.trim().is_empty() || self.config.remote_api_key.trim().is_empty() {
                    self.cloud_status_msg = "⚠️ Server URL and API Key required.".to_string();
                    return Task::none();
                }
                let cfg = self.config.clone();
                self.cloud_status_msg = self.tr("settings.cloud_testing", "Connecting...");
                Task::perform(
                    async move {
                        let client = CloudSyncClient::new(&cfg.remote_server_url, &cfg.remote_api_key);
                        match client.check_health().await {
                            Ok(st) => (st.message, st.role),
                            Err(e) => (format!("⚠️ {}", e), "unknown".to_string()),
                        }
                    },
                    |(msg, role)| Message::CloudStatusResult(msg, role),
                )
            }

            Message::CloudStatusResult(msg, role) => {
                if role != "unknown" {
                    self.cloud_role = role.clone();
                }
                if !msg.is_empty() {
                    if msg == "200 OK" {
                        self.cloud_status_msg = match self.cloud_role.as_str() {
                            "admin" => self.tr("settings.cloud_connected_admin", "✔ Connected (Full Admin)"),
                            "upload" => self.tr("settings.cloud_connected_upload", "✔ Connected (Upload & Download)"),
                            _ => self.tr("settings.cloud_connected_pull", "✔ Connected (Download Only)"),
                        };
                        return self.update(Message::SyncRemote);
                    } else {
                        self.cloud_status_msg = msg;
                    }
                }
                Task::none()
            }

            Message::SyncRemote => {
                if self.is_syncing_remote {
                    return Task::none();
                }
                self.is_syncing_remote = true;
                let cfg = self.config.clone();
                Task::perform(
                    async move {
                        let client = CloudSyncClient::new(&cfg.remote_server_url, &cfg.remote_api_key);
                        let role = match client.check_health().await {
                            Ok(st) => st.role,
                            Err(_) => "unknown".to_string(),
                        };
                        let list = client.list_recordings().await;
                        (role, list)
                    },
                    |(role, res)| Message::RemoteSynced(role, res),
                )
            }

            Message::RemoteSynced(role, res) => {
                self.is_syncing_remote = false;
                if role != "unknown" {
                    self.cloud_role = role;
                }
                match res {
                    Ok(items) => {
                        let count = items.len();
                        self.remote_recordings = items;
                        self.state.log(&format!("☁️ Remote scrims synced: {} recording(s) available on server.", count));
                    }
                    Err(e) => {
                        self.state.log(&format!("⚠️ Cloud sync failed: {}", e));
                    }
                }
                Task::perform(async { get_recordings_sync() }, Message::RecordingsLoaded)
            }

            Message::CloudDownloadSingle(game_id) => {
                let clean_id = game_id.trim().to_string();
                self.downloading_ids.insert(clean_id.clone());
                let cfg = self.config.clone();
                let gid_clone = clean_id.clone();
                Task::perform(
                    async move {
                        let _ = std::fs::create_dir_all(&cfg.record_folder);
                        let dest = cfg.record_folder.join(format!("{}.mp3", gid_clone));
                        let client = CloudSyncClient::new(&cfg.remote_server_url, &cfg.remote_api_key);
                        client.download_recording(&gid_clone, &dest).await
                    },
                    move |res| Message::CloudDownloadSingleFinished(clean_id, res),
                )
            }

            Message::CloudDownloadSingleFinished(game_id, res) => {
                self.downloading_ids.remove(&game_id);
                match res {
                    Ok(_) => self.state.log(&format!("✓ Downloaded {}.mp3 successfully.", game_id)),
                    Err(e) => self.state.log(&format!("⚠️ Download failed for {}.mp3: {}", game_id, e)),
                }
                Task::perform(async { get_recordings_sync() }, Message::RecordingsLoaded)
            }

            Message::CloudUpload(game_id) => {
                if !self.can_upload_to_cloud() {
                    self.state.log(&format!("⚠️ Upload not permitted with active role: {}", self.cloud_role));
                    return Task::none();
                }
                let clean_id = game_id.trim_end_matches(".mp3").trim().to_string();
                self.uploading_ids.insert(clean_id.clone());
                let cfg = self.config.clone();
                let gid_clone = clean_id.clone();
                Task::perform(
                    async move {
                        let client = CloudSyncClient::new(&cfg.remote_server_url, &cfg.remote_api_key);
                        let path = cfg.record_folder.join(format!("{}.mp3", gid_clone));
                        let meta = ScrimMetadata::read_from_mp3(&path).ok();
                        let team_tag = meta.as_ref().map(|m| m.team_tag.as_str()).filter(|s| !s.is_empty());
                        let notes = meta.as_ref().map(|m| m.notes.as_str()).filter(|s| !s.is_empty());
                        let patch = meta.as_ref().map(|m| m.game_patch.as_str()).filter(|s| !s.is_empty());
                        client.upload_recording(&gid_clone, &path, team_tag, notes, patch).await.map(|_| ())
                    },
                    move |res| Message::CloudUploadFinished(clean_id, res),
                )
            }

            Message::CloudUploadFinished(game_id, res) => {
                self.uploading_ids.remove(&game_id);
                match res {
                    Ok(_) => {
                        self.state.log(&format!("✓ Uploaded {}.mp3 successfully to server.", game_id));
                        if !self.remote_recordings.iter().any(|it| it.game_id.trim() == game_id) {
                            self.remote_recordings.push(CloudRecordingDto {
                                game_id,
                                ..Default::default()
                            });
                        }
                    }
                    Err(e) => {
                        self.state.log(&format!("⚠️ Upload failed for {}.mp3: {}", game_id, e));
                    }
                }
                Task::perform(async { get_recordings_sync() }, Message::RecordingsLoaded)
            }

            Message::CloudDownloadAll => {
                let cfg = self.config.clone();
                let local_ids: HashSet<String> = self
                    .recordings
                    .iter()
                    .map(|r| {
                        if r.game_id.is_empty() {
                            r.filename.trim_end_matches(".mp3").trim().to_string()
                        } else {
                            r.game_id.trim().to_string()
                        }
                    })
                    .collect();
                let missing: Vec<String> = self
                    .remote_recordings
                    .iter()
                    .map(|it| it.game_id.trim().to_string())
                    .filter(|gid| !gid.is_empty() && !local_ids.contains(gid))
                    .collect();

                Task::perform(
                    async move {
                        let client = CloudSyncClient::new(&cfg.remote_server_url, &cfg.remote_api_key);
                        let _ = std::fs::create_dir_all(&cfg.record_folder);
                        let mut count = 0usize;
                        for gid in missing {
                            let dest = cfg.record_folder.join(format!("{}.mp3", gid));
                            if !dest.exists() && client.download_recording(&gid, &dest).await.is_ok() {
                                count += 1;
                            }
                        }
                        Ok(count)
                    },
                    Message::CloudDownloadAllFinished,
                )
            }

            Message::CloudDownloadAllFinished(res) => {
                match res {
                    Ok(count) => self.state.log(&format!("✓ Batch download finished: {} scrim file(s) downloaded.", count)),
                    Err(e) => self.state.log(&format!("⚠️ Batch download failed: {}", e)),
                }
                Task::perform(async { get_recordings_sync() }, Message::RecordingsLoaded)
            }

            Message::CheckForUpdates(silent) => {
                if !silent {
                    self.update_status_msg = "Checking GitHub Releases...".to_string();
                }
                Task::perform(
                    async { updater::check_for_update(updater::CURRENT_VERSION).await },
                    move |res| Message::UpdateCheckResult(silent, res),
                )
            }

            Message::UpdateCheckResult(silent, res) => {
                match res {
                    Ok(info) => {
                        if info.available {
                            self.state.log(&format!("🌟 New version available: {}", info.latest_version));
                            self.update_status_msg = format!("New version available: {}", info.latest_version);
                            self.update_info = Some(info);
                        } else {
                            if !silent {
                                self.update_status_msg = format!("✓ Up to date ({})", info.current_version);
                            }
                            self.update_info = Some(info);
                        }
                    }
                    Err(e) => {
                        if !silent {
                            self.update_status_msg = format!("⚠️ {}", e);
                        }
                    }
                }
                Task::none()
            }

            Message::DismissUpdateBanner => {
                self.update_banner_dismissed = true;
                Task::none()
            }

            Message::ApplyUpdate => {
                if let Some(ref info) = self.update_info {
                    if let Some(ref url) = info.download_url {
                        self.update_downloading = true;
                        self.update_banner_dismissed = false;
                        let url_clone = url.clone();
                        let prog_lock = self.state.update_progress.clone();
                        return Task::perform(
                            async move {
                                let cb = Box::new(move |downloaded: u64, total: u64, pct: u32, status: &str| {
                                    if let Ok(mut lk) = prog_lock.lock() {
                                        *lk = Some(DownloadProgress {
                                            downloaded,
                                            total,
                                            percentage: pct,
                                            status: status.to_string(),
                                        });
                                    }
                                });
                                updater::download_and_apply_update(&url_clone, Some(cb)).await
                            },
                            Message::UpdateApplyFinished,
                        );
                    }
                }
                Task::none()
            }

            Message::UpdateApplyFinished(res) => {
                self.update_downloading = false;
                if let Err(e) = res {
                    self.update_status_msg = format!("⚠️ Update failed: {}", e);
                    self.state.log(&format!("⚠️ Update failed: {}", e));
                }
                Task::none()
            }

            Message::OpenReleasePage(url) => {
                #[cfg(windows)]
                {
                    let _ = std::process::Command::new("cmd")
                        .args(["/C", "start", "", &url])
                        .spawn();
                }
                Task::none()
            }
        }
    }

    pub fn view(&self, window: window::Id) -> Element<'_, Message> {
        if Some(window) == self.overlay_id {
            overlay_view::view_overlay(&self.hud_data, self.wave_phase)
        } else if Some(window) == self.toast_id {
            overlay_view::view_toast(self.logo_handle.as_ref())
        } else {
            main_view::view_main(self)
        }
    }

    fn hide_to_tray_with_toast(&mut self) -> Task<Message> {
        hide_main_window_win32(self.main_hwnd);
        play_tray_chime();
        self.toast_gen = self.toast_gen.wrapping_add(1);
        let gen_id = self.toast_gen;

        let mut tasks = Vec::new();
        if let Some(old_tid) = self.toast_id.take() {
            tasks.push(window::close(old_tid));
        }
        let (toast_id, open_toast) = open_toast_window();
        self.toast_id = Some(toast_id);
        tasks.push(open_toast.map(Message::WindowOpened));
        tasks.push(Task::perform(
            async move {
                tokio::time::sleep(std::time::Duration::from_millis(3500)).await;
            },
            move |_| Message::HideToast(gen_id),
        ));
        Task::batch(tasks)
    }

    fn restore_from_tray(&mut self) -> Task<Message> {
        let mut tasks = Vec::new();
        if let Some(tid) = self.toast_id.take() {
            tasks.push(window::close(tid));
        }
        if let Some(mid) = self.main_id {
            restore_main_window_win32(self.main_hwnd);
            tasks.push(window::minimize(mid, false));
            tasks.push(window::gain_focus(mid));
        } else {
            let (mid, open_task) = open_main_window();
            self.main_id = Some(mid);
            tasks.push(open_task.map(Message::WindowOpened));
            tasks.push(window::raw_id::<Message>(mid).map(Message::MainWindowRawId));
            tasks.push(window::gain_focus(mid));
        }
        Task::batch(tasks)
    }

    fn toggle_main_visibility(&mut self) -> Task<Message> {
        if self.main_id.is_none() || !is_main_window_visible_win32(self.main_hwnd) {
            self.restore_from_tray()
        } else {
            self.hide_to_tray_with_toast()
        }
    }

    fn sync_tray_status(&mut self) {
        let Some(ref mut tray) = self.tray else {
            return;
        };

        let is_rec = self.state.rec_active.load(Ordering::SeqCst);
        let is_rep = self.state.rep_active.load(Ordering::SeqCst);
        let clk_str = self
            .state
            .match_time
            .lock()
            .ok()
            .and_then(|c| c.clone())
            .unwrap_or_else(|| "00:00".to_string());
        let rec_stat = self.state.rec_status.lock().map(|s| s.0.clone()).unwrap_or_default();
        let rep_stat = self.state.rep_status.lock().map(|s| s.0.clone()).unwrap_or_default();

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

        if tooltip_str != tray.last_tooltip {
            let _ = tray.icon.set_tooltip(Some(&tooltip_str));
            tray.last_tooltip = tooltip_str;
        }

        if menu_status_str != tray.last_menu_status {
            tray.status_item.set_text(&menu_status_str);
            tray.last_menu_status = menu_status_str;
        }
    }
}

fn open_main_window() -> (window::Id, Task<window::Id>) {
    window::open(window::Settings {
        size: Size::new(1100.0, 860.0),
        min_size: Some(Size::new(920.0, 720.0)),
        position: Position::Centered,
        visible: true,
        resizable: true,
        decorations: true,
        transparent: false,
        exit_on_close_request: false,
        icon: None,
        ..Default::default()
    })
}

fn open_overlay_window(cfg: &AppConfig) -> (window::Id, Task<window::Id>) {
    let pos = if cfg.hud_x >= 0 && cfg.hud_y >= 0 {
        Position::Specific(Point::new(cfg.hud_x as f32, cfg.hud_y as f32))
    } else {
        Position::Specific(Point::new(100.0, 24.0))
    };

    window::open(window::Settings {
        size: Size::new(350.0, 78.0),
        position: pos,
        visible: true,
        resizable: false,
        decorations: false,
        transparent: true,
        level: Level::AlwaysOnTop,
        exit_on_close_request: false,
        platform_specific: PlatformSpecific {
            skip_taskbar: true,
            undecorated_shadow: false,
            ..Default::default()
        },
        ..Default::default()
    })
}

fn toast_bottom_right_position() -> Point {
    #[cfg(windows)]
    {
        #[repr(C)]
        struct RECT {
            left: i32,
            top: i32,
            right: i32,
            bottom: i32,
        }
        unsafe extern "system" {
            fn SystemParametersInfoW(
                uiAction: u32,
                uiParam: u32,
                pvParam: *mut std::ffi::c_void,
                fWinIni: u32,
            ) -> i32;
            fn GetDpiForSystem() -> u32;
        }
        const SPI_GETWORKAREA: u32 = 0x0030;
        let mut rect = RECT {
            left: 0,
            top: 0,
            right: 0,
            bottom: 0,
        };
        let ok = unsafe {
            SystemParametersInfoW(
                SPI_GETWORKAREA,
                0,
                &mut rect as *mut RECT as *mut std::ffi::c_void,
                0,
            )
        };
        if ok != 0 && rect.right > 0 && rect.bottom > 0 {
            let dpi = unsafe { GetDpiForSystem() };
            let scale = if dpi >= 96 { dpi as f32 / 96.0 } else { 1.0 };
            let right = rect.right as f32 / scale;
            let bottom = rect.bottom as f32 / scale;
            let x = (right - 360.0 - 16.0).max(0.0);
            let y = (bottom - 76.0 - 16.0).max(0.0);
            return Point::new(x, y);
        }
    }
    Point::new(32.0, 32.0)
}

fn open_toast_window() -> (window::Id, Task<window::Id>) {
    window::open(window::Settings {
        size: Size::new(360.0, 76.0),
        position: Position::Specific(toast_bottom_right_position()),
        visible: true,
        resizable: false,
        decorations: false,
        transparent: true,
        level: Level::AlwaysOnTop,
        exit_on_close_request: false,
        platform_specific: PlatformSpecific {
            skip_taskbar: true,
            undecorated_shadow: false,
            ..Default::default()
        },
        ..Default::default()
    })
}

fn load_logo_handle() -> Option<image::Handle> {
    let bytes = include_bytes!("../../assets/icon.png");
    Some(image::Handle::from_bytes(bytes.to_vec()))
}

fn decode_app_icon_rgba() -> Option<(Vec<u8>, u32, u32)> {
    let bytes = include_bytes!("../../assets/icon.png");
    let img = ::image::load_from_memory(bytes).ok()?.into_rgba8();
    let (w, h) = img.dimensions();
    Some((img.into_raw(), w, h))
}

fn create_tray_icon_from_rgba(rgba: &[u8], w: u32, h: u32) -> Option<TrayHandle> {
    let icon = tray_icon::Icon::from_rgba(rgba.to_vec(), w, h).ok()?;

    let initial_status = "League Voice Recorder: Idle".to_string();
    let open_item = MenuItem::new("Open", true, None);
    let status_item = MenuItem::new(&initial_status, false, None);
    let exit_item = MenuItem::new("Exit", true, None);
    let open_id = open_item.id().clone();
    let exit_id = exit_item.id().clone();

    let menu = Menu::new();
    let _ = menu.append(&open_item);
    let _ = menu.append(&status_item);
    let _ = menu.append(&exit_item);

    let tray_icon = TrayIconBuilder::new()
        .with_menu(Box::new(menu))
        .with_tooltip(&initial_status)
        .with_icon(icon)
        .with_menu_on_left_click(false)
        .build()
        .ok()?;

    Some(TrayHandle {
        icon: tray_icon,
        open_id,
        status_item,
        exit_id,
        last_tooltip: initial_status.clone(),
        last_menu_status: initial_status,
    })
}

fn play_tray_chime() {
    #[cfg(windows)]
    {
        unsafe extern "system" {
            fn MessageBeep(uType: u32) -> i32;
        }
        unsafe { MessageBeep(0x00000040) };
    }
}

#[cfg(windows)]
mod win32_tray {
    use std::sync::atomic::{AtomicBool, AtomicIsize, Ordering};

    #[repr(C)]
    struct RECT {
        left: i32,
        top: i32,
        right: i32,
        bottom: i32,
    }

    unsafe extern "system" {
        fn SetWindowLongPtrW(hWnd: isize, nIndex: i32, dwNewLong: isize) -> isize;
        fn CallWindowProcW(lpPrevWndFunc: isize, hWnd: isize, Msg: u32, wParam: usize, lParam: isize) -> isize;
        fn ShowWindow(hWnd: isize, nCmdShow: i32) -> i32;
        fn SetForegroundWindow(hWnd: isize) -> i32;
        fn IsWindowVisible(hWnd: isize) -> i32;
        fn IsIconic(hWnd: isize) -> i32;
        fn GetWindowRect(hWnd: isize, lpRect: *mut RECT) -> i32;
    }

    static PREV_MAIN_WND_PROC: AtomicIsize = AtomicIsize::new(0);
    static SUBCLASSED_HWND: AtomicIsize = AtomicIsize::new(0);
    static TITLE_MINIMIZE_CLICKED: AtomicBool = AtomicBool::new(false);
    static MINIMIZE_TO_TRAY_ENABLED: AtomicBool = AtomicBool::new(true);
    static PENDING_MINIMIZE_TO_TRAY: AtomicBool = AtomicBool::new(false);

    pub fn set_minimize_to_tray(enabled: bool) {
        MINIMIZE_TO_TRAY_ENABLED.store(enabled, Ordering::SeqCst);
    }

    pub fn take_pending_minimize() -> bool {
        PENDING_MINIMIZE_TO_TRAY.swap(false, Ordering::SeqCst)
    }

    pub fn hide_window(hwnd: isize) {
        if hwnd != 0 {
            unsafe { ShowWindow(hwnd, 0) }; // SW_HIDE
        }
    }

    pub fn restore_window(hwnd: isize) {
        if hwnd != 0 {
            unsafe {
                ShowWindow(hwnd, 9); // SW_RESTORE
                SetForegroundWindow(hwnd);
            }
        }
    }

    pub fn is_window_visible(hwnd: isize) -> bool {
        if hwnd == 0 {
            return true;
        }
        unsafe { IsWindowVisible(hwnd) != 0 && IsIconic(hwnd) == 0 }
    }

    pub fn install_subclass(hwnd: isize) {
        if hwnd == 0 || SUBCLASSED_HWND.load(Ordering::SeqCst) == hwnd {
            return;
        }
        SUBCLASSED_HWND.store(hwnd, Ordering::SeqCst);
        unsafe {
            let prev = SetWindowLongPtrW(hwnd, -4, main_window_subclass_proc as *const () as isize);
            PREV_MAIN_WND_PROC.store(prev, Ordering::SeqCst);
        }
    }

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
        } else if msg == WM_SYSCOMMAND && (wparam & 0xFFF0) == SC_MINIMIZE {
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

            if is_from_min_button && MINIMIZE_TO_TRAY_ENABLED.load(Ordering::SeqCst) {
                unsafe { ShowWindow(hwnd, 0) }; // SW_HIDE
                PENDING_MINIMIZE_TO_TRAY.store(true, Ordering::SeqCst);
                return 0;
            }
        }

        let prev = PREV_MAIN_WND_PROC.load(Ordering::SeqCst);
        if prev != 0 {
            unsafe { CallWindowProcW(prev, hwnd, msg, wparam, lparam) }
        } else {
            0
        }
    }
}

fn set_minimize_to_tray_flag(enabled: bool) {
    #[cfg(windows)]
    win32_tray::set_minimize_to_tray(enabled);
    #[cfg(not(windows))]
    let _ = enabled;
}

fn take_pending_minimize_to_tray() -> bool {
    #[cfg(windows)]
    { win32_tray::take_pending_minimize() }
    #[cfg(not(windows))]
    { false }
}

fn install_main_window_subclass(hwnd: isize) {
    #[cfg(windows)]
    win32_tray::install_subclass(hwnd);
    #[cfg(not(windows))]
    let _ = hwnd;
}

fn hide_main_window_win32(hwnd: isize) {
    #[cfg(windows)]
    win32_tray::hide_window(hwnd);
    #[cfg(not(windows))]
    let _ = hwnd;
}

fn restore_main_window_win32(hwnd: isize) {
    #[cfg(windows)]
    win32_tray::restore_window(hwnd);
    #[cfg(not(windows))]
    let _ = hwnd;
}

fn is_main_window_visible_win32(hwnd: isize) -> bool {
    #[cfg(windows)]
    { win32_tray::is_window_visible(hwnd) }
    #[cfg(not(windows))]
    { let _ = hwnd; true }
}
