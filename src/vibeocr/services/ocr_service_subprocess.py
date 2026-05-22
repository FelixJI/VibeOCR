"""OCR 子进程服务

通过子进程隔离执行 OCR 识别，解决 PaddlePaddle GPU 与 QThread 的兼容性问题。
提供与 OCRService 兼容的接口。

此版本使用 PaddleX WorkerManager + MinerUService 直调架构：
- PaddleX Worker: 处理 OCR / TABLE / FORMULA 管道（共享内存 IPC）
- MinerUService: 处理 DOCUMENT_PARSING 管道（直调单例，httpx 请求）
"""

import asyncio
import contextlib
import logging
import threading
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Optional, Union

from pathlib import Path

from vibeocr.pipeline_status import is_pipeline_ever_succeeded, mark_pipeline_success
from vibeocr.services.worker_manager import WorkerManager

if TYPE_CHECKING:
    import numpy
    from PIL import Image

    from vibeocr.core.pipelines import OCRPipeline  # 从统一位置导入
    from vibeocr.models.batch_request import PreprocessOptions  # 向后兼容

logger = logging.getLogger(__name__)


class OCRServiceSubprocess:
    """子进程 OCR 服务（单例模式）

    管理一个或多个 OCR Worker 进程，提供与 OCRService 兼容的接口。

    使用示例:
        # 获取单例实例
        service = OCRServiceSubprocess()

        # 执行识别
        result = service.recognize(image, options)

        # 关闭服务
        service.shutdown()
    """

    _instance: Optional["OCRServiceSubprocess"] = None
    _lock = threading.Lock()
    _initialized: bool

    def __new__(
        cls,
        max_workers: int = 1,
        use_gpu: bool = False,
        shm_size: int = 10 * 1024 * 1024,
        auto_start: bool = True,
        start_timeout: float = 120.0,
        start_progress_callback: Callable[[str], None] | None = None,
    ) -> "OCRServiceSubprocess":
        """线程安全的单例创建

        注意：单例创建后，后续调用会忽略 max_workers 等参数。
        如需重新配置，请先调用 shutdown() 并设置 _instance = None。
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._initialized = False  # type: ignore[assignment]
                    cls._instance = instance
        return cls._instance

    def __init__(
        self,
        max_workers: int = 1,
        use_gpu: bool = False,
        shm_size: int = 10 * 1024 * 1024,
        auto_start: bool = True,
        start_timeout: float = 120.0,
        start_progress_callback: Callable[[str], None] | None = None,
    ):
        """初始化子进程 OCR 服务

        Args:
            max_workers: Worker 进程数量（默认 1）
            use_gpu: 是否使用 GPU（默认 False，CPU 模式）
            shm_size: 每个共享内存大小（字节）
            auto_start: 是否自动启动 Worker
            start_timeout: 启动超时时间（秒）
            start_progress_callback: 启动进度回调函数 (stage, percent)
        """
        if self._initialized:
            return

        self.max_workers = max_workers
        self.use_gpu = use_gpu
        self.shm_size = shm_size
        self.start_timeout = start_timeout
        self._start_progress_callback = start_progress_callback

        # 双 WorkerManager: PaddleX 和 MinerU 各自独立
        self._paddlex_manager = WorkerManager(
            max_workers=1,
            use_gpu=use_gpu,
            shm_size=shm_size,
            start_timeout=start_timeout,
            auto_restart=True,
            max_retries=2,
            worker_module="vibeocr.workers.ocr_worker",
        )
        from vibeocr.services.mineru_batch_service import MinerUBatchService

        self._mineru_batch = MinerUBatchService()

        self._initialized = True
        logger.debug(
            f"OCRServiceSubprocess 初始化: max_workers={max_workers}, use_gpu={use_gpu}"
        )

        # 自动启动
        if auto_start:
            self.start(
                timeout=self.start_timeout, progress_callback=start_progress_callback
            )

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例实例

        警告：这会关闭所有现有 Worker，仅用于测试或特殊场景。
        """
        with cls._lock:
            if cls._instance is not None:
                cls._instance.shutdown()
                cls._instance = None

    def start(
        self,
        timeout: float = 120.0,
        progress_callback: Callable[[str], None] | None = None,
    ) -> None:
        """启动所有 Worker

        Args:
            timeout: 每个 Worker 的启动超时时间
            progress_callback: 启动进度回调函数 (stage)
        """
        # 检查是否已初始化（防止 shutdown 后重复启动）
        if not self._initialized:
            logger.warning("OCRServiceSubprocess 已关闭或未初始化，跳过启动")
            return

        try:
            logger.info("开始启动 Worker 进程...")
            if progress_callback:
                progress_callback("初始化 PaddleX Worker")

            self._paddlex_manager.start_all(progress_callback=progress_callback)

            logger.info("所有 Worker 已启动")
            if progress_callback:
                progress_callback("Worker 启动完成")
        except Exception as e:
            logger.error(f"启动 Worker 失败: {e}")
            if progress_callback:
                progress_callback(f"启动失败: {str(e)[:50]}")
            raise

    def _is_mineru_pipeline(self, options) -> bool:
        """判断请求是否为文档解析（MinerU）管道"""
        from vibeocr.core.pipelines import OCRPipeline
        from enum import Enum

        if options is None:
            return False

        if isinstance(options, dict):
            pipeline_val = options.get("pipeline", "OCR")
        elif hasattr(options, "pipeline"):
            pipeline_val = options.pipeline
        else:
            return False

        if isinstance(pipeline_val, Enum):
            pipeline_val = pipeline_val.value

        return pipeline_val == OCRPipeline.DOCUMENT_PARSING.value

    def recognize(
        self, image: Union[bytes, "Image.Image", "numpy.ndarray", str], options=None
    ):
        """执行 OCR 识别（与 OCRService 接口兼容）

        Args:
            image: 输入图像
                - bytes: 图像数据
                - Image.Image: PIL 图像
                - np.ndarray: NumPy 数组
                - str: 图像文件路径
            options: OCR 选项（OCROptions 对象）

        Returns:
            OCRResult 对象

        Raises:
            OCRWorkerProcessError: 识别失败
            RuntimeError: 服务未就绪
        """
        # 检查服务是否就绪
        if not self._initialized:
            raise RuntimeError("OCR 服务未初始化")

        # 准备图像数据
        image_data = self._prepare_image_data(image)

        # 准备选项字典
        options_dict = self._prepare_options_dict(options)

        # 根据模型缓存状态计算超时时间
        pipeline_name = options_dict.get("pipeline", "OCR")
        timeout = self._calculate_recognize_timeout(pipeline_name)

        # 根据管道类型路由到对应处理
        if self._is_mineru_pipeline(options):
            from vibeocr.services.mineru_service import MinerUService

            mime_type = options_dict.get("mime_type", "application/pdf")
            if not mime_type or mime_type == "image/png":
                file_path = options_dict.get("file_path", "")
                if file_path:
                    from vibeocr.utils.mime_types import guess_mime_from_filename

                    mime_type = guess_mime_from_filename(file_path)
                else:
                    mime_type = "application/pdf"
            from vibeocr.models.ocr_options import OCROptions

            ocr_opts = OCROptions.from_dict(options_dict) if options_dict else None
            return MinerUService().parse(image_data, mime_type, ocr_opts)

        result = self._paddlex_manager.execute(
            lambda w: w.recognize(image_data, options_dict, timeout=timeout),
            timeout=timeout,
        )
        # 标记管道识别成功
        pipeline_name_for_mark = options_dict.get("pipeline", "OCR")
        if pipeline_name_for_mark in ("OCR", "PP-StructureV3"):
            try:
                mark_pipeline_success(pipeline_name_for_mark, self._get_project_root())
            except Exception:
                pass
        return result

    def _prepare_image_data(self, image) -> bytes:
        """准备图像数据

        Args:
            image: 输入图像（多种格式）

        Returns:
            图像数据（bytes）
        """
        if isinstance(image, bytes):
            return image

        if isinstance(image, str):
            # 文件路径
            with open(image, "rb") as f:
                return f.read()

        # 检查 PIL Image
        try:
            from PIL import Image

            if isinstance(image, Image.Image):
                import io

                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                return buffer.getvalue()
        except ImportError:
            pass

        # 检查 NumPy 数组
        try:
            import numpy as np

            if isinstance(image, np.ndarray):
                import io

                from PIL import Image

                pil_image = Image.fromarray(image)
                buffer = io.BytesIO()
                pil_image.save(buffer, format="PNG")
                return buffer.getvalue()
        except ImportError:
            pass

        raise ValueError(f"不支持的图像类型: {type(image)}")

    def _prepare_options_dict(self, options) -> dict:
        """准备选项字典

        Args:
            options: OCROptions 对象或 None

        Returns:
            选项字典
        """
        if options is None:
            return {}

        # 优先使用 to_dict() 方法（会正确处理枚举类型）
        if hasattr(options, "to_dict") and callable(options.to_dict):
            result = options.to_dict()
            if isinstance(result, dict):
                return result
            return {}

        # 如果已经是字典
        if isinstance(options, dict):
            return options

        # 兜底：直接使用 __dict__
        if hasattr(options, "__dict__"):
            return {k: v for k, v in options.__dict__.items() if not k.startswith("_")}

        return {}

    @staticmethod
    def _get_project_root() -> Path:
        from vibeocr.env_manager import get_project_root

        return get_project_root()

    def _calculate_recognize_timeout(self, pipeline_name: str | object) -> float:
        """根据管道类型和模型缓存状态计算识别超时时间

        首次使用管道时可能需要下载模型，使用更长的超时时间。
        MinerU 文档解析通过外部 API 调用，处理时间远超本地推理。

        Args:
            pipeline_name: 管道名称（字符串或 OCRPipeline 枚举）

        Returns:
            超时时间（秒）
        """
        from vibeocr.core.pipelines import OCRPipeline
        from enum import Enum

        # 超时配置常量
        TIMEOUT_CACHED = 60.0  # 模型已缓存时的超时（秒）
        TIMEOUT_UNCACHED = 600.0  # 模型未缓存时的超时（秒）- 10分钟，给模型下载留足时间
        TIMEOUT_DOCUMENT_PARSING = (
            600.0  # MinerU 文档解析超时（秒）- 10分钟，匹配 httpx 远程调用的耗时
        )

        if isinstance(pipeline_name, Enum):
            pipeline_name = pipeline_name.value
        pipeline_name_str = str(pipeline_name)

        # MinerU 文档解析、PaddleOCR-VL 和 PP-StructureV3 使用延长超时
        if pipeline_name_str in (
            OCRPipeline.DOCUMENT_PARSING.value,
            OCRPipeline.PADDLEOCR_VL.value,
            OCRPipeline.PP_STRUCTURE_V3.value,
        ):
            logger.debug(
                f"[识别] 管道 {pipeline_name} 为文档解析类，使用延长超时 ({TIMEOUT_DOCUMENT_PARSING}s)"
            )
            return TIMEOUT_DOCUMENT_PARSING

        if is_pipeline_ever_succeeded(pipeline_name_str, self._get_project_root()):
            logger.debug(
                f"[识别] 管道 {pipeline_name} 模型已缓存，使用标准超时 ({TIMEOUT_CACHED}s)"
            )
            return TIMEOUT_CACHED
        logger.warning(
            f"[识别] 管道 {pipeline_name} 模型未缓存，使用延长超时 ({TIMEOUT_UNCACHED}s)"
        )
        return TIMEOUT_UNCACHED

    def preload_pipelines(
        self, pipelines: list[str], timeout: float = 180.0
    ) -> dict[str, bool]:
        """预加载指定管道

        Args:
            pipelines: 管道名称列表 ["ocr", "PP-StructureV3", ...]
            timeout: 超时时间（秒）

        Returns:
            {pipeline_name: success} 结果字典
        """
        # 仅处理 PaddleX 管道
        results = {}
        if pipelines:
            results.update(
                self._paddlex_manager.execute(
                    lambda w: w.preload_pipelines(pipelines, timeout)
                )
            )
        return results

    def preload_pipeline(self, pipeline: "OCRPipeline", timeout: float = 180.0) -> bool:
        """预加载单个管道

        Args:
            pipeline: 要预加载的管道（OCRPipeline 枚举）
            timeout: 超时时间（秒）

        Returns:
            是否成功
        """
        pipeline_name = pipeline.value if hasattr(pipeline, "value") else str(pipeline)
        results = self.preload_pipelines([pipeline_name], timeout)
        return results.get(pipeline_name, False)

    def warmup_pipelines(
        self, pipelines: list[str], timeout: float = 180.0
    ) -> dict[str, bool]:
        """使用测试图片预热指定管道

        预热是真正的模型初始化，通过执行一次虚拟识别
        来触发模型加载到 GPU 内存和 CUDA 上下文创建。

        Args:
            pipelines: 管道名称列表
            timeout: 超时时间（秒）

        Returns:
            {pipeline_name: success} 结果字典
        """
        # 仅处理 PaddleX 管道
        results = {}
        if pipelines:
            results.update(
                self._paddlex_manager.execute(
                    lambda w: w.warmup_pipelines(pipelines, timeout)
                )
            )
        return results

    async def recognize_async(
        self, image: Union[bytes, "Image.Image", "numpy.ndarray", str], options=None
    ):
        """异步执行 OCR 识别（asyncio 协程）

        使用 run_in_executor 将同步调用包装为异步，避免阻塞事件循环。

        Args:
            image: 输入图像（bytes/PIL.Image/ndarray/str路径）
            options: OCR 选项（OCROptions 对象）

        Returns:
            OCRResult 对象

        Raises:
            OCRWorkerProcessError: 识别失败
            RuntimeError: 服务未就绪
        """
        logger.debug("[recognize_async] 开始异步识别...")

        # 检查服务是否就绪
        if not self._initialized:
            logger.error("[recognize_async] 服务未初始化")
            raise RuntimeError("OCR 服务未初始化")

        ready = self.is_ready()
        logger.debug(f"[recognize_async] 服务就绪状态: {ready}")
        if not ready:
            raise RuntimeError("OCR 服务未就绪，Worker 可能未启动")

        logger.debug("[recognize_async] 调用 run_in_executor...")
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self.recognize, image, options)
        logger.debug("[recognize_async] run_in_executor 完成")
        return result

    async def preload_pipelines_async(
        self, pipelines: list[str], timeout: float = 180.0
    ) -> dict[str, bool]:
        """异步预加载指定管道（asyncio 协程）

        Args:
            pipelines: 管道名称列表
            timeout: 超时时间（秒）

        Returns:
            {pipeline_name: success} 结果字典
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self.preload_pipelines, pipelines, timeout
        )

    def shutdown(self) -> None:
        """关闭所有 Worker"""
        logger.debug("关闭 OCRServiceSubprocess...")

        # 标记为已关闭，防止自动重启
        self._initialized = False

        # 停止 PaddleX WorkerManager
        if hasattr(self, "_paddlex_manager"):
            self._paddlex_manager.stop_all()

        logger.info("OCRServiceSubprocess 已关闭")

    def is_ready(self) -> bool:
        """检查服务是否就绪"""
        if not self._initialized:
            return False
        if hasattr(self, "_paddlex_manager"):
            return self._paddlex_manager.get_status().get("healthy", False)
        return False

    def get_status(self) -> dict:
        """获取服务状态"""
        paddlex_stats = (
            self._paddlex_manager.get_stats()
            if hasattr(self, "_paddlex_manager")
            else {"workers": [], "max_workers": 0, "ready_workers": 0}
        )
        return {
            "max_workers": paddlex_stats["max_workers"],
            "use_gpu": self.use_gpu,
            "ready": paddlex_stats["ready_workers"] > 0,
            "workers": paddlex_stats["workers"],
        }

    def get_stats(self) -> dict:
        """获取详细统计信息"""
        return {
            "paddlex": self._paddlex_manager.get_stats()
            if hasattr(self, "_paddlex_manager")
            else {}
        }

    # =========================================================================
    # 批量处理接口
    # =========================================================================

    def batch_add(
        self,
        image: Union[bytes, "Image.Image", "numpy.ndarray", str],
        options=None,
        file_name: str = "",
    ) -> str:
        """添加图片到批量队列

        Args:
            image: 输入图像
            options: OCR 选项
            file_name: 文件名

        Returns:
            request_id: 请求标识符
        """
        if not self._initialized:
            raise RuntimeError("OCR 服务未初始化")

        # 准备图像数据
        image_data = self._prepare_image_data(image)

        # 准备选项字典
        options_dict = self._prepare_options_dict(options)
        options_dict["file_name"] = file_name

        # 生成 request_id
        request_id = uuid.uuid4().hex[:12]

        # 序列化并发送
        from vibeocr.utils.shared_memory_v2 import (
            serialize_batch_request,
        )

        request_data = serialize_batch_request(request_id, image_data, options_dict)

        # 根据管道类型路由到对应处理
        if self._is_mineru_pipeline(options):
            # MinerU 文档解析不走 WorkerManager，后续由 batch_commit 处理
            pass
        else:
            self._paddlex_manager.execute(lambda w: w._send_batch_add(request_data))

        return request_id

    def batch_commit(
        self,
        preprocess_options: "PreprocessOptions",
        timeout: float = 300.0,
        progress_callback=None,
        file_completed_callback=None,
    ) -> dict:
        """提交批量处理

        Args:
            preprocess_options: 预处理选项
            timeout: 超时时间（秒）
            progress_callback: 进度回调（子进程模式下未使用，保留接口兼容）
            file_completed_callback: 单文件完成回调 (request_id, result)

        Returns:
            {request_id: result} 结果字典
        """
        if not self._initialized:
            raise RuntimeError("OCR 服务未初始化")

        from vibeocr.utils.shared_memory_v2 import (
            serialize_batch_commit,
        )

        # 序列化并发送
        commit_data = serialize_batch_commit(preprocess_options.to_dict())

        # 根据管道类型路由到对应处理
        if self._is_mineru_pipeline(preprocess_options):
            # MinerU 文档解析通过 MinerUBatchService 处理
            return self._mineru_batch.commit(
                preprocess_options,
                timeout=timeout,
                progress_callback=progress_callback,
                file_completed_callback=file_completed_callback,
            )
        return self._paddlex_manager.execute(
            lambda w: w._send_batch_commit(
                commit_data, timeout, file_completed_callback
            )
        )

    def batch_cancel(self):
        """取消批量处理"""
        if not self._initialized:
            return

        # 仅取消 PaddleX Worker（MinerU 不走 WorkerManager）
        with contextlib.suppress(Exception):
            self._paddlex_manager.execute(lambda w: w._send_batch_cancel())

    def set_task_queued_callback(self, callback: Callable) -> None:
        """设置任务排队通知回调（透传到 PaddleX WorkerManager）"""
        self._paddlex_manager.set_task_queued_callback(callback)

    def __enter__(self) -> "OCRServiceSubprocess":
        """上下文管理器入口"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """上下文管理器退出"""
        self.shutdown()
