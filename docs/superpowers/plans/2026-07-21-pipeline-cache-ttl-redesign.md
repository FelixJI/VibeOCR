# 管道缓存 TTL 重构 + Bug 修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把单值 `pipeline_ttl_seconds` 重构为每管道独立 `pipeline_ttls: dict[str, int]`（0=持久），用后台 tick 线程替代耦合读超时的懒回收，MinerU 从并存上限移出，并修 4 个配套 bug。

**Architecture:** contracts 层加 `cache_kind` 元数据区分 paddle/mineru 回收路径；manager 内置 daemon 线程每 30s tick（空缓存阻塞唤醒）；MSG_SET_TTL payload 硬切到 dict 格式，协议契约三方（schema/golden/method_validation/C#）原子升级；UI 用 6 个 ComboBox 替代单值 spinner。

**Tech Stack:** Python 3.13, PySide6, threading, 共享内存 RPC（shared_memory_v2）, JSON Schema Draft 2020-12, C# .NET（contracts 测试）

**Spec:** `docs/superpowers/specs/2026-07-21-pipeline-cache-ttl-redesign-design.md`

---

## Global Constraints

每个任务的实现都必须满足以下项目级约束（来自 spec 第八节门控合规）：

- **Phase 0 门禁全绿**：`uv sync --frozen --group dev` / `uv run pytest -q` / `uv run ruff check src tests scripts` / `uv run pyright`，全部 0 error。
- **Phase 1 门禁全绿**（涉及协议层时）：`pytest tests/contracts tests/worker_host` / `ruff` / `pyright` / `worker_host --self-test` / `dotnet restore --locked-mode` / `dotnet test VibeOCR.Contracts.Tests`。
- **Python 3.13**，类型注解完整（`pyright` 标准 mode，`reportUnusedImport="error"`、`reportDuplicateImport="error"`）。
- **Ruff 规则**：`F`/`B`/`TCH`/`PTH`/`RET`/`COM` 全启用；mutable default args 禁用；TYPE_CHECKING 块外非必要 runtime import 会被建议移入。
- **协议契约三方一致**：`methods.schema.json` + `src/dotnet/VibeOCR.Contracts/RpcMethods.cs` + `worker_host/method_validation.py` 的 method 名集合必须完全一致（由 `tests/architecture/test_protocol_method_consistency.py` 强制）。
- **UI 线程阻塞守卫**：`tests/architecture/test_ui_thread_blocking_boundaries.py` AST 扫描 UI 入口函数，禁用特定阻塞调用。
- **报告脱敏**：测试输出不得包含本机绝对路径（`UserProfile`、绝对盘符），用 `Path.relative_to(project_root)` 相对化。
- **路径约定**：真实元数据定义在 `packages/vibeocr-contracts-py/src/vibeocr/contracts/pipelines.py`；`packages/vibeocr-client-py/src/vibeocr/core/pipelines/__init__.py` 是 re-export shim。
- **TDD**：每任务先写失败测试，跑红，再实现，跑绿，commit。
- **不删 method 名**：`pipeline_cache.set_ttl` method 名**保持不变**，只改 payload schema。

---

## File Structure

| 文件 | 操作 | 职责 |
|---|---|---|
| `packages/vibeocr-contracts-py/src/vibeocr/contracts/pipelines.py` | 修改 | 加 `cache_kind` + `get_paddle_pipelines` / `get_mineru_pipelines` |
| `packages/vibeocr-client-py/src/vibeocr/core/pipelines/__init__.py` | 修改 | re-export 新函数 |
| `packages/vibeocr-backend/src/vibeocr/services/pipeline_cache_manager.py` | 修改 | 每管道 TTL + 后台线程 + MinerU 分流 + 8GB 分档 |
| `packages/vibeocr-backend/src/vibeocr/services/ocr_service.py` | 修改 | `set_pipeline_ttls` 替代单值 |
| `packages/vibeocr-backend/src/vibeocr/services/ocr_service_base.py` | 修改 | 抽象接口签名 |
| `packages/vibeocr-backend/src/vibeocr/services/ocr_service_subprocess.py` | 修改 | RPC 客户端封装 |
| `packages/vibeocr-backend/src/vibeocr/services/ocr_worker_process.py` | 修改 | RPC 写入端 `set_ttls` |
| `packages/vibeocr-backend/src/vibeocr/workers/ocr_worker.py` | 修改 | 主循环删懒回收 + shutdown |
| `packages/vibeocr-backend/src/vibeocr/worker_host/composition.py` | 修改 | `set_pipeline_ttls` + `SettingsSnapshot.pipeline_ttls` |
| `packages/vibeocr-backend/src/vibeocr/worker_host/handlers/pipeline_cache.py` | 修改 | handler 签名 |
| `packages/vibeocr-backend/src/vibeocr/worker_host/method_validation.py` | 修改 | payload schema 校验（dict） |
| `packages/vibeocr-contracts-py/src/vibeocr/protocol/v1/methods.schema.json` | 修改 | payload schema 升级 |
| `packages/vibeocr-contracts-py/src/vibeocr/protocol/v1/golden.json` | 修改 | golden 样例升级 |
| `packages/vibeocr-client-py/src/vibeocr/machine_cache.py` | 修改 | Bug 2/3（refresh_cache 重检测 + warmup_machine_id） |
| `packages/vibeocr-client-py/src/vibeocr/env_manager.py` | 检查 | 调用点核查（不预期改） |
| `apps/vibeocr-pyside/src/vibeocr/managers/config_manager.py` | 修改 | 新 API + 迁移 |
| `apps/vibeocr-pyside/src/vibeocr/managers/subprocess_manager.py` | 修改 | 下发 dict |
| `apps/vibeocr-pyside/src/vibeocr/views/settings_page_controller.py` | 修改 | UI 6 ComboBox + Bug 1 文案 + Bug 2 重检测 |
| `apps/vibeocr-pyside/src/vibeocr/views/main_window.py` | 修改 | 改读 `pipeline_ttls` |
| `tests/services/test_pipeline_cache_manager.py` | 修改 | 扩展（每管道 TTL + 后台线程） |
| `tests/managers/test_config_manager.py` | 修改 | 迁移逻辑 |
| `tests/integration/test_pipeline_cache_lifecycle.py` | 修改 | 适配新协议 |
| `tests/contracts/test_json_schema.py` | （可能修改） | 若 schema 测试硬编码旧字段 |
| `.vibeocr/model_cache.json` | 删除 | Bug 4 死文件 |

---

## Task 1: contracts 层加 `cache_kind` 元数据

**Files:**
- Modify: `packages/vibeocr-contracts-py/src/vibeocr/contracts/pipelines.py`
- Test: `tests/core/test_pipelines_metadata.py`（若存在则追加，否则新建）

**Interfaces:**
- Produces: `get_paddle_pipelines() -> list[OCRPipeline]`、`get_mineru_pipelines() -> list[OCRPipeline]`；`OCRPipeline` 元数据新增 `cache_kind: "paddle" | "mineru"` 字段

- [ ] **Step 1: Read current pipelines.py metadata**

Read `packages/vibeocr-contracts-py/src/vibeocr/contracts/pipelines.py:1-167` to confirm `_PIPELINE_METADATA` dict structure and the existing query functions.

- [ ] **Step 2: Write the failing test**

Create/append `tests/core/test_pipelines_metadata.py`:

```python
"""管道元数据 cache_kind 分类测试。"""

from vibeocr.contracts.pipelines import (
    OCRPipeline,
    get_mineru_pipelines,
    get_paddle_pipelines,
)


def test_every_pipeline_has_cache_kind() -> None:
    """每个管道元数据必须含 cache_kind 字段，值为 paddle 或 mineru。"""
    from vibeocr.contracts.pipelines import _PIPELINE_METADATA

    for pipeline in OCRPipeline:
        kind = _PIPELINE_METADATA[pipeline].get("cache_kind")
        assert kind in {"paddle", "mineru"}, (
            f"{pipeline.name} cache_kind 缺失或非法: {kind!r}"
        )


def test_paddle_pipelines_are_five() -> None:
    """paddle 系管道 = OCR + 表格 + 公式 + PP-StructureV3 + PaddleOCR-VL。"""
    paddle = {p.value for p in get_paddle_pipelines()}
    assert paddle == {
        "OCR",
        "TABLE_RECOGNITION",
        "FORMULA_RECOGNITION",
        "PP-StructureV3",
        "PaddleOCR-VL",
    }


def test_mineru_pipelines_are_one() -> None:
    """mineru 系管道 = 仅 DOCUMENT_PARSING (MinerU)。"""
    mineru = {p.value for p in get_mineru_pipelines()}
    assert mineru == {"MinerU"}


def test_paddle_and_mineru_partition_all_pipelines() -> None:
    """paddle ∪ mineru = 全部 6 管道，且不相交。"""
    from vibeocr.contracts.pipelines import get_all_pipelines

    paddle = set(get_paddle_pipelines())
    mineru = set(get_mineru_pipelines())
    all_pipelines = set(get_all_pipelines())
    assert paddle | mineru == all_pipelines
    assert paddle & mineru == set()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/core/test_pipelines_metadata.py -v`
Expected: FAIL with `ImportError: cannot import name 'get_paddle_pipelines'`

- [ ] **Step 4: Add `cache_kind` to metadata**

Edit `packages/vibeocr-contracts-py/src/vibeocr/contracts/pipelines.py`. In `_PIPELINE_METADATA`, add `"cache_kind"` to each entry. Final entries should look like:

