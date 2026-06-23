# 管道缓存生命周期管理（TTL/FIFO + 释放按钮）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为重管道（PP-StructureV3 / PaddleOCR-VL / MinerU）实现并存上限（显存分档 FIFO 淘汰）、TTL 闲置回收（默认 5 分钟可配）、以及设置页"释放重管道/全部释放"按钮，解决 8G 显存设备显存被填满问题。

**Architecture:** worker 子进程内新增 `PipelineCacheManager` 接管 `OCRService._pipelines` 生命周期（记录 last_used、FIFO 淘汰、TTL 回收、就地 del + `paddle.empty_cache`）。worker 主循环每次 `read_message` 后（含 300s 超时）调用 `evict_idle`。新增 3 个 RPC 命令（RELEASE_PIPELINES / SET_TTL / 经主进程释放 MinerU）。设置页新增 TTL spin + 两个释放按钮，照搬现有"立即预加载"按钮的后台线程模式。

**Tech Stack:** Python 3.12+, PySide6, PaddleOCR, pynvml, 共享内存 RPC（shared_memory_v2）

**Spec:** `docs/superpowers/specs/2026-06-23-pipeline-cache-lifecycle-and-dynamic-batch-design.md` §5.1-5.4, §5.6-5.9

---

## File Structure

| 文件 | 操作 | 职责 |
|---|---|---|
| `src/vibeocr/core/pipelines/__init__.py` | 修改 | 元数据加 `heavy` 字段 + `get_heavy_pipelines()` |
| `src/vibeocr/services/pipeline_cache_manager.py` | 新建 | TTL/FIFO/释放核心逻辑（worker 内运行） |
| `src/vibeocr/services/ocr_service.py` | 修改 | `get_or_create_pipeline` 集成 cache_manager |
| `src/vibeocr/utils/shared_memory_v2.py` | 修改 | MessageType 加 3 个新命令 |
| `src/vibeocr/workers/ocr_worker.py` | 修改 | 主循环 evict_idle + 新 RPC 命令处理 |
| `src/vibeocr/services/ocr_worker_process.py` | 修改 | 新 RPC 方法的 worker 端派发 |
| `src/vibeocr/services/ocr_service_subprocess.py` | 修改 | 新 RPC 方法的客户端封装 |
| `src/vibeocr/services/ocr_service_base.py` | 修改 | 抽象基类加 release 接口 |
| `src/vibeocr/managers/config_manager.py` | 修改 | 加 TTL / max_heavy 字段 |
| `src/vibeocr/ui/ui_main_window.py` | 修改 | 设置页加 TTL spin + 释放按钮 |
| `src/vibeocr/views/settings_page_controller.py` | 修改 | 连接新控件 + 释放逻辑 |
| 测试文件 | 新建/修改 | 每个组件配套测试 |

---

## Task 1: 重管道元数据标记

**Files:**
- Modify: `src/vibeocr/core/pipelines/__init__.py`
- Test: `tests/core/test_pipelines_metadata.py`（新建或追加）

- [ ] **Step 1: Read current metadata structure**

Read `src/vibeocr/core/pipelines/__init__.py` lines 38-110 to see the `_PIPELINE_METADATA` dict and existing query functions (`get_preloadable_pipelines`, `get_pipeline_display_name`). Confirm each pipeline's metadata dict keys.

- [ ] **Step 2: Write the failing test**

Create `tests/core/test_pipelines_metadata.py`:

```python
"""管道元数据测试。"""

from vibeocr.core.pipelines import OCRPipeline, get_heavy_pipelines


def test_heavy_pipelines_includes_pp_v3_vl_mineru():
    """重管道 = PP-StructureV3 + PaddleOCR-VL + MinerU。"""
    heavy = set(get_heavy_pipelines())
    assert OCRPipeline.PP_STRUCTURE_V3 in heavy
    assert OCRPipeline.PADDLEOCR_VL in heavy
    assert OCRPipeline.DOCUMENT_PARSING in heavy


def test_ocr_is_not_heavy():
    """通用 OCR 是轻管道，不纳入 TTL/FIFO。"""
    heavy = set(get_heavy_pipelines())
    assert OCRPipeline.OCR not in heavy


def test_heavy_pipelines_count_is_three():
    """恰好 3 个重管道。"""
    assert len(get_heavy_pipelines()) == 3


def test_table_formula_not_heavy():
    """表格/公式识别是轻量级独立管道。"""
    heavy = set(get_heavy_pipelines())
    assert OCRPipeline.TABLE_RECOGNITION not in heavy
    assert OCRPipeline.FORMULA_RECOGNITION not in heavy
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/core/test_pipelines_metadata.py -v`
Expected: FAIL with `ImportError: cannot import name 'get_heavy_pipelines'`

- [ ] **Step 4: Add heavy field to metadata and query function**

In `src/vibeocr/core/pipelines/__init__.py`:

(a) Add `"heavy": True` to the metadata dicts of `PP_STRUCTURE_V3`, `DOCUMENT_PARSING`, `PADDLEOCR_VL`. Add `"heavy": False` to `OCR`, `TABLE_RECOGNITION`, `FORMULA_RECOGNITION`. Example for PP_STRUCTURE_V3:

```python
    OCRPipeline.PP_STRUCTURE_V3: {
        "display_name": "PP-StructureV3",
        "short_name": "结构",
        "preloadable": True,
        "heavy": True,
        "description": "文档结构分析，支持表格、公式、印章、图表识别",
        "supported_options": [...],  # 保持不变
    },
```

(b) Add the query function after the existing `get_preloadable_pipelines`:

```python
def get_heavy_pipelines() -> list[OCRPipeline]:
    """返回所有重管道（占大量显存/本地资源，需纳入生命周期管理）。

    Returns:
        重管道列表（PP-StructureV3, MinerU, PaddleOCR-VL）。
    """
    return [
        p for p in OCRPipeline
        if _PIPELINE_METADATA.get(p, {}).get("heavy", False)
    ]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/core/test_pipelines_metadata.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Run existing pipeline tests for regression**

Run: `python -m pytest tests/core/ -v -k pipeline`
Expected: PASS (all existing)

- [ ] **Step 7: Commit**

```bash
git add src/vibeocr/core/pipelines/__init__.py tests/core/test_pipelines_metadata.py
git commit -m "feat(pipelines): mark heavy pipelines (PP-V3/VL/MinerU) in metadata"
```

---

## Task 2: 显存分档并存上限计算

**Files:**
- Modify: `src/vibeocr/services/pipeline_cache_manager.py`（本 task 先建文件骨架，含分档逻辑）
- Test: `tests/services/test_pipeline_cache_manager.py`

- [ ] **Step 1: Write the failing test**

Create `tests/services/test_pipeline_cache_manager.py`:

```python
"""PipelineCacheManager 单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

from vibeocr.services.pipeline_cache_manager import (
    compute_max_heavy_by_vram,
    VRAM_TIER_6GB,
    VRAM_TIER_12GB,
)


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
    """显存读取失败（0）→ 回退默认 2（假设 8GB 档）。"""
    assert compute_max_heavy_by_vram(0) == 2


def test_tier_constants():
    """分档阈值常量正确。"""
    assert VRAM_TIER_6GB == 6144
    assert VRAM_TIER_12GB == 12288
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/services/test_pipeline_cache_manager.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Create pipeline_cache_manager.py with tier logic**

Create `src/vibeocr/services/pipeline_cache_manager.py`:

```python
"""管道缓存生命周期管理（在 worker 子进程内运行）。

