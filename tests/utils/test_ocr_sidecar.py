# tests/utils/test_ocr_sidecar.py
import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from vibeocr.utils.ocr_sidecar import (
    SIDECAR_VERSION,
    compute_fingerprint,
    sidecar_path,
    load_sidecar,
    save_sidecar,
    mark_pages_saved,
    mark_completed,
    restore_pending_pages,
    refresh_baseline,
)


def _bump_mtime(path: Path, extra_bytes: int = 100) -> None:
    """模拟 incremental save：append 字节（size 增长）并显式推高 mtime。

    显式 os.utime 是为了避免某些平台 mtime 分辨率过粗导致两次写落在同一 ns。
    """
    with open(path, "ab") as fh:
        fh.write(b"X" * extra_bytes)
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))


def test_compute_fingerprint_uses_size_and_mtime(tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"hello")
    fp = compute_fingerprint(str(f))
    size, mtime = fp.split(":")
    assert size == "5"
    assert int(mtime) > 0


def test_sidecar_path_is_path_slug_under_vibeocr_cache(tmp_path):
    """sidecar 文件名按规范化绝对路径的 md5 命名（不按指纹）。"""
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"x")
    p = sidecar_path(str(f))
    assert p.parent.name == "ocr_sessions"
    assert p.parent.parent.name == ".vibeocr"
    assert p.suffix == ".json"
    # 文件名 = md5(abspath)，32 位 hex
    expected = hashlib.md5(os.path.abspath(str(f)).encode("utf-8")).hexdigest()
    assert p.stem == expected


def test_sidecar_path_stable_across_file_changes(tmp_path):
    """文件内容/大小变化时 sidecar_path 不变（路径键稳定）—— 修复核心 bug。"""
    f = tmp_path / "stable.pdf"
    f.write_bytes(b"abc")
    before = sidecar_path(str(f))
    _bump_mtime(f, extra_bytes=500)
    after = sidecar_path(str(f))
    assert before == after


def test_mark_pages_saved_merges_into_existing(tmp_path, monkeypatch):
    f = tmp_path / "d.pdf"
    f.write_bytes(b"abc")
    monkeypatch.setattr(
        "vibeocr.utils.ocr_sidecar._sessions_dir", lambda: tmp_path / "sessions"
    )
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


# ---- 关键回归测试：增量保存增长不得失效 sidecar ----


def test_incremental_save_growth_keeps_sidecar_valid(tmp_path, monkeypatch):
    """核心回归：OCR 增量保存 append 字节后，下一批 mark_pages_saved 仍读到
    上一批累积的页记录（不返回 None→空 sidecar→丢批次）。

    这是修复的主要 bug：旧实现按 size:mtime 指纹命名/校验，incremental save
    改变二者 → load_sidecar 返回 None → _new_sidecar → 旧批次丢失。
    """
    f = tmp_path / "grow.pdf"
    f.write_bytes(b"baseline-pdf")
    monkeypatch.setattr(
        "vibeocr.utils.ocr_sidecar._sessions_dir", lambda: tmp_path / "sessions"
    )
    # 批 1
    assert mark_pages_saved(str(f), [0, 1], {0: 0, 1: 0}) is True
    # 模拟批 1 的 incremental save（append + mtime 增长）
    _bump_mtime(f, extra_bytes=200)
    # 批 2：此时 load_sidecar 应仍读到批 1 的页
    assert mark_pages_saved(str(f), [2], {2: 90}) is True
    data = load_sidecar(str(f))
    assert data is not None
    assert set(data["pages"].keys()) == {"0", "1", "2"}
    # restore 仍能拿到全部 3 页
    assert restore_pending_pages(str(f)) == {0: 0, 1: 0, 2: 90}


def test_file_shrink_invalidates_sidecar(tmp_path, monkeypatch):
    """文件被替换/缩小（用户换文件、回退版本）→ sidecar 失效返回 None。"""
    f = tmp_path / "shrink.pdf"
    f.write_bytes(b"longer-baseline-content-here")
    monkeypatch.setattr(
        "vibeocr.utils.ocr_sidecar._sessions_dir", lambda: tmp_path / "sessions"
    )
    mark_pages_saved(str(f), [0], {0: 0})
    # 用户用更小的文件替换（size 变小）—— 模拟回退/换文件
    st = os.stat(f)
    f.write_bytes(b"short")  # 5 字节 < 28 字节
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000))
    assert load_sidecar(str(f)) is None
    assert restore_pending_pages(str(f)) is None


def test_file_older_mtime_invalidates_sidecar(tmp_path, monkeypatch):
    """mtime 回退（文件被旧版本覆盖，size 不变或更大）→ 失效。"""
    f = tmp_path / "older.pdf"
    f.write_bytes(b"same-size-content")
    monkeypatch.setattr(
        "vibeocr.utils.ocr_sidecar._sessions_dir", lambda: tmp_path / "sessions"
    )
    mark_pages_saved(str(f), [0], {0: 0})
    st = os.stat(f)
    # 同 size，但 mtime 回拨到更早
    f.write_bytes(b"same-size-content")
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns - 10_000_000_000))  # -10s
    assert load_sidecar(str(f)) is None


def test_refresh_baseline_after_compression(tmp_path, monkeypatch):
    """6C 全量压缩后文件变小，refresh_baseline 把基线刷新到压缩后状态，
    随后 load_sidecar/mark_completed 才不会因 size < original 失效。"""
    f = tmp_path / "compress.pdf"
    f.write_bytes(b"bloated-" * 50)  # 大基线
    monkeypatch.setattr(
        "vibeocr.utils.ocr_sidecar._sessions_dir", lambda: tmp_path / "sessions"
    )
    mark_pages_saved(str(f), [0, 1], {0: 0, 1: 0})
    # 模拟 6C 全量压缩重写：文件显著变小（实际场景子集字体合并 + deflate）
    st_before = os.stat(f)
    f.write_bytes(b"small")
    os.utime(f, ns=(st_before.st_atime_ns, st_before.st_mtime_ns + 3_000_000))
    # 未 refresh 前 load_sidecar 失效
    assert load_sidecar(str(f)) is None
    # refresh_baseline 修复
    assert refresh_baseline(str(f)) is True
    # 现在 load_sidecar 重新有效
    data = load_sidecar(str(f))
    assert data is not None
    assert set(data["pages"].keys()) == {"0", "1"}
    # 紧接着 mark_completed 也能成功（不再被增长校验拦截）
    assert mark_completed(str(f)) is True
    assert load_sidecar(str(f))["completed"] is True


def test_refresh_baseline_missing_sidecar(tmp_path, monkeypatch):
    """无 sidecar 时 refresh_baseline 返回 False（不抛异常）。"""
    f = tmp_path / "none.pdf"
    f.write_bytes(b"x")
    monkeypatch.setattr(
        "vibeocr.utils.ocr_sidecar._sessions_dir", lambda: tmp_path / "sessions"
    )
    assert refresh_baseline(str(f)) is False
