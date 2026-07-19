"""Tests for the framework-dependent WinUI release layout rules.

These pin the artifact-verifier contract (no self-contained runtime, no
duplicate WebView2 SDK, no PySide6 UI, no dev profile, no output/test dirs)
without requiring an actual dotnet publish.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]
VERIFIER = REPO_ROOT / "scripts" / "verify_winui_artifact.ps1"
FIXTURE_VERSION = "0.0.0"
RUNTIME_WHEELS = (
    f"vibeocr_contracts_py-{FIXTURE_VERSION}-py3-none-any.whl",
    f"vibeocr_client_py-{FIXTURE_VERSION}-py3-none-any.whl",
    f"vibeocr_backend-{FIXTURE_VERSION}-py3-none-any.whl",
)


def _run_verifier(root: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(VERIFIER),
            "-Artifact",
            str(root),
        ],
        capture_output=True,
    )
    stdout = (proc.stdout or b"").decode("utf-8", errors="replace")
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
    return proc.returncode, stdout + stderr


def _build_layout(root: Path, *, forbidden: list[str] | None = None) -> None:
    """Create a clean minimal layout plus any forbidden entries."""
    root.mkdir(parents=True, exist_ok=True)
    for relative in (
        "VibeOCR.WinUI.exe",
        "VibeOCR.Bootstrapper.exe",
        "updater.exe",
        "VibeOCR.WinUI.dll",
        "VibeOCR.Contracts.dll",
        "VibeOCR.Platform.dll",
        "worker/vibeocr/worker_host/main.py",
        "contracts/v1/golden.json",
        "CHANGELOG.md",
        "LICENSE",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"release-content")

    wheel_records = []
    for name in RUNTIME_WHEELS:
        wheel = root / "backend" / name
        wheel.parent.mkdir(parents=True, exist_ok=True)
        content = f"fixture:{name}".encode()
        wheel.write_bytes(content)
        wheel_records.append(
            {"file": name, "sha256": hashlib.sha256(content).hexdigest()}
        )

    backend_record = wheel_records[-1]
    manifest = {
        "frontend": "winui",
        "frontend_version": FIXTURE_VERSION,
        "backend_wheel": backend_record["file"],
        "backend_sha256": backend_record["sha256"],
        "python_wheels": wheel_records,
        "protocol_major": 1,
        "source_commit": "0" * 40,
    }
    (root / "product-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    for entry in forbidden or []:
        target = root / entry
        target.mkdir(parents=True, exist_ok=True)


def test_clean_layout_passes(tmp_path: Path) -> None:
    root = tmp_path / "release"
    _build_layout(root)
    code, output = _run_verifier(root)
    assert code == 0, output


def test_missing_required_entry_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "release"
    _build_layout(root)
    (root / "VibeOCR.Bootstrapper.exe").unlink()
    code, output = _run_verifier(root)
    assert code == 1, output
    assert "required release file missing" in output


def test_empty_required_entry_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "release"
    _build_layout(root)
    (root / "VibeOCR.WinUI.exe").write_bytes(b"")
    code, output = _run_verifier(root)
    assert code == 1, output
    assert "required release file is empty" in output


def test_legacy_ui_entry_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "release"
    _build_layout(root)
    (root / "worker/vibeocr/main.py").write_text("legacy", encoding="utf-8")
    code, output = _run_verifier(root)
    assert code == 1, output
    assert "legacy PySide UI entry" in output


def test_pyside6_modules_rejected(tmp_path: Path) -> None:
    root = tmp_path / "release"
    _build_layout(root, forbidden=["runtime/lib/PySide6"])
    code, output = _run_verifier(root)
    assert code == 1, output
    assert "PySide6" in output


def test_dev_profile_rejected(tmp_path: Path) -> None:
    root = tmp_path / "release"
    _build_layout(root, forbidden=["data/profiles/winui-dev"])
    code, output = _run_verifier(root)
    assert code == 1, output
    assert "dev profile" in output


def test_output_directory_rejected(tmp_path: Path) -> None:
    root = tmp_path / "release"
    _build_layout(root, forbidden=["output"])
    code, output = _run_verifier(root)
    assert code == 1, output
    assert "output" in output or "build/test" in output


def test_self_contained_runtime_rejected(tmp_path: Path) -> None:
    root = tmp_path / "release"
    _build_layout(root)
    (root / "System.Private.CoreLib.dll").write_bytes(b"")
    code, output = _run_verifier(root)
    assert code == 1, output
    assert "self-contained" in output


def test_invalid_product_manifest_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "release"
    _build_layout(root)
    (root / "product-manifest.json").write_text("not-json", encoding="utf-8")
    code, output = _run_verifier(root)
    assert code == 1, output
    assert "product manifest is invalid JSON" in output


def test_runtime_wheel_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "release"
    _build_layout(root)
    (root / "backend" / RUNTIME_WHEELS[0]).write_bytes(b"tampered")
    code, output = _run_verifier(root)
    assert code == 1, output
    assert "bound runtime wheel hash mismatch" in output
