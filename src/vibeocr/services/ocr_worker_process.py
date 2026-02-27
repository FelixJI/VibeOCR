"""OCR Worker 进程管理器

管理单个 OCR Worker 子进程的生命周期。
"""

import logging
import subprocess
import time
import uuid
from pathlib import Path
from typing import Optional

from vibeocr.utils.shared_memory import (
    SharedMemoryProtocol,
    SharedMemoryProtocolError,
    MSG_RECOGNIZE,
    MSG_RESULT,
    MSG_ERROR,
    MSG_ACK,
    serialize_request,
    deserialize_result,
)

logger = logging.getLogger(__name__)


class OCRWorkerProcessError(Exception):
    """OCR Worker 进程错误"""
    pass


class OCRWorkerProcess:
    """单个 OCR Worker 进程管理器

    负责启动、停止和与 Worker 子进程通信。

    使用示例:
        worker = OCRWorkerProcess(worker_id=0, use_gpu=True)
        worker.start()

        # 执行识别
        result = worker.recognize(image_data, options_dict)

        # 停止 Worker
        worker.stop()
    """

    def __init__(
        self,
        worker_id: int,
        use_gpu: bool = True,
        shm_size: int = 10 * 1024 * 1024
    ):
        """初始化 Worker 进程管理器

        Args:
            worker_id: Worker 标识符
            use_gpu: 是否使用 GPU
            shm_size: 共享内存大小（字节）
        """
        self.worker_id = worker_id
        self.use_gpu = use_gpu
        self.shm_size = shm_size

        # 生成唯一的共享内存名称
        self.shm_name = f"vibeocr_shm_{uuid.uuid4().hex[:16]}_{worker_id}"

        # 进程和通信
        self.process: Optional[subprocess.Popen] = None
        self.protocol: Optional[SharedMemoryProtocol] = None

        # 状态
        self.busy = False
        self._ready = False

    @property
    def is_running(self) -> bool:
        """检查 Worker 进程是否在运行"""
        return self.process is not None and self.process.poll() is None

    @property
    def is_ready(self) -> bool:
        """检查 Worker 是否就绪"""
        return self._ready and self.is_running

    def _get_python_executable(self) -> str:
        """获取 Python 可执行文件路径"""
        # 优先使用当前 Python 解释器
        import sys
        return sys.executable

    def _get_worker_script(self) -> str:
        """获取 Worker 脚本路径"""
        # 使用模块方式运行
        return "-m"

    def start(self, timeout: float = 60.0) -> None:
        """启动 Worker 进程

        Args:
            timeout: 等待就绪的超时时间（秒）

        Raises:
            OCRWorkerProcessError: 启动失败
        """
        if self.is_running:
            logger.warning(f"Worker {self.worker_id} 已在运行")
            return

        logger.info(f"启动 Worker {self.worker_id}...")

        # 创建共享内存
        try:
            self.protocol = SharedMemoryProtocol(self.shm_name, self.shm_size)
            self.protocol.create()
            logger.debug(f"创建共享内存: {self.shm_name}")
        except Exception as e:
            raise OCRWorkerProcessError(f"创建共享内存失败: {e}")

        # 启动子进程
        python_exe = self._get_python_executable()
        cmd = [
            python_exe,
            "-m",
            "vibeocr.workers.ocr_worker",
            "--shm-name", self.shm_name,
            "--shm-size", str(self.shm_size),
            "--use-gpu" if self.use_gpu else "--no-gpu"
        ]

        logger.debug(f"启动命令: {' '.join(cmd)}")

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
        except Exception as e:
            self.protocol.close()
            self.protocol.unlink()
            raise OCRWorkerProcessError(f"启动子进程失败: {e}")

        # 等待就绪信号
        start_time = time.time()
        while time.time() - start_time < timeout:
            if not self.is_running:
                # 进程已退出，读取错误信息
                stdout, stderr = self.process.communicate(timeout=5)
                error_msg = stderr or stdout or "未知错误"
                raise OCRWorkerProcessError(f"Worker 进程启动失败: {error_msg}")

            try:
                # 尝试读取就绪信号
                msg_type, data = self.protocol.read_message(timeout=1.0)
                if msg_type == MSG_ACK and data == b"READY":
                    self._ready = True
                    logger.info(f"Worker {self.worker_id} 已就绪")
                    return
            except SharedMemoryProtocolError:
                # 超时，继续等待
                pass

        # 超时
        self.stop()
        raise OCRWorkerProcessError(f"等待 Worker 就绪超时 ({timeout}s)")

    def recognize(
        self,
        image_data: bytes,
        options_dict: dict,
        timeout: float = 60.0
    ):
        """执行 OCR 识别

        Args:
            image_data: 图像数据（bytes）
            options_dict: OCR 选项字典
            timeout: 超时时间（秒）

        Returns:
            OCRResult 对象

        Raises:
            OCRWorkerProcessError: 识别失败
        """
        if not self.is_ready:
            raise OCRWorkerProcessError(f"Worker {self.worker_id} 未就绪")

        if self.busy:
            raise OCRWorkerProcessError(f"Worker {self.worker_id} 正忙")

        self.busy = True

        try:
            # 序列化并发送请求
            request_data = serialize_request(image_data, options_dict)
            self.protocol.write_message(MSG_RECOGNIZE, request_data, timeout=timeout)

            # 等待结果
            msg_type, data = self.protocol.read_message(timeout=timeout)

            if msg_type == MSG_RESULT:
                # 反序列化结果
                result = deserialize_result(data)
                logger.debug(f"Worker {self.worker_id} 识别完成")
                return result

            elif msg_type == MSG_ERROR:
                error_msg = data.decode("utf-8", errors="replace")
                raise OCRWorkerProcessError(f"OCR 识别失败: {error_msg}")

            else:
                raise OCRWorkerProcessError(f"未知响应类型: {msg_type}")

        except SharedMemoryProtocolError as e:
            raise OCRWorkerProcessError(f"通信错误: {e}")

        finally:
            self.busy = False

    def stop(self, timeout: float = 5.0) -> None:
        """停止 Worker 进程

        Args:
            timeout: 等待进程退出的超时时间（秒）
        """
        if self.process is None:
            return

        logger.info(f"停止 Worker {self.worker_id}...")

        # 尝试发送关闭信号
        if self.protocol and self.is_running:
            try:
                self.protocol.write_message(b"SHUT", b"", timeout=1.0)
            except SharedMemoryProtocolError:
                pass

        # 等待进程退出
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # 强制终止
            logger.warning(f"Worker {self.worker_id} 未响应，强制终止")
            self.process.kill()
            self.process.wait(timeout=1.0)

        # 关闭共享内存
        if self.protocol:
            self.protocol.close()
            self.protocol.unlink()
            self.protocol = None

        self.process = None
        self._ready = False
        self.busy = False

        logger.info(f"Worker {self.worker_id} 已停止")

    def restart(self, timeout: float = 60.0) -> None:
        """重启 Worker 进程

        Args:
            timeout: 等待就绪的超时时间（秒）
        """
        logger.info(f"重启 Worker {self.worker_id}...")
        self.stop()
        self.start(timeout)

    def __enter__(self) -> "OCRWorkerProcess":
        """上下文管理器入口"""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """上下文管理器退出"""
        self.stop()

    def __repr__(self) -> str:
        status = "running" if self.is_running else "stopped"
        ready = "ready" if self._ready else "not ready"
        busy = "busy" if self.busy else "idle"
        return f"<OCRWorkerProcess id={self.worker_id} {status} {ready} {busy}>"
