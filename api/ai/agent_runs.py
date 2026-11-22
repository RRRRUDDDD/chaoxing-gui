"""In-memory background run manager for live Agent progress & cancellation over RPC."""
from __future__ import annotations

from contextvars import copy_context
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from api.logger import logger
from api.privacy import redact
from api.ai.agent_tools import MAX_AGENT_STEPS, AgentRunCancelled

_RUN_TTL_S = 600.0
_MAX_RUNS = 60


class _AgentRunState:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.status = "running"
        self.phase = "thinking"
        self.step = 1
        self.max_steps = MAX_AGENT_STEPS
        self.active_tools: List[Dict[str, Any]] = []
        self.tool_calls: List[Dict[str, Any]] = []
        self.result: Optional[Dict[str, Any]] = None
        self.error: str = ""
        self.started_at = time.monotonic()
        self.finished_at: Optional[float] = None
        self.cancel_event = threading.Event()
        self.lock = threading.Lock()

    def on_event(self, event: Dict[str, Any]) -> None:
        etype = event.get("type")
        with self.lock:
            if etype == "step_start":
                self.step = int(event.get("step") or self.step)
                self.max_steps = int(event.get("maxSteps") or self.max_steps)
                self.phase = str(event.get("phase") or "thinking")
            elif etype == "tool_start":
                self.phase = "running_tools"
                tid = str(event.get("id") or "")
                if not any(t.get("id") == tid for t in self.active_tools):
                    self.active_tools.append({
                        "id": tid,
                        "name": str(event.get("name") or ""),
                        "label": str(event.get("label") or event.get("name") or ""),
                        "arguments": event.get("arguments") or {},
                    })
            elif etype == "tool_done":
                trace = event.get("trace")
                if isinstance(trace, dict):
                    tid = str(trace.get("id") or "")
                    self.active_tools = [t for t in self.active_tools if t.get("id") != tid]
                    self.tool_calls.append(trace)
                if not self.active_tools:
                    self.phase = "thinking"
            elif etype == "cancelled":
                self.active_tools = []
                self.status = "cancelled"
                self.phase = "cancelled"

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            end_ts = self.finished_at if self.finished_at is not None else time.monotonic()
            elapsed_ms = max(0, int((end_ts - self.started_at) * 1000))
            payload: Dict[str, Any] = {
                "runId": self.run_id,
                "status": self.status,
                "phase": self.phase,
                "step": self.step,
                "maxSteps": self.max_steps,
                "activeTools": list(self.active_tools),
                "toolCalls": list(self.tool_calls),
                "elapsedMs": elapsed_ms,
            }
            if self.result is not None:
                payload["result"] = self.result
            if self.error:
                payload["error"] = self.error
            return payload


class AgentRunManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: Dict[str, _AgentRunState] = {}

    def _prune_locked(self) -> None:
        now = time.monotonic()
        expired = [
            rid for rid, r in self._runs.items()
            if r.finished_at is not None and (now - r.finished_at) > _RUN_TTL_S
        ]
        for rid in expired:
            self._runs.pop(rid, None)
        if len(self._runs) > _MAX_RUNS:
            finished_sorted = sorted(
                ((rid, r.finished_at or 0.0) for rid, r in self._runs.items() if r.finished_at is not None),
                key=lambda item: item[1],
            )
            for rid, _ in finished_sorted[: len(self._runs) - _MAX_RUNS]:
                self._runs.pop(rid, None)

    def start_run(self, runner: Callable[..., Dict[str, Any]]) -> Dict[str, Any]:
        run_id = uuid.uuid4().hex[:16]
        state = _AgentRunState(run_id)
        with self._lock:
            self._prune_locked()
            self._runs[run_id] = state

        def _worker() -> None:
            try:
                result = runner(on_event=state.on_event, cancel_event=state.cancel_event)
                with state.lock:
                    if state.cancel_event.is_set():
                        state.status = "cancelled"
                        state.phase = "cancelled"
                    else:
                        state.result = result
                        state.status = "completed"
                        state.phase = "done"
                    state.active_tools = []
                    state.finished_at = time.monotonic()
            except AgentRunCancelled:
                with state.lock:
                    state.status = "cancelled"
                    state.phase = "cancelled"
                    state.active_tools = []
                    state.finished_at = time.monotonic()
            except Exception as exc:
                logger.error("Agent 异步执行异常 ({}): {}", run_id, redact(exc))
                with state.lock:
                    state.status = "error"
                    state.phase = "error"
                    state.error = redact(exc)
                    state.active_tools = []
                    state.finished_at = time.monotonic()

        ctx = copy_context()
        thread = threading.Thread(target=ctx.run, args=(_worker,), name=f"agent-run-{run_id[:8]}", daemon=True)
        thread.start()
        return {"runId": run_id, "status": "running"}

    def poll_run(self, run_id: str) -> Dict[str, Any]:
        rid = str(run_id or "").strip()
        with self._lock:
            state = self._runs.get(rid)
        if state is None:
            raise ValueError("Agent 运行任务不存在或已过期")
        return state.snapshot()

    def cancel_run(self, run_id: str) -> Dict[str, Any]:
        rid = str(run_id or "").strip()
        with self._lock:
            state = self._runs.get(rid)
        if state is None:
            return {"runId": rid, "status": "cancelled"}
        state.cancel_event.set()
        with state.lock:
            if state.status == "running":
                state.status = "cancelled"
                state.phase = "cancelled"
                state.active_tools = []
                state.finished_at = time.monotonic()
        return state.snapshot()


agent_run_manager = AgentRunManager()
