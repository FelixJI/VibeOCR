"""DTOs for the HTTP v2 supervisor protocol.

All DTOs are ``@dataclass(frozen=True, slots=True)`` so they are hashable and
immutable; serialisation helpers convert to/from JSON-native payloads. The
serialised form is the wire contract — the dataclasses are a typed view.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

SCHEMA_VERSION = 2


def _utcnow_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _to_iso(value: str | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class JobState(StrEnum):
    """Terminal states are listed in :data:`TERMINAL`."""

    ACCEPTED = "accepted"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    FAILED = "failed"


TERMINAL_JOB_STATES = frozenset(
    {
        JobState.COMPLETED,
        JobState.COMPLETED_WITH_ERRORS,
        JobState.CANCELLED,
        JobState.FAILED,
    }
)


class ItemState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_ITEM_STATES = frozenset(
    {ItemState.SUCCEEDED, ItemState.FAILED, ItemState.CANCELLED}
)


class JobKind(StrEnum):
    RECOGNITION = "recognition"
    PDF_OCR = "pdf_ocr"
    MINERU_PARSE = "mineru_parse"
    MODEL_DOWNLOAD = "model_download"
    SETTINGS_INSTALL = "settings_install"


class JobPriority(StrEnum):
    INTERACTIVE = "interactive"
    BACKGROUND = "background"


class JobCommandKind(StrEnum):
    CANCEL = "cancel"
    RETRY = "retry"
    FORGET = "forget"


class CancelMode(StrEnum):
    """The actual strength of cancellation the supervisor will apply."""

    QUEUED_ONLY = "queued_only"
    COOPERATIVE = "cooperative"
    FORCED = "forced"


class ResidencyKind(StrEnum):
    SOFT_TTL = "soft_ttl"
    PINNED = "pinned"
    IDLE = "idle"
    EVICTED = "evicted"


class EvictionReason(StrEnum):
    NONE = "none"
    TTL_EXPIRED = "ttl_expired"
    VRAM_PRESSURE = "vram_pressure"
    EXPLICIT_RELEASE = "explicit_release"
    SUPERVISOR_SHUTDOWN = "supervisor_shutdown"


# ---------------------------------------------------------------------------
# Job DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JobItem:
    """A single input item inside a job (image, PDF page, MinerU file)."""

    item_id: str
    display_name: str
    state: ItemState
    attempt: int = 0
    error: str | None = None
    client_item_key: str | None = None
    ordinal: int = 0
    source_item_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value
        return d


@dataclass(frozen=True, slots=True)
class JobSummary:
    succeeded: int = 0
    failed: int = 0
    cancelled: int = 0
    total: int = 0

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class StageEvent:
    """One ordered event in a job's event log."""

    sequence: int
    stage: str
    item_id: str | None
    timestamp: str = field(default_factory=_utcnow_iso)
    detail: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "stage": self.stage,
            "item_id": self.item_id,
            "timestamp": self.timestamp,
            "detail": self.detail or {},
        }


@dataclass(frozen=True, slots=True)
class JobRef:
    """Returned immediately when a job is submitted."""

    job_id: str
    schema_version: int = SCHEMA_VERSION
    instance_id: str | None = None
    state: JobState = JobState.ACCEPTED
    items: tuple[JobItem, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "schema_version": self.schema_version,
            "instance_id": self.instance_id,
            "state": self.state.value,
            "items": [item.to_payload() for item in self.items],
        }


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    """Full status of a job at a point in time."""

    job_id: str
    kind: JobKind
    priority: JobPriority
    state: JobState
    schema_version: int = SCHEMA_VERSION
    instance_id: str | None = None
    created_at: str = field(default_factory=_utcnow_iso)
    started_at: str | None = None
    finished_at: str | None = None
    stage: str | None = None
    progress_current: int = 0
    progress_total: int = 0
    items: tuple[JobItem, ...] = ()
    summary: JobSummary = field(default_factory=JobSummary)
    cancel_requested_at: str | None = None
    cancel_mode: CancelMode | None = None
    degraded: bool = False
    event_sequence: int = 0
    result_available: bool = False
    request_id: str | None = None
    source_job_id: str | None = None
    pipeline: PipelineSelection | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "kind": self.kind.value,
            "priority": self.priority.value,
            "state": self.state.value,
            "schema_version": self.schema_version,
            "instance_id": self.instance_id,
            "created_at": self.created_at,
            "started_at": _to_iso(self.started_at),
            "finished_at": _to_iso(self.finished_at),
            "stage": self.stage,
            "progress_current": self.progress_current,
            "progress_total": self.progress_total,
            "items": [it.to_payload() for it in self.items],
            "summary": self.summary.to_payload(),
            "cancel_requested_at": self.cancel_requested_at,
            "cancel_mode": self.cancel_mode.value if self.cancel_mode else None,
            "degraded": self.degraded,
            "event_sequence": self.event_sequence,
            "result_available": self.result_available,
            "request_id": self.request_id,
            "source_job_id": self.source_job_id,
            "pipeline": self.pipeline.to_payload() if self.pipeline else None,
        }


# ---------------------------------------------------------------------------
# Submission / observation / command DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PipelineSelection:
    """Frozen user-semantic pipeline selection for one logical job."""

    pipeline_id: str
    options_version: int = 1
    options: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "pipeline_id": self.pipeline_id,
            "options_version": self.options_version,
            "options": self.options,
        }


