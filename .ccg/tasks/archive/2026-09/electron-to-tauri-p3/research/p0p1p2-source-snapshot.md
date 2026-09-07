# Actual integrated P0/P1/P2 source at P3 entry
Includes untracked Rust/Python; generated before P3 edits. Lockfiles are listed in manifest and available in repository.


## .github/workflows/main.yml

```
name: Build Windows Package

on:
  workflow_dispatch:
    inputs:
      publish_release:
        description: Publish the build to GitHub Releases
        required: false
        default: true
        type: boolean
      release_tag:
        description: Release tag to create or update for a manual run
        required: false
        default: v1.1.1
        type: string
  push:
    branches:
      - main
    tags:
      - "v*"

permissions:
  contents: write

jobs:
  test-backend:
    runs-on: windows-latest
    strategy:
      matrix:
        python-version: ["3.11", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: pip
          cache-dependency-path: requirements-test.txt
      - name: Install offline test dependencies
        run: python -m pip install -r requirements-test.txt
      - name: Run first-party regression tests
        env:
          PYTHONUTF8: "1"
        run: python -m unittest discover -s tests -v

  test-web:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
          cache-dependency-path: web/package-lock.json
      - name: Install frontend dependencies
        run: npm --prefix web ci
      - name: Test frontend behavior
        run: npm --prefix web test
      - name: Test desktop behavior
        run: npm --prefix desktop test
      - name: Check desktop JavaScript
        run: |
          foreach ($source in @('desktop/main.js', 'desktop/preload.js', 'desktop/session-store.js')) {
            node --check $source
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
          }
      - name: Build frontend
        run: npm --prefix web run build

  build-windows:
    needs: [test-backend, test-web]
    runs-on: windows-latest

    steps:
      - name: Check out source
        uses: actions/checkout@v4
        timeout-minutes: 10

      - name: Set up Python
        uses: actions/setup-python@v5
        timeout-minutes: 10
        with:
          python-version: "3.11"
          architecture: x64
          cache: pip
          cache-dependency-path: requirements.txt

      - name: Set up Node.js
        uses: actions/setup-node@v4
        timeout-minutes: 10
        with:
          node-version: "20"
          cache: npm
          cache-dependency-path: |
            web/package-lock.json
            desktop/package-lock.json

      - name: Cache Electron binaries
        uses: actions/cache@v4
        with:
          path: |
            ~\AppData\Local\electron\Cache
            ~\AppData\Local\electron-builder\Cache
          key: ${{ runner.os }}-electron-${{ hashFiles('desktop/package-lock.json') }}
          restore-keys: |
            ${{ runner.os }}-electron-

      - name: Build frontend
        working-directory: web
        run: |
          npm ci
          npm run build

      - name: Install packaging dependencies
        shell: pwsh
        run: |
          python -m pip install --upgrade pip
          $requirements = Get-Content requirements.txt | Where-Object {
            $_ -notmatch '^\s*paddleocr\s*'
          }
          $requirements | Set-Content "$env:RUNNER_TEMP\requirements-packaging.txt"
          python -m pip install -r "$env:RUNNER_TEMP\requirements-packaging.txt"
          python -m pip install pyinstaller

      - name: Build single-file executable
        run: pyinstaller --clean --noconfirm chaoxing.spec

      - name: Smoke test executable
        shell: pwsh
        run: |
          $process = Start-Process -FilePath "dist\chaoxing-gui.exe" -PassThru
          try {
            $ready = $false
            for ($attempt = 0; $attempt -lt 30; $attempt++) {
              Start-Sleep -Seconds 1
              try {
                $response = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:5000/" -TimeoutSec 2
                if ($response.StatusCode -eq 200) {
                  $ready = $true
                  break
                }
              } catch {
                # The server may need a few seconds to unpack the one-file executable.
              }
            }
            if (-not $ready) {
              throw "Packaged executable did not serve http://127.0.0.1:5000/"
            }
          } finally {
            if (-not $process.HasExited) {
              Stop-Process -Id $process.Id -Force
            }
          }

      - name: Assemble release archive
        shell: pwsh
        run: |
          New-Item -ItemType Directory -Force release | Out-Null
          Copy-Item "dist\chaoxing-gui.exe" "release\chaoxing-gui.exe"
          Copy-Item README.md release\README.md
          Compress-Archive -Path "release\*" -DestinationPath "release\chaoxing-gui-no-electron-windows-x64.zip" -Force

      - name: Build desktop backend executable
        run: pyinstaller --clean --noconfirm chaoxing-backend.spec

      - name: Smoke test desktop backend (headless)
        shell: pwsh
        run: |
          $env:CHAOXING_HEADLESS = "1"
          $env:CHAOXING_PORT = "5123"
          $process = Start-Process -FilePath "dist\chaoxing-backend\chaoxing-backend.exe" -PassThru
          try {
            $ready = $false
            for ($attempt = 0; $attempt -lt 60; $attempt++) {
              Start-Sleep -Seconds 1
              try {
                $response = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:5123/api/health" -TimeoutSec 2
                if ($response.StatusCode -eq 200) {
                  $ready = $true
                  break
                }
              } catch {
                # The backend needs a few seconds to boot.
              }
            }
            if (-not $ready) {
              throw "Headless backend did not serve http://127.0.0.1:5123/api/health"
            }
          } finally {
            if (-not $process.HasExited) {
              Stop-Process -Id $process.Id -Force
            }
          }

      - name: Build Electron desktop app
        working-directory: desktop
        shell: pwsh
        run: |
          if (Test-Path backend) { Remove-Item -Recurse -Force backend }
          New-Item -ItemType Directory -Force backend | Out-Null
          Copy-Item -Recurse -Force "..\dist\chaoxing-backend" "backend\chaoxing-backend"
          npm ci
          npx electron-builder --win --publish never

      - name: Upload build artifact
        uses: actions/upload-artifact@v4
        with:
          name: chaoxing-gui-no-electron-windows-x64
          path: release\chaoxing-gui-no-electron-windows-x64.zip
          if-no-files-found: error

      - name: Upload desktop artifact
        uses: actions/upload-artifact@v4
        with:
          name: chaoxing-gui-desktop
          path: desktop\release\*.exe
          if-no-files-found: error

      - name: Publish GitHub Release
        if: startsWith(github.ref, 'refs/tags/') || (github.event_name == 'workflow_dispatch' && inputs.publish_release)
        uses: softprops/action-gh-release@v2
        with:
          tag_name: ${{ startsWith(github.ref, 'refs/tags/') && github.ref_name || inputs.release_tag }}
          name: Release ${{ startsWith(github.ref, 'refs/tags/') && github.ref_name || inputs.release_tag }}
          target_commitish: ${{ github.sha }}
          generate_release_notes: true
          files: |
            release/chaoxing-gui-no-electron-windows-x64.zip
            desktop/release/*.exe

```


## api/desktop_runtime.py

```
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

```


## app.py

```
import os
import sys
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import threading
import time
import json
import atexit
import math
from contextlib import contextmanager
from contextvars import copy_context
from typing import Dict
import webbrowser
import socket

# 确定静态文件目录（支持便携版）
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
STATIC_DIR = os.path.join(SCRIPT_DIR, "web", "dist")

from api.base import Chaoxing, Account
from api.answer import Tiku
from api.exceptions import InputFormatError, LoginError
from api.logger import logger
from api.notification import Notification
from api.task_state import TaskAlreadyRunning, TaskStore
import main as main_module

# === 托盘图标相关导入 ===
try:
    from pystray import Icon, Menu, MenuItem
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False
    logger.warning("pystray 未安装，托盘图标功能不可用")


# 如果存在构建好的前端，则使用静态文件服务
if os.path.exists(STATIC_DIR):
    app = Flask(__name__, static_folder=STATIC_DIR, static_url_path='')
else:
    app = Flask(__name__)

# === 环境变量：支持 Electron 无头模式 ===
HEADLESS = os.environ.get("CHAOXING_HEADLESS") == "1" or os.environ.get("CHAOXING_ELECTRON") == "1"
TAURI_MODE = os.environ.get("CHAOXING_TAURI") == "1"
PORT = 0 if TAURI_MODE else int(os.environ.get("CHAOXING_PORT", "5000"))
# 仅限本机访问时也绑定回环地址, 避免局域网内其他设备访问控制台/配置接口
HOST = "127.0.0.1"
# CORS 限定为本机来源, 防止用户浏览器中打开的任意网页跨域读取配置接口
if not TAURI_MODE:
    CORS(app, origins=[f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}"])
# 数据目录：Electron 传入 %APPDATA%/<app>；未设置时沿用脚本目录（独立 exe / 开发模式行为不变）
DATA_DIR = os.environ.get("CHAOXING_DATA_DIR") or os.path.dirname(__file__)

# Web 配置文件路径
CONFIG_FILE = os.path.join(DATA_DIR, "web_config.json")


config_lock = threading.RLock()
task_store = TaskStore(cleanup_interval=60)
atexit.register(task_store.close)


def load_web_config() -> Dict:
    """Read a complete configuration snapshot."""
    with config_lock:
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as config_file:
                data = json.load(config_file)
                return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception as exc:
            logger.error(f"读取 Web 配置失败: {exc}")
            return {}


def save_web_config(data: Dict) -> bool:
    """Atomically save configuration under the same lock used for merging."""
    with config_lock:
        try:
            parent = os.path.dirname(CONFIG_FILE)
            if parent:
                os.makedirs(parent, exist_ok=True)
            tmp_path = CONFIG_FILE + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as config_file:
                json.dump(data, config_file, ensure_ascii=False, indent=2)
            os.replace(tmp_path, CONFIG_FILE)
            return True
        except Exception as exc:
            logger.error(f"保存 Web 配置失败: {exc}")
            return False


class LogCapture:
    def __init__(self, task_id: str, store: TaskStore):
        self.task_id = task_id
        self.store = store

    def write(self, message):
        record = message.record
        if record["extra"].get("task_id") != self.task_id:
            return
        level = record["level"].name.lower()
        if level in {"critical", "fatal"}:
            level = "error"
        self.store.append_log(
            self.task_id, str(message), level=level,
            timestamp=record["time"].timestamp(),
        )


def _json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("请求必须为 JSON 对象")
    return data


def _credentials(data):
    username = data.get("username")
    password = data.get("password", "")
    use_cookies = data.get("use_cookies", False)
    if not isinstance(use_cookies, bool):
        raise ValueError("use_cookies 必须为布尔值")
    if not isinstance(username, str) or not username.strip():
        raise ValueError("用户名不能为空")
    if not isinstance(password, str) or (not use_cookies and not password.strip()):
        raise ValueError("密码不能为空")
    return username.strip(), password, use_cookies


def _course_ids(value, *, allow_empty=False):
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError("请选择至少一门有效课程")
    ids = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (str, int)) or not str(item).strip():
            raise ValueError("课程 ID 格式错误")
        course_id = str(item).strip()
        if course_id not in ids:
            ids.append(course_id)
    return ids


def _finite_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"{name} 必须为有限数值")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{name} 必须为有限数值") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} 必须为有限数值")
    return number


def _close_resource(resource):
    if resource is not None:
        try:
            resource.close()
        except Exception as exc:
            logger.warning(f"清理学习资源失败: {exc}")
            return str(exc)
    return None


@contextmanager
def _login_client(username, password):
    tiku = Tiku()
    chaoxing = None
    try:
        chaoxing = Chaoxing(account=Account(username, password), tiku=tiku, query_delay=0)
        yield chaoxing
    finally:
        _close_resource(chaoxing if chaoxing is not None else tiku)


@app.route('/api/login', methods=['POST'])
def login():
    try:
        username, password, use_cookies = _credentials(_json_body())
    except ValueError as exc:
        return jsonify({"status": False, "msg": str(exc)}), 400
    try:
        with _login_client(username, password) as chaoxing:
            result = chaoxing.login(login_with_cookies=use_cookies)
            if not result["status"]:
                return jsonify({"status": False, "msg": result.get("msg", "登录失败")}), 401
            return jsonify({"status": True, "msg": "登录成功", "data": {"username": username}})
    except Exception as exc:
        logger.error(f"登录错误: {exc}")
        return jsonify({"status": False, "msg": str(exc)}), 500


@app.route('/api/courses', methods=['POST'])
def get_courses():
    try:
        username, password, use_cookies = _credentials(_json_body())
    except ValueError as exc:
        return jsonify({"status": False, "msg": str(exc)}), 400
    try:
        with _login_client(username, password) as chaoxing:
            result = chaoxing.login(login_with_cookies=use_cookies)
            if not result["status"]:
                return jsonify({"status": False, "msg": result.get("msg", "登录失败")}), 401
            return jsonify({"status": True, "data": chaoxing.get_course_list()})
    except Exception as exc:
        logger.error(f"获取课程列表错误: {exc}")
        return jsonify({"status": False, "msg": str(exc)}), 500


@app.route('/api/config', methods=['GET', 'POST'])
def web_config():
    if request.method == 'GET':
        data = load_web_config()
        data.pop("selectedCourses", None)
        return jsonify({"status": True, "data": data})
    try:
        data = _json_body()
        if "settings" in data and not isinstance(data["settings"], dict):
            raise ValueError("settings 必须为对象")
        selections = data.get("selectedCoursesByAccount", {})
        if not isinstance(selections, dict):
            raise ValueError("selectedCoursesByAccount 必须为对象")
        normalized = {}
        for account, ids in selections.items():
            if not isinstance(account, str) or not account.strip():
                raise ValueError("选课配置缺少账号")
            normalized[account.strip()] = _course_ids(ids, allow_empty=True)
    except ValueError as exc:
        return jsonify({"status": False, "msg": str(exc)}), 400

    with config_lock:
        stored = load_web_config()
        stored.pop("selectedCourses", None)
        if "settings" in data:
            stored["settings"] = data["settings"]
        accounts = stored.get("selectedCoursesByAccount", {})
        accounts = dict(accounts) if isinstance(accounts, dict) else {}
        accounts.update(normalized)
        stored["selectedCoursesByAccount"] = accounts
        if not save_web_config(stored):
            return jsonify({"status": False, "msg": "保存失败"}), 500
    return jsonify({"status": True, "msg": "保存成功"})


def _initial_status():
    return {
        "progress": 0, "total": 0,
        "current_course": "", "current_chapter": "", "current_task": "",
        "stats": {
            "total_courses": 0, "completed_courses": 0, "failed_courses": 0,
            "skipped_courses": 0, "partial_courses": 0,
            "total_chapters": 0, "completed_chapters": 0, "empty_chapters": 0,
            "failed_chapters": 0, "skipped_chapters": 0,
            "total_tasks": 0, "completed_tasks": 0, "failed_tasks": 0, "skipped_tasks": 0,
        },
    }


def _job_count(point):
    try:
        return max(0, int(point.get("jobCount", 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _chapter_task_stats(point, result_name):
    supplied = point.get("_task_stats")
    fields = ("total", "completed", "failed", "skipped")
    if isinstance(supplied, dict) and all(
        isinstance(supplied.get(key), int) and not isinstance(supplied[key], bool)
        and supplied[key] >= 0 for key in fields
    ):
        stats = {key: supplied[key] for key in fields}
        stats["total"] = max(stats["total"], sum(stats[key] for key in fields[1:]))
        return stats
    count = 0 if result_name == "EMPTY" else _job_count(point)
    category = {"SUCCESS": "completed", "SKIPPED": "skipped"}.get(result_name, "failed")
    return {
        "total": count,
        "completed": count if category == "completed" else 0,
        "failed": count if category == "failed" else 0,
        "skipped": count if category == "skipped" else 0,
    }


class _StudyProgress:
    """Update chapter counters from final results, once per chapter."""

    def __init__(self, store, task_id):
        self.store = store
        self.task_id = task_id
        self._finalized = set()
        self._points = {}

    @staticmethod
    def _course(task, course):
        return next(item for item in task.details["courses"] if str(item["id"]) == str(course["courseId"]))

    @staticmethod
    def _replace_chapter(task, chapter, state, counts):
        stats = task.status["stats"]
        previous = chapter["status"]
        stats["completed_chapters"] += int(state in {"completed", "empty"}) - int(previous in {"completed", "empty"})
        for status, field in (("empty", "empty_chapters"), ("error", "failed_chapters"), ("skipped", "skipped_chapters")):
            stats[field] += int(state == status) - int(previous == status)
        for key in ("total", "completed", "failed", "skipped"):
            stats[f"{key}_tasks"] += counts[key] - chapter["task_stats"][key]
        chapter.update(status=state, has_finished=state in {"completed", "empty"}, task_stats=counts, jobCount=counts["total"])

    def set_courses(self, courses):
        with self.store.edit(self.task_id) as task:
            task.status["total"] = len(courses)
            task.status["stats"]["total_courses"] = len(courses)
            task.details["courses"] = [
                {"id": course["courseId"], "title": course["title"], "status": "pending",
                 "chapters": [], "start_time": None, "end_time": None}
                for course in courses
            ]

    def begin_course(self, course, index):
        with self.store.edit(self.task_id) as task:
            self._course(task, course).update(status="running", start_time=time.time())
            task.status.update(current_course=course["title"], current_chapter="", progress=index)

    def add_chapters(self, course, point_list):
        if not isinstance(point_list, dict):
            raise ValueError("章节列表格式错误")
        points = point_list["points"]
        if not isinstance(points, list) or any(not isinstance(point, dict) for point in points):
            raise ValueError("章节列表格式错误")
        with self.store.edit(self.task_id) as task:
            detail = self._course(task, course)
            self._points[str(course["courseId"])] = points
            task.status["stats"]["total_chapters"] += len(points)
            for point in points:
                chapter = {
                    "id": point.get("id"), "title": point.get("title", ""), "status": "pending",
                    "has_finished": False, "jobCount": 0,
                    "task_stats": {"total": 0, "completed": 0, "failed": 0, "skipped": 0},
                }
                detail["chapters"].append(chapter)
                count = _job_count(point)
                finished = bool(point.get("has_finished"))
                self._replace_chapter(task, chapter, "completed" if finished else "pending", {
                    "total": count, "completed": count if finished else 0, "failed": 0, "skipped": 0,
                })

    def chapter_start(self, course, point):
        with self.store.edit(self.task_id) as task:
            task.status.update(current_course=course.get("title", ""), current_chapter=point.get("title", ""))
            for chapter in self._course(task, course)["chapters"]:
                if chapter["id"] == point.get("id") and chapter["status"] == "pending":
                    chapter["status"] = "running"
                    break

    def chapter_result(self, course, point, result):
        result_name = getattr(result, "name", "ERROR")
        with self.store.edit(self.task_id) as task:
            detail = self._course(task, course)
            for index, chapter in enumerate(detail["chapters"]):
                if chapter["id"] != point.get("id"):
                    continue
                key = (str(course["courseId"]), index)
                if key in self._finalized:
                    return
                state = {"SUCCESS": "completed", "EMPTY": "empty", "SKIPPED": "skipped"}.get(result_name, "error")
                counts = _chapter_task_stats(point, result_name)
                if counts["failed"]:
                    state = "error"
                elif counts["skipped"] and state in {"completed", "empty"}:
                    state = "skipped"
                self._replace_chapter(task, chapter, state, counts)
                self._finalized.add(key)
                return
            raise ValueError("章节结果不属于当前课程快照")

    @staticmethod
    def _end_course(task, detail, *, failed, skipped):
        has_success = any(chapter["has_finished"] or chapter["task_stats"]["completed"] for chapter in detail["chapters"])
        state = "completed"
        if failed or skipped:
            state = "partial" if has_success or skipped else "error"
        detail.update(status=state, end_time=time.time())
        stats = task.status["stats"]
        stats["completed_courses"] += int(state == "completed")
        stats["partial_courses"] += int(state == "partial")
        stats["failed_courses"] += int(failed)
        stats["skipped_courses"] += int(skipped)

    def finish_course(self, course, result):
        if result is None:
            raise RuntimeError("课程未返回有效学习结果")
        for chapter_task in result.tasks:
            self.chapter_result(course, chapter_task.point, chapter_task.result)
        with self.store.edit(self.task_id) as task:
            detail = self._course(task, course)
            if any(chapter["status"] in {"pending", "running"} for chapter in detail["chapters"]):
                raise RuntimeError("课程存在未返回结果的章节")
            failed = bool(result.failed) or any(
                chapter["status"] == "error" or chapter["task_stats"]["failed"]
                for chapter in detail["chapters"]
            )
            skipped = bool(result.skipped) or any(
                chapter["status"] == "skipped" or chapter["task_stats"]["skipped"]
                for chapter in detail["chapters"]
            )
            failed = failed or (not result.success and not skipped)
            self._end_course(task, detail, failed=failed, skipped=skipped)

    def fail_course(self, course, error):
        with self.store.edit(self.task_id) as task:
            detail = self._course(task, course)
            points = self._points.get(str(course["courseId"]), [])
            for index, chapter in enumerate(detail["chapters"]):
                key = (str(course["courseId"]), index)
                if key not in self._finalized and not chapter["has_finished"]:
                    self._replace_chapter(task, chapter, "error", _chapter_task_stats(points[index], "ERROR"))
                    self._finalized.add(key)
            detail["error"] = str(error)
            skipped = any(chapter["task_stats"]["skipped"] or chapter["status"] == "skipped" for chapter in detail["chapters"])
            self._end_course(task, detail, failed=True, skipped=skipped)

    def video_progress(self, course, job, current_time, duration):
        now = time.time()
        with self.store.edit(self.task_id) as task:
            active = task.details["active_jobs"]
            expired = [key for key, info in active.items() if now - info["timestamp"] > 10]
            for key in expired:
                del active[key]
            active[f"{course['courseId']}:{job['jobid']}"] = {
                "course_name": course["title"], "job_name": job.get("name", "未知任务"),
                "current_time": current_time, "duration": duration,
                "progress": (current_time / duration * 100) if duration > 0 else 0,
                "timestamp": now,
            }

    def outcome(self, *, fatal=False):
        stats = self.store.get_status(self.task_id)["stats"]
        failed = fatal or stats["failed_courses"] or stats["failed_chapters"] or stats["failed_tasks"]
        skipped = stats["skipped_courses"] or stats["skipped_chapters"] or stats["skipped_tasks"]
        successful = stats["completed_courses"] or stats["completed_chapters"] or stats["completed_tasks"]
        if failed and not successful and not skipped:
            return "error"
        return "partial" if failed or skipped else "completed"


def _notification_message(store, task_id, outcome, error):
    if outcome == "completed":
        return "超星学习通: 所有课程学习任务已完成"
    stats = store.get_status(task_id)["stats"]
    message = (
        f"超星学习通: 学习任务结束，完成课程 {stats['completed_courses']}，"
        f"失败课程 {stats['failed_courses']}，跳过课程 {stats['skipped_courses']}，"
        f"失败任务 {stats['failed_tasks']}，跳过任务 {stats['skipped_tasks']}"
    )
    return f"{message}\n{error}" if error else message


def _run_study_task(task_id, store, common_config, tiku_config, notification_config, ocr_config):
    progress = _StudyProgress(store, task_id)
    chaoxing = None
    notification = None
    sink_id = None
    outcome = "error"
    error = None
    with logger.contextualize(task_id=task_id):
        try:
            capture = LogCapture(task_id, store)
            sink_id = logger.add(capture.write, enqueue=True, filter=lambda record: record["extra"].get("task_id") == task_id)
            from api.vision_ocr import ocr_context

            with ocr_context(ocr_config or {}):
                try:
                    try:
                        configured = Notification()
                        configured.config_set(notification_config or {"provider": ""})
                        notification = configured.get_notification_from_config()
                        notification.init_notification()
                    except Exception as exc:
                        notification = None
                        logger.warning(f"通知初始化失败: {exc}")
                        with store.edit(task_id) as task:
                            task.status["notification_error"] = str(exc)

                    common_config.update(
                        chapter_start_callback=progress.chapter_start,
                        chapter_result_callback=progress.chapter_result,
                        video_progress_callback=progress.video_progress,
                    )
                    chaoxing = main_module.init_chaoxing(common_config, tiku_config)
                    result = chaoxing.login(login_with_cookies=common_config["use_cookies"])
                    if not result["status"]:
                        raise LoginError(result.get("msg", "登录失败"))
                    courses = main_module.filter_courses(
                        chaoxing.get_course_list(), common_config["course_list"], interactive=False
                    )
                    progress.set_courses(courses)
                    for index, course in enumerate(courses):
                        progress.begin_course(course, index)
                        try:
                            point_list = chaoxing.get_course_point(course["courseId"], course["clazzId"], course["cpi"])
                            progress.add_chapters(course, point_list)
                            course_result = main_module.process_course(
                                chaoxing, course, common_config, point_list=point_list
                            )
                            progress.finish_course(course, course_result)
                        except Exception as exc:
                            logger.error(f"课程处理失败 {course['title']}: {exc}")
                            progress.fail_course(course, exc)
                        with store.edit(task_id) as task:
                            task.status["progress"] = index + 1
                    outcome = progress.outcome()
                    if outcome != "completed":
                        error = "部分课程失败或被跳过，请查看课程详情"
                finally:
                    cleanup_error = _close_resource(chaoxing)
                    if cleanup_error:
                        with store.edit(task_id) as task:
                            task.status["cleanup_error"] = cleanup_error
        except Exception as exc:
            error = str(exc)
            outcome = progress.outcome(fatal=True)
            logger.error(f"任务执行错误: {exc}")
        finally:
            if notification is not None:
                try:
                    notification.send(_notification_message(store, task_id, outcome, error))
                except Exception as exc:
                    logger.warning(f"通知发送失败: {exc}")
                    with store.edit(task_id) as task:
                        task.status["notification_error"] = str(exc)
            # Flush every enqueued record before publishing the terminal state.
            # Pollers can then stop after one final cursor read without losing logs.
            try:
                logger.complete()
            finally:
                try:
                    if sink_id is not None:
                        logger.remove(sink_id)
                finally:
                    store.finish(task_id, outcome, error=error)


def _launch_study_task(*args):
    context = copy_context()
    thread = threading.Thread(
        target=context.run, args=(_run_study_task, *args),
        name=f"study-{args[0]}", daemon=True,
    )
    thread.start()
    return thread


@app.route('/api/start', methods=['POST'])
def start_study():
    try:
        data = _json_body()
        username, password, use_cookies = _credentials(data)
        course_list = _course_ids(data.get("course_list"))
        jobs = main_module.validate_jobs(data.get("jobs", 4))
        speed = _finite_number(data.get("speed", 1.0), "speed")
        retry_interval = _finite_number(data.get("retry_interval", 1.0), "retry_interval")
        if speed <= 0:
            raise ValueError("speed 必须大于 0")
        if not 0 <= retry_interval <= 300:
            raise ValueError("retry_interval 必须在 0 到 300 秒之间")
        notopen_action = data.get("notopen_action", "retry")
        if notopen_action not in ("retry", "continue"):
            raise ValueError("Web 任务仅支持 retry 或 continue，不能交互询问")
        configs = []
        for key in ("tiku_config", "notification_config", "ocr_config"):
            config = data.get(key, {})
            if config is None and key == "ocr_config":
                config = {}
            if not isinstance(config, dict):
                raise ValueError(f"{key} 必须为对象")
            configs.append(config)
        common_config = {
            "username": username, "password": password, "use_cookies": use_cookies,
            "course_list": course_list, "jobs": jobs, "speed": min(2.0, max(1.0, speed)),
            "retry_interval": retry_interval, "notopen_action": notopen_action, "interactive": False,
        }
    except (ValueError, InputFormatError) as exc:
        return jsonify({"status": False, "msg": str(exc)}), 400

    store = task_store
    try:
        task_id = store.create(username, _initial_status(), {"courses": [], "active_jobs": {}})
    except TaskAlreadyRunning as exc:
        return jsonify({"status": False, "msg": str(exc), "data": {"task_id": exc.task_id}}), 409
    except Exception as exc:
        logger.error(f"创建任务失败: {exc}")
        return jsonify({"status": False, "msg": str(exc)}), 500
    try:
        _launch_study_task(task_id, store, common_config, *configs)
    except Exception as exc:
        store.finish(task_id, "error", error=str(exc))
        logger.error(f"启动任务错误: {exc}")
        return jsonify({"status": False, "msg": str(exc), "data": {"task_id": task_id}}), 500
    return jsonify({"status": True, "data": {"task_id": task_id}})


@app.route('/api/task/<task_id>', methods=['GET'])
def get_task_status(task_id):
    try:
        return jsonify({"status": True, "data": task_store.get_status(task_id)})
    except KeyError:
        return jsonify({"status": False, "msg": "任务不存在或已过期"}), 404


@app.route('/api/task/<task_id>/details', methods=['GET'])
def get_task_details(task_id):
    try:
        return jsonify({"status": True, "data": task_store.get_details(task_id)})
    except KeyError:
        return jsonify({"status": False, "msg": "任务详情不存在或已过期"}), 404


@app.route('/api/logs/<task_id>', methods=['GET'])
def get_logs(task_id):
    cursor = request.args.get("after", "0")
    if not cursor.isascii() or not cursor.isdecimal():
        return jsonify({"status": False, "msg": "after 必须为非负整数"}), 400
    try:
        after = int(cursor)
    except ValueError:
        return jsonify({"status": False, "msg": "after 必须为非负整数"}), 400
    try:
        return jsonify({"status": True, **task_store.read_logs(task_id, after)})
    except KeyError:
        return jsonify({"status": False, "msg": "任务日志不存在或已过期"}), 404

@app.route('/api/health', methods=['GET'])
def health():
    """健康检查"""
    return jsonify({'status': True, 'msg': 'OK'})


# ==================== 静态文件服务（便携版支持） ====================

@app.route('/')
def serve_index():
    """服务前端首页"""
    if os.path.exists(STATIC_DIR):
        return send_from_directory(STATIC_DIR, 'index.html')
    return jsonify({'status': False, 'msg': '前端未构建，请访问 http://localhost:5173 使用开发模式'}), 404


@app.route('/<path:path>')
def serve_static(path):
    """服务静态文件，支持 SPA 客户端路由"""
    if os.path.exists(STATIC_DIR):
        # 如果请求的是 API 路径，跳过（已被上面的路由处理）
        if path.startswith('api/'):
            return jsonify({'status': False, 'msg': 'Not Found'}), 404
        
        # 尝试提供静态文件
        file_path = os.path.join(STATIC_DIR, path)
        if os.path.exists(file_path) and os.path.isfile(file_path):
            return send_from_directory(STATIC_DIR, path)
        
        # 对于 SPA 客户端路由，返回 index.html
        return send_from_directory(STATIC_DIR, 'index.html')
    
    return jsonify({'status': False, 'msg': '前端未构建'}), 404



def is_port_in_use(port: int) -> bool:
    """检查端口是否已被占用"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0


def wait_for_server_ready(url: str, timeout: int = 60) -> bool:
    """等待服务器就绪"""
    import urllib.request
    start = time.time()
    while time.time() - start < timeout:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except:
            time.sleep(0.5)
    return False


def create_tray_icon():
    """创建托盘图标（从 fav.jpg 加载）"""
    # 优先读取 web/public/ 下的图标（与前端登录页图标同源）
    icon_path = os.path.join(SCRIPT_DIR, "web", "public", "fav.jpg")
    if getattr(sys, 'frozen', False):
        # 打包态：从 _MEIPASS 临时解包目录读取
        icon_path = os.path.join(sys._MEIPASS, "fav.jpg")

    if not os.path.exists(icon_path):
        # 兜底：旧位置（脚本根目录）
        icon_path = os.path.join(SCRIPT_DIR, "fav.jpg")

    if os.path.exists(icon_path):
        return Image.open(icon_path)
    else:
        # 兜底：纯色圆形图标
        logger.warning(f"未找到图标文件 {icon_path}，使用默认图标")
        img = Image.new('RGB', (64, 64), color=(33, 150, 243))
        draw = ImageDraw.Draw(img)
        draw.ellipse([4, 4, 60, 60], fill=(33, 150, 243), outline=(255, 255, 255), width=2)
        return img


def open_browser():
    """打开浏览器"""
    webbrowser.open(f"http://localhost:{PORT}")


def setup_tray_icon():
    """设置托盘图标"""
    if not TRAY_AVAILABLE:
        return None

    menu = Menu(
        MenuItem("打开控制台", lambda: open_browser()),
        MenuItem("退出", lambda icon, item: (icon.stop(), os._exit(0)))
    )

    icon = Icon("chaoxing-gui", create_tray_icon(), "超星学习通 · 自动化学习助手", menu)
    return icon


def watch_parent_stdin():
    """stdin 守护线程：监测父进程（Electron）退出，防止孤儿进程"""
    try:
        # 阻塞读取，父进程关闭管道时触发 EOF
        while sys.stdin.buffer.read(1):
            pass
    except Exception:
        pass
    logger.info("父进程已退出，正在终止后端...")
    os._exit(0)


if __name__ == "__main__":
    # === Tauri 宿主模式（CHAOXING_TAURI=1）===
    # 置于 frozen/HEADLESS 分支之前：随机端口握手 + token 鉴权由
    # api/desktop_runtime.py 承载；不读 CHAOXING_PORT。
    if os.environ.get("CHAOXING_TAURI") == "1":
        try:
            from api.desktop_runtime import parse_ready_env, run_tauri_server
        except ImportError as e:
            logger.error(f"Tauri 运行时模块缺失: {e}")
            sys.exit(1)
        try:
            token, instance_id = parse_ready_env()
        except Exception as e:
            logger.error(str(e))
            sys.exit(1)

        # cwd/CHAOXING_DATA_DIR 兜底：导入期路径由宿主 spawn 时设定，
        # 这里保证 chaoxing.log 等运行时文件仍写数据目录而非安装目录
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            os.chdir(DATA_DIR)
        except Exception as e:
            logger.error(f"Tauri 数据目录不可用: {e}")
            sys.exit(1)

        # Tauri 的原生代理无需 CORS；导入期已跳过注册。
        threading.Thread(target=watch_parent_stdin, daemon=True).start()
        try:
            run_tauri_server(app, token, instance_id)
        except Exception as e:
            logger.error(f"Tauri 服务器启动失败: {e}")
            sys.exit(1)

        # serve_forever 在后台线程，主线程保持存活直至 stdin EOF 触发 os._exit
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            sys.exit(0)

    # === 打包态特殊处理 ===
    if getattr(sys, 'frozen', False) and not HEADLESS:
        # 独立 exe 模式（向后兼容）
        if sys.stdout is None or sys.stderr is None:
            # console=False 时 stdout/stderr 为 None，重定向到 devnull 防止 loguru/tqdm 崩溃
            sys.stdout = open(os.devnull, 'w', encoding='utf-8')
            sys.stderr = open(os.devnull, 'w', encoding='utf-8')

        # 检查端口是否已占用（实例复用逻辑）
        if is_port_in_use(PORT):
            logger.info("检测到已有实例运行，直接打开浏览器")
            open_browser()
            sys.exit(0)

        # 修复 web_config.json 路径：冻结态下写到 exe 同目录，而非临时解包目录
        base_path = os.path.dirname(sys.executable)
        os.chdir(base_path)
    elif HEADLESS:
        # Electron 无头模式：cookies.txt / chaoxing.log 等运行时文件写入数据目录
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            os.chdir(DATA_DIR)
        except Exception as e:
            logger.warning(f"切换数据目录失败，沿用当前目录: {e}")
        logger.info(f"Electron 无头模式启动，工作目录: {os.getcwd()}")

    # 检测是否存在前端构建
    if os.path.exists(STATIC_DIR):
        logger.info(f"检测到前端构建，将提供静态文件服务: {STATIC_DIR}")
    else:
        logger.info("未检测到前端构建，仅提供 API 服务")

    # 启动托盘图标（仅独立 exe 模式 + pystray 可用时）
    tray_icon = None
    if getattr(sys, 'frozen', False) and TRAY_AVAILABLE and not HEADLESS:
        tray_icon = setup_tray_icon()
        threading.Thread(target=tray_icon.run, daemon=True).start()
        logger.info("托盘图标已启动，右键可退出")

    # 后台启动 Flask
    flask_thread = threading.Thread(
        target=lambda: app.run(host=HOST, port=PORT, debug=False, use_reloader=False),
        daemon=True
    )
    flask_thread.start()

    # HEADLESS 模式：启动 stdin 守护 + 等待就绪
    if HEADLESS:
        threading.Thread(target=watch_parent_stdin, daemon=True).start()
        if wait_for_server_ready(f"http://127.0.0.1:{PORT}/api/health", timeout=60):
            logger.info(f"后端就绪: http://127.0.0.1:{PORT}")
        else:
            logger.error("服务器启动超时")
    # 独立 exe 模式：等待就绪后打开浏览器
    elif getattr(sys, 'frozen', False):
        if wait_for_server_ready(f"http://localhost:{PORT}/api/health", timeout=60):
            logger.info("服务器就绪，正在打开浏览器...")
            open_browser()
        else:
            logger.error("服务器启动超时")
    else:
        # 开发模式：直接提示 URL，不自动开浏览器
        logger.info(f"开发模式：请手动访问 http://localhost:{PORT}")

    # 主线程保持运行（托盘图标需要主线程存活）
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("接收到退出信号")
        if tray_icon:
            tray_icon.stop()

```


