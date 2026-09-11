//! HTTP boundary tests use a real loopback socket with deterministic response gates.
use chaoxing_desktop_lib::api_proxy::{ApiOperation, ProxyError, MAX_RESPONSE_BODY};
use chaoxing_desktop_lib::backend::{
    api_cancel, api_request, stop_backend, BackendPhase, BackendState,
};
use serde_json::{json, Value};
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{mpsc, Arc, Condvar, Mutex};
use std::thread;
use std::time::{Duration, Instant};

#[derive(Clone, Copy)]
enum Hold {
    None,
    Headers,
    Body,
}

#[derive(Default)]
struct Gate(Mutex<bool>, Condvar);

impl Gate {
    fn release(&self) {
        *self.0.lock().unwrap() = true;
        self.1.notify_all();
    }

    fn block(&self) {
        let guard = self.0.lock().unwrap();
        let _ = self
            .1
            .wait_timeout_while(guard, Duration::from_secs(35), |released| !*released)
            .unwrap();
    }
}

#[derive(Clone)]
struct Reply {
    status: u16,
    body: Vec<u8>,
    declared_len: Option<usize>,
    extra_headers: String,
    hold: Hold,
}

impl Reply {
    fn json(status: u16, body: Value) -> Self {
        Self {
            status,
            body: serde_json::to_vec(&body).unwrap(),
            declared_len: None,
            extra_headers: String::new(),
            hold: Hold::None,
        }
    }
}

struct Server {
    state: Arc<BackendState>,
    directory: std::path::PathBuf,
    gate: Arc<Gate>,
    count: Arc<AtomicUsize>,
    received: mpsc::Receiver<Vec<u8>>,
    headers: mpsc::Receiver<()>,
    shutdown: Arc<AtomicBool>,
    worker: Option<thread::JoinHandle<()>>,
}

impl Server {
    fn new(reply: Reply) -> Self {
        static NEXT_SERVER_ID: AtomicUsize = AtomicUsize::new(0);
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let directory = std::env::temp_dir().join(format!(
            "cx-api-lifecycle-{}-{}",
            std::process::id(),
            NEXT_SERVER_ID.fetch_add(1, Ordering::Relaxed)
        ));
        let state = Arc::new(BackendState::new(
            directory.join("data"),
            directory.join("logs"),
        ));
        *state.phase.lock().unwrap() = BackendPhase::Ready;
        *state.port.lock().unwrap() = Some(listener.local_addr().unwrap().port());
        *state.token.lock().unwrap() = "fixture-token".into();
        let gate = Arc::new(Gate::default());
        let count = Arc::new(AtomicUsize::new(0));
        let shutdown = Arc::new(AtomicBool::new(false));
        let (request_tx, received) = mpsc::channel();
        let (header_tx, headers) = mpsc::channel();
        let gate_thread = gate.clone();
        let count_thread = count.clone();
        let shutdown_thread = shutdown.clone();
        let worker = thread::spawn(move || {
            let mut handlers = Vec::new();
            while !shutdown_thread.load(Ordering::Acquire) {
                match listener.accept() {
                    Ok((stream, _)) => {
                        let reply = reply.clone();
                        let gate = gate_thread.clone();
                        let request_tx = request_tx.clone();
                        let header_tx = header_tx.clone();
                        let index = count_thread.fetch_add(1, Ordering::AcqRel);
                        handlers.push(thread::spawn(move || {
                            respond(stream, reply, gate, index == 0, request_tx, header_tx);
                        }));
                    }
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(5));
                    }
                    Err(e) => panic!("accept: {e}"),
                }
            }
            for handler in handlers {
                handler.join().unwrap();
            }
        });
        Self {
            state,
            directory,
            gate,
            count,
            received,
            headers,
            shutdown,
            worker: Some(worker),
        }
    }

    fn request(
        &self,
        operation: ApiOperation,
        id: u64,
    ) -> Result<chaoxing_desktop_lib::api_proxy::ProxyResponse, ProxyError> {
        api_request(
            &self.state,
            operation,
            None,
            None,
            if operation.is_post() {
                json!({"username":"fixture"})
            } else {
                Value::Null
            },
            id,
        )
    }

    fn spawn_request(
        &self,
        operation: ApiOperation,
        id: u64,
    ) -> thread::JoinHandle<Result<chaoxing_desktop_lib::api_proxy::ProxyResponse, ProxyError>>
    {
        let state = self.state.clone();
        thread::spawn(move || {
            api_request(
                &state,
                operation,
                None,
                None,
                if operation.is_post() {
                    json!({"username":"fixture"})
                } else {
                    Value::Null
                },
                id,
            )
        })
    }

    fn wait_request(&self) -> Vec<u8> {
        self.received
            .recv_timeout(Duration::from_secs(3))
            .expect("backend received request")
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        self.gate.release();
        self.shutdown.store(true, Ordering::Release);
        self.worker.take().unwrap().join().unwrap();
        assert!(self.directory.starts_with(std::env::temp_dir()));
        let _ = std::fs::remove_dir_all(&self.directory);
    }
}

