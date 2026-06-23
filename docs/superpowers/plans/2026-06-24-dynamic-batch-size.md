# 动态 BATCH_SIZE（PDF 批量按资源缩放）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `PdfOcrWorker.BATCH_SIZE` 从固定常量 10 改为根据推理模式（GPU/CPU）和可用资源（显存/RAM）动态计算，让小内存设备安全、大内存设备不浪费。

**Architecture:** 新增 `system_memory.py` 工具（标准库 ctypes 读可用 RAM，无新依赖）。在 `PdfOcrWorker.run` 分批前，按已渲染页的像素均值 + 可用资源计算 batch_size。GPU 模式复用现有 `GPUMemoryMonitor`（pynvml）按显存算，CPU 模式按 RAM 算，各自独立的放大系数和安全系数。

**Tech Stack:** Python 3.12+, PySide6, PaddleOCR, pynvml（已有依赖）, ctypes（标准库）

**Spec:** `docs/superpowers/specs/2026-06-23-pipeline-cache-lifecycle-and-dynamic-batch-design.md` §5.5

---

## File Structure

| 文件 | 操作 | 职责 |
|---|---|---|
| `src/vibeocr/utils/system_memory.py` | 新建 | 跨平台读可用物理内存（MB），仅标准库 |
| `src/vibeocr/utils/gpu_memory_monitor.py` | 修改 | 新增 `estimate_batch_size_for_pixels()` 工具函数（显存分批） |
| `src/vibeocr/workers/pdf_ocr_worker.py` | 修改 | `BATCH_SIZE` 常量改为 `_compute_batch_size()` 动态计算 |
| `tests/utils/test_system_memory.py` | 新建 | RAM 读取测试 |
| `tests/utils/test_gpu_memory_monitor.py` | 修改 | 新增显存分批函数测试 |
| `tests/workers/test_pdf_ocr_worker.py` | 修改 | 新增动态 batch 计算测试 |

---

## Task 1: 可用 RAM 读取工具

**Files:**
- Create: `src/vibeocr/utils/system_memory.py`
- Test: `tests/utils/test_system_memory.py`

- [ ] **Step 1: Write the failing test**

Create `tests/utils/test_system_memory.py`:

```python
"""system_memory 工具单元测试。"""

from __future__ import annotations

from vibeocr.utils.system_memory import get_available_ram_mb, FALLBACK_RAM_MB


def test_get_available_ram_mb_returns_positive_int():
    """在真实环境上应返回正值（单位 MB）。"""
    result = get_available_ram_mb()
    assert isinstance(result, int)
    assert result > 0


def test_get_available_ram_mb_at_least_some_memory():
    """任何能跑测试的机器可用内存至少应有 64MB。"""
    assert get_available_ram_mb() >= 64


def test_fallback_constant_is_conservative():
    """回退值应为 2048（2GB），保证 batch 至少为 1-2。"""
    assert FALLBACK_RAM_MB == 2048
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/utils/test_system_memory.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vibeocr.utils.system_memory'`

- [ ] **Step 3: Write minimal implementation**

Create `src/vibeocr/utils/system_memory.py`:

