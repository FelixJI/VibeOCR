"""Task registry and state machine for the WorkerHost (Task 1.5).

Tracks the lifecycle of every RPC task and enforces:

- the state machine ``queued -> running -> completed | failed | cancelled``;
- idempotent cancel (cancelling a terminal task reports its current state and
  ``accepted=False``);
- terminal tasks emit no further business events;
- deadline enforcement via ``is_expired``;
- retry policy: query-type tasks may be retried (``retryable=True``); mutations
  (pdf save/mutation, dependency install, backend switch, update) never are.

Pure-Python and thread-safe (a lock guards the registry map). The dispatcher
(Task 1.5 ``dispatcher.py``) drives transitions; handlers call ``complete`` /
``fail`` / ``record_event``.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class State(StrEnum):
    """Task lifecycle states. Transitions only forward to a terminal state."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"  # only used for unknown task_id lookups


_TERMINAL = frozenset({State.COMPLETED, State.FAILED, State.CANCELLED})


class TaskStateError(ValueError):
    """An illegal state transition or duplicate task was attempted."""


@dataclass
class CancelResult:
    """Outcome of a cancel request."""

    accepted: bool
    state: State


@dataclass
class TaskHandle:
    """Mutable view of a task's state. The registry owns the canonical copy."""

    task_id: str
    request_id: str
    method: str
    retryable: bool
    deadline_unix_ms: int
    state: State = State.QUEUED
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    # Buffered business events pending delivery to the client.
    _events: list[dict[str, Any]] = field(default_factory=list, repr=False)


class TaskRegistry:
    """Thread-safe registry of in-flight and recently terminal tasks.

    The registry retains terminal tasks so late results can be detected and
    discarded; callers may prune them via ``prune``.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, TaskHandle] = {}
        self._by_request: dict[str, str] = {}
        self._lock = threading.Lock()

    # -- create / lookup --------------------------------------------------

    def create(
        self,
        *,
        request_id: str,
        task_id: str,
        method: str,
        retryable: bool,
        deadline_unix_ms: int = 0,
    ) -> TaskHandle:
        with self._lock:
            if request_id in self._by_request:
                raise TaskStateError(f"duplicate request_id: {request_id}")
            if task_id in self._tasks:
                raise TaskStateError(f"duplicate task_id: {task_id}")
            handle = TaskHandle(
                task_id=task_id,
                request_id=request_id,
                method=method,
                retryable=retryable,
                deadline_unix_ms=deadline_unix_ms,
            )
            self._tasks[task_id] = handle
            self._by_request[request_id] = task_id
            return handle

    def get(self, task_id: str) -> TaskHandle | None:
        with self._lock:
            return self._tasks.get(task_id)

    def active_task_ids(self) -> tuple[str, ...]:
        """Return a stable snapshot of queued/running task ids."""
        with self._lock:
            return tuple(
                task_id
                for task_id, handle in self._tasks.items()
                if handle.state not in _TERMINAL
            )

    # -- transitions ------------------------------------------------------

    def mark_running(self, task_id: str) -> None:
        with self._lock:
            h = self._require(task_id)
            if h.state is not State.QUEUED:
                raise TaskStateError(
                    f"cannot mark_running from {h.state} (task {task_id})"
                )
            h.state = State.RUNNING

    def complete(self, task_id: str, *, result: dict[str, Any]) -> None:
        with self._lock:
            h = self._require(task_id)
            if h.state in _TERMINAL:
                # Late/duplicate result after any terminal state: discard
                # silently (distributed late-arrival semantics).
                return
            if h.state is not State.RUNNING:
                raise TaskStateError(
                    f"cannot complete from {h.state} (task {task_id})"
                )
            h.state = State.COMPLETED
            h.result = result

    def fail(
        self,
        task_id: str,
        *,
        error_code: str,
        message: str,
        detail: str | None = None,
    ) -> None:
        with self._lock:
            h = self._require(task_id)
            if h.state in _TERMINAL:
                # Late/duplicate failure after any terminal state: discard.
                return
            if h.state is not State.RUNNING:
                raise TaskStateError(
                    f"cannot fail from {h.state} (task {task_id})"
                )
            h.state = State.FAILED
            err: dict[str, Any] = {"code": error_code, "message": message}
            if detail is not None:
                err["detail"] = detail
            h.error = err

    def cancel(self, task_id: str) -> CancelResult:
        with self._lock:
            h = self._tasks.get(task_id)
            if h is None:
                return CancelResult(accepted=False, state=State.UNKNOWN)
            if h.state is State.CANCELLED:
                # Idempotent: a repeat cancel is accepted (already cancelled).
                return CancelResult(accepted=True, state=State.CANCELLED)
            if h.state in _TERMINAL:
                # Reached another terminal state first; cannot cancel.
                return CancelResult(accepted=False, state=h.state)
            # QUEUED or RUNNING: accept the cancel and move to CANCELLED.
            h.state = State.CANCELLED
            return CancelResult(accepted=True, state=State.CANCELLED)

    # -- events -----------------------------------------------------------

    def record_event(
        self, task_id: str, event: str, *, payload: dict[str, Any]
    ) -> None:
        with self._lock:
            h = self._tasks.get(task_id)
            if h is None or h.state in _TERMINAL:
                # No business events after terminal (or for unknown tasks).
                return
            h._events.append({"event": event, "payload": payload})

    def drain_events(self, task_id: str) -> list[dict[str, Any]]:
        with self._lock:
            h = self._tasks.get(task_id)
            if h is None:
                return []
            events = list(h._events)
            h._events.clear()
            return events

    # -- deadline / retry -------------------------------------------------

    def is_expired(self, task_id: str, *, now_unix_ms: int | None = None) -> bool:
        with self._lock:
            h = self._tasks.get(task_id)
            if h is None or h.deadline_unix_ms <= 0:
                return False
            now = now_unix_ms if now_unix_ms is not None else int(time.time() * 1000)
            return now > h.deadline_unix_ms

    def should_retry(self, task_id: str) -> bool:
        with self._lock:
            h = self._tasks.get(task_id)
            if h is None:
                return False
            if h.state is not State.FAILED:
                return False
            return bool(h.retryable)

    # -- maintenance ------------------------------------------------------

    def prune(self, *, keep_terminal_count: int = 256) -> int:
        """Drop oldest terminal tasks beyond a retention cap. Returns count removed."""
        removed = 0
        with self._lock:
            terminal = [
                (tid, h) for tid, h in self._tasks.items() if h.state in _TERMINAL
            ]
            if len(terminal) <= keep_terminal_count:
                return 0
            terminal.sort(key=lambda kv: kv[1].deadline_unix_ms)
            excess = len(terminal) - keep_terminal_count
            for tid, h in terminal[:excess]:
                self._tasks.pop(tid, None)
                self._by_request.pop(h.request_id, None)
                removed += 1
        return removed

    # -- internal ---------------------------------------------------------

    def _require(self, task_id: str) -> TaskHandle:
        h = self._tasks.get(task_id)
        if h is None:
            raise TaskStateError(f"unknown task_id: {task_id}")
        return h


__all__ = [
    "CancelResult",
    "State",
    "TaskHandle",
    "TaskRegistry",
    "TaskStateError",
]
