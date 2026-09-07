//! Backend process lifecycle: spawn frozen (or dev python) backend, stdout
//! handshake (`chaoxing-ready` v1), token-authenticated health polling,
//! graceful stop via stdin EOF with Job Object kill fallback.
//!
//! Invariants proven in P0 PoC:
//! - the Job handle must live in app state for the whole app lifetime
//!   (dropping it kills the backend immediately via KILL_ON_JOB_CLOSE);
//! - the backend exits instantly if stdin has no pipe — host must hold one;
//! - the backend writes runtime files into its cwd, so cwd must be the data dir.

use crate::api_proxy::{ApiOperation, ProxyError, ProxyResponse};
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
const MAX_HANDSHAKE_LINE: usize = 8192;

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
    #[serde(skip_serializing_if = "Option::is_none")]
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
    /// Job handle kept alive for the whole app lifetime (P0 PoC finding).
    pub job: Mutex<Option<Job>>,
    pub error: Mutex<Option<String>>,
    /// request_id -> cancel flag registry for api_proxy.
    pub cancels: Mutex<HashMap<u64, Arc<AtomicBool>>>,
    pub data_dir: std::path::PathBuf,
    pub log_dir: std::path::PathBuf,
    next_request_id: AtomicU64,
    stop_lock: Mutex<()>,
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
            cancels: Mutex::new(HashMap::new()),
            next_request_id: AtomicU64::new(1),
            data_dir,
            log_dir,
            stop_lock: Mutex::new(()),
        }
    }

    pub fn status(&self) -> BackendStatus {
        BackendStatus {
            phase: *self.phase.lock().unwrap_or_else(|e| e.into_inner()),
            port: *self.port.lock().unwrap_or_else(|e| e.into_inner()),
            error: self.error.lock().unwrap_or_else(|e| e.into_inner()).clone(),
        }
    }

    pub fn next_request_id(&self) -> u64 {
        self.next_request_id.fetch_add(1, Ordering::Relaxed)
    }

    fn fail(&self, msg: String) {
        *self.error.lock().unwrap_or_else(|e| e.into_inner()) = Some(msg.clone());
        *self.phase.lock().unwrap_or_else(|e| e.into_inner()) = BackendPhase::Failed;
        host_log(&self.log_dir, &format!("[backend] FAILED: {msg}"));
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

/// Spawn the backend, do handshake + health, update state. Runs synchronously
/// in setup (single-instance lock already held — no second backend possible).
pub fn start_backend(state: &Arc<BackendState>, launch: BackendLaunch) -> Result<(), String> {
    std::fs::create_dir_all(&state.data_dir).map_err(|e| format!("create data dir: {e}"))?;
    std::fs::create_dir_all(&state.log_dir).map_err(|e| format!("create log dir: {e}"))?;

    let token = random_hex(32);
    let instance_id = random_hex(8);
    *state.token.lock().unwrap_or_else(|e| e.into_inner()) = token.clone();
    *state.instance_id.lock().unwrap_or_else(|e| e.into_inner()) = instance_id.clone();

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
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            let msg = format!("启动后端失败: {e}");
            state.fail(msg.clone());
            return Err(msg);
        }
    };

    if let Err(e) = job.assign(child.id()) {
        let _ = child.kill();
        state.fail(format!("后端进程加入 Job 失败: {e}"));
        return Err(e);
    }

    let stdout = child.stdout.take().expect("stdout piped");
    let stderr = child.stderr.take().expect("stderr piped");
    let log_dir = state.log_dir.clone();
    let (handshake_rx, _stdout_thread) =
        read_handshake(stdout, instance_id.clone(), log_dir.clone());
    let _stderr_thread = drain_stderr(stderr, log_dir.clone());

    let ready = match handshake_rx.recv_timeout(deadline.saturating_duration_since(Instant::now()))
    {
        Ok(Handshake::Ready(r)) => Some(r),
        Ok(Handshake::Eof) => None,
        Err(_) => None,
    };
    let ready = match ready {
        Some(r) => r,
        None => {
            // Distinguish EOF (crash) from timeout for the error message.
            let msg = match child.try_wait() {
                Ok(Some(status)) => format!("后端在就绪握手前退出: {status}"),
                _ => "就绪握手超时（120s）".to_string(),
            };
            terminate_and_collect(&job, &mut child);
            state.fail(msg.clone());
            return Err(msg);
        }
    };

    let port = ready.port;
    *state.port.lock().unwrap_or_else(|e| e.into_inner()) = Some(port);

    // Health probe with token + instanceId double check.
    if let Err(msg) = health_until_ready(port, &token, &instance_id, deadline, &mut child) {
        terminate_and_collect(&job, &mut child);
        state.fail(msg.clone());
        return Err(msg);
    }

    *state.child.lock().unwrap_or_else(|e| e.into_inner()) = Some(child);
    *state.job.lock().unwrap_or_else(|e| e.into_inner()) = Some(job);
    *state.phase.lock().unwrap_or_else(|e| e.into_inner()) = BackendPhase::Ready;
    host_log(&log_dir, &format!("[backend] ready on port {port}"));
    Ok(())
}

