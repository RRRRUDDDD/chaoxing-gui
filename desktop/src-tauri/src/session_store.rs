use serde::{Deserialize, Serialize};

/// Session file schema — 1:1 port of desktop/session-store.js (v1, strict).
/// exactKeys semantics from the JS original: unknown fields are invalid.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct SessionLogin {
    pub username: String,
    pub use_cookies: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct SessionActiveTask {
    pub username: String,
    #[serde(rename = "taskId")]
    pub task_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct SessionData {
    pub version: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub login: Option<SessionLogin>,
    #[serde(
        default,
        rename = "activeTask",
        skip_serializing_if = "Option::is_none"
    )]
    pub active_task: Option<SessionActiveTask>,
}

pub const MAX_FILE_BYTES: usize = 4096;
pub const TASK_ID_RE: &str = r"^[a-zA-Z0-9_-]{1,128}$";

fn valid_username(u: &str) -> bool {
    // JS original: non-empty, <=128, value === value.trim(), no control chars.
    !u.is_empty() && u.len() <= 128 && u.trim() == u && !u.chars().any(|c| c.is_control())
}

fn valid_task_id(t: &str) -> bool {
    t.len() <= 128
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
    let bytes = match std::fs::read(path) {
        Ok(b) => b,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e),
    };
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
    let bytes = serde_json::to_vec_pretty(data).map_err(std::io::Error::other)?;
    std::fs::write(&tmp, bytes)?;
    match std::fs::rename(&tmp, path) {
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
        }
        if let Some((u, t)) = task {
            s.push_str(&format!(
                ",\"activeTask\":{{\"username\":\"{u}\",\"taskId\":\"{t}\"}}"
            ));
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
}