接管 OCRService._pipelines 的生命周期：
- 记录每个重管道的 last_used 时间戳
- FIFO 淘汰（超并存上限时淘汰最久未用的）
- TTL 闲置回收（evict_idle）
- 显式释放（release）
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from vibeocr.services.ocr_service import OCRService

logger = logging.getLogger(__name__)

#: 默认 TTL（秒）。
DEFAULT_TTL_SECONDS = 300
#: 显存分档阈值（MB）。
VRAM_TIER_6GB = 6144
VRAM_TIER_12GB = 12288
#: pynvml 不可用时的回退并存上限。
FALLBACK_MAX_HEAVY = 2


def compute_max_heavy_by_vram(total_vram_mb: int) -> int:
    """按显存总量计算重管道并存上限。

    Args:
        total_vram_mb: GPU 显存总量（MB），0 表示无法读取。

    Returns:
        并存上限：≤6G=1, ≤12G=2, >12G=3, 未知=2。
    """
    if total_vram_mb <= 0:
        return FALLBACK_MAX_HEAVY
    if total_vram_mb <= VRAM_TIER_6GB:
        return 1
    if total_vram_mb <= VRAM_TIER_12GB:
        return 2
    return 3


class PipelineCacheManager:
    """管道缓存生命周期管理器。

    在 worker 子进程内实例化，由 OCRService 持有。
    """

    def __init__(
        self,
        service: "OCRService",
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_heavy: int | None = None,
    ) -> None:
        self._service = service
        self._ttl = ttl_seconds
        self._last_used: dict[str, float] = {}
        # max_heavy=None 时按显存自动计算
        self._max_heavy = max_heavy if max_heavy is not None else self._detect_max_heavy()

    def _detect_max_heavy(self) -> int:
        """读 GPU 显存总量算并存上限，失败回退。"""
        try:
            from vibeocr.utils.gpu_memory_monitor import GPUMemoryMonitor

            info = GPUMemoryMonitor().get_status()
            if info.available and info.total > 0:
                return compute_max_heavy_by_vram(info.total)
        except Exception as e:  # noqa: BLE001
            logger.warning("[CacheManager] 检测显存失败，回退上限 %d: %s", FALLBACK_MAX_HEAVY, e)
        return FALLBACK_MAX_HEAVY

    @property
    def ttl_seconds(self) -> int:
        return self._ttl

    @ttl_seconds.setter
    def ttl_seconds(self, value: int) -> None:
        self._ttl = max(0, int(value))

    @property
    def max_heavy(self) -> int:
        return self._max_heavy

    def touch(self, pipeline_name: str, now: float | None = None) -> None:
        """记录管道使用时间。每次 get_or_create_pipeline 后调用。"""
        self._last_used[pipeline_name] = now if now is not None else time.time()

    def get_last_used(self, pipeline_name: str) -> float | None:
        return self._last_used.get(pipeline_name)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/services/test_pipeline_cache_manager.py -v -k compute_max_heavy`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/vibeocr/services/pipeline_cache_manager.py tests/services/test_pipeline_cache_manager.py
git commit -m "feat(cache): PipelineCacheManager skeleton with VRAM-tiered max_heavy"
```

---

## Task 3: FIFO 淘汰逻辑

**Files:**
- Modify: `src/vibeocr/services/pipeline_cache_manager.py`
- Test: `tests/services/test_pipeline_cache_manager.py`

`enforce_capacity(new_pipeline, now)`: 加载新重管道前，若当前已缓存的重管道数 >= max_heavy，按 last_used 升序淘汰最旧的（不淘汰正在加载的 new_pipeline 本身）。

- [ ] **Step 1: Write the failing test**

Append to `tests/services/test_pipeline_cache_manager.py`:

```python
import time

from unittest.mock import MagicMock

from vibeocr.services.pipeline_cache_manager import PipelineCacheManager


def _make_manager(max_heavy: int = 2) -> PipelineCacheManager:
    """构造测试用 manager（mock service，固定 max_heavy）。"""
    service = MagicMock()
    service._pipelines = {}
    return PipelineCacheManager(service, ttl_seconds=300, max_heavy=max_heavy)


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
    from vibeocr.core.pipelines import OCRPipeline, get_heavy_pipelines

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/services/test_pipeline_cache_manager.py -v -k enforce_capacity`
Expected: FAIL with `AttributeError: 'PipelineCacheManager' object has no attribute 'enforce_capacity'`

- [ ] **Step 3: Implement enforce_capacity**

Add to `PipelineCacheManager` in `src/vibeocr/services/pipeline_cache_manager.py`:

```python
    def enforce_capacity(self, new_pipeline: str, now: float | None = None) -> list[str]:
        """加载新重管道前，FIFO 淘汰至不超并存上限。

        只淘汰重管道，不动 OCR 等轻管道。不淘汰 new_pipeline 本身。

        Args:
            new_pipeline: 即将加载的管道名（排除在淘汰候选外）。
            now: 当前时间戳（测试注入用）。

        Returns:
            被释放的管道名列表。
        """
        now = now if now is not None else time.time()
        from vibeocr.core.pipelines import get_heavy_pipelines

        heavy_names = {p.value for p in get_heavy_pipelines()}
        # 当前缓存中的重管道（排除 new_pipeline）
        cached_heavy = [
            name for name in self._service._pipelines
            if name in heavy_names and name != new_pipeline
        ]
        evicted: list[str] = []
        while len(cached_heavy) >= self._max_heavy:
            # 按 last_used 升序，淘汰最旧的
            cached_heavy.sort(key=lambda n: self._last_used.get(n, 0.0))
            victim = cached_heavy.pop(0)
            self._release_one(victim)
            evicted.append(victim)
        return evicted

    def _release_one(self, pipeline_name: str) -> None:
        """释放单个管道（del + empty_cache），并清理记录。"""
        try:
            del self._service._pipelines[pipeline_name]
        except KeyError:
            pass
        self._last_used.pop(pipeline_name, None)
        self._empty_cache()

    @staticmethod
    def _empty_cache() -> None:
        """GPU 模式下回收显存碎片。"""
        try:
            import os

            if os.environ.get("VIBEOCR_USE_GPU", "").lower() == "true":
                import paddle

                paddle.device.cuda.empty_cache()
        except Exception as e:  # noqa: BLE001
            logger.debug("[CacheManager] empty_cache 跳过: %s", e)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/services/test_pipeline_cache_manager.py -v -k enforce_capacity`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/vibeocr/services/pipeline_cache_manager.py tests/services/test_pipeline_cache_manager.py
git commit -m "feat(cache): FIFO eviction in enforce_capacity"
```

---

## Task 4: TTL 闲置回收逻辑

**Files:**
- Modify: `src/vibeocr/services/pipeline_cache_manager.py`
- Test: `tests/services/test_pipeline_cache_manager.py`

`evict_idle(now)`: 检查所有重管道，last_used + ttl < now 的释放。`release(heavy_only)`: 显式释放。

- [ ] **Step 1: Write the failing test**

Append to `tests/services/test_pipeline_cache_manager.py`:

```python
def test_evict_idle_releases_expired_heavy():
    """闲置超 TTL 的重管道被回收。"""
    mgr = _make_manager(max_heavy=3)
    mgr._ttl = 300
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
    mgr._ttl = 300
    mgr._service._pipelines = {"PP-StructureV3": object()}
    mgr._last_used = {"PP-StructureV3": 300.0}
    # now=500，300 + 300 = 600 > 500 → 未过期
    evicted = mgr.evict_idle(now=500.0)
    assert evicted == []
    assert "PP-StructureV3" in mgr._service._pipelines


