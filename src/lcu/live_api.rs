use reqwest::Client;
use serde::Deserialize;

#[derive(Deserialize)]
struct GameStatsDto {
    #[serde(rename = "gameTime")]
    game_time: Option<f64>,
}

#[derive(Deserialize)]
struct AllGameDataDto {
    #[serde(rename = "gameData")]
    game_data: Option<GameStatsDto>,
}

pub struct LiveApiClient {
    client: Client,
    gamestats_endpoint: String,
    allgamedata_endpoint: String,
}

impl Default for LiveApiClient {
    fn default() -> Self {
        let client = crate::lcu::create_riot_client_builder()
            .timeout(std::time::Duration::from_millis(350))
            .build()
            .unwrap_or_default();

        Self {
            client,
            gamestats_endpoint: "https://127.0.0.1:2999/liveclientdata/gamestats".to_string(),
            allgamedata_endpoint: "https://127.0.0.1:2999/liveclientdata/allgamedata".to_string(),
        }
    }
}

impl LiveApiClient {
    /// Polls Riot's Live Client Data API (:2999/liveclientdata/gamestats)
    /// extracting ONLY gameTime (~40 bytes payload) with zero generic heap allocations.
    pub async fn poll_game_time(&self) -> Result<Option<f64>, reqwest::Error> {
        // 1. Primary lightweight endpoint: /liveclientdata/gamestats
        // Payload is tiny (~40 bytes vs ~45 KB in allgamedata).
        if let Ok(resp) = self.client.get(&self.gamestats_endpoint).send().await {
            if resp.status().is_success() {
                if let Ok(dto) = resp.json::<GameStatsDto>().await {
                    if let Some(gt) = dto.game_time {
                        return Ok(Some(gt));
                    }
                }
            }
        }

        // 2. Fallback: if gamestats is temporarily not ready or mocked with allgamedata,
        // Serde deserializes ONLY gameData.gameTime and strictly ignores all players, runes,
        // events, items, and scores with zero heap allocations.
        if let Ok(resp) = self.client.get(&self.allgamedata_endpoint).send().await {
            if resp.status().is_success() {
                if let Ok(dto) = resp.json::<AllGameDataDto>().await {
                    if let Some(gd) = dto.game_data {
                        if let Some(gt) = gd.game_time {
                            return Ok(Some(gt));
                        }
                    }
                }
            }
        }

        Ok(None)
    }
}
