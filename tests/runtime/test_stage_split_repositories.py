from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from scripts.stage_split_repositories import SPECS, stage_repository

if TYPE_CHECKING:
    from pathlib import Path


def test_all_staging_sources_exist() -> None:
    from scripts.stage_split_repositories import ROOT

    missing = [
        f"{name}: {relative}"
        for name, spec in SPECS.items()
        for relative in spec.paths
        if not (ROOT / relative).exists()
    ]
    assert not missing


def test_protocol_staging_has_required_migration_files(tmp_path: Path) -> None:
    root = stage_repository(
        "protocol",
        tmp_path / "vibeocr-protocol",
        cutover_commit="a" * 40,
    )
    for name in (
        "README.md",
        "LICENSE",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "MIGRATION.md",
        "repository.json",
    ):
        assert (root / name).is_file()
    assert "a" * 40 in (root / "MIGRATION.md").read_text(encoding="utf-8")
    workflow = (root / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "    environment: release\n" in workflow
    assert "subject-checksums: artifacts/SHA256SUMS" in workflow
    build_script = (root / "scripts/build-release.ps1").read_text(encoding="utf-8")
    assert "Repository-specific release builder has not been generated" not in build_script
    assert "build_protocol_release_manifest.py" in build_script


def test_staging_refuses_nonempty_destination(tmp_path: Path) -> None:
    root = tmp_path / "occupied"
    root.mkdir()
    (root / "keep.txt").write_text("user data", encoding="utf-8")
    with pytest.raises(FileExistsError):
        stage_repository("protocol", root, cutover_commit="a" * 40)
    assert (root / "keep.txt").read_text(encoding="utf-8") == "user data"


def test_backend_staging_has_locked_runtime_builder(tmp_path: Path) -> None:
    root = stage_repository(
        "backend",
        tmp_path / "vibeocr-backend",
        cutover_commit="b" * 40,
    )
    script = (root / "scripts/build-release.ps1").read_text(encoding="utf-8")
    assert "Repository-specific release builder has not been generated" not in script
    assert "release/protocol.lock.json" in script
    lock = (
        root / "release/python-runtime.lock.json"
    ).read_text(encoding="utf-8")
    assert "5b4093f92d9bffcb0d92aea050f3d77d" in lock


def test_next_staging_uses_released_protocol_packages(tmp_path: Path) -> None:
    root = stage_repository(
        "next",
        tmp_path / "vibeocr-next",
        cutover_commit="c" * 40,
    )
    app = (
        root / "src/dotnet/VibeOCR.App/VibeOCR.App.csproj"
    ).read_text(encoding="utf-8")
    platform = (
        root / "src/dotnet/VibeOCR.Platform/VibeOCR.Platform.csproj"
    ).read_text(encoding="utf-8")
    versions = (root / "Directory.Packages.props").read_text(encoding="utf-8")
    assert "../VibeOCR.Contracts/" not in app
    assert "../VibeOCR.Runtime.Client/" not in platform
    assert '<PackageReference Include="VibeOCR.Runtime.Contracts" />' in app
    assert '<PackageReference Include="VibeOCR.Runtime.Client" />' in platform
    assert 'Version="[2.0.0]"' in versions
    build_script = (root / "scripts/build-release.ps1").read_text(encoding="utf-8")
    assert "Repository-specific release builder has not been generated" not in build_script
    assert "scripts/updater_main.py" in build_script
    assert "updater.exe" in build_script
    assert "dotnet publish" in build_script
    assert "--locked-mode" in build_script
    assert "verify_winui_artifact.ps1" in build_script
    workflow = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    for capability in (
        "export.document.v1",
        "ocr.recognition.v2",
        "pdf.edit.v2",
        "qrcode.v2",
        "runtime.settings.v2",
    ):
        assert capability in workflow


def test_classic_staging_excludes_local_venv_and_builds_verified_updater(
    tmp_path: Path,
) -> None:
    root = stage_repository(
        "classic",
        tmp_path / "vibeocr-classic",
        cutover_commit="d" * 40,
    )
    assert not (root / "apps/vibeocr-pyside/.venv").exists()
    assert (root / "tests/fakes").is_dir()
    assert (root / "tests/conftest.py").is_file()
    assert (root / "tests/qt_responsiveness.py").is_file()
    build_script = (root / "scripts/build-release.ps1").read_text(encoding="utf-8")
    assert "scripts/updater_main.py" in build_script
    assert "updater.exe" in build_script
    assert "verify_pyside_artifact.py" in build_script
    ci = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "python -m pytest" in ci
    assert "./scripts/build-release.ps1" in ci
