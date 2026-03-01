"""OCR 子进程服务

通过子进程隔离执行 OCR 识别，解决 PaddlePaddle GPU 与 QThread 的兼容性问题。
提供与 OCRService 兼容的接口。

此版本使用 WorkerManager 管理 Worker 进程，支持健康检查、自动恢复、负载均衡等功能。
"""

import asyncio
import logging
import threading
import uuid
from typing import Optional, Union, List, Callable, TYPE_CHECKING

from vibeocr.services.worker_manager import WorkerManager, OCRWorkerProcessError

if TYPE_CHECKING:
    from vibeocr.models.batch_request import PreprocessOptions

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

    def __new__(
        cls,
        max_workers: int = 1,
        use_gpu: bool = True,
        shm_size: int = 10 * 1024 * 1024,
        auto_start: bool = True,
        start_timeout: float = 120.0,
        start_progress_callback: Optional[Callable[[str, int], None]] = None
    ) -> "OCRServiceSubprocess":
        """线程安全的单例创建

        注意：单例创建后，后续调用会忽略 max_workers 等参数。
        如需重新配置，请先调用 shutdown() 并设置 _instance = None。
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._initialized = False
                    cls._instance = instance
        return cls._instance

    def __init__(
        self,
        max_workers: int = 1,
        use_gpu: bool = True,
        shm_size: int = 10 * 1024 * 1024,
        auto_start: bool = True,
        start_timeout: float = 120.0,
        start_progress_callback: Optional[Callable[[str, int], None]] = None
    ):
        """初始化子进程 OCR 服务

        Args:
            max_workers: Worker 进程数量（默认 1）
            use_gpu: 是否使用 GPU
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

        # 使用 WorkerManager 管理 Worker 进程
        self._worker_manager = WorkerManager(
            max_workers=max_workers,
            use_gpu=use_gpu,
            shm_size=shm_size,
            start_timeout=start_timeout,
            auto_restart=True,
            max_retries=2
        )

        self._initialized = True
        logger.info(f"OCRServiceSubprocess 初始化: max_workers={max_workers}, use_gpu={use_gpu}")

        # 自动启动
        if auto_start:
            self.start(timeout=self.start_timeout, progress_callback=start_progress_callback)

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
        progress_callback: Optional[Callable[[str, int], None]] = None
    ) -> None:
        """启动所有 Worker

        Args:
            timeout: 每个 Worker 的启动超时时间
            progress_callback: 启动进度回调函数 (stage, percent)
        """
        # 检查是否已初始化（防止 shutdown 后重复启动）
        if not self._initialized:
            logger.warning("OCRServiceSubprocess 已关闭或未初始化，跳过启动")
            return

        try:
            logger.info("开始启动 Worker 进程...")
            if progress_callback:
                progress_callback("初始化 Worker 管理器", 10)

            self._worker_manager.start_all(progress_callback=progress_callback)
            logger.info("所有 Worker 已启动")

            if progress_callback:
                progress_callback("Worker 启动完成", 100)
        except Exception as e:
            logger.error(f"启动 Worker 失败: {e}")
            if progress_callback:
                progress_callback(f"启动失败: {str(e)[:50]}", 0)
            raise

    def recognize(
        self,
        image: Union[bytes, "Image.Image", "np.ndarray", str],
        options=None
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

        # 执行识别（通过 WorkerManager 自动处理负载均衡和故障恢复）
        return self._worker_manager.execute(
            lambda w: w.recognize(image_data, options_dict)
        )

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
                from PIL import Image
                import io
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

        # 如果是 OCROptions 对象，转换为字典
        if hasattr(options, '__dict__'):
            return {
                k: v for k, v in options.__dict__.items()
                if not k.startswith('_')
            }

        # 如果已经是字典
        if isinstance(options, dict):
            return options

        return {}

    def preload_pipelines(
        self,
        pipelines: list[str],
        timeout: float = 180.0
    ) -> dict[str, bool]:
        """预加载指定管道

        Args:
            pipelines: 管道名称列表 ["ocr", "table_recognition", ...]
            timeout: 超时时间（秒）

        Returns:
            {pipeline_name: success} 结果字典
        """
        # 执行预加载（通过 WorkerManager 自动处理负载均衡）
        return self._worker_manager.execute(
            lambda w: w.preload_pipelines(pipelines, timeout)
        )

    def warmup_pipelines(
        self,
        pipelines: list[str],
        timeout: float = 180.0
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
        # 执行预热（通过 WorkerManager 自动处理负载均衡）
        return self._worker_manager.execute(
            lambda w: w.warmup_pipelines(pipelines, timeout)
        )

    async def recognize_async(
        self,
        image: Union[bytes, "Image.Image", "np.ndarray", str],
        options=None
    ):
        """异步执行 OCR 识别（asyncio 协程）

        使用 run_in_executor 将同步调用包装为异步，避免阻塞事件循环。

        Args:
            image: 输入图像（bytes/PIL.Image/np.ndarray/str路径）
            options: OCR 选项（OCROptions 对象）

        Returns:
            OCRResult 对象

        Raises:
            OCRWorkerProcessError: 识别失败
            RuntimeError: 服务未就绪
        """
        logger.info("[recognize_async] 开始异步识别...")
        
        # 检查服务是否就绪
        if not self._initialized:
            logger.error("[recognize_async] 服务未初始化")
            raise RuntimeError("OCR 服务未初始化")

        ready = self.is_ready()
        logger.info(f"[recognize_async] 服务就绪状态: {ready}")
        if not ready:
            raise RuntimeError("OCR 服务未就绪，Worker 可能未启动")

        logger.info("[recognize_async] 调用 run_in_executor...")
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self.recognize, image, options)
        logger.info("[recognize_async] run_in_executor 完成")
        return result

    async def preload_pipelines_async(
        self,
        pipelines: list[str],
        timeout: float = 180.0
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
        logger.info("关闭 OCRServiceSubprocess...")

        # 标记为已关闭，防止自动重启
        self._initialized = False

        # 停止 WorkerManager
        if hasattr(self, '_worker_manager'):
            self._worker_manager.stop_all()

        logger.info("OCRServiceSubprocess 已关闭")

    def is_ready(self) -> bool:
        """检查服务是否就绪"""
        if not self._initialized or not hasattr(self, '_worker_manager'):
            return False
        return self._worker_manager.get_status().get("healthy", False)

    def get_status(self) -> dict:
        """获取服务状态"""
        if not hasattr(self, '_worker_manager'):
            return {
                "max_workers": self.max_workers,
                "use_gpu": self.use_gpu,
                "ready": False,
                "workers": []
            }
        
        manager_stats = self._worker_manager.get_stats()
        return {
            "max_workers": manager_stats["max_workers"],
            "use_gpu": self.use_gpu,
            "ready": manager_stats["ready_workers"] > 0,
            "workers": manager_stats["workers"]
        }

    def get_stats(self) -> dict:
        """获取详细统计信息"""
        if not hasattr(self, '_worker_manager'):
            return {}
        return self._worker_manager.get_stats()

    # =========================================================================
    # 批量处理接口
    # =========================================================================

    def batch_add(
        self,
        image: Union[bytes, "Image.Image", "np.ndarray", str],
        options=None,
        file_name: str = ""
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
        options_dict['file_name'] = file_name

        # 生成 request_id
        request_id = uuid.uuid4().hex[:12]

        # 序列化并发送
        from vibeocr.utils.shared_memory_v2 import (
            serialize_batch_request,
            MessageType,
        )

        request_data = serialize_batch_request(request_id, image_data, options_dict)

        # 发送到 Worker
        self._worker_manager.execute(
            lambda w: w._send_batch_add(request_data)
        )

        return request_id

    def batch_commit(
        self,
        preprocess_options: "PreprocessOptions",
        timeout: float = 300.0
    ) -> dict:
        """提交批量处理

        Args:
            preprocess_options: 预处理选项
            timeout: 超时时间（秒）

        Returns:
            {request_id: result} 结果字典
        """
        if not self._initialized:
            raise RuntimeError("OCR 服务未初始化")

        from vibeocr.utils.shared_memory_v2 import (
            serialize_batch_commit,
            MessageType,
        )

        # 序列化并发送
        commit_data = serialize_batch_commit(preprocess_options.to_dict())

        # 发送并等待结果
        return self._worker_manager.execute(
            lambda w: w._send_batch_commit(commit_data, timeout)
        )

    def batch_cancel(self):
        """取消批量处理"""
        if not self._initialized:
            return

        from vibeocr.utils.shared_memory_v2 import MessageType

        self._worker_manager.execute(
            lambda w: w._send_batch_cancel()
        )

    def __enter__(self) -> "OCRServiceSubprocess":
        """上下文管理器入口"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """上下文管理器退出"""
        self.shutdown()
