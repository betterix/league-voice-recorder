use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

pub const WS_EX_TRANSPARENT: isize = 0x00000020;
pub const WS_EX_LAYERED: isize = 0x00080000;

#[derive(Debug, Clone)]
pub struct OverlayState {
    pub enabled: Arc<AtomicBool>,
    pub click_through: Arc<AtomicBool>,
    pub locked: Arc<AtomicBool>,
}

impl Default for OverlayState {
    fn default() -> Self {
        Self {
            enabled: Arc::new(AtomicBool::new(true)),
            click_through: Arc::new(AtomicBool::new(false)),
            locked: Arc::new(AtomicBool::new(false)),
        }
    }
}

impl OverlayState {
    pub fn is_enabled(&self) -> bool {
        self.enabled.load(Ordering::SeqCst)
    }

    pub fn is_click_through(&self) -> bool {
        self.click_through.load(Ordering::SeqCst)
    }

    pub fn is_locked(&self) -> bool {
        self.locked.load(Ordering::SeqCst)
    }

    pub fn set_click_through(&self, val: bool) {
        self.click_through.store(val, Ordering::SeqCst);
    }

    pub fn set_locked(&self, val: bool) {
        self.locked.store(val, Ordering::SeqCst);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_overlay_state_defaults() {
        let state = OverlayState::default();
        assert!(state.is_enabled());
        assert!(!state.is_click_through());
        assert!(!state.is_locked());

        state.set_click_through(true);
        assert!(state.is_click_through());

        state.set_locked(true);
        assert!(state.is_locked());
    }
}
