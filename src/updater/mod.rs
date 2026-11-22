use serde::{Deserialize, Serialize};

pub const CURRENT_VERSION: &str = env!("CARGO_PKG_VERSION");
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
        // 1. Prefer standalone executable (LeagueVoiceRecorder.exe), not setup installer
        for asset in assets {
            if let Some(name) = asset.get("name").and_then(|v| v.as_str()) {
                let name_lower = name.to_lowercase();
                if name_lower.ends_with(".exe") && !name_lower.contains("setup") {
                    if let Some(url) = asset.get("browser_download_url").and_then(|v| v.as_str()) {
                        download_url = Some(url.to_string());
                        break;
                    }
                }
            }
        }
        // 2. Fallback to any .exe if standalone name pattern differs
        if download_url.is_none() {
            for asset in assets {
                if let Some(name) = asset.get("name").and_then(|v| v.as_str()) {
                    if name.to_lowercase().ends_with(".exe") {
                        if let Some(url) = asset.get("browser_download_url").and_then(|v| v.as_str()) {
                            download_url = Some(url.to_string());
                            break;
                        }
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

/// Downloads the standalone update executable into %TEMP%, spawns a background updater script,
/// copies it over the running binary once exited, relaunches the updated app,
/// and deletes the temporary update file.
pub async fn download_and_apply_update(download_url: &str) -> Result<(), String> {
    if download_url.is_empty() {
        return Err("Download URL is empty".to_string());
    }

    let client = reqwest::Client::builder()
        .user_agent("LeagueVoiceRecorder-Updater")
        .timeout(std::time::Duration::from_secs(120))
        .build()
        .map_err(|e| format!("Client build error: {}", e))?;

    let resp = client.get(download_url).send().await
        .map_err(|e| format!("Failed to download update: {}", e))?;

    if !resp.status().is_success() {
        return Err(format!("Download failed with status: {}", resp.status()));
    }

    let bytes = resp.bytes().await
        .map_err(|e| format!("Failed to read update stream: {}", e))?;

    let temp_dir = std::env::temp_dir();
    let timestamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);

    let download_path = temp_dir.join(format!("lvr_update_{}.exe", timestamp));
    std::fs::write(&download_path, &bytes)
        .map_err(|e| format!("Failed to save update file: {}", e))?;

    let current_exe = std::env::current_exe().unwrap_or_else(|_| std::path::PathBuf::from("LeagueVoiceRecorder.exe"));
    let target_dir = current_exe.parent().unwrap_or_else(|| std::path::Path::new("."));
    let pid = std::process::id();

    let bat_path = temp_dir.join(format!("lvr_apply_update_{}.bat", timestamp));
    let bat_content = format!(
        r#"@echo off
setlocal
set RETRIES=0
:WAIT_PID
tasklist /fi "PID eq {pid}" 2>nul | find "{pid}" >nul
if not errorlevel 1 (
    ping 127.0.0.1 -n 2 >nul
    set /a RETRIES+=1
    if %RETRIES% lss 10 goto WAIT_PID
)

set COPY_RETRIES=0
:RETRY_COPY
copy /y "{download_path}" "{target_exe}" >nul 2>&1
if errorlevel 1 (
    ping 127.0.0.1 -n 2 >nul
    set /a COPY_RETRIES+=1
    if %COPY_RETRIES% lss 10 goto RETRY_COPY
)

ping 127.0.0.1 -n 2 >nul

if exist "{target_exe}" (
    start "" /d "{target_dir}" "{target_exe}"
)

del /f /q "{download_path}" >nul 2>&1
(goto) 2>nul & del "%~f0"
"#,
        pid = pid,
        download_path = download_path.display(),
        target_exe = current_exe.display(),
        target_dir = target_dir.display(),
    );

    std::fs::write(&bat_path, &bat_content)
        .map_err(|e| format!("Failed to write batch script: {}", e))?;

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x08000000;

        std::process::Command::new("cmd.exe")
            .args(["/c", &bat_path.to_string_lossy()])
            .creation_flags(CREATE_NO_WINDOW)
            .spawn()
            .map_err(|e| format!("Failed to launch updater script: {}", e))?;
    }

    #[cfg(not(windows))]
    {
        let _ = std::process::Command::new("sh").arg(&bat_path).spawn();
    }

    std::thread::sleep(std::time::Duration::from_millis(300));
    std::process::exit(0);
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
