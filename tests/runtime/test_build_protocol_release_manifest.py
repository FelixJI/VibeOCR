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


def _build(root: Path, output: Path) -> Path:
    root.mkdir()
    first = root / "vibeocr_runtime_contracts-2.0.0-py3-none-any.whl"
    second = root / "VibeOCR.Runtime.Contracts.2.0.0.nupkg"
    first.write_bytes(b"python")
    second.write_bytes(b"dotnet")
    return build_protocol_release_manifest(
        protocol_version="2.0.0",
        source_commit="a" * 40,
        build_workflow="tests/protocol-release",
        artifacts=(second, first),
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
    for name, record in value["artifacts"].items():
        assert hashlib.sha256((first.parent / name).read_bytes()).hexdigest() == (
            record["sha256"]
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
