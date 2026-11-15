use league_voice_recorder::{config::AppConfig, record::LeagueRecorder, replay::ReplaySynchronizer};
use tracing::info;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt::init();
    info!("League Voice Recorder Core Engine (Rust v0.5.1)");

    let config = AppConfig::load()?;
    info!("Configuration loaded from: {:?}", AppConfig::config_path());

    // Scaffolding demonstration for Core Watcher loops
    let _recorder = LeagueRecorder::new(config.clone());
    let _replay_sync = ReplaySynchronizer::new(config);

    info!("Engines initialized successfully.");
    Ok(())
}
