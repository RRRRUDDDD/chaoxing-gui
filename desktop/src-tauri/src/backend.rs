//! Backend process lifecycle: spawn frozen (or dev python) backend, stdout
//! handshake (`chaoxing-ready` v1), token-authenticated health polling,
//! graceful stop via stdin EOF with Job Object kill fallback.
//!
//! Invariants proven in P0 PoC:
//! - the Job handle must live in app state for the whole app lifetime
//!   (dropping it kills the backend immediately via KILL_ON_JOB_CLOSE);
//! - the backend exits instantly if stdin has no pipe — host must hold one;
//! - the backend writes runtime files into its cwd, so cwd must be the data dir.

use crate::api_proxy::{ApiOperation, ApiRequest, ProxyError, ProxyResponse};
use crate::windows_job::Job;
use serde::Serialize;
use std::collections::HashMap;
use std::io::Read;
use std::io::{BufRead, BufReader, Write};
use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

const CREATE_NO_WINDOW: u32 = 0x0800_0000;
/// Whole start window (handshake + health) per plan.md §4.1.
const START_DEADLINE: Duration = Duration::from_secs(120);
const HEALTH_INTERVAL: Duration = Duration::from_millis(300);
const HEALTH_TIMEOUT: Duration = Duration::from_millis(2000);
/// Grace period after stdin EOF before TerminateJobObject.
const STOP_GRACE: Duration = Duration::from_secs(5);
const POLL_INTERVAL: Duration = Duration::from_millis(25);
const API_TIMEOUT: Duration = Duration::from_secs(30);
const MAX_HANDSHAKE_LINE: usize = 8192;
const MAX_HEALTH_BODY: usize = 64 * 1024;
const MAX_INFLIGHT_REQUESTS: usize = 64;
const MAX_RECENT_REQUESTS: usize = 1024;
const RECENT_REQUEST_TTL: Duration = Duration::from_secs(60);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum BackendPhase {
    Starting,
    Ready,
    Stopping,
    Stopped,
    Failed,
}

#[derive(Debug, Serialize)]
pub struct BackendStatus {
    pub phase: BackendPhase,
    #[serde(skip)]
    pub port: Option<u16>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

pub struct BackendState {
    pub phase: Mutex<BackendPhase>,
    pub child: Mutex<Option<Child>>,
    pub port: Mutex<Option<u16>>,
    pub token: Mutex<String>,
    pub instance_id: Mutex<String>,
    /// Kept alive through startup/Ready, then owned by teardown until the tree is killed.
    pub job: Mutex<Option<Job>>,
    pub error: Mutex<Option<String>>,
    requests: Mutex<RequestRegistry>,
    pub data_dir: std::path::PathBuf,
    pub log_dir: std::path::PathBuf,
    next_request_id: AtomicU64,
    start_claimed: AtomicBool,
    /// Only short transitions/spawn registration; never held during HTTP or grace.
    lifecycle_lock: Mutex<()>,
    stop_lock: Mutex<()>,
}

#[derive(Clone, Copy)]
enum RecentResult {
    Cancelled,
    Completed,
}

#[derive(Default)]
struct RequestRegistry {
    active: HashMap<u64, Arc<AtomicBool>>,
    recent: HashMap<u64, (Instant, RecentResult)>,
    closed: bool,
}

impl RequestRegistry {
    fn prune(&mut self, now: Instant) {
        self.recent
            .retain(|_, (time, _)| now.duration_since(*time) < RECENT_REQUEST_TTL);
    }

    fn remember(&mut self, id: u64, outcome: RecentResult, now: Instant) {
        self.prune(now);
        if !self.recent.contains_key(&id) && self.recent.len() >= MAX_RECENT_REQUESTS {
            if let Some(oldest) = self
                .recent
                .iter()
                .min_by_key(|(_, (time, _))| *time)
                .map(|(id, _)| *id)
            {
                self.recent.remove(&oldest);
            }
        }
        self.recent.insert(id, (now, outcome));
    }

