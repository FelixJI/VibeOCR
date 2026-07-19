"""Versioned wire DTOs for the WorkerHost v1 protocol.

This module is the Python counterpart to ``contracts/v1/envelope.schema.json``.
It defines:

- ``RpcEnvelope``: the discriminated union of request / response (success or
  error) / event messages.
- ``RpcErrorBody``: the structured error inside an error response.
- ``RpcError``: an error that knows how to render itself as an error envelope.
- ``envelope_from_dict`` / ``envelope_from_json_bytes``: parsing with strict
  rejection of unknown fields, bad UUIDs, wrong protocol versions, and unknown
  error codes.
- ``envelope_to_json_bytes``: canonical serialization for the wire.

DTOs are intentionally strict: the schema is the single source of truth and
these types enforce the same constraints in code so malformed input is rejected
before it reaches a handler.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from vibeocr.worker_host.errors import ErrorCode, WorkerError

PROTOCOL_VERSION = 1

# RFC 4122 v4 UUID, lowercase dashed.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

# Closed set of keys allowed on each envelope kind.
_REQUEST_KEYS = frozenset(
    {"protocol_version", "request_id", "task_id", "method", "payload", "deadline_unix_ms"}
)
_RESPONSE_KEYS = frozenset({"protocol_version", "request_id", "task_id", "result", "error"})
_EVENT_KEYS = frozenset({"protocol_version", "task_id", "event", "sequence", "payload"})


class EnvelopeKind(StrEnum):
    """The discriminator value of an envelope."""

    REQUEST = "request"
    RESPONSE_SUCCESS = "response_success"
    RESPONSE_ERROR = "response_error"
    EVENT = "event"


def _validate_uuid(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _UUID_RE.match(value):
        raise ValueError(f"{field_name} must be a v4 UUID string, got {value!r}")
    return value


def _validate_protocol_version(value: Any) -> int:
    if value != PROTOCOL_VERSION:
        raise ValueError(
            f"protocol_version must be {PROTOCOL_VERSION}, got {value!r}"
        )
    return int(value)


def _validate_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"payload must be a JSON object, got {type(value).__name__}")
    return value


@dataclass(frozen=True, slots=True)
class RpcErrorBody:
    """The structured error inside an error response.

    ``code`` is a stable enum (never a free string). ``detail`` is optional and
    never shown to end users verbatim.
    """

    code: ErrorCode
    message: str
    retryable: bool
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.detail is not None:
            out["detail"] = self.detail
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RpcErrorBody:
        allowed = {"code", "message", "retryable", "detail"}
        extra = set(data.keys()) - allowed
        if extra:
            raise ValueError(f"rpc_error has unknown fields: {sorted(extra)}")
        if not {"code", "message", "retryable"}.issubset(data):
            raise ValueError("rpc_error missing required fields")
        return cls(
            code=ErrorCode.from_value(str(data["code"])),
            message=str(data["message"]),
            retryable=bool(data["retryable"]),
            detail=data.get("detail"),
        )


@dataclass
class RpcEnvelope:
    """A WorkerHost v1 envelope. Exactly one of the message kinds applies.

    Construction validates the discriminating invariants (e.g. a request must
    have a method; a response has exactly one of result/error; an event has an
    event name and sequence). For parsing untrusted input use
    ``envelope_from_dict`` which also rejects unknown fields.
    """

    protocol_version: int = PROTOCOL_VERSION
    request_id: str | None = None
    task_id: str | None = None
    method: str | None = None
    payload: dict[str, Any] | None = None
    deadline_unix_ms: int | None = None
    result: dict[str, Any] | None = None
    error: RpcErrorBody | None = None
    event: str | None = None
    sequence: int | None = None

    def __post_init__(self) -> None:
        self.protocol_version = _validate_protocol_version(self.protocol_version)
        # Determine kind and enforce its invariants.
        _ = self.kind  # raises on invalid combinations
        if self.kind in (EnvelopeKind.REQUEST, EnvelopeKind.EVENT):
            if self.kind is EnvelopeKind.REQUEST and self.method is None:
                raise ValueError("request envelope missing method")
            if self.kind is EnvelopeKind.EVENT and self.event is None:
                raise ValueError("event envelope missing event name")
        if self.kind is EnvelopeKind.REQUEST:
            assert self.request_id is not None and self.task_id is not None
            if self.deadline_unix_ms is None:
                raise ValueError("request envelope missing deadline_unix_ms")
        if self.kind is EnvelopeKind.EVENT:
            assert self.task_id is not None
            if self.sequence is None:
                raise ValueError("event envelope missing sequence")

    @property
    def kind(self) -> EnvelopeKind:
        """Return the discriminator, validating that exactly one applies."""
        has_method = self.method is not None
        has_result = self.result is not None
        has_error = self.error is not None
        has_event = self.event is not None
        if has_result and has_error:
            raise ValueError("envelope has both result and error (mutually exclusive)")
        if has_method and (has_result or has_error or has_event):
            raise ValueError("envelope has method alongside result/error/event")
        if has_event and (has_result or has_error or has_method):
            raise ValueError("envelope has event alongside result/error/method")
        if has_method:
            return EnvelopeKind.REQUEST
        if has_result:
            return EnvelopeKind.RESPONSE_SUCCESS
        if has_error:
            return EnvelopeKind.RESPONSE_ERROR
        if has_event:
            return EnvelopeKind.EVENT
        raise ValueError("envelope is neither request, response, nor event")

    # ----- serialization ------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind
        if kind is EnvelopeKind.REQUEST:
            assert self.request_id is not None
            assert self.task_id is not None
            assert self.method is not None
            assert self.payload is not None
            assert self.deadline_unix_ms is not None
            return {
                "protocol_version": self.protocol_version,
                "request_id": self.request_id,
                "task_id": self.task_id,
                "method": self.method,
                "payload": self.payload,
                "deadline_unix_ms": self.deadline_unix_ms,
            }
        if kind is EnvelopeKind.RESPONSE_SUCCESS:
            assert self.request_id is not None
            assert self.task_id is not None
            assert self.result is not None
            return {
                "protocol_version": self.protocol_version,
                "request_id": self.request_id,
                "task_id": self.task_id,
                "result": self.result,
            }
        if kind is EnvelopeKind.RESPONSE_ERROR:
            assert self.request_id is not None
            assert self.task_id is not None
            assert self.error is not None
            return {
                "protocol_version": self.protocol_version,
                "request_id": self.request_id,
                "task_id": self.task_id,
                "error": self.error.to_dict(),
            }
        # event
        assert self.task_id is not None
        assert self.event is not None
        assert self.sequence is not None
        assert self.payload is not None
        return {
            "protocol_version": self.protocol_version,
            "task_id": self.task_id,
            "event": self.event,
            "sequence": self.sequence,
            "payload": self.payload,
        }


@dataclass
class RpcError:
    """A renderable error carrying the routing ids needed for a response."""

    code: ErrorCode
    message: str
    request_id: str
    task_id: str
    detail: str | None = None
    retryable: bool | None = None

    def to_envelope(self) -> RpcEnvelope:
        return RpcEnvelope(
            protocol_version=PROTOCOL_VERSION,
            request_id=self.request_id,
            task_id=self.task_id,
            error=RpcErrorBody(
                code=self.code,
                message=self.message,
                retryable=(
                    self.retryable
                    if self.retryable is not None
                    else self.code.default_retryable()
                ),
                detail=self.detail,
            ),
        )

    @classmethod
    def from_worker_error(
        cls, err: WorkerError, *, request_id: str, task_id: str
    ) -> RpcError:
        return cls(
            code=err.code,
            message=err.message,
            request_id=request_id,
            task_id=task_id,
            detail=err.detail,
        )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def envelope_from_dict(data: dict[str, Any]) -> RpcEnvelope:
    """Parse a dict into an RpcEnvelope, strictly rejecting malformed input.

    Rejects: non-object input, unknown fields, wrong protocol version, missing
    request/task id, malformed UUID, response with both/neither result and error,
    and unknown error codes.
    """
    if not isinstance(data, dict):
        raise ValueError(f"envelope must be a JSON object, got {type(data).__name__}")

    keys = set(data.keys())
    protocol_version = _validate_protocol_version(data.get("protocol_version"))

    # Classify by allowed key set.
    if keys <= _REQUEST_KEYS and "method" in keys:
        return _parse_request(data, keys, protocol_version)
    if keys <= _RESPONSE_KEYS and ("result" in keys or "error" in keys):
        return _parse_response(data, keys, protocol_version)
    if keys <= _EVENT_KEYS and "event" in keys:
        return _parse_event(data, keys, protocol_version)

    # Fall through: surface the most helpful error.
    unknown = keys - (_REQUEST_KEYS | _RESPONSE_KEYS | _EVENT_KEYS)
    if unknown:
        raise ValueError(f"envelope has unknown fields: {sorted(unknown)}")
    raise ValueError(
        f"envelope is not a well-formed request, response, or event (keys={sorted(keys)})"
    )


def _parse_request(
    data: dict[str, Any], keys: set[str], protocol_version: int
) -> RpcEnvelope:
    missing = _REQUEST_KEYS - keys
    if missing:
        raise ValueError(f"request envelope missing fields: {sorted(missing)}")
    return RpcEnvelope(
        protocol_version=protocol_version,
        request_id=_validate_uuid(data["request_id"], "request_id"),
        task_id=_validate_uuid(data["task_id"], "task_id"),
        method=str(data["method"]),
        payload=_validate_payload(data["payload"]),
        deadline_unix_ms=int(data["deadline_unix_ms"]),
    )


def _parse_response(
    data: dict[str, Any], keys: set[str], protocol_version: int
) -> RpcEnvelope:
    missing = {"request_id", "task_id"} - keys
    if missing:
        raise ValueError(f"response envelope missing fields: {sorted(missing)}")
    request_id = _validate_uuid(data["request_id"], "request_id")
    task_id = _validate_uuid(data["task_id"], "task_id")
    has_result = "result" in keys
    has_error = "error" in keys
    if has_result and has_error:
        raise ValueError("response envelope has both result and error")
    if not has_result and not has_error:
        raise ValueError("response envelope has neither result nor error")
    if has_result:
        result = data["result"]
        if not isinstance(result, dict):
            raise ValueError("response result must be a JSON object")
        return RpcEnvelope(
            protocol_version=protocol_version,
            request_id=request_id,
            task_id=task_id,
            result=result,
        )
    error_raw = data["error"]
    if not isinstance(error_raw, dict):
        raise ValueError("response error must be a JSON object")
    return RpcEnvelope(
        protocol_version=protocol_version,
        request_id=request_id,
        task_id=task_id,
        error=RpcErrorBody.from_dict(error_raw),
    )


def _parse_event(
    data: dict[str, Any], keys: set[str], protocol_version: int
) -> RpcEnvelope:
    missing = _EVENT_KEYS - keys
    if missing:
        raise ValueError(f"event envelope missing fields: {sorted(missing)}")
    return RpcEnvelope(
        protocol_version=protocol_version,
        task_id=_validate_uuid(data["task_id"], "task_id"),
        event=str(data["event"]),
        sequence=int(data["sequence"]),
        payload=_validate_payload(data["payload"]),
    )


# ---------------------------------------------------------------------------
# JSON (de)serialization
# ---------------------------------------------------------------------------


def envelope_to_json_bytes(env: RpcEnvelope) -> bytes:
    """Canonical UTF-8 JSON serialization of an envelope."""
    return json.dumps(env.to_dict(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def envelope_from_json_bytes(raw: bytes) -> RpcEnvelope:
    """Parse UTF-8 JSON bytes into an RpcEnvelope.

    Raises UnicodeDecodeError on invalid UTF-8 and ValueError/JSONDecodeError
    on malformed JSON or contract violations.
    """
    text = raw.decode("utf-8")  # may raise UnicodeDecodeError
    data = json.loads(text)  # may raise JSONDecodeError
    return envelope_from_dict(data)


__all__ = [
    "PROTOCOL_VERSION",
    "EnvelopeKind",
    "RpcEnvelope",
    "RpcError",
    "RpcErrorBody",
    "envelope_from_dict",
    "envelope_from_json_bytes",
    "envelope_to_json_bytes",
]
