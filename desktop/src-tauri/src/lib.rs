pub mod api_proxy;
pub mod backend;
pub mod migration;
pub mod session_store;
pub mod windows_job;

use backend::BackendState;
use serde::de::DeserializeOwned;
use serde::Deserialize;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use tauri::Manager;

/// State is registered before the webview loads. Migration and backend startup
/// run off the event loop, so status, cancellation and window close stay usable.
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .setup(|app| {
            let dev_root = development_root(app.handle())?;
            let data_dir = match &dev_root {
                Some(root) => root.join("data"),
                None => app.path().app_data_dir()?.join("data"),
            };
            let log_dir = match &dev_root {
                Some(root) => root.join("logs"),
                None => app.path().app_log_dir()?,
            };
            let state = Arc::new(BackendState::new(data_dir.clone(), log_dir.clone()));
            app.manage(state.clone());
            app.manage(session_store::SessionStore::new(&data_dir));
            let notice = Arc::new(Mutex::new(None));
            app.manage(StartupNotice(notice.clone()));

            // Explicit construction isolates development's WebView2 profile.
            let config = app.config().app.windows.first().ok_or("main window config missing")?;
            let mut window = tauri::WebviewWindowBuilder::from_config(app, config)?
                .on_navigation(allowed_navigation)
                .on_new_window(|_, _| tauri::webview::NewWindowResponse::Deny);
            if let Some(root) = &dev_root {
                window = window.data_directory(root.join("webview"));
            }
            #[cfg(debug_assertions)]
            if std::env::var("CHAOXING_TAURI_DEV_HIDDEN").as_deref() == Ok("1") {
                window = window.visible(false);
            }
            window.build()?;

            let handle = app.handle().clone();
            std::thread::spawn(move || {
                backend::host_log(&log_dir, "[host] starting");
                // Never read a real Electron profile implicitly in development.
                let import_legacy = !cfg!(debug_assertions)
                    || std::env::var_os(migration::LEGACY_DIR_ENV).is_some();
                if import_legacy {
                    match migration::migrate(&data_dir) {
                        Ok(migration::MigrationOutcome::DeferredLegacyRunning) => {
                            state_phase_fail(&state, "请先关闭旧版桌面应用，再关闭此窗口并重新打开，以导入原有数据。".into());
                            return;
                        }
                        Ok(migration::MigrationOutcome::ImportedButSessionInvalid(reason)) => {
                            *notice.lock().unwrap_or_else(|e| e.into_inner()) = Some("旧账号记录无法导入，请重新登录。其他有效数据已导入，原文件已保留。".into());
                            backend::host_log(&log_dir, &format!("[migration] session skipped: {reason}"));
                        }
                        Ok(outcome) => backend::host_log(&log_dir, &format!("[migration] {outcome:?}")),
                        Err(error) => {
                            backend::host_log(&log_dir, &format!("[migration] failed: {error}"));
                            state_phase_fail(&state, "旧数据导入失败，原有数据已保留。请检查数据目录权限后重新打开应用。".into());
                            return;
                        }
                    }
                }
                match launch_backend(&handle) {
                    Ok(launch) => {
                        if let Err(error) = backend::start_backend(&state, launch) {
                            backend::host_log(&log_dir, &format!("[host] backend start failed: {error}"));
                        }
                    }
                    Err(error) => state_phase_fail(&state, error),
                }
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            backend_status, api_request, api_cancel, session_read,
            session_remember_login, session_remember_task, session_clear
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|handle, event| {
        if let tauri::RunEvent::ExitRequested { .. } = event {
            if let Some(state) = handle.try_state::<Arc<BackendState>>() {
                backend::stop_backend(&state);
            }
        }
    });
}

fn development_root(app: &tauri::AppHandle) -> Result<Option<PathBuf>, Box<dyn std::error::Error>> {
    #[cfg(debug_assertions)]
    {
        let root = match std::env::var_os("CHAOXING_TAURI_DEV_ROOT") {
            Some(root) => PathBuf::from(root),
            None => app.path().app_local_data_dir()?.join("development"),
        };
        if !root.is_absolute() {
            return Err("CHAOXING_TAURI_DEV_ROOT must be an absolute isolated directory".into());
        }
        Ok(Some(root))
    }
    #[cfg(not(debug_assertions))]
    {
        let _ = app;
        Ok(None)
    }
}

fn launch_backend(app: &tauri::AppHandle) -> Result<backend::BackendLaunch, String> {
    #[cfg(debug_assertions)]
    if let Some(path) = std::env::var_os("CHAOXING_TAURI_DEV_BACKEND") {
        let path = PathBuf::from(path);
        if !path.is_absolute() {
            return Err("测试后端路径必须为绝对路径".into());
        }
        return Ok(backend::BackendLaunch::Frozen(path));
    }
    backend::detect_launch(app)
}

fn state_phase_fail(state: &Arc<BackendState>, message: String) {
    let mut phase = state.phase.lock().unwrap_or_else(|e| e.into_inner());
    if !matches!(
        *phase,
        backend::BackendPhase::Starting | backend::BackendPhase::Ready
    ) {
        return;
    }
    *state.error.lock().unwrap_or_else(|e| e.into_inner()) = Some(message.clone());
    *phase = backend::BackendPhase::Failed;
    backend::host_log(&state.log_dir, &format!("[host] failed: {message}"));
}

fn allowed_navigation(url: &tauri::Url) -> bool {
    if !url.username().is_empty() || url.password().is_some() {
        return false;
    }
    let origin = (url.scheme(), url.host_str(), url.port());
    matches!(
        origin,
        ("http" | "https", Some("tauri.localhost"), None) | ("tauri", Some("localhost"), None)
    ) || (cfg!(debug_assertions) && matches!(origin, ("http", Some("localhost"), Some(3000))))
}

/// Serde's derived structs also accept positional sequences. Wire/session DTOs
/// must enter through a map while retaining derived field and duplicate checks.
pub(crate) fn deserialize_object<'de, D, T>(deserializer: D) -> Result<T, D::Error>
where
    D: serde::Deserializer<'de>,
    T: Deserialize<'de>,
{
    struct ObjectVisitor<T>(std::marker::PhantomData<T>);

    impl<'de, T: Deserialize<'de>> serde::de::Visitor<'de> for ObjectVisitor<T> {
        type Value = T;

        fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            formatter.write_str("a JSON object")
        }

        fn visit_map<M: serde::de::MapAccess<'de>>(self, map: M) -> Result<T, M::Error> {
            T::deserialize(serde::de::value::MapAccessDeserializer::new(map))
        }
    }

    deserializer.deserialize_map(ObjectVisitor(std::marker::PhantomData))
}

