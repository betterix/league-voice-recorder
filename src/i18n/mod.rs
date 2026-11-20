use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};
use serde_json::Value;

static I18N_STATE: OnceLock<Mutex<I18nManager>> = OnceLock::new();

pub struct I18nManager {
    current_lang: String,
    translations: HashMap<String, Value>,
    available_languages: HashMap<String, String>,
}

impl I18nManager {
    pub fn new() -> Self {
        let mut mgr = Self {
            current_lang: "en".to_string(),
            translations: HashMap::new(),
            available_languages: HashMap::new(),
        };
        mgr.refresh_available_languages();
        mgr.load_locale("en");
        mgr
    }

    fn find_locales_dir() -> PathBuf {
        let candidates = [
            PathBuf::from("locales"),
            PathBuf::from("../locales"),
            std::env::current_exe()
                .ok()
                .and_then(|p| p.parent().map(|dir| dir.join("locales")))
                .unwrap_or_else(|| PathBuf::from("locales")),
        ];

        for c in candidates {
            if c.is_dir() {
                return c;
            }
        }
        PathBuf::from("locales")
    }

    pub fn refresh_available_languages(&mut self) -> &HashMap<String, String> {
        let dir = Self::find_locales_dir();
        let mut langs = HashMap::new();

        if let Ok(entries) = fs::read_dir(dir) {
            for entry in entries.flatten() {
                let path = entry.path();
                if path.extension().and_then(|s| s.to_str()) == Some("json") {
                    if let Some(stem) = path.file_stem().and_then(|s| s.to_str()) {
                        let mut display_name = stem.to_string();
                        if let Ok(content) = fs::read_to_string(&path) {
                            if let Ok(json) = serde_json::from_str::<Value>(&content) {
                                if let Some(name) = json.get("__language_name__").and_then(|v| v.as_str()) {
                                    display_name = name.to_string();
                                }
                            }
                        }
                        langs.insert(stem.to_string(), display_name);
                    }
                }
            }
        }

        if langs.is_empty() {
            langs.insert("en".to_string(), "English".to_string());
        }

        self.available_languages = langs;
        &self.available_languages
    }

    pub fn load_locale(&mut self, code: &str) {
        if self.translations.contains_key(code) {
            return;
        }

        let dir = Self::find_locales_dir();
        let path = dir.join(format!("{}.json", code));
        if path.is_file() {
            if let Ok(content) = fs::read_to_string(&path) {
                if let Ok(val) = serde_json::from_str::<Value>(&content) {
                    self.translations.insert(code.to_string(), val);
                    return;
                }
            }
        }
    }

    pub fn set_language(&mut self, code: &str) {
        if self.available_languages.contains_key(code) {
            self.current_lang = code.to_string();
            self.load_locale(code);
        } else {
            self.current_lang = "en".to_string();
        }
        self.load_locale("en");
    }

    pub fn get_language(&self) -> &str {
        &self.current_lang
    }

    fn resolve_key<'a>(val: &'a Value, parts: &[&str]) -> Option<&'a str> {
        let mut curr = val;
        for part in parts {
            curr = curr.get(*part)?;
        }
        curr.as_str()
    }

    pub fn translate(&self, key: &str, params: &[(&str, &str)]) -> String {
        let parts: Vec<&str> = key.split('.').collect();

        // 1. Check active language
        let mut text_opt = self.translations.get(&self.current_lang)
            .and_then(|val| Self::resolve_key(val, &parts));

        // 2. Fall back to English
        if text_opt.is_none() && self.current_lang != "en" {
            text_opt = self.translations.get("en")
                .and_then(|val| Self::resolve_key(val, &parts));
        }

        let text = match text_opt {
            Some(t) => t.to_string(),
            None => return key.to_string(),
        };

        // 3. Format placeholders e.g. {version} or {scene}
        let mut result = text;
        for (k, v) in params {
            let pattern = format!("{{{}}}", k);
            result = result.replace(&pattern, v);
        }
        result
    }
}

fn get_manager() -> &'static Mutex<I18nManager> {
    I18N_STATE.get_or_init(|| Mutex::new(I18nManager::new()))
}

pub fn get_available_languages() -> HashMap<String, String> {
    let mut mgr = get_manager().lock().unwrap();
    mgr.refresh_available_languages().clone()
}

pub fn set_language(lang_code: &str) {
    let mut mgr = get_manager().lock().unwrap();
    mgr.set_language(lang_code);
}

pub fn get_language() -> String {
    let mgr = get_manager().lock().unwrap();
    mgr.get_language().to_string()
}

pub fn t(key: &str) -> String {
    let mgr = get_manager().lock().unwrap();
    mgr.translate(key, &[])
}

pub fn t_with(key: &str, params: &[(&str, &str)]) -> String {
    let mgr = get_manager().lock().unwrap();
    mgr.translate(key, params)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_i18n_fallback_and_translation() {
        set_language("en");
        let title = t("app.title");
        assert!(!title.is_empty());
        assert_ne!(title, "app.title");
    }

    #[test]
    fn test_missing_key_returns_raw_key() {
        let missing = t("non_existent_section.missing_key_xyz");
        assert_eq!(missing, "non_existent_section.missing_key_xyz");
    }

    #[test]
    fn test_placeholder_formatting() {
        let formatted = t_with("record.target_scene", &[("scene", "Discord + Mic")]);
        assert!(formatted.contains("Discord + Mic"));
    }
}
