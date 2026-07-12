# tests/utils/test_ocr_sidecar.py
import json
from pathlib import Path
from unittest.mock import patch

from vibeocr.utils.ocr_sidecar import (
    SIDECAR_VERSION,
    compute_fingerprint,
    sidecar_path,
    load_sidecar,
    save_sidecar,
    mark_pages_saved,
    mark_completed,
    restore_pending_pages,
)


def test_compute_fingerprint_uses_size_and_mtime(tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"hello")
    fp = compute_fingerprint(str(f))
    size, mtime = fp.split(":")
    assert size == "5"
    assert int(mtime) > 0


def test_sidecar_path_under_vibeocr_cache(tmp_path):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"x")
    p = sidecar_path(str(f))
    # 位于 .vibeocr/ocr_sessions/ 下，文件名含指纹
    assert p.parent.name == "ocr_sessions"
    assert p.parent.parent.name == ".vibeocr"
    assert p.suffix == ".json"


def test_mark_pages_saved_merges_into_existing(tmp_path, monkeypatch):
    f = tmp_path / "d.pdf"
    f.write_bytes(b"abc")
    # 重定向 sidecar 目录到 tmp，避免污染真实 .vibeocr
    monkeypatch.setattr(
        "vibeocr.utils.ocr_sidecar._sessions_dir", lambda: tmp_path / "sessions"
    )
    # 第一批
    assert mark_pages_saved(str(f), [0, 1], {0: 0, 1: 90}) is True
    data = load_sidecar(str(f))
    assert data["completed"] is False
    assert data["pages"] == {"0": {"has_text_layer": True, "ocr_preproc_angle": 0},
                              "1": {"has_text_layer": True, "ocr_preproc_angle": 90}}
    # 第二批合并
    assert mark_pages_saved(str(f), [2], {2: 0}) is True
    data = load_sidecar(str(f))
    assert set(data["pages"].keys()) == {"0", "1", "2"}


def test_mark_completed_sets_flag(tmp_path, monkeypatch):
    f = tmp_path / "d.pdf"
    f.write_bytes(b"abc")
    monkeypatch.setattr(
        "vibeocr.utils.ocr_sidecar._sessions_dir", lambda: tmp_path / "sessions"
    )
    mark_pages_saved(str(f), [0], {0: 0})
    assert mark_completed(str(f)) is True
    assert load_sidecar(str(f))["completed"] is True


def test_restore_pending_pages_returns_dict_when_incomplete(tmp_path, monkeypatch):
    f = tmp_path / "d.pdf"
    f.write_bytes(b"abc")
    monkeypatch.setattr(
        "vibeocr.utils.ocr_sidecar._sessions_dir", lambda: tmp_path / "sessions"
    )
    mark_pages_saved(str(f), [0, 2], {0: 0, 2: 90})
    result = restore_pending_pages(str(f))
    assert result == {0: 0, 2: 90}


def test_restore_pending_pages_none_when_completed(tmp_path, monkeypatch):
    f = tmp_path / "d.pdf"
    f.write_bytes(b"abc")
    monkeypatch.setattr(
        "vibeocr.utils.ocr_sidecar._sessions_dir", lambda: tmp_path / "sessions"
    )
    mark_pages_saved(str(f), [0], {0: 0})
    mark_completed(str(f))
    assert restore_pending_pages(str(f)) is None


def test_restore_pending_pages_none_when_fingerprint_mismatch(tmp_path, monkeypatch):
    f = tmp_path / "d.pdf"
    f.write_bytes(b"abc")
    monkeypatch.setattr(
        "vibeocr.utils.ocr_sidecar._sessions_dir", lambda: tmp_path / "sessions"
    )
    mark_pages_saved(str(f), [0], {0: 0})
    f.write_bytes(b"changed-content")  # 改动文件 → 指纹变
    assert restore_pending_pages(str(f)) is None