```python
_PIPELINE_METADATA: dict[OCRPipeline, dict[str, Any]] = {
    OCRPipeline.OCR: {
        "display_name": "通用 OCR",
        "short_name": "文字",
        "preloadable": True,
        "heavy": False,
        "cache_kind": "paddle",
        "description": "识别图片中的文字内容，适用于纯文本场景",
        "supported_options": [...],  # 保持不变
    },
    OCRPipeline.PP_STRUCTURE_V3: {
        ...
        "heavy": True,
        "cache_kind": "paddle",
        ...
    },
    OCRPipeline.DOCUMENT_PARSING: {
        ...
        "heavy": True,
        "cache_kind": "mineru",
        ...
    },
    OCRPipeline.PADDLEOCR_VL: {
        ...
        "heavy": True,
        "cache_kind": "paddle",
        ...
    },
    OCRPipeline.TABLE_RECOGNITION: {
        ...
        "heavy": False,
        "cache_kind": "paddle",
        ...
    },
    OCRPipeline.FORMULA_RECOGNITION: {
        ...
        "heavy": False,
        "cache_kind": "paddle",
        ...
    },
}
```

Then append the two query functions before `__all__`:

```python
def get_paddle_pipelines() -> list[OCRPipeline]:
    """走 paddle 回收路径的管道（del + paddle.device.cuda.empty_cache）。"""
    return [p for p in OCRPipeline if _metadata(p).get("cache_kind") == "paddle"]


def get_mineru_pipelines() -> list[OCRPipeline]:
    """走 mineru 回收路径的管道（仅移除 httpx 代理，不调 empty_cache）。"""
    return [p for p in OCRPipeline if _metadata(p).get("cache_kind") == "mineru"]
```

Update `__all__` to include `"get_mineru_pipelines"` and `"get_paddle_pipelines"` (alphabetical order to satisfy ruff isort).

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/core/test_pipelines_metadata.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Update re-export shim**

Edit `packages/vibeocr-client-py/src/vibeocr/core/pipelines/__init__.py`. In the `from vibeocr.contracts.pipelines import (...)` block, add `get_mineru_pipelines` and `get_paddle_pipelines` (alphabetical). Update the shim's `__all__` if it lists exports explicitly.

- [ ] **Step 7: Run Phase 0 lint + types**

Run: `uv run ruff check packages/vibeocr-contracts-py/src packages/vibeocr-client-py/src tests/core/test_pipelines_metadata.py`
Expected: 0 errors
Run: `uv run pyright packages/vibeocr-contracts-py/src packages/vibeocr-client-py/src`
Expected: 0 errors

- [ ] **Step 8: Commit**

```bash
git add packages/vibeocr-contracts-py/src/vibeocr/contracts/pipelines.py \
        packages/vibeocr-client-py/src/vibeocr/core/pipelines/__init__.py \
        tests/core/test_pipelines_metadata.py
git commit -m "feat(contracts): add cache_kind metadata + paddle/mineru partition"
```

---

## Task 2: `PipelineCacheManager` 每管道 TTL + 后台线程

**Files:**
- Modify: `packages/vibeocr-backend/src/vibeocr/services/pipeline_cache_manager.py`
- Test: `tests/services/test_pipeline_cache_manager.py`

**Interfaces:**
- Consumes: Task 1 的 `get_paddle_pipelines` / `get_mineru_pipelines`
- Produces: `PipelineCacheManager(service, ttls: dict[str, int], max_heavy=None, tick_interval=30.0)`；`ttls` property/setter；`shutdown()`；`status()` 返回 `pipeline_ttls`

- [ ] **Step 1: Read current pipeline_cache_manager.py**

Read `packages/vibeocr-backend/src/vibeocr/services/pipeline_cache_manager.py:1-248` 完整文件。

- [ ] **Step 2: Write failing tests for per-pipeline TTL**

Append to `tests/services/test_pipeline_cache_manager.py`:

```python
import threading
import time
from unittest.mock import MagicMock


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
    mgr._last_used: dict[str, float] = {}
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
```

- [ ] **Step 3: Run tests to verify failure**

Run: `uv run pytest tests/services/test_pipeline_cache_manager.py -v`
Expected: FAIL（多个新测试 import error 或 assertion fail）

- [ ] **Step 4: Rewrite pipeline_cache_manager.py**

Replace the entire file content of `packages/vibeocr-backend/src/vibeocr/services/pipeline_cache_manager.py` with:

```python
"""管道缓存生命周期管理（在 worker 子进程内运行）。

接管 OCRService._pipelines 的生命周期：
- 记录每个管道的 last_used 时间戳
- FIFO 淘汰（超并存上限时淘汰最久未用的 paddle 重管道；MinerU 不计入）
- TTL 闲置回收（后台线程每 30s tick，空缓存阻塞唤醒）
- 显式释放（release）
- 按 cache_kind 分流回收：paddle 调 paddle.device.cuda.empty_cache()，mineru 不调
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vibeocr.services.ocr_service import OCRService

logger = logging.getLogger(__name__)

#: 显存分档阈值（MB）。≤8GB=1 并存，>8GB=2 并存。
VRAM_TIER_8GB = 8192
#: pynvml 不可用时的回退并存上限（保守，防 OOM）。
FALLBACK_MAX_HEAVY = 1


def compute_max_heavy_by_vram(total_vram_mb: int) -> int:
    """按显存计算 paddle 重管道并存上限。

    Args:
        total_vram_mb: GPU 显存总量（MB），0 表示无法读取。

    Returns:
        并存上限：≤8G=1, >8G=2, 未知=1。
    """
    if total_vram_mb <= 0:
        return FALLBACK_MAX_HEAVY
    if total_vram_mb <= VRAM_TIER_8GB:
        return 1
    return 2


class PipelineCacheManager:
    """管道缓存生命周期管理器。

    在 worker 子进程内实例化，由 OCRService 持有。
    """

    def __init__(
        self,
        service: OCRService,
        ttls: dict[str, int],
        max_heavy: int | None = None,
        tick_interval: float = 30.0,
    ) -> None:
        self._service = service
        self._ttls = dict(ttls)
        self._last_used: dict[str, float] = {}
        self._max_heavy = (
            max_heavy if max_heavy is not None else self._detect_max_heavy()
        )
        self._tick_interval = tick_interval
        self._stop_event = threading.Event()
        self._wakeup_event = threading.Event()
        self._thread = threading.Thread(
            target=self._tick_loop,
            name="PipelineTTLWatcher",
            daemon=True,
        )
        self._thread.start()

    def _detect_max_heavy(self) -> int:
        """读 GPU 显存总量算并存上限，失败回退。

        CPU 模式（VIBEOCR_USE_GPU != true）固定返回 1（串行更稳）。
        """
        if os.environ.get("VIBEOCR_USE_GPU", "").lower() != "true":
            return 1
        try:
            from vibeocr.utils.gpu_memory_monitor import GPUMemoryMonitor

            info = GPUMemoryMonitor().get_status()
            if info.available and info.total > 0:
                return compute_max_heavy_by_vram(info.total)
        except Exception as e:
            logger.warning(
                "[CacheManager] 检测显存失败，回退上限 %d: %s",
                FALLBACK_MAX_HEAVY,
                e,
            )
        return FALLBACK_MAX_HEAVY

    # ------------------------------------------------------------------
    # 公共属性
    # ------------------------------------------------------------------
    @property
    def ttls(self) -> dict[str, int]:
        return dict(self._ttls)

    @ttls.setter
    def ttls(self, value: dict[str, int]) -> None:
        from vibeocr.core.pipelines import get_all_pipelines

        valid_names = {p.value for p in get_all_pipelines()}
        validated: dict[str, int] = {}
        for name, ttl in value.items():
            if name not in valid_names:
                logger.warning("[CacheManager] 忽略未知管道 TTL: %s", name)
                continue
            validated[name] = max(0, int(ttl))
        self._ttls = validated

    @property
    def max_heavy(self) -> int:
        return self._max_heavy

    # ------------------------------------------------------------------
    # 时间戳 / 容量管理
    # ------------------------------------------------------------------
    def touch(self, pipeline_name: str, now: float | None = None) -> None:
        """记录管道使用时间。每次 get_or_create_pipeline 后调用。"""
        self._last_used[pipeline_name] = now if now is not None else time.time()
        self._wakeup_event.set()

    def get_last_used(self, pipeline_name: str) -> float | None:
        return self._last_used.get(pipeline_name)

    def enforce_capacity(
        self, new_pipeline: str, now: float | None = None
    ) -> list[str]:
        """加载新 paddle 重管道前，FIFO 淘汰至不超并存上限。

        只淘汰 paddle 重管道，不动 OCR/表格/公式（轻）和 MinerU（不计名额）。
        不淘汰 new_pipeline 本身。

        Args:
            new_pipeline: 即将加载的管道名（排除在淘汰候选外）。
            now: 当前时间戳（测试注入用）。

        Returns:
            被释放的管道名列表。
        """
        now = now if now is not None else time.time()
        from vibeocr.core.pipelines import get_paddle_pipelines

        paddle_names = {p.value for p in get_paddle_pipelines()}
        from vibeocr.core.pipelines import get_heavy_pipelines

        heavy_paddle_names = paddle_names & {p.value for p in get_heavy_pipelines()}
        cached_heavy = [
            name
            for name in self._service._pipelines
            if name in heavy_paddle_names and name != new_pipeline
        ]
        evicted: list[str] = []
        while len(cached_heavy) >= self._max_heavy:
            cached_heavy.sort(key=lambda n: self._last_used.get(n, 0.0))
            victim = cached_heavy.pop(0)
            self._release_one(victim)
            evicted.append(victim)
        return evicted

    def evict_idle(self, now: float | None = None) -> list[str]:
        """回收闲置超 TTL 的管道。

        ttl<=0 的管道（含所有持久管道、所有 MinerU 默认配置）不回收。
        回收动作按 cache_kind 分流：paddle 调 empty_cache，mineru 不调。

        Args:
            now: 当前时间戳（测试注入用）。

        Returns:
            被释放的管道名列表。
        """
        now = now if now is not None else time.time()
        evicted: list[str] = []
        for name in list(self._service._pipelines.keys()):
            ttl = self._ttls.get(name, 0)
            if ttl <= 0:
                continue
            last = self._last_used.get(name, 0.0)
            if last + ttl < now:
                self._release_one(name)
                evicted.append(name)
        if evicted:
            logger.info(
                "[CacheManager] TTL 回收 %d 个闲置管道: %s",
                len(evicted),
                evicted,
            )
        return evicted

    def release(self, heavy_only: bool = True) -> list[str]:
        """显式释放管道。

        Args:
            heavy_only: True 只释放重管道，False 释放全部。

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
        logger.info(
            "[CacheManager] release(heavy_only=%s) 释放 %d 个管道: %s",
            heavy_only,
            len(released),
            released,
        )
        return released

    def release_one(self, pipeline_name: str) -> bool:
        """显式释放单个管道并清理其使用记录。

        供运行时兼容回退使用：只丢弃发生错误的管道，不影响其他已加载模型。

        Returns:
            管道原本存在并已释放时返回 True；不存在时返回 False。
        """
        existed = pipeline_name in self._service._pipelines
        self._release_one(pipeline_name)
        if existed:
            logger.info("[CacheManager] 释放单个管道: %s", pipeline_name)
        return existed

    def status(self) -> dict[str, object]:
        """Return an immutable wire-friendly snapshot of the real worker cache."""
        loaded = sorted(str(name) for name in self._service._pipelines)
        return {
            "pipeline_ttls": dict(self._ttls),
            "max_heavy": self._max_heavy,
            "loaded_pipelines": loaded,
            "last_used_unix_ms": {
                name: int(self._last_used[name] * 1000)
                for name in loaded
                if name in self._last_used
            },
        }

    def shutdown(self) -> None:
        """停止后台 tick 线程，等待最多 2 秒退出。"""
        self._stop_event.set()
        self._wakeup_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    # ------------------------------------------------------------------
    # 后台线程
    # ------------------------------------------------------------------
    def _tick_loop(self) -> None:
        """每 tick_interval 秒做一次 evict_idle；空缓存阻塞唤醒。"""
        while not self._stop_event.is_set():
            if not self._service._pipelines:
                # 空缓存：阻塞等新管道加载，避免周期空转
                self._wakeup_event.wait(timeout=60.0)
                self._wakeup_event.clear()
                continue
            try:
                self.evict_idle()
            except Exception as e:
                logger.warning("[CacheManager] tick evict_idle 失败: %s", e)
            self._stop_event.wait(self._tick_interval)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _release_one(self, pipeline_name: str) -> None:
        """释放单个管道，按 cache_kind 决定是否调 empty_cache。"""
        self._service._pipelines.pop(pipeline_name, None)
        self._last_used.pop(pipeline_name, None)
        if self._is_paddle(pipeline_name):
            self._empty_cache()

    @staticmethod
    def _is_paddle(pipeline_name: str) -> bool:
        from vibeocr.core.pipelines import get_paddle_pipelines

        return pipeline_name in {p.value for p in get_paddle_pipelines()}

    @staticmethod
    def _empty_cache() -> None:
        """GPU 模式下回收显存碎片。"""
        try:
            if os.environ.get("VIBEOCR_USE_GPU", "").lower() == "true":
                import paddle

                paddle.device.cuda.empty_cache()
        except Exception as e:
            logger.debug("[CacheManager] empty_cache 跳过: %s", e)
```