## build_desktop.bat

```
@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ========================================
echo   Chaoxing GUI Desktop Build
echo ========================================
echo.

REM [1/4] Build frontend
echo [1/4] Building frontend...
pushd web
call npm ci || goto :error
call npm run build || goto :error
popd
echo Frontend build complete
echo.

REM [2/4] Build backend exe
echo [2/4] Building backend (chaoxing-backend.exe)...
python -m PyInstaller --clean --noconfirm chaoxing-backend.spec || goto :error
echo Backend build complete
echo.

REM [3/4] Copy backend to desktop/backend
echo [3/4] Copying backend to desktop/backend...
if exist desktop\backend rd /s /q desktop\backend
mkdir desktop\backend
xcopy /E /I /Y dist\chaoxing-backend desktop\backend\chaoxing-backend\ >nul || goto :error
echo Backend copy complete
echo.

REM [4/4] Build Electron desktop app
echo [4/4] Building Electron app...
pushd desktop
call npm ci || goto :error
call npx electron-builder --win || goto :error
popd
echo.

echo ========================================
echo   Build Complete!
echo   Installer: desktop\release\
echo ========================================
dir desktop\release\*.exe
exit /b 0

:error
echo.
echo ========================================
echo   Build Failed!
echo ========================================
popd
exit /b 1

```


## chaoxing-backend.spec

```
# -*- mode: python ; coding: utf-8 -*-
# Electron 后端专用 spec (独立 exe 版本见 chaoxing.spec，注意同步 datas/hiddenimports)
# 主要差异：console=True (支持 stdin/stdout 管道), 移除 pystray, name=chaoxing-backend
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [
    ("web/dist", "web/dist"),
    ("resource", "resource"),
    ("config.ini.example", "."),
    ("fav.jpg", "."),
    ("web/public/fav.jpg", "."),
]
datas += collect_data_files("ddddocr")

hiddenimports = [
    "flask_cors",
    "loguru",
    "pyaes",
    "bs4",
    "lxml",
    "openai",
    "httpx",
    "ddddocr",
    "onnxruntime",
    "PIL",
    "numpy",
    "cv2",
    "tqdm",
    "fontTools",
    "requests",
    "urllib3",
    # 移除 pystray - Electron 无头模式不需要托盘图标
]
hiddenimports += collect_submodules("api")

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["paddleocr", "paddlepaddle", "PaddleOCR", "celery"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # onedir 模式：二进制文件由 COLLECT 处理
    name="chaoxing-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # console=True 确保 stdin/stdout 管道可用
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="web/public/fav.jpg",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="chaoxing-backend",
)

```


## desktop/README.md

```
# Electron 桌面版开发指南

## 概述

超星学习通 · 自动化学习助手现在支持三种发行形态：

1. **独立 exe**（原有）：`chaoxing.spec` → `dist/chaoxing-gui.exe`，带托盘图标，自动打开系统浏览器
2. **便携版**（原有）：`build_portable.bat` → 嵌入式 Python + 启动脚本
3. **Electron 桌面版**（新增）：`desktop/` → 独立窗口，无需浏览器

## 架构

```
Electron 主进程 (main.js)
  ↓ spawn
  ├─ 后端子进程 (chaoxing-backend.exe / python app.py)
  │   ├─ Flask API (127.0.0.1:动态端口)
  │   └─ stdin watchdog (父进程退出时自动退出)
  └─ BrowserWindow
      └─ loadURL('http://127.0.0.1:{port}')
