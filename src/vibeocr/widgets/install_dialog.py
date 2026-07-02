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

logger = logging.getLogger(__name__)


class InstallWorker(QThread):
    """安装工作线程"""

    progress = Signal(str, str)  # (stage, message)
    finished = Signal(bool, str)  # (success, message)

    def __init__(
        self,
        project_root: Path,
        force_backend: str | None = None,
        reinstall_python: bool = False,
        missing_only: bool = False,
    ) -> None:
        super().__init__()
        self._project_root = project_root
        self._force_backend = force_backend
        self._reinstall_python = reinstall_python
        self._missing_only = missing_only
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
                    self.finished.emit(False, f"重装 Python 运行时失败:\n{msg}")
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
                        self.finished.emit(False, f"安装嵌入式Python失败:\n{msg}")
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
                self.finished.emit(success, msg)
                return

            self.finished.emit(True, msg)

        except Exception as e:
            logger.error("安装异常: %s", e)
            self.finished.emit(False, f"安装异常: {e}")


class InstallDialog(QDialog):
    """安装进度对话框"""

    install_succeeded = Signal()

    def __init__(self, project_root: Path, parent=None) -> None:
        super().__init__(parent)
        self._project_root = project_root
        self._setup_ui()
        self._worker: InstallWorker | None = None

    def _setup_ui(self) -> None:
        """设置UI"""
        self.setWindowTitle("安装OCR依赖")
        self.setMinimumSize(500, 400)
        self.setModal(True)

        layout = QVBoxLayout(self)

        # 标题
        self._title_label = QLabel("正在安装OCR依赖...")
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
        self._log("开始安装OCR依赖...")

        self._worker = InstallWorker(self._project_root)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
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
            self._stage_label.setText("OCR依赖安装完成")
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
        """关闭事件：协作式取消安装，绝不强杀线程。

        旧实现用 QThread.terminate() 会制造孤儿 pip 子进程并使 Python 层
        timeout 失效（详见 vibeocr.log 分析）。改为：set cancel_event + kill
        当前子进程 + 等待 worker 自然结束（最多 5s），保证子进程被回收。
        """
        if self._worker and self._worker.isRunning():
            self._worker.request_cancel()
            # 等待 worker 自然结束（cancel 后通常数秒内退出）；
            # 超时则强制接受关闭（worker 作为 daemon 线程不会阻止进程退出，
            # 且 aboutToQuit 会兜底清理）。
            self._worker.wait(5000)
        event.accept()
