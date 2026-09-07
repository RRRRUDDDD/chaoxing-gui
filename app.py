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