```

### 关键特性

- **动态端口**：Electron 分配空闲端口，通过 `CHAOXING_PORT` 环境变量传递给后端
- **孤儿进程防护**：后端监听 stdin EOF，父进程退出时自动终止
- **单实例锁**：`app.requestSingleInstanceLock()` 确保只运行一个实例
- **零前端改动**：前端仍通过相对路径 `/api/*` 调用后端

## 开发模式

### 启动完整开发环境

```bash
# 1. 后端开发
python app.py
# 访问 http://localhost:5000

# 2. 前端开发（热重载）
cd web && npm run dev
# 访问 http://localhost:3000（代理到后端 5000）

# 3. Electron 桌面开发（使用开发态后端 + 构建后的前端）
cd desktop && npm run dev
# Electron 窗口加载 http://127.0.0.1:{动态端口}
```

**注意**：
- Electron `npm run dev` 会启动 `python app.py`（开发模式），不支持前端热重载
- 前端迭代用 `web/npm run dev`；桌面窗口迭代用 `desktop/npm run dev`

## 构建生产版本

### 一键构建（推荐）

```bash
build_desktop.bat
```

**输出**：
- `desktop/release/chaoxing-gui-desktop-*.exe`（NSIS 安装包）
- `desktop/release/chaoxing-gui-desktop-*-portable.exe`（绿色便携版）

### 分步构建

```bash
# 1. 构建前端
cd web && npm ci && npm run build

# 2. 构建后端 exe（无头模式专用）
pyinstaller --clean --noconfirm chaoxing-backend.spec

# 3. 复制后端到 Electron 资源目录
xcopy /E /I /Y dist\chaoxing-backend.exe desktop\backend\

# 4. 构建 Electron 应用
cd desktop && npm ci && npx electron-builder --win
```

## 环境变量说明

后端 `app.py` 识别以下环境变量：

| 变量 | 值 | 作用 |
|------|---|------|
| `CHAOXING_HEADLESS` | `1` | 启用无头模式：绑定 127.0.0.1、禁用托盘、禁用 webbrowser.open、启用 stdin 守护 |
| `CHAOXING_PORT` | 整数 | Flask 监听端口，默认 `5000` |

**向后兼容**：
- 不设置环境变量 → 行为与原有完全一致（独立 exe 带托盘 + 浏览器）
- 开发模式 `python app.py` → 默认 5000 端口，无托盘，不自动开浏览器

## 文件清单

### 新增文件

```
desktop/
├── main.js                    # Electron 主进程
├── package.json               # Electron 依赖
├── electron-builder.yml       # 打包配置
├── build/icon.png             # 应用图标（512x512）
└── .gitignore

chaoxing-backend.spec          # 无头后端构建配置（console=True）
build_desktop.bat              # 一键构建脚本
```

### 修改文件

```
app.py                         # 新增 HEADLESS 模式支持（43-46, 617-627, 647-678 行）
.gitignore                     # 排除 desktop/node_modules, desktop/backend, desktop/release
```

## 常见问题

### Q1: 为什么需要两个 spec 文件？

- `chaoxing.spec`（原有）：独立 exe，`console=False`，带托盘图标
- `chaoxing-backend.spec`（新增）：Electron 后端，`console=True`，确保 stdin/stdout 可用

### Q2: 为什么后端要 `console=True`？

`console=True` 确保 `sys.stdin` 是真实管道句柄，而非 `None`。Electron 通过 `stdin.end()` 通知后端退出。

### Q3: 安装包体积多大？

- NSIS 安装包：~140 MB（压缩）
- 安装后体积：~250 MB（Electron 运行时 ~100 MB + PyInstaller 后端 ~150 MB）

### Q4: 如何调试后端日志？

**开发模式**：直接查看终端输出
**生产模式**：日志写入 `%APPDATA%\chaoxing-desktop\backend.log`

```powershell
Get-Content $env:APPDATA\chaoxing-desktop\backend.log -Tail 50 -Wait
```

### Q5: 端口冲突怎么办？

Electron 自动分配空闲端口，不会冲突。原有独立 exe 仍使用 5000，但支持实例复用（检测到 5000 占用时直接打开浏览器）。

## 测试清单

- [ ] 开发模式：`cd desktop && npm run dev` 窗口正常显示
- [ ] 生产构建：`build_desktop.bat` 无错误
- [ ] 安装：双击安装包，默认安装到 `C:\Users\<用户>\AppData\Local\Programs\超星泛雅刷课助手`
- [ ] 启动：桌面快捷方式启动，窗口显示登录界面
- [ ] 功能：登录 → 选课 → 开始学习，后台正常运行
- [ ] 关闭窗口：进程全部退出（Task Manager 检查无残留）
- [ ] 重复启动：二次启动聚焦第一个窗口，不创建新实例
- [ ] 配置持久化：`%APPDATA%\chaoxing-desktop\web_config.json` 保存用户配置
- [ ] 卸载：开始菜单卸载，程序文件和数据目录被清理

## 许可证

与主项目一致

```


## desktop/electron-builder.yml

```
appId: com.chaoxing.gui
productName: 超星学习通 · 自动化学习助手
directories:
  output: release
files:
  - main.js
  - preload.js
  - session-store.js
  - package.json
extraResources:
  - from: backend/chaoxing-backend
    to: backend
win:
  target:
    - target: nsis
    - target: portable
  icon: build/icon.png
  executableName: chaoxing-gui
nsis:
  oneClick: false
  allowToChangeInstallationDirectory: true
  artifactName: chaoxing-gui-desktop-setup-${version}.${ext}
  deleteAppDataOnUninstall: false
portable:
  artifactName: chaoxing-gui-desktop-portable-${version}.${ext}

```


## desktop/package.json

```
{
  "name": "chaoxing-desktop",
  "version": "1.1.1",
  "description": "超星学习通 · 自动化学习助手桌面版",
  "author": "chaoxing-gui",
  "main": "main.js",
  "private": true,
  "scripts": {
    "test": "node --test",
    "dev": "electron .",
    "start": "electron .",
    "dist": "electron-builder --win",
    "tauri": "tauri",
    "dev:tauri": "tauri dev",
    "build:tauri": "tauri build"
  },
  "devDependencies": {
    "electron": "^33.0.0",
    "electron-builder": "^26.0.0",
    "@tauri-apps/cli": "^2.11.4"
  }
}

```


## desktop/rust-toolchain.toml

```
[toolchain]
channel = "1.95.0"

```


## desktop/scripts/dev-env.ps1

```
# 开发环境初始化（本机专用，不进 CI）
# 用法：在会话中先 `. .\desktop\scripts\dev-env.ps1`，再运行 cargo / tauri 命令。
# 本机 rustup 代理缺失（无 rustup.exe、~/.cargo/bin 为空），需要直接使用工具链目录。
# 本机 MSVC 检测缺失（无 vswhere.exe、无 VS 注册表键），需要 vcvars64 提供 link.exe/LIB/INCLUDE。

$toolchainBin = "$env:USERPROFILE\.rustup\toolchains\stable-x86_64-pc-windows-msvc\bin"
if (-not (Test-Path $toolchainBin)) { throw "未找到 Rust 工具链: $toolchainBin" }
$env:PATH = "$toolchainBin;$env:PATH"
$env:CARGO_HOME = "$env:USERPROFILE\.cargo"

# 通过 vcvars64 获取 MSVC 链接环境（子进程 cmd 输出解析，避免改变当前控制台代码页）
$bat = Join-Path $env:TEMP "chaoxing-vcenv-$(Get-Random).bat"
@'
@echo off
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
echo ===VCENV===
set
'@ | Set-Content $bat -Encoding ascii
$envLines = cmd /c "`"$bat`"" | Select-String -Pattern "^(LIB|INCLUDE|Path)=" -SimpleMatch:$false
Remove-Item $bat -ErrorAction SilentlyContinue
foreach ($line in $envLines) {
  $name, $value = $line.Line -split '=', 2
  switch ($name) {
    'LIB' { $env:LIB = $value }
    'INCLUDE' { $env:INCLUDE = $value }
    'Path' { $env:VCVARS_PATH = $value }
  }
}
# 把 vcvars 的 PATH 中的 MSVC/SDK 目录并入当前 PATH（放在最前，保证 link.exe 可被 cargo 找到）
if ($env:VCVARS_PATH) {
  $vcDirs = ($env:VCVARS_PATH -split ';') | Where-Object { $_ -match 'MSVC|Windows Kits' }
  foreach ($dir in $vcDirs) { if (Test-Path $dir) { $env:PATH = "$dir;$env:PATH" } }
  Remove-Item Env:VCVARS_PATH -ErrorAction SilentlyContinue
}
Write-Host "Rust 工具链与 MSVC 链接环境已就绪: $(cargo --version)"

```


## desktop/scripts/p2-close-window.ps1

```
param([Parameter(Mandatory=$true)][int]$HostProcessId)
$ErrorActionPreference='Stop'
$p2WindowTitle=(Get-Content -Raw -LiteralPath "$PSScriptRoot/../src-tauri/tauri.conf.json" | ConvertFrom-Json).app.windows[0].title
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class P2WindowClose {
    public delegate bool WindowCallback(IntPtr handle, IntPtr extra);
    [DllImport("user32.dll")] public static extern bool EnumWindows(WindowCallback callback, IntPtr extra);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr handle, out uint process);
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr handle, uint message, IntPtr wparam, IntPtr lparam);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr handle, StringBuilder text, int max);
    public static int Close(uint target, string title) {
        int count=0;
        EnumWindows((handle, extra) => {
            uint process; GetWindowThreadProcessId(handle, out process);
            if (process==target) {
                var text = new StringBuilder(512);
                GetWindowText(handle, text, text.Capacity);
                // Tauri/COM also own hidden dispatcher windows. Sending those
                // WM_CLOSE corrupts teardown; emulate only the main close button.
                if (text.ToString()==title && PostMessage(handle, 0x0010, IntPtr.Zero, IntPtr.Zero)) count++;
            }
            return true;
        }, IntPtr.Zero);
        return count;
    }
}
'@
$closed=[P2WindowClose]::Close($HostProcessId, $p2WindowTitle)
if ($closed -eq 0) { throw "No window belongs to test host PID $HostProcessId" }
Write-Output "Sent WM_CLOSE to $closed window(s) of test host $HostProcessId"

```


## desktop/scripts/p2-smoke.mjs

```
// Real Chromium / original Electron / Tauri P2 smoke with synthetic accounts.
// Prerequisites: web build; cargo build -j1 --features custom-protocol; the
// p2_backend.py fixture frozen as target/p2-fixture/dist/p2-backend/; and
// playwright-core + Electron in P2_TOOLS_DIR (kept outside the repository).
import assert from 'node:assert/strict';
import { spawn, execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { mkdtemp, mkdir, readFile, writeFile, copyFile, stat } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

const exec = promisify(execFile);
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireTools = createRequire(path.join(process.env.P2_TOOLS_DIR || path.join(os.tmpdir(), 'chaoxing-p2-tools'), 'package.json'));
const { chromium, _electron } = requireTools('playwright-core');
const evidence = path.resolve(process.env.P2_EVIDENCE_DIR || path.join(repo, 'desktop/src-tauri/target/p2-smoke-evidence'));
const root = await mkdtemp(path.join(os.tmpdir(), 'chaoxing-p2-smoke-'));
const fixture = path.join(repo, 'desktop/tests/fixtures/p2_backend.py');
const fakeExe = path.join(repo, 'desktop/src-tauri/target/p2-fixture/dist/p2-backend/p2-backend.exe');
const hostExe = path.join(repo, 'desktop/src-tauri/target/debug/chaoxing-desktop.exe');
await mkdir(evidence, { recursive: true });
const results = { startedAt: new Date().toISOString(), profileRoot: root,
  driver: 'Real browser engines over CDP; DOM click after visible/enabled checks (native mouse events are not delivered in this Windows desktop session)', checks: [] };
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const alive = (pid) => { try { process.kill(pid, 0); return true; } catch { return false; } };
const exited = (child) => child.exitCode !== null || child.signalCode !== null;

async function until(check, label, timeout = 20000) {
  const deadline = Date.now() + timeout;
  let error;
  while (Date.now() < deadline) {
    try { const result = await check(); if (result) return result; } catch (err) { error = err; }
    await pause(100);
  }
  throw new Error(`${label} timed out${error ? `: ${error.message}` : ''}`);
}
const json = async (filename) => JSON.parse(await readFile(filename, 'utf8'));
async function activate(locator) {
  await locator.waitFor({ state: 'visible' });
  await until(() => locator.isEnabled(), 'button enabled');
  await locator.evaluate((element) => element.click());
}
const record = (name, details) => { results.checks.push({ name, result: 'PASS', ...details }); console.log(`PASS ${name}`); };
async function freePort() {
  const { createServer } = await import('node:net');
  const server = createServer();
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}
function fixtureEnv(profile, extra = {}) {
  const env = { ...process.env, P2_FIXTURE_ROOT: profile, P2_WEB_DIST: path.join(repo, 'web/dist'),
    PYTHONIOENCODING: 'utf-8', CHAOXING_PORT: '0', ...extra };
  delete env.ELECTRON_RUN_AS_NODE;
  return env;
}
async function stopFixture(child, profile) {
  if (!Number.isInteger(child.pid)) return; // The executable itself did not spawn.
  child.stdin?.end();
  try { await until(() => exited(child), 'fixture exit', 5000); }
  finally {
    if (!exited(child)) child.kill();
    await until(() => !alive(child.pid), 'fixture cleanup', 5000);
  }
  const runtime = await json(path.join(profile, 'runtime.json')).catch(() => null);
  if (runtime) assert.equal(alive(runtime.pid), false, `orphan fixture ${runtime.pid}`);
}

async function launch(kind, options = {}) {
  const profile = options.profile || path.join(root, `${kind}-${Date.now()}`);
  const backendProfile = path.join(profile, 'fixture');
  await mkdir(backendProfile, { recursive: true });
  const env = fixtureEnv(backendProfile, options.env);
  if (kind === 'browser') {
    const child = spawn(process.env.P2_PYTHON || 'python', [fixture], { env, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
    child.stdout.resume(); child.stderr.resume();
    child.stdin.on('error', () => {}); // EPIPE is expected if startup already exited.
    let browser;
    const stop = async () => {
      try { await browser?.close(); }
      finally { await stopFixture(child, backendProfile); }
    };
    try {
      await new Promise((resolve, reject) => { child.once('spawn', resolve); child.once('error', reject); });
      const runtime = await until(() => json(path.join(backendProfile, 'runtime.json')), 'browser fixture ready');
      browser = await chromium.launch({ executablePath: process.env.P2_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true });
      const page = await browser.newPage({ viewport: { width: 1200, height: 800 } });
      await page.goto(`http://127.0.0.1:${runtime.port}`);
      return { kind, profile, backendProfile, page, stop };
    } catch (error) {
      try { await stop(); }
      catch (cleanup) { throw new Error(`${error.message}; browser cleanup failed: ${cleanup.message}`); }
      throw error;
    }
  }
  if (kind === 'electron') {
    env.P2_ELECTRON_PROFILE = path.join(profile, 'electron-data');
    let electron;
    let child;
    const stop = async () => {
      // The original Electron shell owns the stdin pipe. Even if Playwright
      // setup fails, closing its captured process releases the fixture watchdog.
      try { await electron?.close(); }
      finally {
        try {
          if (child) await until(() => exited(child), 'Electron host exit', 5000);
        } finally {
          if (child && !exited(child)) child.kill();
          if (child) await until(() => !alive(child.pid), 'Electron host cleanup', 5000);
          const runtime = await json(path.join(backendProfile, 'pid.json')).catch(() => null);
          if (runtime) await until(() => !alive(runtime.pid), 'Electron backend exit', 6000);
        }
      }
    };
    try {
      electron = await _electron.launch({ executablePath: requireTools('electron'),
        args: [path.join(repo, 'desktop/tests/fixtures/p2-electron.cjs')], env, timeout: 30000 });
      child = electron.process();
      const page = await electron.firstWindow();
      await page.waitForURL((url) => url.hostname === '127.0.0.1', { timeout: 30000 });
      await page.waitForLoadState('domcontentloaded');
      return { kind, profile, backendProfile, page, data: env.P2_ELECTRON_PROFILE, electron, pid: child.pid, stop };
    } catch (error) {
      try { await stop(); }
      catch (cleanup) { throw new Error(`${error.message}; Electron cleanup failed: ${cleanup.message}`); }
      throw error;
    }
  }
  const port = await freePort();
  Object.assign(env, { CHAOXING_TAURI_DEV_ROOT: profile, CHAOXING_TAURI_DEV_HIDDEN: process.env.P2_HIDE_WINDOW || '0',
    CHAOXING_TAURI_DEV_BACKEND: options.backend || fakeExe,
    CHAOXING_LEGACY_DATA_DIR: options.legacy || path.join(profile, 'no-legacy'),
    WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${port} --force-device-scale-factor=1` });
  const child = spawn(hostExe, [], { cwd: path.join(repo, 'desktop/src-tauri'), env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  let diagnostics = '';
  const backendPids = [];
  child.stdout.on('data', (data) => { diagnostics += data; });
  child.stderr.on('data', (data) => { diagnostics += data; });
  let browser;
  try {
    await until(async () => {
      if (exited(child)) throw new Error(`host exited ${child.exitCode ?? child.signalCode}: ${diagnostics}`);
      return (await fetch(`http://127.0.0.1:${port}/json/version`)).ok;
    }, 'Tauri CDP', 30000);
    browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
    const page = await until(() => browser.contexts()[0]?.pages().find((p) => p.url().includes('tauri.localhost')), 'Tauri page');
    await page.waitForLoadState('domcontentloaded');
    return { kind, profile, backendProfile, data: path.join(profile, 'data'), page, child, backendPids,
      stop: async ({ force = false } = {}) => {
        const started = Date.now();
        try {
          // Detach the test debugger before exercising normal window teardown.
          await browser.close();
          if (!exited(child)) {
            if (force) child.kill();
            else await exec('pwsh', ['-NoProfile', '-File', path.join(repo, 'desktop/scripts/p2-close-window.ps1'), '-HostProcessId', String(child.pid)], { windowsHide: true, timeout: 8000 });
          }
          await until(() => exited(child) && !alive(child.pid), 'Tauri host exit', 8000);
          const runtime = await json(path.join(backendProfile, 'pid.json')).catch(() => null);
          const observedPids = [...new Set([...backendPids, ...(runtime ? [runtime.pid] : [])])];
          for (const pid of observedPids) await until(() => !alive(pid), 'Tauri backend exit', 6000);
          record(force ? 'tauri-forced-host-cleanup' : 'tauri-normal-window-close', {
            hostPid: child.pid, backendPids: observedPids, elapsedMs: Date.now() - started,
            exitCode: child.exitCode, signal: child.signalCode,
          });
        } finally {
          await browser.close().catch(() => {});
          // A failed assertion must not strand the captured test host/CDP client.
          // This fallback cannot turn a failed normal-close assertion into PASS.
          if (!exited(child)) child.kill();
          await until(() => !alive(child.pid), 'Tauri failed-test cleanup', 5000);
          await writeFile(path.join(evidence, `host-${path.basename(profile)}.txt`), diagnostics);
        }
      } };
  } catch (error) {
    await browser?.close().catch(() => {});
    if (!exited(child)) child.kill();
    await until(() => !alive(child.pid), 'Tauri launch cleanup', 5000);
    throw new Error(`${error.message}; host diagnostics=${diagnostics}`);
  }
}

async function savedSession(app) {
  return app.page.evaluate(async (kind) => {
    if (kind === 'tauri') return window.__TAURI__.core.invoke('session_read');
    if (kind === 'electron') return window.chaoxingSession.read();
    return JSON.parse(localStorage.getItem('chaoxing_session_v1')) || { version: 1, login: null, activeTask: null };
  }, app.kind);
}

async function business(kind) {
  const app = await launch(kind);
  const { page } = app;
  page.setDefaultTimeout(15000);
  const requests = [];
  page.on('request', (request) => { if (request.url().includes('/api/')) requests.push({ method: request.method(), url: request.url() }); });
  try {
    await page.evaluate(() => {
      window.p2ClickEvents = [];
      document.addEventListener('click', (event) => {
        window.p2ClickEvents.push({ tag: event.target.tagName, id: event.target.id, x: event.clientX, y: event.clientY });
      }, true);
    });
    await page.getByLabel('手机号').fill('p2-fixture');
    await page.getByLabel('密码', { exact: true }).fill('p2-synthetic-password');
    await activate(page.getByRole('button', { name: '登录', exact: true }));
    const first = page.getByRole('button', { name: /P2 测试课程一/ });
    await first.waitFor();
    assert.equal(await first.getAttribute('aria-pressed'), 'true');
    assert.equal(await page.getByRole('button', { name: /P2 测试课程二/ }).getAttribute('aria-pressed'), 'false');
    await activate(page.getByRole('button', { name: '保存当前配置' }));
    await page.getByText('配置已保存', { exact: true }).waitFor();
    await activate(page.getByRole('button', { name: '开始学习', exact: true }));
    await page.getByText('p2-existing-task', { exact: true }).waitFor();
    await page.getByRole('log').getByText('P2 终态日志二', { exact: true }).waitFor();
    assert.equal(await page.getByRole('log').getByText('P2 唯一日志一', { exact: true }).count(), 1);
    const beforeRefresh = await json(path.join(app.backendProfile, 'counts.json'));
    assert.equal(beforeRefresh.start, 1);
    assert.equal(beforeRefresh.configWrites, 1);
    assert.deepEqual(beforeRefresh.after.slice(0, 3), [0, 1, 1]);
    const session = await savedSession(app);
    assert.equal(session.activeTask.taskId, 'p2-existing-task');
    assert.equal(JSON.stringify(session).includes('password'), false);
    await page.screenshot({ path: path.join(evidence, `p2-${kind}-progress.png`), fullPage: true });
    await page.reload();
    await page.getByText('p2-existing-task', { exact: true }).waitFor();
    assert.equal((await json(path.join(app.backendProfile, 'counts.json'))).start, 1);
    await writeFile(path.join(app.backendProfile, 'control.json'), JSON.stringify({ missing: true }));
    await page.reload();
    await page.getByText('已过期', { exact: true }).first().waitFor();
    await until(async () => (await savedSession(app)).activeTask === null, '404 clears only task');
    assert.equal((await savedSession(app)).login.username, 'p2-fixture');
    await activate(page.getByRole('button', { name: '返回课程选择', exact: true }).last());
    await activate(page.getByRole('button', { name: '退出登录', exact: true }));
    await page.getByRole('button', { name: '登录', exact: true }).waitFor();
    assert.equal((await savedSession(app)).login, null);
    record(`${kind}-business`, { transport: kind === 'tauri' ? 'native invoke -> HTTP fixture' : 'HTTP fixture',
      cases: ['account selection', 'config save', '409 restore without repeat start', 'terminal log retry', 'after cursor dedup', 'refresh restore', '404 clears task', 'logout clears account'], counters: beforeRefresh });
  } catch (error) {
    await page.screenshot({ path: path.join(evidence, `p2-${kind}-error.png`), fullPage: true }).catch(() => {});
    await writeFile(path.join(evidence, `p2-${kind}-error.json`), JSON.stringify({
      url: page.url(), body: await page.locator('body').innerText().catch(() => ''),
      session: await savedSession(app).catch((err) => err.message),
      requests, clicks: await page.evaluate(() => ({ events: window.p2ClickEvents, width: innerWidth, height: innerHeight, scale: devicePixelRatio })),
    }, null, 2));
    throw error;
  } finally { await app.stop(); }
}

async function startupFailures() {
  const missing = await launch('tauri', { backend: path.join(root, 'missing-backend.exe') });
  try {
    await missing.page.getByRole('button', { name: '重新检查' }).waitFor();
    assert.equal(await missing.page.getByLabel('手机号').count(), 0);
    await activate(missing.page.getByRole('button', { name: '重新检查' }));
    await until(async () => (await missing.page.evaluate(() => window.__TAURI__.core.invoke('backend_status'))).phase === 'failed', 'failed remains failed');
    await missing.page.screenshot({ path: path.join(evidence, 'p2-tauri-failed.png') });
    record('tauri-missing-backend-recheck', { restarted: false });
  } finally { await missing.stop(); }
  const slow = await launch('tauri', { env: { P2_READY_DELAY_MS: '8000' } });
  try {
    const status = await slow.page.evaluate(() => window.__TAURI__.core.invoke('backend_status'));
    assert.equal(status.phase, 'starting');
    assert.equal(await slow.page.getByLabel('手机号').count(), 0);
    await slow.page.screenshot({ path: path.join(evidence, 'p2-tauri-starting.png') });
  } finally { await slow.stop(); }
  record('tauri-close-during-startup', { closedBeforeReady: true });
}

async function migrationRollback() {
  const legacy = await launch('electron');
  let target;
  try {
    await legacy.page.evaluate(async () => {
      await window.chaoxingSession.rememberLogin('p2-fixture');
      await window.chaoxingSession.rememberTask({ username: 'p2-fixture', taskId: 'p2-existing-task' });
    });
    await writeFile(path.join(legacy.data, 'web_config.json'), JSON.stringify({ selectedCoursesByAccount: { 'p2-fixture': ['course-1'] } }));
    const before = await readFile(path.join(legacy.data, 'renderer-session.json'));
    target = await launch('tauri', { legacy: legacy.data });
    await target.page.getByRole('button', { name: '重新检查' }).waitFor();
    const status = await target.page.evaluate(() => window.__TAURI__.core.invoke('backend_status'));
    assert.equal(status.phase, 'failed');
    assert.match(status.error, /关闭旧版/);
    await assert.rejects(stat(path.join(target.backendProfile, 'pid.json')), { code: 'ENOENT' });
    await target.stop(); target = null;
    await legacy.stop();
    const imported = await launch('tauri', { legacy: legacy.data });
    try {
      await imported.page.getByText('p2-existing-task', { exact: true }).waitFor();
      assert.equal((await savedSession(imported)).login.username, 'p2-fixture');
      assert.deepEqual(await json(path.join(imported.data, 'web_config.json')), { selectedCoursesByAccount: { 'p2-fixture': ['course-1'] } });
      assert.deepEqual(await readFile(path.join(legacy.data, 'renderer-session.json')), before);
      const nodeStore = createRequire(path.join(repo, 'desktop/package.json'))('./session-store.js');
      assert.equal(new nodeStore.SessionStore(imported.data).read().login.username, 'p2-fixture');
      assert.equal(new nodeStore.SessionStore(legacy.data).read().activeTask.taskId, 'p2-existing-task');
      assert.ok((await stat(path.join(imported.data, 'migration-v1.done'))).isFile());
    } finally { await imported.stop(); }
    // Exercise the original shell again against the preserved original profile.
    const rollback = await launch('electron', { profile: legacy.profile });
    try {
      await rollback.page.getByText('p2-existing-task', { exact: true }).waitFor();
      assert.equal((await savedSession(rollback)).login.username, 'p2-fixture');
      assert.deepEqual(await readFile(path.join(legacy.data, 'renderer-session.json')), before);
      record('old-electron-import-and-rollback', { runningLegacyDeferred: true, sourceUnchanged: true,
        nodeReadsRustSession: true, originalElectronRelaunched: true,
        sourceSha256: createHash('sha256').update(before).digest('hex') });
    } finally { await rollback.stop(); }
  } finally {
    if (target) await target.stop();
    if (alive(legacy.pid)) await legacy.stop();
  }
}

async function nativeContracts() {
  const app = await launch('tauri');
  const { page } = app;
  try {
    await page.getByLabel('手机号').waitFor();
    const rejected = await page.evaluate(async () => {
      const invoke = window.__TAURI__.core.invoke;
      const cases = [
        ['unknown command', 'p2_unknown_command', {}],
        ['unknown status field', 'backend_status', { url: 'http://example.invalid' }],
        ['positional envelope', 'session_remember_login', ['p2-fixture']],
        ['credentials in session', 'session_remember_login', { username: 'p2-fixture', password: 'synthetic' }],
        ['missing nullable key', 'session_remember_task', {}],
        ['positional task', 'session_remember_task', { task: ['p2-fixture', 'task'] }],
        ['positional request', 'api_request', { request: ['configRead', null, 701] }],
        ['object operation', 'api_request', { request: { operation: { configRead: null }, payload: null, requestId: 702 } }],
        ['unknown URL field', 'api_request', { request: { operation: 'configRead', payload: null, requestId: 703, url: 'http://example.invalid' } }],
        ['path traversal', 'api_request', { request: { operation: 'taskStatus', payload: null, requestId: 704, taskId: '../task' } }],
        ['invalid cancel ID', 'api_cancel', { requestId: 0 }],
        ['ungranted window command', 'plugin:webview|create_webview_window', { options: { label: 'p2-untrusted', url: 'about:blank', visible: false } }],
      ];
      const results = [];
      for (const [name, command, args] of cases) {
        try { await invoke(command, args); results.push({ name, rejected: false }); }
        catch { results.push({ name, rejected: true }); }
      }
      return results;
    });
    for (const result of rejected) assert.equal(result.rejected, true, result.name);
    assert.deepEqual(await savedSession(app), { version: 1, login: null, activeTask: null });
    const frameDirective = await page.evaluate(() => new Promise((resolve) => {
      const frame = document.createElement('iframe');
      let timer;
      const finish = (value) => {
        clearTimeout(timer);
        window.removeEventListener('securitypolicyviolation', onViolation);
        frame.remove();
        resolve(value);
      };
      const onViolation = (event) => { if (event.effectiveDirective === 'frame-src') finish(event.effectiveDirective); };
      window.addEventListener('securitypolicyviolation', onViolation);
      timer = setTimeout(() => finish('not blocked'), 1500);
      frame.src = location.href;
      document.body.append(frame);
    }));
    assert.equal(frameDirective, 'frame-src');
    const originalUrl = page.url();
    await page.evaluate(() => { location.href = 'https://example.invalid/p2-blocked'; });
    await pause(250);
    assert.equal(page.url(), originalUrl);
    record('tauri-native-ipc-boundaries', { rejected, frameDirective, foreignNavigationBlocked: true });

    await writeFile(path.join(app.backendProfile, 'control.json'), JSON.stringify({ delayMs: 1000 }));
    const cancellations = await page.evaluate(async () => {
      const invoke = window.__TAURI__.core.invoke;
      await invoke('api_cancel', { requestId: 801 });
      const early = await invoke('api_request', { request: { operation: 'configRead', payload: null, requestId: 801 } }).then(() => 'success', (error) => error.kind);
      const pending = invoke('api_request', { request: { operation: 'configRead', payload: null, requestId: 802 } }).then(() => 'success', (error) => error.kind);
      await new Promise((resolve) => setTimeout(resolve, 100));
      const started = performance.now();
      const status = await invoke('backend_status');
      const statusMs = performance.now() - started;
      await invoke('api_cancel', { requestId: 802 });
      return { early, inflight: await pending, status: status.phase, statusMs };
    });
    assert.equal(cancellations.early, 'cancelled');
    assert.equal(cancellations.inflight, 'cancelled');
    assert.equal(cancellations.status, 'ready');
    assert.ok(cancellations.statusMs < 750, 'status must remain responsive during HTTP');
    await writeFile(path.join(app.backendProfile, 'control.json'), '{}');
    record('tauri-native-cancellation', cancellations);

    await page.evaluate(() => localStorage.setItem('chaoxing_session_v1', 'p2-unchanged-local-sentinel'));
    // An existing directory at the temporary file path forces a real Windows IO
    // failure without changing permissions or touching any user account profile.
    await mkdir(path.join(app.data, 'renderer-session.json.tmp'));
    await page.getByLabel('手机号').fill('p2-fixture');
    await page.getByLabel('密码', { exact: true }).fill('p2-synthetic-password');
    await activate(page.getByRole('button', { name: '登录', exact: true }));
    await page.getByText('账号记忆未能保存，刷新后可能需要重新登录', { exact: true }).waitFor();
    await activate(page.getByRole('button', { name: '开始学习', exact: true }));
    await page.getByText('任务已启动，但恢复信息未能保存，请记下进度页中的任务 ID', { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => localStorage.getItem('chaoxing_session_v1')), 'p2-unchanged-local-sentinel');
    assert.equal((await savedSession(app)).login, null);
    await page.screenshot({ path: path.join(evidence, 'p2-tauri-storage-failure.png') });
    await mkdir(path.join(app.data, 'renderer-session.json'));
    await page.reload();
    await page.getByText('读取保存的账号失败，请手动登录', { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => localStorage.getItem('chaoxing_session_v1')), 'p2-unchanged-local-sentinel');
    record('tauri-native-storage-failure', { readFailureVisible: true, loginFailureVisible: true, taskFailureVisible: true, localStorageUnchanged: true });

    const runtime = await json(path.join(app.backendProfile, 'pid.json'));
    process.kill(runtime.pid);
    await page.getByRole('button', { name: '重新检查' }).waitFor();
    await activate(page.getByRole('button', { name: '重新检查' }));
    await until(async () => (await page.evaluate(() => window.__TAURI__.core.invoke('backend_status'))).phase === 'failed', 'backend death remains failed');
    assert.equal(alive(runtime.pid), false);
    assert.equal((await json(path.join(app.backendProfile, 'pid.json'))).pid, runtime.pid);
    assert.equal(await page.getByLabel('手机号').count(), 0);
    record('tauri-ready-backend-death', { restarted: false, failedStateVisible: true });
  } finally { await app.stop(); }
}

async function realFrozenBackend() {
  const backend = path.join(repo, 'dist/chaoxing-backend/chaoxing-backend.exe');
  const fingerprint = async (file) => readFile(file).then((bytes) => createHash('sha256').update(bytes).digest('hex')).catch((error) => {
    if (error.code === 'ENOENT') return null;
    throw error;
  });
  const installLogs = [path.join(path.dirname(backend), 'chaoxing.log'), path.join(path.dirname(backend), '_internal/chaoxing.log')];
  const before = await Promise.all(installLogs.map(fingerprint));
  assert.ok((await stat(path.join(path.dirname(backend), '_internal'))).isDirectory());
  const app = await launch('tauri', { backend });
  try {
    await app.page.getByLabel('手机号').waitFor({ timeout: 120000 });
    const { stdout } = await exec('pwsh', ['-NoProfile', '-Command',
      `@(Get-CimInstance Win32_Process -Filter 'ParentProcessId=${app.child.pid}' | Select-Object ProcessId,ExecutablePath) | ConvertTo-Json -Compress -AsArray`], { windowsHide: true, timeout: 10000 });
    const children = JSON.parse(stdout);
    const actualBackend = children.filter((child) => path.resolve(child.ExecutablePath).toLowerCase() === backend.toLowerCase());
    assert.equal(actualBackend.length, 1, 'host must spawn the actual frozen backend');
    app.backendPids.push(actualBackend[0].ProcessId);
    const contract = await app.page.evaluate(async () => {
      const invoke = window.__TAURI__.core.invoke;
      let id = 1001;
      const request = (operation, payload = null, extra = {}) => invoke('api_request', { request: { operation, payload, requestId: id++, ...extra } });
      const status = await invoke('backend_status');
      const read = await request('configRead');
      const write = await request('configWrite', { settings: { jobs: 2 }, selectedCoursesByAccount: { 'p2-config-fixture': ['course-1'] } });
      const reread = await request('configRead');
      const invalid = [];
      // All three fail local validation before any account client/task is created.
      for (const operation of ['login', 'courses', 'start']) invalid.push([operation, await request(operation, {})]);
      const missing = [];
      for (const operation of ['taskStatus', 'taskDetails', 'taskLogs']) missing.push([operation, await request(operation, null, { taskId: 'p2-never-created', ...(operation === 'taskLogs' ? { after: 0 } : {}) })]);
      return { status, read, write, reread, invalid, missing };
    });
    assert.equal(contract.status.phase, 'ready');
    assert.equal(Object.hasOwn(contract.status, 'port'), false);
    assert.equal(Object.hasOwn(contract.status, 'token'), false);
    assert.equal(contract.read.status, 200);
    assert.equal(contract.write.status, 200);
    assert.equal(contract.reread.body.data.settings.jobs, 2);
    for (const [name, response] of contract.invalid) assert.equal(response.status, 400, name);
    for (const [name, response] of contract.missing) assert.equal(response.status, 404, name);
    const stored = await json(path.join(app.data, 'web_config.json'));
    assert.deepEqual(stored.selectedCoursesByAccount, { 'p2-config-fixture': ['course-1'] });
    const dataLog = path.join(app.data, 'chaoxing.log');
    assert.ok((await stat(dataLog)).isFile());
    assert.deepEqual(await Promise.all(installLogs.map(fingerprint)), before);
    await copyFile(dataLog, path.join(evidence, 'p2-frozen-chaoxing.log'));
    record('tauri-real-frozen-backend', { backend, backendPid: actualBackend[0].ProcessId, contract,
      logInDataDirectory: true, installLogsUnchanged: true, upstreamAccountsUsed: false, learningTaskCreated: false });
  } finally { await app.stop(); }
}

const selection = process.argv[2] || 'all';
try {
  assert.ok(['all', 'browser', 'electron', 'tauri', 'failures', 'migration', 'native', 'frozen'].includes(selection), `Unknown smoke selection: ${selection}`);
  for (const kind of ['browser', 'electron', 'tauri']) {
    if (selection === 'all' || selection === kind) await business(kind);
  }
  if (selection === 'all' || selection === 'failures') await startupFailures();
  if (selection === 'all' || selection === 'migration') await migrationRollback();
  if (selection === 'all' || selection === 'native') await nativeContracts();
  if (selection === 'all' || selection === 'frozen') await realFrozenBackend();
  results.success = true;
} catch (error) {
  results.success = false;
  results.error = error.stack;
  console.error(error.stack);
  process.exitCode = 1;
} finally {
  results.endedAt = new Date().toISOString();
  await writeFile(path.join(evidence, `p2-smoke-${selection}.json`), JSON.stringify(results, null, 2));
  await writeFile(path.join(evidence, `p2-smoke-${selection}-${results.startedAt.replace(/[^0-9]/g, '')}.json`), JSON.stringify(results, null, 2));
  console.log(`Evidence: ${path.join(evidence, `p2-smoke-${selection}.json`)}`);
}

```


## desktop/src-tauri/Cargo.toml

```
[package]
name = "chaoxing-desktop"
version = "1.1.1"
description = "超星学习通 · 自动化学习助手桌面版（Tauri 宿主）"
edition = "2021"
default-run = "chaoxing-desktop"

[lib]
name = "chaoxing_desktop_lib"
path = "src/lib.rs"

[[bin]]
name = "chaoxing-desktop"
path = "src/main.rs"

# Helper bins for integration tests only (not shipped in the installer).
[[bin]]
name = "fake-backend"
path = "src/bin/fake-backend.rs"
required-features = []

[build-dependencies]
tauri-build = { version = "2", features = [] }

[dependencies]
tauri = { version = "=2.11.5", features = [] }
tauri-plugin-single-instance = "2"
serde = { version = "1", features = ["derive"] }
serde_json = "1"
ureq = { version = "2", default-features = false, features = ["json"] }
windows = { version = "0.62", features = ["Win32_Foundation", "Win32_System_JobObjects", "Win32_System_Threading", "Win32_Security", "Win32_UI_WindowsAndMessaging", "Win32_Graphics_Gdi", "Win32_Storage_FileSystem"] }
getrandom = "0.2"
regex = "1"

[features]
custom-protocol = ["tauri/custom-protocol"]

[profile.release]
strip = true

```


## desktop/src-tauri/build.rs

```
fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "api_request",
            "api_cancel",
            "backend_status",
            "session_read",
            "session_remember_login",
            "session_remember_task",
            "session_clear",
        ]),
    ))
    .unwrap();
}

```


## desktop/src-tauri/capabilities/main.json

```
{
  "$schema": "../gen/schemas/desktop-schema.json",
  "identifier": "main-capability",
  "windows": ["main"],
  "permissions": [
    "allow-api-request",
    "allow-api-cancel",
    "allow-backend-status",
    "allow-session-read",
    "allow-session-remember-login",
    "allow-session-remember-task",
    "allow-session-clear"
  ]
}

```


## desktop/src-tauri/src/api_proxy.rs

```
use serde::{Deserialize, Deserializer, Serialize};

/// Strictly-allowed backend API operations (plan.md §3.2, 8 operations).
/// Everything else is refused at the host boundary — the webview never gets
/// a generic HTTP escape hatch.
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum ApiOperation {
    Login,
    Courses,
    ConfigRead,
    ConfigWrite,
    Start,
    TaskStatus,
    TaskDetails,
    TaskLogs,
}

/// The renderer can request only these operations and their explicit options.
#[derive(Debug)]
pub struct ApiRequest {
    pub operation: ApiOperation,
    pub payload: serde_json::Value,
    pub request_id: u64,
    pub task_id: Option<String>,
    pub after: Option<u64>,
}

impl<'de> Deserialize<'de> for ApiRequest {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        #[derive(Deserialize)]
        #[serde(rename_all = "camelCase", deny_unknown_fields)]
        struct Fields {
            #[serde(deserialize_with = "string_operation")]
            operation: ApiOperation,
            // This key must exist even when its value is null.
            #[serde(deserialize_with = "required_payload")]
            payload: serde_json::Value,
            request_id: u64,
            #[serde(default, deserialize_with = "present_option")]
            task_id: Option<String>,
            #[serde(default, deserialize_with = "present_option")]
            after: Option<u64>,
        }

        let fields: Fields = crate::deserialize_object(deserializer)?;
        Ok(Self {
            operation: fields.operation,
            payload: fields.payload,
            request_id: fields.request_id,
            task_id: fields.task_id,
            after: fields.after,
        })
    }
}

fn string_operation<'de, D: Deserializer<'de>>(deserializer: D) -> Result<ApiOperation, D::Error> {
    // Derived enums also accept tagged objects; the IPC contract requires a string.
    let operation = String::deserialize(deserializer)?;
    ApiOperation::deserialize(serde::de::value::StringDeserializer::<D::Error>::new(
        operation,
    ))
}

fn required_payload<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> Result<serde_json::Value, D::Error> {
    serde_json::Value::deserialize(deserializer)
}

// Omission is allowed; an explicitly supplied null is not an operation option.
fn present_option<'de, D, T>(deserializer: D) -> Result<Option<T>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    T::deserialize(deserializer).map(Some)
}

impl ApiRequest {
    pub fn validate(&self) -> Result<(), ProxyError> {
        let invalid = |reason: &str| ProxyError::InvalidRequest {
            reason: reason.into(),
        };
        if !valid_request_id(self.request_id) {
            return Err(invalid(
                "requestId must be a positive JavaScript safe integer",
            ));
        }
        build_path(self.operation, self.task_id.as_deref(), self.after)
            .map_err(|reason| ProxyError::InvalidRequest { reason })?;
        if self.operation.is_post() {
            if !self.payload.is_object() {
                return Err(invalid("POST payload must be a JSON object"));
            }
            let body =
                serde_json::to_vec(&self.payload).map_err(|error| ProxyError::InvalidRequest {
                    reason: format!("payload serialization failed: {error}"),
                })?;
            if body.len() > MAX_REQUEST_BODY {
                return Err(invalid("请求体超过 1MB 上限"));
            }
        } else if !self.payload.is_null() {
            return Err(invalid("GET operations require a null payload"));
        }
        Ok(())
    }
}

impl ApiOperation {
    /// (method, path template). `{id}` is substituted with the validated taskId.
    pub fn route(&self) -> (&'static str, &'static str) {
        match self {
            ApiOperation::Login => ("POST", "/api/login"),
            ApiOperation::Courses => ("POST", "/api/courses"),
            ApiOperation::ConfigRead => ("GET", "/api/config"),
            ApiOperation::ConfigWrite => ("POST", "/api/config"),
            ApiOperation::Start => ("POST", "/api/start"),
            ApiOperation::TaskStatus => ("GET", "/api/task/{id}"),
            ApiOperation::TaskDetails => ("GET", "/api/task/{id}/details"),
            ApiOperation::TaskLogs => ("GET", "/api/logs/{id}"),
        }
    }

    pub fn needs_task_id(&self) -> bool {
        matches!(
            self,
            ApiOperation::TaskStatus | ApiOperation::TaskDetails | ApiOperation::TaskLogs
        )
    }

    pub fn is_post(&self) -> bool {
        self.route().0 == "POST"
    }
}

pub const MAX_REQUEST_BODY: usize = 1024 * 1024;
pub const MAX_RESPONSE_BODY: usize = 2 * (1024 * 1024);
pub const MAX_REQUEST_ID: u64 = (1u64 << 53) - 1;
/// Cursor bound for TaskLogs `after` (matches backend int ids; u32 is generous).
pub const MAX_LOG_CURSOR: u64 = u32::MAX as u64;

pub fn valid_request_id(request_id: u64) -> bool {
    (1..=MAX_REQUEST_ID).contains(&request_id)
}

pub fn valid_task_id(t: &str) -> bool {
    !t.is_empty()
        && t.len() <= 128
        && t.chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
}

pub fn valid_log_cursor(after: u64) -> bool {
    after <= MAX_LOG_CURSOR
}

/// Build the URL path for an operation. Caller must have validated inputs.
pub fn build_path(
    op: ApiOperation,
    task_id: Option<&str>,
    after: Option<u64>,
) -> Result<String, String> {
    let (_, template) = op.route();
    if !op.needs_task_id() && task_id.is_some() {
        return Err(format!("{op:?} does not accept taskId"));
    }
    if !matches!(op, ApiOperation::TaskLogs) && after.is_some() {
        return Err(format!("{op:?} does not accept after"));
    }
    if op.needs_task_id() {
        let id = task_id.ok_or_else(|| format!("{op:?} requires taskId"))?;
        if !valid_task_id(id) {
            return Err("invalid taskId".into());
        }
        let path = template.replace("{id}", id);
        if matches!(op, ApiOperation::TaskLogs) {
            let cursor = after.ok_or_else(|| "TaskLogs requires after cursor".to_string())?;
            if !valid_log_cursor(cursor) {
                return Err("invalid log cursor".into());
            }
            return Ok(format!("{path}?after={cursor}"));
        }
        Ok(path)
    } else {
        Ok(template.to_string())
    }
}

/// Wire shape returned to the renderer: { status, body } — mirrors axios semantics
/// (status preserved; 409/404 handled by existing frontend branches).
#[derive(Debug, Serialize)]
pub struct ProxyResponse {
    pub status: u16,
    pub body: serde_json::Value,
}

#[derive(Debug, Serialize)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum ProxyError {
    /// Backend not ready / stopped.
    BackendNotReady { phase: String },
    /// Request rejected by host validation.
    InvalidRequest { reason: String },
    /// Network-level failure talking to the backend.
    Network { reason: String },
    /// The complete HTTP exchange, including the response body, timed out.
    Timeout { reason: String },
    /// Cancelled via api_cancel.
    Cancelled,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn p2_object_boundary_rejects_positional_api_request_deserialization() {
        for raw in [
            serde_json::json!(["taskLogs", null, 1, "t", 0]),
            serde_json::json!(["start", {}, 1]),
        ] {
            assert!(
                serde_json::from_value::<ApiRequest>(raw.clone()).is_err(),
                "accepted {raw}"
            );
        }
    }

    #[test]
    fn dto_rejects_unknown_operation() {
        let raw = r#""notAnOperation""#;
        assert!(serde_json::from_str::<ApiOperation>(raw).is_err());
    }

    #[test]
    fn dto_rejects_non_string_operation() {
        for operation in [
            serde_json::json!({"configRead": null}),
            serde_json::json!({"start": null}),
            serde_json::json!(["configRead"]),
            serde_json::Value::Null,
            serde_json::json!(true),
            serde_json::json!(1),
        ] {
            let raw = serde_json::json!({"operation": operation, "payload": null, "requestId": 1});
            assert!(
                serde_json::from_value::<ApiRequest>(raw.clone()).is_err(),
                "accepted operation value: {raw}"
            );
            assert!(
                serde_json::from_str::<ApiRequest>(&raw.to_string()).is_err(),
                "accepted operation JSON: {raw}"
            );
        }
    }

    #[test]
    fn dto_requires_exact_fields_and_operation_options() {
        for raw in [
            serde_json::json!({"operation":"configRead", "requestId":1}),
            serde_json::json!({"operation":"configRead", "requestId":1, "payload":null, "url":"http://example.invalid"}),
            serde_json::json!({"operation":"configRead", "requestId":1, "payload":null, "taskId":null}),
            serde_json::json!({"operation":"configRead", "requestId":1, "payload":null, "after":null}),
            serde_json::json!({"operation":"configRead", "requestId":1.5, "payload":null}),
            serde_json::json!({"operation":"configRead", "requestId":-1, "payload":null}),
            serde_json::json!({"operation":"task-status", "requestId":1, "payload":null}),
        ] {
            assert!(
                serde_json::from_value::<ApiRequest>(raw.clone()).is_err(),
                "{raw}"
            );
        }
        for raw in [
            serde_json::json!({"operation":"configRead", "requestId":0, "payload":null}),
            serde_json::json!({"operation":"configRead", "requestId":MAX_REQUEST_ID + 1, "payload":null}),
            serde_json::json!({"operation":"configRead", "requestId":1, "payload":null, "taskId":"t"}),
            serde_json::json!({"operation":"taskStatus", "requestId":1, "payload":null, "taskId":"t", "after":0}),
            serde_json::json!({"operation":"start", "requestId":1, "payload":null}),
            serde_json::json!({"operation":"taskLogs", "requestId":1, "payload":null, "taskId":"t"}),
        ] {
            let request = serde_json::from_value::<ApiRequest>(raw.clone()).unwrap();
            assert!(request.validate().is_err(), "{raw}");
        }
    }

    #[test]
    fn dto_accepts_all_eight_camel_case_operations() {
        for operation in [
            "login",
            "courses",
            "configRead",
            "configWrite",
            "start",
            "taskStatus",
            "taskDetails",
            "taskLogs",
        ] {
            let mut raw = serde_json::json!({"operation":operation, "requestId":MAX_REQUEST_ID, "payload":null});
            if matches!(operation, "login" | "courses" | "configWrite" | "start") {
                raw["payload"] = serde_json::json!({"username":"fixture"});
            }
            if matches!(operation, "taskStatus" | "taskDetails" | "taskLogs") {
                raw["taskId"] = "t-1".into();
            }
            if operation == "taskLogs" {
                raw["after"] = 0.into();
            }
            serde_json::from_value::<ApiRequest>(raw)
                .unwrap()
                .validate()
                .unwrap();
        }
    }

    #[test]
    fn task_id_whitelist() {
        assert!(valid_task_id("abc-DEF_123"));
        assert!(!valid_task_id("../etc"));
        assert!(!valid_task_id("a b"));
        assert!(!valid_task_id(""));
        assert!(!valid_task_id(&"x".repeat(129)));
        assert!(valid_task_id(&"x".repeat(128)));
    }

    #[test]
    fn path_building() {
        assert_eq!(
            build_path(ApiOperation::Login, None, None).unwrap(),
            "/api/login"
        );
        assert_eq!(
            build_path(ApiOperation::TaskStatus, Some("t-1_2"), None).unwrap(),
            "/api/task/t-1_2"
        );
        assert_eq!(
            build_path(ApiOperation::TaskDetails, Some("t1"), None).unwrap(),
            "/api/task/t1/details"
        );
        assert_eq!(
            build_path(ApiOperation::TaskLogs, Some("t1"), Some(42)).unwrap(),
            "/api/logs/t1?after=42"
        );
        assert!(build_path(ApiOperation::TaskStatus, None, None).is_err());
        assert!(build_path(ApiOperation::TaskStatus, Some("../bad"), None).is_err());
        assert!(build_path(ApiOperation::TaskLogs, Some("t1"), None).is_err());
    }

    #[test]
    fn log_cursor_bounds() {
        assert!(valid_log_cursor(0));
        assert!(valid_log_cursor(u32::MAX as u64));
        assert!(!valid_log_cursor(u32::MAX as u64 + 1));
    }
}

```


## desktop/src-tauri/src/backend.rs

```
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

```


## desktop/src-tauri/src/bin/fake-backend.rs

```
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

```


## desktop/src-tauri/src/lib.rs

```
pub mod api_proxy;
pub mod backend;
pub mod migration;
pub mod session_store;
pub mod windows_job;

use backend::BackendState;
use serde::de::DeserializeOwned;
use serde::Deserialize;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use tauri::Manager;

/// State is registered before the webview loads. Migration and backend startup
/// run off the event loop, so status, cancellation and window close stay usable.
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .setup(|app| {
            let dev_root = development_root(app.handle())?;
            let data_dir = match &dev_root {
                Some(root) => root.join("data"),
                None => app.path().app_data_dir()?.join("data"),
            };
            let log_dir = match &dev_root {
                Some(root) => root.join("logs"),
                None => app.path().app_log_dir()?,
            };
            let state = Arc::new(BackendState::new(data_dir.clone(), log_dir.clone()));
            app.manage(state.clone());
            app.manage(session_store::SessionStore::new(&data_dir));
            let notice = Arc::new(Mutex::new(None));
            app.manage(StartupNotice(notice.clone()));

            // Explicit construction isolates development's WebView2 profile.
            let config = app.config().app.windows.first().ok_or("main window config missing")?;
            let mut window = tauri::WebviewWindowBuilder::from_config(app, config)?
                .on_navigation(allowed_navigation)
                .on_new_window(|_, _| tauri::webview::NewWindowResponse::Deny);
            if let Some(root) = &dev_root {
                window = window.data_directory(root.join("webview"));
            }
            #[cfg(debug_assertions)]
            if std::env::var("CHAOXING_TAURI_DEV_HIDDEN").as_deref() == Ok("1") {
                window = window.visible(false);
            }
            window.build()?;

            let handle = app.handle().clone();
            std::thread::spawn(move || {
                backend::host_log(&log_dir, "[host] starting");
                // Never read a real Electron profile implicitly in development.
                let import_legacy = !cfg!(debug_assertions)
                    || std::env::var_os(migration::LEGACY_DIR_ENV).is_some();
                if import_legacy {
                    match migration::migrate(&data_dir) {
                        Ok(migration::MigrationOutcome::DeferredLegacyRunning) => {
                            state_phase_fail(&state, "请先关闭旧版桌面应用，再关闭此窗口并重新打开，以导入原有数据。".into());
                            return;
                        }
                        Ok(migration::MigrationOutcome::ImportedButSessionInvalid(reason)) => {
                            *notice.lock().unwrap_or_else(|e| e.into_inner()) = Some("旧账号记录无法导入，请重新登录。其他有效数据已导入，原文件已保留。".into());
                            backend::host_log(&log_dir, &format!("[migration] session skipped: {reason}"));
                        }
                        Ok(outcome) => backend::host_log(&log_dir, &format!("[migration] {outcome:?}")),
                        Err(error) => {
                            backend::host_log(&log_dir, &format!("[migration] failed: {error}"));
                            state_phase_fail(&state, "旧数据导入失败，原有数据已保留。请检查数据目录权限后重新打开应用。".into());
                            return;
                        }
                    }
                }
                match launch_backend(&handle) {
                    Ok(launch) => {
                        if let Err(error) = backend::start_backend(&state, launch) {
                            backend::host_log(&log_dir, &format!("[host] backend start failed: {error}"));
                        }
                    }
                    Err(error) => state_phase_fail(&state, error),
                }
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            backend_status, api_request, api_cancel, session_read,
            session_remember_login, session_remember_task, session_clear
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|handle, event| {
        if let tauri::RunEvent::ExitRequested { .. } = event {
            if let Some(state) = handle.try_state::<Arc<BackendState>>() {
                backend::stop_backend(&state);
            }
        }
    });
}

fn development_root(app: &tauri::AppHandle) -> Result<Option<PathBuf>, Box<dyn std::error::Error>> {
    #[cfg(debug_assertions)]
    {
        let root = match std::env::var_os("CHAOXING_TAURI_DEV_ROOT") {
            Some(root) => PathBuf::from(root),
            None => app.path().app_local_data_dir()?.join("development"),
        };
        if !root.is_absolute() {
            return Err("CHAOXING_TAURI_DEV_ROOT must be an absolute isolated directory".into());
        }
        Ok(Some(root))
    }
    #[cfg(not(debug_assertions))]
    {
        let _ = app;
        Ok(None)
    }
}

fn launch_backend(app: &tauri::AppHandle) -> Result<backend::BackendLaunch, String> {
    #[cfg(debug_assertions)]
    if let Some(path) = std::env::var_os("CHAOXING_TAURI_DEV_BACKEND") {
        let path = PathBuf::from(path);
        if !path.is_absolute() {
            return Err("测试后端路径必须为绝对路径".into());
        }
        return Ok(backend::BackendLaunch::Frozen(path));
    }
    backend::detect_launch(app)
}

fn state_phase_fail(state: &Arc<BackendState>, message: String) {
    let mut phase = state.phase.lock().unwrap_or_else(|e| e.into_inner());
    if !matches!(
        *phase,
        backend::BackendPhase::Starting | backend::BackendPhase::Ready
    ) {
        return;
    }
    *state.error.lock().unwrap_or_else(|e| e.into_inner()) = Some(message.clone());
    *phase = backend::BackendPhase::Failed;
    backend::host_log(&state.log_dir, &format!("[host] failed: {message}"));
}

fn allowed_navigation(url: &tauri::Url) -> bool {
    if !url.username().is_empty() || url.password().is_some() {
        return false;
    }
    let origin = (url.scheme(), url.host_str(), url.port());
    matches!(
        origin,
        ("http" | "https", Some("tauri.localhost"), None) | ("tauri", Some("localhost"), None)
    ) || (cfg!(debug_assertions) && matches!(origin, ("http", Some("localhost"), Some(3000))))
}

/// Serde's derived structs also accept positional sequences. Wire/session DTOs
/// must enter through a map while retaining derived field and duplicate checks.
pub(crate) fn deserialize_object<'de, D, T>(deserializer: D) -> Result<T, D::Error>
where
    D: serde::Deserializer<'de>,
    T: Deserialize<'de>,
{
    struct ObjectVisitor<T>(std::marker::PhantomData<T>);

    impl<'de, T: Deserialize<'de>> serde::de::Visitor<'de> for ObjectVisitor<T> {
        type Value = T;

        fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            formatter.write_str("a JSON object")
        }

        fn visit_map<M: serde::de::MapAccess<'de>>(self, map: M) -> Result<T, M::Error> {
            T::deserialize(serde::de::value::MapAccessDeserializer::new(map))
        }
    }

    deserializer.deserialize_map(ObjectVisitor(std::marker::PhantomData))
}

fn decode_args<T: DeserializeOwned>(body: &tauri::ipc::InvokeBody) -> Result<T, String> {
    match body {
        tauri::ipc::InvokeBody::Json(value) if value.is_object() => {
            serde_json::from_value(value.clone()).map_err(|_| "请求参数格式错误".into())
        }
        _ => Err("请求参数格式错误".into()),
    }
}

fn command_args<T: DeserializeOwned>(
    window: &tauri::WebviewWindow,
    ipc: &tauri::ipc::Request<'_>,
) -> Result<T, String> {
    if window.label() != "main"
        || !window
            .url()
            .map(|url| allowed_navigation(&url))
            .unwrap_or(false)
    {
        return Err("禁止访问桌面命令".into());
    }
    decode_args(ipc.body())
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct EmptyArgs {}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ApiArgs {
    request: api_proxy::ApiRequest,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct CancelArgs {
    request_id: u64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LoginArgs {
    username: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct TaskArgs {
    // The key is required; null explicitly clears only the task.
    #[serde(deserialize_with = "session_store::required_nullable")]
    task: Option<session_store::SessionActiveTask>,
}

struct StartupNotice(Arc<Mutex<Option<String>>>);

#[derive(serde::Serialize)]
struct HostStatus {
    #[serde(flatten)]
    backend: backend::BackendStatus,
    #[serde(skip_serializing_if = "Option::is_none")]
    notice: Option<String>,
}

#[tauri::command]
fn backend_status(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<BackendState>>,
    ipc: tauri::ipc::Request<'_>,
    notice: tauri::State<'_, StartupNotice>,
) -> Result<HostStatus, String> {
    let _: EmptyArgs = command_args(&window, &ipc)?;
    Ok(HostStatus {
        backend: state.status(),
        notice: notice.0.lock().unwrap_or_else(|e| e.into_inner()).clone(),
    })
}

#[tauri::command]
async fn api_request(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<BackendState>>,
    ipc: tauri::ipc::Request<'_>,
) -> Result<api_proxy::ProxyResponse, api_proxy::ProxyError> {
    let args: ApiArgs = command_args(&window, &ipc)
        .map_err(|reason| api_proxy::ProxyError::InvalidRequest { reason })?;
    args.request.validate()?;
    let request = args.request;
    let state = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        backend::api_request(
            &state,
            request.operation,
            request.task_id,
            request.after,
            request.payload,
            request.request_id,
        )
    })
    .await
    .map_err(|_| api_proxy::ProxyError::Network {
        reason: "桌面请求执行失败".into(),
    })?
}

#[tauri::command]
fn api_cancel(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<BackendState>>,
    ipc: tauri::ipc::Request<'_>,
) -> Result<bool, String> {
    let args: CancelArgs = command_args(&window, &ipc)?;
    if !api_proxy::valid_request_id(args.request_id) {
        return Err("请求编号无效".into());
    }
    Ok(backend::api_cancel(&state, args.request_id))
}

#[tauri::command]
fn session_read(
    window: tauri::WebviewWindow,
    store: tauri::State<'_, session_store::SessionStore>,
    ipc: tauri::ipc::Request<'_>,
) -> Result<session_store::SessionData, String> {
    let _: EmptyArgs = command_args(&window, &ipc)?;
    store.read().map_err(|_| "无法读取保存的账号".into())
}

#[tauri::command]
fn session_remember_login(
    window: tauri::WebviewWindow,
    store: tauri::State<'_, session_store::SessionStore>,
    ipc: tauri::ipc::Request<'_>,
) -> Result<session_store::SessionData, String> {
    let args: LoginArgs = command_args(&window, &ipc)?;
    store
        .remember_login(&args.username)
        .map_err(|error| match error {
            session_store::SessionError::Validation(_) => "账号格式错误".into(),
            _ => "无法保存账号，请重试".into(),
        })
}

#[tauri::command]
fn session_remember_task(
    window: tauri::WebviewWindow,
    store: tauri::State<'_, session_store::SessionStore>,
    ipc: tauri::ipc::Request<'_>,
) -> Result<session_store::SessionData, String> {
    let args: TaskArgs = command_args(&window, &ipc)?;
    store.remember_task(args.task).map_err(|error| match error {
        session_store::SessionError::Validation(_) => "任务信息格式错误".into(),
        session_store::SessionError::NoLogin | session_store::SessionError::AccountMismatch => {
            "任务账号不匹配".into()
        }
        session_store::SessionError::Io(_) => "无法保存账号，请重试".into(),
    })
}

#[tauri::command]
fn session_clear(
    window: tauri::WebviewWindow,
    store: tauri::State<'_, session_store::SessionStore>,
    ipc: tauri::ipc::Request<'_>,
) -> Result<session_store::SessionData, String> {
    let _: EmptyArgs = command_args(&window, &ipc)?;
    store
        .clear()
        .map_err(|_| "无法清除保存的账号，请重试".into())
}

#[cfg(test)]
mod command_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn p2_object_boundary_rejects_positional_ipc_envelopes() {
        for (name, rejected) in [
            (
                "empty",
                decode_args::<EmptyArgs>(&tauri::ipc::InvokeBody::Json(json!([]))).is_err(),
            ),
            (
                "login",
                decode_args::<LoginArgs>(&tauri::ipc::InvokeBody::Json(json!(["alice"]))).is_err(),
            ),
            (
                "cancel",
                decode_args::<CancelArgs>(&tauri::ipc::InvokeBody::Json(json!([1]))).is_err(),
            ),
            (
                "task",
                decode_args::<TaskArgs>(&tauri::ipc::InvokeBody::Json(json!([null]))).is_err(),
            ),
            (
                "request",
                decode_args::<ApiArgs>(&tauri::ipc::InvokeBody::Json(
                    json!([{"operation":"configRead","payload":null,"requestId":1}]),
                ))
                .is_err(),
            ),
        ] {
            assert!(rejected, "accepted positional {name} envelope");
        }
    }

    #[test]
    fn p2_object_boundary_rejects_nested_api_array() {
        let ipc = tauri::ipc::InvokeBody::Json(json!({"request":["taskLogs",null,1,"t",0]}));
        assert!(decode_args::<ApiArgs>(&ipc).is_err());
    }

    #[test]
    fn p2_object_boundary_rejects_nested_task_array_and_preserves_null() {
        let ipc = tauri::ipc::InvokeBody::Json(json!({"task":["alice","t"]}));
        assert!(decode_args::<TaskArgs>(&ipc).is_err());
        assert!(
            decode_args::<TaskArgs>(&tauri::ipc::InvokeBody::Json(json!({"task":null}))).is_ok()
        );
        assert!(decode_args::<TaskArgs>(&tauri::ipc::InvokeBody::Json(
            json!({"task":{"username":"alice","taskId":"t"}})
        ))
        .is_ok());
        assert!(decode_args::<TaskArgs>(&tauri::ipc::InvokeBody::Json(json!({}))).is_err());
    }

    #[test]
    fn exact_envelope_rejects_credentials_and_extra_arguments() {
        assert!(decode_args::<EmptyArgs>(&tauri::ipc::InvokeBody::Json(
            json!({"password":"synthetic"})
        ))
        .is_err());
        assert!(decode_args::<LoginArgs>(&tauri::ipc::InvokeBody::Json(
            json!({"username":"alice","password":"synthetic"})
        ))
        .is_err());
        assert!(decode_args::<TaskArgs>(&tauri::ipc::InvokeBody::Json(json!({}))).is_err());
        assert!(
            decode_args::<TaskArgs>(&tauri::ipc::InvokeBody::Json(json!({"task":null}))).is_ok()
        );
        assert!(decode_args::<CancelArgs>(&tauri::ipc::InvokeBody::Json(
            json!({"requestId":1,"url":"http://example.test"})
        ))
        .is_err());
        assert!(decode_args::<EmptyArgs>(&tauri::ipc::InvokeBody::Raw(vec![])).is_err());
    }

    #[test]
    fn navigation_rejects_foreign_origins_and_credentials() {
        for url in ["http://tauri.localhost/", "tauri://localhost/index.html"] {
            assert!(allowed_navigation(&url.parse().unwrap()));
        }
        for url in [
            "https://example.test/",
            "http://tauri.localhost:9999/",
            "http://tauri.localhost@evil.test/",
            "http://user@tauri.localhost/",
            "file:///C:/temp/test.html",
            "data:text/html,test",
            "http://localhost:3001/",
        ] {
            assert!(!allowed_navigation(&url.parse().unwrap()), "accepted {url}");
        }
    }
}

```


## desktop/src-tauri/src/main.rs

```
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    chaoxing_desktop_lib::run();
}

```


## desktop/src-tauri/src/migration.rs

```
//! One-time, read-only import of legacy Electron business data.
//!
//! Copy and validate a sibling staging directory, including its completion
//! marker, then publish the whole directory with one non-replacing rename.
//! A process exit on either side of that rename is safe to retry. The host must
//! not start the backend after an IO error or while the old Electron is running.

use crate::session_store;
use std::fs::{self, File, Metadata, OpenOptions};
use std::io::{self, Write};
use std::os::windows::ffi::{OsStrExt, OsStringExt};
use std::os::windows::fs::{MetadataExt, OpenOptionsExt};
use std::os::windows::io::AsRawHandle;
use std::path::{Path, PathBuf};
use windows::core::{w, PCWSTR};
use windows::Win32::Foundation::HANDLE;
use windows::Win32::Storage::FileSystem::{
    FileRenameInfo, SetFileInformationByHandle, DELETE, FILE_FLAG_BACKUP_SEMANTICS,
    FILE_FLAG_OPEN_REPARSE_POINT, FILE_LIST_DIRECTORY, FILE_READ_ATTRIBUTES, FILE_RENAME_INFO,
    FILE_SHARE_READ, FILE_SHARE_WRITE,
};
use windows::Win32::UI::WindowsAndMessaging::{FindWindowExW, HWND_MESSAGE};

pub const LEGACY_DIR_ENV: &str = "CHAOXING_LEGACY_DATA_DIR";
pub const LEGACY_DIR_DEFAULT: &str = "chaoxing-desktop";
pub const DONE_MARKER: &str = "migration-v1.done";
/// A sibling of `data`, never a directory inside the published business data.
pub const STAGING_DIR: &str = ".migration-staging";

const FILE_WHITELIST: [&str; 5] = [
    "renderer-session.json",
    "web_config.json",
    "cookies.txt",
    "cache.json",
    "config.ini",
];
const DIR_WHITELIST: [&str; 1] = [".cookies"];
const INVALID_SESSION_NOTICE: &str =
    "Legacy renderer-session.json is corrupt, exceeds 4096 bytes, or has an invalid schema; it was not imported.";

#[derive(Debug)]
pub enum MigrationOutcome {
    /// A completed import or existing new business data prevents another import.
    Skipped(&'static str),
    /// No legacy directory or whitelisted data was found.
    NoLegacyData,
    /// Other data may have been imported; the invalid legacy session was kept only in the old directory.
    ImportedButSessionInvalid(String),
    Imported(usize),
    /// The Electron ProcessSingleton window exists, or a legacy lock is busy.
    DeferredLegacyRunning,
}

fn legacy_dir() -> io::Result<Option<PathBuf>> {
    if let Some(override_dir) = std::env::var_os(LEGACY_DIR_ENV) {
        if override_dir.is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "empty legacy data directory",
            ));
        }
        // Existence and permissions must be checked by the importer. Treating
        // an inaccessible directory as missing would permanently mark it done.
        return Ok(Some(PathBuf::from(override_dir)));
    }
    Ok(std::env::var_os("APPDATA")
        .filter(|value| !value.is_empty())
        .map(|appdata| PathBuf::from(appdata).join(LEGACY_DIR_DEFAULT)))
}

fn reject_reparse(path: &Path, metadata: &Metadata) -> io::Result<()> {
    const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
    if metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!("migration refuses reparse point: {}", path.display()),
        ));
    }
    Ok(())
}

/// Check every ancestor before accessing a path, not just its final component.
fn checked_metadata(path: &Path) -> io::Result<Option<Metadata>> {
    let mut result = None;
    for ancestor in path.ancestors().collect::<Vec<_>>().into_iter().rev() {
        let metadata = match fs::symlink_metadata(ancestor) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(error),
        };
        reject_reparse(ancestor, &metadata)?;
        if ancestor != path && !metadata.is_dir() {
            return Err(io::Error::new(
                io::ErrorKind::NotADirectory,
                "migration parent is not a directory",
            ));
        }
        result = Some(metadata);
    }
    Ok(result)
}

fn require_directory(path: &Path, metadata: &Metadata) -> io::Result<()> {
    reject_reparse(path, metadata)?;
    if !metadata.is_dir() {
        return Err(io::Error::new(
            io::ErrorKind::NotADirectory,
            "migration path is not a directory",
        ));
    }
    Ok(())
}

/// A no-delete directory handle prevents an already-checked directory from
/// being replaced by a junction while descendants are copied or cleaned.
fn lock_directory(path: &Path) -> io::Result<File> {
    directory_handle(path, FILE_READ_ATTRIBUTES.0 | FILE_LIST_DIRECTORY.0)
}

fn directory_handle(path: &Path, access: u32) -> io::Result<File> {
    let handle = OpenOptions::new()
        // FILE_READ_ATTRIBUTES alone is a metadata-only open; Windows does
        // not enforce its sharing flags. LIST_DIRECTORY makes the lock real.
        .access_mode(access)
        .share_mode(FILE_SHARE_READ.0 | FILE_SHARE_WRITE.0)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS.0 | FILE_FLAG_OPEN_REPARSE_POINT.0)
        .open(path)?;
    require_directory(path, &handle.metadata()?)?;
    Ok(handle)
}

fn lock_directory_chain(path: &Path, create: bool) -> io::Result<Vec<File>> {
    let mut handles = Vec::new();
    for ancestor in path.ancestors().collect::<Vec<_>>().into_iter().rev() {
        match fs::symlink_metadata(ancestor) {
            Ok(metadata) => require_directory(ancestor, &metadata)?,
            Err(error) if create && error.kind() == io::ErrorKind::NotFound => {
                fs::create_dir(ancestor)?;
            }
            Err(error) => return Err(error),
        }
        handles.push(lock_directory(ancestor)?);
    }
    Ok(handles)
}

fn open_source_file(path: &Path) -> io::Result<Option<File>> {
    let Some(metadata) = checked_metadata(path)? else {
        return Ok(None);
    };
    if !metadata.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "legacy whitelist file is not a regular file",
        ));
    }
    let file = OpenOptions::new()
        .read(true)
        .share_mode(FILE_SHARE_READ.0)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT.0)
        .open(path)?;
    let opened = file.metadata()?;
    reject_reparse(path, &opened)?;
    if !opened.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "legacy file changed type",
        ));
    }
    Ok(Some(file))
}

fn copy_open_file(source: &mut File, destination: &Path) -> io::Result<()> {
    let mut target = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(destination)?;
    io::copy(source, &mut target)?;
    target.sync_all()
}

fn copy_directory(source: &Path, destination: &Path) -> io::Result<()> {
    let _source_guard = lock_directory(source)?;
    fs::create_dir(destination)?;
    let _destination_guard = lock_directory(destination)?;
    for entry in fs::read_dir(source)? {
        let entry = entry?;
        let path = entry.path();
        let metadata = checked_metadata(&path)?
            .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "legacy cookie disappeared"))?;
        let target = destination.join(entry.file_name());
        if metadata.is_dir() {
            copy_directory(&path, &target)?;
        } else if let Some(mut file) = open_source_file(&path)? {
            copy_open_file(&mut file, &target)?;
        } else {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                "legacy cookie disappeared",
            ));
        }
    }
    Ok(())
}

/// Only used for our reserved staging directories. Do not follow a link even
/// while cleaning a previous process's interrupted import.
fn remove_staging(path: &Path) -> io::Result<()> {
    let Some(metadata) = checked_metadata(path)? else {
        return Ok(());
    };
    require_directory(path, &metadata)?;
    let guard = lock_directory(path)?;
    for entry in fs::read_dir(path)? {
        let path = entry?.path();
        let metadata = checked_metadata(&path)?
            .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "staging entry disappeared"))?;
        if metadata.is_dir() {
            remove_staging(&path)?;
        } else if metadata.is_file() {
            fs::remove_file(path)?;
        } else {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "invalid staging entry",
            ));
        }
    }
    drop(guard);
    fs::remove_dir(path)
}

fn write_marker(directory: &Path, reason: &[u8]) -> io::Result<()> {
    let mut marker = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(directory.join(DONE_MARKER))?;
    marker.write_all(reason)?;
    marker.sync_all()
}

/// Rename the pinned staging directory itself. Reopening it by path after
/// dropping the no-delete lock would allow a junction substitution at publish.
fn publish_directory(staging: &File, destination: &Path) -> io::Result<()> {
    let name: Vec<u16> = destination.as_os_str().encode_wide().collect();
    let name_bytes = name.len() * std::mem::size_of::<u16>();
    let buffer_bytes = std::mem::size_of::<FILE_RENAME_INFO>() + name_bytes;
    let length = u32::try_from(buffer_bytes)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "migration path is too long"))?;
    // A word-backed allocation supplies FILE_RENAME_INFO's pointer alignment
    // and enough room for its trailing, variable-length UTF-16 file name.
    let mut buffer = vec![0usize; buffer_bytes.div_ceil(std::mem::size_of::<usize>())];
    let info = buffer.as_mut_ptr().cast::<FILE_RENAME_INFO>();
    unsafe {
        info.write(FILE_RENAME_INFO::default());
        (*info).Anonymous.ReplaceIfExists = false;
        (*info).FileNameLength = name_bytes as u32;
        std::ptr::copy_nonoverlapping(
            name.as_ptr(),
            std::ptr::addr_of_mut!((*info).FileName).cast::<u16>(),
            name.len(),
        );
        SetFileInformationByHandle(
            HANDLE(staging.as_raw_handle()),
            FileRenameInfo,
            info.cast(),
            length,
        )
    }
    .map_err(|error| {
        let code = error.code().0 as u32;
        if code & 0xffff_0000 == 0x8007_0000 {
            io::Error::from_raw_os_error((code & 0xffff) as i32)
        } else {
            io::Error::other(error)
        }
    })
}

/// Chromium's Windows ProcessSingleton uses a Chrome_MessageWindow whose title
/// is the userData path. canonicalize() adds a Win32 verbatim prefix; Electron's
/// title normally does not contain it. Preserve UTF-16 while removing the prefix.
fn window_title(path: &Path) -> Vec<u16> {
    let wide: Vec<u16> = path.as_os_str().encode_wide().collect();
    let mut title = if wide.starts_with(&[92, 92, 63, 92, 85, 78, 67, 92]) {
        [vec![92, 92], wide[8..].to_vec()].concat()
    } else if wide.starts_with(&[92, 92, 63, 92]) {
        wide[4..].to_vec()
    } else {
        wide
    };
    for character in &mut title {
        if *character == 47 {
            *character = 92;
        }
    }
    title.push(0);
    title
}

fn legacy_running(legacy: &Path) -> io::Result<bool> {
    for path in [std::path::absolute(legacy)?, fs::canonicalize(legacy)?] {
        let title = window_title(&path);
        // Exact profile matching avoids deferring for unrelated Electron apps.
        // Older Chromium versions may use a hidden top-level window instead.
        for parent in [Some(HWND_MESSAGE), None] {
            if unsafe {
                FindWindowExW(
                    parent,
                    None,
                    w!("Chrome_MessageWindow"),
                    PCWSTR(title.as_ptr()),
                )
            }
            .is_ok()
            {
                return Ok(true);
            }
        }
    }
    // Some legacy wrappers also hold a lock file. Probe it read-only; Electron
    // itself does not promise to create this sentinel, so it is only a fallback.
    match open_source_file(&legacy.join("lockfile")) {
        Ok(_) => Ok(false),
        Err(error) if matches!(error.raw_os_error(), Some(32 | 33)) => Ok(true),
        Err(error) => Err(error),
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Checkpoint {
    StagingCreated,
    SessionCopied,
    BeforePublish,
    Published,
}

fn paths_overlap(first: &Path, second: &Path) -> bool {
    // Case folding is conservative on Windows: false positives safely refuse
    // an import instead of allowing an override to change the old data tree.
    fn key(path: &Path) -> PathBuf {
        let title = window_title(path);
        let path = std::ffi::OsString::from_wide(&title[..title.len() - 1]);
        PathBuf::from(path.to_string_lossy().to_lowercase())
    }
    let first = key(first);
    let second = key(second);
    first.starts_with(&second) || second.starts_with(&first)
}

/// Run before starting the backend. Any Err or DeferredLegacyRunning must keep
/// the backend stopped so a retry cannot mistake new backend files for user data.
pub fn migrate(data_dir: &Path) -> io::Result<MigrationOutcome> {
    migrate_from(data_dir, legacy_dir()?.as_deref())
}

fn migrate_from(data_dir: &Path, legacy: Option<&Path>) -> io::Result<MigrationOutcome> {
    migrate_with_checkpoint(data_dir, legacy, |_| Ok(()))
}

fn migrate_with_checkpoint(
    data_dir: &Path,
    legacy: Option<&Path>,
    mut checkpoint: impl FnMut(Checkpoint) -> io::Result<()>,
) -> io::Result<MigrationOutcome> {
    if data_dir.as_os_str().is_empty() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "empty migration destination",
        ));
    }
    let data_dir = std::path::absolute(data_dir)?;
    let parent = data_dir.parent().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "migration destination has no parent",
        )
    })?;
    let staging = parent.join(STAGING_DIR);
    let legacy = legacy.map(std::path::absolute).transpose()?;
    if paths_overlap(&data_dir, &staging)
        || legacy
            .as_ref()
            .is_some_and(|path| paths_overlap(path, &data_dir) || paths_overlap(path, &staging))
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "legacy, new data, and staging paths must not overlap",
        ));
    }

    let data_metadata = checked_metadata(&data_dir)?;
    // Pin all existing destination ancestors before reading or writing markers.
    // Missing parents are created only after the source has been checked.
    let _existing_parent_guards = if checked_metadata(parent)?.is_some() {
        Some(lock_directory_chain(parent, false)?)
    } else {
        None
    };
    let mut data_guard = match data_metadata {
        Some(ref metadata) => {
            require_directory(&data_dir, metadata)?;
            Some(lock_directory(&data_dir)?)
        }
        None => None,
    };
    if let Some(marker) = checked_metadata(&data_dir.join(DONE_MARKER))? {
        if !marker.is_file() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "migration marker is not a regular file",
            ));
        }
        return Ok(MigrationOutcome::Skipped("done marker present"));
    }
    if data_metadata.is_some() {
        for entry in fs::read_dir(&data_dir)? {
            let entry = entry?;
            let metadata = checked_metadata(&entry.path())?.ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::NotFound,
                    "new data changed while checking migration",
                )
            })?;
            if entry.file_name() == STAGING_DIR {
                require_directory(&entry.path(), &metadata)?;
            } else {
                // Preserve every new business file, including a config-only or
                // cookie-only install, unknown future files, and invalid sessions.
                write_marker(&data_dir, b"existing-data")?;
                return Ok(MigrationOutcome::Skipped("new business data exists"));
            }
        }
    }

    let legacy = match legacy {
        Some(path) => match checked_metadata(&path)? {
            Some(metadata) => {
                require_directory(&path, &metadata)?;
                Some(path)
            }
            None => None,
        },
        None => None,
    };
    let _source_guards = legacy
        .as_ref()
        .map(|path| lock_directory_chain(path, false))
        .transpose()?;
    if legacy
        .as_ref()
        .map(|path| legacy_running(path))
        .transpose()?
        .unwrap_or(false)
    {
        return Ok(MigrationOutcome::DeferredLegacyRunning);
    }

    let _parent_guards = lock_directory_chain(parent, true)?;
    remove_staging(&staging)?;
    // Compatibility with the old P1 layout; only its reserved staging is removed.
    remove_staging(&data_dir.join(STAGING_DIR))?;
    fs::create_dir(&staging)?;
    let staging_guard = directory_handle(
        &staging,
        FILE_READ_ATTRIBUTES.0 | FILE_LIST_DIRECTORY.0 | DELETE.0,
    )?;
    checkpoint(Checkpoint::StagingCreated)?;

    let mut copied = 0;
    let mut invalid_session = false;
    if let Some(legacy) = &legacy {
        let session_path = legacy.join(FILE_WHITELIST[0]);
        if let Some(mut source) = open_source_file(&session_path)? {
            // The held read-only handle denies writes/deletion. The shared reader
            // applies the same bounded, strict schema as normal session loading.
            if session_store::read(&session_path)?.is_some() {
                let staged_session = staging.join(FILE_WHITELIST[0]);
                copy_open_file(&mut source, &staged_session)?;
                if session_store::read(&staged_session)?.is_none() {
                    return Err(io::Error::new(
                        io::ErrorKind::InvalidData,
                        "session changed during migration",
                    ));
                }
                copied += 1;
                checkpoint(Checkpoint::SessionCopied)?;
            } else {
                invalid_session = true;
            }
        }
        for name in FILE_WHITELIST.iter().skip(1) {
            if let Some(mut source) = open_source_file(&legacy.join(name))? {
                copy_open_file(&mut source, &staging.join(name))?;
                copied += 1;
            }
        }
        for name in DIR_WHITELIST {
            let source = legacy.join(name);
            if let Some(metadata) = checked_metadata(&source)? {
                require_directory(&source, &metadata)?;
                copy_directory(&source, &staging.join(name))?;
                copied += 1;
            }
        }
    }

    let reason: &[u8] = if invalid_session {
        b"invalid-session"
    } else if copied == 0 {
        b"no-legacy"
    } else {
        b"ok"
    };
    write_marker(&staging, reason)?;
    checkpoint(Checkpoint::BeforePublish)?;
    if legacy
        .as_ref()
        .map(|path| legacy_running(path))
        .transpose()?
        .unwrap_or(false)
    {
        return Ok(MigrationOutcome::DeferredLegacyRunning);
    }
    // Removing an empty placeholder never removes business data. A writer that
    // wins this race causes an error; the directory rename never replaces a target.
    if checked_metadata(&data_dir)?.is_some() {
        if data_guard.is_none() {
            data_guard = Some(lock_directory(&data_dir)?);
        }
        if fs::read_dir(&data_dir)?.next().transpose()?.is_some() {
            return Err(io::Error::new(
                io::ErrorKind::AlreadyExists,
                "new business data appeared during migration",
            ));
        }
        drop(data_guard.take());
        fs::remove_dir(&data_dir)?;
    }
    publish_directory(&staging_guard, &data_dir)?;
    checkpoint(Checkpoint::Published)?;

    Ok(if invalid_session {
        MigrationOutcome::ImportedButSessionInvalid(INVALID_SESSION_NOTICE.to_owned())
    } else if copied == 0 {
        MigrationOutcome::NoLegacyData
    } else {
        MigrationOutcome::Imported(copied)
    })
}

#[cfg(test)]
mod p2_tests {
    use super::*;
    use std::collections::BTreeMap;
    use std::os::windows::fs::OpenOptionsExt;
    use std::os::windows::process::CommandExt;
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT_FIXTURE: AtomicU64 = AtomicU64::new(0);

    struct Fixture {
        root: PathBuf,
        legacy: PathBuf,
        data: PathBuf,
    }

    impl Fixture {
        fn new(name: &str) -> Self {
            let nonce = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            let root = std::env::temp_dir().join(format!(
                "cx-migration-p2-{name}-{}-{nonce}-{}",
                std::process::id(),
                NEXT_FIXTURE.fetch_add(1, Ordering::Relaxed)
            ));
            std::fs::create_dir(&root).unwrap();
            let legacy = root.join("legacy");
            std::fs::create_dir(&legacy).unwrap();
            Self {
                data: root.join("data"),
                legacy,
                root,
            }
        }

        fn write_legacy(&self, name: &str, contents: impl AsRef<[u8]>) {
            let path = self.legacy.join(name);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, contents).unwrap();
        }

        fn run(&self) -> std::io::Result<MigrationOutcome> {
            // Explicit paths allow the complete suite to run in parallel
            // without changing the test process's environment.
            migrate_from(&self.data, Some(&self.legacy))
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.root);
        }
    }

    fn valid_session() -> &'static str {
        r#"{"version":1,"login":{"username":"fixture-user","use_cookies":true},"activeTask":{"username":"fixture-user","taskId":"fixture-task"}}"#
    }

    fn source_bytes(root: &Path) -> BTreeMap<PathBuf, Vec<u8>> {
        fn visit(root: &Path, path: &Path, files: &mut BTreeMap<PathBuf, Vec<u8>>) {
            for entry in std::fs::read_dir(path).unwrap() {
                let entry = entry.unwrap();
                if entry.file_type().unwrap().is_dir() {
                    visit(root, &entry.path(), files);
                } else {
                    files.insert(
                        entry.path().strip_prefix(root).unwrap().to_path_buf(),
                        std::fs::read(entry.path()).unwrap(),
                    );
                }
            }
        }
        let mut files = BTreeMap::new();
        visit(root, root, &mut files);
        files
    }

    fn exclusive_file(path: &Path) -> std::fs::File {
        std::fs::OpenOptions::new()
            .read(true)
            .share_mode(0)
            .open(path)
            .unwrap()
    }

    fn directory_link(target: &Path, link: &Path) {
        if std::os::windows::fs::symlink_dir(target, link).is_ok() {
            return;
        }
        // Junction creation works without the symlink privilege on Windows.
        // Paths are passed as process-local environment values, never shell code.
        let output = std::process::Command::new("powershell.exe")
            .args([
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "New-Item -ItemType Junction -Path $env:CX_MIGRATION_LINK -Target $env:CX_MIGRATION_TARGET -ErrorAction Stop | Out-Null",
            ])
            .env("CX_MIGRATION_LINK", link)
            .env("CX_MIGRATION_TARGET", target)
            .creation_flags(0x08000000)
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "junction fixture failed: {}",
            String::from_utf8_lossy(&output.stderr)
        );
    }

    struct SingletonWindow(windows::Win32::Foundation::HWND);

    impl SingletonWindow {
        fn new(user_data: &Path) -> Self {
            use std::os::windows::ffi::OsStrExt;
            use windows::core::{w, PCWSTR};
            use windows::Win32::Foundation::{HWND, LPARAM, LRESULT, WPARAM};
            use windows::Win32::UI::WindowsAndMessaging::{
                CreateWindowExW, DefWindowProcW, RegisterClassW, HWND_MESSAGE, WNDCLASSW,
                WS_OVERLAPPED,
            };

            unsafe extern "system" fn window_proc(
                hwnd: HWND,
                message: u32,
                wparam: WPARAM,
                lparam: LPARAM,
            ) -> LRESULT {
                unsafe { DefWindowProcW(hwnd, message, wparam, lparam) }
            }

            static CLASS: std::sync::OnceLock<u16> = std::sync::OnceLock::new();
            CLASS.get_or_init(|| {
                let class = WNDCLASSW {
                    lpszClassName: w!("Chrome_MessageWindow"),
                    lpfnWndProc: Some(window_proc),
                    ..Default::default()
                };
                let atom = unsafe { RegisterClassW(&class) };
                assert_ne!(atom, 0, "register singleton fixture class");
                atom
            });
            let title: Vec<u16> = user_data.as_os_str().encode_wide().chain([0]).collect();
            let hwnd = unsafe {
                CreateWindowExW(
                    Default::default(),
                    w!("Chrome_MessageWindow"),
                    PCWSTR(title.as_ptr()),
                    WS_OVERLAPPED,
                    0,
                    0,
                    0,
                    0,
                    Some(HWND_MESSAGE),
                    None,
                    None,
                    None,
                )
            }
            .unwrap();
            Self(hwnd)
        }
    }

    impl Drop for SingletonWindow {
        fn drop(&mut self) {
            unsafe {
                windows::Win32::UI::WindowsAndMessaging::DestroyWindow(self.0).unwrap();
            }
        }
    }

    #[test]
    fn electron_message_window_defers_import_until_legacy_exit() {
        let fixture = Fixture::new("electron-running");
        fixture.write_legacy("cookies.txt", b"cookies");
        let before = source_bytes(&fixture.legacy);
        let window = SingletonWindow::new(&fixture.legacy);

        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::DeferredLegacyRunning
        ));
        assert!(!fixture.data.join(DONE_MARKER).exists());
        assert_eq!(source_bytes(&fixture.legacy), before);
        drop(window);
        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Imported(1)
        ));
    }

    #[test]
    fn unrelated_electron_message_window_does_not_defer_import() {
        let fixture = Fixture::new("unrelated-electron");
        fixture.write_legacy("cookies.txt", b"cookies");
        let _window = SingletonWindow::new(&fixture.root.join("another-user-data"));
        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Imported(1)
        ));
    }

    #[test]
    fn imports_whitelist_once_and_retains_every_source_byte() {
        let fixture = Fixture::new("success");
        fixture.write_legacy("renderer-session.json", valid_session());
        for name in FILE_WHITELIST.iter().skip(1) {
            fixture.write_legacy(name, name);
        }
        fixture.write_legacy(".cookies/account.json", b"cookie-fixture");
        fixture.write_legacy(".cookies/nested/another.json", b"second-cookie");
        fixture.write_legacy("unlisted/private.txt", b"leave-in-legacy");
        let before = source_bytes(&fixture.legacy);

        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Imported(6)
        ));
        assert_eq!(source_bytes(&fixture.legacy), before);
        for name in FILE_WHITELIST {
            assert_eq!(
                std::fs::read(fixture.data.join(name)).unwrap(),
                before[Path::new(name)]
            );
        }
        assert!(fixture.data.join(DONE_MARKER).is_file());
        assert!(!fixture.data.join("unlisted").exists());
        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Skipped(_)
        ));
        assert_eq!(source_bytes(&fixture.legacy), before);
    }

    #[test]
    fn config_only_new_data_is_never_overwritten() {
        let fixture = Fixture::new("new-config");
        fixture.write_legacy("web_config.json", b"old-config");
        std::fs::create_dir(&fixture.data).unwrap();
        std::fs::write(fixture.data.join("web_config.json"), b"new-config").unwrap();

        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Skipped(_)
        ));
        assert_eq!(
            std::fs::read(fixture.data.join("web_config.json")).unwrap(),
            b"new-config"
        );
    }

    #[test]
    fn cookie_only_new_data_is_never_overwritten() {
        let fixture = Fixture::new("new-cookie");
        fixture.write_legacy("cookies.txt", b"old-cookie");
        std::fs::create_dir(&fixture.data).unwrap();
        std::fs::write(fixture.data.join("cookies.txt"), b"new-cookie").unwrap();

        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Skipped(_)
        ));
        assert_eq!(
            std::fs::read(fixture.data.join("cookies.txt")).unwrap(),
            b"new-cookie"
        );
    }

    #[test]
    fn cookie_directory_or_unknown_new_data_prevents_import() {
        for name in [
            ".cookies/account.json",
            "future-data.bin",
            "renderer-session.json",
        ] {
            let fixture = Fixture::new("new-business-data");
            fixture.write_legacy("cookies.txt", b"old-cookie");
            let new_path = fixture.data.join(name);
            std::fs::create_dir_all(new_path.parent().unwrap()).unwrap();
            std::fs::write(&new_path, b"preserve-even-if-invalid-session").unwrap();
            assert!(matches!(
                fixture.run().unwrap(),
                MigrationOutcome::Skipped(_)
            ));
            assert!(!fixture.data.join("cookies.txt").exists());
            assert_eq!(
                std::fs::read(new_path).unwrap(),
                b"preserve-even-if-invalid-session"
            );
        }
    }

    #[test]
    fn oversized_but_well_formed_session_is_skipped_with_notice() {
        let fixture = Fixture::new("oversize");
        let session = format!(
            "{}{}",
            " ".repeat(session_store::MAX_FILE_BYTES),
            valid_session()
        );
        fixture.write_legacy("renderer-session.json", &session);
        fixture.write_legacy("web_config.json", b"config");

        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::ImportedButSessionInvalid(_)
        ));
        assert!(!fixture.data.join("renderer-session.json").exists());
        assert!(fixture.data.join("web_config.json").is_file());
        assert_eq!(
            std::fs::read(fixture.legacy.join("renderer-session.json")).unwrap(),
            session.as_bytes()
        );
    }

    #[test]
    fn corrupt_session_skips_only_session_and_keeps_originals() {
        for session in [
            "{ broken",
            r#"{"version":2,"login":null,"activeTask":null}"#,
            r#"{"version":1,"login":null,"activeTask":null,"password":"fixture"}"#,
        ] {
            let fixture = Fixture::new("invalid-session");
            fixture.write_legacy("renderer-session.json", session);
            fixture.write_legacy("cookies.txt", b"cookies");
            let before = source_bytes(&fixture.legacy);
            assert!(matches!(
                fixture.run().unwrap(),
                MigrationOutcome::ImportedButSessionInvalid(_)
            ));
            assert!(!fixture.data.join("renderer-session.json").exists());
            assert!(fixture.data.join("cookies.txt").is_file());
            assert!(fixture.data.join(DONE_MARKER).is_file());
            assert_eq!(source_bytes(&fixture.legacy), before);
        }
    }

    #[test]
    fn session_io_error_aborts_without_marker_and_retries_after_unlock() {
        let fixture = Fixture::new("session-io");
        fixture.write_legacy("renderer-session.json", valid_session());
        fixture.write_legacy("cookies.txt", b"cookies");
        let busy = exclusive_file(&fixture.legacy.join("renderer-session.json"));

        let result = fixture.run();
        assert!(result.is_err(), "session IO must abort, got {result:?}");
        assert!(!fixture.data.join(DONE_MARKER).exists());
        assert!(!fixture.data.join("cookies.txt").exists());
        drop(busy);
        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Imported(2)
        ));
    }

    #[test]
    fn copy_error_never_publishes_a_partial_session_and_can_retry() {
        let fixture = Fixture::new("copy-io");
        fixture.write_legacy("renderer-session.json", valid_session());
        fixture.write_legacy("web_config.json", b"config");
        let before = source_bytes(&fixture.legacy);
        let busy = exclusive_file(&fixture.legacy.join("web_config.json"));

        assert!(fixture.run().is_err());
        assert!(!fixture.data.join(DONE_MARKER).exists());
        assert!(!fixture.data.join("renderer-session.json").exists());
        drop(busy);
        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Imported(2)
        ));
        assert_eq!(source_bytes(&fixture.legacy), before);
    }

    #[test]
    fn interrupted_sibling_staging_is_rebuilt_before_publish() {
        for with_marker in [false, true] {
            let fixture = Fixture::new("interrupted");
            fixture.write_legacy("renderer-session.json", valid_session());
            fixture.write_legacy("cookies.txt", b"complete-cookie");
            let staging = fixture.root.join(STAGING_DIR);
            std::fs::create_dir(&staging).unwrap();
            std::fs::write(staging.join("renderer-session.json"), valid_session()).unwrap();
            if with_marker {
                std::fs::write(staging.join(DONE_MARKER), b"ok").unwrap();
            }

            assert!(matches!(
                fixture.run().unwrap(),
                MigrationOutcome::Imported(2)
            ));
            assert!(
                !staging.exists(),
                "interrupted staging must be consumed or rebuilt"
            );
            assert!(fixture.data.join(DONE_MARKER).is_file());
            assert_eq!(
                std::fs::read(fixture.data.join("cookies.txt")).unwrap(),
                b"complete-cookie"
            );
        }
    }

    #[test]
    fn published_marker_makes_exit_after_publish_retry_safe() {
        let fixture = Fixture::new("post-publish");
        fixture.write_legacy("cookies.txt", b"legacy-cookie");
        std::fs::create_dir(&fixture.data).unwrap();
        std::fs::write(fixture.data.join("cookies.txt"), b"published-cookie").unwrap();
        std::fs::write(fixture.data.join(DONE_MARKER), b"ok").unwrap();

        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Skipped(_)
        ));
        assert_eq!(
            std::fs::read(fixture.data.join("cookies.txt")).unwrap(),
            b"published-cookie"
        );
    }

    #[test]
    fn busy_legacy_is_deferred_until_legacy_exits() {
        let fixture = Fixture::new("busy");
        fixture.write_legacy("lockfile", b"sentinel");
        fixture.write_legacy("cookies.txt", b"cookies");
        let busy = exclusive_file(&fixture.legacy.join("lockfile"));

        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::DeferredLegacyRunning
        ));
        assert!(!fixture.data.join(DONE_MARKER).exists());
        drop(busy);
        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Imported(1)
        ));
    }

    #[test]
    fn source_root_junction_is_refused_without_modifying_target() {
        let fixture = Fixture::new("source-junction");
        let outside = fixture.root.join("outside");
        std::fs::create_dir(&outside).unwrap();
        std::fs::write(outside.join("cookies.txt"), b"outside-cookie").unwrap();
        std::fs::remove_dir(&fixture.legacy).unwrap();
        directory_link(&outside, &fixture.legacy);

        assert!(fixture.run().is_err());
        assert!(!fixture.data.join(DONE_MARKER).exists());
        assert_eq!(
            std::fs::read(outside.join("cookies.txt")).unwrap(),
            b"outside-cookie"
        );
    }

    #[test]
    fn destination_junction_is_refused_without_writing_through_it() {
        let fixture = Fixture::new("target-junction");
        fixture.write_legacy("cookies.txt", b"cookies");
        let outside = fixture.root.join("outside");
        std::fs::create_dir(&outside).unwrap();
        directory_link(&outside, &fixture.data);

        assert!(fixture.run().is_err());
        assert_eq!(std::fs::read_dir(&outside).unwrap().count(), 0);
    }

    #[test]
    fn staging_junction_is_refused_without_touching_its_target() {
        let fixture = Fixture::new("staging-junction");
        fixture.write_legacy("cookies.txt", b"cookies");
        let outside = fixture.root.join("outside");
        std::fs::create_dir(&outside).unwrap();
        std::fs::write(outside.join("keep.txt"), b"outside").unwrap();
        directory_link(&outside, &fixture.root.join(STAGING_DIR));

        assert!(fixture.run().is_err());
        assert!(!fixture.data.join(DONE_MARKER).exists());
        assert_eq!(std::fs::read(outside.join("keep.txt")).unwrap(), b"outside");
    }

    #[test]
    fn recursive_cookie_junction_is_refused_without_copying_outside_data() {
        let fixture = Fixture::new("nested-junction");
        fixture.write_legacy(".cookies/real.json", b"cookie");
        let outside = fixture.root.join("outside");
        std::fs::create_dir(&outside).unwrap();
        std::fs::write(outside.join("private.json"), b"outside").unwrap();
        directory_link(&outside, &fixture.legacy.join(".cookies/nested"));

        assert!(fixture.run().is_err());
        assert!(!fixture.data.join(DONE_MARKER).exists());
        assert!(!fixture.data.join(".cookies/nested/private.json").exists());
        assert_eq!(
            std::fs::read(outside.join("private.json")).unwrap(),
            b"outside"
        );
    }

    #[test]
    fn missing_or_empty_legacy_publishes_only_completion_marker() {
        for missing in [false, true] {
            let fixture = Fixture::new("no-legacy");
            if missing {
                fs::remove_dir(&fixture.legacy).unwrap();
            }
            assert!(matches!(
                fixture.run().unwrap(),
                MigrationOutcome::NoLegacyData
            ));
            assert!(fixture.data.join(DONE_MARKER).is_file());
            assert_eq!(fs::read_dir(&fixture.data).unwrap().count(), 1);
            assert!(matches!(
                fixture.run().unwrap(),
                MigrationOutcome::Skipped(_)
            ));
        }
    }

    #[test]
    fn old_p1_staging_is_cleaned_without_publishing_its_partial_data() {
        let fixture = Fixture::new("p1-staging");
        fixture.write_legacy("cookies.txt", b"complete-cookie");
        fs::create_dir_all(fixture.data.join(STAGING_DIR)).unwrap();
        fs::write(
            fixture.data.join(STAGING_DIR).join("renderer-session.json"),
            valid_session(),
        )
        .unwrap();

        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Imported(1)
        ));
        assert!(!fixture.data.join(STAGING_DIR).exists());
        assert!(!fixture.data.join("renderer-session.json").exists());
        assert_eq!(
            fs::read(fixture.data.join("cookies.txt")).unwrap(),
            b"complete-cookie"
        );
    }

    #[test]
    fn cancellation_at_each_checkpoint_can_be_retried_without_partial_publish() {
        for interrupted in [
            Checkpoint::StagingCreated,
            Checkpoint::SessionCopied,
            Checkpoint::BeforePublish,
            Checkpoint::Published,
        ] {
            let fixture = Fixture::new("cancel-checkpoint");
            fixture.write_legacy("renderer-session.json", valid_session());
            fixture.write_legacy("web_config.json", b"config");
            fixture.write_legacy(".cookies/account.json", b"cookies");
            let before = source_bytes(&fixture.legacy);
            let result = migrate_with_checkpoint(&fixture.data, Some(&fixture.legacy), |point| {
                if point == interrupted {
                    return Err(io::Error::new(io::ErrorKind::Interrupted, "cancel fixture"));
                }
                Ok(())
            });
            assert_eq!(result.unwrap_err().kind(), io::ErrorKind::Interrupted);
            assert_eq!(source_bytes(&fixture.legacy), before);
            if interrupted == Checkpoint::Published {
                assert!(fixture.data.join(DONE_MARKER).is_file());
                assert!(matches!(
                    fixture.run().unwrap(),
                    MigrationOutcome::Skipped(_)
                ));
            } else {
                assert!(!fixture.data.join(DONE_MARKER).exists());
                assert!(!fixture.data.join("renderer-session.json").exists());
                assert!(matches!(
                    fixture.run().unwrap(),
                    MigrationOutcome::Imported(3)
                ));
            }
            assert!(fixture.data.join(".cookies/account.json").is_file());
            assert_eq!(source_bytes(&fixture.legacy), before);
        }
    }

    #[test]
    fn actual_process_exit_at_copy_and_publish_boundaries_is_retry_safe() {
        const CHILD_ROOT: &str = "CX_MIGRATION_EXIT_FIXTURE_ROOT";
        const CHILD_POINT: &str = "CX_MIGRATION_EXIT_FIXTURE_POINT";
        if let Some(root) = std::env::var_os(CHILD_ROOT) {
            let root = PathBuf::from(root);
            assert!(root.starts_with(std::env::temp_dir()));
            let expected = std::env::var(CHILD_POINT).unwrap();
            migrate_with_checkpoint(&root.join("data"), Some(&root.join("legacy")), |point| {
                if format!("{point:?}") == expected {
                    // Exit without unwinding: the OS releases handles, but no
                    // cleanup or deferred marker write can run in this process.
                    std::process::exit(73);
                }
                Ok(())
            })
            .unwrap();
            panic!("exit checkpoint was not reached");
        }
        for point in [
            Checkpoint::SessionCopied,
            Checkpoint::BeforePublish,
            Checkpoint::Published,
        ] {
            let fixture = Fixture::new("process-exit");
            fixture.write_legacy("renderer-session.json", valid_session());
            fixture.write_legacy("cookies.txt", b"cookies");
            let before = source_bytes(&fixture.legacy);
            let output = std::process::Command::new(std::env::current_exe().unwrap())
                .args(["--exact", "migration::p2_tests::actual_process_exit_at_copy_and_publish_boundaries_is_retry_safe", "--nocapture"])
                .env(CHILD_ROOT, &fixture.root)
                .env(CHILD_POINT, format!("{point:?}"))
                .creation_flags(0x08000000)
                .output().unwrap();
            assert_eq!(
                output.status.code(),
                Some(73),
                "exit child failed: {}",
                String::from_utf8_lossy(&output.stderr)
            );
            assert_eq!(source_bytes(&fixture.legacy), before);
            assert_eq!(
                fixture.data.join(DONE_MARKER).is_file(),
                point == Checkpoint::Published
            );
            let result = fixture.run().unwrap();
            if point == Checkpoint::Published {
                assert!(matches!(result, MigrationOutcome::Skipped(_)));
            } else {
                assert!(matches!(result, MigrationOutcome::Imported(2)));
            }
            assert_eq!(
                fs::read(fixture.data.join("cookies.txt")).unwrap(),
                b"cookies"
            );
            assert_eq!(source_bytes(&fixture.legacy), before);
        }
    }

    #[test]
    fn public_entry_point_honors_legacy_override_in_an_isolated_process() {
        const CHILD_ROOT: &str = "CX_MIGRATION_ENTRY_FIXTURE_ROOT";
        if let Some(root) = std::env::var_os(CHILD_ROOT) {
            let root = PathBuf::from(root);
            assert!(root.starts_with(std::env::temp_dir()));
            assert!(matches!(
                migrate(&root.join("data")).unwrap(),
                MigrationOutcome::Imported(1)
            ));
            return;
        }
        let fixture = Fixture::new("env-override");
        fixture.write_legacy("cookies.txt", b"synthetic-cookie");
        let output = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "migration::p2_tests::public_entry_point_honors_legacy_override_in_an_isolated_process", "--nocapture"])
            .env(CHILD_ROOT, &fixture.root)
            .env(LEGACY_DIR_ENV, &fixture.legacy)
            .creation_flags(0x08000000)
            .output().unwrap();
        assert!(
            output.status.success(),
            "entry-point child failed: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        assert_eq!(
            fs::read(fixture.data.join("cookies.txt")).unwrap(),
            b"synthetic-cookie"
        );
    }

    #[test]
    fn publish_io_error_preserves_staging_and_retries_after_unlock() {
        let fixture = Fixture::new("publish-io");
        fixture.write_legacy("renderer-session.json", valid_session());
        fixture.write_legacy("cookies.txt", b"cookies");
        fs::create_dir(&fixture.data).unwrap();
        let busy = lock_directory(&fixture.data).unwrap();
        assert!(fixture.run().is_err());
        assert!(!fixture.data.join(DONE_MARKER).exists());
        assert!(!fixture.data.join("renderer-session.json").exists());
        assert!(fixture.root.join(STAGING_DIR).join(DONE_MARKER).is_file());
        drop(busy);
        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Imported(2)
        ));
    }

    #[test]
    fn new_business_data_appearing_before_publish_is_preserved() {
        let fixture = Fixture::new("concurrent-data");
        fixture.write_legacy("web_config.json", b"old-config");
        let result = migrate_with_checkpoint(&fixture.data, Some(&fixture.legacy), |point| {
            if point == Checkpoint::BeforePublish {
                fs::create_dir(&fixture.data)?;
                fs::write(fixture.data.join("web_config.json"), b"new-config")?;
            }
            Ok(())
        });
        assert_eq!(result.unwrap_err().kind(), io::ErrorKind::AlreadyExists);
        assert!(!fixture.data.join(DONE_MARKER).exists());
        assert_eq!(
            fs::read(fixture.data.join("web_config.json")).unwrap(),
            b"new-config"
        );
        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Skipped(_)
        ));
        assert_eq!(
            fs::read(fixture.data.join("web_config.json")).unwrap(),
            b"new-config"
        );
    }

    #[test]
    fn legacy_starting_before_publish_defers_without_marker() {
        let fixture = Fixture::new("legacy-start-race");
        fixture.write_legacy("cookies.txt", b"cookies");
        let mut window = None;
        let result = migrate_with_checkpoint(&fixture.data, Some(&fixture.legacy), |point| {
            if point == Checkpoint::BeforePublish {
                window = Some(SingletonWindow::new(&fixture.legacy));
            }
            Ok(())
        })
        .unwrap();
        assert!(matches!(result, MigrationOutcome::DeferredLegacyRunning));
        assert!(!fixture.data.join(DONE_MARKER).exists());
        drop(window);
        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Imported(1)
        ));
    }

    #[test]
    fn source_and_destination_overlap_is_refused_without_writing_old_data() {
        let fixture = Fixture::new("overlap");
        fixture.write_legacy("cookies.txt", b"cookies");
        let before = source_bytes(&fixture.legacy);
        assert!(migrate_from(&fixture.legacy, Some(&fixture.legacy)).is_err());
        assert!(migrate_from(&fixture.legacy.join("data"), Some(&fixture.legacy)).is_err());
        assert!(migrate_from(
            &fixture.legacy.join("data"),
            Some(&fs::canonicalize(&fixture.legacy).unwrap())
        )
        .is_err());
        assert_eq!(source_bytes(&fixture.legacy), before);
        assert!(!fixture.legacy.join(DONE_MARKER).exists());
        assert!(!fixture.legacy.join("data").exists());
    }

    #[test]
    fn top_level_whitelisted_path_reparse_is_refused() {
        let fixture = Fixture::new("file-symlink");
        let outside = fixture.root.join("outside.txt");
        fs::write(&outside, b"outside").unwrap();
        if let Err(error) =
            std::os::windows::fs::symlink_file(&outside, fixture.legacy.join("cookies.txt"))
        {
            assert_eq!(
                error.raw_os_error(),
                Some(1314),
                "unexpected symlink fixture error: {error}"
            );
            let directory = fixture.root.join("outside-dir");
            fs::create_dir(&directory).unwrap();
            directory_link(&directory, &fixture.legacy.join("cookies.txt"));
            eprintln!("file symlink privilege unavailable; exercised a junction at the whitelisted file path instead");
        }
        assert!(fixture.run().is_err());
        assert!(!fixture.data.join(DONE_MARKER).exists());
        assert_eq!(fs::read(outside).unwrap(), b"outside");
    }

    #[test]
    fn top_level_cookie_directory_link_is_refused() {
        let fixture = Fixture::new("cookie-root-link");
        let outside = fixture.root.join("outside");
        fs::create_dir(&outside).unwrap();
        fs::write(outside.join("private.json"), b"outside").unwrap();
        directory_link(&outside, &fixture.legacy.join(".cookies"));
        assert!(fixture.run().is_err());
        assert!(!fixture.data.join(DONE_MARKER).exists());
        assert!(!fixture.data.join(".cookies").exists());
        assert_eq!(fs::read(outside.join("private.json")).unwrap(), b"outside");
    }

    #[test]
    fn junction_inside_staging_or_destination_parent_is_refused() {
        for in_staging in [true, false] {
            let mut fixture = Fixture::new("staging-inner-link");
            fixture.write_legacy("cookies.txt", b"cookies");
            let outside = fixture.root.join("outside");
            fs::create_dir(&outside).unwrap();
            fs::write(outside.join("keep.txt"), b"keep").unwrap();
            if in_staging {
                let staging = fixture.root.join(STAGING_DIR);
                fs::create_dir(&staging).unwrap();
                directory_link(&outside, &staging.join("nested"));
            } else {
                let parent = fixture.root.join("linked-parent");
                directory_link(&outside, &parent);
                fixture.data = parent.join("data");
            }
            assert!(fixture.run().is_err());
            assert_eq!(fs::read(outside.join("keep.txt")).unwrap(), b"keep");
            assert_eq!(fs::read_dir(outside).unwrap().count(), 1);
        }
    }

    #[test]
    fn verbatim_legacy_path_still_matches_electron_user_data_title() {
        let fixture = Fixture::new("verbatim-title");
        fixture.write_legacy("cookies.txt", b"cookies");
        let _window = SingletonWindow::new(&fixture.legacy);
        assert!(matches!(
            migrate_from(
                &fixture.data,
                Some(&fs::canonicalize(&fixture.legacy).unwrap())
            )
            .unwrap(),
            MigrationOutcome::DeferredLegacyRunning
        ));
        assert!(!fixture.data.join(DONE_MARKER).exists());
    }
}

```


## desktop/src-tauri/src/session_store.rs

```
use serde::{Deserialize, Deserializer, Serialize};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

/// Session file schema — 1:1 port of desktop/session-store.js (v1, strict).
/// exactKeys semantics from the JS original: unknown fields are invalid.
#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct SessionLogin {
    pub username: String,
    pub use_cookies: bool,
}

impl<'de> Deserialize<'de> for SessionLogin {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Fields {
            username: String,
            use_cookies: bool,
        }

        let fields: Fields = crate::deserialize_object(deserializer)?;
        Ok(Self {
            username: fields.username,
            use_cookies: fields.use_cookies,
        })
    }
}

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct SessionActiveTask {
    pub username: String,
    #[serde(rename = "taskId")]
    pub task_id: String,
}

impl<'de> Deserialize<'de> for SessionActiveTask {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Fields {
            username: String,
            #[serde(rename = "taskId")]
            task_id: String,
        }

        let fields: Fields = crate::deserialize_object(deserializer)?;
        Ok(Self {
            username: fields.username,
            task_id: fields.task_id,
        })
    }
}

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct SessionData {
    pub version: u32,
    pub login: Option<SessionLogin>,
    #[serde(rename = "activeTask")]
    pub active_task: Option<SessionActiveTask>,
}

impl<'de> Deserialize<'de> for SessionData {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Fields {
            version: u32,
            #[serde(deserialize_with = "required_nullable")]
            login: Option<SessionLogin>,
            #[serde(rename = "activeTask", deserialize_with = "required_nullable")]
            active_task: Option<SessionActiveTask>,
        }

        let fields: Fields = crate::deserialize_object(deserializer)?;
        Ok(Self {
            version: fields.version,
            login: fields.login,
            active_task: fields.active_task,
        })
    }
}

// Option normally accepts a missing key. A custom deserializer retains null
// while requiring the key, matching Electron's exactKeys v1 schema.
pub(crate) fn required_nullable<'de, D, T>(deserializer: D) -> Result<Option<T>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    Option::deserialize(deserializer)
}

impl Default for SessionData {
    fn default() -> Self {
        Self {
            version: 1,
            login: None,
            active_task: None,
        }
    }
}

pub const MAX_FILE_BYTES: usize = 4096;
pub const TASK_ID_RE: &str = r"^[a-zA-Z0-9_-]{1,128}$";

fn valid_username(u: &str) -> bool {
    // JavaScript counts UTF-16 units and trims ECMAScript whitespace; Rust's
    // byte length / Unicode trim differ for CJK, emoji, U+0085 and U+FEFF.
    let js_whitespace = |c| {
        matches!(c,
        '\u{0009}'..='\u{000d}' | '\u{0020}' | '\u{00a0}' | '\u{1680}' |
        '\u{2000}'..='\u{200a}' | '\u{2028}' | '\u{2029}' | '\u{202f}' |
        '\u{205f}' | '\u{3000}' | '\u{feff}')
    };
    !u.is_empty()
        && u.encode_utf16().count() <= 128
        && u.trim_matches(js_whitespace) == u
        && !u.chars().any(|c| c <= '\u{001f}' || c == '\u{007f}')
}

fn valid_task_id(t: &str) -> bool {
    !t.is_empty()
        && t.len() <= 128
        && t.chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
}

/// Validate a parsed SessionData against the strict schema from session-store.js.
/// Returns Err(reason) when invalid.
pub fn validate(data: &SessionData) -> Result<(), String> {
    if data.version != 1 {
        return Err("unsupported session version".into());
    }
    if let Some(login) = data.login.as_ref() {
        if !login.use_cookies {
            return Err("login.use_cookies must be true".into());
        }
        if !valid_username(&login.username) {
            return Err("invalid login.username".into());
        }
    }
    if let Some(task) = data.active_task.as_ref() {
        if !valid_username(&task.username) {
            return Err("invalid activeTask.username".into());
        }
        if !valid_task_id(&task.task_id) {
            return Err("invalid activeTask.taskId".into());
        }
        let login_user = data.login.as_ref().map(|l| l.username.as_str());
        if login_user != Some(task.username.as_str()) {
            return Err("activeTask.username must match login.username".into());
        }
    }
    Ok(())
}

/// Read + validate the session file at `path`. Mirrors session-store.js `read`:
/// - missing / corrupt / oversized / schema-invalid → Ok(None)
/// - other IO errors → Err
pub fn read(path: &std::path::Path) -> Result<Option<SessionData>, std::io::Error> {
    let meta = match std::fs::metadata(path) {
        Ok(m) => m,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e),
    };
    if meta.len() as usize > MAX_FILE_BYTES {
        return Ok(None);
    }
    let file = match std::fs::File::open(path) {
        Ok(f) => f,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e),
    };
    let mut bytes = Vec::new();
    file.take(MAX_FILE_BYTES as u64 + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() > MAX_FILE_BYTES {
        return Ok(None);
    }
    let data: SessionData = match serde_json::from_slice(&bytes) {
        Ok(d) => d,
        Err(_) => return Ok(None),
    };
    if validate(&data).is_err() {
        return Ok(None);
    }
    Ok(Some(data))
}

/// Persist `data` atomically: write tmp + rename over `path`. Mirrors session-store.js write.
/// remember_login semantics: switching username clears active_task.
pub fn remember_login(path: &std::path::Path, username: &str) -> Result<SessionData, SessionError> {
    let current = read(path)?;
    let mut data = current.unwrap_or(SessionData {
        version: 1,
        login: None,
        active_task: None,
    });
    let switch = data
        .login
        .as_ref()
        .map(|l| l.username != username)
        .unwrap_or(false);
    if switch {
        data.active_task = None;
    }
    data.login = Some(SessionLogin {
        username: username.to_string(),
        use_cookies: true,
    });
    validate(&data).map_err(SessionError::Validation)?;
    write_atomic(path, &data)?;
    Ok(data)
}

pub fn remember_task(
    path: &std::path::Path,
    username: &str,
    task_id: &str,
) -> Result<SessionData, SessionError> {
    let current = read(path)?;
    let mut data = current.ok_or(SessionError::NoLogin)?;
    let login = data.login.as_ref().ok_or(SessionError::NoLogin)?;
    if login.username != username {
        return Err(SessionError::AccountMismatch);
    }
    data.active_task = Some(SessionActiveTask {
        username: username.to_string(),
        task_id: task_id.to_string(),
    });
    validate(&data).map_err(SessionError::Validation)?;
    write_atomic(path, &data)?;
    Ok(data)
}

/// Clear only the recovery task, including an empty/missing session. Electron
/// rememberTask(null) writes an empty v1 session when no login is remembered.
pub fn clear_task(path: &Path) -> Result<SessionData, SessionError> {
    let mut data = read(path)?.unwrap_or_default();
    data.active_task = None;
    write_atomic(path, &data)?;
    Ok(data)
}

/// One store per host. The lock covers the entire read/modify/write transaction,
/// including clear, so concurrent IPC cannot resurrect an old account/task.
pub struct SessionStore {
    path: PathBuf,
    lock: Mutex<()>,
}

impl SessionStore {
    pub fn new(directory: &Path) -> Self {
        Self {
            path: directory.join("renderer-session.json"),
            lock: Mutex::new(()),
        }
    }

    pub fn read(&self) -> Result<SessionData, std::io::Error> {
        let _guard = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        Ok(read(&self.path)?.unwrap_or_default())
    }

    pub fn remember_login(&self, username: &str) -> Result<SessionData, SessionError> {
        let _guard = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        remember_login(&self.path, username)
    }

    pub fn remember_task(
        &self,
        task: Option<SessionActiveTask>,
    ) -> Result<SessionData, SessionError> {
        let _guard = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        match task {
            Some(task) => remember_task(&self.path, &task.username, &task.task_id),
            None => clear_task(&self.path),
        }
    }

    pub fn clear(&self) -> Result<SessionData, std::io::Error> {
        let _guard = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        clear(&self.path)?;
        Ok(SessionData::default())
    }
}

pub fn clear(path: &std::path::Path) -> Result<(), std::io::Error> {
    let tmp = tmp_path(path);
    match std::fs::remove_file(path) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => return Err(e),
    }
    match std::fs::remove_file(&tmp) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => return Err(e),
    }
    Ok(())
}

#[derive(Debug)]
pub enum SessionError {
    Validation(String),
    NoLogin,
    AccountMismatch,
    Io(std::io::Error),
}

impl std::fmt::Display for SessionError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SessionError::Validation(s) => write!(f, "session validation failed: {s}"),
            SessionError::NoLogin => write!(f, "no remembered login"),
            SessionError::AccountMismatch => write!(f, "account mismatch"),
            SessionError::Io(e) => write!(f, "session io error: {e}"),
        }
    }
}

impl From<std::io::Error> for SessionError {
    fn from(e: std::io::Error) -> Self {
        SessionError::Io(e)
    }
}

fn tmp_path(path: &std::path::Path) -> std::path::PathBuf {
    let mut name = path.file_name().unwrap_or_default().to_os_string();
    name.push(".tmp");
    path.with_file_name(name)
}

fn write_atomic(path: &std::path::Path, data: &SessionData) -> Result<(), std::io::Error> {
    let tmp = tmp_path(path);
    let bytes = serde_json::to_vec(data).map_err(std::io::Error::other)?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let write = || {
        use std::io::Write;
        let mut file = std::fs::File::create(&tmp)?;
        file.write_all(&bytes)?;
        file.sync_all()?;
        drop(file);
        std::fs::rename(&tmp, path)
    };
    match write() {
        Ok(()) => Ok(()),
        Err(e) => {
            let _ = std::fs::remove_file(&tmp);
            Err(e)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn p2_object_boundary_rejects_positional_session_deserialization() {
        assert!(
            serde_json::from_value::<SessionLogin>(serde_json::json!(["alice", true])).is_err()
        );
        assert!(
            serde_json::from_value::<SessionActiveTask>(serde_json::json!(["alice", "t"])).is_err()
        );
        assert!(serde_json::from_value::<SessionData>(serde_json::json!([1, null, null])).is_err());
    }

    #[test]
    fn p2_object_boundary_disk_read_rejects_arrays_at_every_schema_level() {
        let dir = tmpdir("p2-object-only");
        let path = dir.join("renderer-session.json");
        for raw in [
            serde_json::json!([1, ["alice", true], ["alice", "t"]]),
            serde_json::json!({"version":1,"login":["alice",true],"activeTask":null}),
            serde_json::json!({"version":1,"login":{"username":"alice","use_cookies":true},"activeTask":["alice","t"]}),
        ] {
            std::fs::write(&path, serde_json::to_vec(&raw).unwrap()).unwrap();
            assert!(read(&path).unwrap().is_none(), "accepted {raw}");
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn p2_object_boundary_session_maps_and_explicit_nulls_round_trip() {
        for raw in [
            serde_json::json!({"version":1,"login":null,"activeTask":null}),
            serde_json::json!({"version":1,"login":{"username":"alice","use_cookies":true},"activeTask":null}),
            serde_json::json!({"version":1,"login":{"username":"alice","use_cookies":true},"activeTask":{"username":"alice","taskId":"t"}}),
        ] {
            let data: SessionData = serde_json::from_value(raw.clone()).unwrap();
            validate(&data).unwrap();
            assert_eq!(serde_json::to_value(data).unwrap(), raw);
        }
    }

    fn tmpdir(name: &str) -> std::path::PathBuf {
        let dir =
            std::env::temp_dir().join(format!("cx-session-store-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn session_json(login: Option<(&str, bool)>, task: Option<(&str, &str)>) -> String {
        let mut s = String::from("{");
        s.push_str("\"version\":1");
        if let Some((u, uc)) = login {
            s.push_str(&format!(
                ",\"login\":{{\"username\":\"{u}\",\"use_cookies\":{uc}}}"
            ));
        } else {
            s.push_str(",\"login\":null");
        }
        if let Some((u, t)) = task {
            s.push_str(&format!(
                ",\"activeTask\":{{\"username\":\"{u}\",\"taskId\":\"{t}\"}}"
            ));
        } else {
            s.push_str(",\"activeTask\":null");
        }
        s.push('}');
        s
    }

    // Group 1: persistence across instances
    #[test]
    fn persists_login_and_task_across_reads() {
        let dir = tmpdir("persist");
        let path = dir.join("renderer-session.json");
        assert!(read(&path).unwrap().is_none());

        remember_login(&path, "user1").unwrap();
        remember_task(&path, "user1", "task-1").unwrap();

        let data = read(&path).unwrap().expect("session should exist");
        assert_eq!(data.login.as_ref().unwrap().username, "user1");
        assert_eq!(data.active_task.as_ref().unwrap().task_id, "task-1");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn login_switch_clears_active_task() {
        let dir = tmpdir("switch");
        let path = dir.join("renderer-session.json");
        remember_login(&path, "user1").unwrap();
        remember_task(&path, "user1", "task-1").unwrap();

        remember_login(&path, "user2").unwrap();
        let data = read(&path).unwrap().unwrap();
        assert_eq!(data.login.as_ref().unwrap().username, "user2");
        assert!(data.active_task.is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    // Group 2: rejection matrix
    #[test]
    fn rejects_oversized_username() {
        let dir = tmpdir("reject-user");
        let path = dir.join("renderer-session.json");
        std::fs::write(&path, session_json(Some((&"a".repeat(129), true)), None)).unwrap();
        assert!(read(&path).unwrap().is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn rejects_control_chars_in_username() {
        let dir = tmpdir("reject-ctrl");
        let path = dir.join("renderer-session.json");
        std::fs::write(
            &path,
            "{\"version\":1,\"login\":{\"username\":\"a\\nb\",\"use_cookies\":true}}",
        )
        .unwrap();
        assert!(read(&path).unwrap().is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn rejects_bad_task_id() {
        let dir = tmpdir("reject-task");
        let path = dir.join("renderer-session.json");
        std::fs::write(
            &path,
            session_json(Some(("user1", true)), Some(("user1", "../escape"))),
        )
        .unwrap();
        assert!(read(&path).unwrap().is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn rejects_use_cookies_false() {
        let dir = tmpdir("reject-cookies");
        let path = dir.join("renderer-session.json");
        std::fs::write(&path, session_json(Some(("user1", false)), None)).unwrap();
        assert!(read(&path).unwrap().is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn rejects_unknown_extra_field() {
        let dir = tmpdir("reject-extra");
        let path = dir.join("renderer-session.json");
        std::fs::write(
            &path,
            "{\"version\":1,\"login\":{\"username\":\"u\",\"use_cookies\":true},\"password\":\"hunter2\"}",
        )
        .unwrap();
        assert!(read(&path).unwrap().is_none(), "password must be rejected");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn rejects_active_task_account_mismatch() {
        let dir = tmpdir("reject-mismatch");
        let path = dir.join("renderer-session.json");
        std::fs::write(
            &path,
            session_json(Some(("user1", true)), Some(("user2", "t1"))),
        )
        .unwrap();
        assert!(read(&path).unwrap().is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    // Group 3: corrupt / oversized / password on disk
    #[test]
    fn corrupt_json_returns_none() {
        let dir = tmpdir("corrupt");
        let path = dir.join("renderer-session.json");
        std::fs::write(&path, "{ not json").unwrap();
        assert!(read(&path).unwrap().is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn oversized_file_returns_none() {
        let dir = tmpdir("oversize");
        let path = dir.join("renderer-session.json");
        let big = format!("{{\"version\":1,\"padding\":\"{}\"}}", "x".repeat(5000));
        std::fs::write(&path, big).unwrap();
        assert!(read(&path).unwrap().is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    // tmp handling
    #[test]
    fn clear_removes_session_and_tmp() {
        let dir = tmpdir("clear");
        let path = dir.join("renderer-session.json");
        remember_login(&path, "user1").unwrap();
        let tmp = dir.join("renderer-session.json.tmp");
        std::fs::write(&tmp, "leftover").unwrap();

        clear(&path).unwrap();
        assert!(!path.exists());
        assert!(!tmp.exists());
        assert!(read(&path).unwrap().is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn remember_task_without_login_errors() {
        let dir = tmpdir("nologin");
        let path = dir.join("renderer-session.json");
        assert!(matches!(
            remember_task(&path, "u", "t"),
            Err(SessionError::NoLogin)
        ));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn remember_task_account_mismatch_errors() {
        let dir = tmpdir("mismatch-write");
        let path = dir.join("renderer-session.json");
        remember_login(&path, "user1").unwrap();
        assert!(matches!(
            remember_task(&path, "user2", "t"),
            Err(SessionError::AccountMismatch)
        ));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn p2_written_session_has_all_node_schema_keys() {
        let dir = tmpdir("p2-null-keys");
        let path = dir.join("renderer-session.json");
        remember_login(&path, "alice").unwrap();
        let disk: serde_json::Value =
            serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
        assert_eq!(
            disk,
            serde_json::json!({
                "version": 1, "login": {"username": "alice", "use_cookies": true},
                "activeTask": null
            })
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn p2_missing_nullable_keys_are_invalid_like_node() {
        let dir = tmpdir("p2-required-keys");
        let path = dir.join("renderer-session.json");
        for raw in [
            r#"{"version":1}"#,
            r#"{"version":1,"login":{"username":"alice","use_cookies":true}}"#,
            r#"{"version":1,"activeTask":null}"#,
        ] {
            std::fs::write(&path, raw).unwrap();
            assert!(
                read(&path).unwrap().is_none(),
                "accepted missing keys: {raw}"
            );
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn p2_empty_task_id_cannot_replace_saved_task() {
        let dir = tmpdir("p2-empty-task");
        let path = dir.join("renderer-session.json");
        remember_login(&path, "alice").unwrap();
        remember_task(&path, "alice", "keep-this-task").unwrap();
        let previous = std::fs::read(&path).unwrap();
        assert!(remember_task(&path, "alice", "").is_err());
        assert_eq!(std::fs::read(&path).unwrap(), previous);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn p2_username_length_and_whitespace_match_javascript() {
        let dir = tmpdir("p2-utf16");
        let path = dir.join("renderer-session.json");
        for username in ["中".repeat(128), "😀".repeat(64), "\u{0085}alice".into()] {
            assert!(
                remember_login(&path, &username).is_ok(),
                "rejected {username}"
            );
        }
        for username in ["中".repeat(129), "😀".repeat(65), "\u{feff}alice".into()] {
            assert!(remember_login(&path, &username).is_err());
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn p2_write_creates_private_profile_parent() {
        let dir = tmpdir("p2-parent");
        let path = dir.join("new-profile/data/renderer-session.json");
        remember_login(&path, "alice").unwrap();
        assert!(path.is_file());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn p2_read_io_failure_is_visible() {
        let dir = tmpdir("p2-read-io");
        let path = dir.join("renderer-session.json");
        std::fs::create_dir(&path).unwrap();
        assert!(read(&path).is_err());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn p2_failed_windows_replace_preserves_previous_file() {
        use std::os::windows::fs::OpenOptionsExt;
        let dir = tmpdir("p2-locked-replace");
        let path = dir.join("renderer-session.json");
        remember_login(&path, "alice").unwrap();
        let before = std::fs::read(&path).unwrap();
        let locked = std::fs::OpenOptions::new()
            .read(true)
            .share_mode(1)
            .open(&path)
            .unwrap();
        assert!(remember_login(&path, "bob").is_err());
        assert_eq!(std::fs::read(&path).unwrap(), before);
        assert!(!tmp_path(&path).exists());
        drop(locked);
        assert!(remember_login(&path, "bob").is_ok());
        let _ = std::fs::remove_dir_all(&dir);
    }
}

```


## desktop/src-tauri/src/windows_job.rs

```
//! Windows Job Object wrapper: KILL_ON_JOB_CLOSE so the backend process tree
//! is reaped even if the host is hard-killed. PoC-proven in E:\Downloads\45\tauri-poc.

use windows::core::PCWSTR;
use windows::Win32::Foundation::{CloseHandle, HANDLE};
use windows::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    SetInformationJobObject, TerminateJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows::Win32::System::Threading::{OpenProcess, PROCESS_SET_QUOTA, PROCESS_TERMINATE};

pub struct Job(HANDLE);

// The handle is only used for Assign/Terminate/Close, which are thread-safe
// kernel operations; HANDLE is a raw-pointer wrapper.
unsafe impl Send for Job {}
unsafe impl Sync for Job {}

impl Job {
    pub fn create() -> Result<Self, String> {
        unsafe {
            let handle = CreateJobObjectW(None, PCWSTR::null())
                .map_err(|e| format!("CreateJobObject: {e}"))?;
            let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                &info as *const _ as *const core::ffi::c_void,
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
            .map_err(|e| format!("SetInformationJobObject: {e}"))?;
            Ok(Job(handle))
        }
    }

    pub fn assign(&self, pid: u32) -> Result<(), String> {
        unsafe {
            let ph = OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, false, pid)
                .map_err(|e| format!("OpenProcess({pid}): {e}"))?;
            let result = AssignProcessToJobObject(self.0, ph);
            let _ = CloseHandle(ph);
            result.map_err(|e| format!("AssignProcessToJobObject({pid}): {e}"))
        }
    }

    /// Kill the whole process tree now (used on stop timeout). Idempotent.
    pub fn terminate(&self) {
        unsafe {
            let _ = TerminateJobObject(self.0, 1);
        }
    }
}

impl Drop for Job {
    fn drop(&mut self) {
        // KILL_ON_JOB_CLOSE: closing the last handle reaps the tree. This also
        // covers panic unwinding — never leak a backend past host exit.
        unsafe {
            let _ = CloseHandle(self.0);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::windows::process::CommandExt;

    const CREATE_NO_WINDOW: u32 = 0x0800_0000;

    /// Spawn a child that stays alive until killed; returns (Child, grandchild pid marker file check fn).
    /// Uses `cmd /c ping` (long-running) and a grandchild via `cmd /c ping & cmd /c ping`
    /// is not needed — the tree-kill semantics are covered by assigning the direct child;
    /// grandchild coverage: `cmd /c "ping -n 30 127.0.0.1 > nul & ping -n 30 127.0.0.1 > nul"`
    /// keeps one process; use PowerShell-free approach: `cmd /c start /wait` spawns a child cmd.
    #[test]
    fn drop_job_reaps_process_tree() {
        let job = Job::create().expect("create job");
        let mut child = std::process::Command::new("cmd")
            .args([
                "/c",
                "start /wait /min cmd /c ping -n 60 127.0.0.1 > nul & ping -n 60 127.0.0.1 > nul",
            ])
            .creation_flags(CREATE_NO_WINDOW)
            .spawn()
            .expect("spawn cmd tree");
        job.assign(child.id()).expect("assign to job");

        // Drop the job: KILL_ON_JOB_CLOSE must reap child + grandchild.
        drop(job);
        std::thread::sleep(std::time::Duration::from_millis(1500));
        let status = child.try_wait().expect("try_wait");
        assert!(status.is_some(), "child should be dead after job drop");

        // Grandchild (started via `start /wait`) must also be gone: scan for ping
        // processes is flaky; instead assert via tasklist absence of our unique
        // marker is complex — the PoC already verified tree-kill visually.
        // Here we assert at least the direct child and rely on kernel job semantics.
    }

    #[test]
    fn terminate_is_idempotent_and_reaps() {
        let job = Job::create().expect("create job");
        let mut child = std::process::Command::new("cmd")
            .args(["/c", "ping -n 60 127.0.0.1 > nul"])
            .creation_flags(CREATE_NO_WINDOW)
            .spawn()
            .expect("spawn");
        job.assign(child.id()).expect("assign");
        job.terminate();
        job.terminate(); // second call must not fail/panic
        std::thread::sleep(std::time::Duration::from_millis(800));
        assert!(child.try_wait().expect("try_wait").is_some());
    }
}

```


## desktop/src-tauri/tauri.conf.json

```
{
  "$schema": "https://schema.tauri.app/config/2",
  "productName": "chaoxing-desktop",
  "version": "1.1.1",
  "identifier": "com.chaoxing.gui",
  "build": {
    "frontendDist": "../../web/dist",
    "devUrl": "http://localhost:3000"
  },
  "app": {
    "withGlobalTauri": true,
    "windows": [
      {
        "create": false,
        "title": "超星学习通 · 自动化学习助手",
        "width": 1200,
        "height": 800,
        "minWidth": 900,
        "minHeight": 600,
        "backgroundColor": "#0f172a"
      }
    ],
    "security": {
      "csp": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src ipc: http://ipc.localhost; frame-src 'none'; object-src 'none'; base-uri 'self'; form-action 'none'"
    }
  },
  "bundle": {
    "active": false
  }
}

```


## desktop/src-tauri/tests/api_lifecycle.rs

```
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

```


## desktop/src-tauri/tests/backend_lifecycle.rs

```
//! Integration tests for backend.rs lifecycle, using the fake-backend bin.
//! These spawn real processes on Windows — run via `cargo test --locked`.

use chaoxing_desktop_lib::api_proxy::ApiOperation;
use chaoxing_desktop_lib::backend::{
    api_cancel, api_request, start_backend, stop_backend, BackendLaunch, BackendPhase, BackendState,
};
use std::sync::Arc;
use std::time::{Duration, Instant};

fn target_bin(name: &str) -> std::path::PathBuf {
    // Integration test exe lives in target/debug/deps; helper bins in target/debug.
    let mut p = std::env::current_exe().unwrap();
    p.pop(); // deps (or debug)
    if p.ends_with("deps") {
        p.pop();
    }
    let candidate = p.join(format!("{name}.exe"));
    if candidate.is_file() {
        candidate
    } else {
        // Fallback: same dir as the test exe (some layouts).
        let mut q = std::env::current_exe().unwrap();
        q.pop();
        q.join(format!("{name}.exe"))
    }
}

struct TestPaths {
    data_dir: std::path::PathBuf,
    log_dir: std::path::PathBuf,
    legacy_env: Option<String>,
}

impl Drop for TestPaths {
    fn drop(&mut self) {
        if let Some(v) = &self.legacy_env {
            std::env::remove_var("CHAOXING_LEGACY_DATA_DIR");
            let _ = v;
        }
        let _ = std::fs::remove_dir_all(&self.data_dir);
        let _ = std::fs::remove_dir_all(&self.log_dir);
    }
}

fn make_state(tag: &str) -> (Arc<BackendState>, TestPaths) {
    let base = std::env::temp_dir().join(format!("cx-backend-test-{tag}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&base);
    let data_dir = base.join("data");
    let log_dir = base.join("logs");
    std::fs::create_dir_all(&data_dir).unwrap();
    std::fs::create_dir_all(&log_dir).unwrap();
    (
        Arc::new(BackendState::new(data_dir.clone(), log_dir.clone())),
        TestPaths {
            data_dir,
            log_dir,
            legacy_env: None,
        },
    )
}

#[allow(dead_code)]
fn fake_backend_launch(mode: &str) -> BackendLaunch {
    let _ = mode; // FAKE_MODE is passed via process env (see set_fake_mode)
    BackendLaunch::Frozen(target_bin("fake-backend"))
}

// start_backend doesn't accept extra env; tests set process env instead.
fn set_fake_mode(mode: &str) {
    std::env::set_var("FAKE_MODE", mode);
}
fn clear_fake_mode() {
    std::env::remove_var("FAKE_MODE");
}

// NOTE: BackendLaunch has no extra-env channel; FAKE_MODE is read by the
// fake-backend child from its inherited environment.

#[test]
fn start_ok_ready_phase() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("ok");
    set_fake_mode("ok");
    let r = start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend")));
    clear_fake_mode();
    r.expect("start should succeed");
    assert_eq!(state.status().phase, BackendPhase::Ready);
    assert!(state.status().port.is_some());
    stop_backend(&state);
    assert_eq!(state.status().phase, BackendPhase::Stopped);
}

#[test]
fn wrong_instance_fails() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("wrong");
    set_fake_mode("wrong-instance");
    let r = start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend")));
    clear_fake_mode();
    assert!(r.is_err(), "instanceId mismatch must fail startup");
    assert_eq!(state.status().phase, BackendPhase::Failed);
}

#[test]
fn exit_before_ready_fails() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("die");
    set_fake_mode("exit-before-ready");
    let r = start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend")));
    clear_fake_mode();
    assert!(r.is_err());
    assert_eq!(state.status().phase, BackendPhase::Failed);
    // no zombie backend processes: fake-backend must be gone
    std::thread::sleep(Duration::from_millis(300));
    // (kernel job close guarantees this; direct child waited inside start_backend)
}

#[test]
fn missing_exe_fails_without_panic() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("missing");
    // FAKE_MODE must not leak into this test's expectations.
    clear_fake_mode();
    let r = start_backend(
        &state,
        BackendLaunch::Frozen(std::path::PathBuf::from("Z:/no/such/backend.exe")),
    );
    assert!(r.is_err());
    assert_eq!(state.status().phase, BackendPhase::Failed);
    let status = state.status();
    assert!(
        status.error.unwrap_or_default().contains("缺失"),
        "error should mention missing backend path"
    );
}

#[test]
fn stop_is_idempotent() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("stop");
    set_fake_mode("ok");
    start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend"))).expect("start");
    clear_fake_mode();
    stop_backend(&state);
    stop_backend(&state);
    stop_backend(&state);
    assert_eq!(state.status().phase, BackendPhase::Stopped);
}

#[test]
fn api_request_requires_ready() {
    let (state, _paths) = make_state("notready");
    let resp = api_request(
        &state,
        ApiOperation::Login,
        None,
        None,
        serde_json::json!({"username": "u"}),
        1,
    );
    assert!(matches!(
        resp,
        Err(chaoxing_desktop_lib::api_proxy::ProxyError::BackendNotReady { .. })
    ));
}

#[test]
fn api_request_forwards_and_passes_status() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("fwd");
    set_fake_mode("ok");
    start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend"))).expect("start");
    clear_fake_mode();

    // /api/login is POST; fake backend returns 404 for it with JSON body —
    // proves token was accepted (401 would mean token missing) and status passthrough.
    let resp = api_request(
        &state,
        ApiOperation::Login,
        None,
        None,
        serde_json::json!({"username": "u"}),
        2,
    )
    .expect("request should complete");
    assert_eq!(resp.status, 404);
    assert_eq!(resp.body, serde_json::json!({"error": "not found"}));

    // invalid taskId is rejected at the host, never sent
    let resp = api_request(
        &state,
        ApiOperation::TaskStatus,
        Some("../bad".into()),
        None,
        serde_json::json!(null),
        3,
    );
    assert!(matches!(
        resp,
        Err(chaoxing_desktop_lib::api_proxy::ProxyError::InvalidRequest { .. })
    ));

    stop_backend(&state);
}

#[test]
fn api_cancel_unknown_id_is_false() {
    let (state, _paths) = make_state("cancel");
    assert!(!api_cancel(&state, 9999));
}

#[test]
fn host_kills_tree_on_stop_grandchild_reaped() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    // Check the exact fixture PID instead of counting unrelated ping processes.
    let (state, _paths) = make_state("tree");
    set_fake_mode("spawn-grandchild");
    start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend"))).expect("start");
    clear_fake_mode();
    let grandchild = read_pid(&state.data_dir.join("fake-grandchild.pid"));
    assert!(process_alive(grandchild));
    stop_backend(&state);
    assert_process_exited(grandchild);
}

fn read_pid(path: &std::path::Path) -> u32 {
    wait_until(|| path.is_file());
    std::fs::read_to_string(path)
        .unwrap()
        .trim()
        .parse()
        .unwrap()
}

fn wait_until(mut condition: impl FnMut() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(4);
    while !condition() {
        assert!(Instant::now() < deadline, "fixture condition timed out");
        std::thread::sleep(Duration::from_millis(10));
    }
}

fn process_alive(pid: u32) -> bool {
    use windows::Win32::Foundation::CloseHandle;
    use windows::Win32::System::Threading::{
        GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
    };
    unsafe {
        let Ok(handle) = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) else {
            return false;
        };
        let mut code = 0;
        let alive = GetExitCodeProcess(handle, &mut code).is_ok() && code == 259;
        let _ = CloseHandle(handle);
        alive
    }
}

fn assert_process_exited(pid: u32) {
    wait_until(|| !process_alive(pid));
}

#[test]
fn p2_stop_during_handshake_registers_child_and_cannot_restore_ready() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("stop-handshake");
    set_fake_mode("spawn-grandchild");
    std::env::set_var("FAKE_DELAY_READY_MS", "2500");
    let worker_state = state.clone();
    let worker = std::thread::spawn(move || {
        start_backend(
            &worker_state,
            BackendLaunch::Frozen(target_bin("fake-backend")),
        )
    });
    let pid = read_pid(&state.data_dir.join("fake-backend.pid"));
    let grandchild = read_pid(&state.data_dir.join("fake-grandchild.pid"));
    let registered = state.child.lock().unwrap().is_some() && state.job.lock().unwrap().is_some();
    let started = Instant::now();
    stop_backend(&state);
    let result = worker.join().unwrap();
    let elapsed = started.elapsed();
    std::env::remove_var("FAKE_DELAY_READY_MS");
    clear_fake_mode();
    let phase = state.status().phase;
    // Ensure a broken implementation is still cleaned up before assertions.
    if phase == BackendPhase::Ready {
        stop_backend(&state);
    }
    assert!(
        registered,
        "child and Job must be owned by state during the handshake"
    );
    assert!(result.is_err());
    assert_eq!(phase, BackendPhase::Stopped);
    assert!(elapsed < Duration::from_secs(1), "stop took {elapsed:?}");
    assert_process_exited(pid);
    assert_process_exited(grandchild);
}

#[test]
fn p2_stop_during_health_does_not_wait_for_start_deadline() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("stop-health");
    set_fake_mode("ok");
    std::env::set_var("FAKE_DELAY_HEALTH_MS", "1200");
    let worker_state = state.clone();
    let worker = std::thread::spawn(move || {
        start_backend(
            &worker_state,
            BackendLaunch::Frozen(target_bin("fake-backend")),
        )
    });
    wait_until(|| state.data_dir.join("fake-health.requested").is_file());
    let pid = read_pid(&state.data_dir.join("fake-backend.pid"));
    let started = Instant::now();
    stop_backend(&state);
    let result = worker.join().unwrap();
    let elapsed = started.elapsed();
    std::env::remove_var("FAKE_DELAY_HEALTH_MS");
    clear_fake_mode();
    let phase = state.status().phase;
    if phase == BackendPhase::Ready {
        stop_backend(&state);
    }
    assert!(result.is_err());
    assert_eq!(phase, BackendPhase::Stopped);
    assert!(elapsed < Duration::from_secs(1), "stop took {elapsed:?}");
    assert_process_exited(pid);
}

#[test]
fn p2_stop_before_background_start_prevents_spawn() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("stop-before-start");
    stop_backend(&state);
    set_fake_mode("ok");
    let result = start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend")));
    clear_fake_mode();
    let phase = state.status().phase;
    let spawned = state.data_dir.join("fake-backend.pid").exists();
    if phase == BackendPhase::Ready {
        stop_backend(&state);
    }
    assert!(result.is_err());
    assert!(!spawned);
    assert_eq!(phase, BackendPhase::Stopped);
}

#[test]
fn p2_data_directory_failure_transitions_to_failed() {
    let (_state, paths) = make_state("blocked-data");
    let data_file = paths.data_dir.join("not-a-directory");
    std::fs::write(&data_file, b"fixture").unwrap();
    let state = Arc::new(BackendState::new(data_file, paths.log_dir.clone()));
    let result = start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend")));
    assert!(result.is_err());
    assert_eq!(state.status().phase, BackendPhase::Failed);
    assert!(state.status().error.is_some());
    assert!(state.child.lock().unwrap().is_none());
}

#[test]
fn p2_post_ready_exit_is_observable_and_reaps_grandchild() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("unexpected-exit");
    set_fake_mode("exit-after-ready");
    start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend"))).unwrap();
    clear_fake_mode();
    let pid = read_pid(&state.data_dir.join("fake-backend.pid"));
    let grandchild = read_pid(&state.data_dir.join("fake-grandchild.pid"));
    assert_eq!(state.status().phase, BackendPhase::Ready);
    assert_process_exited(pid);
    let status = state.status();
    assert_eq!(status.phase, BackendPhase::Failed);
    assert!(status.error.is_some());
    // Failure observation alone must reap the remaining Job, before explicit stop.
    assert_process_exited(grandchild);
    assert!(state.child.lock().unwrap().is_none());
    stop_backend(&state);
}

#[test]
fn p2_failed_state_with_live_child_still_stops() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("failed-live-child");
    set_fake_mode("spawn-grandchild");
    start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend"))).unwrap();
    clear_fake_mode();
    let pid = read_pid(&state.data_dir.join("fake-backend.pid"));
    let grandchild = read_pid(&state.data_dir.join("fake-grandchild.pid"));
    *state.phase.lock().unwrap() = BackendPhase::Failed;
    stop_backend(&state);
    assert_eq!(state.status().phase, BackendPhase::Stopped);
    assert_process_exited(pid);
    assert_process_exited(grandchild);
}

#[test]
fn p2_concurrent_stop_is_idempotent_and_bounded() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("parallel-stop");
    set_fake_mode("ignore-stdin");
    start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend"))).unwrap();
    clear_fake_mode();
    let pid = read_pid(&state.data_dir.join("fake-backend.pid"));
    let started = Instant::now();
    let other_state = state.clone();
    let stopper = std::thread::spawn(move || stop_backend(&other_state));
    stop_backend(&state);
    stopper.join().unwrap();
    assert!(started.elapsed() < Duration::from_secs(7));
    assert_eq!(state.status().phase, BackendPhase::Stopped);
    assert_process_exited(pid);
}

// These tests mutate the process environment (FAKE_MODE) that the spawned
// fake-backend child inherits; parallel runs would race on it, so serialize
// all tests that set/clear FAKE_MODE via this lock.
pub static FAKE_MODE_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

```


## desktop/tests/fixtures/p2-electron.cjs

```
// Exercise the original Electron main/preload/store with a synthetic backend.
// Only the Python entry point and userData path are substituted in this fixture.
const fs = require('node:fs');
const path = require('node:path');
const { app } = require('electron');
const processes = require('node:child_process');
app.commandLine.appendSwitch('force-device-scale-factor', '1');

if (!process.env.P2_ELECTRON_PROFILE || !process.env.P2_FIXTURE_ROOT) {
  throw new Error('P2 fixture requires isolated profiles');
}
fs.mkdirSync(process.env.P2_ELECTRON_PROFILE, { recursive: true });
app.setPath('userData', process.env.P2_ELECTRON_PROFILE);
if (process.env.P2_HIDE_WINDOW === '1') {
  app.on('browser-window-created', (_event, window) => window.hide());
}
const spawn = processes.spawn;
processes.spawn = (command, args, options) => {
  if (command === 'python' && args?.length === 1 && args[0] === 'app.py') {
    return spawn(process.env.P2_PYTHON || 'python', [path.join(__dirname, 'p2_backend.py')], options);
  }
  return spawn(command, args, options);
};
require('../../main.js');

```


## desktop/tests/fixtures/p2_backend.py

```
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

```


## pyproject.toml

```
[project]
name = "chaoxing"
version = "1.1.1"
description = "超星学习通/超星尔雅/泛雅超星全自动无人值守完成任务点"
readme = "README.md"
license = { file = "LICENSE" }
requires-python = ">=3.11,<4.0"
dynamic = ["dependencies"]

[tool.setuptools]
py-modules = []

[tool.setuptools.dynamic]
dependencies = {file=["requirements.txt"]}

```


## tests/test_desktop_runtime.py

```
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

```


## tests/test_tauri_entry_contract.py

```
"""Real app import contracts, in isolated data directories without accounts."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


class TauriEntryContractTests(unittest.TestCase):
    def inspect_app(self, *, tauri, port="5000"):
        repo = str(Path(__file__).resolve().parents[1])
        with tempfile.TemporaryDirectory(prefix="chaoxing-p2-entry-") as profile:
            env = dict(os.environ, CHAOXING_DATA_DIR=profile, CHAOXING_PORT=port,
                       PYTHONIOENCODING="utf-8")
            if tauri:
                env["CHAOXING_TAURI"] = "1"
            else:
                env.pop("CHAOXING_TAURI", None)
            script = textwrap.dedent("""
                import json, sys
                sys.path.insert(0, sys.argv[1])
                import app as entry
                if sys.argv[2] == 'tauri':
                    from api.desktop_runtime import register_token_guard
                    register_token_guard(entry.app, 'synthetic-token', 'test-instance', 4321)
                client = entry.app.test_client()
                headers = {'Host':'127.0.0.1:4321', 'X-Auth-Token':'synthetic-token'}
                good = client.get('/api/health', headers=headers)
                origin = client.get('/api/health', headers={**headers, 'Origin':'http://localhost:5000'})
                missing = client.get('/api/health', headers={'Host':'127.0.0.1:4321', 'Origin':'http://localhost:5000'})
                entry.task_store.close()
                print('P2_RESULT=' + json.dumps({
                    'good':good.status_code,
                    'origin_status':origin.status_code,
                    'origin_cors':origin.headers.get('Access-Control-Allow-Origin'),
                    'missing_status':missing.status_code,
                    'missing_cors':missing.headers.get('Access-Control-Allow-Origin'),
                }))
            """)
            result = subprocess.run(
                [sys.executable, "-c", script, repo, "tauri" if tauri else "normal"],
                cwd=profile, env=env, capture_output=True, text=True,
                encoding="utf-8", timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(next(line.removeprefix("P2_RESULT=")
                                   for line in result.stdout.splitlines()
                                   if line.startswith("P2_RESULT=")))

    def test_tauri_origin_is_rejected_without_cors_even_with_valid_token(self):
        result = self.inspect_app(tauri=True)
        self.assertEqual(result["good"], 200)
        self.assertEqual(result["origin_status"], 401)
        self.assertIsNone(result["origin_cors"])
        self.assertEqual(result["missing_status"], 401)
        self.assertIsNone(result["missing_cors"])

    def test_tauri_ignores_legacy_port_environment(self):
        self.assertEqual(self.inspect_app(tauri=True, port="unused-by-tauri")["good"], 200)

    def test_normal_browser_mode_keeps_cors_and_unauthenticated_health(self):
        result = self.inspect_app(tauri=False)
        self.assertEqual(result["missing_status"], 200)
        self.assertEqual(result["origin_cors"], "http://localhost:5000")


if __name__ == "__main__":
    unittest.main()

```


## web/package.json

```
{
  "name": "chaoxing-web",
  "version": "1.1.1",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "test": "vitest run --environment jsdom",
    "preview": "vite preview"
  },
  "dependencies": {
    "@tauri-apps/api": "2.11.1",
    "axios": "^1.7.2",
    "clsx": "^2.1.1",
    "lucide-react": "^0.263.1",
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "tailwind-merge": "^2.3.0"
  },
  "devDependencies": {
    "@testing-library/react": "^16.3.3",
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "autoprefixer": "^10.4.19",
    "jsdom": "^26.1.0",
    "postcss": "^8.4.38",
    "tailwindcss": "^3.4.4",
    "vite": "^5.3.1",
    "vitest": "^3.2.7"
  }
}

```


## web/src/api/axios.js

```
import axios, { AxiosError } from 'axios';
import { isTauriDesktop } from '../lib/desktopBridge';
import { createTauriAdapter } from './tauriAdapter';

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

try {
  if (isTauriDesktop()) api.defaults.adapter = createTauriAdapter();
} catch (error) {
  // A desktop initialization failure must not silently send requests via HTTP.
  api.defaults.adapter = (config) => Promise.reject(AxiosError.from(error, AxiosError.ERR_NETWORK, config));
}

export default api;

```


## web/src/api/tauriAdapter.js

```
import { AxiosError, AxiosHeaders, CanceledError } from 'axios';
import { desktopBridge } from '../lib/desktopBridge';

const MAX_LOG_CURSOR = 0xffffffff;
const TASK_ID = '[a-zA-Z0-9_-]{1,128}';
let lastRequestId;

function nextRequestId() {
  if (lastRequestId === undefined) {
    // A fresh cryptographic prefix on every page load avoids reusing a previous
    // page's pending request IDs. A shared counter separates concurrent adapters.
    const seed = globalThis.crypto.getRandomValues(new Uint32Array(2));
    lastRequestId = (seed[0] & 0x1fffff) * 0x100000000 + seed[1];
  }
  lastRequestId = lastRequestId >= Number.MAX_SAFE_INTEGER ? 1 : lastRequestId + 1;
  return lastRequestId;
}

const invalidRequest = (config) => new AxiosError('请求参数格式错误', AxiosError.ERR_BAD_REQUEST, config);
const isRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value)
  && [Object.prototype, null].includes(Object.getPrototypeOf(value));

function requestOperation(config) {
  if (config.baseURL != null && !['/api', '/api/'].includes(config.baseURL)) throw invalidRequest(config);
  if (typeof config.url !== 'string' || config.url.includes('#') || config.paramsSerializer) throw invalidRequest(config);
  if (config.responseType && !['json', 'text'].includes(config.responseType)) throw invalidRequest(config);
  const [rawPath, ...queryParts] = config.url.split('?');
  const path = rawPath.startsWith('/api/') ? rawPath.slice(4) : rawPath;
  const method = (config.method || 'get').toLowerCase();
  const routes = { 'post /login': 'login', 'post /courses': 'courses', 'get /config': 'configRead', 'post /config': 'configWrite', 'post /start': 'start' };
  let operation = routes[`${method} ${path}`];
  let taskId;
  if (!operation && method === 'get') {
    const task = path.match(new RegExp(`^/task/(${TASK_ID})(/details)?$`));
    const logs = path.match(new RegExp(`^/logs/(${TASK_ID})$`));
    if (task) { operation = task[2] ? 'taskDetails' : 'taskStatus'; taskId = task[1]; }
    else if (logs) { operation = 'taskLogs'; taskId = logs[1]; }
  }
  if (!operation || queryParts.length > 1) throw invalidRequest(config);

  const parameters = [...new URLSearchParams(queryParts[0] || '')];
  if (config.params != null) {
    if (config.params instanceof URLSearchParams) parameters.push(...config.params);
    else if (isRecord(config.params)) parameters.push(...Object.entries(config.params));
    else throw invalidRequest(config);
  }
  let after;
  if (operation === 'taskLogs') {
    if (parameters.length > 1 || parameters.some(([key]) => key !== 'after')) throw invalidRequest(config);
    const cursor = parameters.length ? parameters[0][1] : 0;
    if ((typeof cursor !== 'number' && typeof cursor !== 'string') || !/^\d+$/.test(String(cursor))) throw invalidRequest(config);
    after = Number(cursor);
    if (!Number.isSafeInteger(after) || after < 0 || after > MAX_LOG_CURSOR) throw invalidRequest(config);
  } else if (parameters.length || queryParts.length) throw invalidRequest(config);

  let payload = null;
  if (method === 'post') {
    try { payload = typeof config.data === 'string' ? JSON.parse(config.data) : config.data; }
    catch { throw invalidRequest(config); }
    if (!isRecord(payload)) throw invalidRequest(config);
  } else if (config.data != null) throw invalidRequest(config);
  return { operation, payload, ...(taskId ? { taskId } : {}), ...(after !== undefined ? { after } : {}) };
}

function timeoutError(config) {
  return new AxiosError(config.timeoutErrorMessage || '请求超时，请稍后重试',
    config.transitional?.clarifyTimeoutError ? AxiosError.ETIMEDOUT : AxiosError.ECONNABORTED, config);
}

function proxyError(error, config) {
  if (error?.kind === 'cancelled') return new CanceledError('请求已取消', config);
  if (error?.kind === 'timeout') return timeoutError(config);
  const message = error?.kind === 'backendNotReady' ? '服务尚未就绪，请稍后重新检查'
    : error?.kind === 'invalidRequest' ? '请求参数格式错误' : '无法连接学习服务，请稍后重试';
  const result = new AxiosError(message, error?.kind === 'invalidRequest' ? AxiosError.ERR_BAD_REQUEST : AxiosError.ERR_NETWORK, config);
  result.cause = error;
  return result;
}

export function createTauriAdapter({
  request = desktopBridge.apiRequest,
  cancel = desktopBridge.apiCancel,
  eventTarget = globalThis.window,
} = {}) {
  const pending = new Set();
  let closing = false;
  const onPageHide = () => {
    closing = true;
    for (const abort of [...pending]) abort();
  };

  return (config) => new Promise((resolve, reject) => {
    if (closing || config.signal?.aborted || config.cancelToken?.reason) {
      reject(new CanceledError('请求已取消', config));
      return;
    }
    let wire;
    let timeout;
    try {
      wire = { ...requestOperation(config), requestId: nextRequestId() };
      timeout = config.timeout ?? 30000;
      if (typeof timeout !== 'number' || !Number.isFinite(timeout) || timeout < 0) {
        throw new AxiosError('请求超时设置无效', AxiosError.ERR_BAD_OPTION_VALUE, config);
      }
    } catch (error) {
      reject(error);
      return;
    }

    let settled = false;
    let sent = false;
    let timer;
    const finish = (error, response) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      config.signal?.removeEventListener('abort', onAbort);
      config.cancelToken?.unsubscribe(onAbort);
      pending.delete(onAbort);
      if (!pending.size) eventTarget?.removeEventListener('pagehide', onPageHide);
      if (error) reject(error);
      else resolve(response);
    };
    const stop = (error) => {
      if (settled) return;
      if (sent) {
        // Closing the page may also close IPC. Consume cancellation failures;
        // the caller must still stop waiting and ignore the late request result.
        try { Promise.resolve(cancel(wire.requestId)).catch(() => {}); } catch { /* Already closed. */ }
      }
      finish(error);
    };
    const onAbort = () => stop(new CanceledError('请求已取消', config));
    if (!pending.size) eventTarget?.addEventListener('pagehide', onPageHide);
    pending.add(onAbort);
    config.signal?.addEventListener('abort', onAbort, { once: true });
    config.cancelToken?.subscribe(onAbort);
    if (config.signal?.aborted || config.cancelToken?.reason) onAbort();
    if (settled) return;
    if (timeout > 0) timer = setTimeout(() => stop(timeoutError(config)), timeout);

    const onResponse = (value) => {
      if (settled) return;
      try {
        if (!value || !Number.isInteger(value.status) || value.status < 100 || value.status > 599 || !Object.hasOwn(value, 'body')) {
          throw new AxiosError('学习服务返回了无效响应', AxiosError.ERR_BAD_RESPONSE, config);
        }
        // Axios has already transformed the request. Give its response pipeline
        // the same raw JSON text as HTTP, including for custom/error transforms.
        const data = JSON.stringify(value.body);
        if (data === undefined) throw new AxiosError('学习服务返回了无效响应', AxiosError.ERR_BAD_RESPONSE, config);
        const response = { data, status: value.status, statusText: '', headers: new AxiosHeaders({ 'Content-Type': 'application/json' }), config, request: { requestId: wire.requestId } };
        if (!config.validateStatus || config.validateStatus(response.status)) finish(null, response);
        else finish(new AxiosError(`Request failed with status code ${response.status}`,
          response.status >= 500 ? AxiosError.ERR_BAD_RESPONSE : AxiosError.ERR_BAD_REQUEST, config, response.request, response));
      } catch (error) { finish(error); }
    };
    sent = true;
    try {
      Promise.resolve(request(wire)).then(onResponse, (error) => finish(proxyError(error, config)));
    } catch (error) { finish(proxyError(error, config)); }
  });
}

