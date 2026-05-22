"""tests/test_pipeline_status.py"""
import json
from pathlib import Path

from vibeocr.machine_cache import generate_machine_id
from vibeocr.pipeline_status import (
    mark_pipeline_success,
    is_pipeline_ever_succeeded,
    PIPELINE_NAMES,
)


def _make_cache(tmp_path: Path, pipeline_success: dict | None = None) -> Path:
    cache_file = tmp_path / ".vibeocr" / "cache.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 1,
        "machine_id": generate_machine_id(),
    }
    if pipeline_success is not None:
        data["pipeline_success"] = pipeline_success
    cache_file.write_text(json.dumps(data), encoding="utf-8")
    return cache_file


def test_not_succeeded_when_no_cache(tmp_path):
    assert is_pipeline_ever_succeeded("OCR", tmp_path) is False


def test_not_succeeded_when_field_missing(tmp_path):
    _make_cache(tmp_path)
    assert is_pipeline_ever_succeeded("OCR", tmp_path) is False


def test_not_succeeded_when_false(tmp_path):
    _make_cache(tmp_path, {"OCR": False})
    assert is_pipeline_ever_succeeded("OCR", tmp_path) is False


def test_succeeded_when_true(tmp_path):
    _make_cache(tmp_path, {"OCR": True})
    assert is_pipeline_ever_succeeded("OCR", tmp_path) is True


def test_other_pipeline_unaffected(tmp_path):
    _make_cache(tmp_path, {"OCR": True})
    assert is_pipeline_ever_succeeded("PP-StructureV3", tmp_path) is False


def test_mark_success_creates_field(tmp_path):
    _make_cache(tmp_path)
    mark_pipeline_success("OCR", tmp_path)
    assert is_pipeline_ever_succeeded("OCR", tmp_path) is True


def test_mark_success_preserves_existing(tmp_path):
    _make_cache(tmp_path, {"OCR": True})
    mark_pipeline_success("PP-StructureV3", tmp_path)
    assert is_pipeline_ever_succeeded("OCR", tmp_path) is True
    assert is_pipeline_ever_succeeded("PP-StructureV3", tmp_path) is True


def test_machine_id_mismatch_returns_false(tmp_path):
    cache_file = tmp_path / ".vibeocr" / "cache.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({
        "version": 1,
        "machine_id": "wrong_id",
        "pipeline_success": {"OCR": True},
    }), encoding="utf-8")
    assert is_pipeline_ever_succeeded("OCR", tmp_path) is False


def test_pipeline_names_constant():
    assert "OCR" in PIPELINE_NAMES
    assert "PP-StructureV3" in PIPELINE_NAMES
    assert "PaddleOCR-VL" in PIPELINE_NAMES
    assert "MinerU" not in PIPELINE_NAMES
