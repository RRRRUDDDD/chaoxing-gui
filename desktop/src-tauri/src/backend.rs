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
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
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
/// Per-line read cap on backend stdout; lines longer than this are split into
/// cap-sized chunks (with the total byte count logged in backend.log).
const MAX_HANDSHAKE_LINE: usize = 8192;
const MAX_HEALTH_BODY: usize = 64 * 1024;
/// api_request reads response bodies in chunks of this size, re-checking
/// cancellation between chunks instead of after the whole 30s read.
const API_READ_CHUNK: usize = 64 * 1024;
/// Consecutive API timeouts before watchdog_after_timeout probes /api/health.
const WATCHDOG_TIMEOUT_STREAK: u32 = 3;
const MAX_INFLIGHT_REQUESTS: usize = 64;
const MAX_RECENT_REQUESTS: usize = 1024;
const RECENT_REQUEST_TTL: Duration = Duration::from_secs(60);
/// host.log/backend.log rotate to `<name>.1` (one copy kept) past this size.
const MAX_LOG_BYTES: u64 = 5 * 1024 * 1024;

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
    host_log: LogFile,
    backend_log: Arc<LogFile>,
    next_request_id: AtomicU64,
    start_claimed: AtomicBool,
    /// Only short transitions/spawn registration; never held during HTTP or grace.
    lifecycle_lock: Mutex<()>,
    stop_lock: Mutex<()>,
    /// Shared loopback clients, one per timeout profile (health 2s, API 30s —
    /// ureq can only set the connect timeout at agent level). Building an
    /// agent per request re-created its pool on every call.
    health_agent: ureq::Agent,
    api_agent: ureq::Agent,
    /// Consecutive api_request timeouts; reset on definitive liveness evidence.
    timeout_streak: AtomicU32,
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
            let now = Instant::now();
            self.prune(now);
            // Never overwrite an existing tombstone: a late cancel must not
            // turn a completed request's "already completed" rejection into a
            // phantom Cancelled. Expired tombstones were just pruned above.
            if !self.recent.contains_key(&id) {
                self.remember(id, RecentResult::Cancelled, now);
            }
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
            host_log: LogFile::new(log_dir.join("host.log")),
            backend_log: Arc::new(LogFile::new(log_dir.join("backend.log"))),
            log_dir,
            lifecycle_lock: Mutex::new(()),
            stop_lock: Mutex::new(()),
            health_agent: loopback_agent(HEALTH_TIMEOUT),
            api_agent: loopback_agent(API_TIMEOUT),
            timeout_streak: AtomicU32::new(0),
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

    // Stopping/Stopped must never become Failed/Ready. Returns false without
    // changing anything when the current phase forbids the transition.
    fn mark_failed(&self, msg: &str) -> bool {
        let mut phase = self.phase.lock().unwrap_or_else(|e| e.into_inner());
        if !matches!(*phase, BackendPhase::Starting | BackendPhase::Ready) {
            return false;
        }
        *self.error.lock().unwrap_or_else(|e| e.into_inner()) = Some(msg.to_string());
        *phase = BackendPhase::Failed;
        true
    }

    /// Startup-thread failure before the backend process exists (migration,
    /// launch errors): there is no child, job, port or request channel to clean
    /// up, so only the phase transition and the host log are needed.
    pub fn fail_launch(&self, msg: String) {
        if self.mark_failed(&msg) {
            self.host_log(&format!("[host] failed: {msg}"));
        }
    }

    // Caller owns lifecycle_lock. Stopping/Stopped must never become Failed/Ready.
    fn fail_locked(&self, msg: String) {
        if !self.mark_failed(&msg) {
            return;
        }
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
        self.host_log(&format!("[backend] FAILED: {msg}"));
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

impl BackendState {
    pub fn host_log(&self, line: &str) {
        self.host_log.append(line);
    }
}

/// Append-only log opened once and shared by writer threads. Past the size
/// limit it is renamed to `<name>.1`, replacing the previous copy.
pub struct LogFile {
    path: PathBuf,
    max_bytes: u64,
    file: Mutex<Option<(std::fs::File, u64)>>,
}

impl LogFile {
    pub fn new(path: PathBuf) -> Self {
        Self::with_limit(path, MAX_LOG_BYTES)
    }

    fn with_limit(path: PathBuf, max_bytes: u64) -> Self {
        LogFile {
            path,
            max_bytes,
            file: Mutex::new(None),
        }
    }

    fn open(&self) -> Option<(std::fs::File, u64)> {
        if let Some(parent) = self.path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let file = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)
            .ok()?;
        let size = file.metadata().map(|m| m.len()).unwrap_or(0);
        Some((file, size))
    }

    pub fn append(&self, line: &str) {
        let ts = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let entry = format!(
            "[{ts}] {line}
"
        );
        let mut current = self.file.lock().unwrap_or_else(|e| e.into_inner());
        // Opening is retried on the next line if the directory is unavailable.
        if current.is_none() {
            *current = self.open();
        }
        if current
            .as_ref()
            .is_some_and(|(_, size)| *size >= self.max_bytes)
        {
            // Windows cannot rename a file this process still holds open.
            *current = None;
            let mut rotated = self.path.clone().into_os_string();
            rotated.push(".1");
            let _ = std::fs::rename(&self.path, PathBuf::from(rotated));
            *current = self.open();
        }
        if let Some((file, size)) = current.as_mut() {
            if file.write_all(entry.as_bytes()).is_ok() {
                *size += entry.len() as u64;
            }
        }
    }
}

