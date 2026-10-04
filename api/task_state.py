"""Thread-safe, bounded storage for Web tasks and their polling cursors."""

from collections import deque
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from heapq import heappop, heappush
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from typing import Callable
from uuid import uuid4

from api.logger import logger
from api.privacy import redact, sanitize_errors


TERMINAL_STATES = frozenset({"completed", "error", "partial", "cancelled"})
RESUME_FIELDS = frozenset({
    "course_list", "jobs", "speed", "retry_interval", "notopen_action",
    "tiku_config", "notification_config",
})
# Records written before question-image OCR was removed still carry this key.
LEGACY_RESUME_FIELDS = RESUME_FIELDS | {"ocr_config"}
TOOL_RESUME_FIELDS = frozenset({"task_type", "course_list", "tool_options"})


class TaskAlreadyRunning(Exception):
    def __init__(self, task_id: str):
        super().__init__("该账号已有正在运行的任务")
        self.task_id = task_id


class TaskNotFound(KeyError):
    """A missing task or an account mismatch, distinct from upstream KeyError."""


@dataclass
class _Task:
    account: str
    status: dict
    details: dict
    resume_config: dict | None = None
    logs: deque = field(default_factory=deque)
    sequence: int = 0
    expires_at: float | None = None
    # Runtime-only stop signal. It is never persisted: a restarted process has
    # no worker left to stop, and reloaded tasks start with a clear event.
    cancel: threading.Event = field(default_factory=threading.Event)


