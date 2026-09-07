use serde::{Deserialize, Deserializer, Serialize};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

/// Session file schema — 1:1 port of desktop/session-store.js (v1, strict).
/// exactKeys semantics from the JS original: unknown fields are invalid.
#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct SessionLogin {
    pub username: String,
    pub use_cookies: bool,
}

impl<'de> Deserialize<'de> for SessionLogin {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Fields {
            username: String,
            use_cookies: bool,
        }

        let fields: Fields = crate::deserialize_object(deserializer)?;
        Ok(Self {
            username: fields.username,
            use_cookies: fields.use_cookies,
        })
    }
}

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct SessionActiveTask {
    pub username: String,
    #[serde(rename = "taskId")]
    pub task_id: String,
}

impl<'de> Deserialize<'de> for SessionActiveTask {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Fields {
            username: String,
            #[serde(rename = "taskId")]
            task_id: String,
        }

        let fields: Fields = crate::deserialize_object(deserializer)?;
        Ok(Self {
            username: fields.username,
            task_id: fields.task_id,
        })
    }
}

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct SessionData {
    pub version: u32,
    pub login: Option<SessionLogin>,
    #[serde(rename = "activeTask")]
    pub active_task: Option<SessionActiveTask>,
}

impl<'de> Deserialize<'de> for SessionData {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Fields {
            version: u32,
            #[serde(deserialize_with = "required_nullable")]
            login: Option<SessionLogin>,
            #[serde(rename = "activeTask", deserialize_with = "required_nullable")]
            active_task: Option<SessionActiveTask>,
        }

        let fields: Fields = crate::deserialize_object(deserializer)?;
        Ok(Self {
            version: fields.version,
            login: fields.login,
            active_task: fields.active_task,
        })
    }
}

// Option normally accepts a missing key. A custom deserializer retains null
// while requiring the key, matching Electron's exactKeys v1 schema.
pub(crate) fn required_nullable<'de, D, T>(deserializer: D) -> Result<Option<T>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    Option::deserialize(deserializer)
}

impl Default for SessionData {
    fn default() -> Self {
        Self {
            version: 1,
            login: None,
            active_task: None,
        }
    }
}

pub const MAX_FILE_BYTES: usize = 4096;
pub const TASK_ID_RE: &str = r"^[a-zA-Z0-9_-]{1,128}$";

fn valid_username(u: &str) -> bool {
    // JavaScript counts UTF-16 units and trims ECMAScript whitespace; Rust's
    // byte length / Unicode trim differ for CJK, emoji, U+0085 and U+FEFF.
    let js_whitespace = |c| {
        matches!(c,
        '\u{0009}'..='\u{000d}' | '\u{0020}' | '\u{00a0}' | '\u{1680}' |
        '\u{2000}'..='\u{200a}' | '\u{2028}' | '\u{2029}' | '\u{202f}' |
        '\u{205f}' | '\u{3000}' | '\u{feff}')
    };
    !u.is_empty()
        && u.encode_utf16().count() <= 128
        && u.trim_matches(js_whitespace) == u
        && !u.chars().any(|c| c <= '\u{001f}' || c == '\u{007f}')
}

fn valid_task_id(t: &str) -> bool {
    !t.is_empty()
        && t.len() <= 128
        && t.chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
}

/// Validate a parsed SessionData against the strict schema from session-store.js.
/// Returns Err(reason) when invalid.
pub fn validate(data: &SessionData) -> Result<(), String> {
    if data.version != 1 {
        return Err("unsupported session version".into());
    }
    if let Some(login) = data.login.as_ref() {
        if !login.use_cookies {
            return Err("login.use_cookies must be true".into());
        }
        if !valid_username(&login.username) {
            return Err("invalid login.username".into());
        }
    }
    if let Some(task) = data.active_task.as_ref() {
        if !valid_username(&task.username) {
            return Err("invalid activeTask.username".into());
        }
        if !valid_task_id(&task.task_id) {
            return Err("invalid activeTask.taskId".into());
        }
        let login_user = data.login.as_ref().map(|l| l.username.as_str());
        if login_user != Some(task.username.as_str()) {
            return Err("activeTask.username must match login.username".into());
        }
    }
    Ok(())
}

