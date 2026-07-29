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

from .contracts.pipelines import (
    OCRPipeline,
    get_pipeline_supported_options,
)
from .dtos import (
    SCHEMA_VERSION,
    TERMINAL_ITEM_STATES,
    TERMINAL_JOB_STATES,
    CancelMode,
    EvictionReason,
    ItemOutcome,
    ItemState,
    JobCommand,
    JobCommandKind,
    JobItem,
    JobKind,
    JobPriority,
    JobRef,
    JobSnapshot,
    JobState,
    JobSummary,
    JobUpdate,
    PipelineSelection,
    PipelineSpec,
    ResidencyEntry,
    ResidencyKind,
    StageEvent,
    SubmitItem,
    SubmitRequest,
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


def _reject_unknown_fields(
    payload: dict[str, Any], allowed: frozenset[str], label: str
) -> None:
    unknown = sorted(set(payload).difference(allowed))
    if unknown:
        raise ContractError(f"{label} has unknown field(s): {', '.join(unknown)}")


def parse_pipeline_selection(payload: dict[str, Any]) -> PipelineSelection:
    if not isinstance(payload, dict):
        raise ContractError("pipeline selection must be a JSON object")
    _reject_unknown_fields(
        payload,
        frozenset({"pipeline_id", "options_version", "options"}),
        "pipeline selection",
    )
    _require_fields(
        payload, ("pipeline_id", "options_version", "options"), "pipeline selection"
    )
    pipeline_id = payload["pipeline_id"]
    if not isinstance(pipeline_id, str):
        raise ContractError("pipeline_id must be a string")
    try:
        pipeline = OCRPipeline(pipeline_id)
    except ValueError as exc:
        raise ContractError(f"unknown pipeline_id: {pipeline_id!r}") from exc
    version = payload["options_version"]
    if version != 1:
        raise ContractError(f"unsupported options_version: {version!r}")
    options = payload["options"]
    if not isinstance(options, dict):
        raise ContractError("pipeline options must be a JSON object")
    allowed_options = set(get_pipeline_supported_options(pipeline))
    unknown_options = sorted(set(options).difference(allowed_options))
    if unknown_options:
        raise ContractError(
            f"unsupported option(s) for {pipeline.value}: {', '.join(unknown_options)}"
        )
    return PipelineSelection(
        pipeline_id=pipeline.value,
        options_version=version,
        options=dict(options),
    )


def _parse_submit_item(payload: Any) -> SubmitItem:
    if not isinstance(payload, dict):
        raise ContractError("submit item must be a JSON object")
    _reject_unknown_fields(
        payload,
        frozenset({"client_item_key", "ordinal", "display_name", "source"}),
        "submit item",
    )
    _require_fields(
        payload,
        ("client_item_key", "ordinal", "display_name", "source"),
        "submit item",
    )
    source = payload["source"]
    if not isinstance(source, dict):
        raise ContractError("submit item source must be a JSON object")
    source_type = source.get("type")
    if source_type == "upload.v1":
        _reject_unknown_fields(
            source, frozenset({"type", "attachment"}), "upload source"
        )
        _require_fields(source, ("type", "attachment"), "upload source")
    elif source_type == "pdf_page.v1":
        _reject_unknown_fields(
            source,
            frozenset({"type", "session_id", "session_revision", "page_index"}),
            "pdf page source",
        )
        _require_fields(
            source,
            ("type", "session_id", "session_revision", "page_index"),
            "pdf page source",
        )
        if not isinstance(source["page_index"], int) or source["page_index"] < 0:
            raise ContractError("pdf page_index must be a non-negative integer")
    else:
        raise ContractError(f"unknown submit source type: {source_type!r}")
    ordinal = payload["ordinal"]
    if not isinstance(ordinal, int) or ordinal < 0:
        raise ContractError("submit item ordinal must be a non-negative integer")
    client_key = payload["client_item_key"]
    if not isinstance(client_key, str) or not client_key:
        raise ContractError("client_item_key must be a non-empty string")
    display_name = payload["display_name"]
    if not isinstance(display_name, str):
        raise ContractError("display_name must be a string")
    return SubmitItem(
        client_item_key=client_key,
        ordinal=ordinal,
        display_name=display_name,
        source=dict(source),
    )


def parse_submit_request(payload: dict[str, Any]) -> SubmitRequest:
    if not isinstance(payload, dict):
        raise ContractError("submit request must be a JSON object")
    _reject_unknown_fields(
        payload,
        frozenset(
            {
                "schema_version",
                "request_id",
                "kind",
                "priority",
                "pipeline",
                "items",
                "parameters",
            }
        ),
        "submit request",
    )
    _require_fields(
        payload,
        ("schema_version", "request_id", "kind", "priority", "pipeline", "items"),
        "submit request",
    )
    if payload["schema_version"] != SCHEMA_VERSION:
        raise ContractError(
            f"schema_version mismatch: expected {SCHEMA_VERSION}, "
            f"got {payload['schema_version']}"
        )
    request_id = payload["request_id"]
    if not isinstance(request_id, str) or not request_id:
        raise ContractError("request_id must be a non-empty string")
    kind = _require_enum(JobKind, payload["kind"], "job kind")
    if kind not in {JobKind.RECOGNITION, JobKind.MINERU_PARSE, JobKind.PDF_OCR}:
        raise ContractError(f"job kind is not submittable: {kind.value}")
    priority = _require_enum(JobPriority, payload["priority"], "job priority")
    pipeline = parse_pipeline_selection(payload["pipeline"])
    if kind is JobKind.MINERU_PARSE and pipeline.pipeline_id != OCRPipeline.DOCUMENT_PARSING.value:
        raise ContractError("mineru_parse requires the MinerU pipeline")
    if kind is JobKind.RECOGNITION and pipeline.pipeline_id == OCRPipeline.DOCUMENT_PARSING.value:
        raise ContractError("MinerU requires kind=mineru_parse")
    items_raw = payload["items"]
    if not isinstance(items_raw, list) or not items_raw:
        raise ContractError("submit request items must be a non-empty list")
    items = tuple(_parse_submit_item(item) for item in items_raw)
    keys = [item.client_item_key for item in items]
    if len(keys) != len(set(keys)):
        raise ContractError("client_item_key must be unique within a job")
    ordinals = [item.ordinal for item in items]
    if sorted(ordinals) != list(range(len(items))):
        raise ContractError("submit item ordinals must be unique and contiguous from zero")
    parameters = payload.get("parameters", {})
    if not isinstance(parameters, dict):
        raise ContractError("submit request parameters must be a JSON object")
    return SubmitRequest(
        request_id=request_id,
        kind=kind,
        priority=priority,
        pipeline=pipeline,
        items=items,
        parameters=dict(parameters),
    )


def parse_job_ref(payload: dict[str, Any]) -> JobRef:
    if not isinstance(payload, dict):
        raise ContractError("job ref must be a JSON object")
    _require_fields(payload, ("job_id", "schema_version", "state"), "job ref")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise ContractError(
            f"schema_version mismatch: expected {SCHEMA_VERSION}, "
            f"got {payload['schema_version']}"
        )
    items_raw = payload.get("items", [])
    if not isinstance(items_raw, list):
        raise ContractError("job ref items must be a list")
    return JobRef(
        job_id=payload["job_id"],
        schema_version=payload["schema_version"],
        instance_id=payload.get("instance_id"),
        state=_require_enum(JobState, payload["state"], "job state"),
        items=tuple(_parse_job_item(item) for item in items_raw),
    )


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
        request_id=payload.get("request_id"),
        source_job_id=payload.get("source_job_id"),
        pipeline=(
            parse_pipeline_selection(payload["pipeline"])
            if payload.get("pipeline") is not None
            else None
        ),
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
        client_item_key=payload.get("client_item_key"),
        ordinal=int(payload.get("ordinal", 0)),
        source_item_id=payload.get("source_item_id"),
    )


