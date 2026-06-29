"""PDF 异步 OCR Worker — 在后台线程执行 OCR 识别。

接收预渲染的 numpy 数组列表，不直接访问 fitz.Document。
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from PySide6.QtCore import QThread, Signal

from vibeocr.utils.gpu_memory_monitor import estimate_gpu_batch_size
from vibeocr.utils.system_memory import estimate_cpu_batch_size, get_available_ram_mb

if TYPE_CHECKING:
    import numpy as np

    from vibeocr.models.ocr_options import OCROptions
    from vibeocr.services.ocr_service_base import OCRServiceBase

logger = logging.getLogger(__name__)


class PdfOcrWorker(QThread):
    """异步 OCR Worker。

    Signals:
        page_done(page_index: int, result: OCRResult | None)
        progress(current: int, total: int)
        all_done(session_id: str, success_count: int, fail_count: int)
    """

    page_done = Signal(int, object)
    progress = Signal(int, int)
    all_done = Signal(str, int, int)

    def __init__(
        self,
        session_id: str,
        pages: list[tuple[int, np.ndarray]],
        ocr_service: OCRServiceBase,
        ocr_options: OCROptions | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._session_id = session_id
        self._pages = pages
        self._ocr_service = ocr_service
        self._ocr_options = ocr_options
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def session_id(self) -> str:
        return self._session_id

    # 每批识别的页数上限。实际批量由 _compute_batch_size 按可用资源动态计算
    # （GPU 按显存、CPU 按 RAM），避免小内存设备 OOM。拆批的另一个目的是避免
    # 单次 predict(list) 运行过久被健康检查误判为卡死（见 worker_manager.STALE_THRESHOLD）。
    # 批间检查 _cancelled 实现可中断的取消。
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
            arrays = [p[1] if isinstance(p, tuple) else p for p in pages]
            avg_pixels = sum(int(a.shape[0]) * int(a.shape[1]) for a in arrays) // len(
                arrays
            )
        except (AttributeError, IndexError, TypeError):
            return self.DEFAULT_BATCH_SIZE
        if use_gpu:
            free_mb = _read_free_vram_mb()
            return estimate_gpu_batch_size(free_mb, avg_pixels)
        free_mb = get_available_ram_mb()
        return estimate_cpu_batch_size(free_mb, avg_pixels)

    def run(self) -> None:
        from vibeocr.models.ocr_options import OCROptions

        total = len(self._pages)
        options = self._ocr_options if self._ocr_options is not None else OCROptions()

        if total == 0:
            self.all_done.emit(self._session_id, 0, 0)
            return

        use_gpu = os.environ.get("VIBEOCR_USE_GPU", "").lower() == "true"
        batch_size = self._compute_batch_size(self._pages, use_gpu=use_gpu)
        logger.info(
            "[PdfOcrWorker] 批量大小=%d (模式=%s, 页数=%d)",
            batch_size,
            "GPU" if use_gpu else "CPU",
            total,
        )

        success = 0
        fail = 0
        processed = 0

        # 按动态 batch_size 拆批识别，每批处理完立即 emit page_done，
        # 并在下一批开始前检查 _cancelled。
        for batch_start in range(0, total, batch_size):
            if self._cancelled:
                break

            batch_end = min(batch_start + batch_size, total)
            batch_pages = self._pages[batch_start:batch_end]
            batch_indices = [idx for idx, _ in batch_pages]
            batch_images = [img for _, img in batch_pages]

            # 识别当前批（内部已容错：批量失败回退逐张）
            results = self._recognize_batch(batch_images, options)

            # emit 该批结果
            for _i, (page_index, result) in enumerate(
                zip(batch_indices, results, strict=False)
            ):
                if self._cancelled:
                    break
                processed += 1
                self.progress.emit(processed, total)
                if result is not None:
                    self.page_done.emit(page_index, result)
                    success += 1
                else:
                    self.page_done.emit(page_index, None)
                    fail += 1

        self.all_done.emit(self._session_id, success, fail)

    def _recognize_batch(self, images, options):
        """批量识别一批图像，逐张容错。

        优先调用服务的 recognize_batch（单次 predict(list)）；失败时回退逐张，
        确保单张图错误不会拖垮整批。返回结果列表，与 images 顺序一致，失败项为 None。
        """
        try:
            return list(self._ocr_service.recognize_batch(images, options))
        except Exception as e:
            logger.warning(
                "PdfOcrWorker 批量识别失败，回退逐张识别: %s", e, exc_info=True
            )
            results: list = []
            for img in images:
                if self._cancelled:
                    results.append(None)
                    continue
                try:
                    results.append(self._ocr_service.recognize(img, options))
                except Exception as e2:
                    logger.error("PdfOcrWorker 单页 OCR 失败: %s", e2)
                    results.append(None)
            return results


def _read_free_vram_mb() -> int:
    """读取 GPU 可用显存（MB）。

    优先用 NVML（pynvml）；失败时用 ``paddle.device.cuda`` 兜底（这条路径
    更可靠，已被 ``OCRService._log_gpu_summary`` 验证可用）。两条都失败
    才返回 0（此时 :func:`estimate_gpu_batch_size` 会用
    :data:`GPU_FALLBACK_BATCH_SIZE` 兜底，不再钉死成 batch=1）。

    作为模块级函数以便测试 mock。
    """
    # 1. 优先 NVML
    try:
        from vibeocr.utils.gpu_memory_monitor import GPUMemoryMonitor

        info = GPUMemoryMonitor().get_status()
        if info.available:
            return info.free
    except Exception:
        pass
    # 2. NVML 失败 → paddle.device.cuda 兜底
    return _read_free_vram_mb_via_paddle()


def _read_free_vram_mb_via_paddle() -> int:
    """用 paddle.device.cuda 读取可用显存（MB）。

    NVML 不可用时的二级兜底。返回 total - reserved - allocated 的估算值，
    失败返回 0。
    """
    try:
        import paddle.device as paddle_device  # type: ignore[import-untyped]

        if paddle_device.cuda.device_count() <= 0:
            return 0
        total_b = paddle_device.cuda.get_device_properties(0).total_memory
        # Paddle 的显存统计接口（与 PyTorch 对齐）；任一缺失则只减去 allocated
        allocated_b = 0
        reserved_b = 0
        for getter in (
            paddle_device.cuda.memory_allocated,
            getattr(paddle_device.cuda, "memory_reserved", None),
        ):
            if getter is None:
                continue
            try:
                val = getter(0)
                if "reserved" in getattr(getter, "__name__", ""):
                    reserved_b = val
                else:
                    allocated_b = val
            except Exception:
                pass
        free_b = max(0, total_b - reserved_b - allocated_b)
        return free_b // (1024 * 1024)
    except Exception:
        return 0
