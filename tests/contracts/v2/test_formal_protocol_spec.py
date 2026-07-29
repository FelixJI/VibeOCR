"""Formal Runtime API v2 specification and deterministic bindings tests."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import typing
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[3]
V2 = ROOT / "packages/vibeocr-contracts-py/src/vibeocr/runtime_contracts"
METHODS = {"get", "put", "post", "delete", "patch", "head", "options", "trace"}


def _spec() -> dict:
    # JSON is a YAML 1.2 subset; keeping it JSON makes this formal .yaml
    # document independently parseable without adding a PyYAML build dependency.
    return json.loads((V2 / "openapi.yaml").read_text(encoding="utf-8"))


def _operations(spec: dict) -> list[tuple[str, str, dict]]:
    return [
        (method, path, operation)
        for path, item in spec["paths"].items()
        for method, operation in item.items()
        if method in METHODS
    ]


def test_formal_spec_is_openapi_31_with_real_35_operation_surface() -> None:
    spec = _spec()
    operations = _operations(spec)
    assert spec["openapi"] == "3.1.0"
    assert len(operations) == 35
    assert {(method.upper(), path) for method, path, _ in operations} == {
        ("GET", "/v2/health"),
        ("POST", "/v2/jobs"),
        ("GET", "/v2/jobs/{job_id}/observe"),
        ("POST", "/v2/jobs/command"),
        ("GET", "/v2/runtime/residency"),
        ("POST", "/v2/runtime/release"),
        ("POST", "/v2/runtime/preload"),
        ("GET", "/v2/settings"),
        ("PUT", "/v2/settings"),
        ("POST", "/v2/export"),
        ("POST", "/v2/qrcode/decode"),
        ("POST", "/v2/qrcode/generate"),
        ("POST", "/v2/pdf/sessions/open"),
        ("POST", "/v2/pdf/sessions/{session_id}/close"),
        ("POST", "/v2/pdf/sessions/{session_id}/model"),
        ("POST", "/v2/pdf/sessions/{session_id}/load"),
        ("GET", "/v2/pdf/sessions/{session_id}/render"),
        ("POST", "/v2/pdf/sessions/{session_id}/render_thumbnail"),
        ("POST", "/v2/pdf/sessions/{session_id}/render_preview"),
        ("POST", "/v2/pdf/sessions/{session_id}/detect_text_layers"),
        ("POST", "/v2/pdf/sessions/{session_id}/rotate"),
        ("POST", "/v2/pdf/sessions/{session_id}/delete_pages"),
        ("POST", "/v2/pdf/sessions/{session_id}/insert_blank"),
        ("POST", "/v2/pdf/sessions/{session_id}/insert_from"),
        ("POST", "/v2/pdf/sessions/{session_id}/move_page"),
        ("POST", "/v2/pdf/sessions/{session_id}/reorder"),
        ("POST", "/v2/pdf/sessions/{session_id}/add_text_layer"),
        ("POST", "/v2/pdf/sessions/{session_id}/add_text_layer_batch"),
        ("POST", "/v2/pdf/sessions/{session_id}/rewrite_text_layer"),
        ("POST", "/v2/pdf/sessions/{session_id}/update_block_text"),
        ("POST", "/v2/pdf/sessions/{session_id}/delete_text_layers"),
        ("POST", "/v2/pdf/sessions/{session_id}/save"),
        ("POST", "/v2/pdf/sessions/{session_id}/save_transactional"),
        ("POST", "/v2/pdf/sessions/{session_id}/cancel"),
        ("POST", "/v2/pdf/sessions/{session_id}/reset_cancel"),
    }


def test_operations_have_unique_ids_and_explicit_success_error_content() -> None:
    spec = _spec()
    operations = _operations(spec)
    ids = [operation["operationId"] for _, _, operation in operations]
    assert len(ids) == len(set(ids))
    for _, _, operation in operations:
        assert operation["responses"]
        assert any(
            "content" in response or "$ref" in response
            for response in operation["responses"].values()
        )
        assert any(
            "$ref" in response and response["$ref"].endswith("/Error")
            for response in operation["responses"].values()
        ) or operation["operationId"] in {"getSettings", "getRuntimeResidency"}


def test_multipart_binary_ndjson_and_ready_schema_are_explicit() -> None:
    spec = _spec()
    assert (
        "multipart/form-data"
        in spec["paths"]["/v2/jobs"]["post"]["requestBody"]["content"]
    )
    assert "image/png" in spec["components"]["responses"]["Png"]["content"]
    assert (
        "application/x-ndjson" in spec["components"]["responses"]["Ndjson"]["content"]
    )
    bootstrap = json.loads((V2 / "bootstrap.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(bootstrap)
    jsonschema.validate(
        {
            "ready": True,
            "pid": 1,
            "port": 1,
            "instance_id": "x",
            "protocol_version": 2,
            "schema_version": 2,
            "ready_version": 1,
            "capabilities": ["ocr.recognition.v2"],
        },
        bootstrap,
    )


def test_capabilities_are_versioned_and_generated_bindings_are_current() -> None:
    registry = json.loads((V2 / "capabilities.json").read_text(encoding="utf-8"))
    capabilities = registry["capabilities"]
    assert all(item.rsplit(".v", 1)[-1].isdigit() for item in capabilities)
    assert set(registry["definitions"]) == set(capabilities)
    assert all(item["description"].strip() for item in registry["definitions"].values())
    result = subprocess.run(
        [sys.executable, "scripts/generate_runtime_protocol.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_all_local_refs_resolve_and_permissive_catch_all_schemas_are_gone() -> None:
    spec = _spec()
    assert "RuntimeObject" not in spec["components"]["schemas"]
    assert "PdfJson" not in spec["components"]["responses"]

    def walk(value: object) -> None:
        if isinstance(value, dict):
            ref = value.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/"):
                target: object = spec
                for part in ref.removeprefix("#/").split("/"):
                    assert isinstance(target, dict)
                    target = target[part]
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(spec)
    for _, _, operation in _operations(spec):
        success = operation["responses"]["200"]
        if "$ref" in success:
            success = spec["components"]["responses"][
                success["$ref"].rsplit("/", 1)[-1]
            ]
        assert any(media.get("schema") for media in success.get("content", {}).values())
        if "requestBody" in operation:
            request = operation["requestBody"]
            if "$ref" in request:
                request = spec["components"]["requestBodies"][
                    request["$ref"].rsplit("/", 1)[-1]
                ]
            assert any(media.get("schema") for media in request["content"].values())


def test_security_and_error_registry_are_explicit() -> None:
    spec = _spec()
    health = spec["paths"]["/v2/health"]["get"]
    assert health["security"] == []
    assert "401" not in health["responses"]
    assert "403" in health["responses"]
    for _, _, operation in _operations(spec):
        if operation is not health:
            assert {"401", "403"} <= operation["responses"].keys()

    registry = json.loads((V2 / "errors.json").read_text(encoding="utf-8"))
    assert spec["components"]["schemas"]["Error"]["properties"]["code"]["enum"] == [
        entry["code"] for entry in registry["codes"]
    ]


def test_generated_python_wire_models_are_strict() -> None:
    from vibeocr.runtime_contracts.generated import (
        RuntimeHealthEnvelope,
        RuntimeReadyEnvelope,
    )

    ready = RuntimeReadyEnvelope.from_payload(
        {
            "ready": True,
            "pid": 1,
            "port": 2,
            "instance_id": "instance",
            "protocol_version": 2,
            "schema_version": 2,
            "ready_version": 1,
            "capabilities": ["ocr.recognition.v2"],
        }
    )
    assert ready.port == 2
    health = RuntimeHealthEnvelope.from_payload(
        {
            "schema_version": 2,
            "instance_id": "instance",
            "protocol_version": 2,
            "ready": True,
            "draining": False,
            "capabilities": ["pdf.edit.v2"],
        }
    )
    assert health.to_payload()["capabilities"] == ["pdf.edit.v2"]
    with pytest.raises(ValueError):
        RuntimeReadyEnvelope.from_payload(
            {
                "ready": 1,
                "pid": 1,
                "port": 2,
                "instance_id": "instance",
                "protocol_version": 2,
                "schema_version": 2,
                "ready_version": 1,
                "capabilities": [],
            }
        )


def test_codegen_covers_wire_dtos_errors_and_operation_signatures() -> None:
    from vibeocr.runtime_contracts.generated import (
        ERROR_REGISTRY,
        OPERATIONS,
        RuntimeErrorCode,
        wire_types,
    )

    spec = _spec()
    object_schemas = {
        name
        for name, schema in spec["components"]["schemas"].items()
        if isinstance(schema.get("properties"), dict)
    }
    assert all(hasattr(wire_types, name) for name in object_schemas)
    assert typing.get_type_hints(wire_types.Health)["protocol_version"] is not None
    assert len(OPERATIONS) == 35
    assert len({operation.operation_id for operation in OPERATIONS}) == 35
    registry = json.loads((V2 / "errors.json").read_text(encoding="utf-8"))
    assert {code.value for code in RuntimeErrorCode} == {
        entry["code"] for entry in registry["codes"]
    }
    assert set(ERROR_REGISTRY) == set(RuntimeErrorCode)

    csharp_protocol = (V2 / "generated/RuntimeProtocol.g.cs").read_text(
        encoding="utf-8"
    )
    csharp_wire = (V2 / "generated/RuntimeWireTypes.g.cs").read_text(encoding="utf-8")
    assert "RuntimeErrorCode" in csharp_protocol
    assert "RuntimeOperation" in csharp_protocol
    assert all(f"record {name}" in csharp_wire for name in object_schemas)


def test_runtime_multipart_ndjson_binary_and_error_goldens_validate() -> None:
    spec = _spec()
    golden = json.loads((V2 / "golden/runtime-api.json").read_text(encoding="utf-8"))
    bootstrap = json.loads((V2 / "bootstrap.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(golden["bootstrap_ready"], bootstrap)

    def validate_component(name: str, value: object) -> None:
        schema = {
            "$ref": f"#/components/schemas/{name}",
            "components": spec["components"],
        }
        jsonschema.Draft202012Validator(schema).validate(value)

    validate_component("Health", golden["health"])
    validate_component("SubmitRequest", golden["multipart_manifest"])
    validate_component("Error", golden["unauthorized_error"])
    for event in golden["ndjson_progress_lines"]:
        validate_component("ProgressEvent", event)
    png = base64.b64decode(golden["png"]["content_base64"], validate=True)
    assert golden["png"]["media_type"] == "image/png"
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
