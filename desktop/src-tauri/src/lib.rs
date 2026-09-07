pub mod api_proxy;
pub mod backend;
pub mod migration;
pub mod session_store;
pub mod windows_job;

use backend::BackendState;

/// Wire the Tauri app: single-instance lock first (before any backend spawn),
/// backend start in setup, commands, stop-on-exit.
pub fn run() {
    use backend::{detect_launch, start_backend, stop_backend, BackendState};
    use std::sync::Arc;
    use tauri::Manager;

    let app: tauri::App = build_app();
    let handle = app.handle().clone();

    // Data/log dirs: app_data_dir()/data (plan.md §4.2); logs beside them.
    let data_dir = handle
        .path()
        .app_data_dir()
        .map(|d| d.join("data"))
        .unwrap_or_else(|_| std::env::temp_dir().join("chaoxing-desktop").join("data"));
    let log_dir = handle
        .path()
        .app_log_dir()
        .unwrap_or_else(|_| data_dir.join("logs"));

    let state = Arc::new(BackendState::new(data_dir.clone(), log_dir.clone()));
    app.manage(state.clone());

    backend::host_log(&log_dir, "[host] starting");

    // One-time legacy data migration BEFORE backend start (plan.md §4.2):
    // imported config.ini must be in place before Python imports resolve paths.
    match migration::migrate(&data_dir) {
        Ok(outcome) => backend::host_log(&log_dir, &format!("[migration] {outcome:?}")),
        Err(e) => backend::host_log(&log_dir, &format!("[migration] FAILED: {e}")),
    }

    // Start the backend synchronously in setup (single-instance already won).
    match detect_launch(&handle) {
        Ok(launch) => {
            if let Err(e) = start_backend(&state, launch) {
                backend::host_log(&log_dir, &format!("[host] backend start failed: {e}"));
                // Keep the window alive so backend_status can surface the error
                // (P2 adds the UI banner).
            }
        }
        Err(e) => {
            state_phase_fail(&state, e.clone());
            backend::host_log(
                &log_dir,
                &format!("[host] backend launch detection failed: {e}"),
            );
        }
    }

    let state_for_run = state.clone();
    app.run(move |_app_handle, event| {
        if let tauri::RunEvent::ExitRequested { .. } = event {
            stop_backend(&state_for_run);
        }
    });
}

fn state_phase_fail(state: &std::sync::Arc<BackendState>, msg: String) {
    *state.error.lock().unwrap_or_else(|e| e.into_inner()) = Some(msg);
    *state.phase.lock().unwrap_or_else(|e| e.into_inner()) = backend::BackendPhase::Failed;
}

fn build_app() -> tauri::App {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            use tauri::Manager;
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.set_focus();
            }
        }))
        .invoke_handler(tauri::generate_handler![
            backend_status,
            api_request_cmd,
            api_cancel_cmd,
            session_read,
            session_remember_login,
            session_remember_task,
            session_clear
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
}

#[tauri::command]
fn backend_status(state: tauri::State<'_, std::sync::Arc<BackendState>>) -> backend::BackendStatus {
    state.status()
}

#[tauri::command]
fn api_request_cmd(
    state: tauri::State<'_, std::sync::Arc<BackendState>>,
    operation: api_proxy::ApiOperation,
    payload: serde_json::Value,
    request_id: u64,
    task_id: Option<String>,
    after: Option<u64>,
) -> Result<api_proxy::ProxyResponse, api_proxy::ProxyError> {
    backend::api_request(&state, operation, task_id, after, payload, request_id)
}

#[tauri::command]
fn api_cancel_cmd(state: tauri::State<'_, std::sync::Arc<BackendState>>, request_id: u64) -> bool {
    backend::api_cancel(&state, request_id)
}

#[tauri::command]
fn session_read(
    state: tauri::State<'_, std::sync::Arc<BackendState>>,
) -> Option<session_store::SessionData> {
    let path = state.data_dir.join("renderer-session.json");
    session_store::read(&path).unwrap_or(None)
}

#[tauri::command]
fn session_remember_login(
    state: tauri::State<'_, std::sync::Arc<BackendState>>,
    username: String,
) -> Result<(), String> {
    let path = state.data_dir.join("renderer-session.json");
    session_store::remember_login(&path, &username)
        .map(|_| ())
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn session_remember_task(
    state: tauri::State<'_, std::sync::Arc<BackendState>>,
    username: String,
    task_id: String,
) -> Result<(), String> {
    let path = state.data_dir.join("renderer-session.json");
    session_store::remember_task(&path, &username, &task_id)
        .map(|_| ())
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn session_clear(state: tauri::State<'_, std::sync::Arc<BackendState>>) -> Result<(), String> {
    let path = state.data_dir.join("renderer-session.json");
    session_store::clear(&path).map_err(|e| e.to_string())
}