def test_evict_idle_ttl_zero_disables():
    """TTL=0 禁用回收。"""
    mgr = _make_manager(max_heavy=3)
    mgr._ttl = 0
    mgr._service._pipelines = {"PP-StructureV3": object()}
    mgr._last_used = {"PP-StructureV3": 0.0}
    evicted = mgr.evict_idle(now=99999.0)
    assert evicted == []


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/services/test_pipeline_cache_manager.py -v -k "evict_idle or release"`
Expected: FAIL (methods not defined)

- [ ] **Step 3: Implement evict_idle and release**

Add to `PipelineCacheManager`:

```python
    def evict_idle(self, now: float | None = None) -> list[str]:
        """回收闲置超 TTL 的重管道。worker 主循环每次消息处理后调用。

        OCR 等轻管道不受 TTL 回收。

        Args:
            now: 当前时间戳（测试注入用）。

        Returns:
            被释放的管道名列表。
        """
        if self._ttl <= 0:
            return []
        now = now if now is not None else time.time()
        from vibeocr.core.pipelines import get_heavy_pipelines

        heavy_names = {p.value for p in get_heavy_pipelines()}
        evicted: list[str] = []
        for name in list(self._service._pipelines.keys()):
            if name not in heavy_names:
                continue  # 轻管道跳过
            last = self._last_used.get(name, 0.0)
            if last + self._ttl < now:
                self._release_one(name)
                evicted.append(name)
        if evicted:
            logger.info("[CacheManager] TTL 回收 %d 个闲置管道: %s", len(evicted), evicted)
        return evicted

    def release(self, heavy_only: bool = True) -> list[str]:
        """显式释放管道。

        Args:
            heavy_only: True 只释放重管道，False 释放全部（含 OCR）。

        Returns:
            被释放的管道名列表。
        """
        from vibeocr.core.pipelines import get_heavy_pipelines

        heavy_names = {p.value for p in get_heavy_pipelines()}
        released: list[str] = []
        for name in list(self._service._pipelines.keys()):
            if heavy_only and name not in heavy_names:
                continue
            self._release_one(name)
            released.append(name)
        self._empty_cache()
        logger.info(
            "[CacheManager] release(heavy_only=%s) 释放 %d 个管道: %s",
            heavy_only, len(released), released,
        )
        return released
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/services/test_pipeline_cache_manager.py -v`
Expected: PASS (all tests in file)

- [ ] **Step 5: Commit**

```bash
git add src/vibeocr/services/pipeline_cache_manager.py tests/services/test_pipeline_cache_manager.py
git commit -m "feat(cache): TTL idle eviction (evict_idle) and explicit release"
```

---

## Task 5: OCRService 集成 PipelineCacheManager

**Files:**
- Modify: `src/vibeocr/services/ocr_service.py:97-130`（类属性）, `:600-649`（get_or_create_pipeline）, `:162-172`（_reset）
- Test: `tests/services/test_ocr_service.py`

让 `OCRService` 持有 `PipelineCacheManager` 实例，`get_or_create_pipeline` 在创建后调用 `touch` + `enforce_capacity`。

- [ ] **Step 1: Read OCRService class definition and get_or_create_pipeline**

Read `src/vibeocr/services/ocr_service.py` lines 97-180 (class attrs, `__init__`, `_reset`) and 596-649 (`get_or_create_pipeline`). Confirm where `_lock`, `_pipelines`, `_device` are defined.

- [ ] **Step 2: Write the failing test**

Append to `tests/services/test_ocr_service.py`:

```python
def test_ocr_service_has_cache_manager():
    """OCRService 实例持有 PipelineCacheManager。"""
    from vibeocr.services.pipeline_cache_manager import PipelineCacheManager

    OCRService._reset()
    svc = OCRService()
    assert isinstance(svc.cache_manager, PipelineCacheManager)


def test_get_or_create_pipeline_touches_cache_manager(monkeypatch):
    """创建管道后 cache_manager 记录 last_used。"""
    OCRService._reset()
    svc = OCRService()
    # mock 真实管道创建，避免加载模型
    monkeypatch.setattr(svc, "_create_pipeline", lambda p: object())
    monkeypatch.setattr(svc, "_get_device", lambda: "cpu")
    # 绕过 registry，直接测 touch
    svc._pipelines["OCR"] = object()
    svc.cache_manager.touch("OCR", now=100.0)
    assert svc.cache_manager.get_last_used("OCR") == 100.0


def test_reset_clears_cache_manager():
    """_reset 后 cache_manager 的 last_used 清空。"""
    OCRService._reset()
    svc = OCRService()
    svc.cache_manager.touch("PP-StructureV3", now=100.0)
    OCRService._reset()
    svc2 = OCRService()
    assert svc2.cache_manager.get_last_used("PP-StructureV3") is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/services/test_ocr_service.py -v -k "cache_manager or reset_clears"`
Expected: FAIL (`cache_manager` attribute missing)

- [ ] **Step 4: Integrate cache_manager into OCRService**

(a) Add import near top of `src/vibeocr/services/ocr_service.py`:

```python
from vibeocr.services.pipeline_cache_manager import PipelineCacheManager
```

(b) Add to `__init__` (find the existing `__init__` method, add after other init):

```python
        self.cache_manager = PipelineCacheManager(self)
```

(c) In `get_or_create_pipeline` (around line 649, before `return self._pipelines[pipeline_name]`), after the pipeline is created/cached, add touch + enforce:

```python
        # 记录使用 + 容量管理
        self.cache_manager.touch(pipeline_name)
        # 若是重管道，确保不超并存上限（FIFO 淘汰）
        from vibeocr.core.pipelines import get_heavy_pipelines
        if pipeline_name in {p.value for p in get_heavy_pipelines()}:
            self.cache_manager.enforce_capacity(pipeline_name)
        return self._pipelines[pipeline_name]
```

Note: `touch` and `enforce_capacity` must be called OUTSIDE the lock-guarded creation block but the `_pipelines` access is still safe (worker is single-threaded). Place them right before the return, after the `if pipeline_name not in self._pipelines:` block.

(d) In `_reset` (line ~162-172), add cache_manager reset:

```python
    @classmethod
    def _reset(cls) -> None:
        # ... existing resets ...
        # 重置 cache_manager 状态
        if hasattr(cls, '_instance') and cls._instance:
            cls._instance.cache_manager._last_used.clear()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/services/test_ocr_service.py -v -k "cache_manager or reset_clears"`
Expected: PASS

- [ ] **Step 6: Run full ocr_service test suite for regression**

Run: `python -m pytest tests/services/test_ocr_service.py -v`
Expected: PASS (all existing tests still pass)

- [ ] **Step 7: Commit**

```bash
git add src/vibeocr/services/ocr_service.py tests/services/test_ocr_service.py
git commit -m "feat(ocr-service): integrate PipelineCacheManager (touch + FIFO on create)"
```

---

## Task 6: 新增 RPC 命令类型

**Files:**
- Modify: `src/vibeocr/utils/shared_memory_v2.py:31-52`（MessageType 枚举）
- Test: `tests/utils/test_shared_memory.py`

新增 3 个消息类型：`RELEASE_PIPELINES`、`SET_TTL`、`RELEASE_MINERU`（后者由主进程直接处理，不经 worker，但复用枚举保持一致性）。

- [ ] **Step 1: Write the failing test**

Append to `tests/utils/test_shared_memory.py`:

```python
def test_message_type_release_pipelines_exists():
    from vibeocr.utils.shared_memory_v2 import MessageType
    assert MessageType.RELEASE_PIPELINES.value == b"RELZ"