```python
"""跨平台读取可用物理内存，仅使用标准库（无 psutil 依赖）。

用于 CPU 模式下动态计算 PDF 批量大小，避免在小内存设备上 OOM。
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

#: RAM 读取失败时的保守回退值（MB）。
#: 2GB 可用，按 CPU 规则（8× 放大、0.3 安全系数）→ batch ≈ 1-2。
FALLBACK_RAM_MB = 2048


def get_available_ram_mb() -> int:
    """获取当前可用物理内存（MB）。

    Windows 使用 ctypes 调用 GlobalMemoryStatusEx；
    Linux 读取 /proc/meminfo 的 MemAvailable；
    其他平台或读取失败时回退到 FALLBACK_RAM_MB。

    Returns:
        可用内存（MB），至少为正数。
    """
    try:
        mb = _read_available_ram()
        if mb and mb > 0:
            return int(mb)
    except Exception as e:  # noqa: BLE001 - 读取系统信息，任何失败都回退
        logger.warning("[system_memory] 读取可用内存失败，回退到 %dMB: %s", FALLBACK_RAM_MB, e)
    return FALLBACK_RAM_MB


def _read_available_ram() -> int | None:
    """平台分发：返回可用内存（MB）或 None。"""
    if sys.platform == "win32":
        return _read_windows()
    if sys.platform.startswith("linux"):
        return _read_linux()
    return None


def _read_windows() -> int | None:
    """Windows: ctypes + GlobalMemoryStatusEx。"""
    import ctypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
        return None
    return stat.ullAvailPhys // (1024 * 1024)


def _read_linux() -> int | None:
    """Linux: 读取 /proc/meminfo 的 MemAvailable（kB）。"""
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    # 行格式: "MemAvailable:  12345678 kB"
                    parts = line.split()
                    return int(parts[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/utils/test_system_memory.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/vibeocr/utils/system_memory.py tests/utils/test_system_memory.py
git commit -m "feat(utils): add system_memory.get_available_ram_mb (cross-platform, stdlib-only)"
```

---

## Task 2: 显存分批计算工具函数

**Files:**
- Modify: `src/vibeocr/utils/gpu_memory_monitor.py`（在现有类之后追加模块级函数）
- Test: `tests/utils/test_gpu_memory_monitor.py`

`GPUMemoryMonitor.estimate_batch_size()`（已有，`:77-100`）是为批量识别 tab 设计的，封装在类内且耦合 `get_status()`。这里新增一个**纯函数**版本，接受显存参数，便于测试和复用。

- [ ] **Step 1: Read the existing estimate_batch_size to understand current logic**

Run: Read `src/vibeocr/utils/gpu_memory_monitor.py` lines 77-100 to confirm the existing `safety_factor=0.7` and pixel-based formula. The new function uses the same pixel-per-batch idea but with the spec's 5× amplification factor.

- [ ] **Step 2: Write the failing test**

Append to `tests/utils/test_gpu_memory_monitor.py`:

```python
from vibeocr.utils.gpu_memory_monitor import estimate_gpu_batch_size


def test_estimate_gpu_batch_size_large_vram_caps_at_10():
    """8G 显存（free 6G）、A4@300（8.7M 像素）→ 5× 放大、0.5 安全 → 夹到 10。"""
    batch = estimate_gpu_batch_size(free_mb=6144, avg_pixels=8_700_000)
    assert batch == 10


def test_estimate_gpu_batch_size_small_vram_scales_down():
    """2G 显存（free 1.5G）、A4@300 → 1536*0.5/124.45=6.17 → 6。"""
    batch = estimate_gpu_batch_size(free_mb=1536, avg_pixels=8_700_000)
    assert batch == 6


def test_estimate_gpu_batch_size_minimum_is_1():
    """极小显存也要至少 1。"""
    batch = estimate_gpu_batch_size(free_mb=100, avg_pixels=8_700_000)
    assert batch == 1


def test_estimate_gpu_batch_size_tiny_image():
    """小图（100K 像素）即便显存小也返回较大值，夹到 10。"""
    batch = estimate_gpu_batch_size(free_mb=2000, avg_pixels=100_000)
    assert batch == 10
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/utils/test_gpu_memory_monitor.py::test_estimate_gpu_batch_size_large_vram_caps_at_10 -v`
Expected: FAIL with `ImportError: cannot import name 'estimate_gpu_batch_size'`

- [ ] **Step 4: Add the pure function to gpu_memory_monitor.py**

Append at the end of `src/vibeocr/utils/gpu_memory_monitor.py` (after the `GPUMemoryMonitor` class):

