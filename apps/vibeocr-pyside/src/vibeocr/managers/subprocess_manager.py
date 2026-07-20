"""子进程管理器

管理 OCR 子进程服务的启动、预加载和生命周期。
"""

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

if TYPE_CHECKING:
    from vibeocr.services.ocr_service_subprocess import OCRServiceSubprocess

logger = logging.getLogger(__name__)


class PreloadCancelled(Exception):
    """预加载任务被协作取消（由 cancel() 触发）。"""


class SubprocessStartSignals(QObject):
    """子进程启动信号"""

    started = Signal(bool)  # 启动是否成功
    progress = Signal(str)  # stage


class SubprocessStartTask(QRunnable):
    """子进程启动任务（在后台线程执行）"""

    def __init__(
        self,
        project_root: Path,
        use_gpu: bool = True,
        start_timeout: float = 120.0,
    ) -> None:
        super().__init__()
        self._project_root = project_root
        self._use_gpu = use_gpu
        self._start_timeout = start_timeout
        self._cancelled = False
        self.signals = SubprocessStartSignals()
        self.service: OCRServiceSubprocess | None = None

    def cancel(self) -> None:
        """取消启动任务"""
        self._cancelled = True

    def _update_progress(self, stage: str) -> None:
        """更新进度

        Args:
            stage: 阶段描述
        """
        if not self._cancelled:
            logger.info(f"[OCR 启动] {stage}")
            self.signals.progress.emit(stage)

    def run(self) -> None:
        """启动子进程服务"""
        if self._cancelled:
            logger.debug("[SubprocessManager] 任务已取消")
            return

        try:
            from vibeocr.services.ocr_service_subprocess import OCRServiceSubprocess

            # 创建并启动子进程服务
            self.service = OCRServiceSubprocess(
                max_workers=1,
                use_gpu=self._use_gpu,
                auto_start=True,
                start_timeout=self._start_timeout,
                start_progress_callback=self._update_progress,
            )

            if not self._cancelled:
                self.signals.started.emit(True)
                logger.debug("[SubprocessManager] 子进程启动成功")
            else:
                # 取消后关闭服务
                if self.service:
                    self.service.shutdown()
                logger.debug("[SubprocessManager] 启动成功但任务已取消，服务已关闭")

        except Exception as e:
            error_msg = str(e)
            logger.error(f"[SubprocessManager] 启动失败: {error_msg}")
            if not self._cancelled:
                self.signals.started.emit(False)


class WorkerHostStartTask(QRunnable):
    """在后台线程建立唯一 WorkerHost 会话并构造前端适配器。"""

    def __init__(self) -> None:
        super().__init__()
        self._cancelled = threading.Event()
        self.signals = SubprocessStartSignals()
        self.service: Any = None

    def cancel(self) -> None:
        self._cancelled.set()

    def run(self) -> None:
        if self._cancelled.is_set():
            return
        self.signals.progress.emit("连接 WorkerHost")
        try:
            from vibeocr.client.batch import BatchBackendAdapter
            from vibeocr.client.session import get_backend_client

            client = get_backend_client()
            if self._cancelled.is_set():
                return
            self.service = BatchBackendAdapter(client)
            self.signals.started.emit(True)
        except Exception:
            logger.exception("[SubprocessManager] WorkerHost 启动失败")
            if not self._cancelled.is_set():
                self.signals.started.emit(False)


class PreloadSignals(QObject):
    """预加载信号"""

    finished = Signal(dict)  # {pipeline_name: success}
    # 逐管道进度：(当前 1-based 序号, 总数, 管道显示名)
    progress = Signal(int, int, str)