def _parse_stage_event(payload: Any) -> StageEvent:
    if not isinstance(payload, dict):
        raise ContractError("stage event must be a JSON object")
    _require_fields(payload, ("sequence", "stage", "timestamp"), "stage event")
    detail = payload.get("detail", {})
    if not isinstance(detail, dict):
        raise ContractError("stage event detail must be a JSON object")
    return StageEvent(
        sequence=int(payload["sequence"]),
        stage=payload["stage"],
        item_id=payload.get("item_id"),
        timestamp=payload["timestamp"],
        detail=detail,
    )


def _parse_item_outcome(payload: Any) -> ItemOutcome:
    if not isinstance(payload, dict):
        raise ContractError("item outcome must be a JSON object")
    _require_fields(payload, ("item_id", "state", "attempt"), "item outcome")
    state = _require_enum(ItemState, payload["state"], "item state")
    if state not in TERMINAL_ITEM_STATES:
        raise ContractError("item outcome state must be terminal")
    result = payload.get("payload")
    error_code = payload.get("error_code")
    payload_type = payload.get("payload_type")
    if state is ItemState.SUCCEEDED:
        if not isinstance(result, dict) or not payload_type or error_code is not None:
            raise ContractError(
                "succeeded item outcome requires payload_type/payload and no error"
            )
    elif result is not None or payload_type is not None or not error_code:
        raise ContractError(
            "failed/cancelled item outcome requires error and no result payload"
        )
    error_detail = payload.get("error_detail", {})
    if not isinstance(error_detail, dict):
        raise ContractError("item outcome error_detail must be a JSON object")
    return ItemOutcome(
        item_id=payload["item_id"],
        state=state,
        attempt=int(payload["attempt"]),
        payload_type=payload_type,
        payload=result,
        error_code=error_code,
        error_detail=error_detail,
    )