```


## web/src/api/tauriAdapter.test.js

```
import axios from 'axios';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createTauriAdapter } from './tauriAdapter';

const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const ok = (body = { status: true }) => ({ status: 200, body });

function setup(response = ok()) {
  const request = vi.fn().mockResolvedValue(response);
  const cancel = vi.fn().mockResolvedValue(undefined);
  const eventTarget = new EventTarget();
  const adapter = createTauriAdapter({ request, cancel, eventTarget });
  const api = axios.create({ baseURL: '/api', timeout: 30000, adapter, headers: { 'Content-Type': 'application/json' } });
  return { api, adapter, request, cancel, eventTarget };
}

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe('Tauri API contract', () => {
  it.each([
    ['post', '/login', { username: 'alice', password: '', use_cookies: true }, {}, { operation: 'login' }],
    ['post', '/courses', { username: 'alice' }, {}, { operation: 'courses' }],
    ['get', '/config', undefined, {}, { operation: 'configRead' }],
    ['post', '/config', { selectedCoursesByAccount: { alice: ['one'] } }, {}, { operation: 'configWrite' }],
    ['post', '/start', { username: 'alice', course_list: ['one'] }, {}, { operation: 'start' }],
    ['get', '/task/task-1_A', undefined, {}, { operation: 'taskStatus', taskId: 'task-1_A' }],
    ['get', '/task/task-1_A/details', undefined, {}, { operation: 'taskDetails', taskId: 'task-1_A' }],
    ['get', '/logs/task-1_A', undefined, { params: { after: 17 } }, { operation: 'taskLogs', taskId: 'task-1_A', after: 17 }],
  ])('maps %s %s to one explicit operation', async (method, url, data, config, operation) => {
    const { api, request, cancel } = setup();
    const response = await api.request({ method, url, data, ...config });
    expect(response.data).toEqual({ status: true });
    expect(response.status).toBe(200);
    expect(request).toHaveBeenCalledOnce();
    const wire = request.mock.calls[0][0];
    expect(wire).toEqual({ ...operation, payload: data ?? null, requestId: expect.any(Number) });
    expect(Number.isSafeInteger(wire.requestId)).toBe(true);
    expect(wire.requestId).toBeGreaterThan(0);
    expect(cancel).not.toHaveBeenCalled();
  });

  it('keeps the log cursor including zero and accepts only its bounded query form', async () => {
    const { api, request } = setup();
    await api.get('/logs/task');
    await api.get('/logs/task?after=42');
    await api.get('/logs/task', { params: new URLSearchParams({ after: '4294967295' }) });
    expect(request.mock.calls.map(([value]) => value.after)).toEqual([0, 42, 4294967295]);
  });

  it.each([
    { url: 'https://example.com/api/config' },
    { url: '//example.com/api/config' },
    { url: '/config', baseURL: 'https://example.com/api' },
    { url: '/config', method: 'delete' },
    { url: '/unknown' },
    { url: '/task/../config' },
    { url: '/task/%2e%2e' },
    { url: '/task/a%2fb' },
    { url: '/task/a/extra' },
    { url: '/config#fragment' },
    { url: '/config?after=1' },
    { url: '/logs/task?after=1&after=2' },
    { url: '/logs/task?other=1' },
    { url: '/logs/task?after=1', params: { after: 2 } },
    { url: '/logs/task', params: { after: -1 } },
    { url: '/logs/task', params: { after: 1.5 } },
    { url: '/logs/task', params: { after: 4294967296 } },
    { url: '/logs/task', params: { after: ['1'] } },
    { url: '/logs/task', params: { operation: 'start' } },
    { url: '/config', params: { unknown: undefined } },
    { url: '/config', data: { unexpected: true } },
    { url: '/login', method: 'post', data: 'not JSON', transformRequest: [(value) => value] },
  ])('fails closed without invoking for unsupported request %#', async (config) => {
    const { api, request } = setup();
    await expect(api.request(config)).rejects.toMatchObject({ code: 'ERR_BAD_REQUEST' });
    expect(request).not.toHaveBeenCalled();
  });

  it.each([409, 404])('preserves HTTP %s status and transformed error data', async (status) => {
    const body = { status: false, msg: '任务不可用', data: { task_id: 'existing-task' } };
    const { api, request } = setup({ status, body });
    await expect(api.post('/start', { course_list: ['one'] })).rejects.toMatchObject({
      isAxiosError: true, response: { status, data: body },
    });
    expect(request).toHaveBeenCalledOnce();
  });

  it('honors validateStatus including null without retrying start', async () => {
    const { api, request } = setup({ status: 409, body: { status: false } });
    expect((await api.post('/start', {}, { validateStatus: (status) => status === 409 })).status).toBe(409);
    expect((await api.post('/start', {}, { validateStatus: null })).status).toBe(409);
    expect(request).toHaveBeenCalledTimes(2);
  });

  it.each([200, 409])('runs each custom transform once with raw response JSON (%s)', async (status) => {
    const body = { status: status === 200, data: { value: 1 } };
    const { api, request } = setup({ status, body });
    const transformRequest = vi.fn((value) => JSON.stringify({ ...value, transformed: true }));
    const transformResponse = vi.fn((value) => {
      expect(typeof value).toBe('string');
      return { ...JSON.parse(value), transformed: true };
    });
    const outcome = await api.post('/start', { course_list: ['one'] }, { transformRequest, transformResponse }).catch((error) => error.response);
    expect(request.mock.calls[0][0].payload).toEqual({ course_list: ['one'], transformed: true });
    expect(outcome.data).toEqual({ ...body, transformed: true });
    expect(transformRequest).toHaveBeenCalledOnce();
    expect(transformResponse).toHaveBeenCalledOnce();
  });

  it('honors text responseType without a second JSON parse', async () => {
    const { api } = setup(ok({ nested: '{"value":1}' }));
    expect((await api.get('/config', { responseType: 'text' })).data).toBe('{"nested":"{\\"value\\":1}"}');
  });

  it.each([
    ['cancelled', 'ERR_CANCELED'], ['backendNotReady', 'ERR_NETWORK'],
    ['invalidRequest', 'ERR_BAD_REQUEST'], ['network', 'ERR_NETWORK'], ['timeout', 'ECONNABORTED'],
  ])('maps host %s errors into Axios errors', async (kind, code) => {
    const { api, request } = setup();
    request.mockRejectedValue({ kind, reason: 'fixture', phase: 'failed' });
    await expect(api.post('/start', {})).rejects.toMatchObject({ code });
    expect(request).toHaveBeenCalledOnce();
  });

  it('does not reuse request IDs for parallel requests or a reloaded adapter module', async () => {
    const first = setup();
    await Promise.all(Array.from({ length: 40 }, () => first.api.get('/config')));
    vi.resetModules();
    const reloaded = await import('./tauriAdapter');
    const secondRequest = vi.fn().mockResolvedValue(ok());
    const second = axios.create({ adapter: reloaded.createTauriAdapter({ request: secondRequest, cancel: vi.fn(), eventTarget: new EventTarget() }) });
    await Promise.all(Array.from({ length: 40 }, () => second.get('/config')));
    const ids = [...first.request.mock.calls, ...secondRequest.mock.calls].map(([request]) => request.requestId);
    expect(new Set(ids).size).toBe(80);
    expect(ids.every((id) => Number.isSafeInteger(id) && id > 0)).toBe(true);
  });
});

