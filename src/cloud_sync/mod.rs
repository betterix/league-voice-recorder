use std::path::Path;
use reqwest::header::{HeaderMap, HeaderValue};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::fs::File;
use std::io::Read;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CloudRecording {
    pub game_id: String,
    pub filename: String,
    pub file_size: u64,
    pub recorded_at: Option<String>,
    pub team_tag: Option<String>,
    pub sha256: Option<String>,
}

#[derive(Debug, Clone)]
pub struct CloudSyncClient {
    pub server_url: String,
    pub api_key: String,
    pub timeout_secs: u64,
}

impl CloudSyncClient {
    pub fn new(server_url: &str, api_key: &str) -> Self {
        Self {
            server_url: server_url.trim().trim_end_matches('/').to_string(),
            api_key: api_key.trim().to_string(),
            timeout_secs: 15,
        }
    }

    pub fn is_configured(&self) -> bool {
        !self.server_url.is_empty() && !self.api_key.is_empty()
    }

    fn build_client(&self) -> reqwest::Client {
        let mut headers = HeaderMap::new();
        if let Ok(key_val) = HeaderValue::from_str(&self.api_key) {
            headers.insert("X-API-Key", key_val);
        }
        headers.insert("User-Agent", HeaderValue::from_static("LeagueVoiceRecorder-Rust/0.5.1"));

        reqwest::Client::builder()
            .default_headers(headers)
            .timeout(std::time::Duration::from_secs(self.timeout_secs))
            .danger_accept_invalid_certs(true) // Support self-signed certificates
            .build()
            .unwrap_or_default()
    }

    pub async fn check_health(&self) -> Result<Value, String> {
        if !self.is_configured() {
            return Err("Server URL and API Key must both be configured".to_string());
        }

        let url = format!("{}/api/v1/health", self.server_url);
        let client = self.build_client();

        let resp = client.get(&url).send().await
            .map_err(|e| format!("Connection error: {}", e))?;

        let status = resp.status();
        if status.is_success() {
            let json = resp.json::<Value>().await
                .map_err(|e| format!("Failed to parse response JSON: {}", e))?;
            Ok(json)
        } else if status.as_u16() == 401 {
            Err("Invalid API key (401 Unauthorized)".to_string())
        } else if status.as_u16() == 403 {
            Err("Forbidden: Key does not have read permissions (403)".to_string())
        } else {
            Err(format!("Server returned HTTP {}", status))
        }
    }

    pub async fn list_recordings(&self) -> Result<Vec<CloudRecording>, String> {
        if !self.is_configured() {
            return Err("Server URL and API Key not configured".to_string());
        }

        let url = format!("{}/api/v1/recordings", self.server_url);
        let client = self.build_client();

        let resp = client.get(&url).send().await
            .map_err(|e| format!("Connection error: {}", e))?;

        if resp.status().is_success() {
            let recordings = resp.json::<Vec<CloudRecording>>().await
                .map_err(|e| format!("Invalid JSON: {}", e))?;
            Ok(recordings)
        } else {
            Err(format!("HTTP error: {}", resp.status()))
        }
    }

    pub fn compute_sha256<P: AsRef<Path>>(path: P) -> std::io::Result<String> {
        let mut file = File::open(path)?;
        let mut hasher = Sha256::new();
        let mut buffer = [0u8; 8192];
        loop {
            let count = file.read(&mut buffer)?;
            if count == 0 {
                break;
            }
            hasher.update(&buffer[..count]);
        }
        Ok(format!("{:x}", hasher.finalize()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_client_is_configured() {
        let client_empty = CloudSyncClient::new("", "");
        assert!(!client_empty.is_configured());

        let client_ready = CloudSyncClient::new("https://scrim.team.com:8443", "secret_key");
        assert!(client_ready.is_configured());
        assert_eq!(client_ready.server_url, "https://scrim.team.com:8443");
    }

    #[test]
    fn test_compute_sha256() {
        let temp_file = std::env::temp_dir().join("test_sha256.txt");
        std::fs::write(&temp_file, b"Hello League Scrims!").unwrap();

        let hash = CloudSyncClient::compute_sha256(&temp_file).unwrap();
        assert_eq!(hash.len(), 64);

        let _ = std::fs::remove_file(&temp_file);
    }
}
