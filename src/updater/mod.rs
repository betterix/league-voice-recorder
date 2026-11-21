use serde::{Deserialize, Serialize};

pub const CURRENT_VERSION: &str = "0.6.0";
pub const GITHUB_REPO: &str = "betterix/league-voice-recorder";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UpdateInfo {
    pub available: bool,
    pub current_version: String,
    pub latest_version: String,
    pub download_url: Option<String>,
    pub changelog: Option<String>,
}

/// Parses a version string like 'v0.3.1' or '0.5.1-beta' into an integer tuple (major, minor, patch).
pub fn parse_version(v: &str) -> (u32, u32, u32) {
    let clean = v.trim().trim_start_matches(|c| c == 'v' || c == 'V');
    let mut parts = clean.split('.');
    let major = parts.next().and_then(|s| s.parse().ok()).unwrap_or(0);
    let minor = parts.next().and_then(|s| s.parse().ok()).unwrap_or(0);
    let patch = parts.next().and_then(|s| {
        let num_str: String = s.chars().take_while(|c| c.is_ascii_digit()).collect();
        num_str.parse().ok()
    }).unwrap_or(0);

    (major, minor, patch)
}

pub fn is_newer_version(latest: &str, current: &str) -> bool {
    parse_version(latest) > parse_version(current)
}

pub async fn check_for_update(current: &str) -> Result<UpdateInfo, String> {
    let url = format!("https://api.github.com/repos/{}/releases/latest", GITHUB_REPO);
    let client = reqwest::Client::builder()
        .user_agent("LeagueVoiceRecorder-Updater")
        .timeout(std::time::Duration::from_secs(5))
        .build()
        .map_err(|e| e.to_string())?;

    let resp = client.get(&url).send().await
        .map_err(|e| format!("Network error: {}", e))?;

    if !resp.status().is_success() {
        return Err(format!("GitHub returned HTTP {}", resp.status()));
    }

    let json: serde_json::Value = resp.json().await
        .map_err(|e| format!("Failed to parse release JSON: {}", e))?;

    let tag = json.get("tag_name").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let body = json.get("body").and_then(|v| v.as_str()).map(|s| s.to_string());

    let mut download_url = None;
    if let Some(assets) = json.get("assets").and_then(|v| v.as_array()) {
        for asset in assets {
            if let Some(name) = asset.get("name").and_then(|v| v.as_str()) {
                let name_lower = name.to_lowercase();
                // Prioritize NSIS setup installer (.exe), falling back to .zip or standalone .exe
                if name_lower.contains("setup") && name_lower.ends_with(".exe") {
                    if let Some(url) = asset.get("browser_download_url").and_then(|v| v.as_str()) {
                        download_url = Some(url.to_string());
                        break;
                    }
                } else if name_lower.ends_with(".zip") && download_url.is_none() {
                    if let Some(url) = asset.get("browser_download_url").and_then(|v| v.as_str()) {
                        download_url = Some(url.to_string());
                    }
                } else if name_lower.ends_with(".exe") && download_url.is_none() {
                    if let Some(url) = asset.get("browser_download_url").and_then(|v| v.as_str()) {
                        download_url = Some(url.to_string());
                    }
                }
            }
        }
    }

    let newer = is_newer_version(&tag, current);

    Ok(UpdateInfo {
        available: newer,
        current_version: current.to_string(),
        latest_version: tag,
        download_url,
        changelog: body,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_version() {
        assert_eq!(parse_version("v0.3.1"), (0, 3, 1));
        assert_eq!(parse_version("0.5.1"), (0, 5, 1));
        assert_eq!(parse_version("1.0.0-rc1"), (1, 0, 0));
    }

    #[test]
    fn test_is_newer_version() {
        assert!(is_newer_version("v0.5.2", "0.5.1"));
        assert!(is_newer_version("1.0.0", "v0.9.9"));
        assert!(!is_newer_version("0.5.1", "0.5.1"));
        assert!(!is_newer_version("0.4.9", "0.5.0"));
    }
}