- [ ] **Step 5: Run tests to verify pass**

Run: `uv run pytest tests/services/test_pipeline_cache_manager.py -v`
Expected: PASS（新加的 8 个测试 + 现有测试若因 API 变更需要更新则一并修）

如果现有测试因 `ttl_seconds` 单值 API 移除而 fail，更新它们（Task 9 会全量适配，但本任务保证本地测试集绿）。

- [ ] **Step 6: Run Phase 0 lint + types**

Run: `uv run ruff check packages/vibeocr-backend/src/vibeocr/services/pipeline_cache_manager.py tests/services/test_pipeline_cache_manager.py`
Run: `uv run pyright packages/vibeocr-backend/src/vibeocr/services/pipeline_cache_manager.py`
Expected: 0 errors

- [ ] **Step 7: Commit**

```bash
git add packages/vibeocr-backend/src/vibeocr/services/pipeline_cache_manager.py \
        tests/services/test_pipeline_cache_manager.py
git commit -m "refactor(cache): per-pipeline TTL + background tick thread + mineru cache_kind split"
```

---

## Task 3: 后台线程行为测试（空缓存阻塞 + shutdown）

**Files:**
- Test: `tests/services/test_pipeline_cache_manager.py`（追加）

**Interfaces:**
- Consumes: Task 2 的 `PipelineCacheManager.__init__(tick_interval=...)`、`shutdown()`

- [ ] **Step 1: Write failing tests for background thread behavior**

Append to `tests/services/test_pipeline_cache_manager.py`:

```python
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


def test_shutdown_joins_thread_cleanly() -> None:
    """shutdown() 后线程在 2s 内退出。"""
    monkeypatch.setenv("VIBEOCR_USE_GPU", "false")  # noqa: F821
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
        time.sleep(0.3)
        assert "PP-StructureV3" not in svc._pipelines  # 被回收
    finally:
        mgr.shutdown()
```

注意：`test_shutdown_joins_thread_cleanly` 里 `monkeypatch` 行的 `# noqa: F821` 是占位——实际实现时把 `monkeypatch` 参数加到函数签名（`def test_shutdown_joins_thread_cleanly(monkeypatch) -> None:`），删掉 noqa 行。

- [ ] **Step 2: Run tests to verify pass**

Run: `uv run pytest tests/services/test_pipeline_cache_manager.py::test_background_tick_evicts_after_ttl tests/services/test_pipeline_cache_manager.py::test_shutdown_joins_thread_cleanly tests/services/test_pipeline_cache_manager.py::test_touch_wakes_blocked_thread -v`
Expected: PASS（3 tests）。若失败，核查 `touch` 是否调 `_wakeup_event.set()`，以及 `_tick_loop` 的阻塞逻辑。

- [ ] **Step 3: Run Phase 0 lint**

Run: `uv run ruff check tests/services/test_pipeline_cache_manager.py`
Expected: 0 errors

- [ ] **Step 4: Commit**

```bash
git add tests/services/test_pipeline_cache_manager.py
git commit -m "test(cache): background tick thread wake/shutdown behavior"
```

---

## Task 4: worker 主循环删懒回收 + shutdown

**Files:**
- Modify: `packages/vibeocr-backend/src/vibeocr/workers/ocr_worker.py`

**Interfaces:**
- Consumes: Task 2 的 `PipelineCacheManager.shutdown()`

- [ ] **Step 1: Read worker main loop**

Read `packages/vibeocr-backend/src/vibeocr/workers/ocr_worker.py:283-720`（主循环 + finally 块）。

- [ ] **Step 2: Remove lazy evict_idle calls**

In `packages/vibeocr-backend/src/vibeocr/workers/ocr_worker.py`:

删除 `:695-699`（消息处理后调 evict_idle 的 try/except 块）：

```python
# 删除以下代码（约行 695-699）：
                # 每次处理完消息后检查闲置管道回收
                try:
                    ocr_service.cache_manager.evict_idle()
                except Exception as ev_err:
                    logger.debug("[Worker] evict_idle 失败: %s", ev_err)
```

删除 `:701-708`（读超时分支调 evict_idle）：

```python
# 删除以下代码（约行 701-708 的 evict_idle 调用部分）：
                    # 读取超时，顺便检查闲置管道回收
                    try:
                        ocr_service.cache_manager.evict_idle()
                    except Exception as ev_err:
                        logger.debug("[Worker] evict_idle 失败: %s", ev_err)
```

保留 `continue`（超时分支仍要继续循环）。修改后的超时分支应为：

```python
            except SharedMemoryProtocolError as e:
                if "超时" in str(e):
                    continue
                logger.error(f"通信错误: {e}")
                break
```

- [ ] **Step 3: Add shutdown to finally block**

在 `:715` 附近的 `finally:` 块开头加 cache_manager shutdown：

```python
    finally:
        # 停止 TTL 后台线程，避免线程泄漏
        try:
            ocr_service.cache_manager.shutdown()
        except Exception as shutdown_err:
            logger.debug("[Worker] cache_manager shutdown 失败: %s", shutdown_err)
        # 清理所有批量管理器
        for pipeline_name, mgr in batch_managers.items():
            ...
```

- [ ] **Step 4: Update MSG_SET_TTL handler**

修改 `:635-652` 的 MSG_SET_TTL 处理，从单值改为 dict：

