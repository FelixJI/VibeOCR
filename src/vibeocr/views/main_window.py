"""Main window view logic"""

import io
from pathlib import Path

from PIL import Image
from PySide6.QtWidgets import (
    QMainWindow,
    QFileDialog,
    QMessageBox,
    QApplication,
)
from PySide6.QtCore import Slot, QThreadPool, QRunnable, Signal, QObject, QTimer, QBuffer
from PySide6.QtGui import QPixmap
from PySide6.QtUiTools import QUiLoader

from vibeocr.widgets.preview_widget import PreviewWidget
from vibeocr.widgets.screenshot_widget import ScreenshotWidget
from vibeocr.services.ocr_service import OCRService


class OCRSignals(QObject):
    """OCR任务信号（用于线程安全通信）"""

    finished = Signal(str)  # 识别完成
    error = Signal(str)  # 识别失败


class OCRTask(QRunnable):
    """OCR识别任务（在后台线程执行）"""

    def __init__(self, image_data: bytes) -> None:
        super().__init__()
        self._image_data = image_data
        self.signals = OCRSignals()

    def run(self) -> None:
        """执行OCR识别"""
        try:
            # 从字节数据创建 PIL Image
            buffer = io.BytesIO(self._image_data)
            pil_image = Image.open(buffer)

            # 执行OCR
            ocr = OCRService()
            result = ocr.recognize(pil_image)
            self.signals.finished.emit(result)
        except Exception as e:
            self.signals.error.emit(str(e))


class MainWindow(QMainWindow):
    """主窗口"""

    def __init__(self) -> None:
        super().__init__()
        self._setup_ui()
        self._connect_signals()
        self._thread_pool = QThreadPool()

    def _setup_ui(self) -> None:
        """设置UI"""
        ui_path = Path(__file__).parent.parent / "ui" / "main_window.ui"
        if not ui_path.exists():
            raise FileNotFoundError(f"UI file not found: {ui_path}")

        loader = QUiLoader()
        # 注册自定义控件
        loader.registerCustomWidget(PreviewWidget)
        self._ui = loader.load(str(ui_path), self)
        self.setCentralWidget(self._ui)

        # 设置窗口属性
        self.setWindowTitle("VibeOCR")
        self.resize(900, 600)

        # 创建截图组件
        self._screenshot_widget = ScreenshotWidget()

    def _connect_signals(self) -> None:
        """连接信号槽"""
        # 菜单动作
        self._ui.actionOpenImage.triggered.connect(self._on_open_image)
        self._ui.actionScreenshot.triggered.connect(self._on_screenshot)
        self._ui.actionExit.triggered.connect(self.close)
        self._ui.actionAbout.triggered.connect(self._on_about)

        # 截图组件
        self._screenshot_widget.captured.connect(self._on_screenshot_captured)

        # 预览组件
        self._ui.previewWidget.screenshot_requested.connect(self._on_screenshot)

        # 复制按钮
        self._ui.btnCopy.clicked.connect(self._on_copy_result)

    @Slot()
    def _on_open_image(self) -> None:
        """打开图片文件"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "打开图片",
            "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif);;所有文件 (*)",
        )
        if file_path:
            pixmap = QPixmap(file_path)
            if not pixmap.isNull():
                self._ui.previewWidget.set_pixmap(pixmap)
                self._run_ocr(pixmap)

    @Slot()
    def _on_screenshot(self) -> None:
        """开始截图"""
        self.showMinimized()
        # 延迟启动截图，让窗口有时间最小化
        QTimer.singleShot(200, self._screenshot_widget.start_capture)

    @Slot(QPixmap)
    def _on_screenshot_captured(self, pixmap: QPixmap) -> None:
        """截图完成"""
        self.showNormal()
        self.activateWindow()
        if not pixmap.isNull():
            self._ui.previewWidget.set_pixmap(pixmap)
            self._run_ocr(pixmap)

    def _run_ocr(self, pixmap: QPixmap) -> None:
        """执行OCR识别"""
        self._ui.textResult.clear()
        self._ui.textResult.setPlaceholderText("正在识别...")
        self._ui.statusbar.showMessage("正在识别...")

        # 在主线程中将 QPixmap 转换为字节（线程安全）
        buffer = QBuffer()
        buffer.open(QBuffer.OpenModeFlag.ReadWrite)
        pixmap.save(buffer, "PNG")
        image_data = buffer.data().data()
        buffer.close()

        task = OCRTask(image_data)
        task.signals.finished.connect(self._on_ocr_finished)
        task.signals.error.connect(self._on_ocr_error)
        self._thread_pool.start(task)

    @Slot(str)
    def _on_ocr_finished(self, result: str) -> None:
        """OCR识别完成"""
        self._ui.textResult.setPlaceholderText("识别结果将显示在这里...")
        if result:
            self._ui.textResult.setPlainText(result)
            self._ui.statusbar.showMessage(f"识别完成，共 {len(result)} 个字符")
        else:
            self._ui.textResult.setPlainText("未识别到文字")
            self._ui.statusbar.showMessage("未识别到文字")

    @Slot(str)
    def _on_ocr_error(self, error_msg: str) -> None:
        """OCR识别失败"""
        self._ui.textResult.setPlaceholderText("识别结果将显示在这里...")
        self._ui.textResult.setPlainText(f"识别失败：{error_msg}")
        self._ui.statusbar.showMessage(f"识别失败：{error_msg}")

    @Slot()
    def _on_copy_result(self) -> None:
        """复制识别结果"""
        text = self._ui.textResult.toPlainText()
        if text:
            clipboard = QApplication.clipboard()
            clipboard.setText(text)
            self._ui.statusbar.showMessage("已复制到剪贴板")

    @Slot()
    def _on_about(self) -> None:
        """显示关于对话框"""
        QMessageBox.about(
            self,
            "关于 VibeOCR",
            "VibeOCR v0.1.0\n\n"
            "一个简单的截图OCR识别工具\n\n"
            "使用 RapidOCR 进行文字识别",
        )

    def closeEvent(self, event) -> None:
        """关闭窗口事件"""
        self._thread_pool.waitForDone(1000)
        event.accept()
