use std::path::Path;
use id3::{Tag, TagLike, Version};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct ScrimMetadata {
    pub game_id: String,
    pub team_tag: String,
    pub notes: String,
    pub game_patch: String,
    pub recorded_at: String,
}

impl ScrimMetadata {
    pub fn read_from_mp3<P: AsRef<Path>>(path: P) -> Result<Self, Box<dyn std::error::Error>> {
        let tag = Tag::read_from_path(&path)?;
        let mut meta = Self::default();

        if let Some(stem) = path.as_ref().file_stem() {
            meta.game_id = stem.to_string_lossy().to_string();
        }

        if let Some(title) = tag.title() {
            if let Some(stripped) = title.strip_prefix("Game ") {
                meta.game_id = stripped.trim().to_string();
            } else if !title.is_empty() {
                meta.game_id = title.to_string();
            }
        }

        if let Some(artist) = tag.artist() {
            meta.team_tag = artist.to_string();
        }

        if let Some(album) = tag.album() {
            let p = album.strip_prefix("Patch ").unwrap_or(album);
            meta.game_patch = p.trim().to_string();
        }

        if let Some(date) = tag.date_recorded() {
            meta.recorded_at = format!("{:04}-{:02}-{:02}", date.year, date.month.unwrap_or(1), date.day.unwrap_or(1));
        } else if let Some(year) = tag.year() {
            meta.recorded_at = format!("{:04}-01-01", year);
        }

        for comment in tag.comments() {
            let txt = comment.text.trim().trim_start_matches('\u{feff}').to_string();
            let lower = txt.to_lowercase();
            if !txt.is_empty()
                && !lower.starts_with("recorded match length:")
                && !lower.starts_with("match ended at")
                && !lower.starts_with("match length:")
                && !lower.contains("match length")
            {
                meta.notes = txt;
                break;
            }
        }

        Ok(meta)
    }

    pub fn write_to_mp3<P: AsRef<Path>>(&self, path: P) -> Result<(), Box<dyn std::error::Error>> {
        let mut tag = Tag::read_from_path(&path).unwrap_or_else(|_| Tag::new());

        tag.set_title(format!("Game {}", self.game_id));
        if !self.team_tag.is_empty() {
            tag.set_artist(&self.team_tag);
        } else {
            tag.remove_artist();
        }
        if !self.game_patch.is_empty() {
            tag.set_album(format!("Patch {}", self.game_patch));
        } else {
            tag.remove_album();
        }
        if !self.recorded_at.is_empty() {
            let parts: Vec<&str> = self.recorded_at.split('-').collect();
            if let Some(y) = parts.get(0).and_then(|s| s.parse::<i32>().ok()) {
                tag.set_year(y);
                let m = parts.get(1).and_then(|s| s.parse::<u8>().ok());
                let d = parts.get(2).and_then(|s| s.parse::<u8>().ok());
                tag.set_date_recorded(id3::Timestamp {
                    year: y,
                    month: m,
                    day: d,
                    hour: None,
                    minute: None,
                    second: None,
                });
            }
        }
        if !self.notes.is_empty() {
            tag.add_frame(id3::frame::Comment {
                lang: "eng".to_string(),
                description: "comment".to_string(),
                text: self.notes.clone(),
            });
        } else {
            tag.remove("COMM");
        }

        tag.write_to_path(path, Version::Id3v23)?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_scrim_meta_roundtrip_id3() {
        let temp_file = std::env::temp_dir().join("test_scrim_meta_roundtrip.mp3");
        // Create an empty dummy file
        std::fs::write(&temp_file, b"ID3dummy").unwrap();

        let meta = ScrimMetadata {
            game_id: "8018190134".to_string(),
            team_tag: "G2".to_string(),
            notes: "Baron steal at 24:10".to_string(),
            game_patch: "14.23".to_string(),
            recorded_at: "2026-11-20".to_string(),
        };

        let write_res = meta.write_to_mp3(&temp_file);
        assert!(write_res.is_ok());

        let read_back = ScrimMetadata::read_from_mp3(&temp_file).unwrap();
        assert_eq!(read_back.game_id, "8018190134");
        assert_eq!(read_back.team_tag, "G2");
        assert_eq!(read_back.notes, "Baron steal at 24:10");
        assert_eq!(read_back.game_patch, "14.23");
        assert_eq!(read_back.recorded_at, "2026-11-20");

        let _ = std::fs::remove_file(&temp_file);
    }

    #[test]
    fn test_legacy_match_length_comment_filtered_out() {
        let temp_file = std::env::temp_dir().join("test_legacy_comment_filtered.mp3");
        std::fs::write(&temp_file, b"ID3dummy").unwrap();

        let mut tag = Tag::new();
        tag.set_title("Game 8018190134");
        tag.add_frame(id3::frame::Comment {
            lang: "eng".to_string(),
            description: "comment".to_string(),
            text: "\u{feff}Recorded match length: 31.84s".to_string(),
        });
        tag.write_to_path(&temp_file, Version::Id3v23).unwrap();

        let meta = ScrimMetadata::read_from_mp3(&temp_file).unwrap();
        assert_eq!(meta.notes, "");

        let _ = std::fs::remove_file(&temp_file);
    }
}