```python
# --- 模块级分批计算工具（供 PDF 动态 BATCH_SIZE 复用）---

#: GPU 模式每页峰值放大系数（含 PaddleOCR 内部多份副本）。
GPU_AMP_FACTOR = 5
#: GPU 模式安全系数（只用一半 free 显存）。
GPU_SAFETY_FACTOR = 0.5
#: GPU 模式 batch 上限（避免超时风险）。
GPU_BATCH_CAP = 10
#: 每像素字节数（RGB）。
BYTES_PER_PIXEL = 3


def estimate_gpu_batch_size(free_mb: int, avg_pixels: int) -> int:
    """按可用显存和平均像素数估算 GPU 批量大小。

    单页峰值（MB）= avg_pixels * 3 字节 * 5× 放大 / 1MB。
    batch = free * 0.5 / 单页峰值，夹到 [1, 10]。

    Args:
        free_mb: 可用显存（MB）。
        avg_pixels: 单页平均像素数（width * height）。

    Returns:
        批量大小，范围 [1, 10]。
    """
    if free_mb <= 0 or avg_pixels <= 0:
        return 1
    per_page_peak_mb = (avg_pixels * BYTES_PER_PIXEL * GPU_AMP_FACTOR) / (1024 * 1024)
    if per_page_peak_mb <= 0:
        return 1
    usable_mb = free_mb * GPU_SAFETY_FACTOR
    batch = int(usable_mb / per_page_peak_mb)
    return max(1, min(batch, GPU_BATCH_CAP))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/utils/test_gpu_memory_monitor.py -v -k estimate_gpu_batch_size`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add src/vibeocr/utils/gpu_memory_monitor.py tests/utils/test_gpu_memory_monitor.py
git commit -m "feat(gpu): add estimate_gpu_batch_size pure function for PDF dynamic batching"
```

---

## Task 3: CPU 分批计算工具函数

**Files:**
- Modify: `src/vibeocr/utils/system_memory.py`
- Test: `tests/utils/test_system_memory.py`

CPU 模式用更大的放大系数（8×，oneDNN 工作区更大）和更严格的安全系数（0.3，RAM 与系统共享），上限更低（6）。

- [ ] **Step 1: Write the failing test**

Append to `tests/utils/test_system_memory.py`:

```python
from vibeocr.utils.system_memory import estimate_cpu_batch_size


def test_estimate_cpu_batch_size_8g_ram():
    """8G RAM（free 4G）、A4@300 → 4096*0.3/199.13=6.17 → 6。"""
    assert estimate_cpu_batch_size(free_mb=4096, avg_pixels=8_700_000) == 6


def test_estimate_cpu_batch_size_4g_ram():
    """4G RAM（free 2G）→ 2048*0.3/199.13=3.08 → 3。"""
    assert estimate_cpu_batch_size(free_mb=2048, avg_pixels=8_700_000) == 3


def test_estimate_cpu_batch_size_16g_ram_caps_at_6():
    """16G RAM（free 8G）→ 8192*0.3/210≈11 → 夹到 6。"""
    assert estimate_cpu_batch_size(free_mb=8192, avg_pixels=8_700_000) == 6


def test_estimate_cpu_batch_size_minimum_is_1():
    """2G RAM（free 1G）→ 1024*0.3/210≈1。"""
    assert estimate_cpu_batch_size(free_mb=1024, avg_pixels=8_700_000) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/utils/test_system_memory.py -v -k estimate_cpu_batch_size`
Expected: FAIL with `ImportError: cannot import name 'estimate_cpu_batch_size'`

- [ ] **Step 3: Add the function to system_memory.py**

Append to `src/vibeocr/utils/system_memory.py`:

```python
#: CPU 模式每页峰值放大系数（oneDNN 工作区 + 多线程缓冲，比 GPU 大）。
CPU_AMP_FACTOR = 8
#: CPU 模式安全系数（RAM 与系统/UI 共享，留更多余量）。
CPU_SAFETY_FACTOR = 0.3
#: CPU 模式 batch 上限（低于 GPU，RAM 更紧张）。
CPU_BATCH_CAP = 6


