"""Worker 进程管理器

管理多个 OCR Worker 进程，提供负载均衡、故障恢复、健康检查等功能。
这是长期优化的一部分，将进程池逻辑从 OCRServiceSubprocess 中分离出来。
"""

import contextlib
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from vibeocr.services.ocr_worker_process import OCRWorkerProcess, OCRWorkerProcessError

logger = logging.getLogger(__name__)


class WorkerState(Enum):
    """Worker 状态枚举"""

    IDLE = auto()  # 空闲，可接受任务
    BUSY = auto()  # 正在处理任务
    STARTING = auto()  # 正在启动
    STOPPING = auto()  # 正在停止
    ERROR = auto()  # 发生错误
    STOPPED = auto()  # 已停止


@dataclass
class WorkerInfo:
    """Worker 信息"""

    worker_id: int
    process: OCRWorkerProcess
    state: WorkerState = WorkerState.STOPPED
    total_tasks: int = 0
    failed_tasks: int = 0
    last_error: str | None = None
    last_active: float = field(default_factory=time.time)
    created_at: float = field(default_factory=time.time)

    @property
    def is_available(self) -> bool:
        """检查 Worker 是否可用"""
        return (
            self.state in (WorkerState.IDLE, WorkerState.BUSY) and self.process.is_ready
        )

    @property
    def uptime(self) -> float:
        """获取运行时间（秒）"""
        return time.time() - self.created_at


