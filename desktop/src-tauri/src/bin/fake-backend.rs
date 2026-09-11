//! Integration-test helper backend: speaks the chaoxing-ready v1 protocol
//! without system Python. Behavior driven by env vars so tests can inject
//! failures:
//!   FAKE_MODE = ok | wrong-instance | exit-before-ready | spawn-grandchild
//!             | exit-after-ready | ignore-stdin
//!   FAKE_DELAY_READY_MS = delay before printing ready line
//!   FAKE_DELAY_HEALTH_MS = delay before returning health
//! PID and request marker files live only in CHAOXING_DATA_DIR test fixtures.

use std::io::{Read, Write};
use std::net::TcpListener;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::Duration;

fn main() {
    if std::env::args().any(|arg| arg == "--grandchild") {
        loop {
            thread::sleep(Duration::from_secs(60));
        }
    }
    let mode = std::env::var("FAKE_MODE").unwrap_or_else(|_| "ok".into());
    let token = std::env::var("CHAOXING_TAURI_TOKEN").unwrap_or_default();
    let instance = std::env::var("CHAOXING_TAURI_INSTANCE_ID").unwrap_or_default();
    let data_dir = std::env::var_os("CHAOXING_DATA_DIR")
        .map(std::path::PathBuf::from)
        .expect("fixture data dir");
    std::fs::write(
        data_dir.join("fake-backend.pid"),
        std::process::id().to_string(),
    )
    .unwrap();

    // stdin watchdog: parent closing stdin must kill us (EOF → exit 0).
    let ignore_stdin = mode == "ignore-stdin";
    thread::spawn(move || {
        let mut buf = [0u8; 1];
        loop {
            match std::io::stdin().read(&mut buf) {
                Ok(0) | Err(_) => {
                    if ignore_stdin {
                        return;
                    }
                    eprintln!("fake-backend: stdin EOF, exiting");
                    std::process::exit(0);
                }
                Ok(_) => {}
            }
        }
    });

    if mode == "exit-before-ready" {
        eprintln!("fake-backend: dying before ready");
        std::process::exit(3);
    }

    if matches!(mode.as_str(), "spawn-grandchild" | "exit-after-ready") {
        // Spawn a long-lived grandchild so tests can verify Job tree-kill.
        use std::os::windows::process::CommandExt;
        let mut child = std::process::Command::new(std::env::current_exe().unwrap())
            .arg("--grandchild")
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .creation_flags(0x0800_0000)
            .spawn()
            .unwrap();
        std::fs::write(data_dir.join("fake-grandchild.pid"), child.id().to_string()).unwrap();
        thread::spawn(move || {
            let _ = child.wait();
        });
    }

    let listener = match TcpListener::bind("127.0.0.1:0") {
        Ok(l) => l,
        Err(e) => {
            eprintln!("fake-backend: bind failed: {e}");
            std::process::exit(2);
        }
    };
    let port = listener.local_addr().unwrap().port();

    let delay: u64 = std::env::var("FAKE_DELAY_READY_MS")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(0);
    if delay > 0 {
        thread::sleep(Duration::from_millis(delay));
    }

    let ready_instance = if mode == "wrong-instance" {
        format!("{instance}-wrong")
    } else {
        instance.clone()
    };
    let line = format!(
        "{{\"ready\":\"chaoxing-ready\",\"version\":1,\"port\":{port},\"instanceId\":\"{ready_instance}\"}}\n"
    );
    {
        let mut out = std::io::stdout();
        let _ = out.write_all(line.as_bytes());
        let _ = out.flush();
    }

    if mode == "wrong-instance" {
        // An invalid handshake followed by EOF must fail without the 120s timeout.
        std::process::exit(3);
    }

    let reported_instance = if mode == "wrong-instance" {
        format!("{instance}-wrong")
    } else {
        instance
    };

    let delay_health = std::env::var("FAKE_DELAY_HEALTH_MS")
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .unwrap_or(0);
    let exit_armed = Arc::new(AtomicBool::new(false));

    // Minimal HTTP server: /api/health with token auth, everything else echo 404.
    for stream in listener.incoming() {
        let mut stream = match stream {
            Ok(s) => s,
            Err(_) => continue,
        };
        let token = token.clone();
        let instance = reported_instance.clone();
        let mode = mode.clone();
        let data_dir = data_dir.clone();
        let exit_armed = exit_armed.clone();
        thread::spawn(move || {
            let _ = stream.set_read_timeout(Some(Duration::from_secs(3)));
            let mut buf = [0u8; 4096];
            let n = stream.read(&mut buf).unwrap_or(0);
            let req = String::from_utf8_lossy(&buf[..n]);
            let head = req.lines().next().unwrap_or("");
            let has_token = req.contains(&format!("X-Auth-Token: {token}"));
            let health = has_token && head.starts_with("GET /api/health");
            let (status, body) = if !has_token {
                (401, "{\"error\":\"unauthorized\"}".to_owned())
            } else if health {
                std::fs::write(data_dir.join("fake-health.requested"), b"1").unwrap();
                thread::sleep(Duration::from_millis(delay_health));
                (
                    200,
                    format!("{{\"status\":true,\"instanceId\":\"{instance}\"}}"),
                )
            } else {
                (404, "{\"error\":\"not found\"}".to_owned())
            };
            let response = format!("HTTP/1.1 {status} Fixture\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
            let _ = stream.write_all(response.as_bytes());
            let _ = stream.flush();
            if health && mode == "exit-after-ready" && !exit_armed.swap(true, Ordering::AcqRel) {
                thread::spawn(|| {
                    thread::sleep(Duration::from_millis(400));
                    std::process::exit(3);
                });
            }
        });
    }
}
