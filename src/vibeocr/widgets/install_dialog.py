"""安装进度对话框"""

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


class InstallWorker(QThread):
    """安装工作线程"""

    progress = Signal(str, str)  # (stage, message)
    finished = Signal(bool, str)  # (success, message)

    def __init__(self, project_root: Path, force_backend: str | None = None) -> None:
        super().__init__()
        self._project_root = project_root
        self._force_backend = force_backend

    def run(self) -> None:
        """执行安装"""
        try:
            # 1. 检测网络环境
            self.progress.emit("网络检测", "正在检测网络环境...")
            detector = NetworkDetector(self._project_root)
            network_type = detector.network_type

            # 2. 决定后端：force_backend 指定时跳过自动检测
            if self._force_backend:
                has_gpu = self._force_backend == "gpu"
                cuda_version = None
                if has_gpu:
                    # GPU 需要 cuda_version（cu-tag）选 paddle index
                    self.progress.emit("硬件检测", "正在检测 GPU CUDA 版本...")
                    _detected, cuda_version = env_manager.detect_gpu()
            else:
                self.progress.emit("硬件检测", "正在检测GPU...")
                has_gpu, cuda_version = env_manager.detect_gpu()

            # 3. 检查嵌入式Python是否存在
            python_exe = env_manager.get_embedded_python_executable(self._project_root)
            if not python_exe.exists():
                # 需要先安装嵌入式Python
                self.progress.emit("环境安装", "正在安装嵌入式Python...")
                success, msg = env_manager.install_embedded_python(
                    self._project_root, network_type
                )
                if not success:
                    self.finished.emit(False, f"安装嵌入式Python失败:\n{msg}")
                    return

            # 4. 安装OCR依赖
            self.progress.emit("依赖安装", "正在安装OCR依赖...")
            success, msg = env_manager.install_embedded_dependencies(
                self._project_root,
                network_type,
                has_gpu,
                cuda_version,
                progress_callback=lambda stage, message: self.progress.emit(
                    stage, message
                ),
                force_backend=self._force_backend,
            )

            self.finished.emit(success, msg)

        except Exception as e:
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

    @Slot(str, str)
    def _on_progress(self, stage: str, message: str) -> None:
        """进度更新"""
        self._stage_label.setText(f"[{stage}] {message}")
        self._log(f"[{stage}] {message}")

    @Slot(bool, str)
    def _on_finished(self, success: bool, message: str) -> None:
        """安装完成"""
        self._progress_bar.setVisible(False)

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
        """关闭事件"""
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait()
        event.accept()
