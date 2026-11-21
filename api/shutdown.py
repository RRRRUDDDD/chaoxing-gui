"""Automatic system shutdown scheduling and cancellation after brushing tasks."""

from __future__ import annotations

import locale
import subprocess
import sys
import threading
import time
from typing import Callable

from api.logger import logger
from api.privacy import redact
from api.task_state import TaskNotFound


DEFAULT_SHUTDOWN_DELAY = 60
SHUTDOWN_OUTCOMES = frozenset({"completed", "partial"})
WINDOWS_NO_SHUTDOWN_IN_PROGRESS = 1116
_COMMAND_TIMEOUT = 5


def _decode_output(raw: bytes | str | None) -> str:
    if not raw:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    try:
        return raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        encoding = locale.getpreferredencoding(False) or "utf-8"
        return raw.decode(encoding, errors="replace").strip()


def _creation_flags(platform: str) -> int:
    if platform == "win32":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def _run_command(args: list[str], *, runner: Callable, platform: str):
    kwargs = {"capture_output": True, "timeout": _COMMAND_TIMEOUT}
    flags = _creation_flags(platform)
    if flags:
        kwargs["creationflags"] = flags
    return runner(args, **kwargs)


def _execute_posix_shutdown(runner: Callable, platform: str) -> None:
    try:
        if platform == "darwin":
            result = _run_command(
                ["osascript", "-e", 'tell application "System Events" to shut down'],
                runner=runner,
                platform=platform,
            )
            if result.returncode != 0:
                detail = _decode_output(result.stderr) or _decode_output(result.stdout)
                logger.warning("macOS 自动关机命令执行失败: {}", detail or f"退出码 {result.returncode}")
            return

        for cmd in (["shutdown", "-h", "now"], ["systemctl", "poweroff"]):
            try:
                result = _run_command(cmd, runner=runner, platform=platform)
            except FileNotFoundError:
                continue
            if result.returncode == 0:
                return
            detail = _decode_output(result.stderr) or _decode_output(result.stdout)
            logger.warning("自动关机命令 {} 执行失败: {}", cmd[0], detail or f"退出码 {result.returncode}")
    except Exception as exc:
        logger.warning("自动关机执行异常: {}", exc)