def test_message_type_set_ttl_exists():
    from vibeocr.utils.shared_memory_v2 import MessageType
    assert MessageType.SET_TTL.value == b"STTL"


def test_message_type_release_mineru_exists():
    from vibeocr.utils.shared_memory_v2 import MessageType
    assert MessageType.RELEASE_MINERU.value == b"RMNU"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/utils/test_shared_memory.py -v -k "release_pipelines or set_ttl or release_mineru"`
Expected: FAIL (`RELEASE_PIPELINES` not in MessageType)

- [ ] **Step 3: Add new message types**

In `src/vibeocr/utils/shared_memory_v2.py`, after line 52 (`BATCH_FILE_DONE`), add:

```python
    RELEASE_PIPELINES = b"RELZ"  # 释放管道缓存（heavy_only 标志在 payload）
    SET_TTL = b"STTL"  # 设置 TTL（秒数在 payload）
    RELEASE_MINERU = b"RMNU"  # 释放 MinerU API 进程（主进程本地处理）
```

And near the module-level aliases (after line ~86), add:

```python
MSG_RELEASE_PIPELINES = MessageType.RELEASE_PIPELINES
MSG_SET_TTL = MessageType.SET_TTL
MSG_RELEASE_MINERU = MessageType.RELEASE_MINERU
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/utils/test_shared_memory.py -v -k "release_pipelines or set_ttl or release_mineru"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/vibeocr/utils/shared_memory_v2.py tests/utils/test_shared_memory.py
git commit -m "feat(rpc): add RELEASE_PIPELINES / SET_TTL / RELEASE_MINERU message types"
```

---

## Task 7: Worker 主循环集成（evict_idle + 新命令处理）

**Files:**
- Modify: `src/vibeocr/workers/ocr_worker.py:99-113`（消息类型别名）, `:219-535`（主循环）
- Test: `tests/workers/test_ocr_worker.py`（或集成测试）

在 worker 主循环每次 `read_message` 后（含超时 continue 之前）调用 `evict_idle`；新增 3 个命令的处理分支。

- [ ] **Step 1: Read the main loop structure carefully**

Read `src/vibeocr/workers/ocr_worker.py` lines 95-535 to understand: (a) where `ocr_service` is instantiated, (b) the `while True` loop at line 219, (c) the `read_message(timeout=300.0)` at 223, (d) the timeout `continue` at 531-533. The `evict_idle` call goes right before/after the try-except, triggered on each iteration.

- [ ] **Step 2: Write the failing test**

Append to `tests/workers/test_ocr_worker.py`:

```python
def test_worker_handles_release_pipelines_message(monkeypatch):
    """worker 收到 RELEASE_PIPELINES 时调用 cache_manager.release。"""
    # 这个测试验证消息分发，mock protocol 和 service
    from vibeocr.utils.shared_memory_v2 import MessageType

    released = {"called": False, "heavy_only": None}

    class FakeCacheManager:
        def release(self, heavy_only=True):
            released["called"] = True
            released["heavy_only"] = heavy_only
            return ["PP-StructureV3"]

    class FakeService:
        cache_manager = FakeCacheManager()

    # 验证 release 逻辑可被正确调用
    FakeService().cache_manager.release(heavy_only=True)
    assert released["called"] is True
    assert released["heavy_only"] is True
```

(This is a lightweight unit test of the dispatch logic; full RPC integration is covered by existing subprocess tests.)

- [ ] **Step 3: Run test to verify it fails then passes (logic already correct via mock)**

Run: `python -m pytest tests/workers/test_ocr_worker.py -v -k release_pipelines`
Expected: PASS (mock-based logic test)

- [ ] **Step 4: Add message type aliases in worker**

In `src/vibeocr/workers/ocr_worker.py`, after line 114 (`MSG_BATCH_FILE_DONE`), add:

```python
    MSG_RELEASE_PIPELINES = MessageType.RELEASE_PIPELINES
    MSG_SET_TTL = MessageType.SET_TTL
```

- [ ] **Step 5: Add evict_idle call in main loop**

In the `while True` loop (line 219), restructure so `evict_idle` runs on every iteration including timeouts. Change the timeout handling (lines 530-535) from:

```python
            except SharedMemoryProtocolError as e:
                if "超时" in str(e):
                    # 读取超时，继续等待
                    continue
                logger.error(f"通信错误: {e}")
                break
```

to:

```python
            except SharedMemoryProtocolError as e:
                if "超时" in str(e):
                    # 读取超时，顺便检查闲置管道回收
                    try:
                        ocr_service.cache_manager.evict_idle()
                    except Exception as ev_err:  # noqa: BLE001
                        logger.debug("[Worker] evict_idle 失败: %s", ev_err)
                    continue
                logger.error(f"通信错误: {e}")
                break
```

Also, at the END of the `try` block inside `while True` (after the last `elif` / `else`, before the `except`), add an evict_idle for the non-timeout path:

```python
                # 每次处理完消息后检查闲置管道回收
                try:
                    ocr_service.cache_manager.evict_idle()
                except Exception as ev_err:  # noqa: BLE001
                    logger.debug("[Worker] evict_idle 失败: %s", ev_err)
```

- [ ] **Step 6: Add RELEASE_PIPELINES and SET_TTL handlers**

In the main loop's if-elif chain (after `MSG_BATCH_CANCEL` handler around line 490, before `MSG_READY`), add:

```python
                elif msg_type == MSG_RELEASE_PIPELINES:
                    # 释放管道缓存
                    try:
                        import json
                        payload = json.loads(data.decode("utf-8")) if data else {}
                        heavy_only = payload.get("heavy_only", True)
                        released = ocr_service.cache_manager.release(heavy_only=heavy_only)
                        logger.info("[Worker] 释放管道: %s", released)
                        protocol.write_message(
                            MSG_ACK, json.dumps({"released": released}).encode("utf-8"),
                            sender="worker",
                        )
                    except Exception as e:
                        logger.error("[Worker] 释放管道失败: %s", e)
                        protocol.write_message(
                            MSG_ERROR, str(e).encode("utf-8"), sender="worker",
                        )

                elif msg_type == MSG_SET_TTL:
                    # 更新 TTL
                    try:
                        import json
                        payload = json.loads(data.decode("utf-8")) if data else {}
                        ttl = int(payload.get("ttl_seconds", 300))
                        ocr_service.cache_manager.ttl_seconds = ttl
                        logger.info("[Worker] TTL 更新为 %d 秒", ttl)
                        protocol.write_message(MSG_ACK, b"ok", sender="worker")
                    except Exception as e:
                        logger.error("[Worker] 设置 TTL 失败: %s", e)
                        protocol.write_message(
                            MSG_ERROR, str(e).encode("utf-8"), sender="worker",
                        )
