"""PipelineCacheManager 单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

from vibeocr.services.pipeline_cache_manager import (
    DEFAULT_TTL_SECONDS,
    FALLBACK_MAX_HEAVY,
    VRAM_TIER_6GB,
    VRAM_TIER_12GB,
    PipelineCacheManager,
    compute_max_heavy_by_vram,
)

# --- compute_max_heavy_by_vram ---


def test_compute_max_heavy_under_6gb():
    """≤6G 显存 → 上限 1。"""
    assert compute_max_heavy_by_vram(4096) == 1  # 4G
    assert compute_max_heavy_by_vram(6144) == 1  # 6G


def test_compute_max_heavy_under_12gb():
    """6G < 显存 ≤ 12G → 上限 2。"""
    assert compute_max_heavy_by_vram(8192) == 2  # 8G
    assert compute_max_heavy_by_vram(12288) == 2  # 12G


def test_compute_max_heavy_over_12gb():
    """>12G 显存 → 上限 3。"""
    assert compute_max_heavy_by_vram(16384) == 3  # 16G
    assert compute_max_heavy_by_vram(24576) == 3  # 24G


def test_compute_max_heavy_zero_vram_returns_default():
    """显存读取失败（0）→ 回退默认 2。"""
    assert compute_max_heavy_by_vram(0) == FALLBACK_MAX_HEAVY


def test_tier_constants():
    """分档阈值常量正确。"""
    assert VRAM_TIER_6GB == 6144
    assert VRAM_TIER_12GB == 12288


def test_default_ttl_is_300():
    """默认 TTL 为 300 秒（5 分钟）。"""
    assert DEFAULT_TTL_SECONDS == 300


# --- 辅助构造 ---


def _make_manager(max_heavy: int = 2, ttl: int = 300) -> PipelineCacheManager:
    """构造测试用 manager（mock service，固定 max_heavy）。"""
    service = MagicMock()
    service._pipelines = {}
    return PipelineCacheManager(service, ttl_seconds=ttl, max_heavy=max_heavy)


# --- enforce_capacity (FIFO) ---


def test_enforce_capacity_no_eviction_when_under_limit():
    """未超上限时不淘汰。"""
    mgr = _make_manager(max_heavy=2)
    mgr._service._pipelines = {"PP-StructureV3": object()}
    mgr._last_used = {"PP-StructureV3": 100.0}
    evicted = mgr.enforce_capacity("PaddleOCR-VL", now=200.0)
    assert evicted == []


def test_enforce_capacity_evicts_oldest():
    """超上限时淘汰 last_used 最早的。"""
    mgr = _make_manager(max_heavy=1)
    mgr._service._pipelines = {"PP-StructureV3": object()}
    mgr._last_used = {"PP-StructureV3": 100.0}
    evicted = mgr.enforce_capacity("PaddleOCR-VL", now=200.0)
    assert evicted == ["PP-StructureV3"]
    assert "PP-StructureV3" not in mgr._service._pipelines
    assert "PP-StructureV3" not in mgr._last_used


def test_enforce_capacity_skips_non_heavy():
    """淘汰只针对重管道，不动 OCR。"""
    mgr = _make_manager(max_heavy=1)
    mgr._service._pipelines = {"OCR": object(), "PP-StructureV3": object()}
    mgr._last_used = {"OCR": 50.0, "PP-StructureV3": 100.0}
    evicted = mgr.enforce_capacity("PaddleOCR-VL", now=200.0)
    assert evicted == ["PP-StructureV3"]
    assert "OCR" in mgr._service._pipelines  # OCR 保留


def test_enforce_capacity_does_not_evict_new_pipeline():
    """不淘汰正在加载的 new_pipeline（即使它已在缓存里）。"""
    mgr = _make_manager(max_heavy=1)
    mgr._service._pipelines = {"PP-StructureV3": object(), "PaddleOCR-VL": object()}
    mgr._last_used = {"PP-StructureV3": 100.0, "PaddleOCR-VL": 90.0}
    evicted = mgr.enforce_capacity("PaddleOCR-VL", now=200.0)
    # VL 是 new_pipeline，不被淘汰；淘汰 PP-V3（更旧）
    assert "PaddleOCR-VL" not in evicted
    assert "PP-StructureV3" in evicted


# --- evict_idle (TTL) ---


def test_evict_idle_releases_expired_heavy():
    """闲置超 TTL 的重管道被回收。"""
    mgr = _make_manager(max_heavy=3)
    mgr._service._pipelines = {"PP-StructureV3": object(), "OCR": object()}
    mgr._last_used = {"PP-StructureV3": 100.0, "OCR": 100.0}
    # now=500，PP-V3 last_used 100 + 300 = 400 < 500 → 过期
    evicted = mgr.evict_idle(now=500.0)
    assert evicted == ["PP-StructureV3"]
    assert "PP-StructureV3" not in mgr._service._pipelines
    assert "OCR" in mgr._service._pipelines  # OCR 不受 TTL


def test_evict_idle_keeps_recent():
    """未超 TTL 的保留。"""
    mgr = _make_manager(max_heavy=3)
    mgr._service._pipelines = {"PP-StructureV3": object()}
    mgr._last_used = {"PP-StructureV3": 300.0}
    # now=500，300 + 300 = 600 > 500 → 未过期
    evicted = mgr.evict_idle(now=500.0)
    assert evicted == []
    assert "PP-StructureV3" in mgr._service._pipelines


def test_evict_idle_ttl_zero_disables():
    """TTL=0 禁用回收。"""
    mgr = _make_manager(max_heavy=3, ttl=0)
    mgr._service._pipelines = {"PP-StructureV3": object()}
    mgr._last_used = {"PP-StructureV3": 0.0}
    evicted = mgr.evict_idle(now=99999.0)
    assert evicted == []


# --- release ---


def test_release_heavy_only_keeps_ocr():
    """release(heavy_only=True) 只释放重管道。"""
    mgr = _make_manager(max_heavy=3)
    mgr._service._pipelines = {
        "PP-StructureV3": object(),
        "PaddleOCR-VL": object(),
        "OCR": object(),
    }
    mgr._last_used = {
        "PP-StructureV3": 100.0,
        "PaddleOCR-VL": 200.0,
        "OCR": 300.0,
    }
    released = mgr.release(heavy_only=True)
    assert set(released) == {"PP-StructureV3", "PaddleOCR-VL"}
    assert "OCR" in mgr._service._pipelines


def test_release_all_includes_ocr():
    """release(heavy_only=False) 释放全部。"""
    mgr = _make_manager(max_heavy=3)
    mgr._service._pipelines = {
        "PP-StructureV3": object(),
        "OCR": object(),
    }
    mgr._last_used = {"PP-StructureV3": 100.0, "OCR": 200.0}
    released = mgr.release(heavy_only=False)
    assert set(released) == {"PP-StructureV3", "OCR"}
    assert len(mgr._service._pipelines) == 0


# --- touch / ttl setter ---


def test_touch_records_timestamp():
    """touch 记录使用时间。"""
    mgr = _make_manager()
    mgr.touch("PP-StructureV3", now=123.0)
    assert mgr.get_last_used("PP-StructureV3") == 123.0


def test_ttl_setter_clamps_to_zero():
    """ttl_seconds 不接受负值，夹到 0。"""
    mgr = _make_manager()
    mgr.ttl_seconds = -10
    assert mgr.ttl_seconds == 0