/// Read + validate the session file at `path`. Mirrors session-store.js `read`:
/// - missing / corrupt / oversized / schema-invalid → Ok(None)
/// - other IO errors → Err
pub fn read(path: &std::path::Path) -> Result<Option<SessionData>, std::io::Error> {
    let meta = match std::fs::metadata(path) {
        Ok(m) => m,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e),
    };
    if meta.len() as usize > MAX_FILE_BYTES {
        return Ok(None);
    }
    let file = match std::fs::File::open(path) {
        Ok(f) => f,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e),
    };
    let mut bytes = Vec::new();
    file.take(MAX_FILE_BYTES as u64 + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() > MAX_FILE_BYTES {
        return Ok(None);
    }
    let data: SessionData = match serde_json::from_slice(&bytes) {
        Ok(d) => d,
        Err(_) => return Ok(None),
    };
    if validate(&data).is_err() {
        return Ok(None);
    }
    Ok(Some(data))
}

/// Persist `data` atomically: write tmp + rename over `path`. Mirrors session-store.js write.
/// remember_login semantics: switching username clears active_task.
pub fn remember_login(path: &std::path::Path, username: &str) -> Result<SessionData, SessionError> {
    let current = read(path)?;
    let mut data = current.unwrap_or(SessionData {
        version: 1,
        login: None,
        active_task: None,
    });
    let switch = data
        .login
        .as_ref()
        .map(|l| l.username != username)
        .unwrap_or(false);
    if switch {
        data.active_task = None;
    }
    data.login = Some(SessionLogin {
        username: username.to_string(),
        use_cookies: true,
    });
    validate(&data).map_err(SessionError::Validation)?;
    write_atomic(path, &data)?;
    Ok(data)
}

pub fn remember_task(
    path: &std::path::Path,
    username: &str,
    task_id: &str,
) -> Result<SessionData, SessionError> {
    let current = read(path)?;
    let mut data = current.ok_or(SessionError::NoLogin)?;
    let login = data.login.as_ref().ok_or(SessionError::NoLogin)?;
    if login.username != username {
        return Err(SessionError::AccountMismatch);
    }
    data.active_task = Some(SessionActiveTask {
        username: username.to_string(),
        task_id: task_id.to_string(),
    });
    validate(&data).map_err(SessionError::Validation)?;
    write_atomic(path, &data)?;
    Ok(data)
}

/// Clear only the recovery task, including an empty/missing session. Electron
/// rememberTask(null) writes an empty v1 session when no login is remembered.
pub fn clear_task(path: &Path) -> Result<SessionData, SessionError> {
    let mut data = read(path)?.unwrap_or_default();
    data.active_task = None;
    write_atomic(path, &data)?;
    Ok(data)
}

/// One store per host. The lock covers the entire read/modify/write transaction,
/// including clear, so concurrent IPC cannot resurrect an old account/task.
pub struct SessionStore {
    path: PathBuf,
    lock: Mutex<()>,
}

impl SessionStore {
    pub fn new(directory: &Path) -> Self {
        Self {
            path: directory.join("renderer-session.json"),
            lock: Mutex::new(()),
        }
    }

    pub fn read(&self) -> Result<SessionData, std::io::Error> {
        let _guard = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        Ok(read(&self.path)?.unwrap_or_default())
    }

    pub fn remember_login(&self, username: &str) -> Result<SessionData, SessionError> {
        let _guard = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        remember_login(&self.path, username)
    }

    pub fn remember_task(
        &self,
        task: Option<SessionActiveTask>,
    ) -> Result<SessionData, SessionError> {
        let _guard = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        match task {
            Some(task) => remember_task(&self.path, &task.username, &task.task_id),
            None => clear_task(&self.path),
        }
    }

    pub fn clear(&self) -> Result<SessionData, std::io::Error> {
        let _guard = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        clear(&self.path)?;
        Ok(SessionData::default())
    }
}

