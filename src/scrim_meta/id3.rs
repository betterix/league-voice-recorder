use std::path::Path;
use id3::{Tag, TagLike, Version};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
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
        }

        for comment in tag.comments() {
            if !comment.text.is_empty() {
                meta.notes = comment.text.clone();
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
        }
        if !self.game_patch.is_empty() {
            tag.set_album(format!("Patch {}", self.game_patch));
        }
        if !self.notes.is_empty() {
            tag.add_frame(id3::frame::Comment {
                lang: "eng".to_string(),
                description: "comment".to_string(),
                text: self.notes.clone(),
            });
        }

        tag.write_to_path(path, Version::Id3v23)?;
        Ok(())
    }
}
