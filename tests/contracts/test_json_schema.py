"""Contract tests for VibeOCR WorkerHost v1 JSON schemas and golden fixtures.

Red/Green boundary for Task 1.1:
- Positive: every public method has at least one valid request/response golden,
  and the golden validates against the envelope + methods schemas.
- Negative: malformed envelope, unknown fields, wrong protocol version, missing
  request/task id, malformed UUID, unknown method, response with both/neither
  result and error, invalid shared-payload descriptors, and unknown error codes
  MUST be rejected by schema validation.

These tests are language-neutral contract checks; the same golden.json is later
consumed by tests/dotnet/VibeOCR.Contracts.Tests (Task 2.2) so Python and C#
agree on the wire format.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import referencing.jsonschema
from jsonschema import Draft202012Validator
from jsonschema import exceptions as js_exceptions

CONTRACTS_DIR = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "vibeocr-contracts-py"
    / "src"
    / "vibeocr"
    / "protocol"
    / "v1"
)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _load(name: str) -> Any:
    return json.loads((CONTRACTS_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def envelope_schema() -> dict[str, Any]:
    return _load("envelope.schema.json")


@pytest.fixture(scope="module")
def methods_schema() -> dict[str, Any]:
    return _load("methods.schema.json")


@pytest.fixture(scope="module")
def errors_registry() -> dict[str, Any]:
    return _load("errors.json")


@pytest.fixture(scope="module")
def golden() -> dict[str, Any]:
    return _load("golden.json")


@pytest.fixture(scope="module")
def envelope_validator(
    envelope_schema: dict[str, Any],
) -> Draft202012Validator:
    return Draft202012Validator(envelope_schema)


@pytest.fixture(scope="module")
def envelope_methods_validator(
    envelope_schema: dict[str, Any], methods_schema: dict[str, Any]
) -> Draft202012Validator:
    """A validator whose resolver knows BOTH schema documents, so per-method
    payload schemas can reference $defs in either file (e.g. methods #/$defs/backend,
    or envelope.json#/$defs/uuid)."""
    envelope_resource = referencing.jsonschema.DRAFT202012.create_resource(
        envelope_schema
    )
    methods_resource = referencing.jsonschema.DRAFT202012.create_resource(
        methods_schema
    )
    envelope_uri = envelope_schema["$id"]
    methods_uri = methods_schema["$id"]
    registry = referencing.Registry().with_resource(
        envelope_uri, envelope_resource
    ).with_resource(methods_uri, methods_resource)
    return Draft202012Validator(envelope_schema, registry=registry)


def _validate_method_payload(
    methods_schema: dict[str, Any],
    method: str,
    payload_kind: str,
    payload: dict[str, Any],
    envelope_schema: dict[str, Any] | None = None,
) -> None:
    """Validate a per-method payload (request or response).

    The sub-schema's internal $refs (e.g. #/$defs/shared_payload_ref,
    #/$defs/backend) resolve against the methods document, and any cross-doc
    refs (e.g. envelope.schema.json#/$defs/uuid) resolve against the envelope
    document when it is provided.
    """
    spec = methods_schema["properties"].get(method)
    assert spec is not None, f"method {method!r} not in methods.schema.json allow-list"
    assert payload_kind in spec["properties"], (
        f"method {method!r} has no {payload_kind!r} schema"
    )
    methods_uri = methods_schema["$id"]
    registry = referencing.Registry().with_resource(
        methods_uri,
        referencing.jsonschema.DRAFT202012.create_resource(methods_schema),
    )
    if envelope_schema is not None:
        registry = registry.with_resource(
            envelope_schema["$id"],
            referencing.jsonschema.DRAFT202012.create_resource(envelope_schema),
        )
    wrapper = {
        "$id": "memory://payload-wrapper",
        "$ref": f"{methods_uri}#/properties/{method}/properties/{payload_kind}",
    }
    Draft202012Validator(wrapper, registry=registry).validate(payload)


# ---------------------------------------------------------------------------
# Schema files themselves are well-formed
# ---------------------------------------------------------------------------


def test_envelope_schema_is_draft_2020_12(envelope_schema: dict[str, Any]) -> None:
    assert envelope_schema["$schema"].endswith("draft/2020-12/schema")
    Draft202012Validator.check_schema(envelope_schema)


def test_methods_schema_is_draft_2020_12(methods_schema: dict[str, Any]) -> None:
    assert methods_schema["$schema"].endswith("draft/2020-12/schema")
    Draft202012Validator.check_schema(methods_schema)


def test_pipeline_enum_matches_frontend_contract(methods_schema: dict[str, Any]) -> None:
    from vibeocr.contracts.pipelines import OCRPipeline

    assert set(methods_schema["$defs"]["pipeline"]["enum"]) == {
        pipeline.value for pipeline in OCRPipeline
    }


def test_protocol_version_is_integer_one(envelope_schema: dict[str, Any]) -> None:
    # The plan pins protocol_version to integer 1 (not string "1.0").
    req = envelope_schema["$defs"]["request"]["properties"]["protocol_version"]
    assert req.get("const") == 1
    resp = envelope_schema["$defs"]["response"]["properties"]["protocol_version"]
    assert resp.get("const") == 1


# ---------------------------------------------------------------------------
# Positive: every public method has a golden, and each golden validates
# ---------------------------------------------------------------------------

# Every method that should have a request/response golden pair.
PUBLIC_METHODS = [
    "system.handshake",
    "system.ping",
    "system.shutdown",
    "task.cancel",
    "memory.release",
    "ocr.recognize",
    "ocr.recognize_batch",
    "ocr.export",
    "pdf.open",
    "pdf.close",
    "pdf.command",
    "pdf.render_page",
    "pdf.rotate",
    "pdf.delete_pages",
    "pdf.add_text_layer",
    "pdf.delete_text_layers",
    "pdf.save",
    "pdf.start_ocr",
    "qrcode.decode",
    "qrcode.generate",
    "settings.snapshot",
    "settings.switch_backend",
    "settings.install_dependency",
]


@pytest.mark.parametrize("method", PUBLIC_METHODS)
def test_each_public_method_has_golden(golden: dict[str, Any], method: str) -> None:
    assert method in golden["positive"], f"missing golden for method {method!r}"
    block = golden["positive"][method]
    assert "request_envelope" in block, f"{method}: missing request_envelope"
    assert "response_envelope" in block, f"{method}: missing response_envelope"


@pytest.mark.parametrize("method", PUBLIC_METHODS)
def test_golden_request_envelopes_validate(
    envelope_validator: Draft202012Validator,
    methods_schema: dict[str, Any],
    envelope_schema: dict[str, Any],
    golden: dict[str, Any],
    method: str,
) -> None:
    req_env = golden["positive"][method]["request_envelope"]
    envelope_validator.validate(req_env)
    _validate_method_payload(
        methods_schema, method, "request", req_env["payload"], envelope_schema
    )


@pytest.mark.parametrize("method", PUBLIC_METHODS)
def test_golden_response_envelopes_validate(
    envelope_validator: Draft202012Validator,
    methods_schema: dict[str, Any],
    envelope_schema: dict[str, Any],
    golden: dict[str, Any],
    method: str,
) -> None:
    resp_env = golden["positive"][method]["response_envelope"]
    envelope_validator.validate(resp_env)
    _validate_method_payload(
        methods_schema, method, "response", resp_env["result"], envelope_schema
    )


def test_event_envelope_validates(
    envelope_validator: Draft202012Validator, golden: dict[str, Any]
) -> None:
    event_env = golden["positive"]["task.progress_event"]["event_envelope"]
    envelope_validator.validate(event_env)


# ---------------------------------------------------------------------------
# Errors registry consistency
# ---------------------------------------------------------------------------


def test_errors_registry_matches_envelope_enum(
    envelope_schema: dict[str, Any], errors_registry: dict[str, Any]
) -> None:
    allowed = set(
        envelope_schema["$defs"]["rpc_error"]["properties"]["code"]["enum"]
    )
    registered = {entry["code"] for entry in errors_registry["codes"]}
    assert registered == allowed, (
        "errors.json codes must exactly match envelope.schema.json rpc_error/code enum"
    )


def test_errors_registry_retryable_flags(errors_registry: dict[str, Any]) -> None:
    # design §7: mutations never auto-retry. The registry must mark mutations false.
    by_code = {e["code"]: e for e in errors_registry["codes"]}
    assert by_code["TASK_CANCELLED"]["retryable"] is False
    assert by_code["INVALID_REQUEST"]["retryable"] is False
    assert by_code["PROTOCOL_MISMATCH"]["retryable"] is False
    # queries may be retried on worker crash
    assert by_code["WORKER_UNAVAILABLE"]["retryable"] is True
    assert by_code["TASK_TIMEOUT"]["retryable"] is True


# ---------------------------------------------------------------------------
# Negative: malformed / disallowed inputs must be rejected
# ---------------------------------------------------------------------------


def _assert_rejected(validator: Draft202012Validator, instance: Any) -> None:
    with pytest.raises(js_exceptions.ValidationError):
        validator.validate(instance)


def test_unknown_extra_field_request_rejected(
    envelope_validator: Draft202012Validator, golden: dict[str, Any]
) -> None:
    _assert_rejected(envelope_validator, golden["negative"]["unknown_extra_field_request"])


def test_wrong_protocol_version_rejected(
    envelope_validator: Draft202012Validator, golden: dict[str, Any]
) -> None:
    _assert_rejected(envelope_validator, golden["negative"]["wrong_protocol_version"])


def test_missing_request_id_rejected(
    envelope_validator: Draft202012Validator, golden: dict[str, Any]
) -> None:
    _assert_rejected(envelope_validator, golden["negative"]["missing_request_id"])


def test_missing_task_id_rejected(
    envelope_validator: Draft202012Validator, golden: dict[str, Any]
) -> None:
    _assert_rejected(envelope_validator, golden["negative"]["missing_task_id"])


def test_malformed_uuid_rejected(
    envelope_validator: Draft202012Validator, golden: dict[str, Any]
) -> None:
    _assert_rejected(envelope_validator, golden["negative"]["malformed_uuid"])


def test_response_both_result_and_error_rejected(
    envelope_validator: Draft202012Validator, golden: dict[str, Any]
) -> None:
    _assert_rejected(envelope_validator, golden["negative"]["response_both_result_and_error"])


def test_response_neither_result_nor_error_rejected(
    envelope_validator: Draft202012Validator, golden: dict[str, Any]
) -> None:
    _assert_rejected(
        envelope_validator, golden["negative"]["response_neither_result_nor_error"]
    )


def test_invalid_shared_payload_bad_sha_rejected(
    envelope_validator: Draft202012Validator,
    methods_schema: dict[str, Any],
    envelope_schema: dict[str, Any],
    golden: dict[str, Any],
) -> None:
    case = golden["negative"]["invalid_shared_payload_bad_sha"]
    # Envelope-level shape is fine; the method payload's shared-payload descriptor
    # is what must be rejected (bad sha256 length).
    envelope_validator.validate(case)
    with pytest.raises(js_exceptions.ValidationError):
        _validate_method_payload(
            methods_schema, "ocr.recognize", "request", case["payload"], envelope_schema
        )


def test_invalid_shared_payload_bad_name_rejected(
    envelope_validator: Draft202012Validator,
    methods_schema: dict[str, Any],
    envelope_schema: dict[str, Any],
    golden: dict[str, Any],
) -> None:
    case = golden["negative"]["invalid_shared_payload_bad_name"]
    envelope_validator.validate(case)
    with pytest.raises(js_exceptions.ValidationError):
        _validate_method_payload(
            methods_schema, "ocr.recognize", "request", case["payload"], envelope_schema
        )


def test_unknown_error_code_rejected(
    envelope_validator: Draft202012Validator, golden: dict[str, Any]
) -> None:
    _assert_rejected(envelope_validator, golden["negative"]["unknown_error_code"])


# ---------------------------------------------------------------------------
# Method allow-listing: method dispatch must reject unknown methods.
# This is enforced by the methods schema (no additional methods) and the
# dispatcher in Task 1.5.
# ---------------------------------------------------------------------------


def test_unknown_method_not_in_allowlist(methods_schema: dict[str, Any]) -> None:
    assert "evil.eval" not in methods_schema["properties"]


def test_methods_schema_no_additional_methods(methods_schema: dict[str, Any]) -> None:
    # The allow-list is closed: adding a new method is a contract change.
    assert methods_schema.get("additionalProperties") is False
