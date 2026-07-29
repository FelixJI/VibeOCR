"""Offline OpenAPI lint and backward-compatibility gate tests."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

from scripts.check_openapi_quality import (
    detect_breaking_changes,
    lint_document,
)

ROOT = Path(__file__).resolve().parents[3]
FORMAL_OPENAPI = (
    ROOT / "packages/vibeocr-contracts-py/src/vibeocr/runtime_contracts/openapi.yaml"
)
RELEASE_BASELINE = (
    ROOT
    / "packages/vibeocr-contracts-py/src/vibeocr/runtime_contracts/baselines"
    / "openapi-2.0.0.yaml"
)


def _schema(properties: dict, required: list[str]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
    }


def _document() -> dict:
    return {
        "openapi": "3.1.0",
        "info": {"title": "Compatibility fixture", "version": "1.0.0"},
        "paths": {
            "/v2/items": {
                "post": {
                    "operationId": "createItem",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/CreateItem"}
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "created",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Item"}
                                }
                            },
                        },
                        "400": {"$ref": "#/components/responses/Error"},
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "CreateItem": _schema(
                    {
                        "name": {"type": "string"},
                        "count": {"type": "integer"},
                    },
                    ["name"],
                ),
                "Item": _schema(
                    {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                    },
                    ["id", "name"],
                ),
                "Error": _schema({"message": {"type": "string"}}, ["message"]),
            },
            "responses": {
                "Error": {
                    "description": "error",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/Error"}
                        }
                    },
                }
            },
        },
    }


def test_formal_openapi_passes_dependency_free_lint() -> None:
    document = json.loads(FORMAL_OPENAPI.read_text(encoding="utf-8"))
    assert lint_document(document) == []


def test_formal_openapi_is_compatible_with_immutable_release_baseline() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/check_openapi_quality.py",
            "--baseline",
            str(RELEASE_BASELINE),
            "--current",
            str(FORMAL_OPENAPI),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "OpenAPI quality gate: OK" in result.stdout


def test_lint_rejects_broken_refs_duplicate_operation_ids_and_invalid_schema() -> None:
    document = _document()
    document["paths"]["/v2/other"] = copy.deepcopy(document["paths"]["/v2/items"])
    document["components"]["schemas"]["CreateItem"]["required"].append("missing")
    document["components"]["schemas"]["Item"]["properties"]["id"] = {
        "$ref": "#/components/schemas/Absent"
    }
    document["paths"]["/v2/items"]["post"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["type"] = "invalid"

    errors = lint_document(document)

    assert any("duplicate 'createItem'" in error for error in errors)
    assert any("target does not exist" in error for error in errors)
    assert any("unknown properties missing" in error for error in errors)
    assert any("schema has an invalid type" in error for error in errors)


def test_breaking_gate_detects_removed_path_and_method() -> None:
    baseline = _document()
    without_method = copy.deepcopy(baseline)
    without_method["paths"]["/v2/items"] = {}
    without_path = copy.deepcopy(baseline)
    without_path["paths"] = {}

    assert "POST /v2/items: operation was removed" in detect_breaking_changes(
        baseline, without_method
    )
    assert "POST /v2/items: operation was removed" in detect_breaking_changes(
        baseline, without_path
    )


def test_breaking_gate_detects_new_required_request_field_and_type_change() -> None:
    baseline = _document()
    current = copy.deepcopy(baseline)
    request_schema = current["components"]["schemas"]["CreateItem"]
    request_schema["required"].append("count")
    request_schema["properties"]["name"]["type"] = "integer"

    issues = detect_breaking_changes(baseline, current)

    assert any(
        "request application/json.count: request field became required" in issue
        for issue in issues
    )
    assert any(
        "request application/json.name: type changed" in issue for issue in issues
    )


def test_breaking_gate_detects_removed_response_status_and_field() -> None:
    baseline = _document()
    current = copy.deepcopy(baseline)
    del current["paths"]["/v2/items"]["post"]["responses"]["400"]
    del current["components"]["schemas"]["Item"]["properties"]["name"]
    current["components"]["schemas"]["Item"]["required"].remove("name")

    issues = detect_breaking_changes(baseline, current)

    assert "POST /v2/items: response status 400 was removed" in issues
    assert any(
        "response 200 application/json.name: response field was removed" in issue
        for issue in issues
    )


def test_cli_returns_nonzero_for_breaking_baseline_current_pair(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    current = tmp_path / "current.json"
    baseline.write_text(json.dumps(_document()), encoding="utf-8")
    changed = copy.deepcopy(_document())
    changed["components"]["schemas"]["Item"]["properties"]["id"]["type"] = "integer"
    current.write_text(json.dumps(changed), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/check_openapi_quality.py",
            "--baseline",
            str(baseline),
            "--current",
            str(current),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "BREAKING" in result.stderr
    assert "type changed" in result.stderr


def test_cli_accepts_compatible_baseline_current_pair(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    current = tmp_path / "current.json"
    baseline.write_text(json.dumps(_document()), encoding="utf-8")
    changed = copy.deepcopy(_document())
    changed["components"]["schemas"]["Item"]["properties"]["description"] = {
        "type": "string"
    }
    current.write_text(json.dumps(changed), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/check_openapi_quality.py",
            "--baseline",
            str(baseline),
            "--current",
            str(current),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "OpenAPI quality gate: OK" in result.stdout
