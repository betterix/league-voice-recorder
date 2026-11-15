use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use league_voice_recorder::{
    config::AppConfig,
    record::LeagueRecorder,
    replay::ReplaySynchronizer,
};
use tracing::info;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt::init();
    info!("🎮 League Voice Recorder Core Engine (Rust v0.5.1)");

    let config = AppConfig::load()?;
    info!("📁 Config file: {:?}", AppConfig::config_path());
    info!("🎙️ Record folder: {:?}", config.record_folder);

    let stop_signal = Arc::new(AtomicBool::new(false));
    let stop_signal_ctrlc = stop_signal.clone();

    tokio::spawn(async move {
        if tokio::signal::ctrl_c().await.is_ok() {
            info!("🛑 Shutdown signal received (Ctrl+C). Exiting watchers...");
            stop_signal_ctrlc.store(true, Ordering::SeqCst);
        }
    });

    let recorder = LeagueRecorder::new(config.clone());
    let replay_sync = ReplaySynchronizer::new(config);

    info!("👀 Watchers active: Polling for live matches (:2999) and active ROFL replays...");
    info!("💡 Press Ctrl+C at any time to stop.");

    let rec_stop = stop_signal.clone();
    let rep_stop = stop_signal.clone();

    let rec_handle = tokio::spawn(async move {
        recorder.run_watcher_loop(rec_stop).await;
    });

    let rep_handle = tokio::spawn(async move {
        replay_sync.run_sync_watcher(rep_stop).await;
    });

    let _ = tokio::join!(rec_handle, rep_handle);

    info!("Clean exit completed.");
    Ok(())
}
