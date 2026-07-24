"""tests/test_pipeline_status.py"""

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

from vibeocr.machine_cache import CACHE_VERSION, generate_machine_id
from vibeocr.pipeline_status import (
    LOCAL_MARKABLE_PIPELINES,
    PIPELINE_NAMES,
    is_pipeline_ever_succeeded,
    mark_pipeline_success,
)


def _make_cache(tmp_path: Path, pipeline_success: dict | None = None) -> Path:
    """构造有效缓存（version + machine_id 双重匹配）。

    version 必须用 CACHE_VERSION——收敛到 machine_cache.is_cache_valid 后，
    pipeline_status 的读写在 version 不匹配时会判无效（P1 修复）。
    """
    cache_file = tmp_path / ".vibeocr" / "cache.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": CACHE_VERSION,
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
    cache_file.write_text(
        json.dumps(
            {
                "version": CACHE_VERSION,
                "machine_id": "wrong_id",
                "pipeline_success": {"OCR": True},
            }
        ),
        encoding="utf-8",
    )
    assert is_pipeline_ever_succeeded("OCR", tmp_path) is False


def test_version_mismatch_returns_false(tmp_path):
    """缓存 version 不匹配时 is_pipeline_ever_succeeded 应返回 False。

    回归（P1 修复）：旧 pipeline_status 自行读 cache.json 不校验 version，
    bump CACHE_VERSION 后 pipeline_success 字段仍被当作有效。收敛到
    machine_cache.is_cache_valid 后，version 不匹配即整体无效。
    """
    cache_file = tmp_path / ".vibeocr" / "cache.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(
        json.dumps(
            {
                "version": CACHE_VERSION - 1,  # 旧 version
                "machine_id": generate_machine_id(),
                "pipeline_success": {"OCR": True},
            }
        ),
        encoding="utf-8",
    )
    assert is_pipeline_ever_succeeded("OCR", tmp_path) is False


def test_mark_success_skipped_when_cache_invalid(tmp_path):
    """缓存无效时 mark_pipeline_success 应静默不标记、不创建错位缓存。

    回归（P2 修复）：旧 fallback 会写 ``{"version": 1, ...}`` 污染当前
    CACHE_VERSION。收敛后无效缓存直接跳过，不写任何文件。
    """
    # 无缓存
    mark_pipeline_success("OCR", tmp_path)
    assert not (tmp_path / ".vibeocr" / "cache.json").exists()
    assert is_pipeline_ever_succeeded("OCR", tmp_path) is False


def test_pipeline_names_constant():
    assert "OCR" in PIPELINE_NAMES
    assert "PP-StructureV3" in PIPELINE_NAMES
    assert "PaddleOCR-VL" in PIPELINE_NAMES
    # MinerU 纳入跟踪：首次使用文档解析需下载模型，
    # 标记成功以跳过重复下载（PdfSessionManager 首用 guard 据此判断）
    assert "MinerU" in PIPELINE_NAMES


def test_local_markable_pipelines_excludes_mineru_and_covers_local():
    """LOCAL_MARKABLE_PIPELINES 必须覆盖全部本地推理管道、排除远程 MinerU。

    回归测试：历史上 mark_pipeline_success 的调用方 gating 元组漏掉了
    TABLE_RECOGNITION / FORMULA_RECOGNITION，导致 is_pipeline_ever_succeeded
    对它们永远为 False，每次识别都同步构造 QWebEngineView，表现为"点击表格
    按钮后截图遮罩卡顿"。本测试锁定该契约，防止未来再次遗漏。
    """
    assert "MinerU" not in LOCAL_MARKABLE_PIPELINES  # 远程 API，单独标记
    for name in (
        "OCR",
        "PP-StructureV3",
        "PaddleOCR-VL",
        "TABLE_RECOGNITION",
        "FORMULA_RECOGNITION",
    ):
        assert name in LOCAL_MARKABLE_PIPELINES, (
            f"{name} 缺失会导致其 is_pipeline_ever_succeeded 永久为 False"
        )


# --------------------------------------------------------------------------
# 每日重置：pipeline_success 标记当天有效，跨天视为未成功（重新用长超时验证）
# --------------------------------------------------------------------------
# 防止模型文件被删/损坏/换机器后仍用短超时快速失败。新格式：
#   {name: {"succeeded": bool, "date": "YYYY-MM-DD"}}


def test_mark_success_records_today_and_reads_true_same_day(tmp_path):
    """mark 后当天 is_pipeline_ever_succeeded 应为 True。"""
    _make_cache(tmp_path)
    with patch("vibeocr.pipeline_status._today") as mock_today:
        mock_today.return_value = date(2026, 7, 24)
        mark_pipeline_success("OCR", tmp_path)
        assert is_pipeline_ever_succeeded("OCR", tmp_path) is True


def test_succeeded_marker_expires_next_day(tmp_path):
    """跨天后视为未成功（走长超时重新验证）。

    回归：旧格式 {name: bool} 永久标记，模型损坏后仍用短超时快速失败。
    """
    yesterday = date(2026, 7, 23)
    today = date(2026, 7, 24)
    # 用昨天的日期写入标记
    _make_cache(
        tmp_path,
        {"OCR": {"succeeded": True, "date": yesterday.isoformat()}},
    )
    with patch("vibeocr.pipeline_status._today", return_value=today):
        assert is_pipeline_ever_succeeded("OCR", tmp_path) is False


def test_succeeded_marker_same_day_true(tmp_path):
    """同一天标记成功 → True。"""
    today = date(2026, 7, 24)
    _make_cache(
        tmp_path,
        {"OCR": {"succeeded": True, "date": today.isoformat()}},
    )
    with patch("vibeocr.pipeline_status._today", return_value=today):
        assert is_pipeline_ever_succeeded("OCR", tmp_path) is True


def test_succeeded_marker_false_even_same_day(tmp_path):
    """succeeded=False 即使同一天也为 False。"""
    today = date(2026, 7, 24)
    _make_cache(
        tmp_path,
        {"OCR": {"succeeded": False, "date": today.isoformat()}},
    )
    with patch("vibeocr.pipeline_status._today", return_value=today):
        assert is_pipeline_ever_succeeded("OCR", tmp_path) is False


def test_legacy_bool_format_still_recognized(tmp_path):
    """旧格式 {name: bool} 向后兼容：True 仍返回 True（宽容，避免升级即失效）。

    mark_pipeline_success 后会升级为新格式（带 date）。旧格式仅在尚未被
    重新标记的过渡期出现，宽容返回避免老用户升级后首次识别白白用长超时。
    """
    _make_cache(tmp_path, {"OCR": True})
    with patch("vibeocr.pipeline_status._today", return_value=date(2026, 7, 24)):
        assert is_pipeline_ever_succeeded("OCR", tmp_path) is True


def test_mark_success_upgrades_legacy_to_dated_format(tmp_path):
    """mark 旧格式缓存后，应写入新格式 {succeeded, date}。"""
    _make_cache(tmp_path, {"OCR": True})
    with patch("vibeocr.pipeline_status._today", return_value=date(2026, 7, 24)):
        mark_pipeline_success("OCR", tmp_path)

    cache_file = tmp_path / ".vibeocr" / "cache.json"
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    entry = data["pipeline_success"]["OCR"]
    assert isinstance(entry, dict)
    assert entry["succeeded"] is True
    assert entry["date"] == "2026-07-24"
