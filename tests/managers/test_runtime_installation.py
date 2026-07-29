from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from vibeocr.classic.runtime_installation import (
    RuntimeInstallerClient,
    RuntimeInstallerClientError,
)


def _bound_client(tmp_path: Path, *, executable_name: str = "renamed.exe"):
    executable = tmp_path / executable_name
    executable.write_bytes(b"installer")
    manifest = tmp_path / "runtime-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "installer": {
                    "executable_sha256": hashlib.sha256(b"installer").hexdigest()
                }
            }
        ),
        encoding="utf-8",
    )
    lock = tmp_path / "component-lock.json"
    lock.write_text(
        json.dumps(
            {
                "backend": {
                    "runtime_manifest_sha256": hashlib.sha256(
                        manifest.read_bytes()
                    ).hexdigest()
                }
            }
        ),
        encoding="utf-8",
    )
    return RuntimeInstallerClient(
        tmp_path,
        component_lock=lock,
        runtime_manifest=manifest,
        command=(str(executable),),
    )


def test_renamed_installer_still_requires_full_binding(tmp_path: Path) -> None:
    client = _bound_client(tmp_path)
    client._verify_installer_executable()
    Path(client.command[0]).write_bytes(b"tampered")
    with pytest.raises(RuntimeInstallerClientError, match="SHA-256"):
        client._verify_installer_executable()


def test_manifest_tamper_is_rejected_before_executable(tmp_path: Path) -> None:
    client = _bound_client(tmp_path)
    client.runtime_manifest.write_text("{}\n", encoding="utf-8")
    with pytest.raises(RuntimeInstallerClientError, match="无法验证"):
        client._verify_installer_executable()


def test_explicit_layout_environment_is_forwarded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "portable-layout.json"
    marker.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("VIBEOCR_PORTABLE_LAYOUT", str(marker))
    client = RuntimeInstallerClient(
        tmp_path / "classic",
        command=("python", "-m", "vibeocr.backend.runtime_installer"),
    )
    arguments = client._arguments("inspect")
    assert arguments[arguments.index("--layout-manifest") + 1] == str(marker.resolve())