```python
                elif msg_type == MSG_SET_TTL:
                    # 更新每管道 TTL
                    import json

                    try:
                        payload = json.loads(data.decode("utf-8")) if data else {}
                        ttls = payload.get("pipeline_ttls")
                        if not isinstance(ttls, dict):
                            raise ValueError("pipeline_ttls 缺失或非 dict")
                        ocr_service.cache_manager.ttls = ttls
                        logger.info("[Worker] 每管道 TTL 更新: %s", ttls)
                        protocol.write_message(MSG_ACK, b"ok", sender="worker")
                        # 等待主进程读取响应，避免读回自己刚写的消息
                        protocol.wait_for_read(timeout=5.0)
                    except Exception as e:
                        logger.error("[Worker] 设置 TTL 失败: %s", e)
                        protocol.write_message(
                            MSG_ERROR, str(e).encode("utf-8"), sender="worker"
                        )
                        protocol.wait_for_read(timeout=5.0)
```

- [ ] **Step 5: Run Phase 0 lint + types + relevant tests**

Run: `uv run ruff check packages/vibeocr-backend/src/vibeocr/workers/ocr_worker.py`
Run: `uv run pyright packages/vibeocr-backend/src/vibeocr/workers/ocr_worker.py`
Run: `uv run pytest tests/services/test_pipeline_cache_manager.py tests/integration/test_pipeline_cache_lifecycle.py -v`

Expected: lint/types 0 errors；测试可能 fail（因 RPC 客户端还没改），记录失败，Task 5-7 修。

- [ ] **Step 6: Commit**

```bash
git add packages/vibeocr-backend/src/vibeocr/workers/ocr_worker.py
git commit -m "refactor(worker): drop lazy evict_idle, add shutdown, accept dict TTL payload"
```

---

## Task 5: 协议契约三方原子升级（schema + golden + method_validation + C#）

**这是关键任务**——三方必须同一 commit，否则 Phase 1 门禁单跑会红。

**Files:**
- Modify: `packages/vibeocr-contracts-py/src/vibeocr/protocol/v1/methods.schema.json`
- Modify: `packages/vibeocr-contracts-py/src/vibeocr/protocol/v1/golden.json`
- Modify: `packages/vibeocr-backend/src/vibeocr/worker_host/method_validation.py`
- Modify: `packages/vibeocr-backend/src/vibeocr/worker_host/composition.py`（`SettingsSnapshot.ttl_seconds` → `pipeline_ttls`）
- Modify: `tests/dotnet/VibeOCR.Contracts.Tests/`（若 C# 强类型引用 TtlSeconds）
- Test: `tests/contracts/test_json_schema.py`（可能需更新）

**Interfaces:**
- Produces: payload schema 中 `ttl_seconds` (integer) → `pipeline_ttls` (object<str→int≥0>)，三处一致

- [ ] **Step 1: Read current schemas/golden/validators**

Read:
- `packages/vibeocr-contracts-py/src/vibeocr/protocol/v1/methods.schema.json:615-723`
- `packages/vibeocr-contracts-py/src/vibeocr/protocol/v1/golden.json:585-680`
- `packages/vibeocr-backend/src/vibeocr/worker_host/method_validation.py:576-675`
- `packages/vibeocr-backend/src/vibeocr/worker_host/composition.py:193-194,600-616`

- [ ] **Step 2: Update methods.schema.json**

在 `packages/vibeocr-contracts-py/src/vibeocr/protocol/v1/methods.schema.json`:

**A.** `pipeline_cache.status` response（`:621-631`）：

把：
```json
          "required": ["ready", "ttl_seconds", "max_heavy", "loaded_pipelines", "last_used_unix_ms"],
          "properties": {
            "ready": { "type": "boolean" },
            "ttl_seconds": { "type": "integer", "minimum": 0 },
```

改为：
```json
          "required": ["ready", "pipeline_ttls", "max_heavy", "loaded_pipelines", "last_used_unix_ms"],
          "properties": {
            "ready": { "type": "boolean" },
            "pipeline_ttls": {
              "type": "object",
              "additionalProperties": { "type": "integer", "minimum": 0 }
            },
```

**B.** `pipeline_cache.set_ttl` request/response（`:640-653`）：

```json
        "request": {
          "type": "object", "additionalProperties": false,
          "required": ["pipeline_ttls"],
          "properties": {
            "pipeline_ttls": {
              "type": "object",
              "additionalProperties": { "type": "integer", "minimum": 0 }
            }
          }
        },
        "response": {
          "type": "object", "additionalProperties": false,
          "required": ["updated", "pipeline_ttls"],
          "properties": {
            "updated": { "type": "boolean" },
            "pipeline_ttls": {
              "type": "object",
              "additionalProperties": { "type": "integer", "minimum": 0 }
            }
          }
        }
```

**C.** `settings.snapshot` response（`:715-719`）：

```json
          "required": ["backend", "preload_pipelines", "pipeline_ttls"],
          "properties": {
            "backend": { "$ref": "#/$defs/backend" },
            "preload_pipelines": { "type": "array", "items": { "type": "string" } },
            "pipeline_ttls": {
              "type": "object",
              "additionalProperties": { "type": "integer", "minimum": 0 }
            }
          }
```

- [ ] **Step 3: Update golden.json 样例**

在 `packages/vibeocr-contracts-py/src/vibeocr/protocol/v1/golden.json`:

准备一个完整的 `pipeline_ttls` 样例对象（所有 6 管道）：

```json
"_ttl_sample": {
  "OCR": 0,
  "TABLE_RECOGNITION": 0,
  "FORMULA_RECOGNITION": 0,
  "PP-StructureV3": 300,
  "MinerU": 0,
  "PaddleOCR-VL": 300
}
```

（实际写入时不要 `_ttl_sample` 这个 key，直接展开到各处。）

替换 `:588`（status response 的 `ttl_seconds`）为：
```json
          "pipeline_ttls": {"OCR": 0, "TABLE_RECOGNITION": 0, "FORMULA_RECOGNITION": 0, "PP-StructureV3": 300, "MinerU": 0, "PaddleOCR-VL": 300},
```

替换 `:601`（set_ttl request payload）为：
```json
        "payload": {"pipeline_ttls": {"OCR": 0, "TABLE_RECOGNITION": 0, "FORMULA_RECOGNITION": 0, "PP-StructureV3": 600, "MinerU": 0, "PaddleOCR-VL": 600}},
```

替换 `:608`（set_ttl response result）为：
```json
        "result": {"updated": true, "pipeline_ttls": {"OCR": 0, "TABLE_RECOGNITION": 0, "FORMULA_RECOGNITION": 0, "PP-StructureV3": 600, "MinerU": 0, "PaddleOCR-VL": 600}}
```

替换 `:677`（settings.snapshot response 的 `ttl_seconds`）为：
```json
          "pipeline_ttls": {"OCR": 0, "TABLE_RECOGNITION": 0, "FORMULA_RECOGNITION": 0, "PP-StructureV3": 300, "MinerU": 0, "PaddleOCR-VL": 300},
```

- [ ] **Step 4: Update method_validation.py**

在 `packages/vibeocr-backend/src/vibeocr/worker_host/method_validation.py`:

**A.** `_response_pipeline_cache_status`（`:580-603`）——把 `"ttl_seconds"` 改为 `"pipeline_ttls"`，校验改为 dict：

```python
def _response_pipeline_cache_status(p: dict[str, Any]) -> None:
    _closed(
        p,
        required={
            "ready",
            "pipeline_ttls",
            "max_heavy",
            "loaded_pipelines",
            "last_used_unix_ms",
        },
        label="pipeline_cache.status response",
    )
    _boolean(p["ready"], "ready")
    _pipeline_ttls_object(p["pipeline_ttls"], "pipeline_ttls")
    _integer(p["max_heavy"], "max_heavy")
    if not isinstance(p["loaded_pipelines"], list) or not all(
        isinstance(item, str) and item in _PIPELINES
        for item in p["loaded_pipelines"]
    ):
        raise MethodPayloadError("loaded_pipelines must be an array of strings")
    last_used = _object(p["last_used_unix_ms"], "last_used_unix_ms")
    for name, timestamp in last_used.items():
        _string(name, "last_used_unix_ms key")
        _integer(timestamp, f"last_used_unix_ms.{name}")
```

**B.** `_request_pipeline_cache_set_ttl` 和 `_response_pipeline_cache_set_ttl`（`:606-622`）：

```python
def _request_pipeline_cache_set_ttl(p: dict[str, Any]) -> None:
    _closed(
        p,
        required={"pipeline_ttls"},
        label="pipeline_cache.set_ttl request",
    )
    _pipeline_ttls_object(p["pipeline_ttls"], "pipeline_ttls")


def _response_pipeline_cache_set_ttl(p: dict[str, Any]) -> None:
    _closed(
        p,
        required={"updated", "pipeline_ttls"},
        label="pipeline_cache.set_ttl response",
    )
    _boolean(p["updated"], "updated")
    _pipeline_ttls_object(p["pipeline_ttls"], "pipeline_ttls")
```

**C.** `_response_settings`（`:663-675`）——`ttl_seconds` 改 `pipeline_ttls`：

