"""Main window view logic"""

import io
import logging
from pathlib import Path

from PIL import Image
from PySide6.QtWidgets import (
    QMainWindow,
    QFileDialog,
    QMessageBox,
    QApplication,
    QMenuBar,
    QStatusBar,
    QWidget,
    QVBoxLayout,
)
from PySide6.QtCore import Slot, QThreadPool, QRunnable, Signal, QObject, QTimer, QBuffer
from PySide6.QtGui import QPixmap, QAction
from PySide6.QtUiTools import QUiLoader

from vibeocr.widgets.preview_widget import PreviewWidget
from vibeocr.widgets.screenshot_widget import ScreenshotWidget
from vibeocr.widgets.console_widget import ConsoleWidget
from vibeocr.services.ocr_service import OCRService
from vibeocr.services.log_service import setup_logging
from vibeocr import env_manager


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

            # 转换为 numpy 数组（PaddleX 只接受 numpy.ndarray 或 str）
            import numpy as np
            image_array = np.array(pil_image)

            # 执行OCR
            ocr = OCRService()
            result = ocr.recognize(image_array)
            self.signals.finished.emit(result)
        except Exception as e:
            self.signals.error.emit(str(e))


class DependencyCheckSignals(QObject):
    """依赖检查信号"""

    finished = Signal(bool, list)  # (是否就绪, 缺失依赖列表)


class DependencyCheckTask(QRunnable):
    """依赖检查任务（在后台线程执行）"""

    def __init__(self, project_root: Path) -> None:
        super().__init__()
        self._project_root = project_root
        self.signals = DependencyCheckSignals()

    def run(self) -> None:
        """检查OCR依赖

        统一使用 env_manager.is_embedded_environment_ready() 检查
        支持虚拟环境模式和便携式模式
        """
        # 记录环境模式
        mode = env_manager.get_environment_mode(self._project_root)
        logging.info(f"[依赖检查] 环境模式: {mode}")

        # 获取目标Python路径
        python_exe = env_manager.get_embedded_python_executable(self._project_root)
        logging.info(f"[依赖检查] 目标Python: {python_exe}")

        # 使用统一的依赖检查接口
        ready, missing = env_manager.is_embedded_environment_ready(self._project_root)

        if ready:
            logging.info("[依赖检查] OCR依赖已就绪")
        else:
            logging.warning(f"[依赖检查] OCR依赖缺失: {missing}")

        self.signals.finished.emit(ready, missing)


