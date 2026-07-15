"""Tests for the framework-dependent WinUI release layout rules.

These pin the artifact-verifier contract (no self-contained runtime, no
duplicate WebView2 SDK, no PySide6 UI, no dev profile, no output/test dirs)
without requiring an actual dotnet publish.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]
VERIFIER = REPO_ROOT / "scripts" / "verify_winui_artifact.ps1"


def _run_verifier(root: Path) -> tuple[int, str]:
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-File", str(VERIFIER), "-Artifact", str(root)],
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
        "product-manifest.json",
        "contracts/v1/golden.json",
        "CHANGELOG.md",
        "LICENSE",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"release-content")
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
