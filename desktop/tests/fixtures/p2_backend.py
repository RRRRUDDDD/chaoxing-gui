"""Synthetic desktop business flow. No Chaoxing imports or upstream requests.

The exact same HTTP fixture serves Chromium, the unmodified Electron shell and
Tauri's native proxy. Control/counters stay in the explicitly supplied test dir.
"""

import json
import os
from pathlib import Path
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


profile = Path(os.environ["P2_FIXTURE_ROOT"]).resolve()
web_dist = Path(os.environ["P2_WEB_DIST"]).resolve()
profile.mkdir(parents=True, exist_ok=True)
(profile / "pid.json").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
lock = threading.Lock()
counts = {"start": 0, "status": 0, "logs": 0, "after": [], "configWrites": 0}
config = {"selectedCoursesByAccount": {"p2-fixture": ["course-1"]}}
tauri = os.environ.get("CHAOXING_TAURI") == "1"
token = os.environ.get("CHAOXING_TAURI_TOKEN", "")
instance = os.environ.get("CHAOXING_TAURI_INSTANCE_ID", "")


def control():
    try:
        return json.loads((profile / "control.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def snapshot():
    temporary = profile / "counts.tmp"
    temporary.write_text(json.dumps(counts), encoding="utf-8")
    temporary.replace(profile / "counts.json")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(web_dist), **kwargs)

    def log_message(self, *_args):
        pass

    def respond(self, status, body):
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_POST(self):
        self.handle_api()

    def do_GET(self):
        if urlsplit(self.path).path.startswith("/api/"):
            self.handle_api()
        else:
            super().do_GET()

    def handle_api(self):
        if tauri and (self.headers.get("X-Auth-Token") != token
                      or self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}"):
            return self.respond(401, {"status": False, "msg": "unauthorized fixture request"})
        route = urlsplit(self.path)
        data = {}
        if self.command == "POST":
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            try:
                data = json.loads(raw)
            except ValueError:
                return self.respond(400, {"status": False, "msg": "invalid fixture JSON"})
        state = control()
        if route.path == "/api/health":
            return self.respond(200, {"status": True, "instanceId": instance})
        if state.get("delayMs"):
            time.sleep(state["delayMs"] / 1000)
        with lock:
            if route.path == "/api/login":
                # Password is deliberately discarded, never counted or persisted.
                return self.respond(200, {"status": True, "data": {"username": data.get("username")}})
            if route.path == "/api/courses":
                return self.respond(200, {"status": True, "data": [
                    {"courseId": "course-1", "title": "P2 测试课程一"},
                    {"courseId": "course-2", "title": "P2 测试课程二"},
                ]})
            if route.path == "/api/config":
                if self.command == "POST":
                    config.update(data)
                    counts["configWrites"] += 1
                    snapshot()
                return self.respond(200, {"status": True, "data": config})
            if route.path == "/api/start":
                counts["start"] += 1
                snapshot()
                return self.respond(409, {"status": False, "msg": "fixture task already exists",
                                          "data": {"task_id": "p2-existing-task"}})
            if state.get("missing") and (route.path.startswith("/api/task/")
                                         or route.path.startswith("/api/logs/")):
                return self.respond(404, {"status": False, "msg": "fixture task expired"})
            if route.path == "/api/task/p2-existing-task":
                counts["status"] += 1
                snapshot()
                terminal = counts["status"] > 1
                return self.respond(200, {"status": True, "data": {
                    "status": "completed" if terminal else "running", "progress": int(terminal),
                    "total": 1, "stats": {"completed_chapters": int(terminal), "total_chapters": 1},
                    "start_time": 1700000000,
                }})
            if route.path == "/api/task/p2-existing-task/details":
                return self.respond(200, {"status": True, "data": {"courses": [
                    {"id": "course-1", "title": "P2 测试课程一", "status": "completed",
                     "chapters": [{"id": "chapter-1", "title": "测试章节", "status": "completed"}]},
                ]}})
            if route.path == "/api/logs/p2-existing-task":
                counts["logs"] += 1
                after = int(parse_qs(route.query).get("after", ["0"])[0])
                counts["after"].append(after)
                snapshot()
                if counts["logs"] == 2:
                    return self.respond(500, {"status": False, "msg": "P2 final logs retry"})
                logs = [{"seq": 1, "message": "P2 唯一日志一", "timestamp": 1700000000, "level": "info"}]
                if counts["logs"] >= 3:
                    logs += [{"seq": 2, "message": "P2 终态日志二", "timestamp": 1700000001, "level": "success"}]
                # Intentionally return overlapping seq=1 despite after=1.
                return self.respond(200, {"status": True, "data": logs,
                                          "next_cursor": logs[-1]["seq"], "truncated": False})
        return self.respond(404, {"status": False, "msg": "fixture route missing"})


def watch_parent():
    while sys.stdin.buffer.read(1):
        pass
    os._exit(0)


threading.Thread(target=watch_parent, daemon=True).start()
delay = int(os.environ.get("P2_READY_DELAY_MS", "0"))
if delay:
    time.sleep(delay / 1000)
server = ThreadingHTTPServer(("127.0.0.1", 0 if tauri else int(os.environ.get("CHAOXING_PORT", "0"))), Handler)
(profile / "runtime.json").write_text(json.dumps({"port": server.server_port, "pid": os.getpid()}), encoding="utf-8")
print(json.dumps({"ready": "chaoxing-ready", "version": 1, "port": server.server_port,
                  "instanceId": instance}), flush=True)
server.serve_forever()
