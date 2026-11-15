pub mod secret;

use std::path::PathBuf;
use serde::{Deserialize, Serialize};
use directories::UserDirs;

pub use secret::{decrypt_secret, encrypt_secret};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppConfig {
    #[serde(default = "default_target_pid")]
    pub target_audio_pid: i32,
    #[serde(default = "default_target_name")]
    pub target_audio_name: String,
    #[serde(default = "default_mic_index")]
    pub mic_device_index: i32,
    #[serde(default = "default_mic_name")]
    pub mic_device_name: String,
    #[serde(default = "default_record_folder")]
    pub record_folder: PathBuf,
    #[serde(default = "default_vlc_dir")]
    pub vlc_dir: PathBuf,
    #[serde(default = "default_replay_volume")]
    pub replay_volume: u32,
    #[serde(default = "default_language")]
    pub language: String,
    #[serde(default = "default_true")]
    pub auto_start_champ_select: bool,
    #[serde(default = "default_true")]
    pub auto_start_replay: bool,
    #[serde(default = "default_audio_format")]
    pub audio_format: String,
    #[serde(default = "default_true")]
    pub minimize_to_tray: bool,
    #[serde(default)]
    pub remote_server_url: String,
    #[serde(default)]
    pub remote_api_key_encrypted: String,
    #[serde(skip)]
    pub remote_api_key: String,
}

fn default_target_pid() -> i32 { -1 }
fn default_target_name() -> String { "discord_auto".to_string() }
fn default_mic_index() -> i32 { -1 }
fn default_mic_name() -> String { "default".to_string() }
fn default_record_folder() -> PathBuf {
    let docs = UserDirs::new()
        .and_then(|u| u.document_dir().map(|p| p.to_path_buf()))
        .unwrap_or_else(|| PathBuf::from("."));
    docs.join("League Voice Recorder").join("recordings")
}
fn default_vlc_dir() -> PathBuf { PathBuf::from(r"C:\Program Files\VideoLAN\VLC") }
fn default_replay_volume() -> u32 { 100 }
fn default_language() -> String { "en".to_string() }
fn default_true() -> bool { true }
fn default_audio_format() -> String { "mp3".to_string() }

impl Default for AppConfig {
    fn default() -> Self {
        Self {
            target_audio_pid: default_target_pid(),
            target_audio_name: default_target_name(),
            mic_device_index: default_mic_index(),
            mic_device_name: default_mic_name(),
            record_folder: default_record_folder(),
            vlc_dir: default_vlc_dir(),
            replay_volume: default_replay_volume(),
            language: default_language(),
            auto_start_champ_select: true,
            auto_start_replay: true,
            audio_format: default_audio_format(),
            minimize_to_tray: true,
            remote_server_url: String::new(),
            remote_api_key_encrypted: String::new(),
            remote_api_key: String::new(),
        }
    }
}

impl AppConfig {
    pub fn config_path() -> PathBuf {
        let docs = UserDirs::new()
            .and_then(|u| u.document_dir().map(|p| p.to_path_buf()))
            .unwrap_or_else(|| PathBuf::from("."));
        docs.join("League Voice Recorder").join("config.json")
    }

    pub fn load() -> Result<Self, Box<dyn std::error::Error>> {
        let path = Self::config_path();
        if path.exists() {
            let content = std::fs::read_to_string(&path)?;
            let mut config: Self = serde_json::from_str(&content)?;
            if !config.remote_api_key_encrypted.is_empty() {
                config.remote_api_key = decrypt_secret(&config.remote_api_key_encrypted);
            }
            // Ensure recording directory exists
            if !config.record_folder.exists() {
                let _ = std::fs::create_dir_all(&config.record_folder);
            }
            Ok(config)
        } else {
            let default = Self::default();
            default.save()?;
            Ok(default)
        }
    }

    pub fn save(&self) -> Result<(), Box<dyn std::error::Error>> {
        let path = Self::config_path();
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }

        let mut to_save = self.clone();
        if !to_save.remote_api_key.is_empty() {
            to_save.remote_api_key_encrypted = encrypt_secret(&to_save.remote_api_key);
        }

        let json = serde_json::to_string_pretty(&to_save)?;
        std::fs::write(&path, json)?;
        Ok(())
    }
}