fn random_hex(bytes: usize) -> Result<String, getrandom::Error> {
    let mut buf = vec![0u8; bytes];
    getrandom::getrandom(&mut buf)?;
    Ok(buf.iter().map(|b| format!("{b:02x}")).collect())
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

/// Read up to `max + 1` bytes from `reader` up to and including the next newline.
/// This bounds a backend stuck printing without newlines from ballooning host
/// memory. Returns `None` at EOF (or on a read error). The line terminator is
/// stripped. If more than `max` bytes were read, the physical line exceeds the
/// cap — the caller (e.g. `read_handshake`) is responsible for accumulating the
/// split chunks and logging the overrun; invalid UTF-8 degrades to replacement
/// characters instead of failing the whole handshake thread.
fn read_bounded_line<R: BufRead>(reader: &mut R, max: usize) -> Option<String> {
    let mut raw = Vec::new();
    let count = reader
        .by_ref()
        .take(max as u64 + 1)
        .read_until(b'\n', &mut raw)
        .ok()?;
    if count == 0 {
        return None;
    }
    if raw.last() == Some(&b'\n') {
        raw.pop();
        if raw.last() == Some(&b'\r') {
            raw.pop();
        }
    }
    Some(String::from_utf8_lossy(&raw).into_owned())
}

/// Read stdout lines until the ready marker (or EOF). Non-ready lines are
/// appended to backend.log. Runs on a dedicated thread until EOF so the
/// pipe never fills up.
fn read_handshake(
    stdout: std::process::ChildStdout,
    expected_instance: String,
    log: Arc<LogFile>,
) -> (
    std::sync::mpsc::Receiver<Handshake>,
    std::thread::JoinHandle<()>,
) {
    let (tx, rx) = std::sync::mpsc::channel();
    let handle = std::thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        let mut oversized_dropped = 0usize;
        let mut ready_sent = false;
        while let Some(line) = read_bounded_line(&mut reader, MAX_HANDSHAKE_LINE) {
            if line.len() > MAX_HANDSHAKE_LINE {
                // Cap hit without a newline: still inside the same physical
                // line. Drop the chunks and report the total once.
                oversized_dropped += line.len();
                continue;
            }
            if oversized_dropped > 0 {
                log.append(&format!(
                    "[oversized line dropped: {oversized_dropped} bytes]"
                ));
                oversized_dropped = 0;
            }
            if !ready_sent {
                if let Ok(r) = serde_json::from_str::<ReadyLine>(&line) {
                    if r.ready == "chaoxing-ready"
                        && r.version == 1
                        && r.instance_id == expected_instance
                    {
                        let _ = tx.send(Handshake::Ready(r));
                        ready_sent = true;
                        continue;
                    }
                    log.append(&format!("[stdout] invalid ready line: {line}"));
                    continue;
                }
            }
            log.append(&format!("[stdout] {line}"));
        }
        if oversized_dropped > 0 {
            log.append(&format!(
                "[oversized line dropped: {oversized_dropped} bytes]"
            ));
        }
        if !ready_sent {
            let _ = tx.send(Handshake::Eof);
        }
    });
    (rx, handle)
}

