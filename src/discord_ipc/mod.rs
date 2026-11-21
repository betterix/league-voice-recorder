use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use serde_json::Value;

pub const STREAMKIT_CLIENT_ID: &str = "207646673902501888";
pub const TOKEN_URL: &str = "https://streamkit.discord.com/overlay/token";

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
            let mute_opt = data.get("mute").and_then(|v| v.as_bool());
            let deaf_opt = data.get("deaf").and_then(|v| v.as_bool());

            // Only update if this is actually a voice settings update payload containing mute/deaf fields
            if mute_opt.is_none() && deaf_opt.is_none() {
                return;
            }

            let mute = mute_opt.unwrap_or(false);
            let deaf = deaf_opt.unwrap_or(false);
            let effective_mute = mute || deaf;

            let prev_mute = self.is_muted.swap(effective_mute, Ordering::SeqCst);
            let prev_deaf = self.is_deafened.swap(deaf, Ordering::SeqCst);

            if prev_mute != effective_mute || prev_deaf != deaf {
                if effective_mute {
                    tracing::info!("🔇 Discord voice status: MUTED (mic input suppressed, team loopback recording continues)");
                } else {
                    tracing::info!("🎙️ Discord voice status: UNMUTED (mic input active)");
                }
            }
        }
    }

    pub fn is_muted(&self) -> bool {
        self.is_muted.load(Ordering::SeqCst)
    }

    pub fn is_deafened(&self) -> bool {
        self.is_deafened.load(Ordering::SeqCst)
    }

    pub fn spawn_watcher(self: Arc<Self>, stop_signal: Arc<AtomicBool>) {
        #[cfg(windows)]
        tokio::spawn(async move {
            use tokio::io::{AsyncReadExt, AsyncWriteExt};
            use tokio::net::windows::named_pipe::ClientOptions;

            async fn read_ipc_frame<R: AsyncReadExt + Unpin>(stream: &mut R) -> Result<(IpcOpcode, Value), ()> {
                let mut hdr = [0u8; 8];
                stream.read_exact(&mut hdr).await.map_err(|_| ())?;
                let opcode_num = u32::from_le_bytes([hdr[0], hdr[1], hdr[2], hdr[3]]);
                let length = u32::from_le_bytes([hdr[4], hdr[5], hdr[6], hdr[7]]) as usize;
                let opcode = IpcOpcode::try_from(opcode_num).map_err(|_| ())?;
                if length == 0 {
                    return Ok((opcode, Value::Null));
                }
                let mut payload = vec![0u8; length];
                stream.read_exact(&mut payload).await.map_err(|_| ())?;
                let json: Value = serde_json::from_slice(&payload).map_err(|_| ())?;
                Ok((opcode, json))
            }

            async fn send_ipc_frame<W: AsyncWriteExt + Unpin>(stream: &mut W, opcode: IpcOpcode, payload: &Value) -> Result<(), ()> {
                let bytes = serde_json::to_vec(payload).map_err(|_| ())?;
                let frame = encode_frame(opcode, &bytes);
                stream.write_all(&frame).await.map_err(|_| ())?;
                Ok(())
            }

            while !stop_signal.load(Ordering::Relaxed) {
                let mut connected_pipe = None;
                for i in 0..10 {
                    let pipe_path = format!(r"\\.\pipe\discord-ipc-{}", i);
                    if let Ok(client) = ClientOptions::new().open(&pipe_path) {
                        connected_pipe = Some(client);
                        break;
                    }
                }

                if let Some(mut client) = connected_pipe {
                    // 1. Handshake
                    let handshake = serde_json::json!({
                        "v": 1,
                        "client_id": STREAMKIT_CLIENT_ID
                    });
                    if send_ipc_frame(&mut client, IpcOpcode::Handshake, &handshake).await.is_ok() {
                        if let Ok((_op, _ready)) = read_ipc_frame(&mut client).await {
                            // 2. Auth with cached token or authorize
                            let mut authed = false;
                            let saved_token = crate::config::AppConfig::load().ok().map(|c| c.discord_ipc_token).filter(|t| !t.is_empty());

                            if let Some(token) = saved_token {
                                let auth_payload = serde_json::json!({
                                    "cmd": "AUTHENTICATE",
                                    "args": { "access_token": token },
                                    "nonce": format!("{}", std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_millis())
                                });
                                if send_ipc_frame(&mut client, IpcOpcode::Frame, &auth_payload).await.is_ok() {
                                    if let Ok((_op, auth_res)) = read_ipc_frame(&mut client).await {
                                        if auth_res.get("evt").and_then(|e| e.as_str()) != Some("ERROR") && auth_res.get("cmd").and_then(|c| c.as_str()) == Some("AUTHENTICATE") {
                                            authed = true;
                                            tracing::info!("✅ Discord IPC authenticated successfully with saved token.");
                                        }
                                    }
                                }
                            }

                            if !authed {
                                tracing::info!("Requesting Discord IPC authorization...");
                                let auth_req = serde_json::json!({
                                    "cmd": "AUTHORIZE",
                                    "args": {
                                        "client_id": STREAMKIT_CLIENT_ID,
                                        "scopes": ["rpc", "rpc.voice.read"]
                                    },
                                    "nonce": format!("{}", std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_millis())
                                });
                                if send_ipc_frame(&mut client, IpcOpcode::Frame, &auth_req).await.is_ok() {
                                    let read_fut = read_ipc_frame(&mut client);
                                    if let Ok(Ok((_op, resp))) = tokio::time::timeout(tokio::time::Duration::from_secs(30), read_fut).await {
                                        if let Some(code) = resp.get("data").and_then(|d| d.get("code")).and_then(|c| c.as_str()) {
                                            let http_client = reqwest::Client::new();
                                            if let Ok(http_res) = http_client.post(TOKEN_URL)
                                                .json(&serde_json::json!({"code": code}))
                                                .send()
                                                .await
                                            {
                                                if let Ok(tok_val) = http_res.json::<Value>().await {
                                                    if let Some(new_tok) = tok_val.get("access_token").and_then(|t| t.as_str()) {
                                                        if let Ok(mut cfg) = crate::config::AppConfig::load() {
                                                            cfg.discord_ipc_token = new_tok.to_string();
                                                            let _ = cfg.save();
                                                        }
                                                        let auth_payload = serde_json::json!({
                                                            "cmd": "AUTHENTICATE",
                                                            "args": { "access_token": new_tok },
                                                            "nonce": format!("{}", std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_millis())
                                                        });
                                                        let _ = send_ipc_frame(&mut client, IpcOpcode::Frame, &auth_payload).await;
                                                        let _ = read_ipc_frame(&mut client).await;
                                                        authed = true;
                                                        tracing::info!("✅ Discord IPC authorized and authenticated successfully.");
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }

                            if authed {
                                // 3. Query initial voice settings
                                let get_settings = serde_json::json!({
                                    "cmd": "GET_VOICE_SETTINGS",
                                    "args": {},
                                    "nonce": "settings_1"
                                });
                                if send_ipc_frame(&mut client, IpcOpcode::Frame, &get_settings).await.is_ok() {
                                    if let Ok((_op, settings_resp)) = read_ipc_frame(&mut client).await {
                                        self.update_from_json(&settings_resp);
                                    }
                                }

                                // 4. Subscribe to VOICE_SETTINGS_UPDATE events
                                let sub = serde_json::json!({
                                    "cmd": "SUBSCRIBE",
                                    "evt": "VOICE_SETTINGS_UPDATE",
                                    "args": {},
                                    "nonce": format!("{}", std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_millis())
                                });
                                if send_ipc_frame(&mut client, IpcOpcode::Frame, &sub).await.is_ok() {
                                    let _ = read_ipc_frame(&mut client).await;
                                }

                                tracing::info!("🎙️ Discord voice settings subscribed. Live mute/deafen tracking active.");

                                // 5. Read loop for live mute/deafen updates
                                while !stop_signal.load(Ordering::Relaxed) {
                                    match read_ipc_frame(&mut client).await {
                                        Ok((IpcOpcode::Ping, payload)) => {
                                            let _ = send_ipc_frame(&mut client, IpcOpcode::Pong, &payload).await;
                                        }
                                        Ok((IpcOpcode::Close, _)) => {
                                            break;
                                        }
                                        Ok((_op, json)) => {
                                            self.update_from_json(&json);
                                        }
                                        Err(_) => break,
                                    }
                                }
                            }
                        }
                    }
                }

                tokio::time::sleep(tokio::time::Duration::from_millis(2000)).await;
            }
        });
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

        // Verify non-voice payloads do not accidentally clear mute state
        let non_voice: Value = serde_json::from_str(r#"
            {
                "cmd": "DISPATCH",
                "data": {
                    "v": 1
                },
                "evt": "READY"
            }
        "#).unwrap();
        state.update_from_json(&non_voice);
        assert!(state.is_muted(), "Non-voice payload must NOT clear mute state");
    }
}
