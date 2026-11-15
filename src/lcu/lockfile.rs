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
        let parts: Vec<&str> = content.split(':').collect();
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
        let is_valid = |d: &Path| -> bool {
            d.join("lockfile").exists()
                || d.join("LeagueClient.exe").exists()
                || d.join("Config/game.cfg").exists()
                || d.join("Game/League of Legends.exe").exists()
        };

        // 1. Check active processes
        let mut sys = System::new_all();
        sys.refresh_processes(sysinfo::ProcessesToUpdate::All, true);

        for (_pid, proc) in sys.processes() {
            let name = proc.name().to_string_lossy().to_lowercase();
            if name.contains("leagueclient") || name.contains("league of legends") {
                if let Some(exe) = proc.exe() {
                    if let Some(dir) = exe.parent() {
                        if is_valid(dir) {
                            return Some(dir.to_path_buf());
                        }
                        if let Some(parent) = dir.parent() {
                            if is_valid(parent) {
                                return Some(parent.to_path_buf());
                            }
                        }
                    }
                }
            }
        }

        // 2. Check Riot Games metadata configuration
        let metadata_yaml = PathBuf::from(r"C:\ProgramData\Riot Games\Metadata\league_of_legends.live\league_of_legends.live.product_settings.yaml");
        if metadata_yaml.exists() {
            if let Ok(content) = std::fs::read_to_string(&metadata_yaml) {
                for line in content.lines() {
                    if line.contains("product_install_full_path") {
                        if let Some((_, path_part)) = line.split_once(':') {
                            let candidate = PathBuf::from(path_part.trim().trim_matches('"').trim_matches('\''));
                            if is_valid(&candidate) {
                                return Some(candidate);
                            }
                        }
                    }
                }
            }
        }

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
                    return Some(candidate);
                }
            }
        }

        None
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

        let json: serde_json::Value = resp.json().await?;
        if let Some(game_id) = json.pointer("/gameData/gameId") {
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

        Err("No valid gameData.gameId in session".into())
    }

    /// Checks if any LeagueClient process is active.
    pub fn is_client_running() -> bool {
        let mut sys = System::new_all();
        sys.refresh_processes(sysinfo::ProcessesToUpdate::All, true);
        for (_pid, proc) in sys.processes() {
            let name = proc.name().to_string_lossy().to_lowercase();
            if name.contains("leagueclient") {
                return true;
            }
        }
        false
    }

    /// Checks if the 3D League of Legends game engine is actively running.
    pub fn is_game_process_running() -> bool {
        let mut sys = System::new_all();
        sys.refresh_processes(sysinfo::ProcessesToUpdate::All, true);
        for (_pid, proc) in sys.processes() {
            let name = proc.name().to_string_lossy().to_lowercase();
            if name == "league of legends.exe" {
                return true;
            }
        }
        false
    }
}
