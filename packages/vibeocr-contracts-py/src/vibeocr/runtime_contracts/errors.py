"""Typed error registry for the HTTP v2 protocol.

Loads ``errors.json`` shipped with this package and exposes it as enums + a
lookup helper. Codes are part of the wire contract: never rename or renumber.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from importlib import resources
from typing import Any

from .generated.error_codes import RuntimeErrorCode as ErrorCode

SCHEMA_VERSION = 2


class ErrorCategories(StrEnum):
    VALIDATION = "validation"
    AUTH = "auth"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    CANCELLED = "cancelled"
    OOM = "oom"
    TRANSIENT = "transient"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class ErrorEntry:
    code: ErrorCode
    category: ErrorCategories
    http_status: int
    retryable: bool
    message: str


@dataclass(frozen=True, slots=True)
class ErrorPayload:
    """Wire form of an error response."""

    schema_version: int
    instance_id: str | None
    code: ErrorCode
    message: str
    category: ErrorCategories
    retryable: bool
    detail: dict[str, Any] | None = None
    job_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "instance_id": self.instance_id,
            "code": self.code.value,
            "message": self.message,
            "category": self.category.value,
            "retryable": self.retryable,
            "detail": self.detail or {},
            "job_id": self.job_id,
        }


def load_error_registry() -> dict[ErrorCode, ErrorEntry]:
    """Load and validate the bundled ``errors.json`` registry."""

    raw = json.loads(resources.files(__package__).joinpath("errors.json").read_text(encoding="utf-8"))
    out: dict[ErrorCode, ErrorEntry] = {}
    for row in raw["codes"]:
        code = ErrorCode(row["code"])
        category = ErrorCategories(row["category"])
        out[code] = ErrorEntry(
            code=code,
            category=category,
            http_status=int(row["http_status"]),
            retryable=bool(row["retryable"]),
            message=row["message"],
        )
    return out


# Module-level registry; loaded once. Re-import safe.
error_registry: dict[ErrorCode, ErrorEntry] = load_error_registry()


def entry_for(code: ErrorCode | str) -> ErrorEntry:
    key = code if isinstance(code, ErrorCode) else ErrorCode(code)
    try:
        return error_registry[key]
    except KeyError as exc:  # pragma: no cover - defensive
        raise ValueError(f"unknown error code: {code}") from exc


__all__ = [
    "SCHEMA_VERSION",
    "ErrorCategories",
    "ErrorCode",
    "ErrorEntry",
    "ErrorPayload",
    "entry_for",
    "error_registry",
    "load_error_registry",
]