pub fn clear(path: &std::path::Path) -> Result<(), std::io::Error> {
    let tmp = tmp_path(path);
    match std::fs::remove_file(path) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => return Err(e),
    }
    match std::fs::remove_file(&tmp) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => return Err(e),
    }
    Ok(())
}

#[derive(Debug)]
pub enum SessionError {
    Validation(String),
    NoLogin,
    AccountMismatch,
    Io(std::io::Error),
}

impl std::fmt::Display for SessionError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SessionError::Validation(s) => write!(f, "session validation failed: {s}"),
            SessionError::NoLogin => write!(f, "no remembered login"),
            SessionError::AccountMismatch => write!(f, "account mismatch"),
            SessionError::Io(e) => write!(f, "session io error: {e}"),
        }
    }
}

impl From<std::io::Error> for SessionError {
    fn from(e: std::io::Error) -> Self {
        SessionError::Io(e)
    }
}

fn tmp_path(path: &std::path::Path) -> std::path::PathBuf {
    let mut name = path.file_name().unwrap_or_default().to_os_string();
    name.push(".tmp");
    path.with_file_name(name)
}

fn write_atomic(path: &std::path::Path, data: &SessionData) -> Result<(), std::io::Error> {
    let tmp = tmp_path(path);
    let bytes = serde_json::to_vec(data).map_err(std::io::Error::other)?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let write = || {
        use std::io::Write;
        let mut file = std::fs::File::create(&tmp)?;
        file.write_all(&bytes)?;
        file.sync_all()?;
        drop(file);
        std::fs::rename(&tmp, path)
    };
    match write() {
        Ok(()) => Ok(()),
        Err(e) => {
            let _ = std::fs::remove_file(&tmp);
            Err(e)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn p2_object_boundary_rejects_positional_session_deserialization() {
        assert!(
            serde_json::from_value::<SessionLogin>(serde_json::json!(["alice", true])).is_err()
        );
        assert!(
            serde_json::from_value::<SessionActiveTask>(serde_json::json!(["alice", "t"])).is_err()
        );
        assert!(serde_json::from_value::<SessionData>(serde_json::json!([1, null, null])).is_err());
    }

    #[test]
    fn p2_object_boundary_disk_read_rejects_arrays_at_every_schema_level() {
        let dir = tmpdir("p2-object-only");
        let path = dir.join("renderer-session.json");
        for raw in [
            serde_json::json!([1, ["alice", true], ["alice", "t"]]),
            serde_json::json!({"version":1,"login":["alice",true],"activeTask":null}),
            serde_json::json!({"version":1,"login":{"username":"alice","use_cookies":true},"activeTask":["alice","t"]}),
        ] {
            std::fs::write(&path, serde_json::to_vec(&raw).unwrap()).unwrap();
            assert!(read(&path).unwrap().is_none(), "accepted {raw}");
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn p2_object_boundary_session_maps_and_explicit_nulls_round_trip() {
        for raw in [
            serde_json::json!({"version":1,"login":null,"activeTask":null}),
            serde_json::json!({"version":1,"login":{"username":"alice","use_cookies":true},"activeTask":null}),
            serde_json::json!({"version":1,"login":{"username":"alice","use_cookies":true},"activeTask":{"username":"alice","taskId":"t"}}),
        ] {
            let data: SessionData = serde_json::from_value(raw.clone()).unwrap();
            validate(&data).unwrap();
            assert_eq!(serde_json::to_value(data).unwrap(), raw);
        }
    }

    fn tmpdir(name: &str) -> std::path::PathBuf {
        let dir =
            std::env::temp_dir().join(format!("cx-session-store-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn session_json(login: Option<(&str, bool)>, task: Option<(&str, &str)>) -> String {
        let mut s = String::from("{");
        s.push_str("\"version\":1");
        if let Some((u, uc)) = login {
            s.push_str(&format!(
                ",\"login\":{{\"username\":\"{u}\",\"use_cookies\":{uc}}}"
            ));
        } else {
            s.push_str(",\"login\":null");
        }
        if let Some((u, t)) = task {
            s.push_str(&format!(
                ",\"activeTask\":{{\"username\":\"{u}\",\"taskId\":\"{t}\"}}"
            ));
        } else {
            s.push_str(",\"activeTask\":null");
        }
        s.push('}');
        s
    }

    // Group 1: persistence across instances
    #[test]
    fn persists_login_and_task_across_reads() {
        let dir = tmpdir("persist");
        let path = dir.join("renderer-session.json");
        assert!(read(&path).unwrap().is_none());

        remember_login(&path, "user1").unwrap();
        remember_task(&path, "user1", "task-1").unwrap();

        let data = read(&path).unwrap().expect("session should exist");
        assert_eq!(data.login.as_ref().unwrap().username, "user1");
        assert_eq!(data.active_task.as_ref().unwrap().task_id, "task-1");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn login_switch_clears_active_task() {
        let dir = tmpdir("switch");
        let path = dir.join("renderer-session.json");
        remember_login(&path, "user1").unwrap();
        remember_task(&path, "user1", "task-1").unwrap();

        remember_login(&path, "user2").unwrap();
        let data = read(&path).unwrap().unwrap();
        assert_eq!(data.login.as_ref().unwrap().username, "user2");
        assert!(data.active_task.is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    // Group 2: rejection matrix
    #[test]
    fn rejects_oversized_username() {
        let dir = tmpdir("reject-user");
        let path = dir.join("renderer-session.json");
        std::fs::write(&path, session_json(Some((&"a".repeat(129), true)), None)).unwrap();
        assert!(read(&path).unwrap().is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn rejects_control_chars_in_username() {
        let dir = tmpdir("reject-ctrl");
        let path = dir.join("renderer-session.json");
        std::fs::write(
            &path,
            "{\"version\":1,\"login\":{\"username\":\"a\\nb\",\"use_cookies\":true}}",
        )
        .unwrap();
        assert!(read(&path).unwrap().is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn rejects_bad_task_id() {
        let dir = tmpdir("reject-task");
        let path = dir.join("renderer-session.json");
        std::fs::write(
            &path,
            session_json(Some(("user1", true)), Some(("user1", "../escape"))),
        )
        .unwrap();
        assert!(read(&path).unwrap().is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn rejects_use_cookies_false() {
        let dir = tmpdir("reject-cookies");
        let path = dir.join("renderer-session.json");
        std::fs::write(&path, session_json(Some(("user1", false)), None)).unwrap();
        assert!(read(&path).unwrap().is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn rejects_unknown_extra_field() {
        let dir = tmpdir("reject-extra");
        let path = dir.join("renderer-session.json");
        std::fs::write(
            &path,
            "{\"version\":1,\"login\":{\"username\":\"u\",\"use_cookies\":true},\"password\":\"hunter2\"}",
        )
        .unwrap();
        assert!(read(&path).unwrap().is_none(), "password must be rejected");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn rejects_active_task_account_mismatch() {
        let dir = tmpdir("reject-mismatch");
        let path = dir.join("renderer-session.json");
        std::fs::write(
            &path,
            session_json(Some(("user1", true)), Some(("user2", "t1"))),
        )
        .unwrap();
        assert!(read(&path).unwrap().is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    // Group 3: corrupt / oversized / password on disk
    #[test]
    fn corrupt_json_returns_none() {
        let dir = tmpdir("corrupt");
        let path = dir.join("renderer-session.json");
        std::fs::write(&path, "{ not json").unwrap();
        assert!(read(&path).unwrap().is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn oversized_file_returns_none() {
        let dir = tmpdir("oversize");
        let path = dir.join("renderer-session.json");
        let big = format!("{{\"version\":1,\"padding\":\"{}\"}}", "x".repeat(5000));
        std::fs::write(&path, big).unwrap();
        assert!(read(&path).unwrap().is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    // tmp handling
    #[test]
    fn clear_removes_session_and_tmp() {
        let dir = tmpdir("clear");
        let path = dir.join("renderer-session.json");
        remember_login(&path, "user1").unwrap();
        let tmp = dir.join("renderer-session.json.tmp");
        std::fs::write(&tmp, "leftover").unwrap();

        clear(&path).unwrap();
        assert!(!path.exists());
        assert!(!tmp.exists());
        assert!(read(&path).unwrap().is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn remember_task_without_login_errors() {
        let dir = tmpdir("nologin");
        let path = dir.join("renderer-session.json");
        assert!(matches!(
            remember_task(&path, "u", "t"),
            Err(SessionError::NoLogin)
        ));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn remember_task_account_mismatch_errors() {
        let dir = tmpdir("mismatch-write");
        let path = dir.join("renderer-session.json");
        remember_login(&path, "user1").unwrap();
        assert!(matches!(
            remember_task(&path, "user2", "t"),
            Err(SessionError::AccountMismatch)
        ));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn p2_written_session_has_all_node_schema_keys() {
        let dir = tmpdir("p2-null-keys");
        let path = dir.join("renderer-session.json");
        remember_login(&path, "alice").unwrap();
        let disk: serde_json::Value =
            serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
        assert_eq!(
            disk,
            serde_json::json!({
                "version": 1, "login": {"username": "alice", "use_cookies": true},
                "activeTask": null
            })
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn p2_missing_nullable_keys_are_invalid_like_node() {
        let dir = tmpdir("p2-required-keys");
        let path = dir.join("renderer-session.json");
        for raw in [
            r#"{"version":1}"#,
            r#"{"version":1,"login":{"username":"alice","use_cookies":true}}"#,
            r#"{"version":1,"activeTask":null}"#,
        ] {
            std::fs::write(&path, raw).unwrap();
            assert!(
                read(&path).unwrap().is_none(),
                "accepted missing keys: {raw}"
            );
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn p2_empty_task_id_cannot_replace_saved_task() {
        let dir = tmpdir("p2-empty-task");
        let path = dir.join("renderer-session.json");
        remember_login(&path, "alice").unwrap();
        remember_task(&path, "alice", "keep-this-task").unwrap();
        let previous = std::fs::read(&path).unwrap();
        assert!(remember_task(&path, "alice", "").is_err());
        assert_eq!(std::fs::read(&path).unwrap(), previous);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn p2_username_length_and_whitespace_match_javascript() {
        let dir = tmpdir("p2-utf16");
        let path = dir.join("renderer-session.json");
        for username in ["中".repeat(128), "😀".repeat(64), "\u{0085}alice".into()] {
            assert!(
                remember_login(&path, &username).is_ok(),
                "rejected {username}"
            );
        }
        for username in ["中".repeat(129), "😀".repeat(65), "\u{feff}alice".into()] {
            assert!(remember_login(&path, &username).is_err());
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn p2_write_creates_private_profile_parent() {
        let dir = tmpdir("p2-parent");
        let path = dir.join("new-profile/data/renderer-session.json");
        remember_login(&path, "alice").unwrap();
        assert!(path.is_file());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn p2_read_io_failure_is_visible() {
        let dir = tmpdir("p2-read-io");
        let path = dir.join("renderer-session.json");
        std::fs::create_dir(&path).unwrap();
        assert!(read(&path).is_err());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn p2_failed_windows_replace_preserves_previous_file() {
        use std::os::windows::fs::OpenOptionsExt;
        let dir = tmpdir("p2-locked-replace");
        let path = dir.join("renderer-session.json");
        remember_login(&path, "alice").unwrap();
        let before = std::fs::read(&path).unwrap();
        let locked = std::fs::OpenOptions::new()
            .read(true)
            .share_mode(1)
            .open(&path)
            .unwrap();
        assert!(remember_login(&path, "bob").is_err());
        assert_eq!(std::fs::read(&path).unwrap(), before);
        assert!(!tmp_path(&path).exists());
        drop(locked);
        assert!(remember_login(&path, "bob").is_ok());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