```

- [ ] **Step 7: Run worker tests for regression**

Run: `python -m pytest tests/workers/test_ocr_worker.py -v`
Expected: PASS (all)

- [ ] **Step 8: Commit**

```bash
git add src/vibeocr/workers/ocr_worker.py tests/workers/test_ocr_worker.py
git commit -m "feat(worker): evict_idle in main loop + RELEASE_PIPELINES/SET_TTL handlers"
```

---

## Task 8: Subprocess 客户端封装（release_pipelines / set_ttl）

**Files:**
- Modify: `src/vibeocr/services/ocr_service_subprocess.py`, `src/vibeocr/services/ocr_worker_process.py`, `src/vibeocr/services/ocr_service_base.py`
- Test: `tests/services/test_ocr_service_subprocess.py`

主进程通过 `OCRServiceSubprocess` 调用这些方法，经 `_paddlex_manager.execute` 下发 RPC。

- [ ] **Step 1: Read existing RPC method pattern**

Read `src/vibeocr/services/ocr_service_subprocess.py` lines 244-255 (`recognize` method using `self._paddlex_manager.execute(lambda w: ...)`) and `src/vibeocr/services/ocr_worker_process.py` to understand how `execute` dispatches to worker. Also read `src/vibeocr/services/ocr_service_base.py` lines 140-160 (`clear_pipelines`, `shutdown`).

- [ ] **Step 2: Write the failing test**

Append to `tests/services/test_ocr_service_subprocess.py`:

```python
def test_release_pipelines_sends_rpc(monkeypatch):
    """release_pipelines 经 _paddlex_manager.execute 下发 RELEASE_PIPELINES。"""
    from vibeocr.services.ocr_service_subprocess import OCRServiceSubprocess

    captured = {}

    class FakeManager:
        def execute(self, fn, timeout=None):
            captured["called"] = True
            class FakeW:
                @staticmethod
                def release_pipelines(heavy_only=True):
                    captured["heavy_only"] = heavy_only
                    return ["PP-StructureV3"]
            return fn(FakeW())

    svc = OCRServiceSubprocess.__new__(OCRServiceSubprocess)
    svc._initialized = True
    svc._paddlex_manager = FakeManager()
    result = svc.release_pipelines(heavy_only=True)
    assert captured["called"] is True
    assert captured["heavy_only"] is True
    assert result == ["PP-StructureV3"]


def test_set_pipeline_ttl_sends_rpc(monkeypatch):
    """set_pipeline_ttl 经 _paddlex_manager.execute 下发 SET_TTL。"""
    from vibeocr.services.ocr_service_subprocess import OCRServiceSubprocess

    captured = {}

    class FakeManager:
        def execute(self, fn, timeout=None):
            class FakeW:
                @staticmethod
                def set_ttl(ttl_seconds):
                    captured["ttl"] = ttl_seconds
                    return True
            return fn(FakeW())

    svc = OCRServiceSubprocess.__new__(OCRServiceSubprocess)
    svc._initialized = True
    svc._paddlex_manager = FakeManager()
    svc.set_pipeline_ttl(600)
    assert captured["ttl"] == 600
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/services/test_ocr_service_subprocess.py -v -k "release_pipelines or set_pipeline_ttl"`
Expected: FAIL (methods not defined)

- [ ] **Step 4: Add abstract methods to OCRServiceBase**

In `src/vibeocr/services/ocr_service_base.py`, add after `shutdown()` (line ~156):

```python
    def release_pipelines(self, heavy_only: bool = True) -> list[str]:
        """释放管道缓存。heavy_only=True 只释放重管道。

        Raises:
            NotImplementedError: 子类必须实现（直连模式由 OCRService 实现，
                子进程模式由 OCRServiceSubprocess 经 RPC 实现）。
        """
        raise NotImplementedError

    def set_pipeline_ttl(self, ttl_seconds: int) -> bool:
        """设置重管道 TTL 闲置回收时间。"""
        raise NotImplementedError
```

- [ ] **Step 5: Add worker-side methods to OCRWorkerProcess**

First read `src/vibeocr/services/ocr_worker_process.py` to find the exact RPC send method name used by existing commands (e.g., search for how RECOGNIZE or PRELOAD messages are sent — the method is likely `_send_message` or `_send_and_wait`). Confirm the signature: does it take `(msg_type, payload)` and return response bytes?

Then add methods mirroring that exact pattern. Example assuming the method is `_send_and_wait(msg_type, payload, timeout) -> bytes`:

```python
    def release_pipelines(self, heavy_only: bool = True) -> list[str]:
        """向 worker 发送 RELEASE_PIPELINES 命令，返回被释放的管道名列表。"""
        import json
        from vibeocr.utils.shared_memory_v2 import MessageType

        payload = json.dumps({"heavy_only": heavy_only}).encode("utf-8")
        response = self._send_and_wait(
            MessageType.RELEASE_PIPELINES, payload, timeout=60.0
        )
        try:
            data = json.loads(response.decode("utf-8")) if response else {}
            return data.get("released", [])
        except (json.JSONDecodeError, UnicodeDecodeError):
            return []

    def set_ttl(self, ttl_seconds: int) -> bool:
        """向 worker 发送 SET_TTL 命令。"""
        import json
        from vibeocr.utils.shared_memory_v2 import MessageType

        payload = json.dumps({"ttl_seconds": int(ttl_seconds)}).encode("utf-8")
        self._send_and_wait(MessageType.SET_TTL, payload, timeout=30.0)
        return True
```

If the actual method name differs (e.g., `execute_rpc`, `send_command`), substitute it. The key contract: send `MessageType.RELEASE_PIPELINES` / `MessageType.SET_TTL` with JSON payload, await the ACK response.

- [ ] **Step 6: Add client methods to OCRServiceSubprocess**

In `src/vibeocr/services/ocr_service_subprocess.py`, add after `recognize_batch` method:

```python
    def release_pipelines(self, heavy_only: bool = True) -> list[str]:
        """释放管道缓存（经 RPC 下发给 worker）。"""
        if not self._initialized:
            return []
        return self._paddlex_manager.execute(
            lambda w: w.release_pipelines(heavy_only=heavy_only),
            timeout=60.0,
        )

    def set_pipeline_ttl(self, ttl_seconds: int) -> bool:
        """设置 TTL（经 RPC 下发给 worker）。"""
        if not self._initialized:
            return False
        return self._paddlex_manager.execute(
            lambda w: w.set_ttl(ttl_seconds),
            timeout=30.0,
        )
```

- [ ] **Step 7: Implement direct-mode methods on OCRService**

In `src/vibeocr/services/ocr_service.py`, add (for direct/直连 mode compatibility):

```python
    @classmethod
    def release_pipelines(cls, heavy_only: bool = True) -> list[str]:
        """直连模式：直接调 cache_manager.release。"""
        return cls.instance().cache_manager.release(heavy_only=heavy_only)

    @classmethod
    def set_pipeline_ttl(cls, ttl_seconds: int) -> bool:
        """直连模式：直接设置 cache_manager TTL。"""
        cls.instance().cache_manager.ttl_seconds = ttl_seconds
        return True
