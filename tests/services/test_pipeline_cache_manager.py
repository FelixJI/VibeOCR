"""PipelineCacheManager 单元测试。"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

from vibeocr.services.pipeline_cache_manager import (
    FALLBACK_MAX_HEAVY,
    VRAM_TIER_8GB,
    PipelineCacheManager,
    compute_max_heavy_by_vram,
)

# --- compute_max_heavy_by_vram ---


def test_compute_max_heavy_under_8gb():
    """≤8G 显存 → 上限 1。"""
    assert compute_max_heavy_by_vram(4096) == 1  # 4G
    assert compute_max_heavy_by_vram(8192) == 1  # 8G


def test_compute_max_heavy_over_8gb():
    """>8G 显存 → 上限 2。"""
    assert compute_max_heavy_by_vram(8193) == 2  # 刚过 8G
    assert compute_max_heavy_by_vram(24576) == 2  # 24G


def test_compute_max_heavy_zero_vram_returns_default():
    """显存读取失败（0）→ 回退默认 1。"""
    assert compute_max_heavy_by_vram(0) == FALLBACK_MAX_HEAVY


def test_tier_constants():
    """分档阈值常量正确。"""
    assert VRAM_TIER_8GB == 8192


def test_fallback_max_heavy_is_1():
    """回退并存上限为 1（保守，防 OOM）。"""
    assert FALLBACK_MAX_HEAVY == 1


# --- 辅助构造 ---


def _make_legacy_manager(
    max_heavy: int = 2, ttls: dict[str, int] | None = None
) -> PipelineCacheManager:
    """构造测试用 manager（mock service，固定 max_heavy）。

    绕过 __init__ 以避免启动后台线程；测试手动注入 ttls。
    """
    service = MagicMock()
    service._pipelines = {}
    mgr = PipelineCacheManager.__new__(PipelineCacheManager)
    mgr._service = service
    mgr._ttls = dict(ttls) if ttls is not None else {"PP-StructureV3": 300}
    mgr._max_heavy = max_heavy
    mgr._last_used = {}
    mgr._tick_interval = 30.0
    mgr._stop_event = threading.Event()
    mgr._wakeup_event = threading.Event()
    mgr._thread = None
    return mgr


# --- _detect_max_heavy (CPU/GPU 分档) ---


def test_detect_max_heavy_cpu_mode_returns_1(monkeypatch):
    """CPU 模式（VIBEOCR_USE_GPU != true）固定返回 1。"""
    monkeypatch.setenv("VIBEOCR_USE_GPU", "false")
    service = MagicMock()
    service._pipelines = {}
    mgr = PipelineCacheManager(service, {}, max_heavy=None)  # 自动检测
    try:
        assert mgr.max_heavy == 1
    finally:
        mgr.shutdown()


def test_detect_max_heavy_no_env_returns_1(monkeypatch):
    """无 VIBEOCR_USE_GPU 环境变量时（默认 CPU）返回 1。"""
    monkeypatch.delenv("VIBEOCR_USE_GPU", raising=False)
    service = MagicMock()
    service._pipelines = {}
    mgr = PipelineCacheManager(service, {})
    try:
        assert mgr.max_heavy == 1
    finally:
        mgr.shutdown()


def test_detect_max_heavy_manual_override(monkeypatch):
    """max_heavy 手动指定时优先于自动检测。"""
    monkeypatch.setenv("VIBEOCR_USE_GPU", "false")
    service = MagicMock()
    service._pipelines = {}
    mgr = PipelineCacheManager(service, {}, max_heavy=3)
    try:
        assert mgr.max_heavy == 3
    finally:
        mgr.shutdown()


# --- enforce_capacity (FIFO) ---


def test_enforce_capacity_no_eviction_when_under_limit():
    """未超上限时不淘汰。"""
    mgr = _make_legacy_manager(max_heavy=2)
    mgr._service._pipelines = {"PP-StructureV3": object()}
    mgr._last_used = {"PP-StructureV3": 100.0}
    evicted = mgr.enforce_capacity("PaddleOCR-VL", now=200.0)
    assert evicted == []


def test_enforce_capacity_evicts_oldest():
    """超上限时淘汰 last_used 最早的。"""
    mgr = _make_legacy_manager(max_heavy=1)
    mgr._service._pipelines = {"PP-StructureV3": object()}
    mgr._last_used = {"PP-StructureV3": 100.0}
    evicted = mgr.enforce_capacity("PaddleOCR-VL", now=200.0)
    assert evicted == ["PP-StructureV3"]
    assert "PP-StructureV3" not in mgr._service._pipelines
    assert "PP-StructureV3" not in mgr._last_used


def test_enforce_capacity_skips_non_heavy():
    """淘汰只针对重管道，不动 OCR。"""
    mgr = _make_legacy_manager(max_heavy=1)
    mgr._service._pipelines = {"OCR": object(), "PP-StructureV3": object()}
    mgr._last_used = {"OCR": 50.0, "PP-StructureV3": 100.0}
    evicted = mgr.enforce_capacity("PaddleOCR-VL", now=200.0)
    assert evicted == ["PP-StructureV3"]
    assert "OCR" in mgr._service._pipelines  # OCR 保留


def test_enforce_capacity_does_not_evict_new_pipeline():
    """不淘汰正在加载的 new_pipeline（即使它已在缓存里）。"""
    mgr = _make_legacy_manager(max_heavy=1)
    mgr._service._pipelines = {"PP-StructureV3": object(), "PaddleOCR-VL": object()}
    mgr._last_used = {"PP-StructureV3": 100.0, "PaddleOCR-VL": 90.0}
    evicted = mgr.enforce_capacity("PaddleOCR-VL", now=200.0)
    # VL 是 new_pipeline，不被淘汰；淘汰 PP-V3（更旧）
    assert "PaddleOCR-VL" not in evicted
    assert "PP-StructureV3" in evicted


# --- evict_idle (TTL) ---


def test_evict_idle_releases_expired_heavy():
    """闲置超 TTL 的重管道被回收。"""
    mgr = _make_legacy_manager(max_heavy=3)
    mgr._service._pipelines = {"PP-StructureV3": object(), "OCR": object()}
    mgr._last_used = {"PP-StructureV3": 100.0, "OCR": 100.0}
    # now=500，PP-V3 last_used 100 + 300 = 400 < 500 → 过期
    evicted = mgr.evict_idle(now=500.0)
    assert evicted == ["PP-StructureV3"]
    assert "PP-StructureV3" not in mgr._service._pipelines
    assert "OCR" in mgr._service._pipelines  # OCR 不受 TTL


def test_evict_idle_keeps_recent():
    """未超 TTL 的保留。"""
    mgr = _make_legacy_manager(max_heavy=3)
    mgr._service._pipelines = {"PP-StructureV3": object()}
    mgr._last_used = {"PP-StructureV3": 300.0}
    # now=500，300 + 300 = 600 > 500 → 未过期
    evicted = mgr.evict_idle(now=500.0)
    assert evicted == []
    assert "PP-StructureV3" in mgr._service._pipelines


def test_evict_idle_ttl_zero_disables():
    """TTL=0 禁用回收。"""
    mgr = _make_legacy_manager(max_heavy=3, ttls={"PP-StructureV3": 0})
    mgr._service._pipelines = {"PP-StructureV3": object()}
    mgr._last_used = {"PP-StructureV3": 0.0}
    evicted = mgr.evict_idle(now=99999.0)
    assert evicted == []


# --- release ---


def test_release_heavy_only_keeps_ocr():
    """release(heavy_only=True) 只释放重管道。"""
    mgr = _make_legacy_manager(max_heavy=3)
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
    mgr = _make_legacy_manager(max_heavy=3)
    mgr._service._pipelines = {
        "PP-StructureV3": object(),
        "OCR": object(),
    }
    mgr._last_used = {"PP-StructureV3": 100.0, "OCR": 200.0}
    released = mgr.release(heavy_only=False)
    assert set(released) == {"PP-StructureV3", "OCR"}
    assert len(mgr._service._pipelines) == 0


def test_release_one_removes_only_target_and_usage_record():
    """release_one 只释放目标管道并清理 last_used。"""
    mgr = _make_legacy_manager(max_heavy=3)
    mgr._service._pipelines = {"OCR": object(), "PP-StructureV3": object()}
    mgr._last_used = {"OCR": 100.0, "PP-StructureV3": 200.0}

    assert mgr.release_one("OCR") is True
    assert "OCR" not in mgr._service._pipelines
    assert mgr.get_last_used("OCR") is None
    assert "PP-StructureV3" in mgr._service._pipelines


def test_release_one_missing_is_idempotent():
    """释放不存在的管道安全返回 False，并清掉可能残留的时间记录。"""
    mgr = _make_legacy_manager(max_heavy=3)
    mgr._last_used = {"OCR": 100.0}

    assert mgr.release_one("OCR") is False
    assert mgr.get_last_used("OCR") is None


# --- touch / ttls setter ---


def test_touch_records_timestamp():
    """touch 记录使用时间。"""
    mgr = _make_legacy_manager()
    mgr.touch("PP-StructureV3", now=123.0)
    assert mgr.get_last_used("PP-StructureV3") == 123.0


def test_ttls_setter_clamps_negative_to_zero():
    """ttls setter 不接受负值，夹到 0。"""
    mgr = _make_legacy_manager()
    mgr.ttls = {"PP-StructureV3": -10}
    assert mgr.ttls == {"PP-StructureV3": 0}


# =============================================================================
# Task 2: per-pipeline TTL + background tick thread + mineru cache_kind split
# =============================================================================


class _FakeService:
    """测试用替身：避免加载真实 paddle 管道。"""

    def __init__(self) -> None:
        self._pipelines: dict[str, object] = {}


def _make_manager(
    pipelines: dict[str, object],
    ttls: dict[str, int],
    *,
    max_heavy: int | None = 1,
    tick_interval: float = 30.0,
):
    """构造一个不自动启动后台线程的 manager（测试手动控制）。"""
    svc = _FakeService()
    svc._pipelines = dict(pipelines)
    # 绕过 __init__ 避免启动线程；然后手动 setup
    mgr = PipelineCacheManager.__new__(PipelineCacheManager)
    mgr._service = svc
    mgr._ttls = dict(ttls)
    mgr._max_heavy = max_heavy if max_heavy is not None else 1
    mgr._last_used = {}
    mgr._tick_interval = tick_interval
    mgr._stop_event = threading.Event()
    mgr._wakeup_event = threading.Event()
    mgr._thread = None  # 不启动
    return mgr, svc


def test_persistent_pipeline_never_evicted_by_ttl() -> None:
    """ttl=0 的管道 evict_idle 不回收。"""
    mgr, svc = _make_manager(
        {"OCR": object()}, {"OCR": 0}
    )
    mgr.touch("OCR", now=1000.0)
    evicted = mgr.evict_idle(now=1000.0 + 999999)
    assert evicted == []
    assert "OCR" in svc._pipelines


def test_ttl_evicts_after_expiry() -> None:
    """ttl=300 的管道超时后被回收。"""
    mgr, svc = _make_manager(
        {"PP-StructureV3": object()}, {"PP-StructureV3": 300}
    )
    mgr.touch("PP-StructureV3", now=1000.0)
    assert mgr.evict_idle(now=1000.0 + 100) == []
    assert mgr.evict_idle(now=1000.0 + 301) == ["PP-StructureV3"]
    assert "PP-StructureV3" not in svc._pipelines


def test_per_pipeline_independent_ttl() -> None:
    """不同管道 TTL 独立，过期时间不同步。"""
    mgr, svc = _make_manager(
        {"OCR": object(), "PP-StructureV3": object(), "MinerU": object()},
        {"OCR": 0, "MinerU": 0, "PP-StructureV3": 300},
    )
    for name in svc._pipelines:
        mgr.touch(name, now=1000.0)
    # 400s 后只有 PP-StructureV3 被回收
    assert mgr.evict_idle(now=1400.0) == ["PP-StructureV3"]
    assert "OCR" in svc._pipelines
    assert "MinerU" in svc._pipelines


def test_ttls_setter_validates_keys_and_values() -> None:
    """ttls setter 忽略未知管道名，value 钳到 >=0。"""
    mgr, _ = _make_manager({}, {"OCR": 0})
    mgr.ttls = {"OCR": 100, "UNKNOWN_PIPELINE": 50, "PP-StructureV3": -5}
    assert mgr.ttls == {"OCR": 100, "PP-StructureV3": 0}


def test_mineru_not_counted_in_max_heavy() -> None:
    """MinerU 不占并存上限名额。"""
    mgr, svc = _make_manager(
        {"MinerU": object(), "PP-StructureV3": object()},
        {"MinerU": 0, "PP-StructureV3": 0, "PaddleOCR-VL": 0},
        max_heavy=1,
    )
    mgr.touch("MinerU", now=1000.0)
    mgr.touch("PP-StructureV3", now=1000.0)
    # 加载第二个 paddle 重管道：应淘汰 PP-StructureV3，不动 MinerU
    svc._pipelines["PaddleOCR-VL"] = object()
    mgr.touch("PaddleOCR-VL", now=2000.0)
    evicted = mgr.enforce_capacity("PaddleOCR-VL", now=2000.0)
    assert evicted == ["PP-StructureV3"]
    assert "MinerU" in svc._pipelines


def test_release_mineru_does_not_call_empty_cache(monkeypatch) -> None:
    """回收 MinerU 时不调 paddle.device.cuda.empty_cache()。"""
    called: list[str] = []
    monkeypatch.setenv("VIBEOCR_USE_GPU", "true")

    import sys
    fake_paddle = MagicMock()
    fake_paddle.device.cuda.empty_cache = lambda: called.append("empty_cache")
    monkeypatch.setitem(sys.modules, "paddle", fake_paddle)

    mgr, svc = _make_manager({"MinerU": object()}, {"MinerU": 0})
    mgr.release(heavy_only=False)
    assert "MinerU" not in svc._pipelines
    assert called == []  # MinerU 不触发 empty_cache


def test_release_paddle_calls_empty_cache(monkeypatch) -> None:
    """回收 paddle 管道时调 paddle.device.cuda.empty_cache()。"""
    called: list[str] = []
    monkeypatch.setenv("VIBEOCR_USE_GPU", "true")

    import sys
    fake_paddle = MagicMock()
    fake_paddle.device.cuda.empty_cache = lambda: called.append("empty_cache")
    monkeypatch.setitem(sys.modules, "paddle", fake_paddle)

    mgr, svc = _make_manager({"OCR": object()}, {"OCR": 0})
    mgr.release(heavy_only=False)
    assert "OCR" not in svc._pipelines
    assert called == ["empty_cache"]


def test_compute_max_heavy_by_vram_8gb_threshold() -> None:
    """≤8GB=1, >8GB=2, 未知=1。"""
    from vibeocr.services.pipeline_cache_manager import compute_max_heavy_by_vram

    assert compute_max_heavy_by_vram(0) == 1       # 未知
    assert compute_max_heavy_by_vram(4096) == 1    # 4GB
    assert compute_max_heavy_by_vram(8192) == 1    # 8GB 边界
    assert compute_max_heavy_by_vram(8193) == 2    # 刚过 8GB
    assert compute_max_heavy_by_vram(24576) == 2   # 24GB


# =============================================================================
# Task 3: live background tick thread behavior (real __init__, no __new__ bypass)
# =============================================================================


def test_background_tick_evicts_after_ttl(monkeypatch) -> None:
    """启动后台线程，注入短 tick_interval，验证 TTL 到期后被回收。"""
    monkeypatch.setenv("VIBEOCR_USE_GPU", "false")
    svc = _FakeService()
    svc._pipelines = {"PP-StructureV3": object()}
    # 真实 __init__，会启动后台线程
    mgr = PipelineCacheManager(
        svc,
        {"PP-StructureV3": 1},  # 1 秒 TTL
        max_heavy=2,
        tick_interval=0.05,
    )
    try:
        mgr.touch("PP-StructureV3")
        time.sleep(0.3)  # 等 tick + ttl 过期
        assert "PP-StructureV3" not in svc._pipelines
    finally:
        mgr.shutdown()


def test_shutdown_joins_thread_cleanly(monkeypatch) -> None:
    """shutdown() 后线程在 2s 内退出。"""
    monkeypatch.setenv("VIBEOCR_USE_GPU", "false")
    svc = _FakeService()
    mgr = PipelineCacheManager(
        svc, {"OCR": 0}, max_heavy=1, tick_interval=0.01
    )
    mgr.shutdown()
    assert not mgr._thread.is_alive()


def test_touch_wakes_blocked_thread(monkeypatch) -> None:
    """空缓存时线程阻塞，touch 唤醒后开始 tick。"""
    monkeypatch.setenv("VIBEOCR_USE_GPU", "false")
    svc = _FakeService()
    mgr = PipelineCacheManager(
        svc, {"PP-StructureV3": 1}, max_heavy=1, tick_interval=0.05
    )
    try:
        time.sleep(0.1)  # 空缓存期，线程阻塞
        assert svc._pipelines == {}  # 未被回收（本来就空）
        svc._pipelines["PP-StructureV3"] = object()
        mgr.touch("PP-StructureV3")  # 唤醒
        # TTL=1s 且 touch 记录的是真实当前时间，因此 sleep 必须 > TTL 才能触发回收。
        # 1.3s = 1.0s(TTL) + 0.3s(6 个 tick 周期 + 调度余量)。
        time.sleep(1.3)
        assert "PP-StructureV3" not in svc._pipelines  # 被回收
    finally:
        mgr.shutdown()