    fn register(&mut self, id: u64) -> Result<Arc<AtomicBool>, ProxyError> {
        self.prune(Instant::now());
        if self.closed {
            return Err(ProxyError::BackendNotReady {
                phase: "stopped".into(),
            });
        }
        if let Some(flag) = self.active.get(&id) {
            return if flag.load(Ordering::Acquire) {
                Err(ProxyError::Cancelled)
            } else {
                Err(ProxyError::InvalidRequest {
                    reason: "requestId is already in use".into(),
                })
            };
        }
        if let Some((_, outcome)) = self.recent.get(&id) {
            return match outcome {
                RecentResult::Cancelled => Err(ProxyError::Cancelled),
                RecentResult::Completed => Err(ProxyError::InvalidRequest {
                    reason: "requestId was already completed".into(),
                }),
            };
        }
        if self.active.len() >= MAX_INFLIGHT_REQUESTS {
            return Err(ProxyError::InvalidRequest {
                reason: "too many in-flight requests".into(),
            });
        }
        let flag = Arc::new(AtomicBool::new(false));
        self.active.insert(id, flag.clone());
        Ok(flag)
    }

    fn cancel(&mut self, id: u64) -> bool {
        if let Some(flag) = self.active.get(&id) {
            flag.store(true, Ordering::Release);
            // The HTTP worker owns this entry until its entire body read settles.
            return true;
        }
        if !self.closed {
            self.remember(id, RecentResult::Cancelled, Instant::now());
        }
        false
    }

    fn finish(&mut self, id: u64, flag: &Arc<AtomicBool>) -> bool {
        let cancelled = flag.load(Ordering::Acquire);
        if self
            .active
            .get(&id)
            .is_some_and(|current| Arc::ptr_eq(current, flag))
        {
            self.active.remove(&id);
            if !self.closed {
                self.remember(
                    id,
                    if cancelled {
                        RecentResult::Cancelled
                    } else {
                        RecentResult::Completed
                    },
                    Instant::now(),
                );
            }
        }
        cancelled
    }

    fn close(&mut self) {
        self.closed = true;
        for flag in self.active.values() {
            flag.store(true, Ordering::Release);
        }
        self.recent.clear();
    }
}

impl BackendState {
    pub fn new(data_dir: std::path::PathBuf, log_dir: std::path::PathBuf) -> Self {
        BackendState {
            phase: Mutex::new(BackendPhase::Starting),
            child: Mutex::new(None),
            port: Mutex::new(None),
            token: Mutex::new(String::new()),
            instance_id: Mutex::new(String::new()),
            job: Mutex::new(None),
            error: Mutex::new(None),
            requests: Mutex::new(RequestRegistry::default()),
            next_request_id: AtomicU64::new(1),
            start_claimed: AtomicBool::new(false),
            data_dir,
            log_dir,
            lifecycle_lock: Mutex::new(()),
            stop_lock: Mutex::new(()),
        }
    }

    pub fn status(&self) -> BackendStatus {
        self.observe_exit();
        BackendStatus {
            phase: *self.phase.lock().unwrap_or_else(|e| e.into_inner()),
            port: *self.port.lock().unwrap_or_else(|e| e.into_inner()),
            error: self.error.lock().unwrap_or_else(|e| e.into_inner()).clone(),
        }
    }

    pub fn next_request_id(&self) -> u64 {
        self.next_request_id
            .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |id| {
                Some(if id >= crate::api_proxy::MAX_REQUEST_ID {
                    1
                } else {
                    id + 1
                })
            })
            .unwrap_or_else(|id| id)
    }

    fn fail(&self, msg: String) {
        let _transition = self
            .lifecycle_lock
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        self.fail_locked(msg);
    }