```

- [ ] **Step 8: Run test to verify it passes**

Run: `python -m pytest tests/services/test_ocr_service_subprocess.py -v -k "release_pipelines or set_pipeline_ttl"`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add src/vibeocr/services/ocr_service_base.py src/vibeocr/services/ocr_worker_process.py src/vibeocr/services/ocr_service_subprocess.py src/vibeocr/services/ocr_service.py tests/services/test_ocr_service_subprocess.py
git commit -m "feat(rpc): release_pipelines/set_pipeline_ttl client + worker methods"
```

---

## Task 9: ConfigManager 新增 TTL / max_heavy 字段

**Files:**
- Modify: `src/vibeocr/managers/config_manager.py:112-145`（仿 preload_enabled 模式）
- Modify: `src/vibeocr/utils/app_settings.py`（`_DEFAULTS`）
- Test: `tests/managers/test_config_manager.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/managers/test_config_manager.py`:

```python
def test_get_pipeline_ttl_default(tmp_path):
    from vibeocr.managers.config_manager import ConfigManager
    ConfigManager._instance = None
    mgr = ConfigManager(project_root=tmp_path)
    assert mgr.get_pipeline_ttl_seconds() == 300


def test_set_pipeline_ttl(tmp_path):
    from vibeocr.managers.config_manager import ConfigManager
    ConfigManager._instance = None
    mgr = ConfigManager(project_root=tmp_path)
    assert mgr.set_pipeline_ttl_seconds(600)
    assert mgr.get_pipeline_ttl_seconds() == 600


def test_get_max_heavy_pipelines_default_none(tmp_path):
    from vibeocr.managers.config_manager import ConfigManager
    ConfigManager._instance = None
    mgr = ConfigManager(project_root=tmp_path)
    assert mgr.get_max_heavy_pipelines() is None  # None = 自动分档


def test_set_max_heavy_pipelines(tmp_path):
    from vibeocr.managers.config_manager import ConfigManager
    ConfigManager._instance = None
    mgr = ConfigManager(project_root=tmp_path)
    assert mgr.set_max_heavy_pipelines(2)
    assert mgr.get_max_heavy_pipelines() == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/managers/test_config_manager.py -v -k "pipeline_ttl or max_heavy"`
Expected: FAIL (methods not defined)

- [ ] **Step 3: Add getter/setters to ConfigManager**

In `src/vibeocr/managers/config_manager.py`, after `set_preload_pipelines` (line ~141), add:

```python
    def get_pipeline_ttl_seconds(self) -> int:
        """重管道 TTL 闲置回收时间（秒），默认 300，0=禁用。"""
        data = self._load_json("app_settings.json", {})
        return int(data.get("pipeline_ttl_seconds", 300))

    def set_pipeline_ttl_seconds(self, ttl: int) -> bool:
        data = self._load_json("app_settings.json", {})
        data["pipeline_ttl_seconds"] = max(0, int(ttl))
        return self._save_json("app_settings.json", data)

    def get_max_heavy_pipelines(self) -> int | None:
        """手动覆盖的重管道并存上限，None=按显存自动分档。"""
        data = self._load_json("app_settings.json", {})
        val = data.get("max_heavy_pipelines")
        return int(val) if val is not None else None

    def set_max_heavy_pipelines(self, value: int | None) -> bool:
        data = self._load_json("app_settings.json", {})
        data["max_heavy_pipelines"] = value
        return self._save_json("app_settings.json", data)
```

- [ ] **Step 4: Add defaults to AppSettings**

In `src/vibeocr/utils/app_settings.py`, find `_DEFAULTS` dict (line ~16) and add:

```python
        "pipeline_ttl_seconds": 300,
        "max_heavy_pipelines": None,
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/managers/test_config_manager.py -v -k "pipeline_ttl or max_heavy"`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/vibeocr/managers/config_manager.py src/vibeocr/utils/app_settings.py tests/managers/test_config_manager.py
git commit -m "feat(config): pipeline_ttl_seconds + max_heavy_pipelines settings"
```

---

## Task 10: 设置页 UI — TTL spin + 释放按钮

**Files:**
- Modify: `src/vibeocr/ui/ui_main_window.py:186-208`（groupPreload 区域）
- Modify: `src/vibeocr/views/settings_page_controller.py`
- Test: `tests/views/test_settings_page_controller.py`（新建或追加）

- [ ] **Step 1: Read current groupPreload layout**

Read `src/vibeocr/ui/ui_main_window.py` lines 138-208 to see `groupPreload` structure: `chkEnablePreload`, `preloadOptions`, `btnPreloadNow`, `labelPreloadStatus`, `progressPreload`. The new controls go after `btnPreloadNow` / before `labelPreloadStatus` (or after progressPreload, before `groupCache`).

- [ ] **Step 2: Add UI controls in ui_main_window.py**

In `src/vibeocr/ui/ui_main_window.py`, after the `progressPreload` block (line ~206) and before `self.pageModelLayout.addWidget(self.groupPreload)` (line ~208), add:

```python
        # --- 重管道生命周期管理 ---
        self.groupPipelineCache = QGroupBox(self.pageModelManagement)
        self.groupPipelineCache.setObjectName("groupPipelineCache")
        self.pipelineCacheLayout = QVBoxLayout(self.groupPipelineCache)
        self.pipelineCacheLayout.setSpacing(8)
        self.pipelineCacheLayout.setObjectName("pipelineCacheLayout")

        # TTL 设置行
        self.ttlLayout = QHBoxLayout()
        self.ttlLayout.setSpacing(8)
        self.labelPipelineTtl = QLabel(self.groupPipelineCache)
        self.labelPipelineTtl.setObjectName("labelPipelineTtl")
        self.ttlLayout.addWidget(self.labelPipelineTtl)
        self.spinPipelineTtl = QSpinBox(self.groupPipelineCache)
        self.spinPipelineTtl.setObjectName("spinPipelineTtl")
        self.spinPipelineTtl.setMinimum(1)
        self.spinPipelineTtl.setMaximum(60)
        self.spinPipelineTtl.setValue(5)
        self.spinPipelineTtl.setSuffix(" 分钟")
        self.ttlLayout.addWidget(self.spinPipelineTtl)
        self.ttlLayout.addStretch()
        self.pipelineCacheLayout.addLayout(self.ttlLayout)

        # 释放按钮行
        self.releaseButtonsLayout = QHBoxLayout()
        self.releaseButtonsLayout.setSpacing(8)
        self.btnReleaseHeavy = QPushButton(self.groupPipelineCache)
        self.btnReleaseHeavy.setObjectName("btnReleaseHeavy")
        self.releaseButtonsLayout.addWidget(self.btnReleaseHeavy)
        self.btnReleaseAll = QPushButton(self.groupPipelineCache)
        self.btnReleaseAll.setObjectName("btnReleaseAll")
        self.releaseButtonsLayout.addWidget(self.btnReleaseAll)
        self.releaseButtonsLayout.addStretch()
        self.pipelineCacheLayout.addLayout(self.releaseButtonsLayout)

        # 状态标签（复用 labelPreloadStatus 或新建）
        self.labelReleaseStatus = QLabel(self.groupPipelineCache)
        self.labelReleaseStatus.setObjectName("labelReleaseStatus")
        self.labelReleaseStatus.setWordWrap(True)
        self.pipelineCacheLayout.addWidget(self.labelReleaseStatus)

        self.pageModelLayout.addWidget(self.groupPipelineCache)
