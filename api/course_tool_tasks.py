"""Course utility validation, progress snapshots and cooperative task execution."""

from copy import deepcopy
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

from api.exceptions import LoginError
from api.logger import logger
from api.task_state import TaskNotFound


TOOL_TYPES = frozenset({"visits", "catalog", "video_time", "reading_time", "download"})
TIMED_TOOLS = frozenset({"video_time", "reading_time"})
RESOURCE_FLAGS = {"video_time": "watchable", "reading_time": "readable", "download": "downloadable"}
TASK_LABELS = {"visits": "课程学习次数", "catalog": "读取课程资源", "video_time": "视频观看时长",
               "reading_time": "课程阅读时长", "download": "课程资源下载"}
MAX_ITEMS = 1000
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
# Reserve a bounded result row for every selectable resource, including Unicode
# JSON escaping. The native proxy caps the complete response at 2 MiB.
RESULT_RESERVE_BYTES = 4096
SNAPSHOT_OVERHEAD_BYTES = 65536
SAFE_ID = re.compile(r"[a-zA-Z0-9_-]{1,128}\Z")
PUBLIC_RESOURCE_FIELDS = frozenset({
    "id", "course_id", "course_title", "chapter_id", "chapter_title", "name",
    "kind", "downloadable", "watchable", "duration", "readable",
    "required_minutes", "read_minutes", "book_count",
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


def parse_options(task_type, options):
    if not isinstance(task_type, str) or task_type not in TOOL_TYPES or not isinstance(options, dict):
        raise ValueError("课程工具类型或参数格式错误")
    allowed = {
        "visits": {"count", "interval"}, "catalog": {"purpose"},
        "video_time": {"source_task_id", "resource_ids", "minutes"},
        "reading_time": {"source_task_id", "resource_ids", "minutes"},
        "download": {"source_task_id", "resource_ids"},
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
    source = options.get("source_task_id")
    ids = options.get("resource_ids")
    if not isinstance(source, str) or not SAFE_ID.fullmatch(source):
        raise ValueError("资源列表任务 ID 格式错误，请重新读取资源")
    if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_ITEMS:
        raise ValueError(f"请选择 1 到 {MAX_ITEMS} 个资源")
    if any(not isinstance(item, str) or not SAFE_ID.fullmatch(item) for item in ids):
        raise ValueError("资源 ID 格式错误，请重新读取资源")
    normalized = {"source_task_id": source, "resource_ids": list(dict.fromkeys(ids))}
    if task_type in TIMED_TOOLS:
        label = "每个阅读任务的新增分钟数" if task_type == "reading_time" else "每个视频的目标分钟数"
        normalized["minutes"] = _number(options.get("minutes", 30), label, 0.1, 1440)
    return normalized


def resume_config(config):
    return deepcopy({key: config[key] for key in ("task_type", "course_list", "tool_options")})


def initial_status(config, details=None):
    kind = config["task_type"]
    total = len(config["tool_options"].get("resource_ids", config["course_list"]))
    label = TASK_LABELS[kind]
    if kind == "catalog":
        label = {"video_time": "读取视频列表", "reading_time": "读取阅读任务",
                 "download": label}[config["tool_options"]["purpose"]]
    status = {
        "task_type": kind, "task_label": label, "progress": 0, "total": total,
        "current_course": "", "current_chapter": "", "current_task": "",
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
    unit = {"visits": "次", "catalog": "章节", "video_time": "秒", "reading_time": "秒", "download": "字节"}[kind]
    total = options["count"] * len(config["course_list"]) if kind == "visits" else (
        round(options["minutes"] * 60) * len(resources) if kind in TIMED_TOOLS else None if kind == "download" else 0
    )
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
        return selected


def _reject_links(path):
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ValueError("下载目录不能包含符号链接或目录联接")


def download_directory(data_dir, task_id, *, create=False):
    if not isinstance(task_id, str) or not SAFE_ID.fullmatch(task_id):
        raise ValueError("任务 ID 格式错误")
    base = Path(os.path.abspath(data_dir))
    directory = base / "downloads" / task_id
    _reject_links(directory)
    if create:
        directory.mkdir(parents=True, exist_ok=True)
        _reject_links(directory)
    resolved = directory.resolve()
    if not resolved.is_relative_to(base.resolve()):
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
        _reject_links(path)
        info = path.stat()
        return stat.S_ISREG(info.st_mode) and type(result.get("bytes")) is int and result["bytes"] == info.st_size
    except (KeyError, TypeError, ValueError, OSError):
        return False


def restored_details(config, previous, data_dir, task_id):
    resources = previous.get("tool", {}).get("resources", [])
    details = initial_details(config, resources if config["task_type"] == "download" else [])
    if config["task_type"] == "download":
        directory = download_directory(data_dir, task_id)
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
    if purpose == "reading_time":
        from api.reading_time import ReadingTools
        return ReadingTools(client, cancel_check=cancel_check)
    from api.course_tools import CourseTools
    return CourseTools(client, cancel_check=cancel_check)


class _Progress:
    def __init__(self, store, task_id, config):
        self.store, self.task_id, self.config = store, task_id, config
        self.kind = config["task_type"]
        self.metric = {"visits": "submitted", "catalog": "chapters", "video_time": "seconds",
                       "reading_time": "seconds", "download": "bytes"}[self.kind]
        self.completed = 0
        self._last_save = 0

    def begin(self, course, item, total, unit=None):
        self.completed = 0
        with self.store.edit(self.task_id) as task:
            task.status.update(current_course=course["title"], current_chapter=item.get("chapter_title", ""), current_task=item["name"])
            tool = task.details["tool"]
            tool["current"] = {"name": item["name"], "completed": 0, "total": total, "unit": unit or tool["unit"]}

    def update(self, completed, total, *, count_units=True):
        self.completed = max(0, completed)
        with self.store.edit(self.task_id) as task:
            tool = task.details["tool"]
            if tool["current"] is not None:
                tool["current"].update(completed=self.completed, total=total)
            if count_units:
                tool["completed_units"] = sum(result.get(self.metric, 0) for result in tool["results"]) + self.completed
                if self.kind == "catalog":
                    tool["total_units"] = sum(result.get("chapters", 0) for result in tool["results"]) + (total or 0)
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
            tool["completed_units"] = sum(result.get(self.metric, 0) for result in tool["results"])
            tool["current"] = None
            _refresh_counts(task.status, task.details)
        _checkpoint(self.store, self.task_id)


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
    candidate = deepcopy(details)
    candidate["tool"]["resources"] = resources
    return (len(resources) <= MAX_ITEMS
            and _wire_size(candidate) + len(resources) * RESULT_RESERVE_BYTES
            + SNAPSHOT_OVERHEAD_BYTES <= MAX_SNAPSHOT_BYTES)


def _outcome(store, task_id, *, fatal=False):
    tool = store.get_details(task_id)["tool"]
    results = tool["results"]
    failed = fatal or any(result["status"] == "error" for result in results)
    skipped = any(result["status"] == "skipped" for result in results)
    useful = any(result["status"] == "completed" for result in results) or tool["completed_units"] > 0
    if failed and not useful:
        return "error"
    return "partial" if failed or skipped else "completed"


def run_tool_task(task_id, store, config, data_dir, client_factory):
    """Run only authenticated, bounded operations; publish terminal state last."""
    progress = _Progress(store, task_id, config)
    cancelled = lambda: store.is_cancelled(task_id)
    outcome, error, sink_id = "error", None, None

    def capture(message):
        record = message.record
        if record["extra"].get("task_id") == task_id:
            store.append_log(task_id, str(message), level=record["level"].name.lower(), timestamp=record["time"].timestamp())

    with logger.contextualize(task_id=task_id):
        try:
            sink_id = logger.add(capture, enqueue=True, filter=lambda record: record["extra"].get("task_id") == task_id)
            if cancelled():
                return
            with client_factory(config["username"], config["password"]) as client:
                result = client.login(login_with_cookies=config["use_cookies"])
                if not result["status"]:
                    raise LoginError(result.get("msg", "登录失败"))
                if cancelled():
                    return
                enrolled = {str(course["courseId"]): course for course in client.get_course_list()}
                if any(course_id not in enrolled for course_id in config["course_list"]):
                    raise ValueError("部分课程已不属于当前账号，请重新获取课程列表")
                courses = [{**enrolled[course_id], "title": str(enrolled[course_id].get("title", ""))[:120]}
                           for course_id in config["course_list"]]
                service = create_service(client, cancelled, config["tool_options"].get("purpose", config["task_type"]))
                with store.edit(task_id) as task:
                    task.details["courses"] = [{"id": course["courseId"], "title": course["title"], "chapters": []} for course in courses]
                if config["task_type"] in {"catalog", "visits"}:
                    _run_courses(task_id, store, config, service, courses, progress, cancelled)
                else:
                    _run_resources(task_id, store, config, service, courses, progress, cancelled, data_dir)
                outcome = _outcome(store, task_id)
                if outcome != "completed":
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
            try:
                logger.complete()
            finally:
                try:
                    if sink_id is not None:
                        logger.remove(sink_id)
                finally:
                    store.finish(task_id, outcome, error=error)


def _run_courses(task_id, store, config, service, courses, progress, cancelled):
    options = config["tool_options"]
    for course in courses:
        if cancelled():
            break
        item = {"id": str(course["courseId"]), "name": course["title"]}
        progress.begin(course, item, options.get("count"))
        try:
            if config["task_type"] == "visits":
                result = service.add_visits(course, options["count"], options["interval"], on_progress=progress.update)
                message = f"已成功提交 {result['submitted']} 次访问；平台统计以返回值为准"
            else:
                found = _public_resources(service, service.scan_course(course, on_chapter=progress.chapters))
                with store.edit(task_id) as task:
                    resources = task.details["tool"]["resources"]
                    merged = list({item["id"]: item for item in [*resources, *found]}.values())
                    if not _catalog_fits(task.details, merged):
                        raise ValueError("课程资源过多，请减少勾选课程后分批读取")
                    task.details["tool"]["resources"] = merged
                label = "阅读任务" if options["purpose"] == "reading_time" else "资源"
                result, message = {}, f"读取到 {len(found)} 个{label}"
            progress.record(course, item, "completed", result, message)
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


def _run_resources(task_id, store, config, service, courses, progress, cancelled, data_dir):
    kind, options = config["task_type"], config["tool_options"]
    details = store.get_details(task_id)
    selected = details["tool"]["resources"]
    if {item["id"] for item in selected} != set(options["resource_ids"]):
        raise ValueError("保存的资源选择不完整，请重新读取资源列表")
    directory = None
    if kind == "download":
        directory = download_directory(data_dir, task_id, create=True)
        with store.edit(task_id) as task:
            task.details["tool"]["output_dir"] = str(directory)
        _checkpoint(store, task_id)
    completed = {row["id"] for row in details["tool"]["results"] if row["status"] == "completed"}
    for course in courses:
        if cancelled():
            break
        items = [item for item in selected if item["course_id"] == str(course["courseId"]) and item["id"] not in completed]
        if not items:
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
                elif kind == "reading_time":
                    result = service.watch_reading(course, resource, seconds, on_progress=progress.update)
                    message = f"已上报 {result['seconds']} 秒阅读记录，平台统计可能次日更新"
                    if result.get("warning"):
                        message += f"；{result['warning']}"
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
