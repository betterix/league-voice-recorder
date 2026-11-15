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
    pub fn parse_from_file<P: AsRef<Path>>(path: P) -> Result<Self, Box<dyn std::error::Error>> {
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

    pub fn find_install_dir() -> Option<PathBuf> {
        let mut sys = System::new_all();
        sys.refresh_processes(sysinfo::ProcessesToUpdate::All, true);

        for (_pid, proc) in sys.processes() {
            let name = proc.name().to_string_lossy().to_lowercase();
            if name.contains("leagueclientux") || name.contains("league of legends") {
                if let Some(exe) = proc.exe() {
                    if let Some(dir) = exe.parent() {
                        let lockfile = dir.join("lockfile");
                        if lockfile.exists() {
                            return Some(dir.to_path_buf());
                        }
                    }
                }
            }
        }
        None
    }

    pub fn find_active() -> Option<Self> {
        let dir = Self::find_install_dir()?;
        Self::parse_from_file(dir.join("lockfile")).ok()
    }
}
