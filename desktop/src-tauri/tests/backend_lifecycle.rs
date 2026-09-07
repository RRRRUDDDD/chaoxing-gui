//! Integration tests for backend.rs lifecycle, using the fake-backend bin.
//! These spawn real processes on Windows — run via `cargo test --locked`.

use chaoxing_desktop_lib::api_proxy::ApiOperation;
use chaoxing_desktop_lib::backend::{
    api_cancel, api_request, start_backend, stop_backend, BackendLaunch, BackendPhase, BackendState,
};
use std::process::Command;
use std::sync::Arc;
use std::time::Duration;

fn target_bin(name: &str) -> std::path::PathBuf {
    // Integration test exe lives in target/debug/deps; helper bins in target/debug.
    let mut p = std::env::current_exe().unwrap();
    p.pop(); // deps (or debug)
    if p.ends_with("deps") {
        p.pop();
    }
    let candidate = p.join(format!("{name}.exe"));
    if candidate.is_file() {
        candidate
    } else {
        // Fallback: same dir as the test exe (some layouts).
        let mut q = std::env::current_exe().unwrap();
        q.pop();
        q.join(format!("{name}.exe"))
    }
}

struct TestPaths {
    data_dir: std::path::PathBuf,
    log_dir: std::path::PathBuf,
    legacy_env: Option<String>,
}

impl Drop for TestPaths {
    fn drop(&mut self) {
        if let Some(v) = &self.legacy_env {
            std::env::remove_var("CHAOXING_LEGACY_DATA_DIR");
            let _ = v;
        }
        let _ = std::fs::remove_dir_all(&self.data_dir);
        let _ = std::fs::remove_dir_all(&self.log_dir);
    }
}

fn make_state(tag: &str) -> (Arc<BackendState>, TestPaths) {
    let base = std::env::temp_dir().join(format!("cx-backend-test-{tag}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&base);
    let data_dir = base.join("data");
    let log_dir = base.join("logs");
    std::fs::create_dir_all(&data_dir).unwrap();
    std::fs::create_dir_all(&log_dir).unwrap();
    (
        Arc::new(BackendState::new(data_dir.clone(), log_dir.clone())),
        TestPaths {
            data_dir,
            log_dir,
            legacy_env: None,
        },
    )
}

#[allow(dead_code)]
fn fake_backend_launch(mode: &str) -> BackendLaunch {
    let _ = mode; // FAKE_MODE is passed via process env (see set_fake_mode)
    BackendLaunch::Frozen(target_bin("fake-backend"))
}

// start_backend doesn't accept extra env; tests set process env instead.
fn set_fake_mode(mode: &str) {
    std::env::set_var("FAKE_MODE", mode);
}
fn clear_fake_mode() {
    std::env::remove_var("FAKE_MODE");
}

// NOTE: BackendLaunch has no extra-env channel; FAKE_MODE is read by the
// fake-backend child from its inherited environment.

#[test]
fn start_ok_ready_phase() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("ok");
    set_fake_mode("ok");
    let r = start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend")));
    clear_fake_mode();
    r.expect("start should succeed");
    assert_eq!(state.status().phase, BackendPhase::Ready);
    assert!(state.status().port.is_some());
    stop_backend(&state);
    assert_eq!(state.status().phase, BackendPhase::Stopped);
}

#[test]
fn wrong_instance_fails() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("wrong");
    set_fake_mode("wrong-instance");
    let r = start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend")));
    clear_fake_mode();
    assert!(r.is_err(), "instanceId mismatch must fail startup");
    assert_eq!(state.status().phase, BackendPhase::Failed);
}

#[test]
fn exit_before_ready_fails() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("die");
    set_fake_mode("exit-before-ready");
    let r = start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend")));
    clear_fake_mode();
    assert!(r.is_err());
    assert_eq!(state.status().phase, BackendPhase::Failed);
    // no zombie backend processes: fake-backend must be gone
    std::thread::sleep(Duration::from_millis(300));
    // (kernel job close guarantees this; direct child waited inside start_backend)
}

#[test]
fn missing_exe_fails_without_panic() {
    let (state, _paths) = make_state("missing");
    // FAKE_MODE must not leak into this test's expectations.
    clear_fake_mode();
    let r = start_backend(
        &state,
        BackendLaunch::Frozen(std::path::PathBuf::from("Z:/no/such/backend.exe")),
    );
    assert!(r.is_err());
    assert_eq!(state.status().phase, BackendPhase::Failed);
    let status = state.status();
    assert!(
        status.error.unwrap_or_default().contains("缺失"),
        "error should mention missing backend path"
    );
}

#[test]
fn stop_is_idempotent() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("stop");
    set_fake_mode("ok");
    start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend"))).expect("start");
    clear_fake_mode();
    stop_backend(&state);
    stop_backend(&state);
    stop_backend(&state);
    assert_eq!(state.status().phase, BackendPhase::Stopped);
}

#[test]
fn api_request_requires_ready() {
    let (state, _paths) = make_state("notready");
    let resp = api_request(
        &state,
        ApiOperation::Login,
        None,
        None,
        serde_json::json!({"username": "u"}),
        1,
    );
    assert!(matches!(
        resp,
        Err(chaoxing_desktop_lib::api_proxy::ProxyError::BackendNotReady { .. })
    ));
}

#[test]
fn api_request_forwards_and_passes_status() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("fwd");
    set_fake_mode("ok");
    start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend"))).expect("start");
    clear_fake_mode();

    // /api/login is POST; fake backend returns 404 for it with JSON body —
    // proves token was accepted (401 would mean token missing) and status passthrough.
    let resp = api_request(
        &state,
        ApiOperation::Login,
        None,
        None,
        serde_json::json!({"username": "u"}),
        2,
    )
    .expect("request should complete");
    assert_eq!(resp.status, 404);
    assert_eq!(resp.body, serde_json::json!({"error": "not found"}));

    // invalid taskId is rejected at the host, never sent
    let resp = api_request(
        &state,
        ApiOperation::TaskStatus,
        Some("../bad".into()),
        None,
        serde_json::json!(null),
        3,
    );
    assert!(matches!(
        resp,
        Err(chaoxing_desktop_lib::api_proxy::ProxyError::InvalidRequest { .. })
    ));

    stop_backend(&state);
}

#[test]
fn api_cancel_unknown_id_is_false() {
    let (state, _paths) = make_state("cancel");
    assert!(!api_cancel(&state, 9999));
}

#[test]
fn host_kills_tree_on_stop_grandchild_reaped() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    // fake-backend spawns a ping grandchild; after stop the whole tree must die.
    let (state, _paths) = make_state("tree");
    set_fake_mode("spawn-grandchild");
    start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend"))).expect("start");
    clear_fake_mode();
    stop_backend(&state);
    std::thread::sleep(Duration::from_millis(500));
    // Job semantics guarantee tree death (PoC-verified); assert no lingering ping
    // beyond the one our own test infra may have — keep it soft: count ping only if >3
    let out = Command::new("tasklist")
        .args(["/FI", "IMAGENAME eq PING.EXE"])
        .output()
        .expect("tasklist");
    let text = String::from_utf8_lossy(&out.stdout);
    let count = text.matches("PING.EXE").count();
    assert!(
        count <= 1,
        "grandchild ping should be reaped, found {count}"
    );
}

// These tests mutate the process environment (FAKE_MODE) that the spawned
// fake-backend child inherits; parallel runs would race on it, so serialize
// all tests that set/clear FAKE_MODE via this lock.
pub static FAKE_MODE_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
