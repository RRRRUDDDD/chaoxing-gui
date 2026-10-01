pub mod api_proxy;
pub mod backend;
pub mod migration;
pub mod preferences;
pub mod session_store;
pub mod window_close;
pub mod windows_job;

use backend::BackendState;
use serde::de::DeserializeOwned;
use serde::Deserialize;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use tauri::{Emitter, Manager};
use window_close::{CloseDecision, CloseGate};

/// State is registered before the webview loads. Migration and backend startup
/// run off the event loop, so status, cancellation and window close stay usable.
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            // The window may be hidden in the tray.
            show_main_window(app);
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
            let state = Arc::new(BackendState::new(data_dir.clone(), log_dir));
            app.manage(state.clone());
            app.manage(session_store::SessionStore::new(&data_dir));
            let preferences_dir = data_dir.parent().ok_or("data directory has no parent")?;
            app.manage(preferences::PreferencesStore::new(preferences_dir));
            app.manage(CloseGate::default());
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
            let main_window = window.build()?;
            let close_app = app.handle().clone();
            let close_window = main_window.clone();
            main_window.on_window_event(move |event| {
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    on_close_requested(&close_app, &close_window, api);
                }
            });
            build_tray(app)?;

            let handle = app.handle().clone();
            std::thread::spawn(move || {
                state.host_log("[host] starting");
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
                            state.host_log(&format!("[migration] session skipped: {reason}"));
                        }
                        Ok(outcome) => state.host_log(&format!("[migration] {outcome:?}")),
                        Err(error) => {
                            state.host_log(&format!("[migration] failed: {error}"));
                            state_phase_fail(&state, "旧数据导入失败，原有数据已保留。请检查数据目录权限后重新打开应用。".into());
                            return;
                        }
                    }
                }
                match launch_backend(&handle) {
                    Ok(launch) => {
                        if let Err(error) = backend::start_backend(&state, launch) {
                            state.host_log(&format!("[host] backend start failed: {error}"));
                        }
                    }
                    Err(error) => state_phase_fail(&state, error),
                }
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            backend_status, open_repository, open_ocs_docs, api_request, api_cancel, session_read,
            session_remember_login, session_remember_task, session_clear,
            close_prompt_shown, close_choice, preferences_read, preferences_write
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

fn show_main_window<R: tauri::Runtime>(app: &tauri::AppHandle<R>) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn build_tray(app: &tauri::App) -> tauri::Result<()> {
    use tauri::menu::{MenuBuilder, MenuItemBuilder};
    use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};

    let show = MenuItemBuilder::with_id("show", "显示主窗口").build(app)?;
    let quit = MenuItemBuilder::with_id("quit", "退出").build(app)?;
    let menu = MenuBuilder::new(app).items(&[&show, &quit]).build()?;
    let mut tray = TrayIconBuilder::with_id("main")
        .tooltip("超星学习通·自动化学习助手")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "show" => show_main_window(app),
            // Runs the normal ExitRequested path, which stops the backend.
            "quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                show_main_window(tray.app_handle());
            }
        });
    if let Some(icon) = app.default_window_icon() {
        tray = tray.icon(icon.clone());
    }
    tray.build(app)?;
    Ok(())
}

