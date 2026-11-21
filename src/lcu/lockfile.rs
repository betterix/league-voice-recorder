use std::path::{Path, PathBuf};
use sysinfo::System;

#[derive(Debug, Clone)]
pub struct LcuCredentials {
    pub process_name: String,
    pub pid: u32,
    pub port: u16,
    pub password: String,
    pub protocol: String,
}

impl LcuCredentials {
    pub fn parse_from_file<P: AsRef<Path>>(path: P) -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        let content = std::fs::read_to_string(path)?;
        let trimmed = content.trim(); let parts: Vec<&str> = trimmed.split(':').collect();
        if parts.len() < 5 {
            return Err("Invalid lockfile format".into());
        }

        Ok(Self {
            process_name: parts[0].to_string(),
            pid: parts[1].parse()?,
            port: parts[2].parse()?,
            password: parts[3].to_string(),
            protocol: parts[4].to_string(),
        })
    }

    /// Finds the League of Legends install directory across active processes, Riot metadata, and standard paths.
    pub fn find_install_dir() -> Option<PathBuf> {
        static CACHE: std::sync::Mutex<Option<(std::time::Instant, Option<PathBuf>)>> = std::sync::Mutex::new(None);
        if let Ok(lk) = CACHE.lock() {
            if let Some((inst, ref dir)) = *lk {
                let ttl = if dir.is_some() {
                    std::time::Duration::from_secs(60)
                } else {
                    std::time::Duration::from_secs(4)
                };
                if inst.elapsed() < ttl {
                    return dir.clone();
                }
            }
        }

        let is_valid = |d: &Path| -> bool {
            d.join("lockfile").exists()
                || d.join("LeagueClient.exe").exists()
                || d.join("Config/game.cfg").exists()
                || d.join("Game/League of Legends.exe").exists()
        };

        let mut found = None;

        // 1. Check active processes using lightweight inspection
        let mut sys = System::new();
        sys.refresh_processes_specifics(
            sysinfo::ProcessesToUpdate::All,
            true,
            sysinfo::ProcessRefreshKind::nothing().with_exe(sysinfo::UpdateKind::OnlyIfNotSet),
        );

        for (_pid, proc) in sys.processes() {
            let name = proc.name().to_string_lossy().to_lowercase();
            if name.contains("leagueclient") || name.contains("league of legends") {
                if let Some(exe) = proc.exe() {
                    if let Some(dir) = exe.parent() {
                        if is_valid(dir) {
                            found = Some(dir.to_path_buf());
                            break;
                        }
                        if let Some(parent) = dir.parent() {
                            if is_valid(parent) {
                                found = Some(parent.to_path_buf());
                                break;
                            }
                        }
                    }
                }
            }
        }

        if found.is_none() {
            // 2. Check Riot Games metadata configuration
            let metadata_yaml = PathBuf::from(r"C:\ProgramData\Riot Games\Metadata\league_of_legends.live\league_of_legends.live.product_settings.yaml");
            if metadata_yaml.exists() {
                if let Ok(content) = std::fs::read_to_string(&metadata_yaml) {
                    for line in content.lines() {
                        if line.contains("product_install_full_path") {
                            if let Some((_, path_part)) = line.split_once(':') {
                                let candidate = PathBuf::from(path_part.trim().trim_matches('"').trim_matches('\''));
                                if is_valid(&candidate) {
                                    found = Some(candidate);
                                    break;
                                }
                            }
                        }
                    }
                }
            }
        }

        if found.is_none() {
            // 3. Fallback drive search
            let drives = ["C", "D", "E", "F", "G"];
            let subpaths = [
                r"Riot Games\League of Legends",
                r"Program Files\Riot Games\League of Legends",
                r"Program Files (x86)\Riot Games\League of Legends",
                r"Games\League of Legends",
            ];
            for d in drives {
                for sub in subpaths {
                    let candidate = PathBuf::from(format!(r"{}:\{}", d, sub));
                    if is_valid(&candidate) {
                        found = Some(candidate);
                        break;
                    }
                }
                if found.is_some() {
                    break;
                }
            }
        }

        if let Ok(mut lk) = CACHE.lock() {
            *lk = Some((std::time::Instant::now(), found.clone()));
        }

        found
    }

    /// Finds active LCU credentials from the running League client lockfile.
    pub fn find_active() -> Option<Self> {
        let dir = Self::find_install_dir()?;
        Self::parse_from_file(dir.join("lockfile")).ok()
    }

    /// Queries the League Client Update (LCU) API for the current gameflow phase:
    /// e.g. "Lobby", "Matchmaking", "ReadyCheck", "ChampSelect", "InProgress", "None"
    pub async fn get_gameflow_phase(&self) -> Result<String, Box<dyn std::error::Error + Send + Sync>> {
        use base64::engine::general_purpose::STANDARD as BASE64;
        use base64::Engine;

        let auth = BASE64.encode(format!("riot:{}", self.password));
        let client = reqwest::Client::builder()
            .danger_accept_invalid_certs(true)
            .build()?;

        let url = format!("https://127.0.0.1:{}/lol-gameflow/v1/gameflow-phase", self.port);
        let resp = client
            .get(&url)
            .header("Authorization", format!("Basic {}", auth))
            .send()
            .await?;

        let phase = resp.text().await?;
        let clean = phase.trim().trim_matches('"').to_string();
        Ok(clean)
    }

    /// Queries the LCU session API (/lol-gameflow/v1/session) to extract the official Game ID.
    pub async fn get_game_id(&self) -> Result<String, Box<dyn std::error::Error + Send + Sync>> {
        use base64::engine::general_purpose::STANDARD as BASE64;
        use base64::Engine;

        let auth = BASE64.encode(format!("riot:{}", self.password));
        let client = reqwest::Client::builder()
            .danger_accept_invalid_certs(true)
            .build()?;

        let url = format!("https://127.0.0.1:{}/lol-gameflow/v1/session", self.port);
        let resp = client
            .get(&url)
            .header("Authorization", format!("Basic {}", auth))
            .send()
            .await?;

        #[derive(serde::Deserialize)]
        struct SessionGameIdDto {
            #[serde(rename = "gameData")]
            game_data: Option<GameDataIdDto>,
        }
        #[derive(serde::Deserialize)]
        struct GameDataIdDto {
            #[serde(rename = "gameId")]
            game_id: Option<serde_json::Value>,
        }

        if let Ok(session) = resp.json::<SessionGameIdDto>().await {
            if let Some(gd) = session.game_data {
                if let Some(game_id) = gd.game_id {
                    if let Some(num) = game_id.as_u64() {
                        if num > 0 {
                            return Ok(num.to_string());
                        }
                    } else if let Some(s) = game_id.as_str() {
                        if !s.is_empty() && s != "0" {
                            return Ok(s.to_string());
                        }
                    }
                }
            }
        }

        Err("No valid gameData.gameId in session".into())
    }

    /// Extracts major.minor patch string (e.g. '14.23' or '16.23') from raw version string.
    pub fn format_patch_version(raw: &str) -> String {
        let clean = raw.trim().trim_matches('"').trim_matches('\'');
        let stripped = clean.strip_prefix("lolpatch_").unwrap_or(clean);
        let parts: Vec<&str> = stripped.split('.').collect();
        if parts.len() >= 2 && parts[0].chars().all(|c| c.is_ascii_digit()) && parts[1].chars().all(|c| c.is_ascii_digit()) {
            format!("{}.{}", parts[0], parts[1])
        } else {
            stripped.to_string()
        }
    }

    /// Queries LCU /lol-patch/v1/game-version or LCU session to extract the active game patch.
    pub async fn get_game_patch(&self) -> Option<String> {
        use base64::engine::general_purpose::STANDARD as BASE64;
        use base64::Engine;

        let auth = BASE64.encode(format!("riot:{}", self.password));
        let client = reqwest::Client::builder()
            .danger_accept_invalid_certs(true)
            .timeout(std::time::Duration::from_millis(1500))
            .build()
            .ok()?;

        // 1. Try /lol-patch/v1/game-version
        let url_patch = format!("https://127.0.0.1:{}/lol-patch/v1/game-version", self.port);
        if let Ok(resp) = client.get(&url_patch).header("Authorization", format!("Basic {}", auth)).send().await {
            if resp.status().is_success() {
                if let Ok(val) = resp.text().await {
                    let formatted = Self::format_patch_version(&val);
                    if !formatted.is_empty() {
                        return Some(formatted);
                    }
                }
            }
        }

        // 2. Try /lol-gameflow/v1/session -> gameData.gameVersion
        let url_session = format!("https://127.0.0.1:{}/lol-gameflow/v1/session", self.port);
        if let Ok(resp) = client.get(&url_session).header("Authorization", format!("Basic {}", auth)).send().await {
            if resp.status().is_success() {
                #[derive(serde::Deserialize)]
                struct SessionPatchDto {
                    #[serde(rename = "gameData")]
                    game_data: Option<GameDataPatchDto>,
                }
                #[derive(serde::Deserialize)]
                struct GameDataPatchDto {
                    #[serde(rename = "gameVersion")]
                    game_version: Option<String>,
                }

                if let Ok(dto) = resp.json::<SessionPatchDto>().await {
                    if let Some(gd) = dto.game_data {
                        if let Some(ver) = gd.game_version {
                            let formatted = Self::format_patch_version(&ver);
                            if !formatted.is_empty() {
                                return Some(formatted);
                            }
                        }
                    }
                }
            }
        }

        None
    }

    /// Offline fallback: inspects file version of League of Legends.exe or LeagueClient.exe on disk.
    pub fn get_offline_binary_patch() -> Option<String> {
        let dir = Self::find_install_dir()?;
        let game_exe = dir.join("Game").join("League of Legends.exe");
        let client_exe = dir.join("LeagueClient.exe");
        let target = if game_exe.exists() {
            game_exe
        } else if client_exe.exists() {
            client_exe
        } else {
            return None;
        };

        let out = std::process::Command::new("powershell")
            .args(["-NoProfile", "-Command", &format!("(Get-Item '{}').VersionInfo.FileVersion", target.display())])
            .output()
            .ok()?;
        if out.status.success() {
            let s = String::from_utf8_lossy(&out.stdout).trim().to_string();
            let formatted = Self::format_patch_version(&s);
            if !formatted.is_empty() {
                return Some(formatted);
            }
        }
        None
    }

    /// Fetches the current League patch version: checks LCU first, falls back to Riot Data Dragon, then offline install binary.
    pub async fn fetch_current_patch() -> String {
        if let Some(creds) = Self::find_active() {
            if let Some(p) = creds.get_game_patch().await {
                if !p.is_empty() {
                    return p;
                }
            }
        }

        // Fallback 1: Data Dragon API
        let client = reqwest::Client::builder()
            .timeout(std::time::Duration::from_millis(1500))
            .build();
        if let Ok(cli) = client {
            if let Ok(resp) = cli.get("https://ddragon.leagueoflegends.com/api/versions.json").send().await {
                if let Ok(items) = resp.json::<Vec<String>>().await {
                    if let Some(first) = items.first() {
                        let formatted = Self::format_patch_version(first);
                        if !formatted.is_empty() {
                            return formatted;
                        }
                    }
                }
            }
        }

        // Fallback 2: Offline League binary version check
        if let Some(offline_ver) = Self::get_offline_binary_patch() {
            if !offline_ver.is_empty() {
                return offline_ver;
            }
        }

        String::new()
    }

    fn refresh_cached_processes() -> (bool, bool) {
        static CACHE: std::sync::Mutex<Option<(std::time::Instant, bool, bool)>> = std::sync::Mutex::new(None);
        if let Ok(mut lk) = CACHE.lock() {
            if let Some((inst, c, g)) = *lk {
                if inst.elapsed() < std::time::Duration::from_millis(800) {
                    return (c, g);
                }
            }
            let mut sys = System::new();
            sys.refresh_processes_specifics(
                sysinfo::ProcessesToUpdate::All,
                true,
                sysinfo::ProcessRefreshKind::nothing(),
            );
            let mut client = false;
            let mut game = false;
            for (_pid, proc) in sys.processes() {
                let name = proc.name().to_string_lossy().to_lowercase();
                if name.contains("leagueclient") {
                    client = true;
                }
                if name == "league of legends.exe" || name.contains("league of legends") {
                    game = true;
                }
                if client && game {
                    break;
                }
            }
            *lk = Some((std::time::Instant::now(), client, game));
            (client, game)
        } else {
            (false, false)
        }
    }

    /// Checks if any LeagueClient process is active.
    pub fn is_client_running() -> bool {
        Self::refresh_cached_processes().0
    }

    /// Checks if the 3D League of Legends game engine is actively running.
    pub fn is_game_process_running() -> bool {
        Self::refresh_cached_processes().1
    }
}


#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_valid_lockfile() {
        let temp_lockfile = std::env::temp_dir().join("test_lockfile");
        std::fs::write(&temp_lockfile, "LeagueClient:12345:54321:secret_pass:https\n").unwrap();

        let creds = LcuCredentials::parse_from_file(&temp_lockfile).unwrap();
        assert_eq!(creds.process_name, "LeagueClient");
        assert_eq!(creds.pid, 12345);
        assert_eq!(creds.port, 54321);
        assert_eq!(creds.password, "secret_pass");
        assert_eq!(creds.protocol, "https");

        let _ = std::fs::remove_file(&temp_lockfile);
    }

    #[test]
    fn test_parse_invalid_lockfile() {
        let temp_lockfile = std::env::temp_dir().join("test_invalid_lockfile");
        std::fs::write(&temp_lockfile, "invalid_data\n").unwrap();

        let err = LcuCredentials::parse_from_file(&temp_lockfile);
        assert!(err.is_err());

        let _ = std::fs::remove_file(&temp_lockfile);
    }
}
