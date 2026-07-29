"""Tests for the legacy pipeline_ttls -> v2 residency migration."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from vibeocr.backend.migration.residency_migration import (
    convert_legacy_pipeline_ttls,
    migrate_settings_file,
)

# ---------------------------------------------------------------------------
# Pure conversion
# ---------------------------------------------------------------------------


def test_empty_dict_yields_empty_pipelines() -> None:
    assert convert_legacy_pipeline_ttls({}) == []
    assert convert_legacy_pipeline_ttls(None) == []


def test_positive_ttl_becomes_finite_ttl() -> None:
    out = convert_legacy_pipeline_ttls({"OCR": 120, "MinerU": 600})
    assert {"name": "OCR", "ttl_seconds": 120, "pinned": False} in out
    assert {"name": "MinerU", "ttl_seconds": 600, "pinned": False} in out


def test_zero_ttl_for_unknown_pipeline_inherits() -> None:
    # Plan §8: 0 was ambiguous; for unknown pipelines we conservatively map
    # to inherit (ttl_seconds=null, pinned=false), never to pin.
    out = convert_legacy_pipeline_ttls({"OCR": 0})
    assert out == [{"name": "OCR", "ttl_seconds": None, "pinned": False}]


def test_negative_ttl_is_treated_as_inherit() -> None:
    out = convert_legacy_pipeline_ttls({"OCR": -5})
    assert out == [{"name": "OCR", "ttl_seconds": None, "pinned": False}]


def test_legacy_zero_never_silently_becomes_pin_for_unknown_pipeline() -> None:
    # This is the key safety property the ADR calls out: the new schema must
    # not turn an ambiguous 0 into a hard pin that could block a running task.
    out = convert_legacy_pipeline_ttls({"WeirdPipeline": 0})
    assert out[0]["pinned"] is False


# ---------------------------------------------------------------------------
# File migration (idempotent + backup)
# ---------------------------------------------------------------------------


def _write_legacy(tmp_path: Path, ttls: dict | None = None) -> Path:
    settings = tmp_path / "settings.json"
    data = {"backend": "gpu", "pipeline_ttls": ttls or {}}
    if ttls is None:
        data = {"backend": "cpu"}
    settings.write_text(json.dumps(data), encoding="utf-8")
    return settings


def test_migrate_writes_residency_and_backs_up_original(tmp_path: Path) -> None:
    settings = _write_legacy(tmp_path, {"OCR": 300, "MinerU": 0})
    result = migrate_settings_file(settings, default_ttl_seconds=300)

    assert result.migrated is True
    assert result.backed_up_to is not None
    assert result.backed_up_to.exists()
    # Backup retains the original legacy content.
    backup = json.loads(result.backed_up_to.read_text(encoding="utf-8"))
    assert backup["pipeline_ttls"] == {"OCR": 300, "MinerU": 0}

    # Migrated file has the new residency schema and no legacy field.
    migrated = json.loads(settings.read_text(encoding="utf-8"))
    assert "pipeline_ttls" not in migrated
    assert migrated["schema_version"] == 2
    res = migrated["residency"]
    assert res["default_ttl_seconds"] == 300
    names = {p["name"]: p for p in res["pipelines"]}
    assert names["OCR"]["ttl_seconds"] == 300
    assert names["MinerU"]["ttl_seconds"] is None
    assert names["MinerU"]["pinned"] is False


def test_migrate_preserves_unrelated_keys(tmp_path: Path) -> None:
    settings = _write_legacy(tmp_path, {"OCR": 60})
    # Add an unrelated key the migration must not touch.
    data = json.loads(settings.read_text(encoding="utf-8"))
    data["theme"] = "dark"
    settings.write_text(json.dumps(data), encoding="utf-8")

    migrate_settings_file(settings)
    migrated = json.loads(settings.read_text(encoding="utf-8"))
    assert migrated["theme"] == "dark"
    assert migrated["backend"] == "gpu"


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    settings = _write_legacy(tmp_path, {"OCR": 120})

    first = migrate_settings_file(settings)
    assert first.migrated is True

    # Second run must be a no-op: no new backup, migrated=False.
    second = migrate_settings_file(settings)
    assert second.migrated is False
    assert second.backed_up_to is None
    # Content unchanged.
    migrated = json.loads(settings.read_text(encoding="utf-8"))
    assert migrated["residency"]["default_ttl_seconds"] == 300


def test_migrate_raises_on_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        migrate_settings_file(tmp_path / "nope.json")


def test_migrate_handles_file_without_pipeline_ttls(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"backend": "cpu"}), encoding="utf-8")

    result = migrate_settings_file(settings)
    assert result.migrated is True
    migrated = json.loads(settings.read_text(encoding="utf-8"))
    assert migrated["residency"]["pipelines"] == []