fn on_close_requested(
    app: &tauri::AppHandle,
    window: &tauri::WebviewWindow,
    api: &tauri::CloseRequestApi,
) {
    let action = app.state::<preferences::PreferencesStore>().close_action();
    match app.state::<CloseGate>().on_close_requested(action) {
        CloseDecision::Allow => {}
        CloseDecision::Minimize => {
            api.prevent_close();
            let _ = window.minimize();
        }
        CloseDecision::Hide => {
            api.prevent_close();
            let _ = window.hide();
        }
        CloseDecision::Prompt(id) => {
            api.prevent_close();
            let _ = window.emit_to("main", window_close::PROMPT_EVENT, id);
            let app = app.clone();
            std::thread::spawn(move || {
                std::thread::sleep(window_close::PROMPT_ACK_TIMEOUT);
                if app.state::<CloseGate>().expire(id) {
                    app.state::<Arc<BackendState>>()
                        .host_log("[host] close prompt not acknowledged; exiting");
                    app.exit(0);
                }
            });
        }
    }
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
    state.host_log(&format!("[host] failed: {message}"));
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

#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct PromptArgs {
    prompt_id: u64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CloseChoiceArgs {
    action: String,
    remember: bool,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct PreferencesArgs {
    close_action: String,
}

#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct PreferencesView {
    close_action: &'static str,
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
fn open_repository(
    window: tauri::WebviewWindow,
    ipc: tauri::ipc::Request<'_>,
) -> Result<(), String> {
    let _: EmptyArgs = command_args(&window, &ipc)?;
    open_browser(BrowserPage::Repository)
}

#[tauri::command]
fn open_ocs_docs(
    window: tauri::WebviewWindow,
    ipc: tauri::ipc::Request<'_>,
) -> Result<(), String> {
    let _: EmptyArgs = command_args(&window, &ipc)?;
    open_browser(BrowserPage::OcsDocs)
}

enum BrowserPage {
    Repository,
    OcsDocs,
}

fn open_browser(page: BrowserPage) -> Result<(), String> {
    // Only fixed destinations are supported; no renderer URL or shell arguments.
    #[cfg(windows)]
    {
        use windows::core::w;
        use windows::Win32::UI::Shell::ShellExecuteW;
        use windows::Win32::UI::WindowsAndMessaging::SW_SHOWNORMAL;

        let url = match page {
            BrowserPage::Repository => w!("https://github.com/RRRRUDDDD/chaoxing-gui"),
            BrowserPage::OcsDocs => w!("https://docs.ocsjs.com/docs/work"),
        };
        let result = unsafe {
            ShellExecuteW(
                None,
                w!("open"),
                url,
                None,
                None,
                SW_SHOWNORMAL,
            )
        };
        if result.0 as isize <= 32 {
            return Err("无法打开系统默认浏览器".into());
        }
        Ok(())
    }
    #[cfg(not(windows))]
    {
        let _ = page;
        Err("当前平台暂不支持打开系统浏览器".into())
    }
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

#[tauri::command]
fn close_prompt_shown(
    window: tauri::WebviewWindow,
    gate: tauri::State<'_, CloseGate>,
    ipc: tauri::ipc::Request<'_>,
) -> Result<bool, String> {
    let args: PromptArgs = command_args(&window, &ipc)?;
    Ok(gate.acknowledge(args.prompt_id))
}

#[tauri::command]
fn close_choice(
    window: tauri::WebviewWindow,
    gate: tauri::State<'_, CloseGate>,
    store: tauri::State<'_, preferences::PreferencesStore>,
    state: tauri::State<'_, Arc<BackendState>>,
    ipc: tauri::ipc::Request<'_>,
) -> Result<(), String> {
    let args: CloseChoiceArgs = command_args(&window, &ipc)?;
    let action = match args.action.as_str() {
        "cancel" if !args.remember => None,
        "minimize" | "tray" | "exit" => preferences::CloseAction::parse(&args.action),
        _ => return Err("关闭方式无效".into()),
    };
    gate.resolve();
    let Some(action) = action else {
        return Ok(());
    };
    let saved = if args.remember {
        store.set_close_action(action).map_err(|error| {
            state.host_log(&format!("[host] close preference not saved: {error}"));
            "无法保存关闭方式，请重试".to_string()
        })
    } else {
        Ok(())
    };
    match action {
        preferences::CloseAction::Minimize => {
            let _ = window.minimize();
        }
        preferences::CloseAction::Tray => {
            let _ = window.hide();
        }
        _ => window.app_handle().exit(0),
    }
    saved
}

#[tauri::command]
fn preferences_read(
    window: tauri::WebviewWindow,
    store: tauri::State<'_, preferences::PreferencesStore>,
    ipc: tauri::ipc::Request<'_>,
) -> Result<PreferencesView, String> {
    let _: EmptyArgs = command_args(&window, &ipc)?;
    Ok(PreferencesView {
        close_action: store.close_action().as_str(),
    })
}

#[tauri::command]
fn preferences_write(
    window: tauri::WebviewWindow,
    store: tauri::State<'_, preferences::PreferencesStore>,
    ipc: tauri::ipc::Request<'_>,
) -> Result<PreferencesView, String> {
    let args: PreferencesArgs = command_args(&window, &ipc)?;
    let action = preferences::CloseAction::parse(&args.close_action).ok_or("关闭方式无效")?;
    store
        .set_close_action(action)
        .map_err(|_| "无法保存关闭方式，请重试")?;
    Ok(PreferencesView {
        close_action: action.as_str(),
    })
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
    fn browser_commands_reject_renderer_destinations_and_arguments() {
        let body = tauri::ipc::InvokeBody::Json;
        assert!(decode_args::<EmptyArgs>(&body(json!({}))).is_ok());
        for rejected in [
            json!({"url": "https://example.test/"}),
            json!({"path": "C:/Windows/System32/cmd.exe"}),
            json!({"args": ["/c", "echo test"]}),
            json!([]),
            json!(null),
        ] {
            assert!(decode_args::<EmptyArgs>(&body(rejected)).is_err());
        }
    }

    #[test]
    fn close_and_preference_envelopes_are_exact_objects() {
        let body = tauri::ipc::InvokeBody::Json;
        assert!(decode_args::<PromptArgs>(&body(json!({"promptId": 3}))).is_ok());
        assert!(decode_args::<PromptArgs>(&body(json!([3]))).is_err());
        assert!(decode_args::<PromptArgs>(&body(json!({"promptId": "3"}))).is_err());
        assert!(
            decode_args::<CloseChoiceArgs>(&body(json!({"action": "tray", "remember": true})))
                .is_ok()
        );
        for rejected in [
            json!(["tray", true]),
            json!({"action": "tray"}),
            json!({"action": {"tray": null}, "remember": false}),
            json!({"action": "tray", "remember": "yes"}),
            json!({"action": "tray", "remember": false, "path": "C:/"}),
        ] {
            assert!(
                decode_args::<CloseChoiceArgs>(&body(rejected.clone())).is_err(),
                "accepted {rejected}"
            );
        }
        assert!(decode_args::<PreferencesArgs>(&body(json!({"closeAction": "ask"}))).is_ok());
        assert!(decode_args::<PreferencesArgs>(&body(json!({"closeAction": ["ask"]}))).is_err());
        assert!(decode_args::<PreferencesArgs>(&body(json!({}))).is_err());
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