class TaskStore:
    """All mutations use one lock; API readers receive independent snapshots.

    Expiry is checked on access. A single optional cleaner also expires idle
    records, so finishing a task never allocates a sleeping cleanup thread.
    Durable checkpoints rewrite only study_tasks/<id>.json, never other tasks.
    The former aggregate file is migrated once and retained as .json.migrated.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = 3600,
        max_logs: int = 1000,
        max_message_chars: int = 4096,
        clock: Callable[[], float] = time.monotonic,
        wall_time: Callable[[], float] = time.time,
        cleanup_interval: float | None = None,
        state_file: str | Path | None = None,
    ):
        if not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive and finite")
        if not isinstance(max_logs, int) or isinstance(max_logs, bool) or max_logs < 1:
            raise ValueError("max_logs must be a positive integer")
        if not isinstance(max_message_chars, int) or isinstance(max_message_chars, bool) or max_message_chars < 1:
            raise ValueError("max_message_chars must be a positive integer")
        if cleanup_interval is not None and (
            not math.isfinite(cleanup_interval) or cleanup_interval <= 0
        ):
            raise ValueError("cleanup_interval must be positive and finite")
        self._ttl = ttl_seconds
        self._max_logs = max_logs
        self._max_message_chars = max_message_chars
        self._clock = clock
        self._wall_time = wall_time
        self._cleanup_interval = cleanup_interval
        self._lock = threading.RLock()
        self._tasks: dict[str, _Task] = {}
        self._active_accounts: dict[str, str] = {}
        self._expirations: list[tuple[float, str]] = []
        self._stop = threading.Event()
        self._cleaner: threading.Thread | None = None
        self._state_file = Path(state_file) if state_file is not None else None
        self._state_dir = self._state_file.with_suffix("") if self._state_file is not None else None
        self._loaded = state_file is None

    def create(self, account: str, status: dict, details: dict, *, resume_config: dict | None = None) -> str:
        with self._lock:
            self._cleanup_locked()
            active_id = self._active_accounts.get(account)
            if active_id is not None:
                raise TaskAlreadyRunning(active_id)
            task_id = str(uuid4())
            state = deepcopy(status)
            state["status"] = "running"
            state.setdefault("start_time", self._wall_time())
            self._tasks[task_id] = _Task(
                account=account,
                status=state,
                details=deepcopy(details),
                resume_config=self._resume_settings(resume_config),
                logs=deque(maxlen=self._max_logs),
            )
            self._active_accounts[account] = task_id
            try:
                self._start_cleaner_locked()
                self._save_locked(task_id)
            except BaseException:
                del self._tasks[task_id]
                del self._active_accounts[account]
                raise
            return task_id

    @staticmethod
    def _resume_settings(config):
        if config is None:
            return None
        if not isinstance(config, dict):
            raise ValueError("恢复配置必须仅包含学习参数")
        if set(config) == LEGACY_RESUME_FIELDS:
            config = {key: value for key, value in config.items() if key != "ocr_config"}
        if set(config) != RESUME_FIELDS:
            if (set(config) != TOOL_RESUME_FIELDS
                    or not isinstance(config.get("task_type"), str)
                    or config["task_type"] not in {"catalog", "visits", "video_time", "reading_time", "download"}
                    or not isinstance(config.get("course_list"), list)
                    or not isinstance(config.get("tool_options"), dict)):
                raise ValueError("恢复配置必须仅包含学习参数")
        return deepcopy(config)

    def checkpoint(self, task_id: str) -> None:
        """Persist a utility's acknowledged progress before its next operation."""
        with self.edit(task_id):
            self._save_locked(task_id)

    def get_resume_config(self, task_id: str, account: str) -> dict | None:
        with self.edit(task_id) as task:
            if task.account != account:
                raise TaskNotFound(task_id)
            return deepcopy(task.resume_config) if task.status["status"] == "interrupted" else None

    def resume(self, task_id: str, account: str, status: dict, details: dict) -> dict | None:
        """Claim an interrupted task once, retaining its ID and account lock."""
        with self.edit(task_id) as task:
            if task.account != account:
                raise TaskNotFound(task_id)
            if task.status["status"] != "interrupted":
                return None
            if task.resume_config is None:
                raise TaskNotFound(task_id)
            previous = task.status, task.details, task.logs, task.sequence
            # A resumed run is a new run: never inherit an earlier stop signal.
            task.cancel.clear()
            task.status = deepcopy(status)
            task.status.update(status="running", start_time=self._wall_time())
            task.details = deepcopy(details)
            task.logs = deque(maxlen=self._max_logs)
            task.sequence = 0
            try:
                self._start_cleaner_locked()
                self._save_locked(task_id)
            except BaseException:
                task.status, task.details, task.logs, task.sequence = previous
                raise
            return deepcopy(task.resume_config)

    def interrupt(self, task_id: str, error: str) -> None:
        """A worker that could not start remains eligible for explicit retry."""
        with self.edit(task_id) as task:
            if task.status["status"] in TERMINAL_STATES:
                return
            if task.cancel.is_set():
                self._finish_locked(task_id, task, "cancelled")
                return
            task.status.update(status="interrupted", resume_error=error)
            try:
                self._save_locked(task_id)
            except OSError:
                task.status["recovery_error"] = "保存任务恢复信息失败，请检查数据目录后重试"

    def request_cancel(self, task_id: str, account: str) -> str:
        """Ask a task to stop, reporting what the request actually changed.

        An interrupted task has no worker to notice the signal, so it becomes
        terminal here; that also releases the account for a new task.
        """
        with self.edit(task_id) as task:
            if task.account != account:
                raise TaskNotFound(task_id)
            if task.status["status"] in TERMINAL_STATES:
                return "already_finished"
            task.cancel.set()
            if task.status["status"] == "interrupted":
                self._finish_locked(task_id, task, "cancelled")
                return "cancelled"
            task.status["cancel_requested"] = True
            try:
                self._save_locked(task_id)
            except OSError:
                # The stop itself is in memory and still takes effect.
                task.status["recovery_error"] = "已请求停止，但保存任务状态失败，请检查数据目录"
            return "stopping"

    def running_count(self) -> int:
        """Tasks with a live worker; interrupted ones have none to lose."""
        with self._lock:
            self._cleanup_locked()
            return sum(task.status.get("status") == "running" for task in self._tasks.values())

    def is_cancelled(self, task_id: str) -> bool:
        """Report the stop signal for workers; a vanished task must also stop."""
        with self._lock:
            self._cleanup_locked()
            task = self._tasks.get(task_id)
            return True if task is None else task.cancel.is_set()

    def _start_cleaner_locked(self):
        if self._cleanup_interval is None or self._cleaner is not None:
            return
        self._cleaner = threading.Thread(target=self._cleanup_loop, name="web-task-cleanup", daemon=True)
        try:
            self._cleaner.start()
        except BaseException:
            self._cleaner = None
            raise

    def _decode_record(self, task_id, record):
        """Validate one persisted record and apply restart/expiry semantics."""
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", task_id) or not isinstance(record, dict):
            raise ValueError("invalid task record")
        account, status, details = record["account"], record["status"], record["details"]
        if not isinstance(account, str) or not account.strip() or not isinstance(status, dict) or not isinstance(details, dict):
            raise ValueError("invalid task snapshot")
        state = status.get("status")
        if state not in TERMINAL_STATES | {"running", "interrupted"}:
            raise ValueError("invalid task state")
        stopped = state not in TERMINAL_STATES and status.get("cancel_requested") is True
        if stopped:
            # Persist the stop time once; restarting again must not extend TTL.
            state = "cancelled"
            status.update(status=state, end_time=self._wall_time())
            details["active_jobs"] = {}
        expires_at = None
        if state in TERMINAL_STATES:
            ended = status.get("end_time")
            if isinstance(ended, bool) or not isinstance(ended, (int, float)) or not math.isfinite(ended):
                raise ValueError("invalid task end time")
            remaining = min(self._ttl, self._ttl - (self._wall_time() - ended))
            if remaining <= 0:
                return None, False
            expires_at = self._clock() + remaining
        else:
            status["status"] = "interrupted"
            details["active_jobs"] = {}
        sequence, logs = record["sequence"], record["logs"]
        if type(sequence) is not int or sequence < 0 or not isinstance(logs, list):
            raise ValueError("invalid task logs")
        last_seq = 0
        for entry in logs:
            if (not isinstance(entry, dict) or type(entry.get("seq")) is not int
                    or not last_seq < entry["seq"] <= sequence or not isinstance(entry.get("message"), str)):
                raise ValueError("invalid log entry")
            last_seq = entry["seq"]
        resume_config = self._resume_settings(record["resume_config"])
        if resume_config is None:
            raise ValueError("missing recovery settings")
        return _Task(
            account=account, status=status, details=details, resume_config=resume_config,
            logs=deque(logs[-self._max_logs:], maxlen=self._max_logs),
            sequence=sequence, expires_at=expires_at,
        ), stopped

    def _read_record(self, path):
        with path.open("r", encoding="utf-8") as source:
            saved = json.load(source)
        if not isinstance(saved, dict) or type(saved.get("version")) is not int or saved["version"] != 1:
            raise ValueError("invalid task file version")
        return self._decode_record(path.stem, saved["task"])

    @staticmethod
    def _record(task):
        return {
            "account": task.account, "status": task.status, "details": task.details,
            "resume_config": task.resume_config, "sequence": task.sequence, "logs": list(task.logs),
        }

    def _migrate_legacy(self):
        try:
            with self._state_file.open("r", encoding="utf-8") as source:
                saved = json.load(source)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            raise OSError("读取保存的任务失败，请检查数据目录后重试") from exc
        try:
            if not isinstance(saved, dict) or saved.get("version") != 1 or not isinstance(saved.get("tasks"), dict):
                raise ValueError("invalid legacy task file")
            tasks, accounts = {}, set()
            # Validate the entire old snapshot before writing any migration files.
            for task_id, record in saved["tasks"].items():
                task, _ = self._decode_record(task_id, record)
                if task is None:
                    continue
                if task.expires_at is None:
                    if task.account in accounts:
                        raise ValueError("duplicate active account")
                    accounts.add(task.account)
                tasks[task_id] = task
        except (KeyError, TypeError, ValueError) as exc:
            raise OSError("保存的任务格式错误，原文件已保留") from exc
        backup = self._state_file.with_name(self._state_file.name + ".migrated")
        if backup.exists():
            raise OSError("任务迁移备份已存在，旧文件与备份均已保留，请检查数据目录")
        for task_id, task in tasks.items():
            target = self._state_dir / (task_id + ".json")
            if target.exists():
                # An interrupted migration may already have published this task.
                # Never replace it, especially its once-assigned cancellation time.
                try:
                    existing, _ = self._read_record(target)
                    if existing is not None and existing.account != task.account:
                        raise ValueError("migration account conflict")
                except (OSError, KeyError, TypeError, ValueError) as exc:
                    raise OSError("任务迁移目标冲突或损坏，原文件已保留") from exc
            else:
                self._write_record(task_id, task)
        # Windows rename refuses an existing destination; keep the original on failure.
        self._state_file.rename(backup)

    def _load_locked(self):
        if self._loaded:
            return
        self._migrate_legacy()
        try:
            paths = sorted(path for path in self._state_dir.iterdir() if path.suffix == ".json")
        except FileNotFoundError:
            paths = []
        except OSError as exc:
            raise OSError("读取任务目录失败，请检查数据目录后重试") from exc
        tasks, accounts, expirations, stopped_tasks = {}, {}, [], []
        for path in paths:
            task_id = path.stem
            try:
                task, stopped = self._read_record(path)
                if task is None:
                    self._delete_record(task_id)
                    continue
                if task.expires_at is None and task.account in accounts:
                    raise ValueError("duplicate active account")
            except (OSError, KeyError, TypeError, ValueError) as exc:
                # Leave the damaged file for diagnosis; other tasks remain usable.
                logger.error("任务文件 {} 无法加载，已保留并跳过：{}", path.name, type(exc).__name__)
                continue
            tasks[task_id] = task
            if task.expires_at is None:
                accounts[task.account] = task_id
            else:
                heappush(expirations, (task.expires_at, task_id))
            if stopped:
                stopped_tasks.append(task_id)
        self._tasks, self._active_accounts, self._expirations = tasks, accounts, expirations
        self._loaded = True
        for task_id in stopped_tasks:
            try:
                self._save_locked(task_id)
            except OSError:
                self._tasks[task_id].status["recovery_error"] = "任务已停止，但保存结果失败，请检查数据目录"

    def _save_locked(self, task_id):
        task = self._tasks[task_id]
        sanitize_errors(task.status)
        sanitize_errors(task.details)
        if self._state_dir is not None and task.resume_config is not None:
            self._write_record(task_id, task)

    def _write_record(self, task_id, task):
        target = self._state_dir / (task_id + ".json")
        temporary = None
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self._state_dir,
                prefix=target.name + ".", suffix=".tmp", delete=False,
            ) as output:
                temporary = output.name
                json.dump({"version": 1, "task": self._record(task)}, output, ensure_ascii=False, allow_nan=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        except (OSError, ValueError, TypeError) as exc:
            raise OSError("保存任务恢复信息失败，请检查数据目录后重试") from exc
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def _delete_record(self, task_id):
        if self._state_dir is None:
            return
        try:
            (self._state_dir / (task_id + ".json")).unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("清理过期任务文件 {} 失败：{}", task_id, type(exc).__name__)

    @contextmanager
    def edit(self, task_id: str):
        """Yield mutable state only while the store lock is held."""
        with self._lock:
            self._cleanup_locked()
            task = self._tasks.get(task_id)
            if task is None:
                raise TaskNotFound(task_id)
            try:
                yield task
            finally:
                sanitize_errors(task.status)
                sanitize_errors(task.details)

    def get_status(self, task_id: str) -> dict:
        with self.edit(task_id) as task:
            snapshot = deepcopy(task.status)
            sanitize_errors(snapshot)
            return snapshot

    def get_details(self, task_id: str) -> dict:
        with self.edit(task_id) as task:
            snapshot = deepcopy(task.details)
            sanitize_errors(snapshot)
            return snapshot

    def finish(self, task_id: str, status: str, *, error: str | None = None) -> None:
        if status not in TERMINAL_STATES:
            raise ValueError("Invalid terminal task status")
        with self.edit(task_id) as task:
            self._finish_locked(task_id, task, status, error=error)

    def _finish_locked(self, task_id: str, task: _Task, status: str, *, error: str | None = None) -> None:
        if task.expires_at is not None:
            return
        # Resolve the race with request_cancel under the same lock. Cleanup
        # and logger flushing may have happened after the worker chose a result.
        if task.cancel.is_set():
            status, error = "cancelled", None
            task.status.pop("error", None)
        task.status["status"] = status
        task.status["end_time"] = self._wall_time()
        if error is not None:
            task.status["error"] = redact(error)
        task.details["active_jobs"] = {}
        task.expires_at = self._clock() + self._ttl
        heappush(self._expirations, (task.expires_at, task_id))
        if self._active_accounts.get(task.account) == task_id:
            del self._active_accounts[task.account]
        try:
            self._save_locked(task_id)
        except OSError:
            # Completion must remain terminal even when storage becomes
            # unavailable. The UI exposes the durability failure.
            task.status["recovery_error"] = "任务已结束，但保存结果失败；重新打开时可能需要再次核对学习进度"

    def append_log(
        self, task_id: str, message: str, *, level: str = "info",
        timestamp: float | None = None, redacted: bool = False,
    ) -> int | None:
        # 调用方已做过 patch_record 全量脱敏（task_log_sink）时可跳过二次 redact。
        text = (message if redacted else redact(message)).strip()
        if not text:
            return None
        if len(text) > self._max_message_chars:
            text = text[: self._max_message_chars - 1] + "…"
        with self._lock:
            self._cleanup_locked()
            task = self._tasks.get(task_id)
            # Late logger messages must not revive expired or finished tasks.
            if task is None or task.expires_at is not None:
                return None
            task.sequence += 1
            task.logs.append({
                "seq": task.sequence,
                "message": text,
                "level": level,
                "timestamp": self._wall_time() if timestamp is None else timestamp,
            })
            return task.sequence

    def read_logs(self, task_id: str, after: int = 0) -> dict:
        if not isinstance(after, int) or isinstance(after, bool) or after < 0:
            raise ValueError("after must be a non-negative integer")
        with self.edit(task_id) as task:
            return {
                "data": [dict(entry) for entry in task.logs if entry["seq"] > after],
                "next_cursor": max(after, task.sequence),
                "truncated": bool(task.logs and after < task.logs[0]["seq"] - 1),
            }

    def cleanup(self) -> int:
        with self._lock:
            return self._cleanup_locked()

    def _cleanup_locked(self) -> int:
        self._load_locked()
        now = self._clock()
        removed = 0
        while self._expirations and self._expirations[0][0] <= now:
            _, task_id = heappop(self._expirations)
            self._delete_record(task_id)
            del self._tasks[task_id]
            removed += 1
        return removed

    def _cleanup_loop(self) -> None:
        while not self._stop.wait(self._cleanup_interval):
            self.cleanup()

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            cleaner = self._cleaner
        if cleaner is not None and cleaner is not threading.current_thread():
            cleaner.join()
