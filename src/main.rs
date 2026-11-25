#![windows_subsystem = "windows"]

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tracing::info;

use league_voice_recorder::{
    app_state::{init_logging, AppState},
    config::AppConfig,
    record::LeagueRecorder,
    replay::ReplaySynchronizer,
    ui::App,
};

#[cfg(windows)]
unsafe extern "system" {
    fn AttachConsole(dwProcessId: u32) -> i32;
    fn CreateMutexW(lpMutexAttributes: *const std::ffi::c_void, bInitialOwner: i32, lpName: *const u16) -> isize;
    fn GetLastError() -> u32;
}

#[cfg(windows)]
fn ensure_single_instance() -> bool {
    let name: Vec<u16> = "Local\\LeagueVoiceRecorder_SingleInstance\0".encode_utf16().collect();
    let handle = unsafe { CreateMutexW(std::ptr::null(), 1, name.as_ptr()) };
    if handle == 0 {
        return true;
    }
    const ERROR_ALREADY_EXISTS: u32 = 183;
    unsafe { GetLastError() != ERROR_ALREADY_EXISTS }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().collect();
    if args.iter().any(|arg| arg == "--headless" || arg == "--cli") {
        let rt = tokio::runtime::Runtime::new()?;
        return rt.block_on(async {
            #[cfg(windows)]
            unsafe {
                AttachConsole(0xFFFFFFFF);
            }
            let state = Arc::new(AppState::default());
            init_logging(state.log_queue.clone());
            info!("🎮 League Voice Recorder v{} (headless CLI)", env!("CARGO_PKG_VERSION"));

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
            Ok(())
        });
    }

    #[cfg(windows)]
    if !ensure_single_instance() {
        return Ok(());
    }

    // Build tokio runtime for background audio/API/watcher tasks
    let rt = tokio::runtime::Runtime::new()?;
    let _guard = rt.enter();

    let state = Arc::new(AppState::default());
    init_logging(state.log_queue.clone());
    info!("🎮 League Voice Recorder v{} (Native iced GPU UI)", env!("CARGO_PKG_VERSION"));
    state.spawn_auto_detect_monitor();

    // Prefer DX12 directly on Windows to skip Vulkan/GL ICD enumeration on startup
    // (iced_renderer still falls back to tiny-skia if GPU initialization fails).
    #[cfg(windows)]
    if std::env::var_os("WGPU_BACKEND").is_none() {
        unsafe {
            std::env::set_var("WGPU_BACKEND", "dx12");
        }
    }

    let boot_state = state.clone();
    let mut daemon = iced::daemon(move || App::boot(boot_state.clone()), App::update, App::view)
        .title(App::title)
        .theme(App::theme)
        .style(App::style)
        .default_font(iced::Font::with_name("Segoe UI"))
        .subscription(App::subscription);

    #[cfg(windows)]
    {
        let fonts_dir = std::env::var_os("WINDIR")
            .map(std::path::PathBuf::from)
            .unwrap_or_else(|| std::path::PathBuf::from(r"C:\Windows"))
            .join("Fonts");
        for font_file in [
            "segoeui.ttf",
            "seguisb.ttf",
            "segoeuib.ttf",
            "consola.ttf",
            "consolab.ttf",
            "seguiemj.ttf",
            "seguisym.ttf",
        ] {
            if let Ok(bytes) = std::fs::read(fonts_dir.join(font_file)) {
                daemon = daemon.font(bytes);
            }
        }
    }

    daemon.run()?;

    Ok(())
}