def estimate_cpu_batch_size(free_mb: int, avg_pixels: int) -> int:
    """按可用 RAM 和平均像素数估算 CPU 批量大小。

    单页峰值（MB）= avg_pixels * 3 字节 * 8× 放大 / 1MB。
    batch = free * 0.3 / 单页峰值，夹到 [1, 6]。

    Args:
        free_mb: 可用 RAM（MB）。
        avg_pixels: 单页平均像素数（width * height）。

    Returns:
        批量大小，范围 [1, 6]。
    """
    if free_mb <= 0 or avg_pixels <= 0:
        return 1
    per_page_peak_mb = (avg_pixels * 3 * CPU_AMP_FACTOR) / (1024 * 1024)
    if per_page_peak_mb <= 0:
        return 1
    usable_mb = free_mb * CPU_SAFETY_FACTOR
    batch = int(usable_mb / per_page_peak_mb)
    return max(1, min(batch, CPU_BATCH_CAP))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/utils/test_system_memory.py -v -k estimate_cpu_batch_size`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/vibeocr/utils/system_memory.py tests/utils/test_system_memory.py
git commit -m "feat(utils): add estimate_cpu_batch_size for RAM-based dynamic batching"
```

---

## Task 4: PdfOcrWorker 动态 batch 计算

**Files:**
- Modify: `src/vibeocr/workers/pdf_ocr_worker.py:57-88`（`BATCH_SIZE` 常量 + `run` 分批逻辑）
- Test: `tests/workers/test_pdf_ocr_worker.py`

把固定 `BATCH_SIZE = 10` 改为 `_compute_batch_size(pages, use_gpu)`。`run()` 分批时调用它。

- [ ] **Step 1: Read the current run() batch loop**

Read `src/vibeocr/workers/pdf_ocr_worker.py` lines 1-120 to understand the current `BATCH_SIZE = 10` usage (line 60), the `run()` method, and how `pages` list is structured (each is a numpy array with `.shape`). Confirm the exact loop that slices `pages[i:i+BATCH_SIZE]`.

- [ ] **Step 2: Write the failing test**

Append to `tests/workers/test_pdf_ocr_worker.py`:

```python
import numpy as np

from vibeocr.workers.pdf_ocr_worker import PdfOcrWorker


def test_compute_batch_size_gpu_uses_vram(monkeypatch):
    """GPU 模式走 estimate_gpu_batch_size。"""
    monkeypatch.setattr(
        "vibeocr.workers.pdf_ocr_worker.estimate_gpu_batch_size",
        lambda free_mb, avg_pixels: 7,
    )
    worker = PdfOcrWorker.__new__(PdfOcrWorker)  # 跳过 __init__
    # pages 是 list[tuple[int, np.ndarray]]（页索引 + 数组）
    pages = [(0, np.zeros((1000, 800, 3), dtype=np.uint8))]
    assert worker._compute_batch_size(pages, use_gpu=True) == 7


def test_compute_batch_size_cpu_uses_ram(monkeypatch):
    """CPU 模式走 estimate_cpu_batch_size。"""
    monkeypatch.setattr(
        "vibeocr.workers.pdf_ocr_worker.estimate_cpu_batch_size",
        lambda free_mb, avg_pixels: 3,
    )
    worker = PdfOcrWorker.__new__(PdfOcrWorker)
    pages = [(0, np.zeros((1000, 800, 3), dtype=np.uint8))]
    assert worker._compute_batch_size(pages, use_gpu=False) == 3


def test_compute_batch_size_empty_pages_returns_minimum():
    """空页列表返回 1（兜底）。"""
    worker = PdfOcrWorker.__new__(PdfOcrWorker)
    assert worker._compute_batch_size([], use_gpu=True) == 1


def test_compute_batch_size_avg_pixels_from_pages():
    """_compute_batch_size 应从 pages 的 shape 算 avg_pixels 并传给 estimator。"""
    captured = {}

    def fake_estimator(free_mb, avg_pixels):
        captured["avg_pixels"] = avg_pixels
        captured["free_mb"] = free_mb
        return 5

    monkeypatch_target = "vibeocr.workers.pdf_ocr_worker"
    # 同时 mock 两个，确保哪个被调用都能捕获
    import vibeocr.workers.pdf_ocr_worker as mod

    orig_gpu = getattr(mod, "estimate_gpu_batch_size", None)
    orig_cpu = getattr(mod, "estimate_cpu_batch_size", None)
    mod.estimate_gpu_batch_size = fake_estimator
    mod.estimate_cpu_batch_size = fake_estimator
    try:
        worker = PdfOcrWorker.__new__(PdfOcrWorker)
        # 两张图：1000x800=800K, 2000x1600=3.2M → avg=2M
        pages = [
            (0, np.zeros((1000, 800, 3), dtype=np.uint8)),
            (1, np.zeros((2000, 1600, 3), dtype=np.uint8)),
        ]
        worker._compute_batch_size(pages, use_gpu=True)
        assert captured["avg_pixels"] == 2_000_000
    finally:
        if orig_gpu:
            mod.estimate_gpu_batch_size = orig_gpu
        if orig_cpu:
            mod.estimate_cpu_batch_size = orig_cpu
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/workers/test_pdf_ocr_worker.py -v -k compute_batch_size`
Expected: FAIL with `AttributeError: 'PdfOcrWorker' object has no attribute '_compute_batch_size'`