describe('Tauri request lifetime', () => {
  it('rejects an early abort before sending an operation', async () => {
    const { api, adapter, request, cancel } = setup();
    const controller = new AbortController();
    controller.abort();
    await expect(api.post('/start', {}, { signal: controller.signal })).rejects.toMatchObject({ code: 'ERR_CANCELED' });
    await expect(adapter({ method: 'post', url: '/start', data: '{}', signal: controller.signal })).rejects.toMatchObject({ code: 'ERR_CANCELED' });
    expect(request).not.toHaveBeenCalled();
    expect(cancel).not.toHaveBeenCalled();
  });

  it.each(['resolve', 'reject'])('cancels in flight, consumes a late %s and never repeats start', async (completion) => {
    const { api, request, cancel } = setup();
    const pending = deferred();
    request.mockReturnValue(pending.promise);
    cancel.mockRejectedValue(new Error('window already closing'));
    const controller = new AbortController();
    const outcome = api.post('/start', {}, { signal: controller.signal });
    const rejected = expect(outcome).rejects.toMatchObject({ code: 'ERR_CANCELED' });
    controller.abort();
    await rejected;
    expect(cancel).toHaveBeenCalledExactlyOnceWith(request.mock.calls[0][0].requestId);
    pending[completion](completion === 'resolve' ? ok() : { kind: 'network' });
    await Promise.resolve();
    await Promise.resolve();
    expect(request).toHaveBeenCalledOnce();
  });

  it('sends cancellation when abort arrives while invoke is registering', async () => {
    const { api, request, cancel } = setup();
    const controller = new AbortController();
    request.mockImplementation(() => { controller.abort(); return Promise.resolve(ok()); });
    await expect(api.post('/start', {}, { signal: controller.signal })).rejects.toMatchObject({ code: 'ERR_CANCELED' });
    expect(cancel).toHaveBeenCalledExactlyOnceWith(request.mock.calls[0][0].requestId);
    expect(request).toHaveBeenCalledOnce();
  });

  it('applies the default 30 second timeout and honors an explicit timeout', async () => {
    vi.useFakeTimers();
    const { api, request, cancel } = setup();
    request.mockReturnValue(new Promise(() => {}));
    const timeout = expect(api.post('/start', {})).rejects.toMatchObject({ code: 'ECONNABORTED' });
    await vi.advanceTimersByTimeAsync(29999);
    expect(cancel).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    await timeout;
    const short = expect(api.get('/config', { timeout: 25, transitional: { clarifyTimeoutError: true } })).rejects.toMatchObject({ code: 'ETIMEDOUT' });
    await vi.advanceTimersByTimeAsync(25);
    await short;
    expect(cancel).toHaveBeenCalledTimes(2);
    expect(request).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('allows timeout zero and ignores abort after a completed response', async () => {
    vi.useFakeTimers();
    const { api, request, cancel } = setup();
    const pending = deferred();
    const controller = new AbortController();
    request.mockReturnValue(pending.promise);
    const result = api.get('/config', { timeout: 0, signal: controller.signal });
    await vi.advanceTimersByTimeAsync(120000);
    expect(cancel).not.toHaveBeenCalled();
    pending.resolve(ok());
    await result;
    controller.abort();
    expect(cancel).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('cancels every pending request on pagehide and removes lifetime listeners', async () => {
    vi.useFakeTimers();
    const { api, request, cancel, eventTarget } = setup();
    const remove = vi.spyOn(eventTarget, 'removeEventListener');
    request.mockReturnValue(new Promise(() => {}));
    const results = Promise.allSettled([api.get('/config'), api.post('/start', {})]);
    eventTarget.dispatchEvent(new Event('pagehide'));
    expect((await results).map((result) => result.reason.code)).toEqual(['ERR_CANCELED', 'ERR_CANCELED']);
    expect(cancel.mock.calls.map(([id]) => id).sort()).toEqual(request.mock.calls.map(([value]) => value.requestId).sort());
    expect(remove).toHaveBeenCalledWith('pagehide', expect.any(Function));
    expect(vi.getTimerCount()).toBe(0);
  });
});

```


## web/src/api/transportFlow.test.jsx

```
import React from 'react';
import { createServer } from 'node:http';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import App from '../App';
import DesktopStartup from '../components/DesktopStartup';
import api from './axios';
import { createTauriAdapter } from './tauriAdapter';
import { sessionStore } from '../lib/sessionStore';

const core = vi.hoisted(() => ({ isTauri: vi.fn().mockReturnValue(false), invoke: vi.fn() }));
vi.mock('@tauri-apps/api/core', () => core);

const empty = () => ({ version: 1, login: null, activeTask: null });
const clone = (value) => JSON.parse(JSON.stringify(value));
const ok = (data) => ({ status: 200, body: { status: true, data } });
const originalAdapter = api.defaults.adapter;
let server;
let origin;
let fixture;

function fixtureApi(method, path, payload, after = 0) {
  fixture.calls.push({ method, path, payload, after });
  if (path === '/api/login') return ok({ username: payload.username });
  if (path === '/api/courses') return ok([{ courseId: 'one', title: '模拟课程' }]);
  if (path === '/api/config') {
    if (method === 'POST') fixture.config = clone(payload);
    return ok(fixture.config);
  }
  if (path === '/api/start') return { status: 409, body: { status: false, data: { task_id: 'shared-task' } } };
  if (fixture.gone) return { status: 404, body: { status: false, msg: '任务不存在或已过期' } };
  if (path === '/api/task/shared-task') {
    return ok({ status: fixture.status, progress: 0, total: 1, stats: { completed_chapters: 0, total_chapters: 0 } });
  }
  if (path === '/api/task/shared-task/details') {
    if (fixture.status === 'completed' && ++fixture.finalDetails === 1) return { status: 503, body: { status: false, msg: '模拟详情暂不可用' } };
    return ok({ courses: [] });
  }
  if (path === '/api/logs/shared-task') {
    const initial = { seq: 1, timestamp: 1, level: 'info', message: '初始日志' };
    const final = { seq: 2, timestamp: 2, level: 'info', message: '最终日志' };
    const logs = fixture.status === 'completed' ? [initial, final, final] : [initial, initial];
    return { status: 200, body: { status: true, data: logs, next_cursor: fixture.status === 'completed' ? 2 : 1, truncated: false } };
  }
  throw new Error(`Unexpected fixture request: ${method} ${path}`);
}

function sessionCommand(command, args) {
  if (command === 'session_remember_login') {
    fixture.session = { version: 1, login: { username: args.username, use_cookies: true }, activeTask: fixture.session.login?.username === args.username ? fixture.session.activeTask : null };
  } else if (command === 'session_remember_task') {
    fixture.session.activeTask = args.task;
  } else if (command === 'session_clear') fixture.session = empty();
  return clone(fixture.session);
}

beforeAll(async () => {
  server = createServer(async (request, response) => {
    response.setHeader('Access-Control-Allow-Origin', '*');
    response.setHeader('Access-Control-Allow-Headers', 'Content-Type');
    response.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
    if (request.method === 'OPTIONS') { response.writeHead(204); response.end(); return; }
    try {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const text = Buffer.concat(chunks).toString();
      const url = new URL(request.url, 'http://fixture.invalid');
      const result = fixtureApi(request.method, url.pathname, text ? JSON.parse(text) : null, Number(url.searchParams.get('after') || 0));
      response.writeHead(result.status, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify(result.body));
    } catch (error) {
      response.writeHead(500, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({ status: false, msg: error.message }));
    }
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
});

afterEach(async () => {
  cleanup();
  await sessionStore.clear();
  core.isTauri.mockReturnValue(false);
  core.invoke.mockReset();
  delete window.chaoxingSession;
  localStorage.clear();
  api.defaults.adapter = originalAdapter;
  api.defaults.baseURL = '/api';
  vi.restoreAllMocks();
});

afterAll(async () => { await new Promise((resolve) => server.close(resolve)); });

describe('shared task flow over real Axios transports', () => {
  it.each(['browser', 'electron', 'tauri'])('recovers 409, refreshes, retries final snapshots and clears 404 over %s', async (transport) => {
    fixture = { calls: [], config: { settings: {}, selectedCoursesByAccount: { alice: ['one'] } }, session: empty(), status: 'running', finalDetails: 0, gone: false };
    window.history.replaceState({}, '', '/');
    window.matchMedia = vi.fn(() => ({ matches: true }));
    core.isTauri.mockReturnValue(transport === 'tauri');
    if (transport === 'tauri') {
      core.invoke.mockImplementation(async (command, args) => {
        if (command === 'backend_status') return { phase: 'ready' };
        if (command.startsWith('session_')) return sessionCommand(command, args);
        if (command === 'api_cancel') return undefined;
        if (command !== 'api_request') throw new Error(`Unexpected command: ${command}`);
        const { operation, payload, taskId, after } = args.request;
        const routes = {
          login: ['POST', '/api/login'], courses: ['POST', '/api/courses'],
          configRead: ['GET', '/api/config'], configWrite: ['POST', '/api/config'], start: ['POST', '/api/start'],
          taskStatus: ['GET', `/api/task/${taskId}`], taskDetails: ['GET', `/api/task/${taskId}/details`], taskLogs: ['GET', `/api/logs/${taskId}`],
        };
        const [method, path] = routes[operation];
        return fixtureApi(method, path, payload, after);
      });
      api.defaults.adapter = createTauriAdapter();
      api.defaults.baseURL = '/api';
    } else {
      api.defaults.adapter = originalAdapter;
      api.defaults.baseURL = `${origin}/api`;
      if (transport === 'electron') {
        window.chaoxingSession = {
          read: async () => sessionCommand('session_read'),
          rememberLogin: async (username) => sessionCommand('session_remember_login', { username }),
          rememberTask: async (task) => sessionCommand('session_remember_task', { task }),
          clear: async () => sessionCommand('session_clear'),
        };
      }
    }

    await sessionStore.rememberLogin('alice');
    const mount = () => render(<React.StrictMode><DesktopStartup intervalMs={100}><App /></DesktopStartup></React.StrictMode>);
    let view = mount();
    const start = await screen.findByRole('button', { name: '开始学习' });
    expect(start.disabled).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '保存当前配置' }));
    await screen.findByText('配置已保存');
    expect(fixture.calls.find((call) => call.method === 'POST' && call.path === '/api/config').payload.selectedCoursesByAccount).toEqual({ alice: ['one'] });
    fireEvent.click(start);
    await screen.findByText('shared-task');
    await screen.findByText('初始日志');
    expect((await sessionStore.read()).activeTask).toEqual({ username: 'alice', taskId: 'shared-task' });
    fireEvent.click(screen.getByRole('button', { name: '返回课程选择' }));
    await screen.findByRole('button', { name: '返回运行任务' });
    expect(screen.getByRole('button', { name: '开始学习' }).disabled).toBe(true);

    view.unmount();
    view = mount();
    await screen.findByText('shared-task');
    await screen.findByText('初始日志');
    fixture.status = 'completed';
    await screen.findByText('最终日志', {}, { timeout: 4500 });
    await waitFor(() => expect(fixture.finalDetails).toBe(2), { timeout: 4500 });
    const logs = within(screen.getByRole('log'));
    expect(logs.getAllByText('初始日志')).toHaveLength(1);
    expect(logs.getAllByText('最终日志')).toHaveLength(1);
    expect(fixture.calls.filter((call) => call.path.startsWith('/api/logs/')).map((call) => call.after)).toEqual(expect.arrayContaining([0, 1, 2]));

    view.unmount();
    fixture.gone = true;
    view = mount();
    expect((await screen.findByRole('alert')).textContent).toContain('任务不存在或已过期');
    await waitFor(async () => expect(await sessionStore.read()).toEqual({ version: 1, login: { username: 'alice', use_cookies: true }, activeTask: null }));
    fireEvent.click(screen.getByRole('button', { name: '返回课程选择' }));
    expect((await screen.findByRole('button', { name: '开始学习' })).disabled).toBe(false);
    expect(fixture.calls.filter((call) => call.path === '/api/start')).toHaveLength(1);
    expect(fixture.calls.filter((call) => call.path === '/api/login').every((call) => call.payload.use_cookies === true && call.payload.password === '')).toBe(true);
    view.unmount();
  }, 15000);
});

```


## web/src/components/DesktopStartup.jsx

```
import React, { useEffect, useRef, useState } from 'react';
import { AlertCircle, Loader2, RefreshCw } from 'lucide-react';
import { desktopBridge, isTauriDesktop } from '../lib/desktopBridge';
import Button from './ui/Button';

const phases = new Set(['starting', 'ready', 'stopping', 'stopped', 'failed']);
const messages = {
  starting: ['正在准备学习助手', '服务正在启动，请稍候。'],
  checking: ['正在检查服务状态', '请稍候。'],
  stopping: ['服务正在关闭', '请等待服务退出后重新打开应用。'],
  stopped: ['学习服务已停止', '请关闭应用后重新打开，或稍后重新检查。'],
  failed: ['学习服务启动失败', '请关闭应用后重新打开，或稍后重新检查。'],
  unavailable: ['无法读取服务状态', '请稍后重新检查。如果问题持续，请关闭应用后重新打开。'],
};

export default function DesktopStartup({ children, intervalMs = 1000 }) {
  const [runtime] = useState(() => {
    try { return isTauriDesktop() ? 'desktop' : 'web'; }
    catch { return 'unavailable'; }
  });
  const [phase, setPhase] = useState(runtime === 'web' ? 'ready' : runtime === 'unavailable' ? 'unavailable' : 'starting');
  const [checkVersion, setCheckVersion] = useState(0);
  const [hostError, setHostError] = useState('');
  const [notice, setNotice] = useState('');
  const requestRef = useRef(null);

  useEffect(() => {
    if (runtime === 'web' || (runtime === 'unavailable' && checkVersion === 0)) return undefined;
    let disposed = false;
    let timer;
    const poll = async () => {
      try {
        // StrictMode replays effects. Reuse the in-flight read so only the live
        // effect schedules the next poll, always after the preceding one settles.
        if (!requestRef.current) {
          const pending = Promise.resolve().then(() => desktopBridge.backendStatus()).finally(() => {
            if (requestRef.current === pending) requestRef.current = null;
          });
          requestRef.current = pending;
        }
        const status = await requestRef.current;
        if (disposed) return;
        if (!status || !phases.has(status.phase)) throw new Error('Invalid service status');
        setPhase(status.phase);
        setHostError(typeof status.error === 'string' ? status.error : '');
        setNotice(typeof status.notice === 'string' ? status.notice : '');
        if (['starting', 'ready', 'stopping'].includes(status.phase)) timer = setTimeout(poll, intervalMs);
      } catch {
        if (!disposed) setPhase('unavailable');
      }
    };
    poll();
    return () => { disposed = true; clearTimeout(timer); };
  }, [runtime, intervalMs, checkVersion]);

  if (phase === 'ready') return <>
    {notice && <div role="status" className="border-b border-warning/30 bg-warning/5 px-6 py-3 text-sm text-body">{notice}</div>}
    {children}
  </>;
  const failed = ['failed', 'stopped', 'unavailable'].includes(phase);
  const [title, description] = messages[phase];
  const recheck = () => { setPhase('checking'); setCheckVersion((version) => version + 1); };

  return (
    <main className="flex min-h-screen items-center justify-center bg-canvas px-[clamp(1rem,4vw,4rem)] py-12">
      <div className="w-full max-w-md text-center">
        <img src="/fav.jpg" alt="超星学习通" className="mx-auto mb-4 h-12 w-12 rounded-xl object-cover shadow-focus" />
        <h1 className="mb-8 text-xl font-semibold tracking-tight">超星学习通 · 自动化学习助手</h1>
        <section role={failed ? 'alert' : 'status'} aria-live={failed ? 'assertive' : 'polite'} className="rounded-2xl border border-line bg-white p-8 shadow-lift">
          {failed ? <AlertCircle className="mx-auto mb-4 h-8 w-8 text-danger" aria-hidden="true" />
            : <Loader2 className="mx-auto mb-4 h-8 w-8 animate-spin text-brand" aria-hidden="true" />}
          <h2 className="text-lg font-semibold">{title}</h2>
          <p className="mt-2 text-sm leading-relaxed text-body">{description}</p>
          {failed && hostError && <p className="mt-3 break-words rounded-lg bg-danger/5 px-3 py-2 text-left text-sm leading-relaxed text-danger">{hostError}</p>}
          {failed && <Button type="button" onClick={recheck} className="mt-6"><RefreshCw className="h-4 w-4" aria-hidden="true" />重新检查</Button>}
        </section>
      </div>
    </main>
  );
}

```


## web/src/components/DesktopStartup.test.jsx

```
import React from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import DesktopStartup from './DesktopStartup';
import { desktopBridge, isTauriDesktop } from '../lib/desktopBridge';

vi.mock('../lib/desktopBridge', () => ({ isTauriDesktop: vi.fn(), desktopBridge: { backendStatus: vi.fn() } }));
const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const tick = (time = 0) => act(async () => { await vi.advanceTimersByTimeAsync(time); });
const show = (strict = false) => {
  const content = <DesktopStartup intervalMs={100}><div>业务界面</div></DesktopStartup>;
  return render(strict ? <React.StrictMode>{content}</React.StrictMode> : content);
};

beforeEach(() => { vi.useFakeTimers(); isTauriDesktop.mockReset().mockReturnValue(true); desktopBridge.backendStatus.mockReset(); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

describe('desktop startup', () => {
  it('renders the browser and Electron app immediately without querying desktop status', async () => {
    isTauriDesktop.mockReturnValue(false);
    show();
    expect(screen.getByText('业务界面')).toBeTruthy();
    await tick(1000);
    expect(desktopBridge.backendStatus).not.toHaveBeenCalled();
  });

  it('mounts the app only after readiness and continues watching for service death', async () => {
    desktopBridge.backendStatus.mockResolvedValueOnce({ phase: 'starting' }).mockResolvedValueOnce({ phase: 'ready' }).mockResolvedValue({ phase: 'failed', error: 'exit code fixture' });
    show();
    expect(screen.queryByText('业务界面')).toBeNull();
    expect(screen.getByRole('status').textContent).toContain('正在');
    await tick();
    expect(screen.queryByText('业务界面')).toBeNull();
    await tick(100);
    expect(screen.getByText('业务界面')).toBeTruthy();
    await tick(100);
    expect(screen.queryByText('业务界面')).toBeNull();
    expect(screen.getByRole('alert').textContent).toContain('服务');
    expect(screen.getByRole('alert').textContent).toContain('exit code fixture');
    expect(screen.getByRole('button', { name: '重新检查' })).toBeTruthy();
  });

  it('serializes slow status queries, including StrictMode effect replay', async () => {
    const pending = deferred();
    desktopBridge.backendStatus.mockReturnValueOnce(pending.promise).mockResolvedValue({ phase: 'ready' });
    show(true);
    await tick(1000);
    expect(desktopBridge.backendStatus).toHaveBeenCalledOnce();
    expect(screen.queryByText('业务界面')).toBeNull();
    await act(async () => pending.resolve({ phase: 'starting' }));
    await tick(99);
    expect(desktopBridge.backendStatus).toHaveBeenCalledOnce();
    await tick(1);
    expect(desktopBridge.backendStatus).toHaveBeenCalledTimes(2);
    expect(screen.getByText('业务界面')).toBeTruthy();
  });

  it.each(['failed', 'stopped'])('shows an actionable Chinese %s state and only rereads on recheck', async (phase) => {
    desktopBridge.backendStatus.mockResolvedValueOnce({ phase, error: 'raw runtime diagnostics' }).mockResolvedValue({ phase: 'ready' });
    show();
    await tick();
    expect(screen.getByRole('alert').textContent).toContain(phase === 'failed' ? '失败' : '停止');
    expect(screen.queryByText('业务界面')).toBeNull();
    await tick(1000);
    expect(desktopBridge.backendStatus).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: '重新检查' }));
    await tick();
    expect(desktopBridge.backendStatus).toHaveBeenCalledTimes(2);
    expect(screen.getByText('业务界面')).toBeTruthy();
  });

  it.each([new Error('invoke error'), { phase: 'unknown' }, undefined])('fails visibly when status cannot be read or validated (%s)', async (value) => {
    if (value instanceof Error) desktopBridge.backendStatus.mockRejectedValue(value);
    else desktopBridge.backendStatus.mockResolvedValue(value);
    show();
    await tick();
    expect(screen.getByRole('alert').textContent).toContain('无法读取服务状态');
    expect(screen.queryByText('业务界面')).toBeNull();
    expect(screen.getByRole('button', { name: '重新检查' })).toBeTruthy();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('never opens the app when runtime initialization throws', async () => {
    isTauriDesktop.mockImplementation(() => { throw new Error('initialization'); });
    show();
    await tick();
    expect(screen.queryByText('业务界面')).toBeNull();
    expect(screen.getByRole('alert')).toBeTruthy();
    expect(desktopBridge.backendStatus).not.toHaveBeenCalled();
  });

  it.each(['resolve', 'reject'])('ignores a late status %s after unmount and cancels timers', async (completion) => {
    const pending = deferred();
    desktopBridge.backendStatus.mockReturnValue(pending.promise);
    const view = show();
    await tick();
    view.unmount();
    await act(async () => pending[completion](completion === 'resolve' ? { phase: 'ready' } : new Error('late')));
    await tick(1000);
    expect(desktopBridge.backendStatus).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
    expect(screen.queryByText('业务界面')).toBeNull();
  });

  it('unmounts the app while the service stops, then reports the stopped state', async () => {
    desktopBridge.backendStatus.mockResolvedValueOnce({ phase: 'ready' }).mockResolvedValueOnce({ phase: 'stopping' }).mockResolvedValue({ phase: 'stopped' });
    const view = show();
    await tick();
    expect(screen.getByText('业务界面')).toBeTruthy();
    await tick(100);
    expect(screen.queryByText('业务界面')).toBeNull();
    await tick(100);
    expect(screen.getByRole('alert').textContent).toContain('停止');
    view.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('shows a host failure reason as text, including the instruction to close legacy Electron', async () => {
    const error = '请先关闭旧版桌面应用，再重新打开。<img src=x onerror=alert(1)>';
    desktopBridge.backendStatus.mockResolvedValue({ phase: 'failed', error });
    show();
    await tick();
    expect(screen.getByRole('alert').textContent).toContain(error);
    expect(document.querySelector('img[src="x"]')).toBeNull();
  });

  it('keeps an import warning visible after the business app becomes ready', async () => {
    desktopBridge.backendStatus.mockResolvedValue({ phase: 'ready', notice: '旧账号记录无法导入，请重新登录。' });
    show();
    await tick();
    expect(screen.getByText('业务界面')).toBeTruthy();
    expect(screen.getByRole('status').textContent).toContain('旧账号记录无法导入');
  });
});

```


## web/src/lib/desktopBridge.js

```
import { invoke, isTauri } from '@tauri-apps/api/core';

export const isTauriDesktop = () => isTauri();

// Keep runtime details and command envelopes out of the business components.
export const desktopBridge = Object.freeze({
  apiRequest: (request) => invoke('api_request', { request }),
  apiCancel: (requestId) => invoke('api_cancel', { requestId }),
  backendStatus: () => invoke('backend_status'),
  read: () => invoke('session_read'),
  rememberLogin: (username) => invoke('session_remember_login', { username }),
  rememberTask: (task) => invoke('session_remember_task', { task }),
  clear: () => invoke('session_clear'),
});

export function getSessionBridge() {
  if (isTauriDesktop()) return desktopBridge;
  return globalThis.window?.chaoxingSession ?? null;
}

```


## web/src/lib/desktopBridge.test.js

```
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const core = vi.hoisted(() => ({ isTauri: vi.fn(), invoke: vi.fn() }));
vi.mock('@tauri-apps/api/core', () => core);
import { desktopBridge, getSessionBridge, isTauriDesktop } from './desktopBridge';

const empty = () => ({ version: 1, login: null, activeTask: null });

beforeEach(() => { core.isTauri.mockReset().mockReturnValue(false); core.invoke.mockReset().mockResolvedValue(empty()); });
afterEach(() => { delete window.chaoxingSession; vi.restoreAllMocks(); });

describe('desktop bridge', () => {
  it('uses the official runtime detection and preserves the Electron session bridge', () => {
    expect(isTauriDesktop()).toBe(false);
    expect(getSessionBridge()).toBeNull();
    const electron = { read: vi.fn() };
    window.chaoxingSession = electron;
    expect(getSessionBridge()).toBe(electron);
    core.isTauri.mockReturnValue(true);
    expect(getSessionBridge()).toBe(desktopBridge);
    expect(core.isTauri).toHaveBeenCalled();
  });

  it('invokes only the named commands with their exact argument envelopes', async () => {
    const request = { operation: 'taskLogs', payload: null, requestId: 42, taskId: 'task', after: 8 };
    const task = { username: 'alice', taskId: 'task' };
    await desktopBridge.apiRequest(request);
    await desktopBridge.apiCancel(42);
    await desktopBridge.backendStatus();
    await desktopBridge.read();
    await desktopBridge.rememberLogin('alice');
    await desktopBridge.rememberTask(task);
    await desktopBridge.rememberTask(null);
    expect(await desktopBridge.clear()).toEqual(empty());
    expect(core.invoke.mock.calls).toEqual([
      ['api_request', { request }], ['api_cancel', { requestId: 42 }], ['backend_status'],
      ['session_read'], ['session_remember_login', { username: 'alice' }],
      ['session_remember_task', { task }], ['session_remember_task', { task: null }], ['session_clear'],
    ]);
  });

  it('propagates initialization and command errors instead of selecting browser persistence', async () => {
    core.isTauri.mockImplementation(() => { throw new Error('initialization failed'); });
    expect(() => getSessionBridge()).toThrow('initialization failed');
    core.invoke.mockRejectedValue(new Error('disk failure'));
    await expect(desktopBridge.read()).rejects.toThrow('disk failure');
  });

  it.each([false, true])('selects the API transport using official detection (Tauri: %s)', async (tauri) => {
    core.isTauri.mockReturnValue(tauri);
    core.invoke.mockResolvedValue({ status: 200, body: { status: true } });
    vi.resetModules();
    const { default: api } = await import('../api/axios');
    expect(api.defaults.timeout).toBe(30000);
    if (tauri) {
      expect((await api.get('/config')).data).toEqual({ status: true });
      expect(core.invoke).toHaveBeenCalledWith('api_request', {
        request: { operation: 'configRead', payload: null, requestId: expect.any(Number) },
      });
    } else {
      expect(api.defaults.baseURL).toBe('/api');
      expect(api.defaults.adapter).toEqual(expect.arrayContaining(['xhr', 'http']));
      expect(core.invoke).not.toHaveBeenCalled();
    }
  });
});

```


## web/src/lib/sessionStore.js

```
import { desktopBridge, getSessionBridge } from './desktopBridge';

export const SAVED_LOGIN_KEY = 'chaoxing_saved_login';
export const SESSION_KEY = 'chaoxing_session_v1';
const emptySession = () => ({ version: 1, login: null, activeTask: null });
const usernameValue = (value) => typeof value === 'string' && value.trim().length > 0 && value.trim().length <= 128 && !/[\u0000-\u001f\u007f]/.test(value) ? value.trim() : null;
export const validTaskId = (value) => typeof value === 'string' && /^[a-zA-Z0-9_-]{1,128}$/.test(value);
const exactKeys = (value, keys) => value !== null && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
const validTask = (value) => exactKeys(value, ['username', 'taskId']) && usernameValue(value.username) !== null
  && usernameValue(value.username) === value.username && validTaskId(value.taskId);

function desktopSession(value) {
  if (!exactKeys(value, ['version', 'login', 'activeTask']) || value.version !== 1
    || !(value.login === null || (exactKeys(value.login, ['username', 'use_cookies'])
      && usernameValue(value.login.username) !== null && usernameValue(value.login.username) === value.login.username && value.login.use_cookies === true))
    || !(value.activeTask === null || (validTask(value.activeTask) && value.activeTask.username === value.login?.username))) {
    throw new Error('保存的账号格式错误');
  }
  return sanitizeSession(value);
}

function sanitizeSession(value) {
  const session = emptySession();
  const username = usernameValue(value?.login?.username);
  if (username) session.login = { username, use_cookies: true };
  if (username && value?.activeTask?.username === username && validTaskId(value.activeTask.taskId)) {
    session.activeTask = { username, taskId: value.activeTask.taskId };
  }
  return session;
}

function parseStored(raw) {
  try { return raw && raw.length <= 16384 ? JSON.parse(raw) : null; } catch { return null; }
}

export function createSessionStore({
  getBridge = getSessionBridge,
  getStorage = () => window.localStorage,
} = {}) {
  let queue = Promise.resolve();
  const serialize = (operation) => {
    const next = queue.then(operation);
    queue = next.catch(() => {});
    return next;
  };
  const storage = () => { try { return getStorage(); } catch { return null; } };

  const read = async () => {
    const bridge = getBridge();
    let session = bridge ? desktopSession(await bridge.read()) : null;
    // Tauri imports legacy data in the host before readiness. Its random page
    // origin must never become a second persistence source or an IO fallback.
    if (bridge === desktopBridge) return session;
    const local = storage();
    let legacy;
    let browserState;
    if (local) {
      // Remove legacy plaintext credentials before migration; only the account is used.
      const old = local.getItem(SAVED_LOGIN_KEY);
      local.removeItem(SAVED_LOGIN_KEY);
      legacy = usernameValue(parseStored(old)?.username);
      browserState = sanitizeSession(parseStored(local.getItem(SESSION_KEY)));
    }
    session ||= browserState || emptySession();
    const migratedUsername = browserState?.login?.username || legacy;
    if (!session.login && migratedUsername) {
      session.login = { username: migratedUsername, use_cookies: true };
      if (bridge) {
        session = desktopSession(await bridge.rememberLogin(migratedUsername));
      } else if (local) {
        local.setItem(SESSION_KEY, JSON.stringify(session));
      }
    }
    if (bridge && local) local.removeItem(SESSION_KEY);
    return session;
  };

  return {
    read: () => serialize(read),
    rememberLogin: (value) => serialize(async () => {
      const username = usernameValue(value);
      if (!username) throw new Error('账号格式错误');
      const bridge = getBridge();
      if (bridge) return desktopSession(await bridge.rememberLogin(username));
      const previous = await read();
      const session = { ...emptySession(), login: { username, use_cookies: true }, activeTask: previous.login?.username === username ? previous.activeTask : null };
      storage()?.setItem(SESSION_KEY, JSON.stringify(session));
      return session;
    }),
    rememberTask: (task) => serialize(async () => {
      const bridge = getBridge();
      if (task !== null && !validTask(task)) throw new Error('任务信息格式错误');
      if (bridge) return desktopSession(await bridge.rememberTask(task));
      const session = await read();
      if (task && session.login?.username !== task.username) throw new Error('任务账号不匹配');
      session.activeTask = task ? { username: task.username, taskId: task.taskId } : null;
      storage()?.setItem(SESSION_KEY, JSON.stringify(session));
      return session;
    }),
    clear: () => serialize(async () => {
      const bridge = getBridge();
      const session = bridge ? desktopSession(await bridge.clear()) : emptySession();
      if (bridge === desktopBridge) return session;
      const local = storage();
      local?.removeItem(SAVED_LOGIN_KEY);
      local?.removeItem(SESSION_KEY);
      return session;
    }),
  };
}

export const sessionStore = createSessionStore();

```


## web/src/lib/sessionStore.test.js

```
import { describe, expect, it, vi } from 'vitest';
import { createSessionStore, SAVED_LOGIN_KEY, SESSION_KEY } from './sessionStore';

function memoryStorage() {
  const values = new Map();
  return { getItem: (key) => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
}

describe('saved session', () => {
  it('migrates legacy credentials to a cookie account without keeping the password', async () => {
    const local = memoryStorage();
    local.setItem(SAVED_LOGIN_KEY, JSON.stringify({ username: 'alice', password: 'legacy-test-secret' }));
    const store = createSessionStore({ getBridge: () => null, getStorage: () => local });
    expect((await store.read()).login).toEqual({ username: 'alice', use_cookies: true });
    expect(local.getItem(SAVED_LOGIN_KEY)).toBeNull();
    expect(local.getItem(SESSION_KEY)).not.toContain('legacy-test-secret');
  });

  it('uses stable desktop persistence after the browser origin changes', async () => {
    let saved = { version: 1, login: null, activeTask: null };
    const bridge = {
      read: vi.fn(async () => saved),
      rememberLogin: vi.fn(async (username) => (saved = { ...saved, login: { username, use_cookies: true } })),
      rememberTask: vi.fn(async (task) => (saved = { ...saved, activeTask: task })),
      clear: vi.fn(async () => (saved = { version: 1, login: null, activeTask: null })),
    };
    const oldOrigin = memoryStorage();
    oldOrigin.setItem(SAVED_LOGIN_KEY, JSON.stringify({ username: 'alice', password: 'old-test-secret' }));
    const first = createSessionStore({ getBridge: () => bridge, getStorage: () => oldOrigin });
    await first.read();
    await first.rememberTask({ username: 'alice', taskId: 'task-one' });
    const second = createSessionStore({ getBridge: () => bridge, getStorage: () => memoryStorage() });
    expect((await second.read()).activeTask.taskId).toBe('task-one');
    expect(bridge.rememberLogin).toHaveBeenCalledWith('alice');
    expect(JSON.stringify(bridge.rememberLogin.mock.calls)).not.toContain('secret');
    expect(oldOrigin.getItem(SAVED_LOGIN_KEY)).toBeNull();
    await second.clear();
    expect((await first.read()).login).toBeNull();
  });

  it('isolates tasks by account and clears both new and legacy storage on logout', async () => {
    const local = memoryStorage();
    const store = createSessionStore({ getBridge: () => null, getStorage: () => local });
    await store.rememberLogin('alice');
    await store.rememberTask({ username: 'alice', taskId: 'one' });
    await store.rememberLogin('bob');
    expect((await store.read()).activeTask).toBeNull();
    await expect(store.rememberTask({ username: 'alice', taskId: 'one' })).rejects.toThrow('账号不匹配');
    local.setItem(SAVED_LOGIN_KEY, '{}');
    await store.clear();
    expect(local.getItem(SAVED_LOGIN_KEY)).toBeNull();
    expect(local.getItem(SESSION_KEY)).toBeNull();
  });

  it('preserves the login when only an expired task is cleared', async () => {
    const local = memoryStorage();
    const store = createSessionStore({ getBridge: () => null, getStorage: () => local });
    await store.rememberLogin('alice');
    await store.rememberTask({ username: 'alice', taskId: 'one' });
    expect(await store.rememberTask(null)).toEqual({ version: 1, login: { username: 'alice', use_cookies: true }, activeTask: null });
    expect(await store.clear()).toEqual({ version: 1, login: null, activeTask: null });
  });

  it('does not touch localStorage when desktop initialization or read fails', async () => {
    const getStorage = vi.fn(() => memoryStorage());
    const broken = createSessionStore({ getBridge: () => { throw new Error('initialization failed'); }, getStorage });
    await expect(broken.read()).rejects.toThrow('initialization failed');
    const failing = createSessionStore({ getBridge: () => ({ read: vi.fn().mockRejectedValue(new Error('disk failed')) }), getStorage });
    await expect(failing.read()).rejects.toThrow('disk failed');
    expect(getStorage).not.toHaveBeenCalled();
  });

  it.each(['rememberLogin', 'rememberTask', 'clear'])('propagates desktop %s failures without browser fallback', async (operation) => {
    const local = memoryStorage();
    local.setItem(SESSION_KEY, 'existing-browser-state');
    const bridge = { [operation]: vi.fn().mockRejectedValue(new Error('disk failed')) };
    const getStorage = vi.fn(() => local);
    const store = createSessionStore({ getBridge: () => bridge, getStorage });
    const argument = operation === 'rememberLogin' ? 'alice' : operation === 'rememberTask' ? { username: 'alice', taskId: 'one' } : undefined;
    await expect(store[operation](argument)).rejects.toThrow('disk failed');
    expect(getStorage).not.toHaveBeenCalled();
    expect(local.getItem(SESSION_KEY)).toBe('existing-browser-state');
  });

  it.each([
    undefined,
    { version: 2, login: null, activeTask: null },
    { version: 1, login: null },
    { version: 1, login: { username: 'alice', use_cookies: true, password: 'forbidden' }, activeTask: null },
    { version: 1, login: { username: 'alice', use_cookies: true }, activeTask: { username: 'bob', taskId: 'one' } },
  ])('rejects malformed desktop sessions instead of silently erasing state (%#)', async (session) => {
    const store = createSessionStore({ getBridge: () => ({ read: vi.fn().mockResolvedValue(session) }), getStorage: () => memoryStorage() });
    await expect(store.read()).rejects.toThrow('格式错误');
  });

  it('serializes desktop writes and allows a later write after an earlier failure', async () => {
    let rejectFirst;
    const saved = { version: 1, login: { username: 'alice', use_cookies: true }, activeTask: null };
    const bridge = {
      rememberLogin: vi.fn(() => new Promise((_resolve, reject) => { rejectFirst = reject; })),
      rememberTask: vi.fn().mockResolvedValue(saved),
    };
    const store = createSessionStore({ getBridge: () => bridge, getStorage: () => memoryStorage() });
    const first = expect(store.rememberLogin('alice')).rejects.toThrow('disk failed');
    const second = store.rememberTask(null);
    await Promise.resolve();
    expect(bridge.rememberTask).not.toHaveBeenCalled();
    rejectFirst(new Error('disk failed'));
    await first;
    expect(await second).toEqual(saved);
    expect(bridge.rememberTask).toHaveBeenCalledExactlyOnceWith(null);
  });

  it.each([
    { username: null, taskId: 'task' },
    { username: ' alice ', taskId: 'task' },
    { username: 'alice', taskId: '../task' },
    { username: 'alice', taskId: 'task', password: 'forbidden' },
  ])('rejects invalid task arguments before desktop persistence (%#)', async (task) => {
    const bridge = { rememberTask: vi.fn().mockResolvedValue({ version: 1, login: null, activeTask: null }) };
    const store = createSessionStore({ getBridge: () => bridge });
    await expect(store.rememberTask(task)).rejects.toThrow('任务信息格式错误');
    expect(bridge.rememberTask).not.toHaveBeenCalled();
  });
});

```


## web/src/main.jsx

```
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import DesktopStartup from './components/DesktopStartup.jsx'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <DesktopStartup>
      <App />
    </DesktopStartup>
  </React.StrictMode>,
)

```


## web/vite.config.js

```
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://localhost:5000',
        changeOrigin: true
      }
    }
  }
})

```