    // Caller owns lifecycle_lock. Stopping/Stopped must never become Failed/Ready.
    fn fail_locked(&self, msg: String) {
        let mut phase = self.phase.lock().unwrap_or_else(|e| e.into_inner());
        if !matches!(*phase, BackendPhase::Starting | BackendPhase::Ready) {
            return;
        }
        *self.error.lock().unwrap_or_else(|e| e.into_inner()) = Some(msg.clone());
        *phase = BackendPhase::Failed;
        drop(phase);
        self.requests
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .close();
        *self.port.lock().unwrap_or_else(|e| e.into_inner()) = None;
        // Terminate even when the direct child has exited: its grandchildren may live.
        if let Some(job) = self.job.lock().unwrap_or_else(|e| e.into_inner()).take() {
            job.terminate();
        }
        if let Some(mut child) = self.child.lock().unwrap_or_else(|e| e.into_inner()).take() {
            let _ = child.kill();
            let _ = child.try_wait();
        }
        host_log(&self.log_dir, &format!("[backend] FAILED: {msg}"));
    }

    fn observe_exit(&self) {
        // A status query stays responsive while spawn/stop is changing ownership.
        let Ok(_transition) = self.lifecycle_lock.try_lock() else {
            return;
        };
        if *self.phase.lock().unwrap_or_else(|e| e.into_inner()) != BackendPhase::Ready {
            return;
        }
        let error = self
            .child
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .as_mut()
            .and_then(|child| match child.try_wait() {
                Ok(Some(status)) => Some(format!("后端进程意外退出: {status}")),
                Ok(None) => None,
                Err(error) => Some(format!("无法检查后端进程: {error}")),
            });
        if let Some(error) = error {
            self.fail_locked(error);
        }
    }
}

pub fn host_log(log_dir: &std::path::Path, line: &str) {
    let _ = std::fs::create_dir_all(log_dir);
    let path = log_dir.join("host.log");
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
    {
        let ts = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let _ = writeln!(f, "[{ts}] {line}");
    }
}

fn random_hex(bytes: usize) -> String {
    let mut buf = vec![0u8; bytes];
    getrandom::getrandom(&mut buf).expect("OS RNG failed");
    buf.iter().map(|b| format!("{b:02x}")).collect()
}

/// Handshake line shape: {"ready":"chaoxing-ready","version":1,"port":N,"instanceId":"..."}
#[derive(serde::Deserialize)]
struct ReadyLine {
    ready: String,
    version: u32,
    port: u16,
    #[serde(rename = "instanceId")]
    instance_id: String,
}

enum Handshake {
    Ready(ReadyLine),
    /// Backend exited before ready.
    Eof,
}

/// Read stdout lines until the ready marker (or EOF). Non-ready lines are
/// appended to backend.log. Runs on a dedicated thread until EOF so the
/// pipe never fills up.
fn read_handshake(
    stdout: std::process::ChildStdout,
    expected_instance: String,
    log_dir: PathBuf,
) -> (
    std::sync::mpsc::Receiver<Handshake>,
    std::thread::JoinHandle<()>,
) {
    let (tx, rx) = std::sync::mpsc::channel();
    let handle = std::thread::spawn(move || {
        let reader = BufReader::new(stdout);
        let mut lines = reader.lines();
        while let Some(line) = lines.next() {
            let line = match line {
                Ok(l) => l,
                Err(_) => break,
            };
            if line.len() > MAX_HANDSHAKE_LINE {
                append_backend_log(
                    &log_dir,
                    &format!("[oversized line dropped: {} bytes]", line.len()),
                );
                continue;
            }
            if let Ok(r) = serde_json::from_str::<ReadyLine>(&line) {
                if r.ready == "chaoxing-ready"
                    && r.version == 1
                    && r.instance_id == expected_instance
                {
                    let _ = tx.send(Handshake::Ready(r));
                    // Keep draining stdout to EOF so the pipe doesn't fill.
                    for rest in lines.by_ref().flatten() {
                        append_backend_log(&log_dir, &format!("[stdout] {rest}"));
                    }
                    return;
                }
                append_backend_log(&log_dir, &format!("[stdout] invalid ready line: {line}"));
                continue;
            }
            append_backend_log(&log_dir, &format!("[stdout] {line}"));
        }
        let _ = tx.send(Handshake::Eof);
    });
    (rx, handle)
}

