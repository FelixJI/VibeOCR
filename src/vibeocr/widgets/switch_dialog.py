"""后端切换进度对话框（重启时消费 pending_backend）"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QThread, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from vibeocr import env_manager

if TYPE_CHECKING:
    from pathlib import Path


class SwitchWorker(QThread):
    """后端切换工作线程"""

    progress = Signal(str, str)  # (stage, message)
    finished = Signal(bool, str)  # (success, message)

    def __init__(self, project_root: Path, target: str) -> None:
        super().__init__()
        self._project_root = project_root
        self._target = target

    def run(self) -> None:
        try:
            self.progress.emit("网络检测", "正在检测网络环境...")
            from vibeocr.network_detector import NetworkDetector

            network_type = NetworkDetector(self._project_root).network_type

            self.progress.emit("后端切换", f"正在切换到 {self._target.upper()}...")
            success, msg = env_manager.switch_paddle_backend(
                self._project_root,
                self._target,
                network_type,
                progress_callback=lambda stage, message: self.progress.emit(
                    stage, message
                ),
            )
            self.finished.emit(success, msg)
        except Exception as e:
            self.finished.emit(False, f"切换异常: {e}")


class SwitchDialog(QDialog):
    """后端切换进度对话框（重启时消费 pending_backend）"""

    switch_succeeded = Signal()

    def __init__(
        self, project_root: Path, target: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._project_root = project_root
        self._target = target
        self._worker: SwitchWorker | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        name = "GPU" if self._target == "gpu" else "CPU"
        self.setWindowTitle(f"切换到 {name} 后端")
        self.setMinimumSize(500, 400)
        self.setModal(True)

        layout = QVBoxLayout(self)

        self._title_label = QLabel(f"正在切换到 {name} 后端...")
        layout.addWidget(self._title_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)  # 不确定进度
        layout.addWidget(self._progress_bar)

        self._stage_label = QLabel("准备中...")
        layout.addWidget(self._stage_label)

        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        layout.addWidget(self._log_text)

        self._close_button = QPushButton("关闭")
        self._close_button.clicked.connect(self.accept)
        self._close_button.setVisible(False)
        layout.addWidget(self._close_button)

    def showEvent(self, event) -> None:
        """显示事件 - 开始切换"""
        super().showEvent(event)
        if not self._worker:
            self._start()

    def _start(self) -> None:
        self._log("开始切换后端...")
        self._worker = SwitchWorker(self._project_root, self._target)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    @Slot(str, str)
    def _on_progress(self, stage: str, message: str) -> None:
        self._stage_label.setText(f"[{stage}] {message}")
        self._log(f"[{stage}] {message}")

    @Slot(bool, str)
    def _on_finished(self, success: bool, message: str) -> None:
        self._progress_bar.setVisible(False)
        if success:
            self._title_label.setText("切换成功!")
            self._stage_label.setText("后端已切换，即将启动 OCR 服务")
            self._log(f"\n{message}")
            self._close_button.setVisible(True)
            self.switch_succeeded.emit()
            self.done(1)
        else:
            self._title_label.setText("切换失败")
            self._stage_label.setText("切换过程中出现错误")
            self._log(f"\n{message}")
            self._close_button.setVisible(True)
            self._close_button.setText("关闭")
            self.done(0)

    def _log(self, message: str) -> None:
        self._log_text.append(message)
        scrollbar = self._log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def closeEvent(self, event) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait()
        event.accept()
