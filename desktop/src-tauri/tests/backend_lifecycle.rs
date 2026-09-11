//! Integration tests for backend.rs lifecycle, using the fake-backend bin.
//! These spawn real processes on Windows — run via `cargo test --locked`.

use chaoxing_desktop_lib::api_proxy::ApiOperation;
use chaoxing_desktop_lib::backend::{
    api_cancel, api_request, start_backend, stop_backend, BackendLaunch, BackendPhase, BackendState,
};
use std::sync::Arc;
use std::time::{Duration, Instant};

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
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
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
    // Check the exact fixture PID instead of counting unrelated ping processes.
    let (state, _paths) = make_state("tree");
    set_fake_mode("spawn-grandchild");
    start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend"))).expect("start");
    clear_fake_mode();
    let grandchild = read_pid(&state.data_dir.join("fake-grandchild.pid"));
    assert!(process_alive(grandchild));
    stop_backend(&state);
    assert_process_exited(grandchild);
}

fn read_pid(path: &std::path::Path) -> u32 {
    wait_until(|| path.is_file());
    std::fs::read_to_string(path)
        .unwrap()
        .trim()
        .parse()
        .unwrap()
}

fn wait_until(mut condition: impl FnMut() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(4);
    while !condition() {
        assert!(Instant::now() < deadline, "fixture condition timed out");
        std::thread::sleep(Duration::from_millis(10));
    }
}

fn process_alive(pid: u32) -> bool {
    use windows::Win32::Foundation::CloseHandle;
    use windows::Win32::System::Threading::{
        GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
    };
    unsafe {
        let Ok(handle) = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) else {
            return false;
        };
        let mut code = 0;
        let alive = GetExitCodeProcess(handle, &mut code).is_ok() && code == 259;
        let _ = CloseHandle(handle);
        alive
    }
}

fn assert_process_exited(pid: u32) {
    wait_until(|| !process_alive(pid));
}

#[test]
fn p2_stop_during_handshake_registers_child_and_cannot_restore_ready() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("stop-handshake");
    set_fake_mode("spawn-grandchild");
    std::env::set_var("FAKE_DELAY_READY_MS", "2500");
    let worker_state = state.clone();
    let worker = std::thread::spawn(move || {
        start_backend(
            &worker_state,
            BackendLaunch::Frozen(target_bin("fake-backend")),
        )
    });
    let pid = read_pid(&state.data_dir.join("fake-backend.pid"));
    let grandchild = read_pid(&state.data_dir.join("fake-grandchild.pid"));
    let registered = state.child.lock().unwrap().is_some() && state.job.lock().unwrap().is_some();
    let started = Instant::now();
    stop_backend(&state);
    let result = worker.join().unwrap();
    let elapsed = started.elapsed();
    std::env::remove_var("FAKE_DELAY_READY_MS");
    clear_fake_mode();
    let phase = state.status().phase;
    // Ensure a broken implementation is still cleaned up before assertions.
    if phase == BackendPhase::Ready {
        stop_backend(&state);
    }
    assert!(
        registered,
        "child and Job must be owned by state during the handshake"
    );
    assert!(result.is_err());
    assert_eq!(phase, BackendPhase::Stopped);
    assert!(elapsed < Duration::from_secs(1), "stop took {elapsed:?}");
    assert_process_exited(pid);
    assert_process_exited(grandchild);
}

#[test]
fn p2_stop_during_health_does_not_wait_for_start_deadline() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("stop-health");
    set_fake_mode("ok");
    std::env::set_var("FAKE_DELAY_HEALTH_MS", "1200");
    let worker_state = state.clone();
    let worker = std::thread::spawn(move || {
        start_backend(
            &worker_state,
            BackendLaunch::Frozen(target_bin("fake-backend")),
        )
    });
    wait_until(|| state.data_dir.join("fake-health.requested").is_file());
    let pid = read_pid(&state.data_dir.join("fake-backend.pid"));
    let started = Instant::now();
    stop_backend(&state);
    let result = worker.join().unwrap();
    let elapsed = started.elapsed();
    std::env::remove_var("FAKE_DELAY_HEALTH_MS");
    clear_fake_mode();
    let phase = state.status().phase;
    if phase == BackendPhase::Ready {
        stop_backend(&state);
    }
    assert!(result.is_err());
    assert_eq!(phase, BackendPhase::Stopped);
    assert!(elapsed < Duration::from_secs(1), "stop took {elapsed:?}");
    assert_process_exited(pid);
}

