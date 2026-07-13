"""Tests for the framework-dependent WinUI release layout rules.

These pin the artifact-verifier contract (no self-contained runtime, no
duplicate WebView2 SDK, no PySide6 UI, no dev profile, no output/test dirs)
without requiring an actual dotnet publish.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

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
    (root / "VibeOCR.WinUI.exe").write_bytes(b"")
    (root / "VibeOCR.Bootstrapper.exe").write_bytes(b"")
    (root / "runtime").mkdir(exist_ok=True)
    (root / "models").mkdir(exist_ok=True)
    (root / "config").mkdir(exist_ok=True)
    for entry in forbidden or []:
        target = root / entry
        target.mkdir(parents=True, exist_ok=True)


def test_clean_layout_passes(tmp_path: Path) -> None:
    root = tmp_path / "release"
    _build_layout(root)
    code, output = _run_verifier(root)
    assert code == 0, output


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
