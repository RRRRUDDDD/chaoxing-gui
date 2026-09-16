use serde::{Deserialize, Deserializer, Serialize};

/// Strictly-allowed backend API operations.
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
    TaskResume,
    TaskStop,
    TaskOpenDownloads,
    TaskDetails,
    TaskLogs,
}

/// The renderer can request only these operations and their explicit options.
#[derive(Debug)]
pub struct ApiRequest {
    pub operation: ApiOperation,
    pub payload: serde_json::Value,
    pub request_id: u64,
    pub task_id: Option<String>,
    pub after: Option<u64>,
}

impl<'de> Deserialize<'de> for ApiRequest {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        #[derive(Deserialize)]
        #[serde(rename_all = "camelCase", deny_unknown_fields)]
        struct Fields {
            #[serde(deserialize_with = "string_operation")]
            operation: ApiOperation,
            // This key must exist even when its value is null.
            #[serde(deserialize_with = "required_payload")]
            payload: serde_json::Value,
            request_id: u64,
            #[serde(default, deserialize_with = "present_option")]
            task_id: Option<String>,
            #[serde(default, deserialize_with = "present_option")]
            after: Option<u64>,
        }

        let fields: Fields = crate::deserialize_object(deserializer)?;
        Ok(Self {
            operation: fields.operation,
            payload: fields.payload,
            request_id: fields.request_id,
            task_id: fields.task_id,
            after: fields.after,
        })
    }
}

fn string_operation<'de, D: Deserializer<'de>>(deserializer: D) -> Result<ApiOperation, D::Error> {
    // Derived enums also accept tagged objects; the IPC contract requires a string.
    let operation = String::deserialize(deserializer)?;
    ApiOperation::deserialize(serde::de::value::StringDeserializer::<D::Error>::new(
        operation,
    ))
}

fn required_payload<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> Result<serde_json::Value, D::Error> {
    serde_json::Value::deserialize(deserializer)
}

// Omission is allowed; an explicitly supplied null is not an operation option.
fn present_option<'de, D, T>(deserializer: D) -> Result<Option<T>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    T::deserialize(deserializer).map(Some)
}

impl ApiRequest {
    pub fn validate(&self) -> Result<(), ProxyError> {
        let invalid = |reason: &str| ProxyError::InvalidRequest {
            reason: reason.into(),
        };
        if !valid_request_id(self.request_id) {
            return Err(invalid(
                "requestId must be a positive JavaScript safe integer",
            ));
        }
        build_path(self.operation, self.task_id.as_deref(), self.after)
            .map_err(|reason| ProxyError::InvalidRequest { reason })?;
        if self.operation.is_post() {
            if !self.payload.is_object() {
                return Err(invalid("POST payload must be a JSON object"));
            }
            let body =
                serde_json::to_vec(&self.payload).map_err(|error| ProxyError::InvalidRequest {
                    reason: format!("payload serialization failed: {error}"),
                })?;
            if body.len() > MAX_REQUEST_BODY {
                return Err(invalid("请求体超过 1MB 上限"));
            }
        } else if !self.payload.is_null() {
            return Err(invalid("GET operations require a null payload"));
        }
        Ok(())
    }
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
            ApiOperation::TaskResume => ("POST", "/api/task/{id}/resume"),
            ApiOperation::TaskStop => ("POST", "/api/task/{id}/stop"),
            ApiOperation::TaskOpenDownloads => ("POST", "/api/task/{id}/open-downloads"),
            ApiOperation::TaskDetails => ("GET", "/api/task/{id}/details"),
            ApiOperation::TaskLogs => ("GET", "/api/logs/{id}"),
        }
    }

    pub fn needs_task_id(&self) -> bool {
        matches!(
            self,
            ApiOperation::TaskStatus
                | ApiOperation::TaskResume
                | ApiOperation::TaskStop
                | ApiOperation::TaskOpenDownloads
                | ApiOperation::TaskDetails
                | ApiOperation::TaskLogs
        )
    }

    pub fn is_post(&self) -> bool {
        self.route().0 == "POST"
    }
}

pub const MAX_REQUEST_BODY: usize = 1024 * 1024;
pub const MAX_RESPONSE_BODY: usize = 2 * (1024 * 1024);
pub const MAX_REQUEST_ID: u64 = (1u64 << 53) - 1;
/// Cursor bound for TaskLogs `after` (matches backend int ids; u32 is generous).
pub const MAX_LOG_CURSOR: u64 = u32::MAX as u64;