class WorkerManager:
    """Worker 进程管理器

    管理多个 Worker 进程，提供以下功能：
    1. 自动负载均衡（轮询）
    2. 健康检查和自动恢复
    3. 任务失败重试
    4. Worker 资源统计

    使用示例:
        manager = WorkerManager(max_workers=2, use_gpu=True)
        manager.start_all()

        # 执行任务
        result = manager.execute(lambda w: w.recognize(image_data, options))

        # 停止所有 Worker
        manager.stop_all()
    """

    def __init__(
        self,
        max_workers: int = 1,
        use_gpu: bool = True,
        shm_size: int = 10 * 1024 * 1024,
        start_timeout: float = 120.0,
        health_check_interval: float = 30.0,
        auto_restart: bool = True,
        max_retries: int = 2,
        worker_module: str = "vibeocr.workers.ocr_worker",
    ):
        """初始化 Worker 管理器

        Args:
            max_workers: Worker 进程数量
            use_gpu: 是否使用 GPU
            shm_size: 每个 Worker 的共享内存大小
            start_timeout: 启动超时时间（秒）
            health_check_interval: 健康检查间隔（秒）
            auto_restart: Worker 崩溃时是否自动重启
            max_retries: 任务失败时的最大重试次数
            worker_module: Worker 子进程模块路径
        """
        self.max_workers = max_workers
        self.use_gpu = use_gpu
        self.shm_size = shm_size
        self.start_timeout = start_timeout
        self.health_check_interval = health_check_interval
        self.auto_restart = auto_restart
        self.max_retries = max_retries
        self.worker_module = worker_module

        # Worker 列表
        self._workers: list[WorkerInfo] = []
        self._workers_lock = threading.RLock()

        # 轮询索引
        self._round_robin_index = 0

        # 健康检查线程
        self._health_check_thread: threading.Thread | None = None
        self._health_check_stop = threading.Event()

        # 统计信息
        self._total_tasks = 0
        self._failed_tasks = 0
        self._retried_tasks = 0

        # 关闭标志（用于避免关闭时误判崩溃）
        self._shutting_down = False

        logger.info(
            f"WorkerManager 初始化: max_workers={max_workers}, use_gpu={use_gpu}"
        )

    def start_all(self, progress_callback: Callable[[str], None] | None = None) -> None:
        """启动所有 Worker

        Args:
            progress_callback: 启动进度回调函数 (stage)
        """

        def report_progress(stage: str):
            """报告进度"""
            if progress_callback:
                with contextlib.suppress(Exception):
                    progress_callback(stage)

        with self._workers_lock:
            # 创建 Worker 实例
            for i in range(self.max_workers):
                if i >= len(self._workers):
                    report_progress(f"创建 Worker {i}")
                    worker_process = OCRWorkerProcess(
                        worker_id=i, use_gpu=self.use_gpu, shm_size=self.shm_size,
                        worker_module=self.worker_module,
                    )
                    info = WorkerInfo(worker_id=i, process=worker_process)
                    self._workers.append(info)
                else:
                    info = self._workers[i]

                # 启动 Worker
                if not info.process.is_running:
                    try:
                        info.state = WorkerState.STARTING
                        report_progress(f"启动 Worker {i}")

                        # 定义 Worker 启动进度回调
                        def make_worker_progress(worker_id: int):
                            def worker_progress(stage: str, percent: int):
                                report_progress(f"Worker {worker_id}: {stage}")

                            return worker_progress

                        info.process.start(
                            timeout=self.start_timeout,
                            progress_callback=make_worker_progress(i),
                        )
                        info.state = WorkerState.IDLE
                        info.last_active = time.time()
                        report_progress(f"Worker {i} 就绪")
                        logger.info(f"Worker {i} 启动成功")
                    except Exception as e:
                        info.state = WorkerState.ERROR
                        info.last_error = str(e)
                        logger.error(f"Worker {i} 启动失败: {e}")
                        raise

        # 启动健康检查线程
        report_progress("启动健康检查")
        self._start_health_check()

        report_progress("所有 Worker 已启动")
        logger.info(f"所有 Worker 已启动 ({len(self._workers)}/{self.max_workers})")

    def stop_all(self, timeout: float = 10.0) -> None:
        """停止所有 Worker

        Args:
            timeout: 每个 Worker 的停止超时时间（秒）
        """
        # 设置关闭标志，避免误判崩溃
        self._shutting_down = True

        # 停止健康检查线程
        self._stop_health_check()

        with self._workers_lock:
            for info in self._workers:
                if info.process.is_running:
                    try:
                        info.state = WorkerState.STOPPING
                        info.process.stop(timeout=timeout)
                        info.state = WorkerState.STOPPED
                        logger.info(f"Worker {info.worker_id} 已停止")
                    except Exception as e:
                        info.state = WorkerState.ERROR
                        info.last_error = str(e)
                        logger.warning(f"Worker {info.worker_id} 停止时出错: {e}")

        logger.info("所有 Worker 已停止")

    def execute(
        self,
        task: Callable[[OCRWorkerProcess], Any],
        timeout: float = 60.0,
        retry_count: int = 0,
    ) -> Any:
        """在可用的 Worker 上执行任务

        Args:
            task: 任务函数，接收 WorkerProcess 参数
            timeout: 任务超时时间（秒）
            retry_count: 当前重试次数（内部使用）

        Returns:
            任务执行结果

        Raises:
            OCRWorkerProcessError: 所有 Worker 都不可用或任务执行失败
        """
        # 获取可用 Worker
        worker_info = self._get_available_worker()
        if worker_info is None:
            # 尝试恢复 Worker
            if self.auto_restart:
                logger.warning("无可用 Worker，尝试恢复...")
                self._recover_workers()
                worker_info = self._get_available_worker()

            # 恢复后仍无可用 Worker，尝试抢占被后台任务（如预加载）阻塞的 Worker
            if worker_info is None:
                worker_info = self._preempt_busy_worker()

            if worker_info is None:
                raise OCRWorkerProcessError("无可用 Worker")

        # 标记为忙碌
        worker_info.state = WorkerState.BUSY
        worker_info.total_tasks += 1
        self._total_tasks += 1

        try:
            # 执行任务
            result = task(worker_info.process)
            worker_info.state = WorkerState.IDLE
            worker_info.last_active = time.time()
            return result

        except Exception as e:
            worker_info.failed_tasks += 1
            self._failed_tasks += 1
            worker_info.last_error = str(e)

            # 检查 Worker 是否仍然存活
            if not worker_info.process.is_running:
                # 如果正在关闭，不要误判为崩溃
                if self._shutting_down:
                    logger.debug(
                        f"Worker {worker_info.worker_id} 已停止（应用程序关闭中）"
                    )
                    worker_info.state = WorkerState.STOPPED
                else:
                    logger.error(f"Worker {worker_info.worker_id} 已崩溃")
                    worker_info.state = WorkerState.ERROR

                    # 自动重启
                    if self.auto_restart and retry_count < self.max_retries:
                        logger.info(f"尝试重启 Worker {worker_info.worker_id}...")
                        if self._restart_worker(worker_info):
                            self._retried_tasks += 1
                            # 重试任务
                            return self.execute(task, timeout, retry_count + 1)

            # 避免覆盖其他任务（如抢占重启后的识别任务）设置的状态
            if worker_info.state not in (WorkerState.BUSY, WorkerState.STARTING):
                worker_info.state = WorkerState.IDLE
            raise

    def _get_available_worker(self, wait_timeout: float = 5.0) -> WorkerInfo | None:
        """获取可用的 Worker（轮询）

        Args:
            wait_timeout: 等待 Worker 可用的超时时间（秒）

        Returns:
            可用的 WorkerInfo，如果没有则返回 None
        """
        start_time = time.time()

        while time.time() - start_time < wait_timeout:
            with self._workers_lock:
                if not self._workers:
                    return None

                # 轮询查找空闲 Worker
                for _ in range(len(self._workers)):
                    worker_info = self._workers[self._round_robin_index]
                    self._round_robin_index = (self._round_robin_index + 1) % len(
                        self._workers
                    )

                    if worker_info.is_available and not worker_info.process.busy:
                        return worker_info

            # 等待一小段时间再重试
            time.sleep(0.1)

        # 超时，没有空闲 Worker
        return None

    def _restart_worker(self, worker_info: WorkerInfo) -> bool:
        """重启指定 Worker

        Args:
            worker_info: 要重启的 Worker

        Returns:
            重启是否成功
        """
        try:
            worker_info.process.stop(timeout=5.0)
        except Exception as e:
            logger.warning(f"Worker {worker_info.worker_id} 停止失败: {e}")

        try:
            worker_info.state = WorkerState.STARTING
            worker_info.process.start(timeout=self.start_timeout)
            worker_info.state = WorkerState.IDLE
            worker_info.last_error = None
            worker_info.last_active = time.time()
            logger.info(f"Worker {worker_info.worker_id} 重启成功")
            return True
        except Exception as e:
            worker_info.state = WorkerState.ERROR
            worker_info.last_error = str(e)
            logger.error(f"Worker {worker_info.worker_id} 重启失败: {e}")
            return False

    def _preempt_busy_worker(self) -> WorkerInfo | None:
        """抢占被后台任务（如预加载）阻塞的 Worker

        当识别任务需要 Worker 但所有 Worker 都忙于长时间后台操作时，
        强制重启一个 Worker 来释放给识别任务使用。

        Returns:
            被释放的 WorkerInfo，如果没有可抢占的 Worker 则返回 None
        """
        target = None
        with self._workers_lock:
            for worker_info in self._workers:
                if worker_info.state == WorkerState.BUSY and worker_info.process.busy:
                    target = worker_info
                    target.state = WorkerState.STOPPING
                    break

        if target is None:
            return None

        logger.warning(
            f"Worker {target.worker_id} 正忙于后台操作，"
            f"强制重启以释放给识别任务"
        )

        if self._restart_worker(target):
            return target

        return None

    def _recover_workers(self) -> None:
        """恢复所有异常的 Worker"""
        with self._workers_lock:
            for worker_info in self._workers:
                if (
                    worker_info.state == WorkerState.ERROR
                    or not worker_info.process.is_running
                ):
                    self._restart_worker(worker_info)

    def _start_health_check(self) -> None:
        """启动健康检查线程"""
        if (
            self._health_check_thread is not None
            and self._health_check_thread.is_alive()
        ):
            return

        self._health_check_stop.clear()
        self._health_check_thread = threading.Thread(
            target=self._health_check_loop, name="WorkerHealthCheck", daemon=True
        )
        self._health_check_thread.start()
        logger.debug("健康检查线程已启动")

    def _stop_health_check(self) -> None:
        """停止健康检查线程"""
        if self._health_check_thread is None:
            return

        self._health_check_stop.set()
        self._health_check_thread.join(timeout=2.0)
        self._health_check_thread = None
        logger.debug("健康检查线程已停止")

    def _health_check_loop(self) -> None:
        """健康检查循环"""
        while not self._health_check_stop.is_set():
            try:
                self._perform_health_check()
            except Exception as e:
                logger.error(f"健康检查出错: {e}")

            # 等待下一次检查
            self._health_check_stop.wait(self.health_check_interval)

    def _perform_health_check(self) -> None:
        """执行健康检查"""
        with self._workers_lock:
            for worker_info in self._workers:
                # 检查进程是否存活
                if not worker_info.process.is_running:
                    if worker_info.state != WorkerState.STOPPED:
                        logger.warning(f"Worker {worker_info.worker_id} 进程已退出")
                        worker_info.state = WorkerState.ERROR

                        # 自动重启
                        if self.auto_restart:
                            self._restart_worker(worker_info)
                    continue

                # 检查是否卡死（超过 5 分钟无活动）
                if worker_info.state == WorkerState.BUSY:
                    idle_time = time.time() - worker_info.last_active
                    if idle_time > 300:  # 5 分钟
                        logger.warning(
                            f"Worker {worker_info.worker_id} 可能卡死（无响应 {idle_time:.0f} 秒）"
                        )
                        # 强制重启
                        self._restart_worker(worker_info)

    def get_stats(self) -> dict:
        """获取统计信息

        Returns:
            统计信息字典
        """
        with self._workers_lock:
            worker_stats = [
                {
                    "id": w.worker_id,
                    "state": w.state.name,
                    "is_running": w.process.is_running,
                    "is_ready": w.process.is_ready,
                    "total_tasks": w.total_tasks,
                    "failed_tasks": w.failed_tasks,
                    "uptime": w.uptime,
                    "last_error": w.last_error,
                }
                for w in self._workers
            ]

            return {
                "max_workers": self.max_workers,
                "active_workers": sum(1 for w in self._workers if w.process.is_running),
                "ready_workers": sum(1 for w in self._workers if w.process.is_ready),
                "total_tasks": self._total_tasks,
                "failed_tasks": self._failed_tasks,
                "retried_tasks": self._retried_tasks,
                "workers": worker_stats,
            }

    def get_status(self) -> dict:
        """获取状态摘要

        Returns:
            状态字典
        """
        stats = self.get_stats()
        return {
            "healthy": stats["ready_workers"] > 0,
            "ready_count": stats["ready_workers"],
            "total_count": stats["max_workers"],
            "availability": (
                stats["ready_workers"] / stats["max_workers"]
                if stats["max_workers"] > 0
                else 0
            ),
        }

    def __enter__(self) -> "WorkerManager":
        """上下文管理器入口"""
        self.start_all()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """上下文管理器退出"""
        self.stop_all()
