"""JSON-Schema validation of the v2 golden payloads and negative cases.

Uses the frozen ``schemas/*.schema.json`` shipped with the contracts package.
This is the "schema positive/negative" portion of the Phase 1 exit criterion.
"""

from __future__ import annotations

import copy
import json
from importlib import resources

import jsonschema
import pytest


def _load(name: str) -> dict:
    return json.loads(
        resources.files("vibeocr.runtime_contracts.schemas").joinpath(name).read_text(encoding="utf-8")
    )


def _golden() -> dict:
    return json.loads(
        resources.files("vibeocr.runtime_contracts.golden").joinpath("golden.json").read_text(encoding="utf-8")
    )


JOB_SCHEMA = _load("job.schema.json")
ERROR_SCHEMA = _load("errors.schema.json")
JOB_INTERFACE_SCHEMA = _load("job-interface.schema.json")


# ---------------------------------------------------------------------------
# Positive cases — golden payloads must validate.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    ["job_snapshot_running", "job_snapshot_completed_with_errors", "job_snapshot_cancelled"],
)
def test_job_schema_accepts_golden(key: str) -> None:
    jsonschema.validate(_golden()[key], JOB_SCHEMA)


@pytest.mark.parametrize("key", ["error_validation", "error_oom", "error_cancelled"])
def test_error_schema_accepts_golden(key: str) -> None:
    jsonschema.validate(_golden()[key], ERROR_SCHEMA)


# ---------------------------------------------------------------------------
# Negative cases — broken payloads must be rejected by the schema.
# ---------------------------------------------------------------------------


def _running() -> dict:
    return copy.deepcopy(_golden()["job_snapshot_running"])


def test_job_schema_rejects_unknown_state() -> None:
    payload = _running()
    payload["state"] = "bogus"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, JOB_SCHEMA)


def test_job_schema_rejects_additional_property() -> None:
    payload = _running()
    payload["secret_internal_field"] = 42
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, JOB_SCHEMA)


def test_job_schema_rejects_wrong_schema_version() -> None:
    payload = _running()
    payload["schema_version"] = 1
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, JOB_SCHEMA)


def test_job_schema_rejects_missing_required_field() -> None:
    payload = _running()
    del payload["state"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, JOB_SCHEMA)


def test_job_schema_rejects_negative_progress() -> None:
    payload = _running()
    payload["progress_current"] = -1
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, JOB_SCHEMA)


def test_job_item_rejects_unknown_state() -> None:
    payload = _running()
    payload["items"][0]["state"] = "weird"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, JOB_SCHEMA)


def test_error_schema_rejects_unknown_code() -> None:
    payload = copy.deepcopy(_golden()["error_validation"])
    payload["code"] = "FAKE_CODE"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, ERROR_SCHEMA)


def test_error_schema_accepts_category_code_combo() -> None:
    # NOTE: JSON Schema cannot express a code→category cross-check, so the
    # schema only enforces that both fields are *valid enum values*. The
    # runtime parser (see test_v2_contracts.test_parse_rejects_error_category_mismatch)
    # enforces that the combination matches the authoritative errors.json.
    payload = copy.deepcopy(_golden()["error_cancelled"])
    payload["category"] = "validation"  # valid enum value on its own
    jsonschema.validate(payload, ERROR_SCHEMA)


def test_job_interface_schema_accepts_submit_manifest() -> None:
    payload = {
        "schema_version": 2,
        "request_id": "req-1",
        "kind": "recognition",
        "priority": "background",
        "pipeline": {
            "pipeline_id": "OCR",
            "options_version": 1,
            "options": {"use_doc_orientation_classify": False},
        },
        "items": [
            {
                "client_item_key": "file-a",
                "ordinal": 0,
                "display_name": "a.png",
                "source": {"type": "upload.v1", "attachment": "file-a"},
            }
        ],
        "parameters": {},
    }
    jsonschema.validate(
        payload, {"$ref": "#/$defs/SubmitRequest", **JOB_INTERFACE_SCHEMA}
    )


def test_job_interface_schema_rejects_empty_success_outcome() -> None:
    payload = {
        "item_id": "it-1",
        "state": "succeeded",
        "attempt": 0,
        "payload_type": None,
        "payload": None,
        "error_code": None,
        "error_detail": {},
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            payload, {"$ref": "#/$defs/ItemOutcome", **JOB_INTERFACE_SCHEMA}
        )


# ---------------------------------------------------------------------------
# Errors.json registry must agree with the schema enum exactly.
# ---------------------------------------------------------------------------


def test_errors_json_codes_match_schema_enum() -> None:
    registry = json.loads(
        resources.files("vibeocr.runtime_contracts").joinpath("errors.json").read_text(encoding="utf-8")
    )
    registry_codes = {row["code"] for row in registry["codes"]}
    schema_codes = set(ERROR_SCHEMA["properties"]["code"]["enum"])
    assert registry_codes == schema_codes, (
        "errors.json codes and errors.schema.json enum must match exactly"
    )


def test_errors_json_categories_match_schema_enum() -> None:
    registry = json.loads(
        resources.files("vibeocr.runtime_contracts").joinpath("errors.json").read_text(encoding="utf-8")
    )
    registry_categories = {row["category"] for row in registry["codes"]}
    schema_categories = set(ERROR_SCHEMA["properties"]["category"]["enum"])
    assert registry_categories == schema_categories
