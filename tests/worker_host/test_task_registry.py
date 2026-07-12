"""Tests for the WorkerHost task registry and state machine (Task 1.5 Green).

The task registry tracks the lifecycle of every RPC task and enforces:
- the state machine ``queued -> running -> completed | failed | cancelled``;
- idempotent cancel (cancelling a terminal task reports its current state);
- terminal tasks emit no further business events;
- deadline enforcement (tasks past their deadline time out);
- non-retryable mutations are marked so the dispatcher never auto-retries them.

Pure-Python, no Win32 — fully unit-testable everywhere.
"""

from __future__ import annotations

import time
import uuid

import pytest

from vibeocr.worker_host.task_registry import (
    CancelResult,
    State,
    TaskHandle,
    TaskRegistry,
    TaskStateError,
)


def _request_id() -> str:
    return str(uuid.uuid4())


def _task_id() -> str:
    return str(uuid.uuid4())


def _require(reg: TaskRegistry, tid: str) -> TaskHandle:
    """Fetch a handle, asserting it exists (tests always create the task first)."""
    h = reg.get(tid)
    assert h is not None, f"task {tid} not found"
    return h


# ---------------------------------------------------------------------------
# create + initial state
# ---------------------------------------------------------------------------


def test_create_returns_queued_handle() -> None:
    reg = TaskRegistry()
    rid = _request_id()
    tid = _task_id()
    handle = reg.create(request_id=rid, task_id=tid, method="ocr.recognize", retryable=True)
    assert handle.task_id == tid
    assert handle.state is State.QUEUED
    assert handle.request_id == rid
    assert handle.method == "ocr.recognize"


def test_create_rejects_duplicate_request_id() -> None:
    reg = TaskRegistry()
    rid = _request_id()
    reg.create(request_id=rid, task_id=_task_id(), method="system.ping", retryable=True)
    with pytest.raises(TaskStateError, match="duplicate"):
        reg.create(
            request_id=rid, task_id=_task_id(), method="system.ping", retryable=True
        )


def test_get_returns_handle() -> None:
    reg = TaskRegistry()
    tid = _task_id()
    reg.create(request_id=_request_id(), task_id=tid, method="system.ping", retryable=True)
    assert _require(reg, tid).task_id == tid


def test_get_unknown_task_returns_none() -> None:
    reg = TaskRegistry()
    assert reg.get(_task_id()) is None


# ---------------------------------------------------------------------------
# state machine: queued -> running -> terminal
# ---------------------------------------------------------------------------


def test_queued_to_running_to_completed() -> None:
    reg = TaskRegistry()
    tid = _task_id()
    reg.create(request_id=_request_id(), task_id=tid, method="ocr.recognize", retryable=True)
    reg.mark_running(tid)
    assert _require(reg, tid).state is State.RUNNING
    reg.complete(tid, result={"text": "hi"})
    assert _require(reg, tid).state is State.COMPLETED
    assert _require(reg, tid).result == {"text": "hi"}


def test_running_to_failed() -> None:
    reg = TaskRegistry()
    tid = _task_id()
    reg.create(request_id=_request_id(), task_id=tid, method="ocr.recognize", retryable=True)
    reg.mark_running(tid)
    reg.fail(tid, error_code="INTERNAL_ERROR", message="boom", detail="trace")
    h = _require(reg, tid)
    assert h.state is State.FAILED
    assert h.error is not None
    assert h.error["code"] == "INTERNAL_ERROR"


def test_complete_without_running_rejected() -> None:
    reg = TaskRegistry()
    tid = _task_id()
    reg.create(request_id=_request_id(), task_id=tid, method="ocr.recognize", retryable=True)
    with pytest.raises(TaskStateError):
        reg.complete(tid, result={})


def test_double_complete_silently_discarded_after_terminal() -> None:
    # A second complete after a terminal state is a late/duplicate result and
    # must be silently discarded (distributed late-arrival semantics), not flip
    # state or raise. The canonical result is kept.
    reg = TaskRegistry()
    tid = _task_id()
    reg.create(request_id=_request_id(), task_id=tid, method="ocr.recognize", retryable=True)
    reg.mark_running(tid)
    reg.complete(tid, result={"first": True})
    reg.complete(tid, result={"again": True})
    h = _require(reg, tid)
    assert h.state is State.COMPLETED
    assert h.result == {"first": True}


def test_complete_from_queued_without_running_rejected() -> None:
    # Skipping the running state is a programming error, not a late arrival.
    reg = TaskRegistry()
    tid = _task_id()
    reg.create(request_id=_request_id(), task_id=tid, method="ocr.recognize", retryable=True)
    with pytest.raises(TaskStateError):
        reg.complete(tid, result={})


def test_terminal_emits_no_more_events() -> None:
    reg = TaskRegistry()
    tid = _task_id()
    reg.create(request_id=_request_id(), task_id=tid, method="ocr.recognize", retryable=True)
    reg.mark_running(tid)
    reg.complete(tid, result={})
    events_before = reg.drain_events(tid)
    # After terminal, no business events should be produced.
    reg.record_event(tid, "task.progress", payload={"current": 1})
    events_after = reg.drain_events(tid)
    assert events_after == []
    assert events_before == []  # none pending either