fn append_backend_log(log_dir: &std::path::Path, line: &str) {
    let _ = std::fs::create_dir_all(log_dir);
    let path = log_dir.join("backend.log");
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
    {
        let ts = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let _ = writeln!(f, "[{ts}] {line}");
    }
}

/// Drain stderr into backend.log on a background thread (never let the pipe fill).
fn drain_stderr(
    stderr: std::process::ChildStderr,
    log_dir: PathBuf,
) -> std::thread::JoinHandle<()> {
    std::thread::spawn(move || {
        let reader = BufReader::new(stderr);
        for line in reader.lines().map_while(Result::ok) {
            append_backend_log(&log_dir, &format!("[stderr] {line}"));
        }
    })
}

pub enum BackendLaunch {
    /// Frozen onedir backend shipped via Tauri resources.
    Frozen(std::path::PathBuf),
    /// Development: run the repo Flask app with system python.
    Dev {
        python: String,
        app_py: std::path::PathBuf,
    },
}

/// Build the backend launch plan from the running app. Production resolves the
/// frozen onedir backend from Tauri resources; debug builds fall back to the
/// repo's Flask app via system python when the resource exe is absent.
pub fn detect_launch(app: &tauri::AppHandle) -> Result<BackendLaunch, String> {
    use tauri::Manager;
    let resource = app
        .path()
        .resolve(
            "backend/chaoxing-backend.exe",
            tauri::path::BaseDirectory::Resource,
        )
        .map_err(|e| format!("resolve resource dir: {e}"))?;
    if resource.is_file() {
        Ok(BackendLaunch::Frozen(resource))
    } else if cfg!(debug_assertions) {
        // Dev fallback: repo layout — desktop/src-tauri → ../../app.py
        let app_py = std::env::current_dir()
            .ok()
            .and_then(|d| d.ancestors().nth(2).map(|p| p.join("app.py")))
            .ok_or("cannot locate repo app.py")?;
        if !app_py.is_file() {
            return Err(format!(
                "后端程序缺失: {}（且开发回退 {} 也不存在）",
                resource.display(),
                app_py.display()
            ));
        }
        Ok(BackendLaunch::Dev {
            python: "python".into(),
            app_py,
        })
    } else {
        Err(format!("后端程序缺失: {}", resource.display()))
    }
}

