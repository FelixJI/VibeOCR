"""Closed runtime payload validation for WorkerHost protocol v1 methods."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_SHARED_NAME_RE = re.compile(
    r"^Local\\VibeOCR-"
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}-"
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_PIPELINES = frozenset({"OCR", "TABLE_RECOGNITION", "FORMULA_RECOGNITION"})


class MethodPayloadError(ValueError):
    """A method request/result violates the protocol-v1 closed shape."""


def _object(payload: Any, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise MethodPayloadError(f"{label} must be a JSON object")
    return payload


def _closed(
    payload: dict[str, Any],
    *,
    required: set[str] | frozenset[str] = frozenset(),
    optional: set[str] | frozenset[str] = frozenset(),
    label: str,
) -> None:
    missing = required - payload.keys()
    if missing:
        raise MethodPayloadError(f"{label} missing fields: {sorted(missing)}")
    extra = payload.keys() - required - optional
    if extra:
        raise MethodPayloadError(f"{label} has unknown fields: {sorted(extra)}")


def _string(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise MethodPayloadError(f"{label} must be a non-empty string")
    return value


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise MethodPayloadError(f"{label} must be an integer >= {minimum}")
    return value


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise MethodPayloadError(f"{label} must be a boolean")
    return value


def _uuid(value: Any, label: str) -> str:
    value = _string(value, label)
    if not _UUID_RE.match(value):
        raise MethodPayloadError(f"{label} must be a lowercase v4 UUID")
    return value


def _shared_ref(value: Any, label: str) -> None:
    ref = _object(value, label)
    required = {"name", "size", "media_type", "sha256", "owner", "expires_unix_ms"}
    _closed(ref, required=required, label=label)
    name = _string(ref["name"], f"{label}.name")
    if not _SHARED_NAME_RE.match(name):
        raise MethodPayloadError(f"{label}.name has an invalid namespace")
    _integer(ref["size"], f"{label}.size")
    _string(ref["media_type"], f"{label}.media_type")
    sha = _string(ref["sha256"], f"{label}.sha256")
    if not _SHA_RE.match(sha):
        raise MethodPayloadError(f"{label}.sha256 must be lowercase SHA-256")
    if ref["owner"] not in ("client", "worker"):
        raise MethodPayloadError(f"{label}.owner must be client or worker")
    _integer(ref["expires_unix_ms"], f"{label}.expires_unix_ms")


def _request_handshake(p: dict[str, Any]) -> None:
    _closed(
        p,
        required={"app_version", "protocol_version"},
        optional={"max_message_bytes", "max_shared_payload_bytes"},
        label="system.handshake request",
    )
    _string(p["app_version"], "app_version")
    if p["protocol_version"] != 1:
        raise MethodPayloadError("protocol_version must be 1")
    if "max_message_bytes" in p:
        _integer(p["max_message_bytes"], "max_message_bytes", minimum=1024)
    if "max_shared_payload_bytes" in p:
        _integer(p["max_shared_payload_bytes"], "max_shared_payload_bytes")


def _response_handshake(p: dict[str, Any]) -> None:
    required = {"worker_version", "protocol_version", "capabilities"}
    optional = {
        "python_version",
        "backend",
        "max_message_bytes",
        "max_shared_payload_bytes",
    }
    _closed(
        p,
        required=required,
        optional=optional,
        label="system.handshake response",
    )
    _string(p["worker_version"], "worker_version")
    if p["protocol_version"] != 1:
        raise MethodPayloadError("protocol_version must be 1")
    if not isinstance(p["capabilities"], list) or not all(
        isinstance(item, str) and item for item in p["capabilities"]
    ):
        raise MethodPayloadError("capabilities must be an array of strings")
    if "python_version" in p and not isinstance(p["python_version"], str):
        raise MethodPayloadError("python_version must be a string")
    if "backend" in p and p["backend"] not in ("cpu", "gpu"):
        raise MethodPayloadError("backend must be cpu or gpu")
    if "max_message_bytes" in p:
        _integer(p["max_message_bytes"], "max_message_bytes", minimum=1024)
    if "max_shared_payload_bytes" in p:
        _integer(p["max_shared_payload_bytes"], "max_shared_payload_bytes")


def _request_ping(p: dict[str, Any]) -> None:
    _closed(p, required={"nonce"}, label="system.ping request")
    _string(p["nonce"], "nonce")


def _response_ping(p: dict[str, Any]) -> None:
    _request_ping(p)


def _request_shutdown(p: dict[str, Any]) -> None:
    _closed(p, optional={"reason"}, label="system.shutdown request")
    if "reason" in p and not isinstance(p["reason"], str):
        raise MethodPayloadError("reason must be a string")


def _response_shutdown(p: dict[str, Any]) -> None:
    _closed(p, required={"acknowledged"}, label="system.shutdown response")
    if p["acknowledged"] is not True:
        raise MethodPayloadError("acknowledged must be true")


def _request_cancel(p: dict[str, Any]) -> None:
    _closed(p, required={"task_id"}, label="task.cancel request")
    _uuid(p["task_id"], "task_id")


def _response_cancel(p: dict[str, Any]) -> None:
    _closed(p, required={"accepted", "state"}, label="task.cancel response")
    _boolean(p["accepted"], "accepted")
    if p["state"] not in {"queued", "running", "completed", "failed", "cancelled", "unknown"}:
        raise MethodPayloadError("state is not a valid task state")


def _request_release(p: dict[str, Any]) -> None:
    _closed(p, required={"name"}, label="memory.release request")
    if not _SHARED_NAME_RE.match(_string(p["name"], "name")):
        raise MethodPayloadError("name has an invalid shared-memory namespace")


def _response_release(p: dict[str, Any]) -> None:
    _closed(p, required={"released"}, label="memory.release response")
    _boolean(p["released"], "released")


def _request_ocr(p: dict[str, Any]) -> None:
    _closed(
        p,
        required={"image"},
        optional={"pipeline", "language"},
        label="ocr.recognize request",
    )
    _shared_ref(p["image"], "image")
    if "pipeline" in p and p["pipeline"] not in _PIPELINES:
        raise MethodPayloadError("pipeline is not supported by protocol v1")
    if "language" in p and p["language"] is not None:
        _string(p["language"], "language")


def _response_ocr(p: dict[str, Any]) -> None:
    _closed(
        p,
        required={"text", "pipeline"},
        optional={"raw_blocks", "markdown_text", "html_text", "raw_text"},
        label="ocr.recognize response",
    )
    _string(p["text"], "text", allow_empty=True)
    if p["pipeline"] not in _PIPELINES:
        raise MethodPayloadError("pipeline is not supported by protocol v1")
    if "raw_blocks" in p and not isinstance(p["raw_blocks"], list):
        raise MethodPayloadError("raw_blocks must be an array")
    for name in ("markdown_text", "html_text", "raw_text"):
        if name in p:
            _string(p[name], name, allow_empty=True)


def _request_ocr_export(p: dict[str, Any]) -> None:
    _closed(
        p,
        required={"raw_text", "markdown_text", "html_text", "raw_blocks", "output_path", "format", "overwrite"},
        label="ocr.export request",
    )
    for name in ("raw_text", "markdown_text", "html_text"):
        _string(p[name], name, allow_empty=True)
    if not isinstance(p["raw_blocks"], list):
        raise MethodPayloadError("raw_blocks must be an array")
    _string(p["output_path"], "output_path")
    if p["format"] not in {"txt", "markdown", "html"}:
        raise MethodPayloadError("unsupported export format")
    _boolean(p["overwrite"], "overwrite")


def _response_ocr_export(p: dict[str, Any]) -> None:
    _closed(p, required={"output_path", "bytes_written"}, label="ocr.export response")
    _string(p["output_path"], "output_path")
    _integer(p["bytes_written"], "bytes_written")


def _request_pdf(p: dict[str, Any]) -> None:
    _closed(p, required={"file_path"}, label="pdf.open request")
    _string(p["file_path"], "file_path")


def _response_pdf(p: dict[str, Any]) -> None:
    _closed(
        p,
        required={"session_id", "file_path", "page_count"},
        label="pdf.open response",
    )
    _string(p["session_id"], "session_id")
    _string(p["file_path"], "file_path")
    _integer(p["page_count"], "page_count")


def _request_qr_decode(p: dict[str, Any]) -> None:
    _closed(p, required={"image"}, label="qrcode.decode request")
    _shared_ref(p["image"], "image")


def _response_qr_decode(p: dict[str, Any]) -> None:
    _closed(p, required={"codes"}, label="qrcode.decode response")
    if not isinstance(p["codes"], list):
        raise MethodPayloadError("codes must be an array")
    for index, item in enumerate(p["codes"]):
        code = _object(item, f"codes[{index}]")
        _closed(code, required={"data", "format"}, label=f"codes[{index}]")
        _string(code["data"], f"codes[{index}].data", allow_empty=True)
        _string(code["format"], f"codes[{index}].format")


def _request_qr_generate(p: dict[str, Any]) -> None:
    _closed(
        p,
        required={"data"},
        optional={"format"},
        label="qrcode.generate request",
    )
    _string(p["data"], "data")
    if "format" in p and p["format"] not in ("qrcode", "barcode"):
        raise MethodPayloadError("format must be qrcode or barcode")


def _response_qr_generate(p: dict[str, Any]) -> None:
    _closed(p, required={"image"}, label="qrcode.generate response")
    _shared_ref(p["image"], "image")


def _request_settings(p: dict[str, Any]) -> None:
    _closed(p, label="settings.snapshot request")


def _response_settings(p: dict[str, Any]) -> None:
    _closed(
        p,
        required={"backend", "preload_pipelines", "ttl_seconds"},
        label="settings.snapshot response",
    )
    if p["backend"] not in ("cpu", "gpu"):
        raise MethodPayloadError("backend must be cpu or gpu")
    if not isinstance(p["preload_pipelines"], list) or not all(
        isinstance(item, str) for item in p["preload_pipelines"]
    ):
        raise MethodPayloadError("preload_pipelines must be an array of strings")
    _integer(p["ttl_seconds"], "ttl_seconds")


Validator = Callable[[dict[str, Any]], None]
_VALIDATORS: dict[str, tuple[Validator, Validator]] = {
    "system.handshake": (_request_handshake, _response_handshake),
    "system.ping": (_request_ping, _response_ping),
    "system.shutdown": (_request_shutdown, _response_shutdown),
    "task.cancel": (_request_cancel, _response_cancel),
    "memory.release": (_request_release, _response_release),
    "ocr.recognize": (_request_ocr, _response_ocr),
    "ocr.export": (_request_ocr_export, _response_ocr_export),
    "pdf.open": (_request_pdf, _response_pdf),
    "qrcode.decode": (_request_qr_decode, _response_qr_decode),
    "qrcode.generate": (_request_qr_generate, _response_qr_generate),
    "settings.snapshot": (_request_settings, _response_settings),
}


def validate_method_payload(method: str, direction: str, payload: Any) -> None:
    """Validate a public method request or response against protocol v1."""
    validators = _VALIDATORS.get(method)
    if validators is None:
        raise MethodPayloadError(f"unknown method: {method}")
    if direction not in ("request", "response"):
        raise ValueError("direction must be request or response")
    validator = validators[0] if direction == "request" else validators[1]
    validator(_object(payload, f"{method} {direction}"))


PUBLIC_METHODS = frozenset(_VALIDATORS)

__all__ = ["PUBLIC_METHODS", "MethodPayloadError", "validate_method_payload"]