- [ ] **Step 4: Implement _compute_batch_size and wire into run()**

In `src/vibeocr/workers/pdf_ocr_worker.py`:

(a) Add imports near the top (after existing imports):

```python
from vibeocr.utils.gpu_memory_monitor import estimate_gpu_batch_size
from vibeocr.utils.system_memory import estimate_cpu_batch_size
```

(b) Replace the `BATCH_SIZE = 10` constant (line ~60) with a module-level fallback constant and an instance method. Keep a `DEFAULT_BATCH_SIZE = 10` for backward-compat references in tests, but the real logic moves to the method:

```python
    #: 固定回退值（仅在无法计算时使用，向后兼容）。
    DEFAULT_BATCH_SIZE = 10

    def _compute_batch_size(self, pages: list, use_gpu: bool) -> int:
        """根据可用资源和页像素均值动态计算批量大小。

        GPU 模式按可用显存，CPU 模式按可用 RAM。

        Args:
            pages: 已渲染页列表，每个为 tuple(int, np.ndarray)（页索引 + 数组）。
            use_gpu: 是否 GPU 模式。

        Returns:
            批量大小，至少为 1。
        """
        if not pages:
            return 1
        try:
            # pages 是 list[tuple[int, np.ndarray]]，取 [1] 得数组
            arrays = [p[1] if isinstance(p, tuple) else p for p in pages]
            avg_pixels = sum(int(a.shape[0]) * int(a.shape[1]) for a in arrays) // len(arrays)
        except (AttributeError, IndexError, TypeError):
            return self.DEFAULT_BATCH_SIZE
        if use_gpu:
            free_mb = self._get_free_vram_mb()
            return estimate_gpu_batch_size(free_mb, avg_pixels)
        free_mb = get_available_ram_mb()
        return estimate_cpu_batch_size(free_mb, avg_pixels)

    @staticmethod
    def _get_free_vram_mb() -> int:
        """读取 GPU 可用显存（MB），失败返回 0（estimate_gpu_batch_size 会回退到 1）。"""
        try:
            from vibeocr.utils.gpu_memory_monitor import GPUMemoryMonitor

            info = GPUMemoryMonitor().get_status()
            return info.free if info.available else 0
        except Exception:
            return 0
```

Add the `get_available_ram_mb` import alongside the others:

