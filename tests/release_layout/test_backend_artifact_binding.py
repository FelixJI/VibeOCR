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
WHEEL_DISTRIBUTIONS = {
    "vibeocr": "vibeocr",
    "vibeocr-backend": "vibeocr_backend",
    "vibeocr-client-py": "vibeocr_client_py",
    "vibeocr-contracts-py": "vibeocr_contracts_py",
    "vibeocr-pyside": "vibeocr_pyside",
}


def _write_wheel(
    wheel_dir: Path,
    distribution: str,
    wheel_stem: str,
    *,
    extra_members: tuple[str, ...] = (),
) -> Path:
    wheel = wheel_dir / f"{wheel_stem}-{VERSION}-py3-none-any.whl"
    metadata_dir = f"{wheel_stem}-{VERSION}.dist-info"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            f"{metadata_dir}/METADATA",
            (
                "Metadata-Version: 2.4\n"
                f"Name: {distribution}\n"
                f"Version: {VERSION}\n\n"
            ),
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
            members = ("vibeocr/supervisor/main.py",)
        elif distribution == "vibeocr-contracts-py":
            members = ("vibeocr/protocol/v2/golden/golden.json",)
        wheels[distribution] = _write_wheel(
            wheel_dir,
            distribution,
            wheel_stem,
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
    assert manifest["frontend"] == "pyside"
    assert len(manifest["python_wheels"]) == 5


def test_binding_rejects_legacy_runtime_member(tmp_path: Path) -> None:
    wheels = _write_wheel_set(tmp_path / "wheels")
    backend = wheels["vibeocr-backend"]
    with zipfile.ZipFile(backend, "a") as archive:
        archive.writestr("vibeocr/worker_host/main.py", "legacy")

    with pytest.raises(RuntimeError, match="legacy runtime paths"):
        bind_backend_artifact._verify_runtime_layout(wheels)
