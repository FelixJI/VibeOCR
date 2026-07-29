from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

import pytest

from scripts.bind_component_releases import (
    bind_product_releases,
    bind_protocol_release,
)

if TYPE_CHECKING:
    from pathlib import Path


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _protocol_release(root: Path) -> Path:
    root.mkdir()
    wheel = root / "vibeocr_runtime_contracts-2.0.0-py3-none-any.whl"
    wheel.write_bytes(b"protocol")
    manifest = {
        "protocol_version": "2.0.0",
        "artifacts": {
            wheel.name: {"sha256": _sha(b"protocol"), "size": len(b"protocol")}
        },
    }
    (root / "release-manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    return root


def _backend_release(root: Path) -> Path:
    root.mkdir()
    wheel = root / "vibeocr_backend-0.7.0-py3-none-any.whl"
    wheel.write_bytes(b"backend")
    manifest = {
        "backend_version": "0.7.0",
        "backend_wheel": wheel.name,
        "backend_sha256": _sha(b"backend"),
        "capabilities": ["ocr.recognition.v2", "pdf.edit.v2"],
        "profiles": {"win-x64-cpu": {}},
    }
    (root / "runtime-manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    return root


def test_binds_verified_protocol_manifest(tmp_path: Path) -> None:
    release = _protocol_release(tmp_path / "protocol")
    output = bind_protocol_release(
        release_dir=release,
        repository="FelixJI/vibeocr-protocol",
        version="2.0.0",
        output=tmp_path / "protocol.lock.json",
    )
    lock = json.loads(output.read_text(encoding="utf-8"))
    assert lock["manifest_sha256"] == _sha(
        (release / "release-manifest.json").read_bytes()
    )


def test_rejects_tampered_protocol_asset(tmp_path: Path) -> None:
    release = _protocol_release(tmp_path / "protocol")
    next(release.glob("*.whl")).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        bind_protocol_release(
            release_dir=release,
            repository="FelixJI/vibeocr-protocol",
            version="2.0.0",
            output=tmp_path / "protocol.lock.json",
        )


def test_binds_product_to_exact_backend_and_protocol(tmp_path: Path) -> None:
    protocol = _protocol_release(tmp_path / "protocol")
    backend = _backend_release(tmp_path / "backend")
    output = bind_product_releases(
        protocol_release_dir=protocol,
        backend_release_dir=backend,
        protocol_repository="FelixJI/vibeocr-protocol",
        protocol_version="2.0.0",
        backend_repository="FelixJI/vibeocr-backend",
        backend_version="0.7.0",
        profile="win-x64-cpu",
        required_capabilities=("ocr.recognition.v2",),
        output=tmp_path / "component-lock.json",
    )
    lock = json.loads(output.read_text(encoding="utf-8"))
    assert lock["backend"]["artifact_sha256"] == _sha(b"backend")
    assert lock["required_capabilities"] == ["ocr.recognition.v2"]


def test_product_lock_rejects_missing_capability(tmp_path: Path) -> None:
    protocol = _protocol_release(tmp_path / "protocol")
    backend = _backend_release(tmp_path / "backend")
    with pytest.raises(ValueError, match="missing required capabilities"):
        bind_product_releases(
            protocol_release_dir=protocol,
            backend_release_dir=backend,
            protocol_repository="FelixJI/vibeocr-protocol",
            protocol_version="2.0.0",
            backend_repository="FelixJI/vibeocr-backend",
            backend_version="0.7.0",
            profile="win-x64-cpu",
            required_capabilities=("qrcode.v2",),
            output=tmp_path / "component-lock.json",
        )
