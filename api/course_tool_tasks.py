"""Course utility validation, progress snapshots and cooperative task execution."""

from copy import deepcopy
import json
import math
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import subprocess
import sys
import time

from api.course_tools import parse_studentstudy_url
from api.exceptions import LoginError
from api.fs_policy import reject_links
from api.logger import logger
from api.privacy import sanitize_errors
from api.shutdown import shutdown_manager
from api.task_logging import task_log_sink
from api.task_state import TaskNotFound


TOOL_TYPES = frozenset({"visits", "catalog", "video_time", "read_time", "download"})
TIMED_TOOLS = frozenset({"video_time", "read_time"})
RESOURCE_FLAGS = {"video_time": "watchable", "read_time": "readable", "download": "downloadable"}
TASK_LABELS = {"visits": "课程学习次数", "catalog": "读取课程资源", "video_time": "视频观看时长",
               "read_time": "阅读时长", "download": "课程资源下载"}
MAX_ITEMS = 1000
MAX_CATALOG_ITEMS = 5000
MAX_READ_URLS = 50
# Keep every complete task snapshot within the native proxy's MAX_RESPONSE_BODY
# (4 MiB in desktop/src-tauri/src/api_proxy.rs).
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
# Reserve a bounded result row for every selected resource in a follow-up task
# (download / video_time / read_time), including Unicode JSON escaping.
RESULT_RESERVE_BYTES = 4096
SNAPSHOT_OVERHEAD_BYTES = 65536
SAFE_ID = re.compile(r"[a-zA-Z0-9_-]{1,128}\Z")
# Keep room for the ~160-char resource names the downloader generates under
# the classic MAX_PATH limit.
DOWNLOAD_DIR_MAX_LENGTH = 200
WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", *(f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10))})
PUBLIC_RESOURCE_FIELDS = frozenset({
    "id", "course_id", "course_title", "chapter_id", "chapter_title", "name",
    "kind", "downloadable", "watchable", "readable", "duration",
})


class CheckpointError(RuntimeError):
    """Stop sending requests when acknowledged work cannot be saved."""


def _checkpoint(store, task_id):
    try:
        store.checkpoint(task_id)
    except OSError as exc:
        raise CheckpointError("保存执行记录失败，已停止后续操作；请检查数据目录") from exc


def _number(value, label, low, high, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"{label}必须为有效数值")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{label}必须为有效数值") from exc
    if not math.isfinite(number) or not low <= number <= high or (integer and not number.is_integer()):
        raise ValueError(f"{label}必须在 {low} 到 {high} 之间" + ("，且为整数" if integer else ""))
    return int(number) if integer else number


def _minutes_text(value):
    """Platform counters arrive as floats or missing; show either honestly."""
    if value is None:
        return "未知"
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return "未知"