```python
def _response_settings(p: dict[str, Any]) -> None:
    _closed(
        p,
        required={"backend", "preload_pipelines", "pipeline_ttls"},
        label="settings.snapshot response",
    )
    if p["backend"] not in ("cpu", "gpu"):
        raise MethodPayloadError("backend must be cpu or gpu")
    if not isinstance(p["preload_pipelines"], list) or not all(
        isinstance(item, str) for item in p["preload_pipelines"]
    ):
        raise MethodPayloadError("preload_pipelines must be an array of strings")
    _pipeline_ttls_object(p["pipeline_ttls"], "pipeline_ttls")
```

**D.** 新增 `_pipeline_ttls_object` 辅助函数（放在文件顶部辅助函数区，紧邻 `_object`、`_integer` 等）：

```python
def _pipeline_ttls_object(value: Any, label: str) -> None:
    """校验 pipeline_ttls：必须是 dict，key 是已知管道名，value 是 int>=0。"""
    obj = _object(value, label)
    for name, ttl in obj.items():
        if name not in _PIPELINES:
            raise MethodPayloadError(
                f"{label} 包含未知管道名: {name!r}"
            )
        if isinstance(ttl, bool) or not isinstance(ttl, int):
            raise MethodPayloadError(f"{label}.{name} 必须是整数")
        if ttl < 0:
            raise MethodPayloadError(f"{label}.{name} 必须 >= 0")
```

- [ ] **Step 5: Update composition.py SettingsSnapshot**

在 `packages/vibeocr-backend/src/vibeocr/worker_host/composition.py`:

**A.** 找到 `SettingsSnapshot` 的定义（grep `class SettingsSnapshot` 或 `ttl_seconds` 字段）。把字段 `ttl_seconds: int` 改为 `pipeline_ttls: dict[str, int]`（或 `Mapping[str, int]`，看现有风格）。

**B.** `:606-616` 的 `_load_settings`（或类似读取 app_settings.json 的函数）：

```python
        ttls_raw = data.get("pipeline_ttls", {})
        if not isinstance(ttls_raw, dict):
            ttls_raw = {}
        # 校验 + 补默认值（委托 ConfigManager 风格）
        from vibeocr.contracts.pipelines import get_all_pipelines

        valid_names = {p.value for p in get_all_pipelines()}
        pipeline_ttls: dict[str, int] = {}
        for name in valid_names:
            raw = ttls_raw.get(name)
            if isinstance(raw, bool) or not isinstance(raw, int):
                # 默认值：paddle 重管道=300，其他=0
                pipeline_ttls[name] = 300 if name in {"PP-StructureV3", "PaddleOCR-VL"} else 0
            else:
                pipeline_ttls[name] = max(0, raw)
        return SettingsSnapshot(
            backend=str(backend),
            preload_pipelines=normalized,
            pipeline_ttls=pipeline_ttls,
        )
```

**C.** `:193-194` 的 `set_pipeline_ttl` 改名为 `set_pipeline_ttls`，参数改 dict：

```python
    def set_pipeline_ttls(self, pipeline_ttls: dict[str, int]) -> bool:
        return bool(self._get_service().set_pipeline_ttls(pipeline_ttls))
```

- [ ] **Step 6: Update composition.py handler 和 worker_host/handlers/pipeline_cache.py**

在 `packages/vibeocr-backend/src/vibeocr/worker_host/handlers/pipeline_cache.py:18`：

```python
    def set_pipeline_ttls(self, pipeline_ttls: dict[str, int]) -> bool: ...
```

实现 `SetTtlHandler`（或现有等价 handler）的 payload 解析：

```python
        ttls = payload.get("pipeline_ttls")
        if (
            not isinstance(ttls, dict)
        ):
            raise WorkerError(
                ErrorCode.INVALID_REQUEST,
                "pipeline_ttls must be an object",
            )
        return {
            "updated": True,
            "pipeline_ttls": self._boundary.set_pipeline_ttls,
            ...
        }
```

注意：具体 handler 结构以现有代码为准，关键是把 `ttl_seconds` 解析替换为 `pipeline_ttls` dict 解析。

- [ ] **Step 7: Update OCRService + Subprocess + WorkerProcess（基础接口签名）**

在 `packages/vibeocr-backend/src/vibeocr/services/ocr_service.py:790-801`：

```python
    @classmethod
    def set_pipeline_ttls(cls, pipeline_ttls: dict[str, int]) -> bool:
        """设置每管道 TTL 闲置回收时间（直连模式）。

        Args:
            pipeline_ttls: 每管道 TTL 字典，0=持久。

        Returns:
            是否设置成功。
        """
        cls().cache_manager.ttls = pipeline_ttls
        return True
```

在 `packages/vibeocr-backend/src/vibeocr/services/ocr_service_base.py:169`：

```python
    def set_pipeline_ttls(self, pipeline_ttls: dict[str, int]) -> bool:
        raise NotImplementedError
```

在 `packages/vibeocr-backend/src/vibeocr/services/ocr_service_subprocess.py:383-402`：

```python
    def set_pipeline_ttls(self, pipeline_ttls: dict[str, int]) -> bool:
        """设置每管道 TTL（经 RPC 下发）。

        Args:
            pipeline_ttls: 每管道 TTL 字典，0=持久。

        Returns:
            是否成功。
        """
        if not self._initialized:
            return False
        try:
            return self._paddlex_manager.execute(
                lambda w: w.set_ttls(
                    pipeline_ttls, timeout=Constants.Timeout.SHM_WRITE
                )
            )
        except Exception as e:
            logger.error("set_pipeline_ttls 失败: %s", e)
            return False
```

在 `packages/vibeocr-backend/src/vibeocr/services/ocr_worker_process.py:1253-1285`：

```python
    def set_ttls(
        self, pipeline_ttls: dict[str, int], timeout: float = Constants.Timeout.SHM_WRITE
    ) -> bool:
        """向 worker 发送 SET_TTL 命令（每管道 dict 格式）。

        Args:
            pipeline_ttls: 每管道 TTL 字典。
            timeout: 超时时间（秒）。

        Returns:
            是否成功。
        """
        import json

        if not self.is_ready:
            return False

        protocol = self.protocol
        if protocol is None:
            raise OCRWorkerProcessError(
                f"Worker {self.worker_id} 通信协议未初始化"
            )
        try:
            payload = json.dumps({"pipeline_ttls": pipeline_ttls}).encode("utf-8")
            protocol.write_message(
                MSG_SET_TTL, payload, timeout=timeout, sender="main"
            )
            logger.debug(
                f"Worker {self.worker_id} SET_TTL 请求已发送 (pipeline_ttls)"
            )

            protocol.wait_for_read(timeout=timeout)

            msg_type, _data = protocol.read_message(
                timeout=timeout, expected_sender="worker"
            )
            return msg_type == MSG_ACK

        except SharedMemoryProtocolError as e:
            logger.warning(f"发送 SET_TTL 请求失败: {e}")
            return False
```

同时修改 `:1287-1320` 的 `cache_status` 默认返回值（`ttl_seconds: 0` → `pipeline_ttls: {}`）：

```python
    def cache_status(
        self, timeout: float = Constants.Timeout.WORKER_TIMEOUT
    ) -> dict[str, object]:
        """Query the cache snapshot from the inference worker process."""
        if not self.is_ready:
            return {
                "pipeline_ttls": {},
                "max_heavy": 0,
                "loaded_pipelines": [],
                "last_used_unix_ms": {},
            }
        ...
```

`get_pipeline_cache_status`（subprocess 端 `:404-432`）的默认返回值也同步把 `ttl_seconds: 0` 改为 `pipeline_ttls: {}`。

- [ ] **Step 8: Update contract tests if hardcoded**

Run: `uv run pytest tests/contracts -v`

若 `tests/contracts/test_json_schema.py` 硬编码了 `ttl_seconds`，更新为 `pipeline_ttls`。该测试主要用 golden.json 驱动，通常不需改；若 fail 则按错误信息定位修。

- [ ] **Step 9: Run Phase 1 gate**

```powershell
./scripts/run_phase1_gate.ps1
```

Expected: PASS（含 `dotnet test VibeOCR.Contracts.Tests`）

若 C# 端 fail（`GoldenContractTests.cs` 消费 golden.json），检查：
- golden.json JSON 合法性
- C# 是否有 `TtlSeconds` 强类型属性（grep `Ttl` in `src/dotnet/VibeOCR.Contracts/`）；若有则同步改名 `PipelineTtls`

- [ ] **Step 10: Run Phase 0 gate**

```powershell
./scripts/run_phase0_gate.ps1
```

Expected: PASS

- [ ] **Step 11: Run architecture consistency test**

```bash
uv run pytest tests/architecture/test_protocol_method_consistency.py -v
```

Expected: PASS（method 名集合不变，应自动通过）

- [ ] **Step 12: Commit (single atomic commit)**

```bash
git add packages/vibeocr-contracts-py/src/vibeocr/protocol/v1/methods.schema.json \
        packages/vibeocr-contracts-py/src/vibeocr/protocol/v1/golden.json \
        packages/vibeocr-backend/src/vibeocr/worker_host/method_validation.py \
        packages/vibeocr-backend/src/vibeocr/worker_host/composition.py \
        packages/vibeocr-backend/src/vibeocr/worker_host/handlers/pipeline_cache.py \
        packages/vibeocr-backend/src/vibeocr/services/ocr_service.py \
        packages/vibeocr-backend/src/vibeocr/services/ocr_service_base.py \
        packages/vibeocr-backend/src/vibeocr/services/ocr_service_subprocess.py \
        packages/vibeocr-backend/src/vibeocr/services/ocr_worker_process.py \
        src/dotnet/VibeOCR.Contracts/ \
        tests/dotnet/VibeOCR.Contracts.Tests/ \
        tests/contracts/
git commit -m "refactor(protocol): upgrade TTL payload to per-pipeline dict (ttl_seconds -> pipeline_ttls)

三方原子升级：
- methods.schema.json: status/set_ttl/settings.snapshot payload 改为 pipeline_ttls dict
- golden.json: 样例同步
- method_validation.py: _pipeline_ttls_object 校验函数
- composition.py: SettingsSnapshot.pipeline_ttls + set_pipeline_ttls
- handler / OCRService / Subprocess / WorkerProcess: 接口签名改 dict
- C# golden 同步

Phase 0 + Phase 1 gate 全绿。"
```