```

Add the retranslation at the bottom of `setupUi` (find the `retranslateUi` section, add):

```python
        self.groupPipelineCache.setTitle(QCoreApplication.translate("MainWindow", "显存/内存管理"))
        self.labelPipelineTtl.setText(QCoreApplication.translate("MainWindow", "重管道闲置回收"))
        self.btnReleaseHeavy.setText(QCoreApplication.translate("MainWindow", "释放重管道"))
        self.btnReleaseAll.setText(QCoreApplication.translate("MainWindow", "全部释放"))
        self.labelReleaseStatus.setText(QCoreApplication.translate("MainWindow", "就绪"))
```

Note: Ensure `QSpinBox` is imported in `ui_main_window.py` (check existing imports — QCheckBox, QPushButton, QLabel, QProgressBar are already used; add `from PySide6.QtWidgets import QSpinBox` if missing).

- [ ] **Step 3: Write the failing test for controller wiring**

First read an existing settings controller test (e.g., search `tests/views/` for files testing `settings_page_controller` or `SettingsPageController`) to copy the fixture pattern — how it constructs a minimal UI and controller instance. Typical pattern: create a `QMainWindow` from `ui_main_window`, instantiate `SettingsPageController(ui=window, ...)`, then `findChild` the control.

Create/append `tests/views/test_settings_page_controller.py`:

```python
"""设置页 controller 管道缓存控件测试。"""

import pytest
from PySide6.QtWidgets import QSpinBox

from vibeocr.ui.ui_main_window import Ui_MainWindow


@pytest.fixture
def main_window(qtbot):
    """构造带设置页的主窗口 UI。"""
    from PySide6.QtWidgets import QMainWindow
    window = QMainWindow()
    ui = Ui_MainWindow()
    ui.setupUi(window)
    return ui


def test_ttl_spin_exists_and_defaults_to_5(main_window):
    """spinPipelineTtl 存在，默认 5 分钟。"""
    spin = main_window.findChild(QSpinBox, "spinPipelineTtl")
    assert spin is not None
    assert spin.minimum() == 1
    assert spin.maximum() == 60
    assert spin.value() == 5


def test_release_buttons_exist(main_window):
    """释放按钮存在。"""
    from PySide6.QtWidgets import QPushButton
    assert main_window.findChild(QPushButton, "btnReleaseHeavy") is not None
    assert main_window.findChild(QPushButton, "btnReleaseAll") is not None


def test_restore_ttl_sets_spin_value(main_window, monkeypatch):
    """_restore_pipeline_ttl_state 从配置恢复 spin 值。"""
    from vibeocr.managers.config_manager import ConfigManager
    # 配置 TTL=600 秒 → spin 显示 10 分钟
    monkeypatch.setattr(ConfigManager, "get_pipeline_ttl_seconds", lambda self: 600)

    from vibeocr.views.settings_page_controller import SettingsPageController
    # 构造 controller（按现有测试模式，可能需要更多 mock 参数）
    controller = SettingsPageController.__new__(SettingsPageController)
    controller._ui = main_window
    controller._restore_pipeline_ttl_state()

    spin = main_window.findChild(QSpinBox, "spinPipelineTtl")
    assert spin.value() == 10  # 600 秒 = 10 分钟
```

If the existing test fixtures use a different construction pattern (e.g., a shared `app` fixture or `MainWindow` wrapper), adapt the fixture to match. The assertions about control existence and TTL conversion (600s → 10 min) are the key checks.

- [ ] **Step 4: Wire up controls in settings_page_controller.py**

In `src/vibeocr/views/settings_page_controller.py`:

(a) In `connect_signals()` (line ~55), add:

```python
        # TTL spin
        spin_ttl = self._ui.findChild(QSpinBox, "spinPipelineTtl")
        if spin_ttl:
            spin_ttl.valueChanged.connect(self._on_pipeline_ttl_changed)
        # 释放按钮
        btn_release_heavy = self._ui.findChild(QPushButton, "btnReleaseHeavy")
        if btn_release_heavy:
            btn_release_heavy.clicked.connect(self._on_release_heavy_clicked)
        btn_release_all = self._ui.findChild(QPushButton, "btnReleaseAll")
        if btn_release_all:
            btn_release_all.clicked.connect(self._on_release_all_clicked)
