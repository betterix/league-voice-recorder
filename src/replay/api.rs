use reqwest::Client;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReplayPlaybackState {
    pub time: f64,
    pub paused: bool,
    pub speed: f64,
}

pub struct ReplayApiClient {
    client: Client,
    endpoint: String,
}

impl Default for ReplayApiClient {
    fn default() -> Self {
        let client = Client::builder()
            .danger_accept_invalid_certs(true)
            .build()
            .unwrap_or_default();

        Self {
            client,
            endpoint: "https://127.0.0.1:2999/replay/playback".to_string(),
        }
    }
}

impl ReplayApiClient {
    pub async fn get_playback_state(&self) -> Result<ReplayPlaybackState, reqwest::Error> {
        let resp = self.client.get(&self.endpoint).send().await?;
        resp.json::<ReplayPlaybackState>().await
    }
}