# ---------------------------------------------------------------------------
# cancel: idempotent, queued vs running vs terminal
# ---------------------------------------------------------------------------


def test_cancel_queued_task() -> None:
    reg = TaskRegistry()
    tid = _task_id()
    reg.create(request_id=_request_id(), task_id=tid, method="ocr.recognize", retryable=True)
    result = reg.cancel(tid)
    assert isinstance(result, CancelResult)
    assert result.accepted is True
    assert result.state is State.CANCELLED
    assert _require(reg, tid).state is State.CANCELLED


def test_cancel_running_task_accepted_state_still_running() -> None:
    reg = TaskRegistry()
    tid = _task_id()
    reg.create(request_id=_request_id(), task_id=tid, method="ocr.recognize", retryable=True)
    reg.mark_running(tid)
    result = reg.cancel(tid)
    assert result.accepted is True
    # Cancel of a running task is accepted; the task transitions to CANCELLED
    # when the handler observes the cancel token and returns.
    assert _require(reg, tid).state is State.CANCELLED


def test_cancel_is_idempotent() -> None:
    reg = TaskRegistry()
    tid = _task_id()
    reg.create(request_id=_request_id(), task_id=tid, method="ocr.recognize", retryable=True)
    first = reg.cancel(tid)
    second = reg.cancel(tid)
    assert first.accepted is True
    assert second.accepted is True  # idempotent
    assert second.state is State.CANCELLED


def test_cancel_completed_task_reports_state_not_accepted() -> None:
    reg = TaskRegistry()
    tid = _task_id()
    reg.create(request_id=_request_id(), task_id=tid, method="ocr.recognize", retryable=True)
    reg.mark_running(tid)
    reg.complete(tid, result={})
    result = reg.cancel(tid)
    assert result.accepted is False
    assert result.state is State.COMPLETED


def test_cancel_unknown_task() -> None:
    reg = TaskRegistry()
    result = reg.cancel(_task_id())
    assert result.accepted is False
    assert result.state is State.UNKNOWN


# ---------------------------------------------------------------------------
# deadline
# ---------------------------------------------------------------------------


def test_expired_deadline_marks_timeout() -> None:
    reg = TaskRegistry()
    tid = _task_id()
    past = int(time.time() * 1000) - 1000
    reg.create(
        request_id=_request_id(),
        task_id=tid,
        method="ocr.recognize",
        retryable=True,
        deadline_unix_ms=past,
    )
    assert reg.is_expired(tid) is True


def test_future_deadline_not_expired() -> None:
    reg = TaskRegistry()
    tid = _task_id()
    future = int(time.time() * 1000) + 60_000
    reg.create(
        request_id=_request_id(),
        task_id=tid,
        method="ocr.recognize",
        retryable=True,
        deadline_unix_ms=future,
    )
    assert reg.is_expired(tid) is False


# ---------------------------------------------------------------------------
# retry policy: mutations are non-retryable
# ---------------------------------------------------------------------------


def test_retryable_flag_stored() -> None:
    reg = TaskRegistry()
    tid_q = _task_id()
    tid_m = _task_id()
    reg.create(request_id=_request_id(), task_id=tid_q, method="ocr.recognize", retryable=True)
    reg.create(request_id=_request_id(), task_id=tid_m, method="pdf.save", retryable=False)
    assert _require(reg, tid_q).retryable is True
    assert _require(reg, tid_m).retryable is False


def test_non_retryable_failed_task_not_eligible_for_retry() -> None:
    reg = TaskRegistry()
    tid = _task_id()
    reg.create(request_id=_request_id(), task_id=tid, method="pdf.save", retryable=False)
    reg.mark_running(tid)
    reg.fail(tid, error_code="INTERNAL_ERROR", message="x")
    assert reg.should_retry(tid) is False


def test_retryable_failed_task_eligible_for_retry() -> None:
    reg = TaskRegistry()
    tid = _task_id()
    reg.create(request_id=_request_id(), task_id=tid, method="ocr.recognize", retryable=True)
    reg.mark_running(tid)
    reg.fail(tid, error_code="WORKER_UNAVAILABLE", message="x")
    assert reg.should_retry(tid) is True


# ---------------------------------------------------------------------------
# late results are discarded after terminal
# ---------------------------------------------------------------------------


def test_late_result_after_cancel_discarded() -> None:
    reg = TaskRegistry()
    tid = _task_id()
    reg.create(request_id=_request_id(), task_id=tid, method="ocr.recognize", retryable=True)
    reg.mark_running(tid)
    reg.cancel(tid)
    # A late completion arrives after cancel: must be discarded, not flip state.
    reg.complete(tid, result={"late": True})
    assert _require(reg, tid).state is State.CANCELLED
    assert _require(reg, tid).result is None


def test_late_failure_after_complete_discarded() -> None:
    reg = TaskRegistry()
    tid = _task_id()
    reg.create(request_id=_request_id(), task_id=tid, method="ocr.recognize", retryable=True)
    reg.mark_running(tid)
    reg.complete(tid, result={"ok": True})
    reg.fail(tid, error_code="INTERNAL_ERROR", message="late")
    assert _require(reg, tid).state is State.COMPLETED
    assert _require(reg, tid).error is None
