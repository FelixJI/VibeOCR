"""Transport-neutral typed errors for the supervisor client.

These are the UI-facing errors; they never carry Python tracebacks from the
backend. The UI maps them to user-visible behaviour.
"""

from __future__ import annotations

from vibeocr.protocol.v2 import ErrorCode, ErrorPayload


class InferenceClientError(Exception):
    """Base class for all supervisor client errors."""

    def __init__(self, code: ErrorCode, message: str, *, retryable: bool = False, detail: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.detail = detail or {}

    @classmethod
    def from_payload(cls, payload: ErrorPayload) -> InferenceClientError:
        mapping = {
            ErrorCode.UNAUTHORIZED: Unauthorized,
            ErrorCode.FORBIDDEN_LOOPBACK: Unauthorized,
            ErrorCode.QUOTA_EXCEEDED: QuotaExceeded,
            ErrorCode.JOB_NOT_FOUND: JobNotFound,
            ErrorCode.RESOURCE_NOT_FOUND: JobNotFound,
        }
        klass = mapping.get(payload.code, cls)
        return klass(
            payload.code,
            payload.message,
            retryable=payload.retryable,
            detail=payload.detail,
        )


class Unauthorized(InferenceClientError):
    """Session token rejected."""


class QuotaExceeded(InferenceClientError):
    """Request exceeded a body/count/staging quota."""


class JobNotFound(InferenceClientError):
    """Referenced job id is unknown or has been purged."""


__all__ = ["InferenceClientError", "JobNotFound", "QuotaExceeded", "Unauthorized"]
