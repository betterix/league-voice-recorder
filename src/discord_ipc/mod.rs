use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use serde_json::Value;

pub const STREAMKIT_CLIENT_ID: &str = "207646673902501888";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u32)]
pub enum IpcOpcode {
    Handshake = 0,
    Frame = 1,
    Close = 2,
    Ping = 3,
    Pong = 4,
}

impl TryFrom<u32> for IpcOpcode {
    type Error = ();
    fn try_from(val: u32) -> Result<Self, Self::Error> {
        match val {
            0 => Ok(Self::Handshake),
            1 => Ok(Self::Frame),
            2 => Ok(Self::Close),
            3 => Ok(Self::Ping),
            4 => Ok(Self::Pong),
            _ => Err(()),
        }
    }
}

/// Encodes an IPC packet: <opcode: u32 LE><length: u32 LE><payload bytes>
pub fn encode_frame(opcode: IpcOpcode, payload: &[u8]) -> Vec<u8> {
    let mut buf = Vec::with_capacity(8 + payload.len());
    buf.extend_from_slice(&(opcode as u32).to_le_bytes());
    buf.extend_from_slice(&(payload.len() as u32).to_le_bytes());
    buf.extend_from_slice(payload);
    buf
}

/// Decodes an IPC packet from a buffer if at least 8 bytes are present.
pub fn decode_frame(buf: &[u8]) -> Option<(IpcOpcode, &[u8], usize)> {
    if buf.len() < 8 {
        return None;
    }
    let opcode_num = u32::from_le_bytes([buf[0], buf[1], buf[2], buf[3]]);
    let length = u32::from_le_bytes([buf[4], buf[5], buf[6], buf[7]]) as usize;
    let total_size = 8 + length;

    if buf.len() < total_size {
        return None;
    }

    let opcode = IpcOpcode::try_from(opcode_num).ok()?;
    let payload = &buf[8..total_size];
    Some((opcode, payload, total_size))
}

#[derive(Debug, Clone, Default)]
pub struct DiscordVoiceState {
    pub is_muted: Arc<AtomicBool>,
    pub is_deafened: Arc<AtomicBool>,
}

impl DiscordVoiceState {
    pub fn new() -> Self {
        Self {
            is_muted: Arc::new(AtomicBool::new(false)),
            is_deafened: Arc::new(AtomicBool::new(false)),
        }
    }

    pub fn update_from_json(&self, json: &Value) {
        if let Some(data) = json.get("data") {
            if let Some(mute) = data.get("mute").and_then(|v| v.as_bool()) {
                self.is_muted.store(mute, Ordering::SeqCst);
            }
            if let Some(deaf) = data.get("deaf").and_then(|v| v.as_bool()) {
                self.is_deafened.store(deaf, Ordering::SeqCst);
            }
        }
    }

    pub fn is_muted(&self) -> bool {
        self.is_muted.load(Ordering::SeqCst)
    }

    pub fn is_deafened(&self) -> bool {
        self.is_deafened.load(Ordering::SeqCst)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_encode_decode_frame() {
        let payload = br#"{"cmd":"DISPATCH","evt":"VOICE_SETTINGS_UPDATE"}"#;
        let encoded = encode_frame(IpcOpcode::Frame, payload);

        assert_eq!(encoded.len(), 8 + payload.len());
        let (opcode, decoded_payload, consumed) = decode_frame(&encoded).unwrap();
        assert_eq!(opcode, IpcOpcode::Frame);
        assert_eq!(decoded_payload, payload);
        assert_eq!(consumed, encoded.len());
    }

    #[test]
    fn test_decode_frame_too_short() {
        let buf = [0u8; 4];
        assert_eq!(decode_frame(&buf), None);
    }

    #[test]
    fn test_voice_state_update_json() {
        let state = DiscordVoiceState::new();
        assert!(!state.is_muted());
        assert!(!state.is_deafened());

        let update: Value = serde_json::from_str(r#"
            {
                "cmd": "DISPATCH",
                "data": {
                    "mute": true,
                    "deaf": false
                },
                "evt": "VOICE_SETTINGS_UPDATE"
            }
        "#).unwrap();

        state.update_from_json(&update);
        assert!(state.is_muted());
        assert!(!state.is_deafened());
    }
}
