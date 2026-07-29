"""Tests for the framework-dependent WinUI release layout rules.

These pin the artifact-verifier contract (no self-contained runtime, no
duplicate WebView2 SDK, no PySide6 UI, no dev profile, no output/test dirs)
without requiring an actual dotnet publish.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]
VERIFIER = REPO_ROOT / "scripts" / "verify_winui_artifact.ps1"
FIXTURE_VERSION = "0.0.0"
RUNTIME_WHEELS = (
    f"vibeocr_runtime_contracts-{FIXTURE_VERSION}-py3-none-any.whl",
    f"vibeocr_runtime_client-{FIXTURE_VERSION}-py3-none-any.whl",
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
        "VibeOCR.WinUI.pri",
        "VibeOCR.Contracts.dll",
        "VibeOCR.Platform.dll",
        "App.xbf",
        "MainWindow.xbf",
        "Views/AboutPage.xbf",
        "Views/BatchPage.xbf",
        "Views/DiagnosticsPage.xbf",
        "Views/PdfPage.xbf",
        "Views/QrCodePage.xbf",
        "Views/RecognitionPage.xbf",
        "Views/SettingsPage.xbf",
        "runtime-installer/vibeocr-runtime-installer.exe",
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
    installer = root / "runtime-installer/vibeocr-runtime-installer.exe"
    runtime_manifest = {
        "backend_version": FIXTURE_VERSION,
        "backend_wheel": backend_record["file"],
        "backend_sha256": backend_record["sha256"],
        "installer": {
            "executable_sha256": hashlib.sha256(installer.read_bytes()).hexdigest()
        },
    }
    runtime_manifest_path = root / "backend/runtime-manifest.json"
    runtime_manifest_path.write_text(
        json.dumps(runtime_manifest), encoding="utf-8"
    )
    component_lock = {
        "protocol": {
            "repository": "FelixJI/vibeocr-protocol",
            "version": "2.0.0",
            "manifest_sha256": "0" * 64,
        },
        "backend": {
            "repository": "FelixJI/vibeocr-backend",
            "version": FIXTURE_VERSION,
            "artifact_sha256": backend_record["sha256"],
            "runtime_manifest_sha256": hashlib.sha256(
                runtime_manifest_path.read_bytes()
            ).hexdigest(),
        },
        "required_capabilities": [
            "export.document.v1",
            "ocr.recognition.v2",
            "pdf.edit.v2",
            "qrcode.v2",
            "runtime.settings.v2",
        ],
    }
    component_lock_path = root / "component-lock.json"
    component_lock_path.write_text(json.dumps(component_lock), encoding="utf-8")

    files = {}
    for path in root.rglob("*"):
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            content = path.read_bytes()
            files[relative] = {
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
    product_manifest = {
        "frontend": "next",
        "component_lock_sha256": hashlib.sha256(
            component_lock_path.read_bytes()
        ).hexdigest(),
        "files": files,
    }
    (root / "product-release-manifest.json").write_text(
        json.dumps(product_manifest), encoding="utf-8"
    )
    for entry in forbidden or []:
        target = root / entry
        target.mkdir(parents=True, exist_ok=True)


def test_clean_layout_passes(tmp_path: Path) -> None:
    root = tmp_path / "release"
    _build_layout(root)
    code, output = _run_verifier(root)
    assert code == 0, output


def test_clean_zip_layout_passes(tmp_path: Path) -> None:
    root = tmp_path / "release"
    _build_layout(root)
    archive = tmp_path / "release.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for path in root.rglob("*"):
            if path.is_file():
                bundle.write(path, Path("VibeOCR.Next") / path.relative_to(root))
    code, output = _run_verifier(archive)
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


def test_missing_compiled_xaml_resource_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "release"
    _build_layout(root)
    (root / "MainWindow.xbf").unlink()
    code, output = _run_verifier(root)
    assert code == 1, output
    assert "required release file missing" in output
    assert "MainW" in output


def test_legacy_ui_entry_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "release"
    _build_layout(root)
    legacy = root / "worker/vibeocr/main.py"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("legacy", encoding="utf-8")
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
    (root / "product-release-manifest.json").write_text(
        "not-json", encoding="utf-8"
    )
    code, output = _run_verifier(root)
    assert code == 1, output
    assert "product manifest is invalid JSON" in output


def test_incomplete_capability_set_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "release"
    _build_layout(root)
    lock_path = root / "component-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["required_capabilities"].remove("qrcode.v2")
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    code, output = _run_verifier(root)
    assert code == 1, output
    assert "capability set is incomplete" in output


def test_wrong_product_frontend_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "release"
    _build_layout(root)
    manifest_path = root / "product-release-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["frontend"] = "classic"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    code, output = _run_verifier(root)
    assert code == 1, output
    assert "product release manifest frontend" in output


def test_backend_hash_must_match_bound_wheel(tmp_path: Path) -> None:
    root = tmp_path / "release"
    _build_layout(root)
    lock_path = root / "component-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["backend"]["artifact_sha256"] = "f" * 64
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    code, output = _run_verifier(root)
    assert code == 1, output
    assert "bound backend wheel hash mismatch" in output


def test_legacy_backend_layout_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "release"
    _build_layout(root)
    legacy = root / "worker/vibeocr/worker_host/main.py"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("legacy", encoding="utf-8")
    code, output = _run_verifier(root)
    assert code == 1, output
    assert "legacy backend entry present" in output


def test_product_file_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "release"
    _build_layout(root)
    (root / "backend" / RUNTIME_WHEELS[0]).write_bytes(b"tampered")
    code, output = _run_verifier(root)
    assert code == 1, output
    assert "bound product file hash mismatch" in output
