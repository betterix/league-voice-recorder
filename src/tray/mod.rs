use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

#[derive(Debug, Clone)]
pub struct TrayState {
    pub is_minimized: Arc<AtomicBool>,
    pub status_tooltip: Arc<std::sync::Mutex<String>>,
}

impl Default for TrayState {
    fn default() -> Self {
        Self {
            is_minimized: Arc::new(AtomicBool::new(false)),
            status_tooltip: Arc::new(std::sync::Mutex::new("League Voice Recorder: Idle".to_string())),
        }
    }
}

impl TrayState {
    pub fn set_minimized(&self, val: bool) {
        self.is_minimized.store(val, Ordering::SeqCst);
    }

    pub fn is_minimized(&self) -> bool {
        self.is_minimized.load(Ordering::SeqCst)
    }

    pub fn set_status(&self, status: &str) {
        if let Ok(mut lock) = self.status_tooltip.lock() {
            *lock = format!("League Voice Recorder: {}", status);
        }
    }

    pub fn get_status(&self) -> String {
        self.status_tooltip.lock().map(|g| g.clone()).unwrap_or_default()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_tray_state_status() {
        let tray = TrayState::default();
        assert!(!tray.is_minimized());
        assert_eq!(tray.get_status(), "League Voice Recorder: Idle");

        tray.set_status("🔴 Recording Scrim");
        assert_eq!(tray.get_status(), "League Voice Recorder: 🔴 Recording Scrim");

        tray.set_minimized(true);
        assert!(tray.is_minimized());
    }
}