pub fn valid_request_id(request_id: u64) -> bool {
    (1..=MAX_REQUEST_ID).contains(&request_id)
}

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
    let (_, template) = op.route();
    if !op.needs_task_id() && task_id.is_some() {
        return Err(format!("{op:?} does not accept taskId"));
    }
    if !matches!(op, ApiOperation::TaskLogs) && after.is_some() {
        return Err(format!("{op:?} does not accept after"));
    }
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
    /// The complete HTTP exchange, including the response body, timed out.
    Timeout { reason: String },
    /// Cancelled via api_cancel.
    Cancelled,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn p2_object_boundary_rejects_positional_api_request_deserialization() {
        for raw in [
            serde_json::json!(["taskLogs", null, 1, "t", 0]),
            serde_json::json!(["start", {}, 1]),
        ] {
            assert!(
                serde_json::from_value::<ApiRequest>(raw.clone()).is_err(),
                "accepted {raw}"
            );
        }
    }

    #[test]
    fn dto_rejects_unknown_operation() {
        let raw = r#""notAnOperation""#;
        assert!(serde_json::from_str::<ApiOperation>(raw).is_err());
    }

    #[test]
    fn dto_rejects_non_string_operation() {
        for operation in [
            serde_json::json!({"configRead": null}),
            serde_json::json!({"start": null}),
            serde_json::json!(["configRead"]),
            serde_json::Value::Null,
            serde_json::json!(true),
            serde_json::json!(1),
        ] {
            let raw = serde_json::json!({"operation": operation, "payload": null, "requestId": 1});
            assert!(
                serde_json::from_value::<ApiRequest>(raw.clone()).is_err(),
                "accepted operation value: {raw}"
            );
            assert!(
                serde_json::from_str::<ApiRequest>(&raw.to_string()).is_err(),
                "accepted operation JSON: {raw}"
            );
        }
    }

    #[test]
    fn dto_requires_exact_fields_and_operation_options() {
        for raw in [
            serde_json::json!({"operation":"configRead", "requestId":1}),
            serde_json::json!({"operation":"configRead", "requestId":1, "payload":null, "url":"http://example.invalid"}),
            serde_json::json!({"operation":"configRead", "requestId":1, "payload":null, "taskId":null}),
            serde_json::json!({"operation":"configRead", "requestId":1, "payload":null, "after":null}),
            serde_json::json!({"operation":"configRead", "requestId":1.5, "payload":null}),
            serde_json::json!({"operation":"configRead", "requestId":-1, "payload":null}),
            serde_json::json!({"operation":"task-status", "requestId":1, "payload":null}),
        ] {
            assert!(
                serde_json::from_value::<ApiRequest>(raw.clone()).is_err(),
                "{raw}"
            );
        }
        for raw in [
            serde_json::json!({"operation":"configRead", "requestId":0, "payload":null}),
            serde_json::json!({"operation":"configRead", "requestId":MAX_REQUEST_ID + 1, "payload":null}),
            serde_json::json!({"operation":"configRead", "requestId":1, "payload":null, "taskId":"t"}),
            serde_json::json!({"operation":"taskStatus", "requestId":1, "payload":null, "taskId":"t", "after":0}),
            serde_json::json!({"operation":"start", "requestId":1, "payload":null}),
            serde_json::json!({"operation":"taskLogs", "requestId":1, "payload":null, "taskId":"t"}),
        ] {
            let request = serde_json::from_value::<ApiRequest>(raw.clone()).unwrap();
            assert!(request.validate().is_err(), "{raw}");
        }
    }

    #[test]
    fn task_stop_keeps_post_payload_and_task_path_boundaries() {
        let raw = serde_json::json!({
            "operation": "taskStop", "requestId": 1,
            "payload": {"username": "fixture"}, "taskId": "t-1_2"
        });
        let request: ApiRequest = serde_json::from_value(raw.clone()).unwrap();
        request.validate().unwrap();
        assert_eq!(request.operation.route(), ("POST", "/api/task/{id}/stop"));
        assert_eq!(
            build_path(request.operation, request.task_id.as_deref(), None).unwrap(),
            "/api/task/t-1_2/stop"
        );
        for (field, value) in [
            ("payload", serde_json::Value::Null),
            ("payload", serde_json::json!([])),
            ("taskId", serde_json::json!("../bad")),
            ("taskId", serde_json::json!("a%2fb")),
            ("after", serde_json::json!(0)),
        ] {
            let mut invalid = raw.clone();
            invalid[field] = value;
            let request: ApiRequest = serde_json::from_value(invalid).unwrap();
            assert!(request.validate().is_err(), "accepted invalid {field}");
        }
        let mut missing_id = raw;
        missing_id.as_object_mut().unwrap().remove("taskId");
        let request: ApiRequest = serde_json::from_value(missing_id).unwrap();
        assert!(request.validate().is_err());
    }

    #[test]
    fn task_open_downloads_keeps_post_payload_and_task_path_boundaries() {
        let raw = serde_json::json!({
            "operation": "taskOpenDownloads", "requestId": 1,
            "payload": {"username": "fixture"}, "taskId": "t-1_2"
        });
        let request: ApiRequest = serde_json::from_value(raw.clone()).unwrap();
        request.validate().unwrap();
        assert_eq!(
            request.operation.route(),
            ("POST", "/api/task/{id}/open-downloads")
        );
        assert_eq!(
            build_path(request.operation, request.task_id.as_deref(), None).unwrap(),
            "/api/task/t-1_2/open-downloads"
        );
        for (field, value) in [
            ("payload", serde_json::Value::Null),
            ("payload", serde_json::json!([])),
            ("payload", serde_json::json!("fixture")),
            ("taskId", serde_json::json!("")),
            ("taskId", serde_json::json!("../bad")),
            ("taskId", serde_json::json!("a%2fb")),
            ("taskId", serde_json::json!("a b")),
            ("taskId", serde_json::json!("a".repeat(129))),
            ("taskId", serde_json::json!("t?after=0")),
            ("after", serde_json::json!(0)),
        ] {
            let mut invalid = raw.clone();
            invalid[field] = value;
            let request: ApiRequest = serde_json::from_value(invalid).unwrap();
            assert!(request.validate().is_err(), "accepted invalid {field}");
        }
        for (field, value) in [
            ("taskId", serde_json::Value::Null),
            ("after", serde_json::Value::Null),
            ("query", serde_json::json!({"username": "fixture"})),
            ("url", serde_json::json!("file:///downloads")),
        ] {
            let mut invalid = raw.clone();
            invalid[field] = value;
            assert!(serde_json::from_value::<ApiRequest>(invalid).is_err());
        }
        let mut missing_id = raw;
        missing_id.as_object_mut().unwrap().remove("taskId");
        let request: ApiRequest = serde_json::from_value(missing_id).unwrap();
        assert!(request.validate().is_err());
    }

    #[test]
    fn start_keeps_course_tool_payloads() {
        for (task_type, tool_options) in [
            ("visits", serde_json::json!({"count": 10, "interval": 30})),
            ("catalog", serde_json::json!({"purpose": "download"})),
            (
                "video_time",
                serde_json::json!({"source_task_id": "catalog-1", "resource_ids": ["video-1"], "minutes": 0.5}),
            ),
            (
                "download",
                serde_json::json!({"source_task_id": "catalog-1", "resource_ids": ["file-1"]}),
            ),
        ] {
            let payload = serde_json::json!({
                "username": "fixture", "password": "", "use_cookies": true,
                "course_list": ["course-1"], "task_type": task_type,
                "tool_options": tool_options
            });
            let request: ApiRequest = serde_json::from_value(serde_json::json!({
                "operation": "start", "requestId": 1, "payload": payload
            }))
            .unwrap();
            request.validate().unwrap();
            assert_eq!(request.operation.route(), ("POST", "/api/start"));
            assert_eq!(request.payload, payload);
        }
    }

    #[test]
    fn dto_accepts_all_camel_case_operations() {
        for operation in [
            "login",
            "courses",
            "configRead",
            "configWrite",
            "start",
            "taskStatus",
            "taskResume",
            "taskStop",
            "taskOpenDownloads",
            "taskDetails",
            "taskLogs",
        ] {
            let mut raw = serde_json::json!({"operation":operation, "requestId":MAX_REQUEST_ID, "payload":null});
            if matches!(
                operation,
                "login"
                    | "courses"
                    | "configWrite"
                    | "start"
                    | "taskResume"
                    | "taskStop"
                    | "taskOpenDownloads"
            ) {
                raw["payload"] = serde_json::json!({"username":"fixture"});
            }
            if matches!(
                operation,
                "taskStatus"
                    | "taskResume"
                    | "taskStop"
                    | "taskOpenDownloads"
                    | "taskDetails"
                    | "taskLogs"
            ) {
                raw["taskId"] = "t-1".into();
            }
            if operation == "taskLogs" {
                raw["after"] = 0.into();
            }
            serde_json::from_value::<ApiRequest>(raw)
                .unwrap()
                .validate()
                .unwrap();
        }
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
            build_path(ApiOperation::TaskResume, Some("t1"), None).unwrap(),
            "/api/task/t1/resume"
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
        assert!(build_path(ApiOperation::TaskResume, None, None).is_err());
        assert!(build_path(ApiOperation::TaskResume, Some("../bad"), None).is_err());
        assert!(build_path(ApiOperation::TaskResume, Some("t1"), Some(1)).is_err());
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