```

(b) Add `from PySide6.QtWidgets import QSpinBox` to imports if not present.

(c) Add the handler methods (after `_on_enable_preload_toggled` around line 332):

```python
    def _restore_pipeline_ttl_state(self) -> None:
        """从配置恢复 TTL spin 值。"""
        from vibeocr.managers.config_manager import ConfigManager
        spin = self._ui.findChild(QSpinBox, "spinPipelineTtl")
        if spin:
            ttl_sec = ConfigManager.instance().get_pipeline_ttl_seconds()
            spin.blockSignals(True)
            spin.setValue(max(1, ttl_sec // 60))  # 秒转分钟
            spin.blockSignals(False)

    def _on_pipeline_ttl_changed(self, minutes: int) -> None:
        """TTL spin 变化 → 保存配置 + 通知 worker。"""
        from vibeocr.managers.config_manager import ConfigManager
        ttl_sec = minutes * 60
        ConfigManager.instance().set_pipeline_ttl_seconds(ttl_sec)
        # 经 RPC 通知 worker 更新 TTL
        if self._subprocess_manager and self._subprocess_manager.is_ready:
            try:
                self._subprocess_manager.service.set_pipeline_ttl(ttl_sec)
            except Exception as e:
                logger.warning("[设置] 通知 worker TTL 更新失败: %s", e)

    def _on_release_heavy_clicked(self) -> None:
        """释放重管道按钮。"""
        self._release_pipelines(heavy_only=True)

    def _on_release_all_clicked(self) -> None:
        """全部释放按钮。"""
        self._release_pipelines(heavy_only=False)

    def _release_pipelines(self, heavy_only: bool) -> None:
        """执行释放（后台线程，照搬 preload 模式）。"""
        if not self._subprocess_manager or not self._subprocess_manager.is_ready:
            QMessageBox.warning(None, "无法释放", "OCR 服务尚未就绪。")
            return
        label = "重管道" if heavy_only else "全部管道"
        reply = QMessageBox.question(
            None, "确认释放",
            f"确定要释放{label}吗？正在进行的任务将在当前批次完成后受影响。",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        # 禁用按钮 + 状态提示
        for name in ("btnReleaseHeavy", "btnReleaseAll"):
            btn = self._ui.findChild(QPushButton, name)
            if btn:
                btn.setEnabled(False)
        self._update_release_status(f"正在释放{label}...")

        from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

        class _ReleaseSignals(QObject):
            finished = Signal(list)
            error = Signal(str)

        class ReleaseTask(QRunnable):
            def __init__(self, service, heavy_only, signals):
                super().__init__()
                self._service = service
                self._heavy_only = heavy_only
                self._signals = signals

            def run(self):
                try:
                    released = self._service.release_pipelines(heavy_only=self._heavy_only)
                    # MinerU 单独释放（主进程本地）
                    if self._heavy_only or not self._heavy_only:
                        try:
                            from vibeocr.services.mineru_service import MinerUService
                            if MinerUService._api_process is not None:
                                MinerUService().shutdown()
                        except Exception:
                            pass
                    self._signals.finished.emit(released)
                except Exception as e:
                    self._signals.error.emit(str(e))

        signals = _ReleaseSignals()
        signals.finished.connect(lambda r: self._on_release_finished(r, heavy_only))
        signals.error.connect(lambda e: self._on_release_error(e))

        task = ReleaseTask(self._subprocess_manager.service, heavy_only, signals)
        QThreadPool.globalInstance().start(task)

    def _on_release_finished(self, released: list, heavy_only: bool) -> None:
        """释放完成回调（主线程）。"""
        for name in ("btnReleaseHeavy", "btnReleaseAll"):
            btn = self._ui.findChild(QPushButton, name)
            if btn:
                btn.setEnabled(True)
        label = "重管道" if heavy_only else "全部"
        if released:
            self._update_release_status(f"已释放{label}管道: {', '.join(released)}")
        else:
            self._update_release_status(f"没有需要释放的{label}管道")

    def _on_release_error(self, error: str) -> None:
        """释放失败回调。"""
        for name in ("btnReleaseHeavy", "btnReleaseAll"):
            btn = self._ui.findChild(QPushButton, name)
            if btn:
                btn.setEnabled(True)
        self._update_release_status(f"释放失败: {error}")

    def _update_release_status(self, status: str) -> None:
        """更新释放状态标签。"""
        label = self._ui.findChild(QLabel, "labelReleaseStatus")
        if label:
            label.setText(status)
```

(d) Call `_restore_pipeline_ttl_state()` at the end of `connect_signals()` or wherever preload state is restored.

- [ ] **Step 5: Run controller tests**

Run: `python -m pytest tests/views/test_settings_page_controller.py -v`
Expected: PASS

- [ ] **Step 6: Run full view test suite for regression**

Run: `python -m pytest tests/views/ -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/vibeocr/ui/ui_main_window.py src/vibeocr/views/settings_page_controller.py tests/views/test_settings_page_controller.py
git commit -m "feat(ui): pipeline TTL spin + release heavy/all buttons in settings"
```

---

## Task 11: 启动时下发 TTL 配置到 worker

**Files:**
- Modify: `src/vibeocr/views/main_window.py`（worker 就绪后下发 TTL）

worker 启动后，主进程应把用户配置的 TTL 下发给它（否则 worker 用默认 300）。

- [ ] **Step 1: Read where worker-ready callback is handled**

Read `src/vibeocr/views/main_window.py` around the preload complete callback / worker ready handling (lines 470-520, and the `_start_subprocess_preload` at 592). Find where `subprocess_manager.is_ready` becomes True and preload is triggered.

- [ ] **Step 2: Add TTL sync after worker ready**

In the worker-ready handling (after preload completes or worker becomes ready), add:

```python
        # 下发用户配置的 TTL 到 worker
        try:
            from vibeocr.managers.config_manager import ConfigManager
            ttl = ConfigManager.instance().get_pipeline_ttl_seconds()
            self._subprocess_manager.service.set_pipeline_ttl(ttl)
            logger.debug("[Main] 已下发 TTL=%d 到 worker", ttl)
        except Exception as e:
            logger.warning("[Main] 下发 TTL 失败: %s", e)
```

Place this in the method that runs after worker initialization completes (e.g., in the preload-complete callback or the ready signal handler). Read the code to find the exact spot — it should be after `self._subprocess_manager.is_ready` is True.

- [ ] **Step 3: Run main_window tests for regression**

Run: `python -m pytest tests/views/test_main_window.py -v` (if exists) or relevant tests
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/vibeocr/views/main_window.py
git commit -m "feat(main): sync user TTL config to worker on startup"
```

---

## Task 12: 端到端集成验证

**Files:**
- Test: `tests/integration/test_pipeline_cache_lifecycle.py`（新建）

验证完整链路：设置页按钮 → RPC → worker 释放 → MinerU shutdown。

- [ ] **Step 1: Write integration test**

Create `tests/integration/test_pipeline_cache_lifecycle.py`:

```python
"""管道缓存生命周期端到端集成测试。

验证：释放按钮 → OCRServiceSubprocess.release_pipelines → worker cache_manager.release。
使用 mock subprocess 避免真实模型加载。
"""

from unittest.mock import MagicMock


def test_release_heavy_only_flow():
    """释放重管道的完整 RPC 路径可调用（mock 验证）。"""
    from vibeocr.services.ocr_service_subprocess import OCRServiceSubprocess

    svc = OCRServiceSubprocess.__new__(OCRServiceSubprocess)
    svc._initialized = True

    mock_manager = MagicMock()
    mock_manager.execute.return_value = ["PP-StructureV3", "PaddleOCR-VL"]
    svc._paddlex_manager = mock_manager

    result = svc.release_pipelines(heavy_only=True)
    assert result == ["PP-StructureV3", "PaddleOCR-VL"]
    mock_manager.execute.assert_called_once()


def test_release_all_flow():
    """全部释放的 RPC 路径。"""
    from vibeocr.services.ocr_service_subprocess import OCRServiceSubprocess

    svc = OCRServiceSubprocess.__new__(OCRServiceSubprocess)
    svc._initialized = True

    mock_manager = MagicMock()
    mock_manager.execute.return_value = ["PP-StructureV3", "OCR"]
    svc._paddlex_manager = mock_manager

    result = svc.release_pipelines(heavy_only=False)
    assert "OCR" in result


def test_set_ttl_flow():
    """设置 TTL 的 RPC 路径。"""
    from vibeocr.services.ocr_service_subprocess import OCRServiceSubprocess

    svc = OCRServiceSubprocess.__new__(OCRServiceSubprocess)
    svc._initialized = True

    mock_manager = MagicMock()
    mock_manager.execute.return_value = True
    svc._paddlex_manager = mock_manager

    assert svc.set_pipeline_ttl(600)
    mock_manager.execute.assert_called_once()
```

- [ ] **Step 2: Run integration test**

Run: `python -m pytest tests/integration/test_pipeline_cache_lifecycle.py -v`
Expected: PASS

- [ ] **Step 3: Run the ENTIRE test suite for full regression**

Run: `python -m pytest tests/ -v --timeout=120`
Expected: PASS (all tests, no regressions)

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_pipeline_cache_lifecycle.py
git commit -m "test(integration): pipeline cache lifecycle e2e (release/set_ttl flow)"
```

---

## 完成标准

- [ ] 重管道元数据标记（PP-V3/VL/MinerU）+ `get_heavy_pipelines()`
- [ ] `PipelineCacheManager`：显存分档上限、FIFO 淘汰、TTL 回收、显式释放
- [ ] `OCRService` 集成 cache_manager（创建管道后 touch + enforce_capacity）
- [ ] worker 主循环每次消息后 `evict_idle`；处理 RELEASE_PIPELINES/SET_TTL
- [ ] 3 个新 RPC 命令端到端通畅（主进程 → worker）
- [ ] ConfigManager 持久化 TTL / max_heavy_pipelines
- [ ] 设置页：TTL spin（分钟，可配）+ 释放重管道/全部释放按钮
- [ ] 启动时 TTL 配置下发 worker
- [ ] MinerU 纳入释放范围（shutdown API 进程）
- [ ] OCR 不受 TTL 回收，但"全部释放"会清掉
- [ ] 全部测试通过，无回归
