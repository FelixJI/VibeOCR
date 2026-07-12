"""批量队列管理器

在 Worker 子进程内管理批量请求队列，
支持显存自适应的 batch_size 调整，
调用 pipeline.predict(batch) 进行批量推理。
"""

import logging
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable

from vibeocr.core.constants import OCR_BATCH_GPU_SIZE_CAP
from vibeocr.models.batch_request import (
    BatchProgress,
    BatchRequest,
    BatchRequestStatus,
    PreprocessOptions,
)
from vibeocr.utils.gpu_memory_monitor import GPUMemoryMonitor

logger = logging.getLogger(__name__)


class BatchQueueManager:
    """批量请求队列管理器

    管理批量识别请求的队列，支持：
    - 添加请求到队列
    - 根据显存动态调整 batch_size
    - 执行批量推理
    - 结果分发

    使用示例:
        manager = BatchQueueManager(pipeline)  # max_batch_size 默认为显存动态估算上界

        # 添加请求
        request_id = manager.add_request(image_data, options)

        # 提交并执行
        results = manager.commit(preprocess_options)

        # 获取结果
        result = manager.get_result(request_id)
    """

    def __init__(
        self,
        pipeline,
        max_batch_size: int = OCR_BATCH_GPU_SIZE_CAP,
        progress_callback: Callable[[BatchProgress], None] | None = None,
        service=None,
    ):
        """初始化队列管理器

        Args:
            pipeline: PaddleX pipeline 对象（保留用于回退/兼容）
            max_batch_size: 显存动态估算的上界（非固定 batch_size）。实际值由
                _calculate_batch_size 按当前显存动态算出，低显存卡自动降级。
            progress_callback: 进度回调函数
            service: OCRService 实例。提供时，批量推理会委派给各管道
                注册的 recognize_batch / recognize 函数，复用单图路径的
                选项映射逻辑（如 VL 的 vl_use_layout_detection→
                use_layout_detection 重命名、公式管道强制
                use_formula_recognition=True、表格管道的特殊模型名参数等），
                并返回与单图一致的 OCRResult 对象。
        """
        self.pipeline = pipeline
        self.max_batch_size = max_batch_size
        self.progress_callback = progress_callback
        self.service = service

        # 请求队列 (OrderedDict 保持顺序)
        self._queue: OrderedDict[str, BatchRequest] = OrderedDict()
        self._lock = threading.Lock()

        # 显存监控器
        self._memory_monitor = GPUMemoryMonitor()

        # 取消标志
        self._cancelled = False

        # 统计信息
        self._stats = {
            "total_requests": 0,
            "total_batches": 0,
            "total_time": 0.0,
        }

    def add_request(
        self,
        image_data: bytes,
        options: dict,
        file_path: str = "",
        file_name: str = "",
        request_id: str = "",
    ) -> str:
        """添加请求到队列

        Args:
            image_data: 图像数据
            options: OCR 选项
            file_path: 文件路径
            file_name: 文件名
            request_id: 主进程生成的请求标识符。批量场景下主进程已为
                每个文件分配了 id 并建立 id -> file_path 映射，必须复用
                该 id，否则结果返回时无法匹配到文件，导致 UI 显示
                "0 成功, 0 失败"。为空时自动生成（兼容单进程用法）。

        Returns:
            request_id: 请求标识符（与传入一致；未传则返回自动生成的）
        """
        request = BatchRequest(
            request_id=request_id or uuid.uuid4().hex[:12],
            file_path=file_path,
            file_name=file_name,
            image_data=image_data,
            options=options,
        )

        with self._lock:
            self._queue[request.request_id] = request
            self._stats["total_requests"] += 1

        logger.debug(
            f"添加批量请求: {request.request_id}, 队列长度: {len(self._queue)}"
        )
        return request.request_id

    def clear_queue(self):
        """清空队列"""
        with self._lock:
            self._queue.clear()
        logger.debug("批量队列已清空")

    def get_queue_size(self) -> int:
        """获取队列大小"""
        with self._lock:
            return len(self._queue)

    def cancel(self):
        """取消当前处理"""
        self._cancelled = True
        logger.debug("批量处理已取消")

    def commit(
        self,
        preprocess_options: PreprocessOptions,
        file_completed_callback: Callable[[str, object], None] | None = None,
    ) -> dict[str, object]:
        """提交并执行批量处理

        Args:
            preprocess_options: 预处理选项
            file_completed_callback: 单文件完成回调 (request_id, result)

        Returns:
            {request_id: result} 结果字典
        """
        self._cancelled = False
        results: dict[str, object] = {}

        # 获取待处理请求
        with self._lock:
            pending_requests = [
                req
                for req in self._queue.values()
                if req.status == BatchRequestStatus.PENDING
            ]

        if not pending_requests:
            logger.debug("没有待处理的请求")
            return results

        total = len(pending_requests)
        completed = 0
        failed = 0

        logger.info(f"开始批量处理: {total} 个请求")

        # 初始化进度
        progress = BatchProgress(total=total)
        self._report_progress(progress)

        start_time = time.time()

        # 分批处理
        batch_start = 0
        while batch_start < total and not self._cancelled:
            # 计算当前批次的 batch_size
            batch_size = self._calculate_batch_size(pending_requests[batch_start:])
            batch_end = min(batch_start + batch_size, total)
            batch_requests = pending_requests[batch_start:batch_end]

            logger.debug(
                f"处理批次: {batch_start + 1}-{batch_end}/{total}, batch_size={len(batch_requests)}"
            )

            # 更新进度
            progress.current_batch_size = len(batch_requests)
            progress.current_file = batch_requests[0].file_name
            self._report_progress(progress)

            # 执行批量推理
            batch_results = self._process_batch(batch_requests, preprocess_options)

            # 收集结果
            for request_id, result in batch_results.items():
                results[request_id] = result

                request = self._queue.get(request_id)
                if request:
                    if request.status == BatchRequestStatus.COMPLETED:
                        completed += 1
                    elif request.status == BatchRequestStatus.FAILED:
                        failed += 1

                # 单文件完成回调（流式返回结果）
                if file_completed_callback:
                    try:
                        file_completed_callback(request_id, result)
                    except Exception as e:
                        logger.warning(f"单文件完成回调失败: {e}")

            # 更新进度
            progress.completed = completed
            progress.failed = failed
            self._report_progress(progress)

            # 移动到下一批
            batch_start = batch_end
            self._stats["total_batches"] += 1

        # 记录统计
        elapsed = time.time() - start_time
        self._stats["total_time"] += elapsed

        if self._cancelled:
            # 标记未处理的请求为取消
            with self._lock:
                for req in self._queue.values():
                    if req.status == BatchRequestStatus.PENDING:
                        req.mark_cancelled()
            logger.info(f"批量处理已取消: 完成 {completed}/{total}")

        logger.info(
            f"批量处理完成: {completed}/{total}, 失败: {failed}, 耗时: {elapsed:.2f}s"
        )

        return results

    @staticmethod
    def _to_ndarray(image_data):
        """将图像数据转换为 numpy.ndarray（PaddleX pipeline 只接受 ndarray 和 str）"""
        if not isinstance(image_data, bytes):
            return image_data

        import io

        import numpy as np
        from PIL import Image as PILImage

        pil_image = PILImage.open(io.BytesIO(image_data))
        if pil_image.mode != "RGB":
            pil_image = pil_image.convert("RGB")
        return np.array(pil_image)

    def _calculate_batch_size(self, requests: list[BatchRequest]) -> int:
        """计算合适的 batch_size

        Args:
            requests: 待处理请求列表

        Returns:
            推荐的 batch_size
        """
        if not requests:
            return 1

        # 估算平均图片尺寸
        total_size = 0
        for req in requests:
            # 使用图像数据大小作为近似
            total_size += len(req.image_data)

        avg_size = total_size / len(requests)

        # 使用显存监控器估算
        if self._memory_monitor.is_available():
            # 假设平均图像尺寸约为 1920x1080 (约 2M 像素)
            # 实际应该解码图像获取真实尺寸，这里用简化的估算
            estimated_pixels = int(avg_size / 3 * 2)  # 粗略估计
            batch_size = self._memory_monitor.estimate_batch_size(estimated_pixels)
        else:
            batch_size = self.max_batch_size

        # 限制在请求数量范围内
        return min(batch_size, len(requests), self.max_batch_size)

    def _process_batch(
        self, requests: list[BatchRequest], preprocess_options: PreprocessOptions
    ) -> dict[str, object]:
        """处理单个批次

        Args:
            requests: 批次请求列表
            preprocess_options: 预处理选项

        Returns:
            {request_id: result} 结果字典
        """
        results = {}

        # 标记所有请求为处理中
        for req in requests:
            req.mark_processing()

        try:
            # 准备批量输入（bytes 需转换为 numpy.ndarray）
            images = [self._to_ndarray(req.image_data) for req in requests]

            # 解析管道名称
            pipeline_name = getattr(preprocess_options, "pipeline", "OCR")
            from enum import Enum

            if isinstance(pipeline_name, Enum):
                pipeline_name = pipeline_name.value

            # 优先走注册表分发：复用各管道单图路径的选项映射逻辑，
            # 保证选项名称转换、强制标志（如公式 use_formula_recognition=True）、
            # 结果格式（OCRResult）与单图完全一致。这是参数正确性的
            # 单一来源，避免批量路径与单图路径出现两套映射逻辑。
            batch_results = self._run_via_registry(
                pipeline_name, images, preprocess_options
            )

            # 分发结果
            for i, req in enumerate(requests):
                if i < len(batch_results):
                    result = batch_results[i]
                    req.mark_completed(result)
                    results[req.request_id] = result
                else:
                    error = "结果数量不匹配"
                    req.mark_failed(error)
                    results[req.request_id] = {"error": error}

        except Exception as e:
            # 整个批次失败，单独处理每个请求
            error_msg = str(e)
            logger.error(f"批次处理失败: {error_msg}")

            for req in requests:
                req.mark_failed(error_msg)
                results[req.request_id] = {"error": error_msg}

        return results

    def _run_via_registry(
        self,
        pipeline_name: str,
        images: list,
        options: PreprocessOptions,
    ) -> list:
        """通过管道注册表执行批量推理。

        优先使用 spec.recognize_batch（真批量，单次 predict(list)）；
        若该管道未注册批量接口，则回退逐张 spec.recognize。
        两者都复用单图路径的选项映射逻辑，保证参数正确性。

        无 service 时回退到原始 pipeline.predict()（仅用于不依赖
        服务的测试/兼容场景），此时按管道 supported_options 过滤选项。
        """
        # 有 service：走注册表分发（生产路径）
        if self.service is not None:
            from vibeocr.core.pipelines import get_registry

            registry = get_registry()
            if registry.has(pipeline_name):
                spec = registry.get(pipeline_name)
                if spec.recognize_batch is not None:
                    logger.debug(f"[批量] 管道 {pipeline_name} 走 recognize_batch")
                    return list(spec.recognize_batch(self.service, images, options))
                logger.debug(
                    f"[批量] 管道 {pipeline_name} 无批量接口，回退逐张 recognize"
                )
                return [spec.recognize(self.service, img, options) for img in images]

        # 无 service：原始 predict() 回退路径，按 supported_options 过滤
        from vibeocr.core.pipelines import (
            OCRPipeline,
            get_pipeline_supported_options,
        )

        try:
            pipeline_enum = OCRPipeline(pipeline_name)
        except ValueError:
            pipeline_enum = OCRPipeline.OCR

        supported_options = set(get_pipeline_supported_options(pipeline_enum))
        pipeline_options = options.to_dict()
        predict_options = {
            k: v for k, v in pipeline_options.items() if k in supported_options
        }
        return list(self.pipeline.predict(images, **predict_options))

    def _report_progress(self, progress: BatchProgress):
        """报告进度"""
        if self.progress_callback:
            try:
                self.progress_callback(progress)
            except Exception as e:
                logger.warning(f"进度回调失败: {e}")

    def get_result(self, request_id: str) -> object | None:
        """获取指定请求的结果"""
        with self._lock:
            request = self._queue.get(request_id)
            if request and request.is_finished:
                return request.result
        return None

    def get_stats(self) -> dict:
        """获取统计信息"""
        return self._stats.copy()

    def close(self):
        """清理资源"""
        self._memory_monitor.close()
        self.clear_queue()
