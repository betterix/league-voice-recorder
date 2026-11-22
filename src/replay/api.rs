use reqwest::Client;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ReplayPlaybackState {
    #[serde(default)]
    pub time: f64,
    #[serde(default)]
    pub paused: bool,
    #[serde(default = "default_speed")]
    pub speed: f64,
    #[serde(default)]
    pub length: Option<f64>,
}

fn default_speed() -> f64 {
    1.0
}

pub struct ReplayApiClient {
    client: Client,
    endpoint: String,
}

impl Default for ReplayApiClient {
    fn default() -> Self {
        let client = crate::lcu::create_riot_client_builder()
            .timeout(std::time::Duration::from_millis(800))
            .build()
            .unwrap_or_default();

        Self {
            client,
            endpoint: "https://127.0.0.1:2999/replay/playback".to_string(),
        }
    }
}

impl ReplayApiClient {
    pub async fn get_playback_state(&self) -> Result<Option<ReplayPlaybackState>, reqwest::Error> {
        let resp_opt = match self.client.get(&self.endpoint).send().await {
            Ok(r) => Some(r),
            Err(_) => self.client.get("http://127.0.0.1:2999/replay/playback").send().await.ok(),
        };

        if let Some(resp) = resp_opt {
            if resp.status().is_success() {
                if let Ok(state) = resp.json::<ReplayPlaybackState>().await {
                    return Ok(Some(state));
                }
            }
        }
        Ok(None)
    }
}