---

## Task 6: ConfigManager 新 API + 迁移逻辑

**Files:**
- Modify: `apps/vibeocr-pyside/src/vibeocr/managers/config_manager.py`
- Test: `tests/managers/test_config_manager.py`

**Interfaces:**
- Produces: `get_pipeline_ttls() -> dict[str, int]`、`set_pipeline_ttl(name, ttl) -> bool`、`set_pipeline_ttls(ttls) -> bool`；自动迁移 `pipeline_ttl_seconds` → `pipeline_ttls`

- [ ] **Step 1: Read current ConfigManager TTL methods**

Read `apps/vibeocr-pyside/src/vibeocr/managers/config_manager.py:140-200`（`get_pipeline_ttl_seconds` / `set_pipeline_ttl_seconds` / `get_max_heavy_pipelines`）。

- [ ] **Step 2: Write failing tests for migration + new API**

Append to `tests/managers/test_config_manager.py`:

```python
def test_migrate_legacy_single_ttl_value(tmp_path):
    """旧 pipeline_ttl_seconds=600 → 重管道 600，轻管道 0，MinerU 0。"""
    from vibeocr.managers.config_manager import ConfigManager

    config = tmp_path / "app_settings.json"
    config.write_text('{"pipeline_ttl_seconds": 600}')
    mgr = ConfigManager.instance()
    mgr._config_dir = tmp_path  # 测试注入；以实际 ConfigManager 测试模式为准
    ttls = mgr.get_pipeline_ttls()
    assert ttls["OCR"] == 0
    assert ttls["TABLE_RECOGNITION"] == 0
    assert ttls["FORMULA_RECOGNITION"] == 0
    assert ttls["PP-StructureV3"] == 600
    assert ttls["PaddleOCR-VL"] == 600
    assert ttls["MinerU"] == 0
    # 旧字段已删除
    import json
    data = json.loads(config.read_text(encoding="utf-8"))
    assert "pipeline_ttl_seconds" not in data
    assert "pipeline_ttls" in data


def test_default_ttls_for_fresh_user(tmp_path):
    """新用户：轻=0, MinerU=0, paddle 重=300。"""
    from vibeocr.managers.config_manager import ConfigManager

    mgr = ConfigManager.instance()
    mgr._config_dir = tmp_path
    ttls = mgr.get_pipeline_ttls()
    assert ttls == {
        "OCR": 0,
        "TABLE_RECOGNITION": 0,
        "FORMULA_RECOGNITION": 0,
        "PP-StructureV3": 300,
        "MinerU": 0,
        "PaddleOCR-VL": 300,
    }


def test_partial_dict_filled_with_defaults(tmp_path):
    """只配了部分管道，缺失的补默认。"""
    from vibeocr.managers.config_manager import ConfigManager

    config = tmp_path / "app_settings.json"
    config.write_text(
        '{"pipeline_ttls": {"OCR": 100, "PP-StructureV3": 600}}'
    )
    mgr = ConfigManager.instance()
    mgr._config_dir = tmp_path
    ttls = mgr.get_pipeline_ttls()
    assert len(ttls) == 6
    assert ttls["OCR"] == 100
    assert ttls["PP-StructureV3"] == 600
    assert ttls["TABLE_RECOGNITION"] == 0  # 补默认
    assert ttls["MinerU"] == 0
    assert ttls["PaddleOCR-VL"] == 300  # 补默认


def test_set_pipeline_ttl_single(tmp_path):
    """set_pipeline_ttl 改单个管道。"""
    from vibeocr.managers.config_manager import ConfigManager

    mgr = ConfigManager.instance()
    mgr._config_dir = tmp_path
    assert mgr.set_pipeline_ttl("OCR", 180) is True
    assert mgr.get_pipeline_ttls()["OCR"] == 180
```

注意：`ConfigManager.instance()` 是单例，测试间状态会污染。实施时参考 `tests/managers/test_config_manager.py` 现有测试的注入模式（可能是 fixture 重置 `_config_dir` 或用 `ConfigManager()` 直接构造）。

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/managers/test_config_manager.py -v -k "ttl or migrate"`
Expected: FAIL（`get_pipeline_ttls` 不存在）

- [ ] **Step 4: Implement new API + migration**

Edit `apps/vibeocr-pyside/src/vibeocr/managers/config_manager.py`. Replace `get_pipeline_ttl_seconds` / `set_pipeline_ttl_seconds`（约 `:152-160`）：

```python
    # 默认每管道 TTL。paddle 重管道 5 分钟，其他持久。
    _DEFAULT_PIPELINE_TTLS: dict[str, int] = {
        "OCR": 0,
        "TABLE_RECOGNITION": 0,
        "FORMULA_RECOGNITION": 0,
        "PP-StructureV3": 300,
        "MinerU": 0,
        "PaddleOCR-VL": 300,
    }

    def get_pipeline_ttls(self) -> dict[str, int]:
        """返回完整 6 管道 TTL dict；缺失补默认；自动迁移旧字段。"""
        data = self._load_json("app_settings.json", {})
        # 迁移旧字段（一次性）
        if "pipeline_ttl_seconds" in data and "pipeline_ttls" not in data:
            legacy = int(data.pop("pipeline_ttl_seconds"))
            data["pipeline_ttls"] = {
                "OCR": 0,
                "TABLE_RECOGNITION": 0,
                "FORMULA_RECOGNITION": 0,
                "PP-StructureV3": legacy,
                "MinerU": 0,
                "PaddleOCR-VL": legacy,
            }
            self._save_json("app_settings.json", data)
        # 读取 + 补默认
        raw = data.get("pipeline_ttls", {})
        if not isinstance(raw, dict):
            raw = {}
        result = dict(self._DEFAULT_PIPELINE_TTLS)
        for name, default in self._DEFAULT_PIPELINE_TTLS.items():
            val = raw.get(name, default)
            if isinstance(val, bool) or not isinstance(val, int):
                val = default
            result[name] = max(0, val)
        return result

    def set_pipeline_ttl(self, pipeline_name: str, ttl: int) -> bool:
        """设置单个管道 TTL（0=持久）。"""
        ttls = self.get_pipeline_ttls()
        ttls[pipeline_name] = max(0, int(ttl))
        return self.set_pipeline_ttls(ttls)

    def set_pipeline_ttls(self, ttls: dict[str, int]) -> bool:
        """批量设置每管道 TTL。"""
        data = self._load_json("app_settings.json", {})
        # 合法化
        result = dict(self._DEFAULT_PIPELINE_TTLS)
        for name, default in self._DEFAULT_PIPELINE_TTLS.items():
            val = ttls.get(name, default)
            if isinstance(val, bool) or not isinstance(val, int):
                val = default
            result[name] = max(0, val)
        data["pipeline_ttls"] = result
        return self._save_json("app_settings.json", data)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/managers/test_config_manager.py -v -k "ttl or migrate"`
Expected: PASS

- [ ] **Step 6: Update callers in main_window.py and subprocess_manager.py**

在 `apps/vibeocr-pyside/src/vibeocr/views/main_window.py:1373,1385,1411`：

把 `ttl = ConfigManager.instance().get_pipeline_ttl_seconds()` 改为 `ttls = ConfigManager.instance().get_pipeline_ttls()`；`preload_pipelines([], ttl_seconds=ttl)` 改为 `preload_pipelines([], pipeline_ttls=ttls)`。

在 `apps/vibeocr-pyside/src/vibeocr/managers/subprocess_manager.py:149-185, 384-410`：

把 `ttl_seconds: int | None = None` 改为 `pipeline_ttls: dict[str, int] | None = None`；`service.set_pipeline_ttl(self._ttl_seconds)` 改为 `service.set_pipeline_ttls(self._pipeline_ttls)`。

- [ ] **Step 7: Run Phase 0 gate**

```powershell
./scripts/run_phase0_gate.ps1
```

Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add apps/vibeocr-pyside/src/vibeocr/managers/config_manager.py \
        apps/vibeocr-pyside/src/vibeocr/managers/subprocess_manager.py \
        apps/vibeocr-pyside/src/vibeocr/views/main_window.py \
        tests/managers/test_config_manager.py
git commit -m "feat(config): per-pipeline TTL dict + legacy migration"
```

---

## Task 7: UI 6 ComboBox + label 拆分

**Files:**
- Modify: `apps/vibeocr-pyside/src/vibeocr/views/settings_page_controller.py`
- Test: `tests/views/test_settings_preload.py`（或类似，核查 ComboBox 创建）

**Interfaces:**
- Consumes: Task 6 的 `get_pipeline_ttls` / `set_pipeline_ttl`
- Produces: 6 个 `QComboBox`（objectName 如 `comboTtlOCR`、`comboTtlPPStructureV3` 等）

- [ ] **Step 1: Read current settings page TTL UI code**

