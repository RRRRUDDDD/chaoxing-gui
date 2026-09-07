use serde::{Deserialize, Serialize};

/// Strictly-allowed backend API operations (plan.md §3.2, 8 operations).
/// Everything else is refused at the host boundary — the webview never gets
/// a generic HTTP escape hatch.
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum ApiOperation {
    Login,
    Courses,
    ConfigRead,
    ConfigWrite,
    Start,
    TaskStatus,
    TaskDetails,
    TaskLogs,
}

impl ApiOperation {
    /// (method, path template). `{id}` is substituted with the validated taskId.
    pub fn route(&self) -> (&'static str, &'static str) {
        match self {
            ApiOperation::Login => ("POST", "/api/login"),
            ApiOperation::Courses => ("POST", "/api/courses"),
            ApiOperation::ConfigRead => ("GET", "/api/config"),
            ApiOperation::ConfigWrite => ("POST", "/api/config"),
            ApiOperation::Start => ("POST", "/api/start"),
            ApiOperation::TaskStatus => ("GET", "/api/task/{id}"),
            ApiOperation::TaskDetails => ("GET", "/api/task/{id}/details"),
            ApiOperation::TaskLogs => ("GET", "/api/logs/{id}"),
        }
    }

    pub fn needs_task_id(&self) -> bool {
        matches!(
            self,
            ApiOperation::TaskStatus | ApiOperation::TaskDetails | ApiOperation::TaskLogs
        )
    }

    pub fn is_post(&self) -> bool {
        self.route().0 == "POST"
    }
}

pub const MAX_REQUEST_BODY: usize = 1024 * 1024;
pub const MAX_RESPONSE_BODY: usize = 2 * (1024 * 1024);
/// Cursor bound for TaskLogs `after` (matches backend int ids; u32 is generous).
pub const MAX_LOG_CURSOR: u64 = u32::MAX as u64;

pub fn valid_task_id(t: &str) -> bool {
    !t.is_empty()
        && t.len() <= 128
        && t.chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
}

pub fn valid_log_cursor(after: u64) -> bool {
    after <= MAX_LOG_CURSOR
}

/// Build the URL path for an operation. Caller must have validated inputs.
pub fn build_path(
    op: ApiOperation,
    task_id: Option<&str>,
    after: Option<u64>,
) -> Result<String, String> {
    let (method, template) = op.route();
    let _ = method;
    if op.needs_task_id() {
        let id = task_id.ok_or_else(|| format!("{op:?} requires taskId"))?;
        if !valid_task_id(id) {
            return Err("invalid taskId".into());
        }
        let path = template.replace("{id}", id);
        if matches!(op, ApiOperation::TaskLogs) {
            let cursor = after.ok_or_else(|| "TaskLogs requires after cursor".to_string())?;
            if !valid_log_cursor(cursor) {
                return Err("invalid log cursor".into());
            }
            return Ok(format!("{path}?after={cursor}"));
        }
        Ok(path)
    } else {
        Ok(template.to_string())
    }
}

/// Wire shape returned to the renderer: { status, body } — mirrors axios semantics
/// (status preserved; 409/404 handled by existing frontend branches).
#[derive(Debug, Serialize)]
pub struct ProxyResponse {
    pub status: u16,
    pub body: serde_json::Value,
}

#[derive(Debug, Serialize)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum ProxyError {
    /// Backend not ready / stopped.
    BackendNotReady { phase: String },
    /// Request rejected by host validation.
    InvalidRequest { reason: String },
    /// Network-level failure talking to the backend.
    Network { reason: String },
    /// Cancelled via api_cancel.
    Cancelled,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dto_rejects_unknown_operation() {
        let raw = r#""notAnOperation""#;
        assert!(serde_json::from_str::<ApiOperation>(raw).is_err());
    }

    #[test]
    fn task_id_whitelist() {
        assert!(valid_task_id("abc-DEF_123"));
        assert!(!valid_task_id("../etc"));
        assert!(!valid_task_id("a b"));
        assert!(!valid_task_id(""));
        assert!(!valid_task_id(&"x".repeat(129)));
        assert!(valid_task_id(&"x".repeat(128)));
    }

    #[test]
    fn path_building() {
        assert_eq!(
            build_path(ApiOperation::Login, None, None).unwrap(),
            "/api/login"
        );
        assert_eq!(
            build_path(ApiOperation::TaskStatus, Some("t-1_2"), None).unwrap(),
            "/api/task/t-1_2"
        );
        assert_eq!(
            build_path(ApiOperation::TaskDetails, Some("t1"), None).unwrap(),
            "/api/task/t1/details"
        );
        assert_eq!(
            build_path(ApiOperation::TaskLogs, Some("t1"), Some(42)).unwrap(),
            "/api/logs/t1?after=42"
        );
        assert!(build_path(ApiOperation::TaskStatus, None, None).is_err());
        assert!(build_path(ApiOperation::TaskStatus, Some("../bad"), None).is_err());
        assert!(build_path(ApiOperation::TaskLogs, Some("t1"), None).is_err());
    }

    #[test]
    fn log_cursor_bounds() {
        assert!(valid_log_cursor(0));
        assert!(valid_log_cursor(u32::MAX as u64));
        assert!(!valid_log_cursor(u32::MAX as u64 + 1));
    }
}