fn respond(
    mut stream: TcpStream,
    reply: Reply,
    gate: Arc<Gate>,
    first: bool,
    request_tx: mpsc::Sender<Vec<u8>>,
    header_tx: mpsc::Sender<()>,
) {
    stream
        .set_read_timeout(Some(Duration::from_secs(3)))
        .unwrap();
    stream
        .set_write_timeout(Some(Duration::from_secs(3)))
        .unwrap();
    let mut request = Vec::new();
    let mut buf = [0; 8192];
    loop {
        let n = match stream.read(&mut buf) {
            Ok(0) | Err(_) => return,
            Ok(n) => n,
        };
        request.extend_from_slice(&buf[..n]);
        if let Some(end) = request.windows(4).position(|part| part == b"\r\n\r\n") {
            let headers = String::from_utf8_lossy(&request[..end]);
            let length = headers
                .lines()
                .find_map(|line| {
                    let (name, value) = line.split_once(':')?;
                    name.eq_ignore_ascii_case("content-length")
                        .then(|| value.trim().parse::<usize>().ok())
                        .flatten()
                })
                .unwrap_or(0);
            if request.len() >= end + 4 + length {
                break;
            }
        }
    }
    let _ = request_tx.send(request);
    if first && matches!(reply.hold, Hold::Headers) {
        gate.block();
    }
    let headers = format!("HTTP/1.1 {} Fixture\r\nContent-Type: application/json\r\nContent-Length: {}\r\n{}Connection: close\r\n\r\n", reply.status, reply.declared_len.unwrap_or(reply.body.len()), reply.extra_headers);
    if stream.write_all(headers.as_bytes()).is_err() {
        return;
    }
    let _ = stream.flush();
    let _ = header_tx.send(());
    if first && matches!(reply.hold, Hold::Body) {
        gate.block();
    }
    let _ = stream.write_all(&reply.body);
    let _ = stream.flush();
}

#[test]
fn p2_success_and_http_error_statuses_are_preserved_without_start_retry() {
    for status in [200, 404, 409] {
        let body = json!({"task_id":"fixture-task", "status":status});
        let server = Server::new(Reply::json(status, body.clone()));
        let response = server.request(ApiOperation::Start, 1).unwrap();
        assert_eq!(response.status, status);
        assert_eq!(response.body, body);
        let request = String::from_utf8(server.wait_request()).unwrap();
        assert!(request.starts_with("POST /api/start HTTP/1.1\r\n"));
        assert!(request.contains("X-Auth-Token: fixture-token\r\n"));
        assert_eq!(server.count.load(Ordering::Acquire), 1);
    }
}

