"""Stable error codes for the WorkerHost v1 wire contract.

The codes here MUST stay in lockstep with ``contracts/v1/errors.json`` and with
``envelope.schema.json``'s ``rpc_error/code`` enum. The test suite
(``tests/worker_host/test_contracts.py``) asserts they are identical, so a drift
fails CI on the Python side; the C# golden test (Task 2.2) fails the other side.

Design §7 retry policy is encoded in ``default_retryable``: query-type OCR may
auto-retry once on worker crash; mutations (pdf save/mutation, dependency
install, backend switch, update) NEVER auto-retry.
"""

from __future__ import annotations

import json
from enum import StrEnum
from importlib.resources import files
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from vibeocr.worker_host.contracts import RpcErrorBody

_PROTOCOL_VERSION = 1
_ERRORS_JSON = files("vibeocr.protocol.v1").joinpath("errors.json")


def _load_registry() -> dict[str, Any]:
    """Load the authoritative error registry (single source of truth)."""
    return json.loads(_ERRORS_JSON.read_text(encoding="utf-8"))


class ErrorCode(StrEnum):
    """Stable error code enum.

    Values are strings (the wire representation). ``StrEnum`` lets an
    ``ErrorCode`` be serialized directly by ``json.dumps``.
    """

    INVALID_REQUEST = "INVALID_REQUEST"
    DEPENDENCY_MISSING = "DEPENDENCY_MISSING"
    WORKER_UNAVAILABLE = "WORKER_UNAVAILABLE"
    TASK_CANCELLED = "TASK_CANCELLED"
    TASK_TIMEOUT = "TASK_TIMEOUT"
    PROTOCOL_MISMATCH = "PROTOCOL_MISMATCH"
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"
    INTERNAL_ERROR = "INTERNAL_ERROR"

    def default_retryable(self) -> bool:
        """Retry policy for this code, sourced from errors.json (design §7)."""
        for entry in _load_registry()["codes"]:
            if entry["code"] == self.value:
                return bool(entry["retryable"])
        # Unknown code: never retry (safe default for mutations).
        return False

    @classmethod
    def from_value(cls, value: str) -> ErrorCode:
        """Parse a wire string into an ErrorCode; raise on unknown codes."""
        try:
            return cls(value)
        except ValueError as exc:
            raise ValueError(f"unknown error code: {value!r}") from exc

    @property
    def default_message(self) -> str:
        for entry in _load_registry()["codes"]:
            if entry["code"] == self.value:
                return str(entry["message"])
        return self.value


class WorkerError(Exception):
    """An error raised inside the WorkerHost that maps onto a wire error code.

    Attributes:
        code: stable ErrorCode.
        message: user-displayable message (defaults to the registry template).
        detail: optional diagnostic for logs, never shown verbatim to users.
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str | None = None,
        *,
        detail: str | None = None,
    ) -> None:
        self.code = code
        self.message = message if message is not None else code.default_message
        self.detail = detail
        super().__init__(self.message)

    def to_body(self) -> RpcErrorBody:
        from vibeocr.worker_host.contracts import (
            RpcErrorBody,  # local import: avoid cycle
        )

        return RpcErrorBody(
            code=self.code,
            message=self.message,
            detail=self.detail,
            retryable=self.code.default_retryable(),
        )


__all__ = ["ErrorCode", "WorkerError"]
