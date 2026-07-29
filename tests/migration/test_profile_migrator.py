"""Tests for the idempotent profile/config migrator."""

from __future__ import annotations

import json
from pathlib import Path

from vibeocr.backend.migration.profile_migrator import (
    CURRENT_SCHEMA_VERSION,
    migrate_config,
    migrate_profile,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "migration"


def _copy_fixture(name: str, dest: Path) -> Path:
    dest.write_bytes((FIXTURES / name).read_bytes())
    return dest


def test_migrate_unversioned_adds_schema_version_and_backup(tmp_path: Path) -> None:
    path = _copy_fixture("v0-unversioned-complete.json", tmp_path / "app_settings.json")
    original = path.read_bytes()

    result = migrate_config(path)

    assert result.status == "migrated"
    assert result.schema_version == CURRENT_SCHEMA_VERSION
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    # Unknown fields preserved.
    assert data["hotkeys"]["global_screenshot"] == "Ctrl+Alt+Q"
    assert data["max_heavy_pipelines"] == 1
    # Backup written and matches the original bytes.
    assert result.backup_path is not None
    backup = Path(result.backup_path)
    assert backup.exists()
    assert backup.read_bytes() == original


def test_second_run_is_already_migrated_noop(tmp_path: Path) -> None:
    path = _copy_fixture("v0-unversioned-minimal.json", tmp_path / "app_settings.json")
    migrate_config(path)
    snapshot = path.read_bytes()

    result = migrate_config(path)

    assert result.status == "already_migrated"
    # File untouched on the second run.
    assert path.read_bytes() == snapshot


def test_missing_file_is_skipped(tmp_path: Path) -> None:
    result = migrate_config(tmp_path / "absent.json")
    assert result.status == "skipped"
    assert "not found" in result.message


def test_newer_schema_version_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "app_settings.json"
    path.write_text(json.dumps({"schema_version": 99, "x": 1}), encoding="utf-8")

    result = migrate_config(path)

    assert result.status == "skipped"
    assert "newer" in result.message
    # File untouched.
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 99


def test_corrupt_json_is_skipped_without_writing(tmp_path: Path) -> None:
    path = tmp_path / "app_settings.json"
    path.write_text("{ not valid json", encoding="utf-8")

    result = migrate_config(path)

    assert result.status == "skipped"
    assert path.read_text(encoding="utf-8") == "{ not valid json"


def test_readonly_file_does_not_corrupt_original(tmp_path: Path) -> None:
    path = _copy_fixture("v0-unversioned-minimal.json", tmp_path / "app_settings.json")
    original = path.read_bytes()
    try:
        path.chmod(0o444)
        result = migrate_config(path)
        # On read-only media the write fails but the original is intact.
        assert result.status in ("skipped", "migrated")
    finally:
        path.chmod(0o644)
    # Either way the file is valid JSON (migrated) or byte-identical (skipped).
    if result.status == "skipped":
        assert path.read_bytes() == original


def test_two_runs_byte_identical_after_first(tmp_path: Path) -> None:
    path = _copy_fixture("v0-unversioned-complete.json", tmp_path / "app_settings.json")
    migrate_config(path)
    first = path.read_bytes()
    migrate_config(path)
    migrate_config(path)
    assert path.read_bytes() == first


def test_migrate_profile_returns_one_result_per_target(tmp_path: Path) -> None:
    _copy_fixture("v0-unversioned-minimal.json", tmp_path / "app_settings.json")
    results = migrate_profile(tmp_path)
    assert len(results) == 1
    assert results[0].status == "migrated"


def test_real_fixtures_load() -> None:
    """The checked-in fixtures must be valid JSON of the expected shape."""
    minimal = json.loads((FIXTURES / "v0-unversioned-minimal.json").read_text(encoding="utf-8"))
    assert "schema_version" not in minimal
    current = json.loads((FIXTURES / "v1-current.json").read_text(encoding="utf-8"))
    assert current["schema_version"] == CURRENT_SCHEMA_VERSION


def test_write_hashed_backup_returns_none_when_read_fails(tmp_path: Path, monkeypatch) -> None:
    """源文件读取失败时 _write_hashed_backup 返回 None 并记日志（line 72-74）。"""
    from vibeocr.backend.migration import profile_migrator

    path = tmp_path / "app_settings.json"
    path.write_text("{}", encoding="utf-8")

    def _raise_oserror(_p):
        raise OSError("read denied")

    monkeypatch.setattr(Path, "read_bytes", _raise_oserror)
    result = profile_migrator._write_hashed_backup(path)
    assert result is None


def test_write_hashed_backup_returns_none_when_write_fails(tmp_path: Path, monkeypatch) -> None:
    """备份文件写入失败时返回 None（line 80-82）。"""
    from vibeocr.backend.migration import profile_migrator

    path = tmp_path / "app_settings.json"
    path.write_text("{}", encoding="utf-8")

    original_write = Path.write_bytes

    def _fail_write(self, _data):
        # 只让备份文件写入失败；源文件读取仍正常
        if ".pre-migrate-" in self.name:
            raise OSError("write denied")
        return original_write(self, _data)

    monkeypatch.setattr(Path, "write_bytes", _fail_write)
    result = profile_migrator._write_hashed_backup(path)
    assert result is None


def test_migrate_skips_non_object_json(tmp_path: Path) -> None:
    """顶层 JSON 不是对象（如数组/数字）时跳过迁移（line 96）。"""
    path = tmp_path / "app_settings.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    result = migrate_config(path)

    assert result.status == "skipped"
    assert "not a JSON object" in (result.message or "")