/// Drain stderr into backend.log on a background thread (never let the pipe fill).
/// Uses `read_bounded_line` so native crash output (often non-UTF-8, e.g. GBK
/// on Chinese Windows) degrades to replacement characters instead of killing
/// the drain thread, and runaway lines are capped per read.
fn drain_stderr_lines<R: BufRead>(mut reader: R, log: Arc<LogFile>) {
    while let Some(line) = read_bounded_line(&mut reader, MAX_HANDSHAKE_LINE) {
        log.append(&format!("[stderr] {line}"));
    }
}

fn drain_stderr(
    stderr: std::process::ChildStderr,
    log: Arc<LogFile>,
) -> std::thread::JoinHandle<()> {
    std::thread::spawn(move || {
        drain_stderr_lines(BufReader::new(stderr), log);
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
        // Dev fallback: resolved at compile time so it works from any cwd —
        // CARGO_MANIFEST_DIR (desktop/src-tauri) → ../../app.py
        let app_py = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .ancestors()
            .nth(2)
            .map(|p| p.join("app.py"))
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

    let token = random_hex(32).map_err(|e| {
        let message = format!("生成随机 token 失败: {e}");
        state.fail(message.clone());
        message
    })?;
    let instance_id = random_hex(8).map_err(|e| {
        let message = format!("生成随机 instanceId 失败: {e}");
        state.fail(message.clone());
        message
    })?;

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
    let (handshake_rx, _stdout_thread) =
        read_handshake(stdout, instance_id.clone(), state.backend_log.clone());
    let _stderr_thread = drain_stderr(stderr, state.backend_log.clone());

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
        state.host_log(&format!("[backend] ready on port {}", ready.port));
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

/// Constructor for the shared agents: loopback only, no env proxies, no
/// redirects. The overall timeout remains per-request overridable (health
/// probes cap it at the remaining start deadline).
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
        match state
            .health_agent
            .get(&url)
            .set("X-Auth-Token", token)
            .timeout(HEALTH_TIMEOUT.min(remaining))
            .call()
        {
            Ok(resp) => {
                let status = resp.status();
                if status == 401 || status == 403 {
                    // The backend answers 401 for every token/host guard miss
                    // (api/desktop_runtime.py); polling cannot fix that, so
                    // fail instead of burning the whole start deadline.
                    return Err(format!("health 探测返回 {status}：token 校验失败"));
                }
                if status == 200 {
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

/// Set phase→Stopping, cut off new requests and take ownership of the child
/// and Job. `None` means already Stopped. Shared by both stop paths.
fn claim_for_stop(state: &BackendState) -> Option<(Option<Child>, Option<Job>)> {
    let _transition = state
        .lifecycle_lock
        .lock()
        .unwrap_or_else(|e| e.into_inner());
    let mut phase = state.phase.lock().unwrap_or_else(|e| e.into_inner());
    if *phase == BackendPhase::Stopped {
        return None;
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
    Some((child, job))
}

/// Idempotent stop: stdin EOF → up to 5s grace → TerminateJobObject.
pub fn stop_backend(state: &Arc<BackendState>) {
    let _guard = state.stop_lock.lock().unwrap_or_else(|e| e.into_inner());
    let Some((mut child, job)) = claim_for_stop(state) else {
        return;
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
    state.host_log("[backend] stopped");
}

/// Stop on host exit. The main thread must not block on a stuck backend, so
/// only the instant signals run inline — stdin EOF, then an immediate
/// TerminateJobObject — and the reaping (kill + wait + phase) moves to a
/// background thread. If the process exits before that thread finishes, the
/// Job handle dropping reaps the tree via KILL_ON_JOB_CLOSE.
pub fn stop_backend_on_exit(state: &Arc<BackendState>) {
    let _guard = state.stop_lock.lock().unwrap_or_else(|e| e.into_inner());
    let Some((mut child, job)) = claim_for_stop(state) else {
        return;
    };
    if let Some(child) = child.as_mut() {
        child.stdin.take(); // drop = EOF → backend watchdog os._exit(0)
    }
    if let Some(job) = job.as_ref() {
        job.terminate();
    }
    let exit_state = state.clone();
    std::thread::spawn(move || {
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
            let _transition = exit_state
                .lifecycle_lock
                .lock()
                .unwrap_or_else(|e| e.into_inner());
            *exit_state.phase.lock().unwrap_or_else(|e| e.into_inner()) = BackendPhase::Stopped;
        }
        exit_state.host_log("[backend] stopped (exit)");
    });
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
/// The response body is read in bounded chunks with cancellation re-checked
/// between them, so a cancelled worker frees its HTTP socket almost
/// immediately instead of holding it for the whole 30s timeout. Keep its ID
/// guarded until the entire response body settles, then drop the result.
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
        let mut req = state.api_agent.request(method, &url);
        let token = state
            .token
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone();
        req = req.set("X-Auth-Token", &token);
        // The single payload serialization + size cap for the whole request.
        let body_bytes = request.encoded_body()?;
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
        let mut read = Ok(());
        {
            let mut reader = resp
                .into_reader()
                .take(crate::api_proxy::MAX_RESPONSE_BODY as u64 + 1);
            // Chunked read with a cancellation check between chunks: without
            // it a cancel would have to wait out the 30s body timeout.
            loop {
                if flag.load(Ordering::Acquire) {
                    break;
                }
                match reader
                    .by_ref()
                    .take(API_READ_CHUNK as u64)
                    .read_to_end(&mut body)
                {
                    Ok(0) => break,
                    Ok(_) => {}
                    Err(error) => {
                        read = Err(error);
                        break;
                    }
                }
            }
        }
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

    match &result {
        Err(ProxyError::Timeout { .. }) => watchdog_after_timeout(state, port),
        // Only definitive liveness evidence resets the streak; a cancelled
        // request says nothing about backend health.
        Err(ProxyError::Cancelled) => {}
        _ => state.timeout_streak.store(0, Ordering::Relaxed),
    }

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

/// Hung-backend watchdog: after WATCHDOG_TIMEOUT_STREAK consecutive API
/// timeouts, probe /api/health on the short health timeout. If even the cheap
/// probe fails, mark the backend Failed instead of leaving the user with
/// endless 30s timeouts; a healthy probe resets the streak (alive, just slow).
fn watchdog_after_timeout(state: &BackendState, port: u16) {
    let streak = state.timeout_streak.fetch_add(1, Ordering::Relaxed) + 1;
    if streak < WATCHDOG_TIMEOUT_STREAK {
        return;
    }
    let token = state
        .token
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .clone();
    let url = format!("http://127.0.0.1:{port}/api/health");
    match state
        .health_agent
        .get(&url)
        .set("X-Auth-Token", &token)
        .call()
    {
        Ok(resp) if resp.status() == 200 => {
            state.timeout_streak.store(0, Ordering::Relaxed);
        }
        _ => state.fail(format!("后端连续 {streak} 次请求超时且健康检查无响应")),
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
    fn cancel_does_not_overwrite_a_completed_tombstone() {
        let mut requests = RequestRegistry::default();
        let flag = requests.register(7).unwrap();
        requests.finish(7, &flag);
        assert!(matches!(
            requests.register(7),
            Err(ProxyError::InvalidRequest { .. })
        ));
        // A late cancel must not turn the completed tombstone into Cancelled.
        assert!(!requests.cancel(7));
        assert!(matches!(
            requests.register(7),
            Err(ProxyError::InvalidRequest { .. })
        ));
        // An expired tombstone must not block a fresh early cancellation.
        requests.recent.insert(
            8,
            (Instant::now() - RECENT_REQUEST_TTL, RecentResult::Completed),
        );
        assert!(!requests.cancel(8));
        assert!(matches!(requests.register(8), Err(ProxyError::Cancelled)));
    }

    #[test]
    fn bounded_line_reader_caps_runaway_lines() {
        let mut input = b"short\n".to_vec();
        input.extend(std::iter::repeat_n(b'x', MAX_HANDSHAKE_LINE * 2 + 10));
        input.push(b'\n');
        input.extend_from_slice(b"after\r\n");
        let mut reader = std::io::Cursor::new(input);
        let mut lines = Vec::new();
        while let Some(line) = read_bounded_line(&mut reader, MAX_HANDSHAKE_LINE) {
            lines.push(line);
        }
        assert_eq!(lines.first().map(String::as_str), Some("short"));
        assert_eq!(lines.last().map(String::as_str), Some("after"));
        // The oversized physical line comes back in cap-sized chunks whose
        // reassembly is the original content.
        let middle = &lines[1..lines.len() - 1];
        assert!(middle.len() >= 2, "runaway line must be split: {middle:?}");
        assert_eq!(middle.concat(), "x".repeat(MAX_HANDSHAKE_LINE * 2 + 10));
    }

    #[test]
    fn stderr_drain_survives_invalid_utf8_and_runaway_lines() {
        let dir = std::env::temp_dir().join(format!("chaoxing-stderr-{}", random_hex(8).unwrap()));
        let log = Arc::new(LogFile::with_limit(dir.join("backend.log"), 1024 * 1024));
        let mut input = b"before\n".to_vec();
        input.push(0xFF); // invalid UTF-8, e.g. GBK crash output
        input.push(b'\n');
        input.extend(std::iter::repeat_n(b'y', MAX_HANDSHAKE_LINE + 100));
        input.extend_from_slice(b"\nafter\n");
        drain_stderr_lines(std::io::Cursor::new(input), log);
        let content = std::fs::read_to_string(dir.join("backend.log")).unwrap();
        assert!(content.contains("[stderr] before"));
        assert!(
            content.contains('\u{FFFD}'),
            "invalid UTF-8 must degrade to a replacement character: {content:?}"
        );
        assert!(
            content.contains("[stderr] after"),
            "drain must keep reading to EOF after bad bytes and a runaway line: {content:?}"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn log_file_rotates_once_past_the_limit_and_keeps_one_copy() {
        let dir = std::env::temp_dir().join(format!("chaoxing-log-{}", random_hex(8).unwrap()));
        let path = dir.join("nested").join("backend.log");
        let log = LogFile::with_limit(path.clone(), 64);
        log.append("first line that fills most of the limit ........");
        log.append("second line crosses the limit");
        let rotated = dir.join("nested").join("backend.log.1");
        assert!(!rotated.exists(), "rotated before the limit was reached");
        log.append("third line starts a new file");
        let old = std::fs::read_to_string(&rotated).unwrap();
        assert!(old.contains("first line") && old.contains("second line"));
        assert!(std::fs::read_to_string(&path)
            .unwrap()
            .contains("third line"));
        for index in 0..8 {
            log.append(&format!(
                "filler {index} ....................................."
            ));
        }
        let current = std::fs::read_to_string(&path).unwrap();
        let previous = std::fs::read_to_string(&rotated).unwrap();
        assert!(
            !previous.contains("first line"),
            "more than one old copy kept"
        );
        assert!(current.len() as u64 <= 64 + 80 && previous.len() as u64 <= 64 + 80);
        drop(log);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn timeout_io_error_is_distinct_from_network_error() {
        let timeout = std::io::Error::new(std::io::ErrorKind::TimedOut, "fixture timeout");
        assert!(matches!(http_error(&timeout), ProxyError::Timeout { .. }));
        let network = std::io::Error::new(std::io::ErrorKind::ConnectionReset, "fixture reset");
        assert!(matches!(http_error(&network), ProxyError::Network { .. }));
    }
}
