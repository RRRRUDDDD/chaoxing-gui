//! Integration-test helper backend: speaks the chaoxing-ready v1 protocol
//! without system Python. Behavior driven by env vars so tests can inject
//! failures:
//!   FAKE_MODE = ok | wrong-instance | exit-before-ready | hang-then-exit | spawn-grandchild
//!   FAKE_DELAY_READY_MS = delay before printing ready line
#![allow(dead_code)]

use std::io::Write;
use std::net::TcpListener;
use std::thread;
use std::time::Duration;

fn main() {
    let mode = std::env::var("FAKE_MODE").unwrap_or_else(|_| "ok".into());
    let token = std::env::var("CHAOXING_TAURI_TOKEN").unwrap_or_default();
    let instance = std::env::var("CHAOXING_TAURI_INSTANCE_ID").unwrap_or_default();

    // stdin watchdog: parent closing stdin must kill us (EOF → exit 0).
    thread::spawn(move || {
        let mut buf = [0u8; 1];
        loop {
            use std::io::Read;
            match std::io::stdin().read(&mut buf) {
                Ok(0) | Err(_) => {
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

    if mode == "spawn-grandchild" {
        // Spawn a long-lived grandchild so tests can verify Job tree-kill.
        use std::os::windows::process::CommandExt;
        let _ = std::process::Command::new("cmd")
            .args(["/c", "ping -n 120 127.0.0.1 > nul"])
            .creation_flags(0x0800_0000)
            .spawn();
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

    let reported_instance = if mode == "wrong-instance" {
        format!("{instance}-wrong")
    } else {
        instance
    };

    // Minimal HTTP server: /api/health with token auth, everything else echo 404.
    for stream in listener.incoming() {
        let mut stream = match stream {
            Ok(s) => s,
            Err(_) => continue,
        };
        use std::io::Read;
        let mut buf = [0u8; 4096];
        let n = stream.read(&mut buf).unwrap_or(0);
        let req = String::from_utf8_lossy(&buf[..n]);
        let status_line_ok =
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n"
                .to_string();
        let body_ok =
            format!("{{\"status\":true,\"msg\":\"OK\",\"instanceId\":\"{reported_instance}\"}}");
        let head = req.lines().next().unwrap_or("");
        let has_token = req.contains(&format!("X-Auth-Token: {token}"));
        let response = if !has_token {
            "HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                .to_string()
        } else if head.starts_with("GET /api/health") {
            format!("{status_line_ok}{body_ok}")
        } else {
            let body = "{\"error\":\"not found\"}";
            format!(
                "HTTP/1.1 404 Not Found\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            )
        };
        let _ = stream.write_all(response.as_bytes());
        let _ = stream.flush();
    }
}
