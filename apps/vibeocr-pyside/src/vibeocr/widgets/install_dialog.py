"""安装进度对话框"""

import logging
import subprocess
import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from vibeocr import env_manager
from vibeocr.network_detector import NetworkDetector
from vibeocr.utils.dialog_workers import track_dialog_worker

logger = logging.getLogger(__name__)


class InstallWorker(QThread):
    """安装工作线程"""

    progress = Signal(str, str)  # (stage, message)
    completed = Signal(bool, str)  # (success, message)

    def __init__(
        self,
        project_root: Path,
        force_backend: str | None = None,
        reinstall_python: bool = False,
        missing_only: bool = False,
        single_pkg: str | None = None,
        packages: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._project_root = project_root
        self._force_backend = force_backend
        self._reinstall_python = reinstall_python
        self._missing_only = missing_only
        # 单包重装模式：只装指定的一个包（设置页依赖表格"重装"按钮）。
        # 与 missing_only 互斥；指定时跳过 Python 运行时/GPU 检测，直接单包安装。
        self._single_pkg = single_pkg
        # 批量重装模式：一次装多个指定包（设置页依赖树"重装选中项"）。
        # 与 single_pkg / missing_only 互斥；跳过 Python 运行时/GPU 检测，直接批量装。
        self._packages = packages
        # 协作式取消机制：替代危险的 QThread.terminate()。
        # cancel_event 被 set 后，env_manager._run_pip 会 kill 当前 pip 子进程并抛
        # InstallCancelled；for 循环在每个包安装前检查 event 快速中止。
        self._cancel_event = threading.Event()
        # 当前正在运行的子进程句柄（由 env_manager._run_pip 的 on_proc 回调设置），
        # request_cancel 时立即 kill，避免孤儿 pip 进程。
        self._current_proc: subprocess.Popen | None = None
        self._proc_lock = threading.Lock()

    def _on_proc(self, proc: subprocess.Popen) -> None:
        """env_manager._run_pip 的 on_proc 回调：记录当前子进程句柄。"""
        with self._proc_lock:
            self._current_proc = proc

    def request_cancel(self) -> None:
        """协作式取消安装（线程安全）。

        1. set cancel_event → env_manager 的 for 循环/ _run_pip 检测到后中止；
        2. 立即 kill 当前 pip 子进程（若有），避免它成为孤儿继续后台运行。
        调用方（对话框 closeEvent / 取消按钮）应在 request_cancel 后用 wait()
        等待 worker 自然结束，而非用 terminate() 强杀。
        """
        self._cancel_event.set()
        with self._proc_lock:
            proc = self._current_proc
        if proc is not None:
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def _emit_progress(self, stage: str, message: str) -> None:
        """发送进度信号并同步写入 logger（确保 UI 进度落盘到 vibeocr.log）。

        无论是否连接 InstallDialog，进度都落盘，便于无界面场景（如测试/后台）排查。
        """
        logger.info("[%s] %s", stage, message)
        self.progress.emit(stage, message)

    def run(self) -> None:
        """执行安装"""
        try:
            # 运行时文件可能正被 WorkerHost 占用。关闭动作放在安装线程中，
            # 避免 GUI 卡顿，也保证重装 Python 不会残留被锁定的旧文件。
            self._emit_progress("运行时维护", "正在断开 OCR 运行时...")
            try:
                from vibeocr.client.session import shutdown_backend_client

                shutdown_backend_client()
            except Exception as exc:
                logger.warning("关闭旧 WorkerHost 失败，将继续安装: %s", exc)

            # 单包重装模式：跳过网络/GPU/Python 检测，直接装指定包。
            # 复用 install_single_dependency 的取消/超时/进度机制。
            if self._single_pkg is not None:
                self._emit_progress(
                    "依赖安装", f"正在单独安装 {self._single_pkg}..."
                )
                # 单包重装仍需网络源选镜像，做一次轻量网络检测。
                detector = NetworkDetector(self._project_root)
                network_type = detector.network_type
                success, msg = env_manager.install_single_dependency(
                    self._project_root,
                    self._single_pkg,
                    network_type,
                    progress_callback=self._emit_progress,
                    cancel_event=self._cancel_event,
                    on_proc=self._on_proc,
                )
                self.completed.emit(success, msg)
                return

            # 批量重装模式：跳过网络/GPU/Python 检测，直接批量装指定包。
            # 复用 install_dependencies_batch 的取消/超时/进度机制。
            if self._packages is not None:
                n = len(self._packages)
                self._emit_progress(
                    "批量重装", f"正在批量重装 {n} 个依赖包..."
                )
                # 批量重装仍需网络源选镜像，做一次轻量网络检测。
                detector = NetworkDetector(self._project_root)
                network_type = detector.network_type
                success, msg = env_manager.install_dependencies_batch(
                    self._project_root,
                    self._packages,
                    network_type,
                    progress_callback=self._emit_progress,
                    cancel_event=self._cancel_event,
                    on_proc=self._on_proc,
                )
                self.completed.emit(success, msg)
                return

            # 1. 检测网络环境
            self._emit_progress("网络检测", "正在检测网络环境...")
            detector = NetworkDetector(self._project_root)
            network_type = detector.network_type

            # 2. 决定后端：force_backend 指定时跳过自动检测
            if self._force_backend:
                has_gpu = self._force_backend == "gpu"
                cuda_version = None
                if has_gpu:
                    # GPU 需要 cuda_version（cu-tag）选 paddle index
                    self._emit_progress("硬件检测", "正在检测 GPU CUDA 版本...")
                    _detected, cuda_version = env_manager.detect_gpu()
            else:
                self._emit_progress("硬件检测", "正在检测GPU...")
                has_gpu, cuda_version = env_manager.detect_gpu()

            # 3. Python 运行时
            if self._reinstall_python:
                # 重装模式：强制删除 python/ 后重下（连带丢失依赖，后续装依赖）
                self._emit_progress(
                    "环境安装", "正在重装 Python 运行时（删除 python/ 后重新下载）..."
                )
                success, msg = env_manager.reinstall_embedded_python(
                    self._project_root,
                    network_type,
                    progress_callback=self._emit_progress,
                )
                if not success:
                    self.completed.emit(False, f"重装 Python 运行时失败:\n{msg}")
                    return
            else:
                # 常规模式：检查嵌入式Python是否存在，不存在才装
                python_exe = env_manager.get_embedded_python_executable(
                    self._project_root
                )
                if not python_exe.exists():
                    self._emit_progress("环境安装", "正在安装嵌入式Python...")
                    success, msg = env_manager.install_embedded_python(
                        self._project_root, network_type
                    )
                    if not success:
                        self.completed.emit(False, f"安装嵌入式Python失败:\n{msg}")
                        return

            # 4. 安装OCR依赖（增量或全量）
            install_fn = (
                env_manager.install_missing_dependencies
                if self._missing_only
                else env_manager.install_embedded_dependencies
            )
            action = "补装缺失" if self._missing_only else "安装"
            self._emit_progress("依赖安装", f"正在{action}OCR依赖...")
            success, msg = install_fn(
                self._project_root,
                network_type,
                has_gpu,
                cuda_version,
                progress_callback=self._emit_progress,
                force_backend=self._force_backend,
                cancel_event=self._cancel_event,
                on_proc=self._on_proc,
            )
            if not success:
                self.completed.emit(success, msg)
                return

            self.completed.emit(True, msg)

        except Exception as e:
            logger.error("安装异常: %s", e)
            self.completed.emit(False, f"安装异常: {e}")


class InstallDialog(QDialog):
    """安装进度对话框"""

    install_succeeded = Signal()

    def __init__(
        self,
        project_root: Path,
        parent=None,
        missing_only: bool = False,
        force_backend: str | None = None,
        single_pkg: str | None = None,
        packages: list[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._project_root = project_root
        self._missing_only = missing_only
        self._force_backend = force_backend
        self._single_pkg = single_pkg
        self._packages = packages
        self._setup_ui()
        self._worker: InstallWorker | None = None

    def _setup_ui(self) -> None:
        """设置UI"""
        if self._single_pkg:
            self.setWindowTitle(f"重装依赖：{self._single_pkg}")
            self._title_text = f"正在重装 {self._single_pkg}..."
        elif self._packages is not None:
            n = len(self._packages)
            self.setWindowTitle(f"批量重装 {n} 个依赖包")
            self._title_text = f"正在批量重装 {n} 个依赖包..."
        else:
            self.setWindowTitle("安装OCR依赖")
            self._title_text = "正在安装OCR依赖..."
        self.setMinimumSize(500, 400)
        self.setModal(True)

        layout = QVBoxLayout(self)

        # 标题
        self._title_label = QLabel(self._title_text)
        layout.addWidget(self._title_label)

        # 进度条
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)  # 不确定进度
        layout.addWidget(self._progress_bar)

        # 当前阶段
        self._stage_label = QLabel("准备中...")
        layout.addWidget(self._stage_label)

        # 日志输出
        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        layout.addWidget(self._log_text)

        # 取消按钮（安装进行中显示，触发协作式取消）
        self._cancel_button = QPushButton("取消安装")
        self._cancel_button.clicked.connect(self._on_cancel_clicked)
        self._cancel_button.setVisible(False)
        layout.addWidget(self._cancel_button)

        # 关闭按钮（初始隐藏）
        self._close_button = QPushButton("关闭")
        self._close_button.clicked.connect(self.accept)
        self._close_button.setVisible(False)
        layout.addWidget(self._close_button)

    def showEvent(self, event) -> None:
        """显示事件 - 开始安装"""
        super().showEvent(event)
        if not self._worker:
            self._start_install()

    def _start_install(self) -> None:
        """开始安装"""
        if self._single_pkg:
            self._log(f"开始重装 {self._single_pkg}...")
        elif self._packages is not None:
            self._log(f"开始批量重装 {len(self._packages)} 个依赖包...")
        else:
            self._log("开始安装OCR依赖...")

        self._worker = InstallWorker(
            self._project_root,
            missing_only=self._missing_only,
            force_backend=self._force_backend,
            single_pkg=self._single_pkg,
            packages=self._packages,
        )
        track_dialog_worker(self._worker)
        self._worker.progress.connect(self._on_progress)
        self._worker.completed.connect(self._on_finished)
        self._worker.start()
        # 安装开始后显示取消按钮
        self._cancel_button.setVisible(True)

    def _on_cancel_clicked(self) -> None:
        """取消按钮：确认后协作式取消安装（不杀线程，只 kill 子进程 + 设标志）。"""
        from PySide6.QtWidgets import QMessageBox

        reply = QMessageBox.question(
            self,
            "取消安装",
            "确定要取消安装吗？\n已下载的内容会保留，下次可继续补装缺失依赖。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if self._worker and self._worker.isRunning():
            self._cancel_button.setEnabled(False)
            self._cancel_button.setText("正在取消...")
            self._log("用户取消安装，正在停止当前任务（可能需要数秒）...")
            self._worker.request_cancel()
            # 不阻塞 UI 事件循环：让 worker 自然结束，finished 信号会驱动后续 UI。
        else:
            self._cancel_button.setVisible(False)

    @Slot(str, str)
    def _on_progress(self, stage: str, message: str) -> None:
        """进度更新（日志已在 InstallWorker._emit_progress 落盘，此处仅更新 UI）"""
        self._stage_label.setText(f"[{stage}] {message}")
        self._log(f"[{stage}] {message}")

    @Slot(bool, str)
    def _on_finished(self, success: bool, message: str) -> None:
        """安装完成"""
        self._progress_bar.setVisible(False)
        self._cancel_button.setVisible(False)

        if success:
            self._title_label.setText("安装成功!")
            # 单包/批量重装时 message 是具体结果（如"scipy 安装成功"/"已重装 3 个依赖包"），
            # 优先用它，避免笼统的"OCR依赖安装完成"（用户报告"单包却提示全部安装完毕"）。
            self._stage_label.setText(message or "OCR依赖安装完成")
            self._log(f"\n安装成功: {message}")
            self._close_button.setVisible(True)
            # 设置结果为成功
            self.install_succeeded.emit()
            self.done(1)
        else:
            self._title_label.setText("安装失败")
            self._stage_label.setText("安装过程中出现错误")
            self._log(f"\n安装失败: {message}")
            self._close_button.setVisible(True)
            self._close_button.setText("关闭")
            # 设置结果为失败
            self.done(0)

    def _log(self, message: str) -> None:
        """添加日志"""
        self._log_text.append(message)
        # 滚动到底部
        scrollbar = self._log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def closeEvent(self, event) -> None:
        """Request cancellation and return immediately; registry owns the worker."""
        if self._worker and self._worker.isRunning():
            self._worker.request_cancel()
        event.accept()

    def request_shutdown(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.request_cancel()
        self.close()
