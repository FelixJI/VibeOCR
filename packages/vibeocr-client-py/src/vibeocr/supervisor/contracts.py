"""Shared client-side contract helpers.

Thin convenience re-exports so the client package has a single import point
without duplicating DTOs. The authoritative source remains
``vibeocr.protocol.v2``.
"""

from __future__ import annotations

from vibeocr.protocol.v2 import (
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