class PreloadTask(QRunnable):
    """预加载任务（在后台线程执行）

    在后台线程下发 TTL 并逐个预加载/预热管道，每完成一个上报进度，
    避免长时间无反馈。避免阻塞 GUI 主线程。

    协作取消：通过 ``_cancelled``（threading.Event）实现。``cancel()`` 设置
    事件后，``run()`` 在每个昂贵步骤（TTL 下发、每管道预加载、预热）前检查
    并提前退出。不使用 QThread.terminate()。
    """

    def __init__(
        self,
        service: "OCRServiceSubprocess",
        pipelines: list[str],
        ttl_seconds: int | None = None,
    ) -> None:
        super().__init__()
        self._service: OCRServiceSubprocess | None = service
        self._pipelines = pipelines
        self._ttl_seconds = ttl_seconds
        self.signals = PreloadSignals()
        # 协作取消事件：cancel() 设置后，run() 在每个管道前检查并退出
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        """请求取消：设置取消事件，run() 在下一个检查点退出。"""
        self._cancelled.set()

    def _raise_if_cancelled(self) -> None:
        """检查取消事件，若已取消则抛出 PreloadCancelled 中断 run()。"""
        if self._cancelled.is_set():
            raise PreloadCancelled

    def run(self) -> None:
        """下发 TTL、逐个预加载管道并预热"""
        # 捕获 service 到局部变量：finally 中清零 self._service 后，
        # 局部变量仍持有有效引用，避免延迟回调访问已销毁的 service。
        service = self._service
        if service is None:
            self.signals.finished.emit({"preload": {}, "warmup": {}})
            return
        try:
            # 取消检查点 0：TTL 下发前
            self._raise_if_cancelled()

            # 先下发 TTL（无论是否预加载，TTL 配置都需要同步到 worker）
            if self._ttl_seconds is not None:
                try:
                    service.set_pipeline_ttl(self._ttl_seconds)
                    logger.debug(
                        f"[SubprocessManager] 已下发 TTL={self._ttl_seconds} 到 worker"
                    )
                except Exception as e:
                    logger.warning(f"[SubprocessManager] 下发 TTL 失败: {e}")

            if not self._pipelines:
                logger.debug("[SubprocessManager] 未配置预加载管道，仅完成 TTL 下发")
                self.signals.finished.emit({"preload": {}, "warmup": {}})
                return

            # 逐个预加载：每完成一个上报进度，让状态栏实时反映
            results: dict[str, bool] = {}
            total = len(self._pipelines)
            for i, pipeline_name in enumerate(self._pipelines, 1):
                # 取消检查点：每个管道预加载前
                self._raise_if_cancelled()
                self.signals.progress.emit(i, total, pipeline_name)
                try:
                    single = service.preload_pipelines([pipeline_name])
                    results.update(single)
                except Exception as e:
                    logger.warning(
                        f"[SubprocessManager] 预加载 {pipeline_name} 失败: {e}"
                    )
                    results[pipeline_name] = False

            success_count = sum(1 for v in results.values() if v)
            logger.debug(
                f"[SubprocessManager] 预加载完成: {success_count}/{len(results)} 个管道"
            )

            # 取消检查点：预热前
            self._raise_if_cancelled()

            # 预热：对预加载成功的管道执行一次虚拟识别，触发 CUDA 上下文初始化
            succeeded_pipelines = [name for name, ok in results.items() if ok]
            warmup_results: dict[str, bool] = {}
            if succeeded_pipelines:
                try:
                    warmup_results = service.warmup_pipelines(succeeded_pipelines)
                    warmup_ok = sum(1 for v in warmup_results.values() if v)
                    logger.debug(
                        f"[SubprocessManager] 预热完成: {warmup_ok}/{len(succeeded_pipelines)} 个管道"
                    )
                    # 预热失败的管道仍标记预加载成功（管道已加载，仅 CUDA 初始化未完成）
                except Exception as e:
                    logger.warning(f"[SubprocessManager] 预热失败（预加载仍有效）: {e}")

            # 如实上报两阶段结果：preload（管道加载）+ warmup（CUDA 初始化）。
            self.signals.finished.emit(
                {"preload": results, "warmup": warmup_results}
            )
        except PreloadCancelled:
            logger.debug("[SubprocessManager] 预加载已取消")
            self.signals.finished.emit({"preload": {}, "warmup": {}, "cancelled": True})
        except Exception as e:
            logger.error(f"[SubprocessManager] 预加载失败: {e}")
            self.signals.finished.emit({"preload": {}, "warmup": {}})
        finally:
            # 任务结束后清零 service 引用，避免延迟 signal 访问已销毁的 UI/service
            self._service = None