/// Runs on the host's background startup worker after state registration.
/// Spawn/ownership transfer is serialized with stop; handshake/HTTP never hold it.
pub fn start_backend(state: &Arc<BackendState>, launch: BackendLaunch) -> Result<(), String> {
    if state.start_claimed.swap(true, Ordering::AcqRel) {
        return Err("后端启动已请求，不能自动重启".into());
    }
    check_starting(state)?;
    for (label, directory) in [("data", &state.data_dir), ("log", &state.log_dir)] {
        if let Err(error) = std::fs::create_dir_all(directory) {
            let message = format!("create {label} dir: {error}");
            state.fail(message.clone());
            return Err(message);
        }
    }

    let token = random_hex(32);
    let instance_id = random_hex(8);

    let job = Job::create().map_err(|e| {
        state.fail(format!("创建 Job Object 失败: {e}"));
        e
    })?;

    let deadline = Instant::now() + START_DEADLINE;
    let mut cmd = match &launch {
        BackendLaunch::Frozen(exe) => {
            let mut c = Command::new(exe);
            c.env("CHAOXING_TAURI", "1");
            c
        }
        BackendLaunch::Dev { python, app_py } => {
            let mut c = Command::new(python);
            c.arg("-u").arg(app_py);
            c.env("CHAOXING_TAURI", "1");
            c
        }
    };
    cmd.current_dir(&state.data_dir)
        .env("CHAOXING_HEADLESS", "1")
        .env("CHAOXING_TAURI_TOKEN", &token)
        .env("CHAOXING_TAURI_INSTANCE_ID", &instance_id)
        .env("CHAOXING_DATA_DIR", &state.data_dir)
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .creation_flags(CREATE_NO_WINDOW);

    // Fail fast with a clear message when the executable is missing (Windows
    // spawn errors are opaque); the launch resolver already checks the frozen
    // path in production, tests exercise this branch directly.
    if let BackendLaunch::Frozen(exe) = &launch {
        if !exe.is_file() {
            let msg = format!("后端程序缺失: {}", exe.display());
            state.fail(msg.clone());
            return Err(msg);
        }
    }
    let (stdout, stderr) = {
        let _transition = state
            .lifecycle_lock
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        check_starting(state)?;
        let mut child = match cmd.spawn() {
            Ok(child) => child,
            Err(error) => {
                let message = format!("启动后端失败: {error}");
                state.fail_locked(message.clone());
                return Err(message);
            }
        };
        let assigned = job.assign(child.id());
        let stdout = child.stdout.take().expect("stdout piped");
        let stderr = child.stderr.take().expect("stderr piped");
        // From this point every failure/stop path can reach both process handles.
        *state.child.lock().unwrap_or_else(|e| e.into_inner()) = Some(child);
        *state.job.lock().unwrap_or_else(|e| e.into_inner()) = Some(job);
        *state.token.lock().unwrap_or_else(|e| e.into_inner()) = token.clone();
        *state.instance_id.lock().unwrap_or_else(|e| e.into_inner()) = instance_id.clone();
        if let Err(error) = assigned {
            let message = format!("后端进程加入 Job 失败: {error}");
            state.fail_locked(message.clone());
            return Err(message);
        }
        (stdout, stderr)
    };
    let log_dir = state.log_dir.clone();
    let (handshake_rx, _stdout_thread) =
        read_handshake(stdout, instance_id.clone(), log_dir.clone());
    let _stderr_thread = drain_stderr(stderr, log_dir.clone());

    let result: Result<(), String> = (|| {
        let ready = await_handshake(state, &handshake_rx, deadline)?;
        health_until_ready(state, ready.port, &token, &instance_id, deadline)?;
        let _transition = state
            .lifecycle_lock
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        check_starting(state)?;
        check_starting_child(state)?;
        *state.port.lock().unwrap_or_else(|e| e.into_inner()) = Some(ready.port);
        *state.phase.lock().unwrap_or_else(|e| e.into_inner()) = BackendPhase::Ready;
        host_log(&log_dir, &format!("[backend] ready on port {}", ready.port));
        Ok(())
    })();
    if let Err(message) = &result {
        state.fail(message.clone());
    }
    result
}

fn check_starting(state: &BackendState) -> Result<(), String> {
    if *state.phase.lock().unwrap_or_else(|e| e.into_inner()) == BackendPhase::Starting {
        Ok(())
    } else {
        Err("后端启动已取消或已结束".into())
    }
}

fn check_starting_child(state: &BackendState) -> Result<(), String> {
    let mut child = state.child.lock().unwrap_or_else(|e| e.into_inner());
    match child.as_mut().map(Child::try_wait) {
        Some(Ok(None)) => Ok(()),
        Some(Ok(Some(status))) => Err(format!("后端进程在启动期间退出: {status}")),
        Some(Err(error)) => Err(format!("无法检查后端进程: {error}")),
        None => Err("后端启动已取消".into()),
    }
}

fn await_handshake(
    state: &BackendState,
    receiver: &std::sync::mpsc::Receiver<Handshake>,
    deadline: Instant,
) -> Result<ReadyLine, String> {
    loop {
        check_starting(state)?;
        check_starting_child(state)?;
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Err("就绪握手超时（120s）".into());
        }
        match receiver.recv_timeout(remaining.min(POLL_INTERVAL)) {
            Ok(Handshake::Ready(ready)) if ready.port != 0 => return Ok(ready),
            Ok(Handshake::Ready(_)) => return Err("就绪握手 port 无效".into()),
            Ok(Handshake::Eof) | Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => {
                return Err("后端在就绪握手前退出".into());
            }
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {}
        }
    }
}