def parse_job_update(payload: dict[str, Any]) -> JobUpdate:
    if not isinstance(payload, dict):
        raise ContractError("job update must be a JSON object")
    _require_fields(
        payload,
        (
            "schema_version",
            "snapshot",
            "events",
            "outcomes",
            "through_sequence",
            "more",
        ),
        "job update",
    )
    if payload["schema_version"] != SCHEMA_VERSION:
        raise ContractError(
            f"schema_version mismatch: expected {SCHEMA_VERSION}, "
            f"got {payload['schema_version']}"
        )
    events_raw = payload["events"]
    outcomes_raw = payload["outcomes"]
    if not isinstance(events_raw, list) or not isinstance(outcomes_raw, list):
        raise ContractError("job update events/outcomes must be lists")
    events = tuple(_parse_stage_event(event) for event in events_raw)
    outcomes = tuple(_parse_item_outcome(outcome) for outcome in outcomes_raw)
    through = int(payload["through_sequence"])
    if through < 0 or any(event.sequence > through for event in events):
        raise ContractError("job update through_sequence is inconsistent")
    return JobUpdate(
        snapshot=parse_job_snapshot(payload["snapshot"]),
        events=events,
        outcomes=outcomes,
        through_sequence=through,
        more=bool(payload["more"]),
    )


def parse_job_command(payload: dict[str, Any]) -> JobCommand:
    if not isinstance(payload, dict):
        raise ContractError("job command must be a JSON object")
    _reject_unknown_fields(
        payload,
        frozenset(
            {"command_id", "kind", "job_id", "item_ids", "priority_override"}
        ),
        "job command",
    )
    _require_fields(payload, ("command_id", "kind", "job_id"), "job command")
    item_ids = payload.get("item_ids", [])
    if not isinstance(item_ids, list) or any(not isinstance(item, str) for item in item_ids):
        raise ContractError("job command item_ids must be a list of strings")
    priority_raw = payload.get("priority_override")
    return JobCommand(
        command_id=payload["command_id"],
        kind=_require_enum(JobCommandKind, payload["kind"], "job command kind"),
        job_id=payload["job_id"],
        item_ids=tuple(item_ids),
        priority_override=(
            _require_enum(JobPriority, priority_raw, "job priority")
            if priority_raw is not None
            else None
        ),
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
    # rejected. ``error_registry`` is loaded from errors.json and is total over
    # the ErrorCode enum (every member is registered), so a value that survived
    # the ErrorCode() construction above is always present here; the guard is
    # retained as a defensive invariant for future registry edits.
    if code not in error_registry:  # pragma: no cover - registry is total over ErrorCode
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
    "parse_job_command",
    "parse_job_ref",
    "parse_job_snapshot",
    "parse_job_update",
    "parse_pipeline_selection",
    "parse_pipeline_spec",
    "parse_residency_entry",
    "parse_submit_request",
]