fn health_until_ready(
    port: u16,
    token: &str,
    instance_id: &str,
    deadline: Instant,
    child: &mut Child,
) -> Result<(), String> {
    let agent = ureq::AgentBuilder::new()
        .timeout(HEALTH_TIMEOUT)
        .redirects(0)
        .build();
    let url = format!("http://127.0.0.1:{port}/api/health");
    loop {
        match agent.get(&url).set("X-Auth-Token", token).call() {
            Ok(resp) => {
                if resp.status() == 200 {
                    let body = resp.into_string().unwrap_or_default();
                    let ok = serde_json::from_str::<serde_json::Value>(&body)
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
        if child.try_wait().map(|s| s.is_some()).unwrap_or(false) {
            return Err("后端进程在 health 探测期间退出".into());
        }
        if Instant::now() >= deadline {
            return Err("health 探测超时（120s）".into());
        }
        std::thread::sleep(HEALTH_INTERVAL);
    }
}

fn terminate_and_collect(job: &Job, child: &mut Child) {
    let _ = child.kill();
    let _ = child.wait();
    job.terminate();
}

/// Idempotent stop: stdin EOF → up to 5s grace → TerminateJobObject.
pub fn stop_backend(state: &Arc<BackendState>) {
    let _guard = state.stop_lock.lock().unwrap_or_else(|e| e.into_inner());
    let mut phase = state.phase.lock().unwrap_or_else(|e| e.into_inner());
    if matches!(
        *phase,
        BackendPhase::Stopping | BackendPhase::Stopped | BackendPhase::Failed
    ) {
        return;
    }
    *phase = BackendPhase::Stopping;
    drop(phase);

    // Cancel all in-flight requests.
    {
        let mut cancels = state.cancels.lock().unwrap_or_else(|e| e.into_inner());
        for flag in cancels.values() {
            flag.store(true, Ordering::Relaxed);
        }
        cancels.clear();
    }

    let mut child_opt = state.child.lock().unwrap_or_else(|e| e.into_inner());
    if let Some(ref mut child) = *child_opt {
        child.stdin.take(); // drop = EOF → backend watchdog os._exit(0)
        let deadline = Instant::now() + STOP_GRACE;
        loop {
            if child.try_wait().map(|s| s.is_some()).unwrap_or(false) {
                break;
            }
            if Instant::now() >= deadline {
                break;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        if child.try_wait().map(|s| s.is_none()).unwrap_or(false) {
            if let Some(job) = state.job.lock().unwrap_or_else(|e| e.into_inner()).as_ref() {
                job.terminate();
            }
        }
        let _ = child.wait();
    }
    *child_opt = None;
    *state.phase.lock().unwrap_or_else(|e| e.into_inner()) = BackendPhase::Stopped;
    host_log(&state.log_dir, "[backend] stopped");
    // Job handle stays in state.job until app exit; KILL_ON_JOB_CLOSE covers
    // the case where we never terminated explicitly (incl. panic unwind).
}

/// Non-Ready guard for business requests.
pub fn ensure_ready(state: &BackendState) -> Result<(), ProxyError> {
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
/// Cancellation: flag checked before send; an in-flight ureq call can only be
/// abandoned (bounded ≤ timeout), the result is dropped. Recorded trade-off.
pub fn api_request(
    state: &Arc<BackendState>,
    op: ApiOperation,
    task_id: Option<String>,
    after: Option<u64>,
    payload: serde_json::Value,
    request_id: u64,
) -> Result<ProxyResponse, ProxyError> {
    ensure_ready(state)?;

    let port = state.port.lock().unwrap_or_else(|e| e.into_inner()).ok_or(
        ProxyError::BackendNotReady {
            phase: "Starting".into(),
        },
    )?;

    let path = crate::api_proxy::build_path(op, task_id.as_deref(), after)
        .map_err(|reason| ProxyError::InvalidRequest { reason })?;

    // Register cancel flag; if already cancelled, refuse.
    let flag = Arc::new(AtomicBool::new(false));
    {
        let mut cancels = state.cancels.lock().unwrap_or_else(|e| e.into_inner());
        if cancels.contains_key(&request_id) {
            return Err(ProxyError::Cancelled);
        }
        cancels.insert(request_id, flag.clone());
    }

    let result = (|| {
        let method = op.route().0;
        let url = format!("http://127.0.0.1:{port}{path}");
        let agent = ureq::AgentBuilder::new()
            .timeout(Duration::from_secs(30))
            .redirects(0)
            .build();
        let mut req = agent.request(method, &url);
        let token = state
            .token
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone();
        req = req.set("X-Auth-Token", &token);
        let body_bytes: Option<Vec<u8>> = if op.is_post() {
            let body = serde_json::to_vec(&payload).map_err(|e| ProxyError::InvalidRequest {
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
        let resp = match body_bytes {
            Some(b) => req.set("Content-Type", "application/json").send_bytes(&b),
            None => req.call(),
        };
        // Cancelled after completion: report cancelled, drop the response.
        if flag.load(Ordering::Relaxed) {
            return Err(ProxyError::Cancelled);
        }
        match resp {
            Ok(resp) => {
                let status = resp.status();
                let reader = resp.into_reader();
                let mut body = Vec::new();
                let mut limited = reader.take(crate::api_proxy::MAX_RESPONSE_BODY as u64 + 1);
                std::io::Read::read_to_end(&mut limited, &mut body).map_err(|e| {
                    ProxyError::Network {
                        reason: format!("读取响应失败: {e}"),
                    }
                })?;
                if body.len() > crate::api_proxy::MAX_RESPONSE_BODY {
                    return Err(ProxyError::Network {
                        reason: "响应体超过 2MB 上限".into(),
                    });
                }
                let body: serde_json::Value =
                    serde_json::from_slice(&body).unwrap_or(serde_json::Value::Null);
                Ok(ProxyResponse { status, body })
            }
            Err(ureq::Error::Status(code, resp)) => {
                let reader = resp.into_reader();
                let mut body = Vec::new();
                let mut limited = reader.take(crate::api_proxy::MAX_RESPONSE_BODY as u64 + 1);
                let _ = std::io::Read::read_to_end(&mut limited, &mut body);
                let body: serde_json::Value =
                    serde_json::from_slice(&body).unwrap_or(serde_json::Value::Null);
                Ok(ProxyResponse { status: code, body })
            }
            Err(e) => Err(ProxyError::Network {
                reason: format!("{e}"),
            }),
        }
    })();

    // Deregister; a cancel arriving after deregistration sees no entry → Cancelled.
    state
        .cancels
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .remove(&request_id);
    result
}

pub fn api_cancel(state: &Arc<BackendState>, request_id: u64) -> bool {
    match state
        .cancels
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .remove(&request_id)
    {
        Some(flag) => {
            flag.store(true, Ordering::Relaxed);
            true
        }
        None => false,
    }
}
