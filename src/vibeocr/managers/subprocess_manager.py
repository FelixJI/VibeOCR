"""子进程管理器

管理 OCR 子进程服务的启动、预加载和生命周期。
"""

import logging
from pathlib import Path
from typing import Optional, Callable, List, TYPE_CHECKING

from PySide6.QtCore import QObject, Signal, QRunnable, QThreadPool

if TYPE_CHECKING:
    from vibeocr.services.ocr_service_subprocess import OCRServiceSubprocess

logger = logging.getLogger(__name__)


class SubprocessStartSignals(QObject):
    """子进程启动信号"""

    started = Signal(bool)  # 启动是否成功
    progress = Signal(str, int)  # (stage, percent)


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
        self.service: Optional["OCRServiceSubprocess"] = None

    def cancel(self) -> None:
        """取消启动任务"""
        self._cancelled = True

    def _update_progress(self, stage: str, percent: int) -> None:
        """更新进度"""
        if not self._cancelled:
            logger.info(f"[OCR 启动] {stage} ({percent}%)")
            self.signals.progress.emit(stage, percent)

    def run(self) -> None:
        """启动子进程服务"""
        if self._cancelled:
            logger.info("[SubprocessManager] 任务已取消")
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
                logger.info("[SubprocessManager] 子进程启动成功")
            else:
                # 取消后关闭服务
                if self.service:
                    self.service.shutdown()
                logger.info("[SubprocessManager] 启动成功但任务已取消，服务已关闭")

        except Exception as e:
            error_msg = str(e)
            logger.error(f"[SubprocessManager] 启动失败: {error_msg}")
            if not self._cancelled:
                self.signals.started.emit(False)


class PreloadSignals(QObject):
    """预加载信号"""

    finished = Signal(dict)  # {pipeline_name: success}


class PreloadTask(QRunnable):
    """预加载任务（在后台线程执行）"""

    def __init__(
        self,
        service: "OCRServiceSubprocess",
        pipelines: List[str],
    ) -> None:
        super().__init__()
        self._service = service
        self._pipelines = pipelines
        self.signals = PreloadSignals()

    def run(self) -> None:
        """预加载管道"""
        try:
            results = self._service.preload_pipelines(self._pipelines)
            success_count = sum(1 for v in results.values() if v)
            logger.info(f"[SubprocessManager] 预加载完成: {success_count}/{len(results)} 个管道")
            self.signals.finished.emit(results)
        except Exception as e:
            logger.error(f"[SubprocessManager] 预加载失败: {e}")
            self.signals.finished.emit({})


class SubprocessManager(QObject):
    """子进程管理器

    管理 OCR 子进程服务的启动、预加载和状态。

    Signals:
        service_ready: (bool) 服务是否就绪
        progress_update: (str, int) (stage, percent) 进度更新
        preload_finished: (dict) 预加载结果
    """

    service_ready = Signal(bool)
    progress_update = Signal(str, int)
    preload_finished = Signal(dict)

    def __init__(
        self,
        project_root: Path,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._project_root = project_root
        self._thread_pool = QThreadPool()
        self._service: Optional["OCRServiceSubprocess"] = None
        self._is_ready = False
        self._start_task: Optional[SubprocessStartTask] = None

    @property
    def service(self) -> Optional["OCRServiceSubprocess"]:
        """获取子进程服务"""
        return self._service

    @property
    def is_ready(self) -> bool:
        """检查服务是否就绪"""
        return self._is_ready and self._service is not None

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
            logger.info("[SubprocessManager] 服务已就绪，跳过启动")
            return

        if self._start_task is not None:
            logger.info("[SubprocessManager] 正在启动中，跳过重复启动")
            return

        logger.info("[SubprocessManager] 正在启动子进程服务...")

        self._start_task = SubprocessStartTask(
            self._project_root,
            use_gpu=use_gpu,
            start_timeout=start_timeout,
        )
        self._start_task.signals.started.connect(self._on_started)
        self._start_task.signals.progress.connect(self.progress_update.emit)
        self._thread_pool.start(self._start_task)

    def _on_started(self, success: bool) -> None:
        """启动完成回调"""
        self._is_ready = success

        if success and self._start_task is not None:
            self._service = self._start_task.service

        self._start_task = None
        self.service_ready.emit(success)

        if success:
            logger.info("[SubprocessManager] 子进程服务已就绪")
        else:
            logger.warning("[SubprocessManager] 子进程服务启动失败")

    def preload_pipelines(self, pipelines: List[str]) -> None:
        """预加载管道

        Args:
            pipelines: 要预加载的管道名称列表
        """
        if not self._service:
            logger.warning("[SubprocessManager] 服务未就绪，无法预加载")
            return

        if not pipelines:
            logger.info("[SubprocessManager] 未配置预加载管道")
            return

        logger.info(f"[SubprocessManager] 开始预加载管道: {pipelines}")

        task = PreloadTask(self._service, pipelines)
        task.signals.finished.connect(self.preload_finished.emit)
        self._thread_pool.start(task)

    def shutdown(self, timeout_ms: int = 3000) -> bool:
        """关闭子进程服务

        Args:
            timeout_ms: 等待超时时间（毫秒）

        Returns:
            是否成功关闭
        """
        logger.info("[SubprocessManager] 正在关闭子进程服务...")

        # 取消正在进行的启动任务并断开信号
        if self._start_task is not None:
            self._start_task.cancel()
            try:
                self._start_task.signals.started.disconnect(self._on_started)
                self._start_task.signals.progress.disconnect(self.progress_update.emit)
            except RuntimeError:
                pass  # 信号已断开

        # 等待线程池完成
        if not self._thread_pool.waitForDone(timeout_ms):
            logger.warning("[SubprocessManager] 线程池未能在超时时间内完成")

        # 关闭服务
        if self._service:
            try:
                self._service.shutdown()
                logger.info("[SubprocessManager] 子进程服务已关闭")
            except Exception as e:
                logger.error(f"[SubprocessManager] 关闭服务失败: {e}")

        self._start_task = None
        self._service = None
        self._is_ready = False
        return True
