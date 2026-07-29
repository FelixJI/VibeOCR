from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

import pytest

from scripts.build_protocol_release_manifest import (
    build_protocol_release_manifest,
)

if TYPE_CHECKING:
    from pathlib import Path


EXPECTED_ARTIFACT_NAMES = {
    "vibeocr-runtime-openapi-2.0.0.yaml",
    "vibeocr-runtime-schemas-2.0.0.zip",
    "vibeocr_runtime_contracts-2.0.0-py3-none-any.whl",
    "vibeocr_runtime_client-2.0.0-py3-none-any.whl",
    "VibeOCR.Runtime.Contracts.2.0.0.nupkg",
    "VibeOCR.Runtime.Client.2.0.0.nupkg",
    "vibeocr-runtime-golden-2.0.0.zip",
    "SBOM.spdx.json",
}


def _write_artifacts(root: Path, names: set[str]) -> tuple[Path, ...]:
    root.mkdir()
    artifacts = []
    for name in sorted(names):
        path = root / name
        path.write_bytes(name.encode())
        artifacts.append(path)
    return tuple(artifacts)


def _build(root: Path, output: Path) -> Path:
    return build_protocol_release_manifest(
        protocol_version="2.0.0",
        source_commit="a" * 40,
        build_workflow="tests/protocol-release",
        artifacts=_write_artifacts(root, EXPECTED_ARTIFACT_NAMES),
        output_dir=output,
    )


def test_protocol_release_manifest_is_deterministic_and_complete(
    tmp_path: Path,
) -> None:
    first = _build(tmp_path / "first-input", tmp_path / "first-output")
    second = _build(tmp_path / "second-input", tmp_path / "second-output")
    assert first.read_bytes() == second.read_bytes()
    assert (first.parent / "SHA256SUMS").read_bytes() == (
        second.parent / "SHA256SUMS"
    ).read_bytes()
    value = json.loads(first.read_text(encoding="utf-8"))
    assert value["protocol_version"] == "2.0.0"
    assert set(value["artifacts"]) == EXPECTED_ARTIFACT_NAMES
    for name, record in value["artifacts"].items():
        assert (
            hashlib.sha256((first.parent / name).read_bytes()).hexdigest()
            == (record["sha256"])
        )


def test_protocol_release_manifest_rejects_duplicate_names(tmp_path: Path) -> None:
    first_root = tmp_path / "one"
    second_root = tmp_path / "two"
    first_root.mkdir()
    second_root.mkdir()
    first = first_root / "same.whl"
    second = second_root / "same.whl"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    with pytest.raises(ValueError, match="unique"):
        build_protocol_release_manifest(
            protocol_version="2.0.0",
            source_commit="a" * 40,
            build_workflow="tests",
            artifacts=(first, second),
            output_dir=tmp_path / "output",
        )


def test_protocol_release_manifest_rejects_missing_asset(tmp_path: Path) -> None:
    names = EXPECTED_ARTIFACT_NAMES - {"SBOM.spdx.json"}
    with pytest.raises(ValueError, match=r"missing: SBOM\.spdx\.json"):
        build_protocol_release_manifest(
            protocol_version="2.0.0",
            source_commit="a" * 40,
            build_workflow="tests",
            artifacts=_write_artifacts(tmp_path / "input", names),
            output_dir=tmp_path / "output",
        )


def test_protocol_release_manifest_rejects_unexpected_asset(tmp_path: Path) -> None:
    names = EXPECTED_ARTIFACT_NAMES | {"release-notes.txt"}
    with pytest.raises(ValueError, match=r"unexpected: release-notes\.txt"):
        build_protocol_release_manifest(
            protocol_version="2.0.0",
            source_commit="a" * 40,
            build_workflow="tests",
            artifacts=_write_artifacts(tmp_path / "input", names),
            output_dir=tmp_path / "output",
        )


def test_protocol_release_manifest_rejects_other_version_asset(
    tmp_path: Path,
) -> None:
    names = EXPECTED_ARTIFACT_NAMES - {"vibeocr-runtime-openapi-2.0.0.yaml"} | {
        "vibeocr-runtime-openapi-1.9.0.yaml"
    }
    with pytest.raises(
        ValueError,
        match=(
            r"missing: vibeocr-runtime-openapi-2\.0\.0\.yaml; "
            r"unexpected: vibeocr-runtime-openapi-1\.9\.0\.yaml"
        ),
    ):
        build_protocol_release_manifest(
            protocol_version="2.0.0",
            source_commit="a" * 40,
            build_workflow="tests",
            artifacts=_write_artifacts(tmp_path / "input", names),
            output_dir=tmp_path / "output",
        )
