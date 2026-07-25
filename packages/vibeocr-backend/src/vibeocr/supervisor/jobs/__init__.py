"""Job domain: registry, events, staging, retention.

These modules are the observable state machine of the supervisor. They hold
no OCR/model/PDF knowledge — an :class:`~vibeocr.supervisor.module.Executor`
drives item transitions.
"""

from __future__ import annotations

from .registry import JobNotFoundError, JobRecord, JobRegistry
from .retention import RetentionPolicy
from .staging import InputStager, StagedInput, StagingQuotaError

__all__ = [
    "InputStager",
    "JobNotFoundError",
    "JobRecord",
    "JobRegistry",
    "RetentionPolicy",
    "StagedInput",
    "StagingQuotaError",
]
