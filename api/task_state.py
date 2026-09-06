"""Thread-safe, bounded storage for Web tasks and their polling cursors."""

from collections import deque
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from heapq import heappop, heappush
import math
import threading
import time
from typing import Callable
from uuid import uuid4


TERMINAL_STATES = frozenset({"completed", "error", "partial"})


class TaskAlreadyRunning(Exception):
    def __init__(self, task_id: str):
        super().__init__("该账号已有正在运行的任务")
        self.task_id = task_id


@dataclass
class _Task:
    account: str
    status: dict
    details: dict
    logs: deque = field(default_factory=deque)
    sequence: int = 0
    expires_at: float | None = None


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

    def create(self, account: str, status: dict, details: dict) -> str:
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
                logs=deque(maxlen=self._max_logs),
            )
            self._active_accounts[account] = task_id
            if self._cleanup_interval is not None and self._cleaner is None:
                self._cleaner = threading.Thread(
                    target=self._cleanup_loop, name="web-task-cleanup", daemon=True
                )
                try:
                    self._cleaner.start()
                except BaseException:
                    self._cleaner = None
                    del self._tasks[task_id]
                    del self._active_accounts[account]
                    raise
            return task_id

    @contextmanager
    def edit(self, task_id: str):
        """Yield mutable state only while the store lock is held."""
        with self._lock:
            self._cleanup_locked()
            yield self._tasks[task_id]

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
            if task.expires_at is not None:
                return
            task.status["status"] = status
            task.status["end_time"] = self._wall_time()
            if error is not None:
                task.status["error"] = error
            task.details["active_jobs"] = {}
            task.expires_at = self._clock() + self._ttl
            heappush(self._expirations, (task.expires_at, task_id))
            if self._active_accounts.get(task.account) == task_id:
                del self._active_accounts[task.account]

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