#[test]
fn p2_early_cancel_sends_zero_requests_and_remains_cancelled() {
    let server = Server::new(Reply::json(409, json!({"task_id":"late"})));
    assert!(!api_cancel(&server.state, 7));
    let first = server.request(ApiOperation::Start, 7);
    let second = server.request(ApiOperation::Start, 7);
    assert!(matches!(first, Err(ProxyError::Cancelled)), "{first:?}");
    assert!(matches!(second, Err(ProxyError::Cancelled)), "{second:?}");
    assert_eq!(server.count.load(Ordering::Acquire), 0);
}

#[test]
fn p2_active_and_cancelled_request_ids_cannot_be_reused() {
    for cancel in [false, true] {
        let mut reply = Reply::json(409, json!({"task_id":"late"}));
        reply.hold = Hold::Headers;
        let server = Server::new(reply);
        let first = server.spawn_request(ApiOperation::Start, 8);
        server.wait_request();
        if cancel {
            assert!(api_cancel(&server.state, 8));
        }
        let duplicate = server.request(ApiOperation::Start, 8);
        server.gate.release();
        let original = first.join().unwrap();
        assert!(
            matches!(
                duplicate,
                Err(ProxyError::InvalidRequest { .. }) | Err(ProxyError::Cancelled)
            ),
            "{duplicate:?}"
        );
        if cancel {
            assert!(matches!(original, Err(ProxyError::Cancelled)));
        }
        assert_eq!(server.count.load(Ordering::Acquire), 1);
    }
}

#[test]
fn p2_cancel_during_headers_or_body_discards_success_and_409() {
    for hold in [Hold::Headers, Hold::Body] {
        for status in [200, 409] {
            let mut reply = Reply::json(status, json!({"task_id":"late"}));
            reply.hold = hold;
            let server = Server::new(reply);
            let request = server.spawn_request(ApiOperation::Start, 9);
            server.wait_request();
            if matches!(hold, Hold::Body) {
                server.headers.recv_timeout(Duration::from_secs(3)).unwrap();
                // Let ureq enter its separate response-body read before cancelling.
                thread::sleep(Duration::from_millis(75));
            }
            assert!(api_cancel(&server.state, 9));
            server.gate.release();
            let result = request.join().unwrap();
            assert!(
                matches!(result, Err(ProxyError::Cancelled)),
                "status {status}: {result:?}"
            );
        }
    }
}

#[test]
fn p2_oversized_success_and_error_bodies_are_rejected() {
    for status in [200, 409] {
        let reply = Reply::json(status, json!({"message":"x".repeat(MAX_RESPONSE_BODY)}));
        let server = Server::new(reply);
        let result = server.request(ApiOperation::Start, 10);
        assert!(
            matches!(result, Err(ProxyError::Network { .. })),
            "status {status}: {result:?}"
        );
    }
}

#[test]
fn p2_truncated_error_body_is_not_successful_null() {
    let mut reply = Reply::json(404, json!({"error":"missing"}));
    reply.declared_len = Some(reply.body.len() + 20);
    let server = Server::new(reply);
    let result = server.request(ApiOperation::Start, 11);
    assert!(
        matches!(result, Err(ProxyError::Network { .. })),
        "{result:?}"
    );
}

#[test]
fn p2_invalid_json_response_is_not_successful_null() {
    let mut reply = Reply::json(409, Value::Null);
    reply.body = b"{truncated".to_vec();
    let server = Server::new(reply);
    let result = server.request(ApiOperation::Start, 12);
    assert!(
        matches!(result, Err(ProxyError::Network { .. })),
        "{result:?}"
    );
}

