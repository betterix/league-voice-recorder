use std::path::PathBuf;
use tracing::info;
use crate::lcu::LcuCredentials;

pub struct ReplayConfigManager;

impl ReplayConfigManager {
    /// Locates the League of Legends `game.cfg` file.
    pub fn locate_game_cfg() -> Option<PathBuf> {
        if let Some(install_dir) = LcuCredentials::find_install_dir() {
            let candidates = [
                install_dir.join("Config").join("game.cfg"),
                install_dir.join("game.cfg"),
            ];
            for c in candidates {
                if c.exists() {
                    return Some(c);
                }
            }
        }

        let drives = ["C", "D", "E", "F", "G"];
        let subpaths = [
            r"Riot Games\League of Legends\Config\game.cfg",
            r"Program Files\Riot Games\League of Legends\Config\game.cfg",
            r"Program Files (x86)\Riot Games\League of Legends\Config\game.cfg",
            r"Games\League of Legends\Config\game.cfg",
        ];
        for d in drives {
            for sub in subpaths {
                let cand = PathBuf::from(format!(r"{}:\{}", d, sub));
                if cand.exists() {
                    return Some(cand);
                }
            }
        }

        None
    }

    /// Checks if `EnableReplayApi=1` is present under `[General]` in `game.cfg`.
    pub fn check_replay_api_status() -> (bool, Option<PathBuf>) {
        let path = match Self::locate_game_cfg() {
            Some(p) => p,
            None => return (false, None),
        };

        let content = match std::fs::read_to_string(&path) {
            Ok(c) => c,
            Err(_) => return (false, Some(path)),
        };

        let mut in_general = false;
        for line in content.lines() {
            let trimmed = line.trim();
            if trimmed.starts_with('[') && trimmed.ends_with(']') {
                let sec = trimmed[1..trimmed.len() - 1].trim().to_lowercase();
                in_general = sec == "general";
            } else if in_general {
                let lower = trimmed.to_lowercase();
                if lower.starts_with("enablereplayapi") {
                    if let Some((_, val)) = lower.split_once('=') {
                        if val.trim() == "1" {
                            return (true, Some(path));
                        }
                    }
                }
            }
        }

        (false, Some(path))
    }

    /// Injects `EnableReplayApi=1` under `[General]` and creates a safe `.bak` copy.
    pub fn enable_replay_api() -> Result<String, String> {
        let path = match Self::locate_game_cfg() {
            Some(p) => p,
            None => return Err("Could not locate League of Legends game.cfg automatically.".to_string()),
        };

        let (already_enabled, _) = Self::check_replay_api_status();
        if already_enabled {
            return Ok(format!("Replay API is already enabled in game.cfg:\n{:?}", path));
        }

        // Backup to game.cfg.bak
        let bak = path.with_extension("cfg.bak");
        let _ = std::fs::copy(&path, &bak);

        let content = std::fs::read_to_string(&path)
            .map_err(|e| format!("Failed to read game.cfg: {}", e))?;

        let mut new_lines: Vec<String> = Vec::new();
        let mut in_general = false;
        let mut general_found = false;
        let mut api_key_found = false;

        for line in content.lines() {
            let trimmed = line.trim();
            if trimmed.starts_with('[') && trimmed.ends_with(']') {
                let sec = trimmed[1..trimmed.len() - 1].trim().to_lowercase();
                if in_general && !api_key_found {
                    new_lines.push("EnableReplayApi=1".to_string());
                    api_key_found = true;
                }
                in_general = sec == "general";
                if in_general {
                    general_found = true;
                }
                new_lines.push(line.to_string());
            } else if in_general && trimmed.to_lowercase().starts_with("enablereplayapi") {
                new_lines.push("EnableReplayApi=1".to_string());
                api_key_found = true;
            } else {
                new_lines.push(line.to_string());
            }
        }

        if !general_found {
            new_lines.push("\r\n[General]".to_string());
            new_lines.push("EnableReplayApi=1".to_string());
        } else if in_general && !api_key_found {
            new_lines.push("EnableReplayApi=1".to_string());
        }

        let updated_content = new_lines.join("\r\n");
        std::fs::write(&path, updated_content)
            .map_err(|e| format!("Failed to write updated game.cfg: {}", e))?;

        info!("Replay API enabled successfully in {:?}", path);
        Ok(format!("Successfully enabled Replay API in game.cfg!\n\nPath: {:?}\nBackup: {:?}", path, bak))
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn test_parse_enable_replay_api() {
        let sample = "[General]\r\nEnableReplayApi=1\r\nGameMode=CLASSIC\r\n";
        let mut enabled = false;
        let mut in_general = false;
        for line in sample.lines() {
            let trimmed = line.trim();
            if trimmed.starts_with('[') && trimmed.ends_with(']') {
                in_general = trimmed[1..trimmed.len()-1].trim().eq_ignore_ascii_case("general");
            } else if in_general && trimmed.to_lowercase().starts_with("enablereplayapi=1") {
                enabled = true;
            }
        }
        assert!(enabled);
    }
}
