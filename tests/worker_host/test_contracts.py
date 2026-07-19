"""Tests for WorkerHost v1 Python DTOs and error mapping (Task 1.2 Green).

Red-first cases: DTOs reject extra fields; error codes map stably to the
shared registry; envelope round-trips to/from JSON; invalid UTF-8 and unknown
error codes are rejected at the DTO layer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vibeocr.worker_host.contracts import (
    EnvelopeKind,
    RpcEnvelope,
    RpcError,
    RpcErrorBody,
    envelope_from_dict,
    envelope_from_json_bytes,
    envelope_to_json_bytes,
)
from vibeocr.worker_host.errors import ErrorCode, WorkerError

CONTRACTS_DIR = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "vibeocr-contracts-py"
    / "src"
    / "vibeocr"
    / "protocol"
    / "v1"
)


def _load_errors_registry() -> dict:
    return json.loads((CONTRACTS_DIR / "errors.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

REQUEST_UUID = "00000000-0000-4000-8000-000000000010"
TASK_UUID = "00000000-0000-4000-8000-000000000010"


# ---------------------------------------------------------------------------
# RpcEnvelope: construction, rejection of extra fields
# ---------------------------------------------------------------------------


def test_request_envelope_constructs() -> None:
    env = RpcEnvelope(
        protocol_version=1,
        request_id=REQUEST_UUID,
        task_id=TASK_UUID,
        method="system.ping",
        payload={"nonce": "abc"},
        deadline_unix_ms=0,
    )
    assert env.kind is EnvelopeKind.REQUEST
    assert env.method == "system.ping"


def test_response_success_envelope_kind() -> None:
    env = RpcEnvelope(
        protocol_version=1,
        request_id=REQUEST_UUID,
        task_id=TASK_UUID,
        result={"nonce": "abc"},
    )
    assert env.kind is EnvelopeKind.RESPONSE_SUCCESS


def test_response_error_envelope_kind() -> None:
    env = RpcEnvelope(
        protocol_version=1,
        request_id=REQUEST_UUID,
        task_id=TASK_UUID,
        error=RpcErrorBody(
            code=ErrorCode.TASK_CANCELLED, message="x", retryable=False
        ),
    )
    assert env.kind is EnvelopeKind.RESPONSE_ERROR


def test_event_envelope_kind() -> None:
    env = RpcEnvelope(
        protocol_version=1,
        request_id=None,
        task_id=TASK_UUID,
        event="task.progress",
        sequence=0,
        payload={"current": 1},
    )
    assert env.kind is EnvelopeKind.EVENT


def test_envelope_rejects_both_result_and_error() -> None:
    with pytest.raises(ValueError, match=r"result and error"):
        RpcEnvelope(
            protocol_version=1,
            request_id=REQUEST_UUID,
            task_id=TASK_UUID,
            result={"x": 1},
            error=RpcErrorBody(
                code=ErrorCode.INTERNAL_ERROR, message="m", retryable=False
            ),
        )


# ---------------------------------------------------------------------------
# envelope_from_dict: rejects extra fields, wrong version, missing ids
# ---------------------------------------------------------------------------


def test_envelope_from_dict_rejects_extra_field() -> None:
    with pytest.raises(ValueError, match="unknown fields"):
        envelope_from_dict(
            {
                "protocol_version": 1,
                "request_id": REQUEST_UUID,
                "task_id": TASK_UUID,
                "method": "system.ping",
                "payload": {"nonce": "x"},
                "deadline_unix_ms": 0,
                "sneaky": True,
            }
        )


def test_envelope_from_dict_rejects_wrong_protocol_version() -> None:
    with pytest.raises(ValueError):
        envelope_from_dict(
            {
                "protocol_version": 2,
                "request_id": REQUEST_UUID,
                "task_id": TASK_UUID,
                "method": "system.ping",
                "payload": {"nonce": "x"},
                "deadline_unix_ms": 0,
            }
        )


def test_envelope_from_dict_rejects_missing_request_id_for_request() -> None:
    with pytest.raises((ValueError, TypeError)):
        envelope_from_dict(
            {
                "protocol_version": 1,
                "task_id": TASK_UUID,
                "method": "system.ping",
                "payload": {"nonce": "x"},
                "deadline_unix_ms": 0,
            }
        )


def test_envelope_from_dict_rejects_missing_task_id() -> None:
    with pytest.raises((ValueError, TypeError)):
        envelope_from_dict(
            {
                "protocol_version": 1,
                "request_id": REQUEST_UUID,
                "method": "system.ping",
                "payload": {"nonce": "x"},
                "deadline_unix_ms": 0,
            }
        )


def test_envelope_from_dict_rejects_bad_uuid() -> None:
    with pytest.raises(ValueError):
        envelope_from_dict(
            {
                "protocol_version": 1,
                "request_id": "not-a-uuid",
                "task_id": TASK_UUID,
                "method": "system.ping",
                "payload": {"nonce": "x"},
                "deadline_unix_ms": 0,
            }
        )


def test_envelope_from_dict_rejects_response_neither_result_nor_error() -> None:
    with pytest.raises(ValueError):
        envelope_from_dict(
            {
                "protocol_version": 1,
                "request_id": REQUEST_UUID,
                "task_id": TASK_UUID,
            }
        )


def test_envelope_from_dict_rejects_unknown_error_code() -> None:
    with pytest.raises(ValueError):
        envelope_from_dict(
            {
                "protocol_version": 1,
                "request_id": REQUEST_UUID,
                "task_id": TASK_UUID,
                "error": {"code": "NUKE_EVERYTHING", "message": "x", "retryable": True},
            }
        )


# ---------------------------------------------------------------------------
# JSON round-trip
# ---------------------------------------------------------------------------


def test_envelope_round_trip_request() -> None:
    env = RpcEnvelope(
        protocol_version=1,
        request_id=REQUEST_UUID,
        task_id=TASK_UUID,
        method="ocr.recognize",
        payload={"text": "café"},
        deadline_unix_ms=42,
    )
    raw = envelope_to_json_bytes(env)
    restored = envelope_from_json_bytes(raw)
    assert restored == env


def test_envelope_round_trip_error() -> None:
    env = RpcEnvelope(
        protocol_version=1,
        request_id=REQUEST_UUID,
        task_id=TASK_UUID,
        error=RpcErrorBody(
            code=ErrorCode.WORKER_UNAVAILABLE, message="busy", retryable=True
        ),
    )
    raw = envelope_to_json_bytes(env)
    restored = envelope_from_json_bytes(raw)
    assert restored == env


def test_envelope_to_json_bytes_omits_none_fields() -> None:
    env = RpcEnvelope(
        protocol_version=1,
        request_id=REQUEST_UUID,
        task_id=TASK_UUID,
        method="system.ping",
        payload={"nonce": "x"},
        deadline_unix_ms=0,
    )
    doc = json.loads(envelope_to_json_bytes(env))
    # No result/error/event/sequence keys on a request envelope.
    for absent in ("result", "error", "event", "sequence"):
        assert absent not in doc


def test_envelope_from_json_bytes_rejects_invalid_utf8() -> None:
    with pytest.raises((UnicodeDecodeError, ValueError)):
        envelope_from_json_bytes(b'\xff\xfe{"a":1}')


def test_envelope_from_json_bytes_rejects_invalid_json() -> None:
    with pytest.raises(json.JSONDecodeError):
        envelope_from_json_bytes(b"not json")


# ---------------------------------------------------------------------------
# ErrorCode / errors registry consistency
# ---------------------------------------------------------------------------


def test_error_code_enum_matches_registry() -> None:
    registry = _load_errors_registry()
    registered = {entry["code"] for entry in registry["codes"]}
    enum_members = {code.value for code in ErrorCode}
    assert enum_members == registered, (
        "Python ErrorCode enum must exactly match contracts/v1/errors.json"
    )


def test_error_code_retryable_flags_match_registry() -> None:
    registry = _load_errors_registry()
    retryable_by_code = {e["code"]: e["retryable"] for e in registry["codes"]}
    for code in ErrorCode:
        assert code.default_retryable() == retryable_by_code[code.value], (
            f"{code} default_retryable mismatch with registry"
        )


# ---------------------------------------------------------------------------
# WorkerError -> RpcErrorBody mapping
# ---------------------------------------------------------------------------


def test_worker_error_to_body_preserves_code_message_retryable() -> None:
    err = WorkerError(ErrorCode.TASK_TIMEOUT, "took too long")
    body = err.to_body()
    assert body.code is ErrorCode.TASK_TIMEOUT
    assert body.message == "took too long"
    assert body.retryable is ErrorCode.TASK_TIMEOUT.default_retryable()


def test_worker_error_to_body_includes_detail_when_provided() -> None:
    err = WorkerError(ErrorCode.INTERNAL_ERROR, "boom", detail="trace")
    body = err.to_body()
    assert body.detail == "trace"


def test_worker_error_default_message_from_registry() -> None:
    err = WorkerError(ErrorCode.PROTOCOL_MISMATCH)
    body = err.to_body()
    registry = _load_errors_registry()
    expected = next(e["message"] for e in registry["codes"] if e["code"] == "PROTOCOL_MISMATCH")
    assert body.message == expected


def test_rpc_error_to_envelope() -> None:
    err = RpcError(
        code=ErrorCode.RESOURCE_EXHAUSTED,
        message="no memory",
        request_id=REQUEST_UUID,
        task_id=TASK_UUID,
    )
    env = err.to_envelope()
    assert env.kind is EnvelopeKind.RESPONSE_ERROR
    assert env.error is not None
    assert env.error.code is ErrorCode.RESOURCE_EXHAUSTED
