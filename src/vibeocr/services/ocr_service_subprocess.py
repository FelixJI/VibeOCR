"""OCR 子进程服务

通过子进程隔离执行 OCR 识别，解决 PaddlePaddle GPU 与 QThread 的兼容性问题。
提供与 OCRService 兼容的接口。
"""

import logging
import threading
from typing import Optional, Union
from pathlib import Path

from vibeocr.services.ocr_worker_process import OCRWorkerProcess, OCRWorkerProcessError

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
        auto_start: bool = True
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
        auto_start: bool = True
    ):
        """初始化子进程 OCR 服务

        Args:
            max_workers: Worker 进程数量（默认 1）
            use_gpu: 是否使用 GPU
            shm_size: 每个共享内存大小（字节）
            auto_start: 是否自动启动 Worker
        """
        if self._initialized:
            return

        self.max_workers = max_workers
        self.use_gpu = use_gpu
        self.shm_size = shm_size

        self.workers: list[OCRWorkerProcess] = []
        self._round_robin_index = 0
        self._workers_lock = threading.Lock()

        # 创建 Worker 实例（但不启动）
        for i in range(max_workers):
            worker = OCRWorkerProcess(
                worker_id=i,
                use_gpu=use_gpu,
                shm_size=shm_size
            )
            self.workers.append(worker)

        self._initialized = True
        logger.info(f"OCRServiceSubprocess 初始化: max_workers={max_workers}, use_gpu={use_gpu}")

        # 自动启动
        if auto_start:
            self.start()

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例实例

        警告：这会关闭所有现有 Worker，仅用于测试或特殊场景。
        """
        with cls._lock:
            if cls._instance is not None:
                cls._instance.shutdown()
                cls._instance = None

    def start(self, timeout: float = 120.0) -> None:
        """启动所有 Worker

        Args:
            timeout: 每个 Worker 的启动超时时间
        """
        with self._workers_lock:
            for worker in self.workers:
                if not worker.is_running:
                    try:
                        worker.start(timeout=timeout)
                    except OCRWorkerProcessError as e:
                        logger.error(f"启动 Worker {worker.worker_id} 失败: {e}")
                        raise

        logger.info("所有 Worker 已启动")

    def _get_available_worker(self) -> OCRWorkerProcess:
        """轮询获取可用的 Worker

        Returns:
            可用的 WorkerProcess

        Raises:
            OCRWorkerProcessError: 没有可用的 Worker
        """
        with self._workers_lock:
            if not self.workers:
                raise OCRWorkerProcessError("没有可用的 Worker")

            # 轮询查找空闲 Worker
            for _ in range(len(self.workers)):
                worker = self.workers[self._round_robin_index]
                self._round_robin_index = (self._round_robin_index + 1) % len(self.workers)

                if worker.is_ready and not worker.busy:
                    return worker

            # 如果所有 Worker 都忙，等待第一个（简单策略）
            # 在实际使用中，由于单 Worker 模式，这种情况很少发生
            worker = self.workers[0]
            if worker.is_ready:
                return worker

            raise OCRWorkerProcessError("所有 Worker 都忙或未就绪")

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
        """
        # 获取可用 Worker
        worker = self._get_available_worker()

        # 准备图像数据
        image_data = self._prepare_image_data(image)

        # 准备选项字典
        options_dict = self._prepare_options_dict(options)

        # 执行识别
        return worker.recognize(image_data, options_dict)

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

    def shutdown(self) -> None:
        """关闭所有 Worker"""
        logger.info("关闭 OCRServiceSubprocess...")

        with self._workers_lock:
            for worker in self.workers:
                try:
                    worker.stop()
                except Exception as e:
                    logger.warning(f"停止 Worker {worker.worker_id} 时出错: {e}")

            self.workers.clear()

        logger.info("OCRServiceSubprocess 已关闭")

    def is_ready(self) -> bool:
        """检查服务是否就绪"""
        with self._workers_lock:
            return any(w.is_ready for w in self.workers)

    def get_status(self) -> dict:
        """获取服务状态"""
        with self._workers_lock:
            workers_status = [
                {
                    "id": w.worker_id,
                    "running": w.is_running,
                    "ready": w.is_ready,
                    "busy": w.busy
                }
                for w in self.workers
            ]

            return {
                "max_workers": self.max_workers,
                "use_gpu": self.use_gpu,
                "ready": self.is_ready(),
                "workers": workers_status
            }

    def __enter__(self) -> "OCRServiceSubprocess":
        """上下文管理器入口"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """上下文管理器退出"""
        self.shutdown()