class ShutdownManager:
    """Coordinate delayed OS shutdown with task completion and user cancellation."""

    def __init__(
        self,
        *,
        delay: int = DEFAULT_SHUTDOWN_DELAY,
        wall_time: Callable[[], float] = time.time,
        runner: Callable = subprocess.run,
        platform: str | None = None,
    ):
        self._delay = max(1, int(delay))
        self._wall_time = wall_time
        self._runner = runner
        self._platform = platform
        self._lock = threading.RLock()
        self._active_task_id: str | None = None
        self._timer: threading.Timer | None = None

    def _current_platform(self) -> str:
        return self._platform if self._platform is not None else sys.platform

    def _schedule_os(self, task_id: str) -> None:
        platform = self._current_platform()
        if platform == "win32":
            comment = f"超星学习通：刷取任务已完成，系统将在 {self._delay} 秒后关机"
            result = _run_command(
                ["shutdown", "/s", "/t", str(self._delay), "/c", comment],
                runner=self._runner,
                platform=platform,
            )
            if result.returncode != 0:
                detail = _decode_output(result.stderr) or _decode_output(result.stdout)
                raise RuntimeError(detail or f"shutdown 命令返回退出码 {result.returncode}")
            self._active_task_id = task_id
            return

        if self._timer is not None:
            self._timer.cancel()
        timer = threading.Timer(self._delay, self._on_posix_timer, args=(task_id, platform))
        timer.daemon = True
        self._timer = timer
        self._active_task_id = task_id
        timer.start()

    def _on_posix_timer(self, task_id: str, platform: str) -> None:
        with self._lock:
            if self._active_task_id != task_id:
                return
            self._active_task_id = None
            self._timer = None
        _execute_posix_shutdown(self._runner, platform)

    def _cancel_os(self, task_id: str) -> None:
        platform = self._current_platform()
        if platform == "win32":
            result = _run_command(["shutdown", "/a"], runner=self._runner, platform=platform)
            if result.returncode not in (0, WINDOWS_NO_SHUTDOWN_IN_PROGRESS):
                detail = _decode_output(result.stderr) or _decode_output(result.stdout)
                raise RuntimeError(detail or f"shutdown /a 返回退出码 {result.returncode}")
            if self._active_task_id == task_id:
                self._active_task_id = None
            return

        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._active_task_id == task_id:
            self._active_task_id = None

    def finish_task(self, store, task_id: str, outcome: str, *, error: str | None = None) -> dict | None:
        """Publish terminal task state and schedule shutdown when armed and eligible."""
        with self._lock:
            should_schedule = False
            if outcome in SHUTDOWN_OUTCOMES:
                try:
                    with store.edit(task_id) as task:
                        already_cancelled = (
                            isinstance(task.status.get("shutdown"), dict)
                            and task.status["shutdown"].get("state") == "cancelled"
                        )
                        should_schedule = (
                            bool(task.status.get("shutdown_on_complete"))
                            and not task.cancel.is_set()
                            and not already_cancelled
                            and task.expires_at is None
                        )
                except TaskNotFound:
                    should_schedule = False

            if not should_schedule:
                store.finish(task_id, outcome, error=error)
                return None

            try:
                self._schedule_os(task_id)
                now = self._wall_time()
                info = {
                    "state": "scheduled",
                    "delay_seconds": self._delay,
                    "scheduled_at": now,
                    "shutdown_at": now + self._delay,
                }
                logger.info("任务 {} 已完成，已计划在 {} 秒后自动关机", task_id, self._delay)
            except Exception as exc:
                failure = redact(exc) or "系统拒绝了关机请求"
                info = {"state": "failed", "error": failure}
                logger.warning("任务 {} 计划自动关机失败: {}", task_id, failure)

            try:
                with store.edit(task_id) as task:
                    task.status["shutdown"] = info
                if info["state"] == "scheduled":
                    store.append_log(
                        task_id,
                        f"刷取任务已结束，系统将在 {self._delay} 秒后自动关机，可在进度页取消关机",
                        level="warning",
                    )
                else:
                    store.append_log(task_id, f"自动关机启动失败：{info['error']}", level="error")
            except TaskNotFound:
                if info["state"] == "scheduled":
                    try:
                        self._cancel_os(task_id)
                    except Exception:
                        pass
                raise

            store.finish(task_id, outcome, error=error)

            # Resolve the narrow race where a user stop arrived between the
            # pre-check and store.finish(): never leave a shutdown pending on
            # a cancelled task.
            if info["state"] == "scheduled":
                try:
                    final_status = store.get_status(task_id)
                except TaskNotFound:
                    final_status = None
                if final_status and final_status.get("status") == "cancelled":
                    try:
                        self._cancel_os(task_id)
                    except Exception as exc:
                        logger.warning("任务 {} 停止后取消已计划关机失败: {}", task_id, exc)
                    cancelled_info = {"state": "cancelled"}
                    try:
                        with store.edit(task_id) as task:
                            task.status["shutdown_on_complete"] = False
                            task.status["shutdown"] = cancelled_info
                    except TaskNotFound:
                        pass
                    return cancelled_info

            return info

    def cancel_for_task(self, store, task_id: str, account: str) -> dict:
        """Cancel a scheduled shutdown or disarm auto-shutdown on a running task."""
        with self._lock:
            with store.edit(task_id) as task:
                if task.account != account:
                    raise TaskNotFound(task_id)
                shutdown = task.status.get("shutdown")
                armed = bool(task.status.get("shutdown_on_complete"))

            if isinstance(shutdown, dict) and shutdown.get("state") == "scheduled":
                self._cancel_os(task_id)
                with store.edit(task_id) as task:
                    task.status["shutdown_on_complete"] = False
                    task.status["shutdown"] = {"state": "cancelled"}
                logger.info("已取消任务 {} 的自动关机计划", task_id)
                return {"state": "cancelled"}

            if armed:
                with store.edit(task_id) as task:
                    task.status["shutdown_on_complete"] = False
                    task.status["shutdown"] = {"state": "cancelled"}
                try:
                    store.append_log(task_id, "已取消当前任务的完成后自动关机", level="info")
                except TaskNotFound:
                    pass
                logger.info("已取消运行中任务 {} 的完成后自动关机", task_id)
                return {"state": "cancelled"}

            if isinstance(shutdown, dict) and shutdown.get("state") == "cancelled":
                return {"state": "cancelled"}

            return {"state": "none"}

    def reset(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._active_task_id = None


shutdown_manager = ShutdownManager()
