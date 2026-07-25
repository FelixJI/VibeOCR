"""VibeOCR HTTP v2 protocol: stable DTOs, error codes and schema parser.

This package is the single source of truth for the supervisor ↔ frontend
contract. It is dependency-free (stdlib only) so it can be imported by both
``vibeocr-client-py`` and ``vibeocr-backend`` without creating a cycle.

Design rules (see specs/2026-07-24-inference-supervisor-adr.md):

* DTOs only carry JSON-native types or lists/dicts of JSON-native types plus
  ``enum.Enum``/``datetime``/``uuid.UUID`` helpers exposed as ISO strings.
* ``schema_version`` is always present on the wire (currently 2).
* The parser rejects unknown required fields, illegal state transitions and
  unknown error codes — fake compatibility with v1 is treated as a failure.
"""

from __future__ import annotations

from .dtos import (
    TERMINAL_ITEM_STATES,
    TERMINAL_JOB_STATES,
    CancelMode,
    EvictionReason,
    ItemState,
    JobItem,
    JobKind,
    JobPriority,
    JobRef,
    JobSnapshot,
    JobState,
    JobSummary,
    PipelineSpec,
    ResidencyEntry,
    ResidencyKind,
    ResidencyStatus,
    ResultEntry,
    SettingsSnapshot,
    StageEvent,
    UnknownJobError,
    new_job_id,
)
from .errors import (
    ErrorCategories,
    ErrorCode,
    ErrorPayload,
    error_registry,
    load_error_registry,
)
from .parser import (
    ContractError,
    JobStateTransitionError,
    SchemaValidator,
    assert_item_transition,
    assert_job_transition,
    is_terminal_item,
    is_terminal_job,
    parse_error_payload,
    parse_job_snapshot,
    parse_pipeline_spec,
    parse_residency_entry,
)

SCHEMA_VERSION = 2
"""Wire schema major version for the v2 protocol."""

__all__ = [
    "SCHEMA_VERSION",
    "TERMINAL_ITEM_STATES",
    "TERMINAL_JOB_STATES",
    "CancelMode",
    "ContractError",
    "ErrorCategories",
    "ErrorCode",
    "ErrorPayload",
    "EvictionReason",
    "ItemState",
    "JobItem",
    "JobKind",
    "JobPriority",
    "JobRef",
    "JobSnapshot",
    "JobState",
    "JobStateTransitionError",
    "JobSummary",
    "PipelineSpec",
    "ResidencyEntry",
    "ResidencyKind",
    "ResidencyStatus",
    "ResultEntry",
    "SchemaValidator",
    "SettingsSnapshot",
    "StageEvent",
    "UnknownJobError",
    "assert_item_transition",
    "assert_job_transition",
    "error_registry",
    "is_terminal_item",
    "is_terminal_job",
    "load_error_registry",
    "new_job_id",
    "parse_error_payload",
    "parse_job_snapshot",
    "parse_pipeline_spec",
    "parse_residency_entry",
]
