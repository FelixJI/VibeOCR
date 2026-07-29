"""Shared client-side contract helpers.

Thin convenience re-exports so the client package has a single import point
without duplicating DTOs. The authoritative source remains
``vibeocr.runtime_contracts``.
"""

from __future__ import annotations

from vibeocr.runtime_contracts import (
    CancelMode,
    JobKind,
    JobPriority,
    JobRef,
    JobSnapshot,
    ResidencyStatus,
    ResultEntry,
    SettingsSnapshot,
    StageEvent,
    parse_error_payload,
    parse_job_snapshot,
)

__all__ = [
    "CancelMode",
    "JobKind",
    "JobPriority",
    "JobRef",
    "JobSnapshot",
    "ResidencyStatus",
    "ResultEntry",
    "SettingsSnapshot",
    "StageEvent",
    "parse_error_payload",
    "parse_job_snapshot",
]
