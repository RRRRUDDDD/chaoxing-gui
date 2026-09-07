"""Tauri 宿主模式运行时（CHAOXING_TAURI=1）。

协议（P1 计划 §7 / plan.md §3.3）：
- 环境变量缺失/为空 → 直接退出，绝不退回旧模式。
- werkzeug make_server 绑定 127.0.0.1:0（随机端口），threaded=True。
- 就绪握手：向 stdout 输出单行 JSON：
  {"ready":"chaoxing-ready","version":1,"port":<n>,"instanceId":"<id>"}
- token 鉴权：before_request 钩子校验 X-Auth-Token 与 Host 头，
  覆盖所有路径（含 /api/health 与静态路由）；失败 401。
- Tauri 模式不注册 CORS（原生转发无跨域需求；普通模式 CORS 契约不动）。
- stdin watchdog（app.py 的 watch_parent_stdin）语义不变：EOF → os._exit(0)。
"""

import hmac
import json
import os
import sys
import threading

from flask import Flask, jsonify, request
from werkzeug.serving import make_server

READY_MARKER = "chaoxing-ready"
READY_VERSION = 1

TOKEN_HEADER = "X-Auth-Token"


class TauriEnvError(RuntimeError):
    """CHAOXING_TAURI 环境变量不完整。"""


def parse_ready_env(environ=None):
    """读取 Tauri 注入的 token/instanceId；缺失即抛 TauriEnvError。"""
    env = environ if environ is not None else os.environ
    token = env.get("CHAOXING_TAURI_TOKEN", "")
    instance_id = env.get("CHAOXING_TAURI_INSTANCE_ID", "")
    if not token or not instance_id:
        raise TauriEnvError(
            "CHAOXING_TAURI_TOKEN / CHAOXING_TAURI_INSTANCE_ID 未设置，"
            "Tauri 模式拒绝启动（不允许回退旧模式）"
        )
    return token, instance_id


def register_token_guard(app: Flask, token: str, instance_id: str, port: int):
    """注册 before_request 钩子：所有路径校验 token + Host。

    Host 必须恰为 127.0.0.1:<port>（防 DNS-rebinding / 错误端口接管）。
    同时改造 /api/health 回显 instanceId（宿主 token+instanceId 双重核对）。
    app.py 自带的 /api/health 视图函数被原地增强，路由表不变。
    """

    @app.before_request
    def _tauri_token_guard():
        supplied = request.headers.get(TOKEN_HEADER, "")
        if not hmac.compare_digest(supplied.encode("utf-8"), token.encode("utf-8")):
            return jsonify({"status": False, "msg": "unauthorized"}), 401
        if request.host != f"127.0.0.1:{port}":
            return jsonify({"status": False, "msg": "bad host"}), 401
        if "Origin" in request.headers:
            return jsonify({"status": False, "msg": "bad origin"}), 401
        return None

    original_health = app.view_functions.get("health")

    def _tauri_health():
        import flask

        resp = (
            original_health()
            if original_health is not None
            else jsonify({"status": True, "msg": "OK"})
        )
        # 回显 instanceId；Flask 视图返回 tuple 时原样透传
        if isinstance(resp, tuple):
            body, status = resp[0], resp[1]
        else:
            body, status = resp, 200
        body_json = body.get_json(silent=True) or {}
        body_json["instanceId"] = instance_id
        return flask.make_response(jsonify(body_json), status)

    # app.py 已注册名为 health 的视图时原地增强（路由表不变）；
    # 测试 app 无自带路由时注册新 /api/health。
    if original_health is not None:
        app.view_functions["health"] = _tauri_health
    else:
        app.add_url_rule("/api/health", "health", _tauri_health)


def emit_ready_line(port: int, instance_id: str, stream=None):
    """输出就绪握手行（单行 JSON + flush）。"""
    stream = stream if stream is not None else sys.stdout
    line = json.dumps(
        {
            "ready": READY_MARKER,
            "version": READY_VERSION,
            "port": port,
            "instanceId": instance_id,
        },
        separators=(",", ":"),
    )
    stream.write(line + "\n")
    stream.flush()


def run_tauri_server(app: Flask, token: str, instance_id: str):
    """绑定 127.0.0.1:0，后台 serve_forever，输出握手行。

    返回 server 对象（测试可用 server.server_address[1] 取端口）。
    """
    server = make_server("127.0.0.1", 0, app, threaded=True)
    port = server.server_address[1]

    register_token_guard(app, token, instance_id, port)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # serve_forever 线程已启动、logger 此前未向 stdout 写行时是静默点；
    # 宿主侧本就容忍握手前的前置日志行。
    emit_ready_line(port, instance_id)
    return server