fn loopback_agent(timeout: Duration) -> ureq::Agent {
    ureq::AgentBuilder::new()
        .try_proxy_from_env(false)
        .redirects(0)
        .timeout_connect(timeout)
        .timeout(timeout)
        .build()
}

fn health_until_ready(
    state: &BackendState,
    port: u16,
    token: &str,
    instance_id: &str,
    deadline: Instant,
) -> Result<(), String> {
    let url = format!("http://127.0.0.1:{port}/api/health");
    loop {
        check_starting(state)?;
        check_starting_child(state)?;
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Err("health 探测超时（120s）".into());
        }
        let agent = loopback_agent(HEALTH_TIMEOUT.min(remaining));
        match agent.get(&url).set("X-Auth-Token", token).call() {
            Ok(resp) => {
                if resp.status() == 200 {
                    let mut body = Vec::new();
                    let read = resp
                        .into_reader()
                        .take(MAX_HEALTH_BODY as u64 + 1)
                        .read_to_end(&mut body);
                    check_starting(state)?;
                    read.map_err(|error| format!("读取 health 响应失败: {error}"))?;
                    if body.len() > MAX_HEALTH_BODY {
                        return Err("health 响应过大".into());
                    }
                    let ok = serde_json::from_slice::<serde_json::Value>(&body)
                        .ok()
                        .and_then(|v| {
                            v.get("instanceId")
                                .and_then(|i| i.as_str())
                                .map(|s| s == instance_id)
                        })
                        .unwrap_or(false);
                    if ok {
                        return Ok(());
                    }
                    return Err("health 响应 instanceId 不匹配（可能端口被占用）".into());
                }
                // non-200 while starting: keep polling until deadline
            }
            Err(_) => { /* connect refused while backend boots */ }
        }
        check_starting(state)?;
        check_starting_child(state)?;
        if Instant::now() >= deadline {
            return Err("health 探测超时（120s）".into());
        }
        let next_probe = (Instant::now() + HEALTH_INTERVAL).min(deadline);
        while Instant::now() < next_probe {
            check_starting(state)?;
            std::thread::sleep(
                POLL_INTERVAL.min(next_probe.saturating_duration_since(Instant::now())),
            );
        }
    }
}

/// Idempotent stop: stdin EOF → up to 5s grace → TerminateJobObject.
pub fn stop_backend(state: &Arc<BackendState>) {
    let _guard = state.stop_lock.lock().unwrap_or_else(|e| e.into_inner());
    let (mut child, job) = {
        let _transition = state
            .lifecycle_lock
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        let mut phase = state.phase.lock().unwrap_or_else(|e| e.into_inner());
        if *phase == BackendPhase::Stopped {
            return;
        }
        *phase = BackendPhase::Stopping;
        drop(phase);
        state
            .requests
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .close();
        *state.port.lock().unwrap_or_else(|e| e.into_inner()) = None;
        let child = state.child.lock().unwrap_or_else(|e| e.into_inner()).take();
        let job = state.job.lock().unwrap_or_else(|e| e.into_inner()).take();
        (child, job)
    };
    if let Some(child) = child.as_mut() {
        child.stdin.take(); // drop = EOF → backend watchdog os._exit(0)
        let deadline = Instant::now() + STOP_GRACE;
        while matches!(child.try_wait(), Ok(None)) && Instant::now() < deadline {
            std::thread::sleep(POLL_INTERVAL);
        }
    }
    // Always reap the Job: a graceful direct-child exit may leave grandchildren.
    if let Some(job) = job.as_ref() {
        job.terminate();
    }
    if let Some(child) = child.as_mut() {
        let _ = child.kill();
        let deadline = Instant::now() + Duration::from_millis(500);
        while matches!(child.try_wait(), Ok(None)) && Instant::now() < deadline {
            std::thread::sleep(POLL_INTERVAL);
        }
    }
    drop(child);
    drop(job);
    {
        let _transition = state
            .lifecycle_lock
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        *state.phase.lock().unwrap_or_else(|e| e.into_inner()) = BackendPhase::Stopped;
    }
    host_log(&state.log_dir, "[backend] stopped");
}