@dataclass(frozen=True, slots=True)
class SubmitItem:
    """One logical input. ``source`` is a strict discriminated wire object."""

    client_item_key: str
    ordinal: int
    display_name: str
    source: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return {
            "client_item_key": self.client_item_key,
            "ordinal": self.ordinal,
            "display_name": self.display_name,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class SubmitRequest:
    """Versioned manifest submitted alongside multipart attachments."""

    request_id: str
    kind: JobKind
    priority: JobPriority
    pipeline: PipelineSelection
    items: tuple[SubmitItem, ...]
    schema_version: int = SCHEMA_VERSION
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "kind": self.kind.value,
            "priority": self.priority.value,
            "pipeline": self.pipeline.to_payload(),
            "items": [item.to_payload() for item in self.items],
            "parameters": self.parameters,
        }


@dataclass(frozen=True, slots=True)
class ItemOutcome:
    """Typed terminal outcome delta for one item."""

    item_id: str
    state: ItemState
    attempt: int
    payload_type: str | None = None
    payload: dict[str, Any] | None = None
    error_code: str | None = None
    error_detail: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "state": self.state.value,
            "attempt": self.attempt,
            "payload_type": self.payload_type,
            "payload": self.payload,
            "error_code": self.error_code,
            "error_detail": self.error_detail or {},
        }


@dataclass(frozen=True, slots=True)
class JobUpdate:
    """Atomic snapshot + event/outcome delta at one sequence watermark."""

    snapshot: JobSnapshot
    events: tuple[StageEvent, ...]
    outcomes: tuple[ItemOutcome, ...]
    through_sequence: int
    more: bool = False
    schema_version: int = SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "snapshot": self.snapshot.to_payload(),
            "events": [event.to_payload() for event in self.events],
            "outcomes": [outcome.to_payload() for outcome in self.outcomes],
            "through_sequence": self.through_sequence,
            "more": self.more,
        }


@dataclass(frozen=True, slots=True)
class JobCommand:
    command_id: str
    kind: JobCommandKind
    job_id: str
    item_ids: tuple[str, ...] = ()
    priority_override: JobPriority | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "kind": self.kind.value,
            "job_id": self.job_id,
            "item_ids": list(self.item_ids),
            "priority_override": (
                self.priority_override.value if self.priority_override else None
            ),
        }


# ---------------------------------------------------------------------------
# Result DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResultEntry:
    """One stable-ordered result item in a job result set."""

    item_id: str
    display_name: str
    payload: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "display_name": self.display_name,
            "payload": self.payload,
            "error_code": self.error_code,
        }


# ---------------------------------------------------------------------------
# Residency DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PipelineSpec:
    """Per-pipeline residency override."""

    name: str
    ttl_seconds: int | None = None
    pinned: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ttl_seconds": self.ttl_seconds,
            "pinned": self.pinned,
        }


@dataclass(frozen=True, slots=True)
class ResidencyEntry:
    """State of one loaded model/child process."""

    pipeline: str
    kind: ResidencyKind
    active_leases: int = 0
    remaining_ttl_seconds: int | None = None
    estimated_vram_mb: int | None = None
    eviction_reason: EvictionReason = EvictionReason.NONE

    def to_payload(self) -> dict[str, Any]:
        return {
            "pipeline": self.pipeline,
            "kind": self.kind.value,
            "active_leases": self.active_leases,
            "remaining_ttl_seconds": self.remaining_ttl_seconds,
            "estimated_vram_mb": self.estimated_vram_mb,
            "eviction_reason": self.eviction_reason.value,
        }


@dataclass(frozen=True, slots=True)
class ResidencyStatus:
    schema_version: int = SCHEMA_VERSION
    default_ttl_seconds: int = 300
    entries: tuple[ResidencyEntry, ...] = ()
    pipelines: tuple[PipelineSpec, ...] = ()
    vram_total_mb: int | None = None
    vram_used_mb: int | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "default_ttl_seconds": self.default_ttl_seconds,
            "entries": [e.to_payload() for e in self.entries],
            "pipelines": [p.to_payload() for p in self.pipelines],
            "vram_total_mb": self.vram_total_mb,
            "vram_used_mb": self.vram_used_mb,
        }


# ---------------------------------------------------------------------------
# Settings DTO
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SettingsSnapshot:
    """Backend settings snapshot/exchange. Transport-neutral."""

    schema_version: int = SCHEMA_VERSION
    default_ttl_seconds: int = 300
    pipelines: tuple[PipelineSpec, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "residency": {
                "default_ttl_seconds": self.default_ttl_seconds,
                "pipelines": [p.to_payload() for p in self.pipelines],
            },
            "extra": self.extra,
        }


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def new_job_id() -> str:
    """Generate a fresh job id (stringified UUID4)."""
    return str(uuid4())


@dataclass(frozen=True, slots=True)
class UnknownJobError:
    """Marker used by long-poll callers to distinguish 404 from transient gaps."""

    job_id: str

    def to_payload(self) -> dict[str, Any]:
        return {"job_id": self.job_id, "unknown": True}


__all__ = [
    "SCHEMA_VERSION",
    "TERMINAL_ITEM_STATES",
    "TERMINAL_JOB_STATES",
    "CancelMode",
    "EvictionReason",
    "ItemOutcome",
    "ItemState",
    "JobCommand",
    "JobCommandKind",
    "JobItem",
    "JobKind",
    "JobPriority",
    "JobRef",
    "JobSnapshot",
    "JobState",
    "JobSummary",
    "JobUpdate",
    "PipelineSelection",
    "PipelineSpec",
    "ResidencyEntry",
    "ResidencyKind",
    "ResidencyStatus",
    "ResultEntry",
    "SettingsSnapshot",
    "StageEvent",
    "SubmitItem",
    "SubmitRequest",
    "UnknownJobError",
    "new_job_id",
]
