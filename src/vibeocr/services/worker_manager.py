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

from vibeocr.core.constants import DEFAULT_SHM_SIZE, Constants
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

    # 健康检查卡死阈值（秒）。必须大于最大批量任务超时（recognize_batch
    # 封顶 1800s），否则会把正常的 25 页批量识别误判为卡死并强制重启，
    # 导致任务在已 unlink 的 shm 上空轮询、UI 卡死、取消无效。
    STALE_THRESHOLD: float = 2000.0

    def __init__(
        self,
        max_workers: int = 1,
        use_gpu: bool = True,
        shm_size: int = DEFAULT_SHM_SIZE,
        start_timeout: float = Constants.Timeout.WORKER_START,
        health_check_interval: float = 30.0,
        auto_restart: bool = True,
        max_retries: int = 2,
        worker_module: str = "vibeocr.workers.ocr_worker",
        cancel_event: threading.Event | None = None,
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
            cancel_event: 外部取消事件。set() 后,正在 _get_available_worker
                中长等待的 execute 调用会立即返回并抛 OCRWorkerProcessError,
                避免 5 分钟盲等无法中断。None 时退化为旧行为(纯 sleep 轮询)。
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

        # 任务排队通知回调
        self._task_queued_callback: Callable | None = None

        # 外部取消事件（用于中断 _get_available_worker 的长等待）
        self._cancel_event: threading.Event | None = cancel_event

        logger.debug(
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
                        worker_id=i,
                        use_gpu=self.use_gpu,
                        shm_size=self.shm_size,
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
                        logger.debug(f"Worker {i} 启动成功")
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
                        logger.debug(f"Worker {info.worker_id} 已停止")
                    except Exception as e:
                        info.state = WorkerState.ERROR
                        info.last_error = str(e)
                        logger.warning(f"Worker {info.worker_id} 停止时出错: {e}")

        logger.debug("所有 Worker 已停止")

    def _reserve_worker(self, wait_timeout: float = 5.0) -> WorkerInfo | None:
        """原子地选择空闲 worker 并标记为 BUSY。

        选择 + 状态迁移 + 计数在同一个 _workers_lock 临界区内完成，
        避免两个调用线程同时拿到同一 worker（旧 _get_available_worker 在
        锁内返回引用、锁外才设 BUSY 的 TOCTOU 竞态）。
        """
        start_time = time.time()
        while time.time() - start_time < wait_timeout:
            with self._workers_lock:
                if not self._workers:
                    return None
                for _ in range(len(self._workers)):
                    worker_info = self._workers[self._round_robin_index]
                    self._round_robin_index = (self._round_robin_index + 1) % len(
                        self._workers
                    )
                    if (
                        worker_info.state == WorkerState.IDLE
                        and worker_info.process.is_ready
                        and not worker_info.process.busy
                    ):
                        # 原子标记 BUSY + 计数（与选择在同一临界区）
                        worker_info.state = WorkerState.BUSY
                        worker_info.total_tasks += 1
                        self._total_tasks += 1
                        return worker_info
            # 锁外等待
            if self._cancel_event is not None:
                if self._cancel_event.wait(0.1):
                    logger.debug("等待 Worker 时收到取消信号,提前返回")
                    return None
            else:
                time.sleep(0.1)
        return None

    def _release_worker(self, worker_info: WorkerInfo, success: bool) -> None:
        """在锁内完成 worker 状态迁移（完成→IDLE 或 失败处理）。

        将原 execute() 中散落在锁外的状态迁移集中到锁内，保证健康检查
        等并发读者看到一致的状态。
        """
        with self._workers_lock:
            if success:
                worker_info.state = WorkerState.IDLE
                worker_info.last_active = time.time()
            else:
                worker_info.failed_tasks += 1
                self._failed_tasks += 1
                if not worker_info.process.is_running:
                    if self._shutting_down:
                        worker_info.state = WorkerState.STOPPED
                    else:
                        worker_info.state = WorkerState.ERROR
                elif worker_info.state not in (
                    WorkerState.BUSY,
                    WorkerState.STARTING,
                ):
                    worker_info.state = WorkerState.IDLE

    def execute(
        self,
        task: Callable[[OCRWorkerProcess], Any],
        retry_count: int = 0,
    ) -> Any:
        """在可用的 Worker 上执行任务

        注意:本方法**不强制任务超时**。任务的超时由 ``task`` 闭包通过
        ``worker.recognize(..., timeout=)`` 自行管理(下沉到 SHM IPC 层)。
        历史上本方法曾有 ``timeout`` 形参,但从未传给 ``task``,属于死参数,
        已移除以避免误导。

        Args:
            task: 任务函数，接收 WorkerProcess 参数。调用方应在闭包内
                绑定具体的超时值（如 ``lambda w: w.recognize(..., timeout=to)``）。
            retry_count: 当前重试次数（内部使用）

        Returns:
            任务执行结果

        Raises:
            OCRWorkerProcessError: 所有 Worker 都不可用或任务执行失败
        """
        # 原子领取 Worker（选择+BUSY+计数在锁内完成）
        worker_info = self._reserve_worker(wait_timeout=2.0)
        if worker_info is None:
            # 尝试恢复 Worker
            if self.auto_restart:
                logger.warning("无可用 Worker，尝试恢复...")
                self._recover_workers()
                worker_info = self._reserve_worker(wait_timeout=2.0)

            if worker_info is None:
                # Worker 仍在忙（可能在预加载），通知 UI 并长等待
                # 但若已收到取消信号,直接抛错,不进入 300s 盲等
                if self._cancel_event is not None and self._cancel_event.is_set():
                    raise OCRWorkerProcessError("任务已取消（应用关闭中）")
                logger.debug("Worker 忙碌，排队等待...")
                if self._task_queued_callback:
                    try:
                        self._task_queued_callback()
                    except Exception:
                        pass
                worker_info = self._reserve_worker(wait_timeout=300.0)

            if worker_info is None:
                raise OCRWorkerProcessError("无可用 Worker")

        try:
            # 执行任务
            result = task(worker_info.process)
            self._release_worker(worker_info, success=True)
            return result

        except Exception as e:
            worker_info.last_error = str(e)
            crashed = not worker_info.process.is_running
            self._release_worker(worker_info, success=False)
            if crashed and not self._shutting_down:
                logger.error(f"Worker {worker_info.worker_id} 已崩溃")
                # 自动重启
                if self.auto_restart and retry_count < self.max_retries:
                    logger.debug(f"尝试重启 Worker {worker_info.worker_id}...")
                    if self._restart_worker(worker_info):
                        self._retried_tasks += 1
                        # 重试任务
                        return self.execute(task, retry_count + 1)
            raise

    def _get_available_worker(self, wait_timeout: float = 5.0) -> WorkerInfo | None:
        """获取可用的 Worker（轮询）

        Args:
            wait_timeout: 等待 Worker 可用的超时时间（秒）

        Returns:
            可用的 WorkerInfo，如果没有则返回 None。
            若设置了 cancel_event 且被 set,会提前返回 None。
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

            # 等待一小段时间再重试。
            # 若设置了 cancel_event,用 event.wait(0.1) 替代 sleep(0.1),
            # 这样事件被 set 时能立即返回 True,中断长等待。
            if self._cancel_event is not None:
                if self._cancel_event.wait(0.1):
                    # 取消信号已触发,立即返回 None
                    logger.debug("等待 Worker 时收到取消信号,提前返回")
                    return None
            else:
                time.sleep(0.1)

        # 超时，没有空闲 Worker
        return None

    def _restart_worker(self, worker_info: WorkerInfo) -> bool:
        """重启指定 Worker（仅在 worker 未就绪时真正重启）

        委托给 OCRWorkerProcess._try_restart() 执行，
        后者在 is_ready 时直接返回 True（不 stop/start）。
        适用于崩溃恢复（进程已退出，is_ready=False）。

        注意：健康检查发现 stale-but-alive worker 时不应使用此方法
        （is_ready=True 会导致跳过重启），应使用 _force_restart_worker。

        Args:
            worker_info: 要重启的 Worker

        Returns:
            重启是否成功
        """
        worker_info.state = WorkerState.STARTING
        success = worker_info.process._try_restart(timeout=self.start_timeout)
        if success:
            worker_info.state = WorkerState.IDLE
            worker_info.last_error = None
            worker_info.last_active = time.time()
            logger.debug(f"Worker {worker_info.worker_id} 重启成功")
        else:
            worker_info.state = WorkerState.ERROR
            logger.error(f"Worker {worker_info.worker_id} 重启失败")
        return success

    def _force_restart_worker(
        self, worker_info: WorkerInfo, reason: str = ""
    ) -> bool:
        """强制重启 worker（健康检查/抢占专用）。

        调用 force_restart（总是 stop+start，即使 is_ready），而非 _try_restart
        （后者在 is_ready 时跳过重启）。这确保 stale-but-alive worker 的
        协议状态被重建，避免误报"重启成功"后继续消费旧响应。
        """
        worker_info.state = WorkerState.STARTING
        success = worker_info.process.force_restart(
            reason=reason, timeout=self.start_timeout
        )
        if success:
            worker_info.state = WorkerState.IDLE
            worker_info.last_error = None
            worker_info.last_active = time.time()
            logger.debug(f"Worker {worker_info.worker_id} 强制重启成功")
        else:
            worker_info.state = WorkerState.ERROR
            logger.error(f"Worker {worker_info.worker_id} 强制重启失败")
        return success

    def set_task_queued_callback(self, callback: Callable) -> None:
        """设置任务排队通知回调"""
        self._task_queued_callback = callback

    def set_cancel_event(self, event: threading.Event) -> None:
        """设置外部取消事件（用于中断 execute 内 _get_available_worker 的长等待）

        可在应用关闭时 set() 该事件,使正在排队等待 Worker 的 execute
        调用立即返回,而不是继续等待最多 5 分钟。
        """
        self._cancel_event = event

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
        workers_to_restart = []
        with self._workers_lock:
            for worker_info in self._workers:
                # 检查进程是否存活
                if not worker_info.process.is_running:
                    if worker_info.state != WorkerState.STOPPED:
                        logger.warning(f"Worker {worker_info.worker_id} 进程已退出")
                        worker_info.state = WorkerState.ERROR

                        # 自动重启
                        if self.auto_restart:
                            workers_to_restart.append(worker_info)
                    continue

                # 检查是否卡死（超过 STALE_THRESHOLD 无活动）
                # 阈值必须 > 最大批量超时(1800s)，否则误杀正常批量任务
                if worker_info.state == WorkerState.BUSY:
                    idle_time = time.time() - worker_info.last_active
                    if idle_time > self.STALE_THRESHOLD:
                        logger.warning(
                            f"Worker {worker_info.worker_id} 可能卡死（无响应 {idle_time:.0f} 秒）"
                        )
                        workers_to_restart.append(worker_info)

        # 在锁外执行强制重启（重启耗时较长，避免阻塞其他操作）
        # 必须用 _force_restart_worker 而非 _restart_worker：后者在 is_ready
        # 时跳过 stop/start，导致 stale-but-alive worker 被误报为重启成功
        for worker_info in workers_to_restart:
            self._force_restart_worker(worker_info, reason="stale_health_check")

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