```python
from vibeocr.utils.system_memory import estimate_cpu_batch_size, get_available_ram_mb
```

(c) In the `run()` method, find where it slices `pages[i:i + BATCH_SIZE]` and replace the constant with a call to `_compute_batch_size` computed once before the loop. The exact lines depend on current code; locate the batch loop and change:

```python
# Before the loop (after pages are rendered/available):
batch_size = self._compute_batch_size(pages, use_gpu=self._use_gpu)
logger.info("[PdfOcrWorker] 动态 batch_size=%d (use_gpu=%s)", batch_size, self._use_gpu)
# Then use batch_size in the slicing instead of BATCH_SIZE.
```

Note: `self._use_gpu` — check the `__init__` for how GPU mode is passed. If it's stored differently (e.g. via options), read `self._options` or the OCR service device. If the worker doesn't currently know GPU mode, derive it from `os.environ.get("VIBEOCR_USE_GPU") == "true"` at the top of `run()`.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/workers/test_pdf_ocr_worker.py -v -k compute_batch_size`
Expected: PASS (4 tests)

- [ ] **Step 6: Run full worker test suite to check no regressions**

Run: `python -m pytest tests/workers/test_pdf_ocr_worker.py -v`
Expected: PASS (all existing tests still pass — they may reference `BATCH_SIZE`; if any test references the old constant name, update it to `DEFAULT_BATCH_SIZE`)

- [ ] **Step 7: Commit**

```bash
git add src/vibeocr/workers/pdf_ocr_worker.py tests/workers/test_pdf_ocr_worker.py
git commit -m "feat(pdf): dynamic BATCH_SIZE based on GPU VRAM / CPU RAM"
```

---

## Task 5: 集成验证与日志

**Files:**
- Verify: `src/vibeocr/workers/pdf_ocr_worker.py`

- [ ] **Step 1: Verify the GPU/CPU mode detection in run()**

Confirm how `run()` determines GPU mode. Read the `__init__` and `run()` methods. The correct signal is `os.environ.get("VIBEOCR_USE_GPU", "").lower() == "true"` (set by `ocr_worker.run_worker` at `ocr_worker.py:66-78`). If `PdfOcrWorker` runs in the main process (it does — it calls `ocr_service.recognize_batch` via subprocess), it should read the same env var that the subprocess was started with.

Add at the top of `run()` (if not already present):

```python
use_gpu = os.environ.get("VIBEOCR_USE_GPU", "").lower() == "true"
```

Ensure `import os` is present.

- [ ] **Step 2: Add a sanity log line**

In `run()`, after computing `batch_size`, ensure the log line exists:

```python
logger.info(
    "[PdfOcrWorker] 批量大小=%d (模式=%s, 页数=%d)",
    batch_size, "GPU" if use_gpu else "CPU", len(pages),
)
```

- [ ] **Step 3: Run the full test suite for affected modules**

Run: `python -m pytest tests/workers/test_pdf_ocr_worker.py tests/utils/test_system_memory.py tests/utils/test_gpu_memory_monitor.py -v`
Expected: PASS (all)

- [ ] **Step 4: Commit (if any changes from steps 1-2)**

```bash
git add src/vibeocr/workers/pdf_ocr_worker.py
git commit -m "feat(pdf): log dynamic batch size with mode info"
```

If no changes were needed (Task 4 already covered it), skip this commit.

---

## 完成标准

- [ ] `system_memory.py` 提供 `get_available_ram_mb()` + `estimate_cpu_batch_size()`，跨平台，无新依赖
- [ ] `gpu_memory_monitor.py` 新增 `estimate_gpu_batch_size()` 纯函数
- [ ] `PdfOcrWorker` 用动态 batch 替代固定 10，GPU 按显存、CPU 按 RAM
- [ ] 所有新测试通过，现有 worker 测试无回归
- [ ] 小内存设备（2G/4G RAM）安全（batch 1-2），大内存设备不浪费（batch 6-10）
