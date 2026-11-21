use std::path::PathBuf;
use sysinfo::{ProcessRefreshKind, ProcessesToUpdate, System, UpdateKind};

pub struct RoflProcessDetector;

impl RoflProcessDetector {
    /// Scans running processes and extracts any active `.rofl` file path and Game ID.
    pub fn detect_running_rofl() -> Option<(String, PathBuf)> {
        // Fast guard: If League game process is not running, no .rofl can be running
        if !crate::lcu::LcuCredentials::is_game_process_running() {
            return None;
        }

        static CACHE: std::sync::Mutex<Option<(std::time::Instant, Option<(String, PathBuf)>)>> = std::sync::Mutex::new(None);
        if let Ok(mut lk) = CACHE.lock() {
            if let Some((inst, ref res)) = *lk {
                if inst.elapsed() < std::time::Duration::from_millis(800) {
                    return res.clone();
                }
            }
            let mut sys = System::new();
            sys.refresh_processes_specifics(
                ProcessesToUpdate::All,
                true,
                ProcessRefreshKind::nothing().with_cmd(UpdateKind::Always),
            );

            let mut found = None;
            for (_pid, proc) in sys.processes() {
                let name = proc.name().to_string_lossy().to_lowercase();
                if name.contains("league of legends") {
                    for arg in proc.cmd() {
                        let arg_str = arg.to_string_lossy();
                        let clean_str = arg_str.trim().trim_matches(|c| c == '"' || c == '\'');
                        if clean_str.to_lowercase().contains(".rofl") {
                            // Extract file path portion (handling potential -GameLaunch= or quotes)
                            let file_str = if let Some(idx) = clean_str.find('=') {
                                clean_str[idx + 1..].trim().trim_matches(|c| c == '"' || c == '\'')
                            } else {
                                clean_str
                            };
                            let path = PathBuf::from(file_str);
                            if let Some(game_id) = Self::parse_game_id(&path) {
                                found = Some((game_id, path));
                                break;
                            }
                        }
                    }
                    if found.is_some() {
                        break;
                    }
                }
            }
            *lk = Some((std::time::Instant::now(), found.clone()));
            found
        } else {
            None
        }
    }

    /// Extracts numeric Game ID from a `.rofl` filename (e.g. `EUW1-8018190134.rofl` -> `8018190134`).
    pub fn parse_game_id(path: &PathBuf) -> Option<String> {
        let filename = path.file_name()?.to_string_lossy();
        let stem = if filename.to_lowercase().ends_with(".rofl") {
            &filename[..filename.len() - 5]
        } else {
            path.file_stem()?.to_str()?
        };

        if let Some(idx) = stem.rfind('-') {
            let id = &stem[idx + 1..];
            if !id.is_empty() && id.chars().all(|c| c.is_ascii_digit()) {
                return Some(id.to_string());
            }
        }
        if let Some(idx) = stem.rfind('_') {
            let id = &stem[idx + 1..];
            if !id.is_empty() && id.chars().all(|c| c.is_ascii_digit()) {
                return Some(id.to_string());
            }
        }
        if !stem.is_empty() && stem.chars().all(|c| c.is_ascii_digit()) {
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

    #[test]
    fn test_parse_game_id_with_equals_sign() {
        let path = PathBuf::from(r"-GameLaunch=C:\replays\EUW1-9988776655.rofl");
        // Simulated extraction of path after =
        let file_str = path.to_string_lossy();
        let clean = file_str.split_once('=').map(|(_, p)| p).unwrap_or(&file_str);
        let parsed = PathBuf::from(clean);
        assert_eq!(RoflProcessDetector::parse_game_id(&parsed), Some("9988776655".to_string()));
    }

    #[test]
    fn test_sysinfo_cmd_retrieval() {
        use sysinfo::{ProcessRefreshKind, ProcessesToUpdate, UpdateKind};
        let mut sys = System::new();
        sys.refresh_processes_specifics(
            ProcessesToUpdate::All,
            true,
            ProcessRefreshKind::nothing().with_cmd(UpdateKind::Always),
        );
        let mut has_any_cmd = false;
        let mut sample_name = String::new();
        let mut sample_cmd = String::new();
        for (_pid, p) in sys.processes() {
            if !p.cmd().is_empty() {
                has_any_cmd = true;
                sample_name = p.name().to_string_lossy().to_string();
                sample_cmd = format!("{:?}", p.cmd());
                break;
            }
        }
        println!("test_sysinfo_cmd_retrieval with_cmd: has_any_cmd = {}, name = {}, sample = {}", has_any_cmd, sample_name, sample_cmd);
        assert!(!sample_name.is_empty(), "Process name must not be empty");
        assert!(has_any_cmd, "Must retrieve process cmdline when with_cmd is set");
    }
}
