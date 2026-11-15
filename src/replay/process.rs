use std::path::PathBuf;
use sysinfo::System;

pub struct RoflProcessDetector;

impl RoflProcessDetector {
    /// Scans running processes and extracts any active `.rofl` file path and Game ID.
    pub fn detect_running_rofl() -> Option<(String, PathBuf)> {
        let mut sys = System::new_all();
        sys.refresh_processes(sysinfo::ProcessesToUpdate::All, true);

        for (_pid, proc) in sys.processes() {
            let name = proc.name().to_string_lossy().to_lowercase();
            if name == "league of legends.exe" {
                for arg in proc.cmd() {
                    let arg_str = arg.to_string_lossy();
                    if arg_str.to_lowercase().ends_with(".rofl") {
                        let path = PathBuf::from(arg_str.to_string());
                        if let Some(game_id) = Self::parse_game_id(&path) {
                            return Some((game_id, path));
                        }
                    }
                }
            }
        }
        None
    }

    /// Extracts numeric Game ID from a `.rofl` filename (e.g. `EUW1-8018190134.rofl` -> `8018190134`).
    pub fn parse_game_id(path: &PathBuf) -> Option<String> {
        let stem = path.file_stem()?.to_string_lossy();
        if let Some(idx) = stem.rfind('-') {
            let id = &stem[idx + 1..];
            if id.chars().all(|c| c.is_ascii_digit()) {
                return Some(id.to_string());
            }
        } else if stem.chars().all(|c| c.is_ascii_digit()) {
            return Some(stem.to_string());
        }
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_game_id_with_region_prefix() {
        let path = PathBuf::from(r"C:\replays\EUW1-8018190134.rofl");
        assert_eq!(RoflProcessDetector::parse_game_id(&path), Some("8018190134".to_string()));
    }

    #[test]
    fn test_parse_game_id_numeric_only() {
        let path = PathBuf::from(r"D:\Riot Games\8018190134.rofl");
        assert_eq!(RoflProcessDetector::parse_game_id(&path), Some("8018190134".to_string()));
    }

    #[test]
    fn test_parse_game_id_invalid_returns_none() {
        let path = PathBuf::from(r"C:\replays\corrupt_replay.rofl");
        assert_eq!(RoflProcessDetector::parse_game_id(&path), None);
    }
}
