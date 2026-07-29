"""Protocol-v2 release binding tests using minimal real wheel archives."""

from __future__ import annotations

import json
import sys
import zipfile
from typing import TYPE_CHECKING

import pytest

from scripts import bind_backend_artifact

if TYPE_CHECKING:
    from pathlib import Path

VERSION = "1.2.3"
PROTOCOL_VERSION = "2.0.0"
WHEEL_DISTRIBUTIONS = {
    "vibeocr-backend": "vibeocr_backend",
    "vibeocr-classic": "vibeocr_classic",
    "vibeocr-runtime-contracts": "vibeocr_runtime_contracts",
    "vibeocr-runtime-client": "vibeocr_runtime_client",
}


def _write_wheel(
    wheel_dir: Path,
    distribution: str,
    wheel_stem: str,
    *,
    version: str = VERSION,
    extra_members: tuple[str, ...] = (),
) -> Path:
    wheel = wheel_dir / f"{wheel_stem}-{version}-py3-none-any.whl"
    metadata_dir = f"{wheel_stem}-{version}.dist-info"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            f"{metadata_dir}/METADATA",
            (f"Metadata-Version: 2.4\nName: {distribution}\nVersion: {version}\n\n"),
        )
        for member in extra_members:
            archive.writestr(member, "fixture")
    return wheel


def _write_wheel_set(wheel_dir: Path) -> dict[str, Path]:
    wheel_dir.mkdir()
    wheels: dict[str, Path] = {}
    for distribution, wheel_stem in WHEEL_DISTRIBUTIONS.items():
        members: tuple[str, ...] = ()
        if distribution == "vibeocr-backend":
            members = ("vibeocr/backend/supervisor/main.py",)
        elif distribution == "vibeocr-runtime-contracts":
            members = ("vibeocr/runtime_contracts/golden/golden.json",)
        elif distribution == "vibeocr-runtime-client":
            members = ("vibeocr/runtime_client/client.py",)
        elif distribution == "vibeocr-classic":
            members = ("vibeocr/classic/main.py",)
        wheels[distribution] = _write_wheel(
            wheel_dir,
            distribution,
            wheel_stem,
            version=(
                PROTOCOL_VERSION
                if distribution in bind_backend_artifact.PROTOCOL_WHEELS
                else VERSION
            ),
            extra_members=members,
        )
    return wheels


def test_binding_writes_protocol_v2_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheel_dir = tmp_path / "wheels"
    _write_wheel_set(wheel_dir)
    frontend = tmp_path / "frontend.zip"
    with zipfile.ZipFile(frontend, "w") as archive:
        archive.writestr("VibeOCR/VibeOCR.exe", "fixture")
    output = tmp_path / "bound.zip"
    monkeypatch.setattr(
        bind_backend_artifact.subprocess,
        "check_output",
        lambda *args, **kwargs: "0" * 40,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bind_backend_artifact.py",
            "--frontend",
            "pyside",
            "--version",
            VERSION,
            "--input",
            str(frontend),
            "--wheel-dir",
            str(wheel_dir),
            "--output",
            str(output),
        ],
    )

    assert bind_backend_artifact.main() == 0
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(
            archive.read("VibeOCR/product-manifest.json").decode("utf-8")
        )
    assert manifest["protocol_major"] == 2
    assert manifest["protocol_version"] == PROTOCOL_VERSION
    assert manifest["frontend"] == "pyside"
    assert len(manifest["python_wheels"]) == 4


def test_binding_rejects_legacy_runtime_member(tmp_path: Path) -> None:
    wheels = _write_wheel_set(tmp_path / "wheels")
    backend = wheels["vibeocr-backend"]
    with zipfile.ZipFile(backend, "a") as archive:
        archive.writestr("vibeocr/worker_host/main.py", "legacy")

    with pytest.raises(RuntimeError, match="legacy runtime paths"):
        bind_backend_artifact._verify_runtime_layout(wheels)
