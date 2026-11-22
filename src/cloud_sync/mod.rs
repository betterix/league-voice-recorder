use std::path::Path;
use reqwest::header::{HeaderMap, HeaderValue};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::fs::File;
use std::io::Read;

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct CloudRecordingDto {
    pub game_id: String,
    #[serde(default)]
    pub filename: Option<String>,
    #[serde(default)]
    pub file_size: Option<u64>,
    #[serde(default)]
    pub recorded_at: Option<String>,
    #[serde(default)]
    pub created_at: Option<String>,
    #[serde(default)]
    pub upload_timestamp: Option<String>,
    #[serde(default)]
    pub team_tag: Option<String>,
    #[serde(default)]
    pub notes: Option<String>,
    #[serde(default)]
    pub patch: Option<String>,
    #[serde(default)]
    pub game_patch: Option<String>,
    #[serde(default)]
    pub sha256: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CloudHealthStatus {
    pub reachable: bool,
    pub role: String, // "admin", "upload", "pull", or "unknown"
    pub message: String,
    #[serde(default)]
    pub raw: Option<Value>,
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
        headers.insert("User-Agent", HeaderValue::from_static("LeagueVoiceRecorder-Client/1.0"));

        reqwest::Client::builder()
            .default_headers(headers)
            .timeout(std::time::Duration::from_secs(self.timeout_secs))
            .danger_accept_invalid_certs(true) // Support self-signed certificates
            .build()
            .unwrap_or_default()
    }

    pub fn determine_role(info: Option<&Value>, api_key: &str) -> String {
        if let Some(data) = info {
            if let Some(r) = data.get("authenticated_role").or_else(|| data.get("role")).and_then(|v| v.as_str()) {
                let lower = r.to_lowercase();
                if lower == "admin" || lower == "upload" || lower == "pull" {
                    return lower;
                }
            }
        }
        // Fallback inspection of API key string
        let key_lower = api_key.to_lowercase();
        if key_lower.contains("admin") {
            "admin".to_string()
        } else if key_lower.contains("upload") || key_lower.contains("push") {
            "upload".to_string()
        } else if key_lower.contains("pull") {
            "pull".to_string()
        } else {
            "unknown".to_string()
        }
    }

    pub async fn check_health(&self) -> Result<CloudHealthStatus, String> {
        if !self.is_configured() {
            return Err("Server URL and API Key must both be configured in Settings.".to_string());
        }

        let url = format!("{}/api/v1/health", self.server_url);
        let client = self.build_client();

        let resp = client.get(&url).send().await
            .map_err(|e| format!("Could not connect to server: {}. Check IP, port, and firewall.", e))?;

        let status = resp.status();
        if status.is_success() {
            let json = resp.json::<Value>().await.ok();
            let role = Self::determine_role(json.as_ref(), &self.api_key);
            Ok(CloudHealthStatus {
                reachable: true,
                role,
                message: "200 OK".to_string(),
                raw: json,
            })
        } else if status.as_u16() == 401 {
            Err("Invalid API key (401 Unauthorized)".to_string())
        } else if status.as_u16() == 403 {
            Err("Forbidden: Key does not have read permissions (403)".to_string())
        } else if status.as_u16() == 429 {
            Err("Rate limit exceeded. Please wait a moment (429)".to_string())
        } else {
            Err(format!("Server returned HTTP {}", status))
        }
    }

    pub async fn check_recording_exists(&self, game_id: &str) -> bool {
        if !self.is_configured() {
            return false;
        }

        let clean_id = game_id.trim();
        let url = format!("{}/api/v1/recordings/{}", self.server_url, clean_id);
        let client = self.build_client();

        if let Ok(resp) = client.get(&url).send().await {
            resp.status().is_success()
        } else {
            false
        }
    }

    pub async fn list_recordings(&self) -> Result<Vec<CloudRecordingDto>, String> {
        if !self.is_configured() {
            return Err("Server URL and API Key not configured in Settings.".to_string());
        }

        let url = format!("{}/api/v1/recordings?skip=0&limit=100", self.server_url);
        let client = self.build_client();

        let resp = client.get(&url)
            .send().await
            .map_err(|e| format!("Connection error: {}", e))?;

        if resp.status().is_success() {
            let val = resp.json::<Value>().await
                .map_err(|e| format!("Invalid JSON response: {}", e))?;

            let items_val = if let Some(items) = val.get("items") {
                items.clone()
            } else if val.is_array() {
                val
            } else {
                Value::Array(Vec::new())
            };

            let mut recordings: Vec<CloudRecordingDto> = serde_json::from_value(items_val)
                .map_err(|e| format!("Failed to parse remote recordings: {}", e))?;

            for rec in &mut recordings {
                if rec.filename.is_none() {
                    rec.filename = Some(format!("{}.mp3", rec.game_id));
                }
            }
            Ok(recordings)
        } else {
            Err(format!("HTTP error {}: Failed to list recordings", resp.status()))
        }
    }

    pub async fn download_recording<P: AsRef<Path>>(&self, game_id: &str, dest_path: P) -> Result<(), String> {
        if !self.is_configured() {
            return Err("Cloud sync is not configured".to_string());
        }

        let clean_id = game_id.trim();
        let url = format!("{}/api/v1/recordings/{}/download", self.server_url, clean_id);
        let client = self.build_client();

        let resp = client.get(&url).send().await
            .map_err(|e| format!("Download request error: {}", e))?;

        if !resp.status().is_success() {
            return Err(format!("Download failed with HTTP {}", resp.status()));
        }

        let dest = dest_path.as_ref();
        if let Some(parent) = dest.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let temp_file = dest.with_extension("tmp_download");

        let bytes = resp.bytes().await
            .map_err(|e| format!("Failed to read stream bytes: {}", e))?;

        std::fs::write(&temp_file, &bytes)
            .map_err(|e| format!("Failed to write temp file: {}", e))?;

        if dest.exists() {
            let _ = std::fs::remove_file(dest);
        }
        std::fs::rename(&temp_file, dest)
            .map_err(|e| format!("Failed to save recording file: {}", e))?;

        Ok(())
    }

    pub async fn upload_recording<P: AsRef<Path>>(
        &self,
        game_id: &str,
        file_path: P,
        team_tag: Option<&str>,
        notes: Option<&str>,
        game_patch: Option<&str>,
    ) -> Result<Value, String> {
        if !self.is_configured() {
            return Err("Cloud sync is not configured with server URL and API key".to_string());
        }

        let clean_id = game_id.trim();
        let p = file_path.as_ref();
        if !p.exists() {
            return Err(format!("File does not exist: {}", p.display()));
        }

        // Check if recording already exists on the server first
        if self.check_recording_exists(clean_id).await {
            return Ok(serde_json::json!({
                "message": "Already exists on server",
                "already_exists": true,
                "game_id": clean_id
            }));
        }

        let sha256 = Self::compute_sha256(p)
            .map_err(|e| format!("Failed to compute SHA-256 checksum: {}", e))?;

        let file_bytes = std::fs::read(p)
            .map_err(|e| format!("Failed to read audio file: {}", e))?;

        let url = format!("{}/api/v1/recordings/{}", self.server_url, clean_id);

        let mut form = reqwest::multipart::Form::new()
            .part("file", reqwest::multipart::Part::bytes(file_bytes)
                .file_name(format!("{}.mp3", clean_id))
                .mime_str("audio/mpeg").unwrap_or_else(|_| reqwest::multipart::Part::bytes(vec![])));

        if let Some(tag) = team_tag {
            if !tag.is_empty() {
                form = form.text("team_tag", tag.to_string());
            }
        }
        if let Some(n) = notes {
            if !n.is_empty() {
                form = form.text("notes", n.to_string());
            }
        }
        if let Some(patch) = game_patch {
            if !patch.is_empty() {
                form = form.text("patch", patch.to_string())
                           .text("game_patch", patch.to_string());
            }
        }

        let client = self.build_client();
        let resp = client.post(&url)
            .header("X-Checksum-SHA256", sha256)
            .multipart(form)
            .send()
            .await
            .map_err(|e| format!("Upload connection error: {}", e))?;

        let status = resp.status();
        if status.is_success() || status.as_u16() == 201 {
            let json = resp.json::<Value>().await.unwrap_or(serde_json::json!({"success": true}));
            Ok(json)
        } else if status.as_u16() == 409 {
            Ok(serde_json::json!({"message": "Already uploaded (HTTP 409 Conflict)"}))
        } else if status.as_u16() == 403 {
            Err("Upload forbidden: Key does not have upload permissions (requires UPLOAD_KEY or ADMIN_KEY)".to_string())
        } else if status.as_u16() == 401 {
            Err("Unauthorized: Invalid API key".to_string())
        } else {
            Err(format!("Upload failed with HTTP {}", status))
        }
    }

    pub async fn delete_recording(&self, game_id: &str) -> Result<String, String> {
        if !self.is_configured() {
            return Err("Cloud sync is not configured".to_string());
        }

        let clean_id = game_id.trim();
        let url = format!("{}/api/v1/recordings/{}", self.server_url, clean_id);
        let client = self.build_client();

        let resp = client.delete(&url).send().await
            .map_err(|e| format!("Delete error: {}", e))?;

        let status = resp.status();
        if status.is_success() {
            Ok(format!("Recording {}.mp3 deleted successfully from remote server", clean_id))
        } else if status.as_u16() == 404 {
            Err(format!("Recording {}.mp3 not found on server", clean_id))
        } else if status.as_u16() == 403 {
            Err("Forbidden: Admin key required to delete remote recordings".to_string())
        } else {
            Err(format!("Delete failed with HTTP {}", status))
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
        let hash = hasher.finalize();
        let mut hex = String::with_capacity(hash.len() * 2);
        for b in hash {
            use std::fmt::Write;
            let _ = write!(hex, "{:02x}", b);
        }
        Ok(hex)
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
    fn test_role_determination() {
        assert_eq!(CloudSyncClient::determine_role(None, "pull_secret_key_123"), "pull");
        assert_eq!(CloudSyncClient::determine_role(None, "upload_secret_key_123"), "upload");
        assert_eq!(CloudSyncClient::determine_role(None, "admin_secret_key_123"), "admin");

        let json_admin = serde_json::json!({"authenticated_role": "admin"});
        assert_eq!(CloudSyncClient::determine_role(Some(&json_admin), "custom_key"), "admin");

        let json_pull = serde_json::json!({"role": "pull"});
        assert_eq!(CloudSyncClient::determine_role(Some(&json_pull), "custom_key"), "pull");
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
