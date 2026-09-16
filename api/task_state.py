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


TERMINAL_STATES = frozenset({"completed", "error", "partial", "cancelled"})
RESUME_FIELDS = frozenset({
    "course_list", "jobs", "speed", "retry_interval", "notopen_action",
    "tiku_config", "notification_config", "ocr_config",
})


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
                self._save_locked()
            except BaseException:
                del self._tasks[task_id]
                del self._active_accounts[account]
                raise
            return task_id

    @staticmethod
    def _resume_settings(config):
        if config is None:
            return None
        if not isinstance(config, dict) or set(config) != RESUME_FIELDS:
            raise ValueError("恢复配置必须仅包含学习参数")
        return deepcopy(config)

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
                self._save_locked()
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
                self._save_locked()
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
                self._save_locked()
            except OSError:
                # The stop itself is in memory and still takes effect.
                task.status["recovery_error"] = "已请求停止，但保存任务状态失败，请检查数据目录"
            return "stopping"

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

    def _load_locked(self):
        if self._loaded:
            return
        try:
            with self._state_file.open("r", encoding="utf-8") as source:
                saved = json.load(source)
        except FileNotFoundError:
            self._loaded = True
            return
        except (OSError, ValueError) as exc:
            raise OSError("读取保存的任务失败，请检查数据目录后重试") from exc
        try:
            if not isinstance(saved, dict) or saved.get("version") != 1 or not isinstance(saved.get("tasks"), dict):
                raise ValueError("invalid task file")
            tasks, accounts, expirations = {}, {}, []
            stopped_tasks = []
            for task_id, record in saved["tasks"].items():
                if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", task_id) or not isinstance(record, dict):
                    raise ValueError("invalid task record")
                account, status, details = record["account"], record["status"], record["details"]
                if not isinstance(account, str) or not account.strip() or not isinstance(status, dict) or not isinstance(details, dict):
                    raise ValueError("invalid task snapshot")
                state = status.get("status")
                if state not in TERMINAL_STATES | {"running", "interrupted"}:
                    raise ValueError("invalid task state")
                if state not in TERMINAL_STATES and status.get("cancel_requested") is True:
                    # No worker survives a process restart. An accepted stop
                    # must not become a resumable task or reserve its account.
                    state = "cancelled"
                    status.update(status=state, end_time=self._wall_time())
                    details["active_jobs"] = {}
                    stopped_tasks.append(task_id)
                expires_at = None
                if state in TERMINAL_STATES:
                    ended = status.get("end_time")
                    if isinstance(ended, bool) or not isinstance(ended, (int, float)) or not math.isfinite(ended):
                        raise ValueError("invalid task end time")
                    remaining = min(self._ttl, self._ttl - (self._wall_time() - ended))
                    if remaining <= 0:
                        continue
                    expires_at = self._clock() + remaining
                else:
                    if account in accounts:
                        raise ValueError("duplicate active account")
                    status["status"] = "interrupted"
                    details["active_jobs"] = {}
                    accounts[account] = task_id
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
                tasks[task_id] = _Task(
                    account=account, status=status, details=details, resume_config=resume_config,
                    logs=deque(logs[-self._max_logs:], maxlen=self._max_logs),
                    sequence=sequence, expires_at=expires_at,
                )
                if expires_at is not None:
                    heappush(expirations, (expires_at, task_id))
        except (KeyError, TypeError, ValueError) as exc:
            raise OSError("保存的任务格式错误，原文件已保留") from exc
        self._tasks, self._active_accounts, self._expirations = tasks, accounts, expirations
        self._loaded = True
        if stopped_tasks:
            try:
                # Persist the end time once so repeated restarts cannot extend
                # the lifetime of a cancelled task indefinitely.
                self._save_locked()
            except OSError:
                for task_id in stopped_tasks:
                    self._tasks[task_id].status["recovery_error"] = "任务已停止，但保存结果失败，请检查数据目录"

    def _save_locked(self):
        if self._state_file is None:
            return
        snapshot = {"version": 1, "tasks": {
            task_id: {
                "account": task.account, "status": task.status, "details": task.details,
                "resume_config": task.resume_config, "sequence": task.sequence, "logs": list(task.logs),
            }
            for task_id, task in self._tasks.items() if task.resume_config is not None
        }}
        temporary = None
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self._state_file.parent,
                prefix=self._state_file.name + ".", suffix=".tmp", delete=False,
            ) as output:
                temporary = output.name
                json.dump(snapshot, output, ensure_ascii=False, allow_nan=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self._state_file)
        except (OSError, ValueError, TypeError) as exc:
            raise OSError("保存任务恢复信息失败，请检查数据目录后重试") from exc
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    @contextmanager
    def edit(self, task_id: str):
        """Yield mutable state only while the store lock is held."""
        with self._lock:
            self._cleanup_locked()
            task = self._tasks.get(task_id)
            if task is None:
                raise TaskNotFound(task_id)
            yield task

    def get_status(self, task_id: str) -> dict:
        with self.edit(task_id) as task:
            return deepcopy(task.status)

    def get_details(self, task_id: str) -> dict:
        with self.edit(task_id) as task:
            return deepcopy(task.details)

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
            task.status["error"] = error
        task.details["active_jobs"] = {}
        task.expires_at = self._clock() + self._ttl
        heappush(self._expirations, (task.expires_at, task_id))
        if self._active_accounts.get(task.account) == task_id:
            del self._active_accounts[task.account]
        try:
            self._save_locked()
        except OSError:
            # Completion must remain terminal even when storage becomes
            # unavailable. The UI exposes the durability failure.
            task.status["recovery_error"] = "任务已结束，但保存结果失败；重新打开时可能需要再次核对学习进度"

    def append_log(
        self, task_id: str, message: str, *, level: str = "info", timestamp: float | None = None
    ) -> int | None:
        text = str(message).strip()
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
