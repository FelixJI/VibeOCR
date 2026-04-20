# src/vibeocr/widgets/model_download_dialog.py
"""模型下载进度对话框

在安装依赖完成后或用户手动触发时显示，展示 PaddleX 和 MinerU 模型下载进度。
"""

import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from vibeocr.services.model_download_service import (
    DownloadStatus,
    ModelDownloadService,
)


class ModelDownloadWorker(QThread):
    """模型下载工作线程"""

    progress = Signal(str, str)  # (stage, message)
    status_changed = Signal(str, str)  # (pipeline_name, status)
    finished = Signal(dict)  # {pipeline_name: DownloadStatus}

    def __init__(self, project_root: Path, parent=None) -> None:
        super().__init__(parent)
        self._project_root = project_root
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        service = ModelDownloadService(self._project_root)

        def on_progress(stage: str, message: str) -> None:
            self.progress.emit(stage, message)
            for name, status in service.get_status().items():
                self.status_changed.emit(name, status.value)

        results = service.download_all(
            progress_callback=on_progress,
            cancel_event=self._cancel_event,
        )
        self.finished.emit({k: v.value for k, v in results.items()})


class ModelDownloadDialog(QDialog):
    """模型下载进度对话框

    显示各管道的下载状态，支持跳过和取消。
    """

    def __init__(self, project_root: Path, parent=None) -> None:
        super().__init__(parent)
        self._project_root = project_root
        self._worker: ModelDownloadWorker | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setWindowTitle("模型下载")
        self.setMinimumSize(500, 400)
        self.setModal(True)

        layout = QVBoxLayout(self)

        # 标题
        self._title_label = QLabel("正在下载模型文件...")
        layout.addWidget(self._title_label)

        # 进度条
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)  # 不确定进度
        layout.addWidget(self._progress_bar)

        # 各管道状态
        self._status_widget = QWidget()
        self._status_layout = QVBoxLayout(self._status_widget)
        self._status_layout.setContentsMargins(0, 0, 0, 0)
        self._status_labels: dict[str, QLabel] = {}

        for name in ["OCR", "table_recognition", "formula_recognition", "MinerU"]:
            label = QLabel(f"  {name}: 等待中")
            self._status_labels[name] = label
            self._status_layout.addWidget(label)

        layout.addWidget(self._status_widget)

        # 日志区域
        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        layout.addWidget(self._log_text)

        # 按钮
        button_layout = QHBoxLayout()

        self._skip_button = QPushButton("跳过")
        self._skip_button.clicked.connect(self._on_skip)
        button_layout.addWidget(self._skip_button)

        self._cancel_button = QPushButton("取消下载")
        self._cancel_button.clicked.connect(self._on_cancel)
        button_layout.addWidget(self._cancel_button)

        self._close_button = QPushButton("关闭")
        self._close_button.clicked.connect(self.accept)
        self._close_button.setVisible(False)
        button_layout.addWidget(self._close_button)

        layout.addLayout(button_layout)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._worker:
            self._start_download()

    def _start_download(self) -> None:
        self._log("开始下载模型...")

        self._worker = ModelDownloadWorker(self._project_root, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.status_changed.connect(self._on_status_changed)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    @Slot(str, str)
    def _on_progress(self, stage: str, message: str) -> None:
        self._log(f"[{stage}] {message}")

    @Slot(str, str)
    def _on_status_changed(self, pipeline_name: str, status: str) -> None:
        status_display = {
            "pending": "等待中",
            "downloading": "下载中...",
            "completed": "已完成",
            "failed": "失败",
            "skipped": "已跳过",
        }
        display_text = status_display.get(status, status)
        label = self._status_labels.get(pipeline_name)
        if label:
            label.setText(f"  {pipeline_name}: {display_text}")

    @Slot(dict)
    def _on_finished(self, results: dict) -> None:
        self._progress_bar.setVisible(False)
        self._cancel_button.setVisible(False)

        success_count = sum(1 for v in results.values() if v == "completed")
        total_count = len(results)

        if success_count == total_count:
            self._title_label.setText("模型下载完成!")
        else:
            self._title_label.setText(
                f"模型下载结束 ({success_count}/{total_count} 成功)"
            )

        self._log(f"\n下载完成: {success_count}/{total_count} 成功")
        self._close_button.setVisible(True)
        self._skip_button.setVisible(False)

    def _on_skip(self) -> None:
        self._log("用户跳过模型下载，后台继续下载...")
        self.accept()

    def _on_cancel(self) -> None:
        if self._worker:
            self._worker.cancel()
        self._log("正在取消下载...")
        self._cancel_button.setEnabled(False)

    def _log(self, message: str) -> None:
        self._log_text.append(message)
        scrollbar = self._log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def closeEvent(self, event) -> None:
        # 跳过或关闭对话框时不取消后台下载，让 Worker 自然完成
        event.accept()