/// Non-Ready guard for business requests.
pub fn ensure_ready(state: &BackendState) -> Result<(), ProxyError> {
    state.observe_exit();
    match *state.phase.lock().unwrap_or_else(|e| e.into_inner()) {
        BackendPhase::Ready => Ok(()),
        phase => Err(ProxyError::BackendNotReady {
            phase: serde_json::to_value(phase)
                .ok()
                .and_then(|v| v.as_str().map(String::from))
                .unwrap_or_default(),
        }),
    }
}

/// Forward one whitelisted operation to the backend over loopback HTTP.
/// A cancelled worker can still occupy its HTTP socket for at most 30 seconds.
/// Keep its ID guarded until the entire response body settles, then drop the result.
pub fn api_request(
    state: &Arc<BackendState>,
    op: ApiOperation,
    task_id: Option<String>,
    after: Option<u64>,
    payload: serde_json::Value,
    request_id: u64,
) -> Result<ProxyResponse, ProxyError> {
    let request = ApiRequest {
        operation: op,
        task_id,
        after,
        payload,
        request_id,
    };
    request.validate()?;
    ensure_ready(state)?;
    let path = crate::api_proxy::build_path(op, request.task_id.as_deref(), request.after)
        .map_err(|reason| ProxyError::InvalidRequest { reason })?;
    // Serialize registration with stop: it either refuses or is included in close().
    let (port, flag) = {
        let _transition = state
            .lifecycle_lock
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        ensure_ready(state)?;
        let port = state.port.lock().unwrap_or_else(|e| e.into_inner()).ok_or(
            ProxyError::BackendNotReady {
                phase: "starting".into(),
            },
        )?;
        let flag = state
            .requests
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .register(request_id)?;
        (port, flag)
    };

    let result = (|| {
        let method = op.route().0;
        let url = format!("http://127.0.0.1:{port}{path}");
        let agent = loopback_agent(API_TIMEOUT);
        let mut req = agent.request(method, &url);
        let token = state
            .token
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone();
        req = req.set("X-Auth-Token", &token);
        let body_bytes: Option<Vec<u8>> = if op.is_post() {
            let body =
                serde_json::to_vec(&request.payload).map_err(|e| ProxyError::InvalidRequest {
                    reason: format!("payload 序列化失败: {e}"),
                })?;
            if body.len() > crate::api_proxy::MAX_REQUEST_BODY {
                return Err(ProxyError::InvalidRequest {
                    reason: "请求体超过 1MB 上限".into(),
                });
            }
            Some(body)
        } else {
            None
        };
        // Also cover cancellation while validating/serializing/creating the request.
        if flag.load(Ordering::Acquire) {
            return Err(ProxyError::Cancelled);
        }
        let resp = match body_bytes {
            Some(b) => req.set("Content-Type", "application/json").send_bytes(&b),
            None => req.call(),
        };
        if flag.load(Ordering::Acquire) {
            return Err(ProxyError::Cancelled);
        }
        // HTTP errors use exactly the same bounded body read as successful responses.
        let resp = match resp {
            Ok(resp) | Err(ureq::Error::Status(_, resp)) => resp,
            Err(error) => return Err(http_error(&error)),
        };
        let status = resp.status();
        let declared_len = resp
            .header("Content-Length")
            .and_then(|value| value.parse::<u64>().ok());
        if declared_len.is_some_and(|length| length > crate::api_proxy::MAX_RESPONSE_BODY as u64) {
            return Err(ProxyError::Network {
                reason: "响应体超过 2MB 上限".into(),
            });
        }
        let mut body = Vec::new();
        let read = resp
            .into_reader()
            .take(crate::api_proxy::MAX_RESPONSE_BODY as u64 + 1)
            .read_to_end(&mut body);
        // Cancellation takes precedence over a late successful/error body or read error.
        if flag.load(Ordering::Acquire) {
            return Err(ProxyError::Cancelled);
        }
        read.map_err(|error| http_error(&error))?;
        if body.len() > crate::api_proxy::MAX_RESPONSE_BODY {
            return Err(ProxyError::Network {
                reason: "响应体超过 2MB 上限".into(),
            });
        }
        if declared_len.is_some_and(|length| length != body.len() as u64) {
            return Err(ProxyError::Network {
                reason: "响应体长度与 Content-Length 不符".into(),
            });
        }
        let body = serde_json::from_slice(&body).map_err(|error| ProxyError::Network {
            reason: format!("响应不是有效 JSON: {error}"),
        })?;
        Ok(ProxyResponse { status, body })
    })();

    // Serialize completion with cancellation, including JSON parsing time.
    if state
        .requests
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .finish(request_id, &flag)
    {
        Err(ProxyError::Cancelled)
    } else {
        result
    }
}

