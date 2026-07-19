from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from vibeocr.worker_host.method_validation import (
    PUBLIC_METHODS,
    MethodPayloadError,
    validate_method_payload,
)

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = (
    ROOT
    / "packages"
    / "vibeocr-contracts-py"
    / "src"
    / "vibeocr"
    / "protocol"
    / "v1"
)


@pytest.fixture(scope="module")
def golden() -> dict[str, Any]:
    return json.loads((CONTRACTS_DIR / "golden.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("method", sorted(PUBLIC_METHODS))
def test_golden_request_and_response_validate(
    golden: dict[str, Any], method: str
) -> None:
    case = golden["positive"][method]
    validate_method_payload(method, "request", case["request_envelope"]["payload"])
    validate_method_payload(method, "response", case["response_envelope"]["result"])


def test_unknown_request_field_is_rejected() -> None:
    with pytest.raises(MethodPayloadError, match="unknown fields"):
        validate_method_payload("system.ping", "request", {"nonce": "x", "extra": 1})


def test_invalid_nested_shared_descriptor_is_rejected(golden: dict[str, Any]) -> None:
    payload = dict(golden["positive"]["ocr.recognize"]["request_envelope"]["payload"])
    payload["image"] = {**payload["image"], "owner": "attacker"}
    with pytest.raises(MethodPayloadError, match="owner"):
        validate_method_payload("ocr.recognize", "request", payload)


def test_unknown_method_is_rejected() -> None:
    with pytest.raises(MethodPayloadError, match="unknown method"):
        validate_method_payload("evil.eval", "request", {})


def test_empty_ocr_batch_is_rejected() -> None:
    with pytest.raises(MethodPayloadError, match="between 1 and 64"):
        validate_method_payload(
            "ocr.recognize_batch",
            "request",
            {"images": [], "pipeline": "OCR"},
        )