class MainWindow(QMainWindow):
    """主窗口"""

    def __init__(self) -> None:
        super().__init__()
        self._project_root = env_manager.get_project_root()
        self._ocr_ready = False
        self._setup_ui()
        self._setup_console()
        self._create_menus()
        self._connect_signals()
        self._thread_pool = QThreadPool()

        # 延迟检查嵌入式依赖（在UI显示后）
        QTimer.singleShot(100, self._check_embedded_dependencies)

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

        # 创建状态栏
        self._statusbar = QStatusBar(self)
        self.setStatusBar(self._statusbar)

        # 创建截图组件
        self._screenshot_widget = ScreenshotWidget()

    def _setup_console(self) -> None:
        """初始化控制台"""
        # 创建控制台控件
        self._console = ConsoleWidget(self)

        # 将控制台添加到 UI 中的容器
        container = self._ui.findChild(QWidget, "consoleContainer")
        if container:
            container_layout = container.layout()
            if not container_layout:
                container_layout = QVBoxLayout(container)
                container_layout.setContentsMargins(0, 0, 0, 0)
            container_layout.addWidget(self._console)

        # 配置日志
        setup_logging(self._console.append_log)
        logging.info("VibeOCR 启动")

    def _create_menus(self) -> None:
        """创建菜单栏"""
        menubar = self.menuBar()

        # 文件菜单
        file_menu = menubar.addMenu("文件")

        self._action_open_image = QAction("打开图片", self)
        self._action_open_image.setShortcut("Ctrl+O")
        file_menu.addAction(self._action_open_image)

        self._action_screenshot = QAction("截图", self)
        self._action_screenshot.setShortcut("Ctrl+S")
        file_menu.addAction(self._action_screenshot)

        file_menu.addSeparator()

        self._action_exit = QAction("退出", self)
        self._action_exit.setShortcut("Ctrl+Q")
        file_menu.addAction(self._action_exit)

        # 工具菜单
        tools_menu = menubar.addMenu("工具")

        self._action_refresh_cache = QAction("刷新依赖缓存", self)
        self._action_refresh_cache.setShortcut("Ctrl+Shift+R")
        self._action_refresh_cache.setStatusTip("清除缓存并重新检测OCR依赖")
        tools_menu.addAction(self._action_refresh_cache)

        # 帮助菜单
        help_menu = menubar.addMenu("帮助")

        self._action_about = QAction("关于", self)
        help_menu.addAction(self._action_about)

    def _connect_signals(self) -> None:
        """连接信号槽"""
        # 菜单动作
        self._action_open_image.triggered.connect(self._on_open_image)
        self._action_screenshot.triggered.connect(self._on_screenshot)
        self._action_exit.triggered.connect(self.close)
        self._action_about.triggered.connect(self._on_about)
        self._action_refresh_cache.triggered.connect(self._on_refresh_cache)

        # 截图组件
        self._screenshot_widget.captured.connect(self._on_screenshot_captured)

        # 预览组件
        self._ui.previewWidget.screenshot_requested.connect(self._on_screenshot)

        # 复制按钮
        self._ui.btnCopy.clicked.connect(self._on_copy_result)

    def _check_embedded_dependencies(self) -> None:
        """异步检查嵌入式OCR依赖"""
        task = DependencyCheckTask(self._project_root)
        task.signals.finished.connect(self._on_dependency_check_finished)
        self._thread_pool.start(task)

    @Slot(bool, list)
    def _on_dependency_check_finished(self, ready: bool, missing: list) -> None:
        """依赖检查完成"""
        if ready:
            self._ocr_ready = True
            self._statusbar.showMessage("OCR功能已就绪")
            logging.info("OCR功能已就绪")
        else:
            self._ocr_ready = False
            missing_str = ", ".join(missing)
            self._statusbar.showMessage(f"OCR功能未就绪: {missing_str}")
            logging.warning(f"OCR功能未就绪，缺少: {missing_str}")
            # 显示安装提示对话框
            self._show_install_dialog(missing)

    def _show_install_dialog(self, missing: list) -> None:
        """显示安装提示对话框"""
        missing_str = ", ".join(missing)
        reply = QMessageBox.question(
            self,
            "OCR功能需要安装依赖",
            f"OCR功能需要安装以下依赖:\n{missing_str}\n\n"
            "这将下载并安装PaddlePaddle和PaddleX。\n"
            "系统会自动检测GPU，优先安装GPU版本（如有CUDA环境），\n"
            "否则安装CPU版本。GPU版本需要cuDNN运行时库。\n"
            "可能需要几分钟时间。\n\n"
            "是否现在安装？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            self._start_install()
        else:
            QMessageBox.information(
                self,
                "提示",
                "OCR功能将不可用。\n您可以稍后通过菜单重新安装。"
            )

    def _start_install(self) -> None:
        """开始安装依赖"""
        from vibeocr.widgets.install_dialog import InstallDialog

        dialog = InstallDialog(self._project_root, self)
        dialog.finished.connect(self._on_install_finished)
        dialog.exec()

    @Slot(int)
    def _on_install_finished(self, result: int) -> None:
        """安装完成"""
        if result == 1:  # 安装成功
            self._ocr_ready = True
            self._statusbar.showMessage("OCR依赖安装成功")
            QMessageBox.information(self, "安装成功", "OCR依赖安装成功，现在可以使用OCR功能。")
        else:
            self._statusbar.showMessage("OCR依赖安装失败")

    @Slot()
    def _on_open_image(self) -> None:
        """打开图片文件"""
        if not self._check_ocr_ready():
            return
        logging.info("打开图片文件对话框")

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

    def _check_ocr_ready(self) -> bool:
        """检查OCR功能是否可用"""
        if not self._ocr_ready:
            reply = QMessageBox.question(
                self,
                "OCR功能未就绪",
                "OCR功能需要安装依赖才能使用。\n\n是否现在安装？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._start_install()
            return False
        return True

    @Slot()
    def _on_screenshot(self) -> None:
        """开始截图"""
        if not self._check_ocr_ready():
            return

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
        logging.info("开始 OCR 识别")
        self._ui.textResult.clear()
        self._ui.textResult.setPlaceholderText("正在识别...")
        self._statusbar.showMessage("正在识别...")

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
        char_count = len(result) if result else 0
        logging.info(f"OCR 识别完成，共 {char_count} 个字符")
        self._ui.textResult.setPlaceholderText("识别结果将显示在这里...")
        if result:
            self._ui.textResult.setPlainText(result)
            self._statusbar.showMessage(f"识别完成，共 {len(result)} 个字符")
        else:
            self._ui.textResult.setPlainText("未识别到文字")
            self._statusbar.showMessage("未识别到文字")

    @Slot(str)
    def _on_ocr_error(self, error_msg: str) -> None:
        """OCR识别失败"""
        logging.error(f"OCR 识别失败: {error_msg}")
        self._ui.textResult.setPlaceholderText("识别结果将显示在这里...")
        self._ui.textResult.setPlainText(f"识别失败：{error_msg}")
        self._statusbar.showMessage(f"识别失败：{error_msg}")

    @Slot()
    def _on_copy_result(self) -> None:
        """复制识别结果"""
        text = self._ui.textResult.toPlainText()
        if text:
            clipboard = QApplication.clipboard()
            clipboard.setText(text)
            self._statusbar.showMessage("已复制到剪贴板")

    @Slot()
    def _on_about(self) -> None:
        """显示关于对话框"""
        QMessageBox.about(
            self,
            "关于 VibeOCR",
            "VibeOCR v0.1.0\n\n"
            "一个简单的截图OCR识别工具\n\n"
            "使用 PaddleOCR 进行文字识别",
        )

    @Slot()
    def _on_refresh_cache(self) -> None:
        """刷新依赖缓存"""
        from vibeocr.machine_cache import clear_cache

        logging.info("正在清除依赖缓存...")
        clear_cache(self._project_root)
        self._statusbar.showMessage("缓存已清除，正在重新检测依赖...")

        # 重新检测依赖
        task = DependencyCheckTask(self._project_root)
        task.signals.finished.connect(self._on_refresh_cache_finished)
        self._thread_pool.start(task)

    @Slot(bool, list)
    def _on_refresh_cache_finished(self, ready: bool, missing: list) -> None:
        """刷新缓存完成"""
        if ready:
            self._ocr_ready = True
            self._statusbar.showMessage("依赖检测完成，OCR功能已就绪")
            logging.info("依赖缓存刷新完成，OCR功能已就绪")
        else:
            self._ocr_ready = False
            missing_str = ", ".join(missing)
            self._statusbar.showMessage(f"依赖检测完成，缺失: {missing_str}")
            logging.warning(f"依赖缓存刷新完成，缺失: {missing_str}")

    def closeEvent(self, event) -> None:
        """关闭窗口事件"""
        self._thread_pool.waitForDone(1000)
        event.accept()
