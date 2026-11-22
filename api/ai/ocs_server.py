"""Optional local OCS-compatible HTTP question server (127.0.0.1 only).

Compatible with OCS 网课助手 AnswererWrapper JSON configurations.
"""
from __future__ import annotations

import json
import threading
from typing import Any, Dict, Optional

from flask import Flask, jsonify, request
from werkzeug.serving import make_server

from api.logger import logger
from api.privacy import redact
from api.ai.config import AIConfigStore
from api.ai.engine import AIEngine


class LocalOcsServer:
    def __init__(self, engine: AIEngine, config_store: AIConfigStore) -> None:
        self._engine = engine
        self._config_store = config_store
        self._lock = threading.RLock()
        self._server = None
        self._thread: Optional[threading.Thread] = None
        self._port: Optional[int] = None
        self._running = False
        self._last_error: str = ""

    def _build_app(self) -> Flask:
        srv_app = Flask("ai_ocs_local_server")

        @srv_app.after_request
        def _add_cors(resp):
            resp.headers["Access-Control-Allow-Origin"] = "*"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-AI-Token"
            return resp

        def _check_token() -> bool:
            cfg = self._config_store.load()
            expected = str(cfg["engine"].get("localServerToken") or "").strip()
            if not expected:
                return True
            auth = request.headers.get("Authorization", "").strip()
            if auth.lower().startswith("bearer "):
                auth = auth[7:].strip()
            token = auth or request.headers.get("X-AI-Token", "").strip() or request.args.get("token", "").strip()
            return token == expected

        @srv_app.route("/health", methods=["GET", "OPTIONS"])
        def health():
            if request.method == "OPTIONS":
                return ("", 204)
            return jsonify({"status": "ok", "service": "ai-local-ocs"})

        @srv_app.route("/query", methods=["GET", "POST", "OPTIONS"])
        def query():
            if request.method == "OPTIONS":
                return ("", 204)
            if not _check_token():
                return jsonify({"code": 0, "msg": "认证失败：Token 无效"}), 401
            if request.method == "POST":
                body = request.get_json(silent=True)
                if not isinstance(body, dict):
                    body = request.form.to_dict()
            else:
                body = request.args.to_dict()
            q_info = {
                "title": body.get("title") or body.get("question") or body.get("q") or "",
                "options": body.get("options") or "",
                "type": body.get("type") or "",
                "images": body.get("images") if isinstance(body.get("images"), list) else [],
            }
            res = self._engine.answer_question(
                q_info,
                allow_local=True,
                allow_ai=True,
                record_log=True,
                request_path="/query",
                request_method=request.method,
            )
            if res.get("ok") and res.get("answer"):
                return jsonify(
                    {
                        "code": 1,
                        "question": q_info["title"],
                        "answer": res["answer"],
                        "source": res.get("source"),
                        "model": res.get("modelName"),
                        "requestId": res.get("requestId"),
                    }
                )
            return jsonify(
                {
                    "code": 0,
                    "question": q_info["title"],
                    "answer": "",
                    "msg": res.get("error") or "未找到答案",
                    "requestId": res.get("requestId"),
                }
            )

        return srv_app

    def start(self, port: Optional[int] = None) -> Dict[str, Any]:
        with self._lock:
            cfg = self._config_store.load()
            target_port = int(port or cfg["engine"].get("localServerPort") or 3005)
            target_port = max(1024, min(65535, target_port))
            if self._running and self._port == target_port:
                return self.status()
            if self._running:
                self.stop()
            try:
                flask_app = self._build_app()
                server = make_server("127.0.0.1", target_port, flask_app, threaded=True)
                thread = threading.Thread(target=server.serve_forever, name="ai-ocs-server", daemon=True)
                thread.start()
                self._server = server
                self._thread = thread
                self._port = int(server.server_port)
                self._running = True
                self._last_error = ""
                logger.info("AI 本地 OCS 服务已启动: http://127.0.0.1:{}", self._port)
            except Exception as exc:
                self._running = False
                self._server = None
                self._thread = None
                self._last_error = redact(exc)
                logger.error("AI 本地 OCS 服务启动失败: {}", self._last_error)
            return self.status()

    def stop(self) -> Dict[str, Any]:
        with self._lock:
            if self._server is not None:
                try:
                    self._server.shutdown()
                    self._server.server_close()
                except Exception as exc:
                    logger.debug("停止 AI 本地 OCS 服务异常: {}", exc)
            self._server = None
            self._thread = None
            self._running = False
            return self.status()

    def status(self) -> Dict[str, Any]:
        with self._lock:
            cfg = self._config_store.load()
            port = self._port if self._running and self._port else int(cfg["engine"].get("localServerPort") or 3005)
            token = str(cfg["engine"].get("localServerToken") or "").strip()
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            ocs_wrapper = [
                {
                    "name": "内置 AI 题库",
                    "homepage": "https://github.com/RRRRUDDDD/chaoxing-gui",
                    "url": f"http://127.0.0.1:{port}/query",
                    "method": "post",
                    "type": "GM_xmlhttpRequest",
                    "contentType": "json",
                    "headers": headers,
                    "data": {
                        "title": "${title}",
                        "options": "${options}",
                        "type": "${type}",
                    },
                    "handler": "return (res) => res.code === 1 ? [res.question, res.answer] : undefined",
                }
            ]
            return {
                "running": self._running,
                "port": port,
                "url": f"http://127.0.0.1:{port}/query",
                "error": self._last_error,
                "ocsConfigJson": json.dumps(ocs_wrapper, ensure_ascii=False, indent=2),
            }