def _parse_download_dir(value):
    """Return a trimmed custom download directory, or None for the default."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("下载目录必须是有效路径")
    text = value.strip()
    if not text:
        return None
    if len(text) > DOWNLOAD_DIR_MAX_LENGTH:
        raise ValueError("下载目录路径过长，请选择更浅的目录")
    if any(ord(char) < 32 or char in '<>"|?*' for char in text):
        raise ValueError("下载目录包含无效字符")
    pure = PureWindowsPath(text)
    if not pure.is_absolute() or len(pure.parts) < 2:
        raise ValueError("下载目录必须是形如 D:\\Download 的绝对路径")
    for part in pure.parts[1:]:
        if part in {".", ".."} or part.upper() in WINDOWS_RESERVED_NAMES or part != part.rstrip(" ."):
            raise ValueError("下载目录包含无效的路径片段")
    return text


def parse_options(task_type, options):
    if not isinstance(task_type, str) or task_type not in TOOL_TYPES or not isinstance(options, dict):
        raise ValueError("课程工具类型或参数格式错误")
    allowed = {
        "visits": {"count", "interval"}, "catalog": {"purpose"},
        "video_time": {"source_task_id", "resource_ids", "minutes"},
        "read_time": {"source_task_id", "resource_ids", "urls", "minutes"},
        "download": {"source_task_id", "resource_ids", "download_dir"},
    }[task_type]
    if set(options) - allowed:
        raise ValueError("课程工具包含不支持的参数")
    if task_type == "visits":
        return {"count": _number(options.get("count", 10), "学习次数", 1, 1000, integer=True),
                "interval": _number(options.get("interval", 30), "请求间隔（秒）", 1, 3600)}
    if task_type == "catalog":
        if not isinstance(options.get("purpose"), str) or options["purpose"] not in RESOURCE_FLAGS:
            raise ValueError("请选择视频时长、阅读时长或资源下载功能")
        return {"purpose": options["purpose"]}
    if task_type == "read_time" and "urls" in options:
        if "source_task_id" in options or "resource_ids" in options:
            raise ValueError("按链接阅读时不能同时传入资源列表参数")
        raw_urls = options.get("urls")
        if not isinstance(raw_urls, list) or not 1 <= len(raw_urls) <= MAX_READ_URLS:
            raise ValueError(f"请输入 1 到 {MAX_READ_URLS} 条章节链接")
        urls = list(dict.fromkeys(parse_studentstudy_url(item)["url"] for item in raw_urls))
        return {
            "urls": urls,
            "minutes": _number(options.get("minutes", 30), "每个链接的目标阅读分钟数", 0.1, 1440),
        }
    source = options.get("source_task_id")
    ids = options.get("resource_ids")
    if not isinstance(source, str) or not SAFE_ID.fullmatch(source):
        raise ValueError("资源列表任务 ID 格式错误，请重新读取资源")
    if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_ITEMS:
        raise ValueError(f"请选择 1 到 {MAX_ITEMS} 个资源")
    if any(not isinstance(item, str) or not SAFE_ID.fullmatch(item) for item in ids):
        raise ValueError("资源 ID 格式错误，请重新读取资源")
    normalized = {"source_task_id": source, "resource_ids": list(dict.fromkeys(ids))}
    if task_type == "download":
        normalized["download_dir"] = _parse_download_dir(options.get("download_dir"))
    if task_type in TIMED_TOOLS:
        label = "每项的目标阅读分钟数" if task_type == "read_time" else "每个视频的目标分钟数"
        normalized["minutes"] = _number(options.get("minutes", 30), label, 0.1, 1440)
    return normalized


def resume_config(config):
    saved = deepcopy({key: config[key] for key in ("task_type", "course_list", "tool_options")})
    if saved["task_type"] == "read_time" and "urls" in saved["tool_options"]:
        # Strip signed tokens (enc/openc) from disk persistence; timed tasks never auto-replay.
        saved["tool_options"]["urls"] = [
            parse_studentstudy_url(item)["sanitized_url"] for item in saved["tool_options"]["urls"]
        ]
    return saved


def initial_status(config, details=None):
    kind = config["task_type"]
    options = config["tool_options"]
    if kind == "read_time" and "urls" in options:
        total = len(options["urls"])
    else:
        total = len(options.get("resource_ids", config["course_list"]))
    label = TASK_LABELS[kind]
    if kind == "catalog":
        label = {"video_time": "读取视频列表", "read_time": "读取阅读列表", "download": label}[options["purpose"]]
    status = {
        "task_type": kind, "task_label": label, "progress": 0, "total": total,
        "current_course": "", "current_chapter": "", "current_task": "",
        "shutdown_on_complete": bool(config.get("shutdown_on_complete")) and kind != "catalog",
        "stats": {"total_courses": len(config["course_list"]), "completed_courses": 0,
                  "failed_courses": 0, "skipped_courses": 0, "partial_courses": 0,
                  "total_chapters": 0, "completed_chapters": 0, "empty_chapters": 0,
                  "failed_chapters": 0, "skipped_chapters": 0,
                  "total_tasks": total, "completed_tasks": 0, "failed_tasks": 0, "skipped_tasks": 0},
    }
    if details is not None:
        _refresh_counts(status, details)
    return status


def _refresh_counts(status, details):
    results = details["tool"]["results"]
    status["progress"] = len(results)
    for name, value in (("completed", "completed"), ("failed", "error"), ("skipped", "skipped")):
        status["stats"][f"{name}_tasks"] = sum(result["status"] == value for result in results)


def initial_details(config, resources):
    kind, options = config["task_type"], config["tool_options"]
    unit = {"visits": "次", "catalog": "章节", "video_time": "秒", "read_time": "秒", "download": "字节"}[kind]
    if kind == "visits":
        total = options["count"] * len(config["course_list"])
    elif kind == "read_time" and "urls" in options:
        total = round(options["minutes"] * 60) * len(options["urls"])
    elif kind in TIMED_TOOLS:
        total = round(options["minutes"] * 60) * len(resources)
    elif kind == "download":
        total = None
    else:
        total = 0
    return {"courses": [], "active_jobs": {}, "tool": {
        "purpose": options.get("purpose", kind), "course_ids": list(config["course_list"]),
        "resources": deepcopy(resources), "results": [],
        "completed_units": 0, "total_units": total, "unit": unit, "current": None,
    }}


def selected_resources(store, account, course_ids, task_type, options):
    """Resolve opaque IDs against a completed catalogue owned by this account."""
    with store.edit(options["source_task_id"]) as source:
        if source.account != account:
            raise TaskNotFound(options["source_task_id"])
        if source.status.get("task_type") != "catalog" or source.status["status"] not in {"completed", "partial"}:
            raise ValueError("资源列表尚未读取完成，请等待完成或重新读取")
        tool = source.details.get("tool", {})
        if tool.get("purpose") != task_type:
            raise ValueError("资源列表与当前功能不符，请重新读取")
        if not set(course_ids).issubset(tool.get("course_ids", [])):
            raise ValueError("所选课程不属于当前资源列表")
        available = {item["id"]: item for item in tool.get("resources", [])}
        selected = []
        for resource_id in options["resource_ids"]:
            resource = available.get(resource_id)
            if resource is None or resource.get("course_id") not in course_ids:
                raise ValueError("所选资源不属于当前课程，请重新读取资源列表")
            if resource.get(RESOURCE_FLAGS[task_type]) is not True:
                raise ValueError("所选资源不支持当前操作")
            selected.append(deepcopy(resource))
        probe = initial_details(
            {"task_type": task_type, "course_list": list(course_ids), "tool_options": options},
            [],
        )
        if not _selection_fits(probe, selected):
            raise ValueError("单次勾选的资源过多，请减少勾选数量后分批执行")
        return selected


def download_directory(data_dir, task_id, *, create=False, custom=None):
    """Resolve where a download task stores files: a user-chosen absolute
    directory, or the per-task folder under the data dir by default."""
    if not isinstance(task_id, str) or not SAFE_ID.fullmatch(task_id):
        raise ValueError("任务 ID 格式错误")
    base = Path(os.path.abspath(data_dir))
    directory = Path(custom) if custom else base / "downloads" / task_id
    if custom and not directory.is_absolute():
        raise ValueError("下载目录必须是绝对路径")
    reject_links(directory)
    if create:
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ValueError(f"无法创建下载目录：{exc}") from exc
        reject_links(directory)
    resolved = directory.resolve()
    if not custom and not resolved.is_relative_to(base.resolve()):
        raise ValueError("下载目录超出允许范围")
    return resolved


def _valid_download(result, directory):
    try:
        path = Path(result["path"])
        if ".." in path.parts:
            return False
        if not path.is_absolute():
            path = directory / path
        if not path.is_relative_to(directory):
            return False
        reject_links(path)
        info = path.stat()
        return stat.S_ISREG(info.st_mode) and type(result.get("bytes")) is int and result["bytes"] == info.st_size
    except (KeyError, TypeError, ValueError, OSError):
        return False


def restored_details(config, previous, data_dir, task_id):
    resources = previous.get("tool", {}).get("resources", [])
    details = initial_details(config, resources if config["task_type"] == "download" else [])
    if config["task_type"] == "download":
        directory = download_directory(data_dir, task_id, custom=config["tool_options"].get("download_dir"))
        selected = set(config["tool_options"]["resource_ids"])
        details["tool"]["results"] = [deepcopy(result) for result in previous.get("tool", {}).get("results", [])
            if result.get("id") in selected and result.get("status") == "completed" and _valid_download(result, directory)]
        details["tool"]["completed_units"] = sum(result["bytes"] for result in details["tool"]["results"])
        if directory.is_dir():
            details["tool"]["output_dir"] = str(directory)
    return details


def open_download_directory(store, account, task_id, data_dir):
    with store.edit(task_id) as task:
        if task.account != account:
            raise TaskNotFound(task_id)
        if task.status.get("task_type") != "download":
            raise ValueError("此任务没有下载目录")
        stored = task.details.get("tool", {}).get("output_dir")
    if stored:
        directory = Path(stored)
        reject_links(directory)
    else:
        # Task created but never started: fall back to the default location,
        # which does not exist yet either and reports the same message below.
        directory = download_directory(data_dir, task_id)
    if not directory.is_dir():
        raise ValueError("下载目录尚未创建或已被移动")
    if sys.platform == "win32":
        os.startfile(str(directory))
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(directory)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return str(directory)


def create_service(client, cancel_check, purpose=None):
    from api.course_tools import CourseTools
    return CourseTools(client, cancel_check=cancel_check)


def _sanitize_current(task):
    """分块级热路径写入只有计数器与 current 小卡片，擦洗范围收窄到 current。"""
    sanitize_errors(task.details.get("tool", {}).get("current"))


class _Progress:
    def __init__(self, store, task_id, config):
        self.store, self.task_id, self.config = store, task_id, config
        self.kind = config["task_type"]
        self.metric = {"visits": "submitted", "catalog": "chapters", "video_time": "seconds",
                       "read_time": "seconds", "download": "bytes"}[self.kind]
        self.completed = 0
        self._last_save = 0
        # 已记录行（含断点恢复行）的累计量：update() 增量累加，
        # 不再随每个下载分块对 results 全表重算。
        with store.edit(task_id) as task:
            self._recorded = sum(result.get(self.metric, 0) for result in task.details["tool"]["results"])

    def begin(self, course, item, total, unit=None):
        self.completed = 0
        with self.store.edit(self.task_id) as task:
            task.status.update(current_course=course["title"], current_chapter=item.get("chapter_title", ""), current_task=item["name"])
            _set_course_status(task.details, course["courseId"], "running")
            tool = task.details["tool"]
            tool["current"] = {"name": item["name"], "completed": 0, "total": total, "unit": unit or tool["unit"]}

    def update(self, completed, total, *, count_units=True):
        self.completed = max(0, completed)
        with self.store.edit(self.task_id, sanitize=_sanitize_current) as task:
            tool = task.details["tool"]
            if tool["current"] is not None:
                tool["current"].update(completed=self.completed, total=total)
            if count_units:
                tool["completed_units"] = self._recorded + self.completed
                if self.kind == "catalog":
                    tool["total_units"] = self._recorded + (total or 0)
        now = time.monotonic()
        if count_units and (self.kind == "visits" or now - self._last_save >= 5):
            _checkpoint(self.store, self.task_id)
            self._last_save = now

    def chapters(self, title, completed, total):
        with self.store.edit(self.task_id) as task:
            task.status["current_chapter"] = str(title)
        self.update(completed, total, count_units=self.kind == "catalog")

    def record(self, course, item, state, data=None, message=""):
        row = {"id": item["id"], "name": str(item["name"])[:120], "course_title": str(course["title"])[:120],
               "course_id": str(course["courseId"]), "status": state, "message": str(message)[:240]}
        # Failed downloads remove their partial file. Received bytes are useful
        # only while transferring; they must never be presented as saved files.
        row[self.metric] = 0 if self.kind == "download" and state != "completed" else self.completed
        if data:
            for key in ("before", "after", "submitted", "seconds", "path", "bytes"):
                if key in data:
                    row[key] = data[key]
        _bound_result_row(row)
        with self.store.edit(self.task_id) as task:
            tool = task.details["tool"]
            tool["results"].append(row)
            self._recorded += row[self.metric]
            tool["completed_units"] = self._recorded
            tool["current"] = None
            if self.kind in {"catalog", "visits"}:
                _set_course_status(task.details, course["courseId"], state)
            _refresh_counts(task.status, task.details)
        _checkpoint(self.store, self.task_id)


def _set_course_status(details, course_id, state):
    target = str(course_id)
    for course in details.get("courses", []):
        if str(course.get("id")) == target:
            course["status"] = state
            return


def _course_status_from_results(details, course_id, *, partial_run=False):
    target = str(course_id)
    rows = [row for row in details.get("tool", {}).get("results", []) if str(row.get("course_id")) == target]
    if not rows:
        return "skipped" if partial_run else "completed"
    statuses = {row.get("status") for row in rows}
    if partial_run:
        return "partial" if statuses & {"completed", "partial"} else "skipped"
    if statuses == {"completed"}:
        return "completed"
    if statuses == {"error"}:
        return "error"
    if statuses == {"skipped"}:
        return "skipped"
    return "partial"


def _finalize_course_statuses(details, *, cancelled=False):
    results = details.get("tool", {}).get("results", [])
    for course in details.get("courses", []):
        current = course.get("status")
        if current in {"completed", "partial", "error", "skipped", "cancelled"}:
            continue
        target = str(course.get("id"))
        rows = [row for row in results if str(row.get("course_id")) == target]
        if any(row.get("status") in {"completed", "partial"} for row in rows):
            course["status"] = "partial"
        elif cancelled:
            course["status"] = "cancelled" if current == "running" else "skipped"
        else:
            course["status"] = "error" if current == "running" else "skipped"


def _public_resources(service, resources):
    public = []
    for resource in resources:
        item = {key: value for key, value in service.public_resource(resource).items() if key in PUBLIC_RESOURCE_FIELDS}
        if not isinstance(item.get("id"), str) or not SAFE_ID.fullmatch(item["id"]):
            raise ValueError("课程资源标识无效")
        for key in ("name", "course_title", "chapter_title"):
            item[key] = str(item.get(key, ""))[:120]
        public.append(item)
    return public


def _filter_catalog_resources(resources, purpose):
    flag = RESOURCE_FLAGS[purpose]
    if purpose == "video_time":
        return [item for item in resources if item.get(flag) is True or item.get("kind") == "video"]
    if purpose == "read_time":
        return [item for item in resources if item.get(flag) is True or item.get("kind") in {"read", "document"}]
    return [item for item in resources if item.get(flag) is True]


def _json_size(value):
    return len(json.dumps(value, ensure_ascii=True, allow_nan=False,
                          separators=(",", ":")).encode("utf-8"))


def _wire_size(details):
    # Flask's default JSON encoder escapes Chinese characters; use that exact
    # representation instead of measuring only an unescaped resources array.
    return _json_size({"status": True, "data": details}) + 1


def _bound_result_row(row):
    # Include the array separator. Trim display text by whole Unicode code
    # points; identifiers, numeric results and saved paths must remain exact.
    excess = _json_size(row) + 1 - RESULT_RESERVE_BYTES
    for key in ("message", "name", "course_title"):
        if excess <= 0:
            break
        value = row[key]
        end = len(value)
        while end and excess > 0:
            end -= 1
            excess -= _json_size(value[end]) - 2  # Exclude JSON string quotes.
        row[key] = value[:end]


def _catalog_fits(details, resources):
    if len(resources) > MAX_CATALOG_ITEMS:
        return False
    candidate = deepcopy(details)
    candidate["tool"]["resources"] = resources
    course_count = len(candidate["tool"].get("course_ids", []))
    remaining_results = max(0, course_count - len(candidate["tool"].get("results", [])))
    return (_wire_size(candidate)
            + remaining_results * RESULT_RESERVE_BYTES
            + SNAPSHOT_OVERHEAD_BYTES <= MAX_SNAPSHOT_BYTES)


def _selection_fits(details, resources):
    candidate = deepcopy(details)
    candidate["tool"]["resources"] = resources
    return (len(resources) <= MAX_ITEMS
            and _wire_size(candidate) + len(resources) * RESULT_RESERVE_BYTES
            + SNAPSHOT_OVERHEAD_BYTES <= MAX_SNAPSHOT_BYTES)


def _fit_catalog_prefix(details, existing, incoming):
    """Return the largest prefix of incoming that keeps the catalog within budget."""
    if _catalog_fits(details, [*existing, *incoming]):
        return incoming
    low, high = 0, len(incoming)
    while low < high:
        mid = (low + high + 1) // 2
        if _catalog_fits(details, [*existing, *incoming[:mid]]):
            low = mid
        else:
            high = mid - 1
    return incoming[:low]


def _outcome(store, task_id, *, fatal=False):
    status = store.get_status(task_id)
    tool = store.get_details(task_id)["tool"]
    results = tool["results"]
    failed = fatal or any(result["status"] == "error" for result in results)
    partial = any(result["status"] == "partial" for result in results)
    skipped = any(result["status"] == "skipped" for result in results)
    if status.get("task_type") == "catalog":
        useful = any(result["status"] in {"completed", "partial"} for result in results) or bool(tool.get("resources"))
    else:
        useful = any(result["status"] in {"completed", "partial"} for result in results) or tool["completed_units"] > 0
    if failed and not useful:
        return "error"
    return "partial" if failed or partial or skipped else "completed"


def run_tool_task(task_id, store, config, data_dir, client_factory):
    """Run only authenticated, bounded operations; publish terminal state last."""
    progress = _Progress(store, task_id, config)
    cancelled = lambda: store.is_cancelled(task_id)
    outcome, error = "error", None

    try:
        with task_log_sink(store, task_id):
            try:
                if cancelled():
                    return
                with client_factory(config["username"], config["password"]) as client:
                    result = client.login(login_with_cookies=config["use_cookies"])
                    if not result["status"]:
                        raise LoginError(result.get("msg", "登录失败"))
                    if cancelled():
                        return
                    enrolled = {str(course["courseId"]): course for course in client.get_course_list()}
                    service = create_service(client, cancelled, config["tool_options"].get("purpose", config["task_type"]))
                    if config["task_type"] == "read_time" and "urls" in config["tool_options"]:
                        url_courses = []
                        seen_courses = set()
                        for raw_url in config["tool_options"]["urls"]:
                            course_id = parse_studentstudy_url(raw_url)["courseId"]
                            if course_id in seen_courses:
                                continue
                            seen_courses.add(course_id)
                            title = str(enrolled.get(course_id, {}).get("title") or f"课程 {course_id}")[:120]
                            url_courses.append({"id": course_id, "title": title, "status": "pending", "chapters": []})
                        with store.edit(task_id) as task:
                            task.details["courses"] = url_courses
                        _run_read_urls(task_id, store, config, service, enrolled, progress, cancelled)
                    else:
                        if any(course_id not in enrolled for course_id in config["course_list"]):
                            raise ValueError("部分课程已不属于当前账号，请重新获取课程列表")
                        courses = [{**enrolled[course_id], "title": str(enrolled[course_id].get("title", ""))[:120]}
                                   for course_id in config["course_list"]]
                        with store.edit(task_id) as task:
                            task.details["courses"] = [
                                {"id": str(course["courseId"]), "title": course["title"], "status": "pending", "chapters": []}
                                for course in courses
                            ]
                        if config["task_type"] in {"catalog", "visits"}:
                            _run_courses(task_id, store, config, service, courses, progress, cancelled)
                        else:
                            _run_resources(task_id, store, config, service, courses, progress, cancelled, data_dir)
                    outcome = _outcome(store, task_id)
                    if outcome == "error":
                        results = store.get_details(task_id)["tool"]["results"]
                        if len(results) == 1 and results[0].get("message"):
                            error = results[0]["message"]
                        else:
                            error = "操作未完成，请查看执行结果后重试"
                    elif outcome != "completed":
                        error = "部分操作未完成，请查看执行结果后重试"
                    if config["task_type"] == "download" and outcome == "completed":
                        with store.edit(task_id) as task:
                            task.details["tool"]["total_units"] = task.details["tool"]["completed_units"]
                    logger.info("{}执行结束", TASK_LABELS[config["task_type"]])
            except Exception as exc:
                error = str(exc)
                outcome = _outcome(store, task_id, fatal=True)
                if not cancelled():
                    logger.error("课程工具执行失败：{}", exc)
            finally:
                if cancelled():
                    outcome, error = "cancelled", None
                    logger.info("课程工具已停止，已完成的记录和下载文件已保留")
    finally:
        # 已无 worker 会再回到 record()；终态不得残留"正在处理"的卡片或"等待中"的课程。
        try:
            with store.edit(task_id) as task:
                tool = task.details.get("tool")
                if tool is not None:
                    tool["current"] = None
                _finalize_course_statuses(task.details, cancelled=outcome == "cancelled")
        except TaskNotFound:
            pass
        if config.get("task_type") != "catalog":
            shutdown_manager.finish_task(store, task_id, outcome, error=error)
        else:
            store.finish(task_id, outcome, error=error)


def _run_courses(task_id, store, config, service, courses, progress, cancelled):
    options = config["tool_options"]
    for course in courses:
        if cancelled():
            break
        item = {"id": str(course["courseId"]), "name": course["title"]}
        progress.begin(course, item, options.get("count"))
        try:
            state = "completed"
            if config["task_type"] == "visits":
                result = service.add_visits(course, options["count"], options["interval"], on_progress=progress.update)
                message = f"已成功提交 {result['submitted']} 次访问；平台统计以返回值为准"
            else:
                purpose = options["purpose"]
                scanned = _public_resources(service, service.scan_course(course, on_chapter=progress.chapters))
                found = _filter_catalog_resources(scanned, purpose)
                with store.edit(task_id) as task:
                    resources = task.details["tool"]["resources"]
                    existing_ids = {entry["id"] for entry in resources}
                    incoming = list({entry["id"]: entry for entry in found if entry["id"] not in existing_ids}.values())
                    merged = [*resources, *incoming]
                    if _catalog_fits(task.details, merged):
                        task.details["tool"]["resources"] = merged
                        if not found and scanned:
                            message = f"读取到 0 个符合当前功能的资源（课程共 {len(scanned)} 个其他类型资源）"
                        else:
                            message = f"读取到 {len(found)} 个资源"
                    elif len(courses) > 1 and resources:
                        raise ValueError("课程资源过多，请减少勾选课程后分批读取")
                    else:
                        flag = RESOURCE_FLAGS[purpose]
                        prioritized = [entry for entry in incoming if entry.get(flag) is True] + [
                            entry for entry in incoming if entry.get(flag) is not True
                        ]
                        kept = _fit_catalog_prefix(task.details, resources, prioritized)
                        if not kept:
                            raise ValueError(
                                "单门课程资源过多，无法在单次请求中完整返回"
                                if len(courses) == 1 else "课程资源过多，请减少勾选课程后分批读取"
                            )
                        task.details["tool"]["resources"] = [*resources, *kept]
                        state = "partial"
                        if purpose == "read_time":
                            message = f"课程资源过多，已保留前 {len(kept)} / {len(incoming)} 个资源；其余章节可使用“按章节链接”阅读"
                        else:
                            message = f"课程资源过多，已保留前 {len(kept)} / {len(incoming)} 个资源；其余资源超出单次响应上限未载入"
                result = {}
            progress.record(course, item, state, result, message)
            logger.info("{}：{}", course["title"], message)
        except CheckpointError:
            raise
        except Exception as exc:
            state = "skipped" if cancelled() else "error"
            progress.record(course, item, state, message="已停止" if cancelled() else str(exc))
            if not cancelled():
                logger.error("{}处理失败：{}", course["title"], exc)
            else:
                break


def _read_message(result):
    message = f"已提交 {result['seconds']} 秒阅读记录，平台统计可能延迟更新（次日更新）"
    before, target = result.get("before_minutes"), result.get("target_minutes")
    if before is not None or target is not None:
        message += f"；开始前平台显示 {_minutes_text(before)} / {_minutes_text(target)} 分钟"
    return message


def _run_read_urls(task_id, store, config, service, enrolled, progress, cancelled):
    options = config["tool_options"]
    seconds = round(options["minutes"] * 60)
    for index, raw_url in enumerate(options["urls"], 1):
        if cancelled():
            break
        parsed = parse_studentstudy_url(raw_url)
        course_id = parsed["courseId"]
        chapter_id = parsed["chapterId"]
        course_title = str(enrolled.get(course_id, {}).get("title") or f"课程 {course_id}")[:120]
        course = {"courseId": course_id, "title": course_title}
        item = {"id": f"url-{index}", "name": f"章节 {chapter_id}", "chapter_title": f"章节 {chapter_id}"}
        progress.begin(course, item, seconds)
        try:
            result = service.read_study_url(raw_url, seconds, enrolled=enrolled, on_progress=progress.update)
            message = _read_message(result)
            progress.record(course, item, "completed", result, message)
            logger.info("{}（{}）：{}", course_title, item["name"], message)
        except CheckpointError:
            raise
        except Exception as exc:
            state = "skipped" if cancelled() else "error"
            progress.record(course, item, state, message="已停止" if cancelled() else str(exc))
            if not cancelled():
                logger.error("{}（{}）处理失败：{}", course_title, item["name"], exc)
            else:
                break
    if not cancelled():
        with store.edit(task_id) as task:
            for course in task.details.get("courses", []):
                course["status"] = _course_status_from_results(task.details, course["id"])


def _run_resources(task_id, store, config, service, courses, progress, cancelled, data_dir):
    kind, options = config["task_type"], config["tool_options"]
    details = store.get_details(task_id)
    selected = details["tool"]["resources"]
    if {item["id"] for item in selected} != set(options["resource_ids"]):
        raise ValueError("保存的资源选择不完整，请重新读取资源列表")
    directory = None
    if kind == "download":
        directory = download_directory(data_dir, task_id, create=True, custom=options.get("download_dir"))
        with store.edit(task_id) as task:
            task.details["tool"]["output_dir"] = str(directory)
        _checkpoint(store, task_id)
    completed = {row["id"] for row in details["tool"]["results"] if row["status"] == "completed"}
    for course in courses:
        if cancelled():
            break
        items = [item for item in selected if item["course_id"] == str(course["courseId"]) and item["id"] not in completed]
        if not items:
            with store.edit(task_id) as task:
                _set_course_status(task.details, course["courseId"], _course_status_from_results(task.details, course["courseId"]))
            continue
        progress.begin(course, {"name": "刷新课程资源"}, None, "章节")
        try:
            fresh = {resource["id"]: resource for resource in service.scan_course(course, on_chapter=progress.chapters)}
        except CheckpointError:
            raise
        except Exception as exc:
            for item in items:
                progress.completed = 0
                progress.record(course, item, "skipped" if cancelled() else "error", message="已停止" if cancelled() else str(exc))
            with store.edit(task_id) as task:
                _set_course_status(task.details, course["courseId"], _course_status_from_results(task.details, course["courseId"], partial_run=cancelled()))
            if not cancelled():
                logger.error("刷新课程资源失败：{}", exc)
            continue
        for item in items:
            if cancelled():
                break
            seconds = round(options["minutes"] * 60) if kind in TIMED_TOOLS else None
            progress.begin(course, item, seconds)
            try:
                resource = fresh.get(item["id"])
                if resource is None:
                    raise ValueError("此资源已不可用或章节未开放，请重新读取资源列表")
                if kind == "video_time":
                    result = service.watch_video(course, resource, seconds, on_progress=progress.update)
                    message = f"已提交 {result['seconds']} 秒观看记录，平台统计可能延迟更新"
                elif kind == "read_time":
                    result = service.read_resource(course, resource, seconds, on_progress=progress.update)
                    message = _read_message(result)
                else:
                    result = service.download_resource(course, resource, directory, on_progress=progress.update)
                    # The task directory is displayed once; rows retain its
                    # relative filename, which also keeps large lists bounded.
                    result = {**result, "path": Path(result["path"]).relative_to(directory).as_posix()}
                    message = "文件已保存"
                progress.record(course, item, "completed", result, message)
                logger.info("{}：{}", item["name"], message)
            except CheckpointError:
                if kind == "download":
                    # The downloader has removed the unfinished part file.
                    # A row already added by record() represents a published
                    # file and must remain visible even if saving it failed.
                    with store.edit(task_id) as task:
                        tool = task.details["tool"]
                        tool["completed_units"] = sum(row.get("bytes", 0) for row in tool["results"] if row["status"] == "completed")
                        tool["current"] = None
                raise
            except Exception as exc:
                progress.record(course, item, "skipped" if cancelled() else "error", message="已停止" if cancelled() else str(exc))
                if not cancelled():
                    logger.error("{}处理失败：{}", item["name"], exc)
                else:
                    break
        with store.edit(task_id) as task:
            _set_course_status(task.details, course["courseId"], _course_status_from_results(task.details, course["courseId"], partial_run=cancelled()))
