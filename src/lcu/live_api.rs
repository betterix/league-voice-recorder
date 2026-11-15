use reqwest::Client;
use serde_json::Value;

pub struct LiveApiClient {
    client: Client,
    endpoint: String,
}

impl Default for LiveApiClient {
    fn default() -> Self {
        let client = Client::builder()
            .danger_accept_invalid_certs(true)
            .timeout(std::time::Duration::from_millis(350))
            .build()
            .unwrap_or_default();

        Self {
            client,
            endpoint: "https://127.0.0.1:2999/liveclientdata/allgamedata".to_string(),
        }
    }
}

impl LiveApiClient {
    /// Polls Riot's Live Client Data API (:2999/liveclientdata/allgamedata)
    /// and extracts gameTime as an f64 without failing on schema variations.
    pub async fn poll_game_time(&self) -> Result<Option<f64>, reqwest::Error> {
        let resp = self.client.get(&self.endpoint).send().await?;
        if resp.status().is_success() {
            if let Ok(json) = resp.json::<Value>().await {
                if let Some(gt) = json.pointer("/gameData/gameTime").and_then(|v| v.as_f64()) {
                    return Ok(Some(gt));
                }
            }
        }
        Ok(None)
    }
}
