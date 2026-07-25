"""VibeOCR supervisor client (HTTP v2).

This package is the frontend-facing client: it launches a supervisor child
process, talks to it over localhost HTTP v2, and exposes job handles for
PySide. It only depends on ``vibeocr-contracts-py`` (no backend imports).

Public surface:

* :class:`SupervisorClient` — async HTTP client over ``httpx``.
* :class:`SupervisorProcess` — child process launcher + ready handshake.
* :class:`JobHandle` — a submitted job's lifecycle helper.
* :class:`InferenceClientError` — transport-neutral typed errors.

This directory is part of a namespace package merged across distributions:
``vibeocr-client-py`` contributes the client modules and
``vibeocr-backend`` contributes the server modules. We use ``extend_path``
so both merge at runtime rather than shadowing each other.
"""

from __future__ import annotations

from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)

from .client import SupervisorClient
from .errors import InferenceClientError, JobNotFound, QuotaExceeded, Unauthorized
from .job_handle import JobHandle
from .process import ReadyEnvelope, SupervisorLaunchError, SupervisorProcess

__all__ = [
    "InferenceClientError",
    "JobHandle",
    "JobNotFound",
    "QuotaExceeded",
    "ReadyEnvelope",
    "SupervisorClient",
    "SupervisorLaunchError",
    "SupervisorProcess",
    "Unauthorized",
]