class SubprocessManager(QObject):
    """子进程管理器

    管理 OCR 子进程服务的启动、预加载和状态。

    Signals:
        service_ready: (bool) 服务是否就绪
        progress_update: (str) (stage) 进度更新
        preload_finished: (dict) 预加载结果
    """

    service_ready = Signal(bool)
    progress_update = Signal(str)
    preload_finished = Signal(dict)
    # 预加载逐管道进度：(当前 1-based 序号, 总数, 管道显示名)
    preload_progress = Signal(int, int, str)
    recognition_queued = Signal(str)  # 识别请求因预加载排队，参数为提示消息

    def __init__(
        self,
        project_root: Path,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._project_root = project_root
        self._thread_pool = QThreadPool()
        self._service: Any = None
        self._is_ready = False
        self._start_task: SubprocessStartTask | WorkerHostStartTask | None = None
        self._preload_task: PreloadTask | None = None
        self._shutdown_requested = False
        # 取消事件:shutdown 时 set,中断 WorkerManager.execute 内的 5 分钟长等待
        import threading

        self._cancel_event = threading.Event()

    @property
    def service(self) -> Any:
        """获取子进程服务"""
        return self._service

    @property
    def is_ready(self) -> bool:
        """检查服务是否就绪"""
        return self._is_ready and self._service is not None

    def attach_service(self, service: object) -> None:
        """Attach the shared WorkerHost client adapter without spawning legacy OCR."""
        self._service = service  # type: ignore[assignment]
        self._is_ready = True

    def start(
        self,
        use_gpu: bool = True,
        start_timeout: float = 120.0,
    ) -> None:
        """启动子进程服务

        Args:
            use_gpu: 是否使用 GPU
            start_timeout: 启动超时时间（秒）
        """
        if self._is_ready:
            logger.debug("[SubprocessManager] 服务已就绪，跳过启动")
            return

        if self._start_task is not None:
            logger.debug("[SubprocessManager] 正在启动中，跳过重复启动")
            return

        logger.debug("[SubprocessManager] 正在启动子进程服务...")
        self._cancel_event.clear()

        # 记录实际并发预算（集中配置，供未来多 worker 扩展参考）
        try:
            from vibeocr.core.concurrency_budget import ConcurrencyBudget

            ConcurrencyBudget.default().log_summary()
        except Exception:
            pass

        self._start_task = SubprocessStartTask(
            self._project_root,
            use_gpu=use_gpu,
            start_timeout=start_timeout,
        )
        self._start_task.signals.started.connect(self._on_started)
        self._start_task.signals.progress.connect(self.progress_update.emit)
        self._thread_pool.start(self._start_task)

    def start_worker_host(self) -> None:
        """在管理器线程池中启动当前生产 WorkerHost，避免阻塞 GUI。"""
        if self._is_ready:
            logger.debug("[SubprocessManager] WorkerHost 已就绪，跳过启动")
            return
        if self._start_task is not None:
            logger.debug("[SubprocessManager] WorkerHost 正在启动，跳过重复启动")
            return

        self._cancel_event.clear()
        self._start_task = WorkerHostStartTask()
        self._start_task.signals.started.connect(self._on_started)
        self._start_task.signals.progress.connect(self.progress_update.emit)
        self._thread_pool.start(self._start_task)

    def _on_started(self, success: bool) -> None:
        """启动完成回调"""
        # shutdown 会断开信号并清空任务；Windows/Qt 仍可能投递已经排队的 started。
        # 该迟到信号不属于任何活动启动，必须忽略，不能重新挂接服务。
        if self._start_task is None:
            logger.debug("[SubprocessManager] 忽略已取消启动任务的迟到结果")
            return
        self._is_ready = success

        if success and self._start_task is not None:
            self._service = self._start_task.service
            if self._service:
                self._service.set_task_queued_callback(
                    lambda: self.recognition_queued.emit(
                        "正在预加载模型，识别请求将在预加载完成后自动执行..."
                    )
                )
                # 下发取消事件,使 shutdown 时能中断 execute 内的长等待
                self._service.set_cancel_event(self._cancel_event)

        self._start_task = None
        self.service_ready.emit(success)

        if success:
            logger.info("[SubprocessManager] 子进程服务已就绪")
        else:
            logger.warning("[SubprocessManager] 子进程服务启动失败")

    def preload_pipelines(
        self,
        pipelines: list[str],
        ttl_seconds: int | None = None,
    ) -> bool:
        """预加载管道（在后台线程执行，同时下发 TTL）

        Args:
            pipelines: 要预加载的管道名称列表（可为空，此时仅下发 TTL）
            ttl_seconds: 可选的 TTL 秒数，下发到 worker
        """
        if not self._service:
            logger.warning("[SubprocessManager] 服务未就绪，无法预加载")
            return False

        if self._preload_task is not None:
            logger.info("[SubprocessManager] 已有预加载任务进行中，忽略重复请求")
            return False

        if not pipelines and ttl_seconds is None:
            logger.debug("[SubprocessManager] 无预加载管道且无需下发 TTL")
            return False

        logger.debug(
            f"[SubprocessManager] 开始预加载管道: {pipelines}"
            + (f"，下发 TTL={ttl_seconds}" if ttl_seconds is not None else "")
        )

        self._preload_task = PreloadTask(
            self._service, pipelines, ttl_seconds=ttl_seconds
        )
        self._preload_task.signals.finished.connect(self._on_preload_done)
        self._preload_task.signals.progress.connect(self.preload_progress.emit)
        self._thread_pool.start(self._preload_task)
        return True

    def invalidate_worker_host(self) -> None:
        """安装维护前立即使旧服务失效；真正关闭在安装线程中完成。"""
        self._cancel_event.set()
        if self._start_task is not None:
            self._start_task.cancel()
            try:
                self._start_task.signals.started.disconnect(self._on_started)
            except (RuntimeError, TypeError):
                pass
            self._start_task = None
        if self._preload_task is not None:
            self._preload_task.cancel()
        self._service = None
        self._is_ready = False

    def _on_preload_done(self, results: dict) -> None:
        """预加载完成，清理引用并转发信号"""
        self._preload_task = None
        self.preload_finished.emit(results)

    def request_preload_shutdown(self) -> None:
        """Cancel only the settings-triggered preload, without stopping service."""
        task = self._preload_task
        if task is not None:
            task.cancel()

    def is_preload_drained(self) -> bool:
        """Zero-wait probe for a preload owned by the manager thread pool."""
        return self._preload_task is None or self._thread_pool.activeThreadCount() == 0

    def request_shutdown(self) -> None:
        """GUI 阶段只请求取消 Qt 任务；不 wait、不关闭外部 service。"""
        if self._shutdown_requested:
            return
        self._shutdown_requested = True
        self._cancel_event.set()
        if self._start_task is not None:
            self._start_task.cancel()
            try:
                self._start_task.signals.started.disconnect(self._on_started)
                self._start_task.signals.progress.disconnect(self.progress_update.emit)
            except RuntimeError:
                pass
        if self._preload_task is not None:
            self._preload_task.cancel()
            try:
                self._preload_task.signals.finished.disconnect(self._on_preload_done)
                self._preload_task.signals.progress.disconnect(
                    self.preload_progress.emit
                )
            except RuntimeError:
                pass

    def is_drained(self) -> bool:
        """纯状态探测：Qt 线程池无 native runnable 才可释放 owner。"""
        return self._thread_pool.activeThreadCount() == 0

    def take_shutdown_callable(self):
        """GUI 线程 detach 普通 service，返回可在非 GUI 线程执行的 callable。"""
        if not self.is_drained():
            raise RuntimeError("subprocess Qt tasks are still running")
        # request_shutdown 会断开 started 信号。若启动任务恰好在断开后完成，
        # _on_started 不会把 task.service 搬到 _service；线程池虽已 drained，
        # 但服务仍由已完成的 QRunnable 持有。detach 时必须覆盖这个竞态窗口。
        start_task = self._start_task
        service = self._service or (
            getattr(start_task, "service", None) if start_task is not None else None
        )
        self._start_task = None
        self._preload_task = None
        self._service = None
        self._is_ready = False
        return getattr(service, "shutdown", None) if service is not None else None

    def shutdown(self, timeout_ms: int = 3000) -> bool:
        """关闭子进程服务

        Args:
            timeout_ms: 等待超时时间（毫秒）

        Returns:
            是否成功关闭
        """
        logger.debug("[SubprocessManager] 正在关闭子进程服务...")

        # 立即触发取消事件,中断 WorkerManager.execute 内正在进行的 5 分钟长等待
        self._cancel_event.set()

        # 取消正在进行的启动任务并断开信号
        if self._start_task is not None:
            self._start_task.cancel()
            try:
                self._start_task.signals.started.disconnect(self._on_started)
                self._start_task.signals.progress.disconnect(self.progress_update.emit)
            except RuntimeError:
                pass  # 信号已断开

        # 取消正在进行的预加载任务（协作取消：设置 _cancelled 事件，
        # run() 在下一个检查点退出；同时断开 signal 避免迟到回调）
        if self._preload_task is not None:
            self._preload_task.cancel()
            try:
                self._preload_task.signals.finished.disconnect(self._on_preload_done)
                self._preload_task.signals.progress.disconnect(
                    self.preload_progress.emit
                )
            except RuntimeError:
                pass

        # 等待线程池完成
        timed_out = not self._thread_pool.waitForDone(timeout_ms)
        if timed_out:
            logger.warning("[SubprocessManager] 线程池未能在超时时间内完成")

        # 关闭服务
        if self._service:
            try:
                self._service.shutdown()
                logger.debug("[SubprocessManager] 子进程服务已关闭")
            except Exception as e:
                logger.error(f"[SubprocessManager] 关闭服务失败: {e}")
                timed_out = True

        self._start_task = None
        self._preload_task = None
        self._service = None
        self._is_ready = False
        return not timed_out