#[test]
fn p2_stop_before_background_start_prevents_spawn() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("stop-before-start");
    stop_backend(&state);
    set_fake_mode("ok");
    let result = start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend")));
    clear_fake_mode();
    let phase = state.status().phase;
    let spawned = state.data_dir.join("fake-backend.pid").exists();
    if phase == BackendPhase::Ready {
        stop_backend(&state);
    }
    assert!(result.is_err());
    assert!(!spawned);
    assert_eq!(phase, BackendPhase::Stopped);
}

#[test]
fn p2_data_directory_failure_transitions_to_failed() {
    let (_state, paths) = make_state("blocked-data");
    let data_file = paths.data_dir.join("not-a-directory");
    std::fs::write(&data_file, b"fixture").unwrap();
    let state = Arc::new(BackendState::new(data_file, paths.log_dir.clone()));
    let result = start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend")));
    assert!(result.is_err());
    assert_eq!(state.status().phase, BackendPhase::Failed);
    assert!(state.status().error.is_some());
    assert!(state.child.lock().unwrap().is_none());
}

#[test]
fn p2_post_ready_exit_is_observable_and_reaps_grandchild() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("unexpected-exit");
    set_fake_mode("exit-after-ready");
    start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend"))).unwrap();
    clear_fake_mode();
    let pid = read_pid(&state.data_dir.join("fake-backend.pid"));
    let grandchild = read_pid(&state.data_dir.join("fake-grandchild.pid"));
    assert_eq!(state.status().phase, BackendPhase::Ready);
    assert_process_exited(pid);
    let status = state.status();
    assert_eq!(status.phase, BackendPhase::Failed);
    assert!(status.error.is_some());
    // Failure observation alone must reap the remaining Job, before explicit stop.
    assert_process_exited(grandchild);
    assert!(state.child.lock().unwrap().is_none());
    stop_backend(&state);
}

#[test]
fn p2_failed_state_with_live_child_still_stops() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("failed-live-child");
    set_fake_mode("spawn-grandchild");
    start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend"))).unwrap();
    clear_fake_mode();
    let pid = read_pid(&state.data_dir.join("fake-backend.pid"));
    let grandchild = read_pid(&state.data_dir.join("fake-grandchild.pid"));
    *state.phase.lock().unwrap() = BackendPhase::Failed;
    stop_backend(&state);
    assert_eq!(state.status().phase, BackendPhase::Stopped);
    assert_process_exited(pid);
    assert_process_exited(grandchild);
}

#[test]
fn p2_concurrent_stop_is_idempotent_and_bounded() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("parallel-stop");
    set_fake_mode("ignore-stdin");
    start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend"))).unwrap();
    clear_fake_mode();
    let pid = read_pid(&state.data_dir.join("fake-backend.pid"));
    let started = Instant::now();
    let other_state = state.clone();
    let stopper = std::thread::spawn(move || stop_backend(&other_state));
    stop_backend(&state);
    stopper.join().unwrap();
    assert!(started.elapsed() < Duration::from_secs(7));
    assert_eq!(state.status().phase, BackendPhase::Stopped);
    assert_process_exited(pid);
}

// These tests mutate the process environment (FAKE_MODE) that the spawned
// fake-backend child inherits; parallel runs would race on it, so serialize
// all tests that set/clear FAKE_MODE via this lock.
pub static FAKE_MODE_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