Read `apps/vibeocr-pyside/src/vibeocr/views/settings_page_controller.py:1556-1675`（TTL restore/sync/release 相关方法）。

- [ ] **Step 2: Add 6 ComboBoxes to settings page**

在 `_init_settings_page`（`:693`）或类似初始化方法中，在原 `spinPipelineTtl` 位置（或新增区域）构建 6 个 ComboBox。

参考现有动态添加控件的风格（如 `_init_log_level_control` `:704-730`）：

```python
    def _init_pipeline_ttl_combos(self) -> None:
        """构建每管道 TTL ComboBox（替代旧 spinPipelineTtl + chkEnablePipelineTtl）。"""
        layout = self._ui.findChild(QVBoxLayout, "appSettingsLayout")
        if layout is None:
            return
        # 检查是否已构建（避免重复）
        if self._ui.findChild(QComboBox, "comboTtlOCR") is not None:
            return

        from vibeocr.contracts.pipelines import (
            OCRPipeline,
            get_pipeline_display_name,
        )

        # 预设档：显示文本 -> TTL 秒数
        PRESET_TTLS = [
            ("持久停留", 0),
            ("1 分钟", 60),
            ("3 分钟", 180),
            ("5 分钟", 300),
            ("10 分钟", 600),
            ("15 分钟", 900),
            ("30 分钟", 1800),
        ]

        ttls = ConfigManager.instance().get_pipeline_ttls()
        for pipeline in OCRPipeline:
            row = QWidget(self._ui)
            row.setObjectName(f"ttlRow_{pipeline.value}")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            label = QLabel(get_pipeline_display_name(pipeline), row)
            combo = QComboBox(row)
            combo.setObjectName(f"comboTtl_{pipeline.value}")
            for display_text, _secs in PRESET_TTLS:
                combo.addItem(display_text)
            # 从配置恢复选中项
            current_ttl = ttls.get(pipeline.value, 0)
            self._select_ttl_combo(combo, current_ttl, PRESET_TTLS)
            # MinerU 特殊 tooltip
            if pipeline == OCRPipeline.DOCUMENT_PARSING:
                label.setToolTip(
                    "MinerU 是 HTTP 服务客户端，回收代理对象不释放底层进程资源。"
                    "默认持久停留。改短 TTL 几乎无收益。"
                )
            combo.currentIndexChanged.connect(
                lambda _idx, name=pipeline.value, c=combo: self._on_pipeline_ttl_combo_changed(
                    name, c, PRESET_TTLS
                )
            )
            row_layout.addWidget(label)
            row_layout.addWidget(combo)
            row_layout.addStretch(1)
            layout.addWidget(row)

    @staticmethod
    def _select_ttl_combo(
        combo: QComboBox, ttl: int, presets: list[tuple[str, int]]
    ) -> None:
        """根据 TTL 秒数选中 ComboBox 项；无精确匹配时选最接近的。"""
        for idx, (_, secs) in enumerate(presets):
            if secs == ttl:
                combo.setCurrentIndex(idx)
                return
        # 回退到第一个（持久）
        combo.setCurrentIndex(0)

    def _on_pipeline_ttl_combo_changed(
        self,
        pipeline_name: str,
        combo: QComboBox,
        presets: list[tuple[str, int]],
    ) -> None:
        """单个管道 TTL 改变 → 写配置 + 下发 worker。"""
        idx = combo.currentIndex()
        if idx < 0 or idx >= len(presets):
            return
        _display, ttl = presets[idx]
        ConfigManager.instance().set_pipeline_ttl(pipeline_name, ttl)
        self._sync_configured_pipeline_ttls()
        self._show_settings_toast()
```

- [ ] **Step 3: Replace _restore_pipeline_ttl_state and _sync_configured_pipeline_ttl**

删除或重写 `:1556-1594` 的 `_restore_pipeline_ttl_state` / `_on_pipeline_ttl_changed` / 相关代码，替换为：

```python
    def _restore_pipeline_ttl_combos(self) -> None:
        """从配置恢复所有 TTL ComboBox 选中项。"""
        ttls = ConfigManager.instance().get_pipeline_ttls()
        from vibeocr.contracts.pipelines import OCRPipeline

        for pipeline in OCRPipeline:
            combo = self._ui.findChild(QComboBox, f"comboTtl_{pipeline.value}")
            if combo is None:
                continue
            current_ttl = ttls.get(pipeline.value, 0)
            # 反查 ComboBox 索引
            for idx in range(combo.count()):
                if combo.itemData(idx, Qt.UserRole) == current_ttl:
                    combo.blockSignals(True)
                    combo.setCurrentIndex(idx)
                    combo.blockSignals(False)
                    break

    def _sync_configured_pipeline_ttls(self) -> None:
        """把配置中的 pipeline_ttls 整批下发到 worker。"""
        if not self._subprocess_manager or not self._subprocess_manager.is_ready:
            self._update_release_status("运行时缓存状态：OCR 服务未连接")
            return
        ttls = ConfigManager.instance().get_pipeline_ttls()
        self._cache_generation += 1
        generation = self._cache_generation
        service = self._subprocess_manager.service

        def operation() -> bool:
            return service.set_pipeline_ttls(ttls)

        self._run_cache_operation(
            operation,
            lambda _result: self._update_release_status(
                f"运行时缓存状态：已下发每管道 TTL"
            ),
            lambda error: self._update_release_status(
                f"运行时缓存状态：TTL 下发失败 ({error})"
            ),
        )
```

注意：ComboBox 的 `addItem(display_text)` 默认不带 data。为让 `_restore_pipeline_ttl_combos` 反查，在 `_init_pipeline_ttl_combos` 里改为：

```python
            for display_text, secs in PRESET_TTLS:
                combo.addItem(display_text, secs)  # 第二参数设为 UserRole data
```

然后 `_restore_pipeline_ttl_combos` 用 `combo.findData(current_ttl)` 找索引。

- [ ] **Step 4: Update _update_release_status / status display to read pipeline_ttls**

修改 `:1660-1670` 附近读 status 的代码，从 `status["ttl_seconds"]` 改为 `status["pipeline_ttls"]`：

```python
            ttls = status.get("pipeline_ttls", {})
            loaded = status.get("loaded_pipelines", [])
            max_heavy = status.get("max_heavy", 0)
            loaded_text = ", ".join(loaded) if loaded else "(无)"
            ttl_summary = ", ".join(
                f"{name}={'持久' if v == 0 else f'{v // 60}分钟'}"
                for name, v in ttls.items()
            )
            status_text = (
                f"运行时状态: 已加载 {loaded_text} · 上限 {max_heavy}/显存 · "
                f"TTL: {ttl_summary}"
            )
            self._update_release_status(status_text)
```

- [ ] **Step 5: Call _init_pipeline_ttl_combos from _init_settings_page**

在 `_init_settings_page`（`:693-702`）末尾加：

```python
        self._init_pipeline_ttl_combos()
```

- [ ] **Step 6: Delete old spinPipelineTtl / chkEnablePipelineTtl references**

grep `spinPipelineTtl\|chkEnablePipelineTtl\|_on_pipeline_ttl_changed\|_restore_pipeline_ttl_state\|_ttl_sync_timer` 全部出现位置（除上述替换的外），删除或改用新 combo。`_ttl_sync_timer` 可保留（防抖用），但触发的 slot 改为 `_sync_configured_pipeline_ttls`。

- [ ] **Step 7: Run Phase 0 gate**

```powershell
./scripts/run_phase0_gate.ps1
```

Expected: PASS。若有 UI 测试 fail（test_settings_preload.py 等），适配新控件名。

- [ ] **Step 8: Run UI blocking boundary architecture test**

```bash
uv run pytest tests/architecture/test_ui_thread_blocking_boundaries.py -v
```

Expected: PASS。若 fail，核查 `_on_pipeline_ttl_combo_changed` 是否引入了禁用调用（应只调 ConfigManager + `_run_cache_operation`）。

- [ ] **Step 9: Manual UI verification**

启动应用，确认：
- [ ] 设置页显示 6 个 ComboBox，默认值正确
- [ ] 改 OCR 为"5 分钟" → toast 显示 → worker 日志（如有）显示 TTL 更新
- [ ] MinerU 行 tooltip 显示"HTTP 客户端，回收无益"

- [ ] **Step 10: Commit**

```bash
git add apps/vibeocr-pyside/src/vibeocr/views/settings_page_controller.py \
        tests/views/
git commit -m "feat(ui): per-pipeline TTL ComboBox + split cache/pipeline status labels"
```

---

## Task 8: Bug 1（文案）+ Bug 2（refresh_cache 重检测）+ Bug 3（machine_id warmup）

**Files:**
- Modify: `apps/vibeocr-pyside/src/vibeocr/views/settings_page_controller.py`
- Modify: `packages/vibeocr-client-py/src/vibeocr/machine_cache.py`
- Test: `tests/client/test_machine_cache.py`（追加）

- [ ] **Step 1: Bug 1 - Fix misleading copy**

Edit `apps/vibeocr-pyside/src/vibeocr/views/settings_page_controller.py:984-985`:

```python
                self._apply_cache_status(generation, True, info, "缓存已刷新")
                self._show_settings_toast("机器/依赖缓存已重置（下次启动时重新检测）")
                logger.debug("[缓存] 已刷新机器/依赖缓存")
```

- [ ] **Step 2: Bug 2 - Rewrite _refresh_machine_cache_operation**