fn decode_args<T: DeserializeOwned>(body: &tauri::ipc::InvokeBody) -> Result<T, String> {
    match body {
        tauri::ipc::InvokeBody::Json(value) if value.is_object() => {
            serde_json::from_value(value.clone()).map_err(|_| "请求参数格式错误".into())
        }
        _ => Err("请求参数格式错误".into()),
    }
}

fn command_args<T: DeserializeOwned>(
    window: &tauri::WebviewWindow,
    ipc: &tauri::ipc::Request<'_>,
) -> Result<T, String> {
    if window.label() != "main"
        || !window
            .url()
            .map(|url| allowed_navigation(&url))
            .unwrap_or(false)
    {
        return Err("禁止访问桌面命令".into());
    }
    decode_args(ipc.body())
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct EmptyArgs {}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ApiArgs {
    request: api_proxy::ApiRequest,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct CancelArgs {
    request_id: u64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LoginArgs {
    username: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct TaskArgs {
    // The key is required; null explicitly clears only the task.
    #[serde(deserialize_with = "session_store::required_nullable")]
    task: Option<session_store::SessionActiveTask>,
}

struct StartupNotice(Arc<Mutex<Option<String>>>);

#[derive(serde::Serialize)]
struct HostStatus {
    #[serde(flatten)]
    backend: backend::BackendStatus,
    #[serde(skip_serializing_if = "Option::is_none")]
    notice: Option<String>,
}

#[tauri::command]
fn backend_status(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<BackendState>>,
    ipc: tauri::ipc::Request<'_>,
    notice: tauri::State<'_, StartupNotice>,
) -> Result<HostStatus, String> {
    let _: EmptyArgs = command_args(&window, &ipc)?;
    Ok(HostStatus {
        backend: state.status(),
        notice: notice.0.lock().unwrap_or_else(|e| e.into_inner()).clone(),
    })
}

#[tauri::command]
async fn api_request(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<BackendState>>,
    ipc: tauri::ipc::Request<'_>,
) -> Result<api_proxy::ProxyResponse, api_proxy::ProxyError> {
    let args: ApiArgs = command_args(&window, &ipc)
        .map_err(|reason| api_proxy::ProxyError::InvalidRequest { reason })?;
    args.request.validate()?;
    let request = args.request;
    let state = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        backend::api_request(
            &state,
            request.operation,
            request.task_id,
            request.after,
            request.payload,
            request.request_id,
        )
    })
    .await
    .map_err(|_| api_proxy::ProxyError::Network {
        reason: "桌面请求执行失败".into(),
    })?
}

#[tauri::command]
fn api_cancel(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<BackendState>>,
    ipc: tauri::ipc::Request<'_>,
) -> Result<bool, String> {
    let args: CancelArgs = command_args(&window, &ipc)?;
    if !api_proxy::valid_request_id(args.request_id) {
        return Err("请求编号无效".into());
    }
    Ok(backend::api_cancel(&state, args.request_id))
}

#[tauri::command]
fn session_read(
    window: tauri::WebviewWindow,
    store: tauri::State<'_, session_store::SessionStore>,
    ipc: tauri::ipc::Request<'_>,
) -> Result<session_store::SessionData, String> {
    let _: EmptyArgs = command_args(&window, &ipc)?;
    store.read().map_err(|_| "无法读取保存的账号".into())
}

#[tauri::command]
fn session_remember_login(
    window: tauri::WebviewWindow,
    store: tauri::State<'_, session_store::SessionStore>,
    ipc: tauri::ipc::Request<'_>,
) -> Result<session_store::SessionData, String> {
    let args: LoginArgs = command_args(&window, &ipc)?;
    store
        .remember_login(&args.username)
        .map_err(|error| match error {
            session_store::SessionError::Validation(_) => "账号格式错误".into(),
            _ => "无法保存账号，请重试".into(),
        })
}

#[tauri::command]
fn session_remember_task(
    window: tauri::WebviewWindow,
    store: tauri::State<'_, session_store::SessionStore>,
    ipc: tauri::ipc::Request<'_>,
) -> Result<session_store::SessionData, String> {
    let args: TaskArgs = command_args(&window, &ipc)?;
    store.remember_task(args.task).map_err(|error| match error {
        session_store::SessionError::Validation(_) => "任务信息格式错误".into(),
        session_store::SessionError::NoLogin | session_store::SessionError::AccountMismatch => {
            "任务账号不匹配".into()
        }
        session_store::SessionError::Io(_) => "无法保存账号，请重试".into(),
    })
}

#[tauri::command]
fn session_clear(
    window: tauri::WebviewWindow,
    store: tauri::State<'_, session_store::SessionStore>,
    ipc: tauri::ipc::Request<'_>,
) -> Result<session_store::SessionData, String> {
    let _: EmptyArgs = command_args(&window, &ipc)?;
    store
        .clear()
        .map_err(|_| "无法清除保存的账号，请重试".into())
}

#[cfg(test)]
mod command_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn p2_object_boundary_rejects_positional_ipc_envelopes() {
        for (name, rejected) in [
            (
                "empty",
                decode_args::<EmptyArgs>(&tauri::ipc::InvokeBody::Json(json!([]))).is_err(),
            ),
            (
                "login",
                decode_args::<LoginArgs>(&tauri::ipc::InvokeBody::Json(json!(["alice"]))).is_err(),
            ),
            (
                "cancel",
                decode_args::<CancelArgs>(&tauri::ipc::InvokeBody::Json(json!([1]))).is_err(),
            ),
            (
                "task",
                decode_args::<TaskArgs>(&tauri::ipc::InvokeBody::Json(json!([null]))).is_err(),
            ),
            (
                "request",
                decode_args::<ApiArgs>(&tauri::ipc::InvokeBody::Json(
                    json!([{"operation":"configRead","payload":null,"requestId":1}]),
                ))
                .is_err(),
            ),
        ] {
            assert!(rejected, "accepted positional {name} envelope");
        }
    }

    #[test]
    fn p2_object_boundary_rejects_nested_api_array() {
        let ipc = tauri::ipc::InvokeBody::Json(json!({"request":["taskLogs",null,1,"t",0]}));
        assert!(decode_args::<ApiArgs>(&ipc).is_err());
    }

    #[test]
    fn p2_object_boundary_rejects_nested_task_array_and_preserves_null() {
        let ipc = tauri::ipc::InvokeBody::Json(json!({"task":["alice","t"]}));
        assert!(decode_args::<TaskArgs>(&ipc).is_err());
        assert!(
            decode_args::<TaskArgs>(&tauri::ipc::InvokeBody::Json(json!({"task":null}))).is_ok()
        );
        assert!(decode_args::<TaskArgs>(&tauri::ipc::InvokeBody::Json(
            json!({"task":{"username":"alice","taskId":"t"}})
        ))
        .is_ok());
        assert!(decode_args::<TaskArgs>(&tauri::ipc::InvokeBody::Json(json!({}))).is_err());
    }

    #[test]
    fn exact_envelope_rejects_credentials_and_extra_arguments() {
        assert!(decode_args::<EmptyArgs>(&tauri::ipc::InvokeBody::Json(
            json!({"password":"synthetic"})
        ))
        .is_err());
        assert!(decode_args::<LoginArgs>(&tauri::ipc::InvokeBody::Json(
            json!({"username":"alice","password":"synthetic"})
        ))
        .is_err());
        assert!(decode_args::<TaskArgs>(&tauri::ipc::InvokeBody::Json(json!({}))).is_err());
        assert!(
            decode_args::<TaskArgs>(&tauri::ipc::InvokeBody::Json(json!({"task":null}))).is_ok()
        );
        assert!(decode_args::<CancelArgs>(&tauri::ipc::InvokeBody::Json(
            json!({"requestId":1,"url":"http://example.test"})
        ))
        .is_err());
        assert!(decode_args::<EmptyArgs>(&tauri::ipc::InvokeBody::Raw(vec![])).is_err());
    }

    #[test]
    fn navigation_rejects_foreign_origins_and_credentials() {
        for url in ["http://tauri.localhost/", "tauri://localhost/index.html"] {
            assert!(allowed_navigation(&url.parse().unwrap()));
        }
        for url in [
            "https://example.test/",
            "http://tauri.localhost:9999/",
            "http://tauri.localhost@evil.test/",
            "http://user@tauri.localhost/",
            "file:///C:/temp/test.html",
            "data:text/html,test",
            "http://localhost:3001/",
        ] {
            assert!(!allowed_navigation(&url.parse().unwrap()), "accepted {url}");
        }
    }
}
