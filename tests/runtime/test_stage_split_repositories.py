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