Edit `apps/vibeocr-pyside/src/vibeocr/views/settings_page_controller.py:998-1003`:

```python
    def _refresh_machine_cache_operation(self) -> tuple[bool, str]:
        """真正重检测：清缓存 → 触发完整环境检测 → 读回 cache info。

        在 _run_cache_operation 后台线程执行。完整检测耗时数十秒
        （40+ subprocess + paddle import），UI 通过按钮 disable + 进度文案提示。
        """
        from vibeocr import env_manager
        from vibeocr.machine_cache import clear_cache, get_cache_info

        # 1. 先清旧缓存，强制下次检测为"全量"
        clear_cache(self._project_root)
        # 2. 跑完整检测（以 env_manager.check_embedded_environment_dependencies
        #    实际签名为准，关键是 use_cache=False）
        env_manager.check_embedded_environment_dependencies(
            self._project_root,
            use_cache=False,
        )
        return True, get_cache_info(self._project_root)
```

同时更新 `_on_refresh_cache_clicked`（`:963-996`）的进度文案：

```python
        self._update_cache_status("正在重新检测环境（可能需要数十秒）...")
```

- [ ] **Step 3: Bug 2 - Rename machine_cache.refresh_cache → reset_cache_to_empty**

Edit `packages/vibeocr-client-py/src/vibeocr/machine_cache.py:363-390`:

把函数 `refresh_cache` 改名为 `reset_cache_to_empty`，docstring 改为：

```python
def reset_cache_to_empty(project_root: Path) -> bool:
    """重置缓存为空壳（仅清 deps/hardware_info，不重新检测）。

    供测试和迁移使用。UI 的"刷新缓存"按钮应调用 env_manager 路径做真重检测。

    Args:
        project_root: 项目根目录

    Returns:
        是否重置成功
    """
    # ... 原实现保留
```

grep `refresh_cache` 全部引用（应只有 `_refresh_machine_cache_operation` 一处，已在 Step 2 改掉），若无其他引用则完成。

- [ ] **Step 4: Bug 3 - Add warmup_machine_id**

Edit `packages/vibeocr-client-py/src/vibeocr/machine_cache.py`. 在 `generate_machine_id`（`:170` 之后）新增：

```python
def warmup_machine_id(project_root: Path | None = None) -> None:
    """启动期后台预热机器码，避免后续 GUI 操作感知 wmic 延迟。

    安全在任何线程调用。若 _cached_machine_id 已设置则立即返回。
    project_root 参数仅为 API 一致性保留，实际不使用。
    """
    generate_machine_id()
```

- [ ] **Step 5: Write regression tests for Bug 2/3**

Append to `tests/client/test_machine_cache.py`（若不存在则新建）：

```python
def test_reset_cache_to_empty_clears_dependencies(tmp_path):
    """Bug 2: reset_cache_to_empty 清空 deps/hardware_info。"""
    from vibeocr.machine_cache import (
        create_cache_entry,
        load_cache,
        reset_cache_to_empty,
    )

    # 先建一个有内容的缓存
    create_cache_entry(
        tmp_path,
        dependencies={"paddle": True},
        hardware_info={"has_gpu": True},
    )
    cached = load_cache(tmp_path)
    assert cached is not None
    assert cached.get("dependencies") == {"paddle": True}

    # 重置
    assert reset_cache_to_empty(tmp_path) is True
    reset = load_cache(tmp_path)
    assert reset is not None
    assert reset.get("dependencies") == {}
    assert reset.get("hardware_info") == {}


def test_warmup_machine_id_caches_result(monkeypatch):
    """Bug 3: warmup_machine_id 调一次后 generate_machine_id 不再跑 wmic。"""
    import vibeocr.machine_cache as mc

    # 重置模块级缓存
    monkeypatch.setattr(mc, "_cached_machine_id", None)

    call_count = {"cpu": 0, "baseboard": 0}

    def fake_cpu() -> str:
        call_count["cpu"] += 1
        return "FAKE_CPU"

    def fake_baseboard() -> str:
        call_count["baseboard"] += 1
        return "FAKE_BB"

    monkeypatch.setattr(mc, "_get_cpu_id", fake_cpu)
    monkeypatch.setattr(mc, "_get_baseboard_serial", fake_baseboard)

    mc.warmup_machine_id()
    assert call_count == {"cpu": 1, "baseboard": 1}

    # 再次调用不应触发 wmic
    mc.generate_machine_id()
    mc.generate_machine_id()
    assert call_count == {"cpu": 1, "baseboard": 1}
```

- [ ] **Step 6: Run Phase 0 gate**

```powershell
./scripts/run_phase0_gate.ps1
```

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add apps/vibeocr-pyside/src/vibeocr/views/settings_page_controller.py \
        packages/vibeocr-client-py/src/vibeocr/machine_cache.py \
        tests/client/test_machine_cache.py
git commit -m "fix(cache): misleading copy + real refresh_cache detection + machine_id warmup

Bug 1: toast/log 文案去掉误导的\"模型缓存\"字样
Bug 2: refresh_cache 改为真重检测（env_manager.check_embedded_environment_dependencies use_cache=False），
       原函数改名 reset_cache_to_empty
Bug 3: 新增 warmup_machine_id，预热机器码避免 wmic 锁争用"
```

---

## Task 9: Bug 4（删死文件）+ 全量回归

**Files:**
- Delete: `.vibeocr/model_cache.json`

- [ ] **Step 1: Verify the file is truly orphaned**

```bash
cd C:/Users/felji/PycharmProjects/vibeocr
grep -rn "model_cache\.json" --include="*.py" --include="*.xaml" --include="*.md" . 2>/dev/null | grep -v ".git/" | grep -v "node_modules/"
```

Expected: 无输出（CHANGELOG 历史记录可忽略，不是代码引用）

- [ ] **Step 2: Delete the file**

```bash
rm .vibeocr/model_cache.json
```

- [ ] **Step 3: Update existing tests that reference old TTL API**

grep 全测试目录，更新所有 `pipeline_ttl_seconds` / `set_pipeline_ttl` / `ttl_seconds` 引用：

```bash
grep -rn "pipeline_ttl_seconds\|set_pipeline_ttl\b\|\"ttl_seconds\"" tests/ 2>/dev/null
```

逐个改为新 API：
- `pipeline_ttl_seconds` → `pipeline_ttls`（dict）
- `set_pipeline_ttl(x)` → `set_pipeline_ttls({...})`
- `"ttl_seconds"` 在测试 fixture JSON 里 → `"pipeline_ttls"`

参考 `tests/integration/test_pipeline_cache_lifecycle.py`、`tests/views/test_settings_preload.py`、`tests/views/test_settings_nav_merge.py`、`tests/services/test_ocr_service.py`、`tests/views/test_settings_install_succeeded.py`、`tests/client/test_batch_and_export.py`。

- [ ] **Step 4: Run full Phase 0 + Phase 1 gates**

```powershell
./scripts/run_phase0_gate.ps1
./scripts/run_phase1_gate.ps1
```

Expected: 全部 PASS

- [ ] **Step 5: Run architecture tests**

```bash
uv run pytest tests/architecture/ -v
```

Expected: 全 PASS

- [ ] **Step 6: Manual end-to-end UI verification**

启动应用，按 spec 7.7 节手动验证清单逐项确认：

- [ ] 启动后设置页显示 6 个 ComboBox，默认值正确
- [ ] 改 OCR 为"5 分钟" → 立即下发 → worker 日志显示 TTL 更新
- [ ] 改 PP-StructureV3 为"持久" → 用 PP-StructureV3 OCR 一次 → 等待 10 分钟 → 模型仍在
- [ ] 改 PP-StructureV3 为"1 分钟" → OCR 一次 → 等 70 秒 → 模型被回收（status label 更新）
- [ ] MinerU tooltip 显示"HTTP 客户端，回收无益"
- [ ] 8GB 卡用户：只能并存 1 个 paddle 重模型（手动切换测试）
- [ ] 老用户配置文件含 pipeline_ttl_seconds → 启动后迁移为每管道，行为不变
- [ ] 点"刷新缓存"按钮 → 真重检测（耗时数十秒）→ toast 显示新文案
- [ ] .vibeocr/model_cache.json 已删除

- [ ] **Step 7: Final commit**

```bash
git add -A
git commit -m "chore: remove orphan model_cache.json + adapt all tests to per-pipeline TTL"
```

---

## 完成标志

所有任务完成且：
- [ ] `./scripts/run_phase0_gate.ps1` PASS
- [ ] `./scripts/run_phase1_gate.ps1` PASS
- [ ] `uv run pytest tests/architecture/ -v` PASS
- [ ] spec 7.7 节手动 UI 验证清单全绿
- [ ] CHANGELOG.md 加一条本次变更记录（可选，按项目惯例）

---

## 实施提示

- **Task 5 是最高风险**——三方契约必须原子提交。建议在干净工作区开始 Task 5，失败时 `git reset` 重来。
- **Task 7 与 Task 8 修改同一个文件**（`settings_page_controller.py`），若并行实施注意 merge 顺序；建议串行。
- **测试中避免绝对路径**——用 `tmp_path` fixture 或 `Path.relative_to(project_root)`。
- **若 Phase 1 的 C# dotnet 工具链不可用**，至少保证 Python 端（schema + golden + method_validation）一致，C# 留到 CI 验证；但本地 commit 前必须 grep 确认 C# 端无 `TtlSeconds` 强类型属性。