#[test]
fn p2_rejects_invalid_ids_and_options_before_http() {
    let server = Server::new(Reply::json(200, json!({})));
    for id in [0, 9_007_199_254_740_992, u64::MAX] {
        let result = server.request(ApiOperation::Start, id);
        assert!(
            matches!(result, Err(ProxyError::InvalidRequest { .. })),
            "id {id}: {result:?}"
        );
    }
    for (operation, task_id, after, payload) in [
        (ApiOperation::Start, Some("t".into()), None, json!({})),
        (ApiOperation::Start, None, Some(0), json!({})),
        (
            ApiOperation::TaskStatus,
            Some("t".into()),
            Some(0),
            Value::Null,
        ),
        (
            ApiOperation::ConfigRead,
            None,
            None,
            json!({"unexpected":true}),
        ),
    ] {
        let result = api_request(&server.state, operation, task_id, after, payload, 20);
        assert!(
            matches!(result, Err(ProxyError::InvalidRequest { .. })),
            "{result:?}"
        );
    }
    assert_eq!(server.count.load(Ordering::Acquire), 0);
}

#[test]
fn p2_completed_ids_remain_guarded_against_late_reuse() {
    let server = Server::new(Reply::json(200, json!({})));
    server.request(ApiOperation::ConfigRead, 22).unwrap();
    let result = server.request(ApiOperation::ConfigRead, 22);
    assert!(
        matches!(result, Err(ProxyError::InvalidRequest { .. })),
        "{result:?}"
    );
    assert_eq!(server.count.load(Ordering::Acquire), 1);
}

#[test]
fn p2_stop_cancels_body_read_and_blocks_new_requests() {
    let mut reply = Reply::json(409, json!({"task_id":"late"}));
    reply.hold = Hold::Body;
    let server = Server::new(reply);
    let request = server.spawn_request(ApiOperation::Start, 23);
    server.headers.recv_timeout(Duration::from_secs(3)).unwrap();
    thread::sleep(Duration::from_millis(75));
    stop_backend(&server.state);
    server.gate.release();
    let result = request.join().unwrap();
    assert!(matches!(result, Err(ProxyError::Cancelled)), "{result:?}");
    assert!(matches!(
        server.request(ApiOperation::Start, 24),
        Err(ProxyError::BackendNotReady { .. })
    ));
}

#[test]
fn p2_non_ready_phases_reject_requests() {
    let server = Server::new(Reply::json(200, json!({})));
    for phase in [
        BackendPhase::Starting,
        BackendPhase::Stopping,
        BackendPhase::Stopped,
        BackendPhase::Failed,
    ] {
        *server.state.phase.lock().unwrap() = phase;
        assert!(matches!(
            server.request(ApiOperation::ConfigRead, 25),
            Err(ProxyError::BackendNotReady { .. })
        ));
    }
    assert_eq!(server.count.load(Ordering::Acquire), 0);
}

#[test]
fn p2_backend_status_does_not_serialize_connection_credentials() {
    let server = Server::new(Reply::json(200, json!({})));
    let value = serde_json::to_value(server.state.status()).unwrap();
    assert_eq!(value["phase"], "ready");
    assert!(value.get("port").is_none());
    assert!(value.get("token").is_none());
}

#[test]
fn p2_redirect_is_not_followed() {
    let destination = Server::new(Reply::json(200, json!({"leaked":true})));
    let port = destination.state.port.lock().unwrap().unwrap();
    let mut reply = Reply::json(302, json!({"redirect":true}));
    reply.extra_headers = format!("Location: http://127.0.0.1:{port}/unexpected\r\n");
    let origin = Server::new(reply);
    let result = origin.request(ApiOperation::ConfigRead, 26).unwrap();
    assert_eq!(result.status, 302);
    assert_eq!(destination.count.load(Ordering::Acquire), 0);
}

#[test]
fn p2_stalled_error_body_obeys_whole_request_timeout() {
    let mut reply = Reply::json(409, json!({"task_id":"late"}));
    reply.hold = Hold::Body;
    let server = Server::new(reply);
    let started = Instant::now();
    let result = server.request(ApiOperation::Start, 27);
    server.gate.release();
    assert!(started.elapsed() >= Duration::from_secs(29));
    assert!(started.elapsed() < Duration::from_secs(33));
    let error =
        serde_json::to_value(result.expect_err("timeout must not be successful null")).unwrap();
    assert_eq!(error["kind"], "timeout");
}
