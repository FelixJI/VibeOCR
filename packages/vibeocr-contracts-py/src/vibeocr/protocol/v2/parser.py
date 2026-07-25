"""Strict parser/validator for the HTTP v2 protocol payloads.

This module is the single entry point for converting wire JSON back into the
DTO dataclasses. It intentionally rejects anything that would create *fake
compatibility*:

* unknown top-level required fields,
* unknown enum values (JobState/ItemState/JobKind/etc.),
* unknown error codes,
* illegal job/item state transitions when the caller asks for transition
  validation.

It does **not** try to be a general JSON validator — it only enforces the
shape we actually put on the wire.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .dtos import (
    SCHEMA_VERSION,
    TERMINAL_ITEM_STATES,
    TERMINAL_JOB_STATES,
    CancelMode,
    EvictionReason,
    ItemState,
    JobItem,
    JobKind,
    JobPriority,
    JobSnapshot,
    JobState,
    JobSummary,
    PipelineSpec,
    ResidencyEntry,
    ResidencyKind,
)
from .errors import ErrorCode, ErrorPayload, error_registry


class ContractError(ValueError):
    """Raised when a wire payload violates the v2 contract."""


class JobStateTransitionError(ContractError):
    """Raised when an observed transition is illegal per the state machine."""


# ---------------------------------------------------------------------------
# Allowed transitions
# ---------------------------------------------------------------------------

_JOB_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.ACCEPTED: frozenset({JobState.QUEUED, JobState.FAILED, JobState.CANCELLED}),
    JobState.QUEUED: frozenset(
        {JobState.RUNNING, JobState.CANCELLED, JobState.FAILED, JobState.CANCEL_REQUESTED}
    ),
    JobState.RUNNING: frozenset(
        {
            JobState.COMPLETED,
            JobState.COMPLETED_WITH_ERRORS,
            JobState.CANCEL_REQUESTED,
            JobState.FAILED,
        }
    ),
    JobState.CANCEL_REQUESTED: frozenset({JobState.CANCELLED, JobState.FAILED}),
    # Terminal states never transition out.
    JobState.COMPLETED: frozenset(),
    JobState.COMPLETED_WITH_ERRORS: frozenset(),
    JobState.CANCELLED: frozenset(),
    JobState.FAILED: frozenset(),
}


_ITEM_TRANSITIONS: dict[ItemState, frozenset[ItemState]] = {
    ItemState.QUEUED: frozenset({ItemState.RUNNING, ItemState.CANCELLED, ItemState.FAILED}),
    ItemState.RUNNING: frozenset({ItemState.SUCCEEDED, ItemState.FAILED, ItemState.CANCELLED}),
    ItemState.SUCCEEDED: frozenset(),
    ItemState.FAILED: frozenset(),
    ItemState.CANCELLED: frozenset(),
}


def assert_job_transition(current: JobState, target: JobState) -> None:
    if target == current:
        return
    allowed = _JOB_TRANSITIONS.get(current)
    if allowed is None or target not in allowed:
        raise JobStateTransitionError(
            f"illegal job state transition: {current.value} -> {target.value}"
        )


def assert_item_transition(current: ItemState, target: ItemState) -> None:
    if target == current:
        return
    allowed = _ITEM_TRANSITIONS.get(current)
    if allowed is None or target not in allowed:
        raise JobStateTransitionError(
            f"illegal item state transition: {current.value} -> {target.value}"
        )


def is_terminal_job(state: JobState) -> bool:
    return state in TERMINAL_JOB_STATES


def is_terminal_item(state: ItemState) -> bool:
    return state in TERMINAL_ITEM_STATES


# ---------------------------------------------------------------------------
# Enum parsing helpers
# ---------------------------------------------------------------------------


def _require_enum(enum_cls: type, raw: Any, label: str) -> Any:
    if not isinstance(raw, str):
        raise ContractError(f"{label} must be a string, got {type(raw).__name__}")
    try:
        return enum_cls(raw)
    except ValueError as exc:
        raise ContractError(f"unknown {label}: {raw!r}") from exc


# ---------------------------------------------------------------------------
# Payload parsing
# ---------------------------------------------------------------------------


def _require_fields(payload: dict[str, Any], fields: tuple[str, ...], label: str) -> None:
    missing = [f for f in fields if f not in payload or payload[f] is None]
    if missing:
        raise ContractError(f"{label} missing required field(s): {', '.join(missing)}")


def parse_job_snapshot(payload: dict[str, Any]) -> JobSnapshot:
    if not isinstance(payload, dict):
        raise ContractError("job snapshot must be a JSON object")
    _require_fields(
        payload,
        ("job_id", "kind", "priority", "state", "schema_version"),
        "job snapshot",
    )
    sv = payload["schema_version"]
    if sv != SCHEMA_VERSION:
        raise ContractError(
            f"schema_version mismatch: expected {SCHEMA_VERSION}, got {sv}"
        )
    items_raw = payload.get("items", [])
    if not isinstance(items_raw, list):
        raise ContractError("items must be a list")
    items = tuple(_parse_job_item(it) for it in items_raw)
    summary_raw = payload.get("summary", {})
    summary = _parse_summary(summary_raw)
    state = _require_enum(JobState, payload["state"], "job state")
    cancel_mode_raw = payload.get("cancel_mode")
    cancel_mode = (
        _require_enum(CancelMode, cancel_mode_raw, "cancel_mode")
        if cancel_mode_raw is not None
        else None
    )
    return JobSnapshot(
        job_id=payload["job_id"],
        kind=_require_enum(JobKind, payload["kind"], "job kind"),
        priority=_require_enum(JobPriority, payload["priority"], "job priority"),
        state=state,
        schema_version=int(sv),
        instance_id=payload.get("instance_id"),
        created_at=payload["created_at"],
        started_at=payload.get("started_at"),
        finished_at=payload.get("finished_at"),
        stage=payload.get("stage"),
        progress_current=int(payload.get("progress_current", 0)),
        progress_total=int(payload.get("progress_total", 0)),
        items=items,
        summary=summary,
        cancel_requested_at=payload.get("cancel_requested_at"),
        cancel_mode=cancel_mode,
        degraded=bool(payload.get("degraded", False)),
        event_sequence=int(payload.get("event_sequence", 0)),
        result_available=bool(payload.get("result_available", False)),
    )


def _parse_job_item(payload: Any) -> JobItem:
    if not isinstance(payload, dict):
        raise ContractError("job item must be a JSON object")
    _require_fields(payload, ("item_id", "display_name", "state"), "job item")
    return JobItem(
        item_id=payload["item_id"],
        display_name=payload["display_name"],
        state=_require_enum(ItemState, payload["state"], "item state"),
        attempt=int(payload.get("attempt", 0)),
        error=payload.get("error"),
    )


def _parse_summary(payload: Any) -> JobSummary:
    if payload is None:
        return JobSummary()
    if not isinstance(payload, dict):
        raise ContractError("summary must be a JSON object")
    return JobSummary(
        succeeded=int(payload.get("succeeded", 0)),
        failed=int(payload.get("failed", 0)),
        cancelled=int(payload.get("cancelled", 0)),
        total=int(payload.get("total", 0)),
    )


def parse_error_payload(payload: dict[str, Any]) -> ErrorPayload:
    if not isinstance(payload, dict):
        raise ContractError("error payload must be a JSON object")
    _require_fields(
        payload, ("schema_version", "code", "message", "category"), "error payload"
    )
    code_raw = payload["code"]
    try:
        code = code_raw if isinstance(code_raw, ErrorCode) else ErrorCode(code_raw)
    except ValueError as exc:
        raise ContractError(f"unknown error code: {code_raw!r}") from exc
    # Cross-check against the registry so a code only valid in one place is
    # rejected.
    if code not in error_registry:
        raise ContractError(f"error code not in registry: {code.value}")
    registry_entry = error_registry[code]
    category_raw = payload["category"]
    if category_raw != registry_entry.category.value:
        raise ContractError(
            f"category mismatch for {code.value}: payload={category_raw!r} "
            f"registry={registry_entry.category.value}"
        )
    return ErrorPayload(
        schema_version=int(payload["schema_version"]),
        instance_id=payload.get("instance_id"),
        code=code,
        message=payload["message"],
        category=registry_entry.category,
        retryable=registry_entry.retryable,
        detail=payload.get("detail") or {},
        job_id=payload.get("job_id"),
    )


def parse_residency_entry(payload: dict[str, Any]) -> ResidencyEntry:
    if not isinstance(payload, dict):
        raise ContractError("residency entry must be a JSON object")
    _require_fields(payload, ("pipeline", "kind"), "residency entry")
    kind = _require_enum(ResidencyKind, payload["kind"], "residency kind")
    reason = EvictionReason.NONE
    raw_reason = payload.get("eviction_reason")
    if raw_reason is not None:
        reason = _require_enum(EvictionReason, raw_reason, "eviction reason")
    return ResidencyEntry(
        pipeline=payload["pipeline"],
        kind=kind,
        active_leases=int(payload.get("active_leases", 0)),
        remaining_ttl_seconds=payload.get("remaining_ttl_seconds"),
        estimated_vram_mb=payload.get("estimated_vram_mb"),
        eviction_reason=reason,
    )


def parse_pipeline_spec(payload: dict[str, Any]) -> PipelineSpec:
    if not isinstance(payload, dict):
        raise ContractError("pipeline spec must be a JSON object")
    _require_fields(payload, ("name",), "pipeline spec")
    ttl = payload.get("ttl_seconds")
    if ttl is not None and (not isinstance(ttl, int) or ttl < 0):
        raise ContractError(f"ttl_seconds must be null or non-negative int, got {ttl!r}")
    return PipelineSpec(
        name=payload["name"],
        ttl_seconds=ttl,
        pinned=bool(payload.get("pinned", False)),
    )


@dataclass(frozen=True, slots=True)
class SchemaValidator:
    """Bundles the parse helpers so callers can inject a fake clock etc."""

    schema_version: int = SCHEMA_VERSION

    def snapshot(self, payload: dict[str, Any]) -> JobSnapshot:
        return parse_job_snapshot(payload)

    def error(self, payload: dict[str, Any]) -> ErrorPayload:
        return parse_error_payload(payload)


__all__ = [
    "ContractError",
    "JobStateTransitionError",
    "SchemaValidator",
    "assert_item_transition",
    "assert_job_transition",
    "is_terminal_item",
    "is_terminal_job",
    "parse_error_payload",
    "parse_job_snapshot",
    "parse_pipeline_spec",
    "parse_residency_entry",
]
