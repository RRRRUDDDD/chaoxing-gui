//! Desktop-only preferences, currently what the window close button does.
//!
//! Stored as `desktop-preferences.json` next to the `data` directory, never
//! inside it: migration treats any file in `data` as business data, so a
//! preference saved there would block or break the one-time legacy import.

use serde::{Deserialize, Serialize};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

pub const FILE_NAME: &str = "desktop-preferences.json";
const MAX_FILE_BYTES: u64 = 4096;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum CloseAction {
    #[default]
    Ask,
    Minimize,
    Tray,
    Exit,
}

impl CloseAction {
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "ask" => Some(CloseAction::Ask),
            "minimize" => Some(CloseAction::Minimize),
            "tray" => Some(CloseAction::Tray),
            "exit" => Some(CloseAction::Exit),
            _ => None,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            CloseAction::Ask => "ask",
            CloseAction::Minimize => "minimize",
            CloseAction::Tray => "tray",
            CloseAction::Exit => "exit",
        }
    }
}

/// The action stays a String on the wire: a derived enum would also accept
/// a single-key object, so parsing is explicit.
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct StoredPreferences {
    version: u32,
    close_action: String,
}

fn parse(bytes: &[u8]) -> Option<CloseAction> {
    let mut deserializer = serde_json::Deserializer::from_slice(bytes);
    let stored: StoredPreferences = crate::deserialize_object(&mut deserializer).ok()?;
    deserializer.end().ok()?;
    if stored.version != 1 {
        return None;
    }
    CloseAction::parse(&stored.close_action)
}

pub struct PreferencesStore {
    path: PathBuf,
    lock: Mutex<()>,
}

impl PreferencesStore {
    pub fn new(directory: &Path) -> Self {
        PreferencesStore {
            path: directory.join(FILE_NAME),
            lock: Mutex::new(()),
        }
    }

    /// Missing, oversized, malformed or unknown values all fall back to Ask.
    pub fn close_action(&self) -> CloseAction {
        let _guard = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        let Ok(file) = std::fs::File::open(&self.path) else {
            return CloseAction::Ask;
        };
        let mut bytes = Vec::new();
        if file
            .take(MAX_FILE_BYTES + 1)
            .read_to_end(&mut bytes)
            .is_err()
            || bytes.len() as u64 > MAX_FILE_BYTES
        {
            return CloseAction::Ask;
        }
        parse(&bytes).unwrap_or_default()
    }

    pub fn set_close_action(&self, action: CloseAction) -> Result<(), std::io::Error> {
        let _guard = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        crate::session_store::write_atomic(
            &self.path,
            &StoredPreferences {
                version: 1,
                close_action: action.as_str().into(),
            },
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmpdir(name: &str) -> PathBuf {
        let dir =
            std::env::temp_dir().join(format!("cx-preferences-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        dir
    }

    #[test]
    fn missing_file_asks_and_writes_round_trip() {
        let dir = tmpdir("round-trip");
        let store = PreferencesStore::new(&dir);
        assert_eq!(store.close_action(), CloseAction::Ask);
        for action in [
            CloseAction::Minimize,
            CloseAction::Tray,
            CloseAction::Exit,
            CloseAction::Ask,
        ] {
            store.set_close_action(action).unwrap();
            assert_eq!(store.close_action(), action);
        }
        let saved = std::fs::read_to_string(dir.join(FILE_NAME)).unwrap();
        assert_eq!(saved, r#"{"version":1,"closeAction":"ask"}"#);
        assert!(!dir.join(format!("{FILE_NAME}.tmp")).exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn invalid_files_fall_back_to_ask() {
        let dir = tmpdir("invalid");
        std::fs::create_dir_all(&dir).unwrap();
        let store = PreferencesStore::new(&dir);
        let oversized = format!(
            r#"{{"version":1,"closeAction":"tray","pad":"{}"}}"#,
            "x".repeat(5000)
        );
        for raw in [
            r#"["tray"]"#,
            r#"[1,"tray"]"#,
            r#"{"version":1,"closeAction":{"tray":null}}"#,
            r#"{"version":1,"closeAction":"hide"}"#,
            r#"{"version":2,"closeAction":"tray"}"#,
            r#"{"version":1,"closeAction":"tray","extra":true}"#,
            r#"{"version":1}"#,
            r#"{"version":1,"closeAction":"tray"} trailing"#,
            "not json",
            oversized.as_str(),
        ] {
            std::fs::write(dir.join(FILE_NAME), raw).unwrap();
            assert_eq!(store.close_action(), CloseAction::Ask, "accepted {raw:.60}");
        }
        std::fs::write(dir.join(FILE_NAME), r#"{"version":1,"closeAction":"tray"}"#).unwrap();
        assert_eq!(store.close_action(), CloseAction::Tray);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn action_names_are_exact() {
        for name in ["ask", "minimize", "tray", "exit"] {
            assert_eq!(CloseAction::parse(name).unwrap().as_str(), name);
        }
        for name in ["", "Tray", " tray", "quit"] {
            assert!(CloseAction::parse(name).is_none());
        }
    }
}
