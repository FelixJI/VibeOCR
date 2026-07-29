"""Transport-neutral typed errors for the supervisor client.

These are the UI-facing errors; they never carry Python tracebacks from the
backend. The UI maps them to user-visible behaviour.
"""

from __future__ import annotations

from vibeocr.runtime_client.client import RuntimeClientError

# Transitional alias: product code keeps its historic error import while the
# implementation and typed fields are owned by the Protocol package.
InferenceClientError = RuntimeClientError


class Unauthorized(RuntimeClientError):
    """Session token rejected."""


class QuotaExceeded(RuntimeClientError):
    """Request exceeded a body/count/staging quota."""


class JobNotFound(RuntimeClientError):
    """Referenced job id is unknown or has been purged."""


__all__ = ["InferenceClientError", "JobNotFound", "QuotaExceeded", "Unauthorized"]