pub fn api_cancel(state: &Arc<BackendState>, request_id: u64) -> bool {
    if !crate::api_proxy::valid_request_id(request_id) {
        return false;
    }
    state
        .requests
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .cancel(request_id)
}

fn http_error(error: &(dyn std::error::Error + 'static)) -> ProxyError {
    let mut cause = Some(error);
    while let Some(current) = cause {
        if current.downcast_ref::<std::io::Error>().is_some_and(|io| {
            matches!(
                io.kind(),
                std::io::ErrorKind::TimedOut | std::io::ErrorKind::WouldBlock
            )
        }) {
            return ProxyError::Timeout {
                reason: "后端请求超时（30s）".into(),
            };
        }
        cause = current.source();
    }
    ProxyError::Network {
        reason: format!("后端请求失败: {error}"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn early_cancel_ledger_is_bounded_and_expires() {
        let mut requests = RequestRegistry::default();
        for id in 1..=(MAX_RECENT_REQUESTS as u64 + 20) {
            assert!(!requests.cancel(id));
            assert!(requests.recent.len() <= MAX_RECENT_REQUESTS);
        }
        requests.recent.insert(
            42,
            (Instant::now() - RECENT_REQUEST_TTL, RecentResult::Cancelled),
        );
        assert!(
            requests.register(42).is_ok(),
            "expired early cancellation must be released"
        );
    }

    #[test]
    fn inflight_capacity_and_cancellation_ownership_are_bounded() {
        let mut requests = RequestRegistry::default();
        let mut workers = Vec::new();
        for id in 1..=MAX_INFLIGHT_REQUESTS as u64 {
            workers.push((id, requests.register(id).unwrap()));
        }
        assert!(matches!(
            requests.register(9999),
            Err(ProxyError::InvalidRequest { .. })
        ));
        assert!(requests.cancel(1));
        assert_eq!(requests.active.len(), MAX_INFLIGHT_REQUESTS);
        // Evicting early-cancel/completion tombstones cannot evict a live worker.
        for id in 10_000..(10_000 + MAX_RECENT_REQUESTS as u64 + 20) {
            requests.cancel(id);
        }
        assert!(matches!(requests.register(1), Err(ProxyError::Cancelled)));
        requests.close();
        assert!(requests.recent.is_empty());
        for (id, flag) in workers {
            assert!(flag.load(Ordering::Acquire));
            assert!(requests.finish(id, &flag));
        }
        assert!(requests.active.is_empty());
        assert!(requests.recent.is_empty());
        assert!(!requests.cancel(123));
        assert!(requests.recent.is_empty());
    }

    #[test]
    fn timeout_io_error_is_distinct_from_network_error() {
        let timeout = std::io::Error::new(std::io::ErrorKind::TimedOut, "fixture timeout");
        assert!(matches!(http_error(&timeout), ProxyError::Timeout { .. }));
        let network = std::io::Error::new(std::io::ErrorKind::ConnectionReset, "fixture reset");
        assert!(matches!(http_error(&network), ProxyError::Network { .. }));
    }
}
