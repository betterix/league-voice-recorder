use reqwest::Client;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LiveGameData {
    #[serde(rename = "gameData")]
    pub game_data: Option<GameData>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GameData {
    #[serde(rename = "gameTime")]
    pub game_time: f64,
    #[serde(rename = "gameId")]
    pub game_id: Option<i64>,
}

pub struct LiveApiClient {
    client: Client,
    endpoint: String,
}

impl Default for LiveApiClient {
    fn default() -> Self {
        let client = Client::builder()
            .danger_accept_invalid_certs(true)
            .build()
            .unwrap_or_default();

        Self {
            client,
            endpoint: "https://127.0.0.1:2999/liveclientdata/allgamedata".to_string(),
        }
    }
}

impl LiveApiClient {
    pub async fn poll_all_game_data(&self) -> Result<LiveGameData, reqwest::Error> {
        let resp = self.client.get(&self.endpoint).send().await?;
        resp.json::<LiveGameData>().await
    }
}
