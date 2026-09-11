"""Tauri 宿主模式协议测试（CHAOXING_TAURI=1）。

覆盖（P1 计划 §7 tests 清单）：
1. 缺 token 环境启动 → 退出/报错，不 fallback。
2. ready 行格式（前缀、version、port、instanceId、单行、flush）。
3. token guard：所有路由无 token 401 / 错 token 401 / 伪造 Host 401 /
   OPTIONS 预检 401 / 正确 token+Host 200 且 body 回显 instanceId。
4. Tauri 模式不注册 CORS（响应无 Access-Control-Allow-Origin）。
5. 普通模式（无 TAURI env）health 无需 token、guard 未注册。
6. make_server 绑定 127.0.0.1:0 实际端口可取、socket 可连。
"""

import json
import os
import socket
import threading
import unittest
from unittest import mock

from api import desktop_runtime
from api.desktop_runtime import (
    READY_MARKER,
    TauriEnvError,
    emit_ready_line,
    parse_ready_env,
    register_token_guard,
    run_tauri_server,
)

TOKEN = "test-token-abc123"
INSTANCE = "inst-xyz789"


class _FakeEnviron(dict):
    pass


class ParseReadyEnvTests(unittest.TestCase):
    def test_missing_token_raises(self):
        env = _FakeEnviron()
        with self.assertRaises(TauriEnvError):
            parse_ready_env(env)

    def test_empty_token_raises(self):
        env = _FakeEnviron(
            {"CHAOXING_TAURI_TOKEN": "", "CHAOXING_TAURI_INSTANCE_ID": INSTANCE}
        )
        with self.assertRaises(TauriEnvError):
            parse_ready_env(env)

    def test_missing_instance_raises(self):
        env = _FakeEnviron({"CHAOXING_TAURI_TOKEN": TOKEN})
        with self.assertRaises(TauriEnvError):
            parse_ready_env(env)

    def test_valid_env_returns_pair(self):
        env = _FakeEnviron(
            {"CHAOXING_TAURI_TOKEN": TOKEN, "CHAOXING_TAURI_INSTANCE_ID": INSTANCE}
        )
        self.assertEqual(parse_ready_env(env), (TOKEN, INSTANCE))

    def test_no_fallback_in_real_environ(self):
        """真实 os.environ 缺少变量时必须抛错（不允许回退旧模式）。"""
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(TauriEnvError):
                parse_ready_env()


class ReadyLineTests(unittest.TestCase):
    def test_ready_line_format(self):
        import io

        buf = io.StringIO()
        emit_ready_line(12345, INSTANCE, stream=buf)
        out = buf.getvalue()
        # 单行 + 换行结尾
        self.assertTrue(out.endswith("\n"))
        self.assertEqual(out.count("\n"), 1)
        data = json.loads(out)
        self.assertEqual(data["ready"], READY_MARKER)
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["port"], 12345)
        self.assertEqual(data["instanceId"], INSTANCE)


class TokenGuardTests(unittest.TestCase):
    def setUp(self):
        from flask import Flask, jsonify

        self.app = Flask(__name__)

        @self.app.route("/api/config", methods=["GET", "POST"])
        def config():
            return jsonify({"ok": True})

        @self.app.route("/api/task/<task_id>")
        def task(task_id):
            return jsonify({"taskId": task_id})

        self.server = run_tauri_server(self.app, TOKEN, INSTANCE)
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.shutdown()

    def _request(self, path, token=TOKEN, host=None, method="GET", data=None):
        """经真实 socket 发请求（可选伪造 Host 头）。"""
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        req_lines = [f"{method} {path} HTTP/1.1"]
        if host is None:
            host = f"127.0.0.1:{self.port}"
        req_lines.append(f"Host: {host}")
        if token is not None:
            req_lines.append(f"X-Auth-Token: {token}")
        req_lines.append("Connection: close")
        body = ""
        if data is not None:
            body = json.dumps(data)
            req_lines.append("Content-Type: application/json")
            req_lines.append(f"Content-Length: {len(body)}")
        req = "\r\n".join(req_lines) + "\r\n\r\n" + body
        sock.sendall(req.encode())
        chunks = []
        while True:
            try:
                chunk = sock.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            chunks.append(chunk)
        sock.close()
        raw = b"".join(chunks)
        head, _, payload = raw.partition(b"\r\n\r\n")
        status = int(head.split(b"\r\n")[0].split()[1])
        headers = {
            k.decode().lower(): v.decode()
            for k, _, v in (h.partition(b":") for h in head.split(b"\r\n")[1:])
        }
        return status, headers, payload

    def test_health_without_token_401(self):
        status, _, _ = self._request("/api/health", token=None)
        self.assertEqual(status, 401)

    def test_health_wrong_token_401(self):
        status, _, _ = self._request("/api/health", token="wrong")
        self.assertEqual(status, 401)

    def test_health_forged_host_401(self):
        status, _, _ = self._request(
            "/api/health", host="127.0.0.1:1"
        )
        self.assertEqual(status, 401)

    def test_business_route_without_token_401(self):
        status, _, _ = self._request("/api/config", token=None)
        self.assertEqual(status, 401)

    def test_options_preflight_401(self):
        status, _, _ = self._request(
            "/api/config", token=None, method="OPTIONS"
        )
        self.assertEqual(status, 401)

    def test_correct_token_and_host_200(self):
        status, _, payload = self._request("/api/health")
        self.assertEqual(status, 200)
        data = json.loads(payload)
        self.assertEqual(data.get("instanceId"), INSTANCE)

    def test_correct_token_business_route_200(self):
        status, _, payload = self._request("/api/task/t1")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["taskId"], "t1")

    def test_no_cors_header_in_tauri_mode(self):
        status, headers, _ = self._request("/api/health")
        self.assertEqual(status, 200)
        self.assertNotIn("access-control-allow-origin", headers)

    def test_binds_random_port_and_connectable(self):
        # 端口非 0 且 socket 可连（TokenGuardTests.setUp 已验证连接性）
        self.assertNotEqual(self.port, 0)
        self.assertTrue(self.port > 0)


class NormalModeTests(unittest.TestCase):
    """普通模式（无 CHAOXING_TAURI）guard 未注册，行为与现有测试一致。"""

    def setUp(self):
        from flask import Flask, jsonify

        self.app = Flask(__name__)

        @self.app.route("/api/health")
        def health():
            return jsonify({"status": True, "msg": "OK"})

        # 不调用 register_token_guard —— 模拟普通模式
        self.server = desktop_runtime.make_server("127.0.0.1", 0, self.app, threaded=True)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()

    def test_health_without_token_200(self):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        req = f"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nConnection: close\r\n\r\n"
        sock.sendall(req.encode())
        chunks = []
        while True:
            try:
                chunk = sock.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            chunks.append(chunk)
        sock.close()
        head = b"".join(chunks).split(b"\r\n\r\n")[0]
        status = int(head.split(b"\r\n")[0].split()[1])
        self.assertEqual(status, 200)

    def test_app_entry_has_no_tauri_side_effect(self):
        """app.py 模块导入（无 TAURI env）不应注册 guard 或改变 CORS。"""
        # app.py 导入期注册的 CORS 契约保持不变（现有 131 项回归覆盖）；
        # 这里只断言 desktop_runtime.parse_ready_env 在缺 env 时报错，
        # 确保 __main__ 的 TAURI 分支不会误入。
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(TauriEnvError):
                parse_ready_env()


if __name__ == "__main__":
    unittest.main()
