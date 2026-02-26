"""Main window view logic"""

import io
import logging
from pathlib import Path
from typing import Optional

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
    QLabel,
)
from PySide6.QtCore import Slot, QThreadPool, QRunnable, Signal, QObject, QTimer, QBuffer
from PySide6.QtGui import QPixmap, QAction
from PySide6.QtUiTools import QUiLoader

from vibeocr.widgets.preview_widget import PreviewWidget
from vibeocr.widgets.screenshot_widget import ScreenshotWidget
from vibeocr.widgets.console_widget import ConsoleWidget
from vibeocr.services.ocr_service import OCRService, OCRPreset, OCRPipeline, OCROptions
from vibeocr.services.log_service import setup_logging
from vibeocr.models.ocr_result import OCRResult
from vibeocr import env_manager
from vibeocr.machine_cache import is_cache_valid


class OCRSignals(QObject):
    """OCR任务信号（用于线程安全通信）"""

    finished = Signal(object)  # 识别完成 (OCRResult)
    error = Signal(str)  # 识别失败


class OCRTask(QRunnable):
    """OCR识别任务（在后台线程执行）"""

    def __init__(self, image_data: bytes, options: OCROptions | None = None) -> None:
        super().__init__()
        self._image_data = image_data
        self._options = options or OCROptions()
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
            result = ocr.recognize(image_array, self._options)
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

    # 状态更新信号（用于线程安全的状态栏更新）
    _status_update_signal = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._project_root = env_manager.get_project_root()
        self._ocr_ready = False
        self._dependency_check_complete = False  # 依赖检测是否完成
        self._setup_ui()
        self._setup_console()
        self._create_menus()
        self._connect_signals()
        self._thread_pool = QThreadPool()

        # 设置 OCRService 状态回调（用于显示模型下载进度）
        self._setup_ocr_status_callback()

        # 启动时立即读取缓存，如果有有效缓存则直接更新状态
        self._try_load_cache()
        # 异步检查嵌入式依赖（在UI显示后）
        QTimer.singleShot(100, self._check_embedded_dependencies)

    def _setup_ocr_status_callback(self) -> None:
        """设置 OCR 状态回调，用于在状态栏显示模型下载进度"""
        def on_ocr_status(stage: str, message: str) -> None:
            """OCR 状态回调（可能从后台线程调用）"""
            # 使用信号确保在主线程中更新 UI
            self._status_update_signal.emit(message)

        # 连接信号到状态栏更新槽
        self._status_update_signal.connect(self._on_status_update)
        OCRService.set_status_callback(on_ocr_status)

    @Slot(str)
    def _on_status_update(self, message: str) -> None:
        """状态更新槽（线程安全）"""
        self._statusbar.showMessage(message)

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

        # 初始化 OCR 预设下拉框
        self._init_preset_combo()

        # 创建截图组件
        self._screenshot_widget = ScreenshotWidget()

        # 创建复制成功提示标签
        self._copy_toast = QLabel("已复制到剪贴板", self._ui.btnCopyRich)
        self._copy_toast.setStyleSheet("""
            QLabel {
                background-color: #333333;
                color: white;
                padding: 6px 12px;
                border-radius: 4px;
                font-size: 12px;
            }
        """)
        self._copy_toast.hide()

        # 保存当前 OCR 结果（用于复制）
        self._current_ocr_result: OCRResult | None = None

    def _init_preset_combo(self) -> None:
        """初始化 OCR 管道和选项按钮"""
        # 按钮样式：选中/未选中状态
        button_style = """
            QPushButton {
                border: 1px solid #c0c0c0;
                border-radius: 4px;
                padding: 4px 10px;
                background-color: #f0f0f0;
                color: #333;
            }
            QPushButton:hover {
                background-color: #e0e0e0;
            }
            QPushButton:checked {
                background-color: #0078d4;
                color: white;
                border-color: #0078d4;
            }
        """

        # 管道按钮映射
        self._pipeline_buttons = {
            OCRPipeline.OCR: self._ui.findChild(QWidget, "btnPipelineOCR"),
            OCRPipeline.TABLE_RECOGNITION: self._ui.findChild(QWidget, "btnPipelineTable"),
            OCRPipeline.FORMULA_RECOGNITION: self._ui.findChild(QWidget, "btnPipelineFormula"),
            OCRPipeline.PP_STRUCTURE_V3: self._ui.findChild(QWidget, "btnPipelineStructure"),
        }

        # 预处理按钮
        self._btn_orient = self._ui.findChild(QWidget, "btnOrient")
        self._btn_unwarp = self._ui.findChild(QWidget, "btnUnwarp")
        self._btn_textline = self._ui.findChild(QWidget, "btnTextline")
        self._btn_layout = self._ui.findChild(QWidget, "btnLayout")

        # 子产线按钮
        self._btn_sub_table = self._ui.findChild(QWidget, "btnSubTable")
        self._btn_sub_formula = self._ui.findChild(QWidget, "btnSubFormula")
        self._btn_sub_seal = self._ui.findChild(QWidget, "btnSubSeal")
        self._btn_sub_chart = self._ui.findChild(QWidget, "btnSubChart")

        # 应用样式并连接信号
        for pipeline, btn in self._pipeline_buttons.items():
            if btn:
                btn.setStyleSheet(button_style)
                btn.clicked.connect(lambda checked, p=pipeline: self._on_pipeline_clicked(p))

        # 预处理按钮样式
        preprocess_style = """
            QPushButton {
                border: 1px solid #c0c0c0;
                border-radius: 4px;
                padding: 4px 10px;
                background-color: #f0f0f0;
                color: #333;
            }
            QPushButton:hover {
                background-color: #e0e0e0;
            }
            QPushButton:checked {
                background-color: #f7630c;
                color: white;
                border-color: #f7630c;
            }
        """
        for btn in [self._btn_orient, self._btn_unwarp, self._btn_textline, self._btn_layout]:
            if btn:
                btn.setStyleSheet(preprocess_style)

        # 子产线按钮样式
        sub_button_style = """
            QPushButton {
                border: 1px solid #c0c0c0;
                border-radius: 4px;
                padding: 4px 10px;
                background-color: #f0f0f0;
                color: #333;
            }
            QPushButton:hover {
                background-color: #e0e0e0;
            }
            QPushButton:checked {
                background-color: #107c10;
                color: white;
                border-color: #107c10;
            }
        """
        for btn in [self._btn_sub_table, self._btn_sub_formula, self._btn_sub_seal, self._btn_sub_chart]:
            if btn:
                btn.setStyleSheet(sub_button_style)

        # 初始化子产线选项（默认隐藏，仅版面解析时显示）
        self._sub_pipeline_widget = self._ui.findChild(QWidget, "subPipelineOptions")

        # 初始化按钮可见性
        self._update_button_visibility(OCRPipeline.OCR)

        # 创建截图组件
        self._screenshot_widget = ScreenshotWidget()

        # 创建复制成功提示标签
        self._copy_toast = QLabel("已复制到剪贴板", self._ui.btnCopyRich)
        self._copy_toast.setStyleSheet("""
            QLabel {
                background-color: #333333;
                color: white;
                padding: 6px 12px;
                border-radius: 4px;
                font-size: 12px;
            }
        """)
        self._copy_toast.hide()

        # 保存当前 OCR 结果（用于复制）
        self._current_ocr_result: OCRResult | None = None

    def _on_pipeline_clicked(self, pipeline: OCRPipeline) -> None:
        """管道按钮点击时更新 UI"""
        self._update_button_visibility(pipeline)

    def _update_button_visibility(self, pipeline: OCRPipeline) -> None:
        """根据管道类型更新按钮可见性"""
        # 子产线选项：仅版面解析时显示
        if self._sub_pipeline_widget:
            self._sub_pipeline_widget.setVisible(pipeline == OCRPipeline.PP_STRUCTURE_V3)

        # 文本行方向按钮：仅通用 OCR 时显示
        if self._btn_textline:
            self._btn_textline.setVisible(pipeline == OCRPipeline.OCR)

        # 版面检测按钮：仅表格和公式管道时显示
        if self._btn_layout:
            self._btn_layout.setVisible(pipeline in [OCRPipeline.TABLE_RECOGNITION, OCRPipeline.FORMULA_RECOGNITION])

        logging.debug(f"管道切换为 {pipeline.display_name}")

    def _get_current_pipeline(self) -> OCRPipeline:
        """获取当前选中的管道"""
        for pipeline, btn in self._pipeline_buttons.items():
            if btn and btn.isChecked():
                return pipeline
        return OCRPipeline.OCR

    def _setup_console(self) -> None:
        """初始化控制台"""
        # 创建控制台控件
        self._console = ConsoleWidget(self)

        # 连接低置信度计数变化信号
        self._console.low_confidence_count_changed.connect(self._on_low_confidence_changed)

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

    @Slot(int, list)
    def _on_low_confidence_changed(self, count: int, items: list) -> None:
        """低置信度文本块数量变化

        Args:
            count: 低置信度文本块数量
            items: 低置信度文本块详情列表 [(文本, 置信度), ...]
        """
        if count > 0:
            # 构建低置信度详情信息
            details = []
            for text, confidence in items:
                # 截断长文本
                display_text = text[:20] + "..." if len(text) > 20 else text
                details.append(f"'{display_text}' ({confidence:.0%})")

            detail_str = "、".join(details)
            message = f"{count} 个低置信度文本块: {detail_str}"

            # 在状态栏显示低置信度信息
            current_msg = self._statusbar.currentMessage()
            # 如果当前消息是 OCR 识别完成相关的，替换为带详情的消息
            if "识别完成" in current_msg:
                # 从原消息中提取文本块数和平均置信度
                self._statusbar.showMessage(f"{current_msg}，{message}")
            else:
                self._statusbar.showMessage(message, 5000)  # 显示5秒

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
        self._ui.btnCopyRich.clicked.connect(self._on_copy_rich)
        self._ui.btnCopyMarkdown.clicked.connect(self._on_copy_markdown)
        self._ui.btnCopyPlain.clicked.connect(self._on_copy_plain)

    def _try_load_cache(self) -> None:
        """尝试从缓存加载依赖检测结果"""
        is_valid, cached_data = is_cache_valid(self._project_root)
        if is_valid and cached_data:
            dependencies = cached_data.get("dependencies", {})
            # 检查关键依赖
            paddlepaddle_ok = dependencies.get("paddlepaddle", False)
            paddlex_ok = dependencies.get("paddlex", False)
            if paddlepaddle_ok and paddlex_ok:
                self._ocr_ready = True
                self._dependency_check_complete = True
                self._statusbar.showMessage("OCR功能已就绪（缓存）")
                logging.info("OCR功能已就绪（缓存）")

    def _check_embedded_dependencies(self) -> None:
        """异步检查嵌入式OCR依赖"""
        task = DependencyCheckTask(self._project_root)
        task.signals.finished.connect(self._on_dependency_check_finished)
        self._thread_pool.start(task)

    @Slot(bool, list)
    def _on_dependency_check_finished(self, ready: bool, missing: list) -> None:
        """依赖检查完成"""
        self._dependency_check_complete = True
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
        """检查OCR功能是否可用

        Returns:
            True if OCR is ready, False otherwise
        """
        # 如果依赖检测还没完成，显示检测中提示
        if not self._dependency_check_complete:
            QMessageBox.information(
                self,
                "正在检测依赖",
                "OCR依赖检测中，请稍候...\n\n"
                "检测完成后才能使用截图识别功能。"
            )
            return False

        # 如果依赖检测完成但不可用，提示安装
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
            # 记录截图分辨率信息
            dpr = pixmap.devicePixelRatio()
            width = pixmap.width()
            height = pixmap.height()
            logging.info(f"截图完成: {width}x{height} 像素, DPR={dpr}")

            self._ui.previewWidget.set_pixmap(pixmap)
            self._run_ocr(pixmap)

    def _run_ocr(self, pixmap: QPixmap) -> None:
        """执行OCR识别"""
        logging.info("开始 OCR 识别")
        self._ui.textResult.clear()
        self._ui.textResult.setPlaceholderText("正在识别...")
        self._statusbar.showMessage("正在识别...")

        # 获取当前选择的管道
        pipeline = self._get_current_pipeline()

        # 获取预处理选项
        use_orient = self._btn_orient.isChecked() if self._btn_orient else False
        use_unwarp = self._btn_unwarp.isChecked() if self._btn_unwarp else False
        use_textline = self._btn_textline.isChecked() if self._btn_textline else True
        use_layout = self._btn_layout.isChecked() if self._btn_layout else False

        # 获取子产线选项（仅版面解析管道有效）
        use_table = self._btn_sub_table.isChecked() if self._btn_sub_table else True
        use_formula = self._btn_sub_formula.isChecked() if self._btn_sub_formula else True
        use_seal = self._btn_sub_seal.isChecked() if self._btn_sub_seal else False
        use_chart = self._btn_sub_chart.isChecked() if self._btn_sub_chart else False

        # 创建 OCR 选项
        options = OCROptions(
            pipeline=pipeline,
            use_doc_orientation_classify=use_orient,
            use_doc_unwarping=use_unwarp,
            use_textline_orientation=use_textline,
            use_layout_detection=use_layout,
            use_table_recognition=use_table,
            use_formula_recognition=use_formula,
            use_seal_recognition=use_seal,
            use_chart_recognition=use_chart,
        )

        logging.info(f"OCR 管道: {pipeline.display_name}, 预处理: 方向={use_orient}, 去弯={use_unwarp}")
        if pipeline == OCRPipeline.PP_STRUCTURE_V3:
            logging.info(f"子产线: 表格={use_table}, 公式={use_formula}, 印章={use_seal}, 图表={use_chart}")

        # 在主线程中将 QPixmap 转换为字节（线程安全）
        buffer = QBuffer()
        buffer.open(QBuffer.OpenModeFlag.ReadWrite)
        pixmap.save(buffer, "PNG")
        image_data = bytes(buffer.data().data())
        buffer.close()

        task = OCRTask(image_data, options)
        task.signals.finished.connect(self._on_ocr_finished)
        task.signals.error.connect(self._on_ocr_error)
        self._thread_pool.start(task)

    @Slot(object)
    def _on_ocr_finished(self, result: OCRResult) -> None:
        """OCR识别完成"""
        # 保存结果用于复制
        self._current_ocr_result = result

        char_count = len(result.raw_text) if result.raw_text else 0
        block_count = len(result.text_with_scores)
        logging.info(f"OCR 识别完成，共 {block_count} 个文本块，{char_count} 个字符")

        # 记录置信度详情
        if result.text_with_scores:
            logging.info("=== OCR 置信度详情 ===")
            for i, (text, score) in enumerate(result.text_with_scores, 1):
                # 截断长文本用于显示
                display_text = text[:30] + "..." if len(text) > 30 else text
                display_text = display_text.replace("\n", " ")
                logging.info(f"  [{i}] 置信度: {score:.2%} | {display_text}")
            logging.info(f"  平均置信度: {result.avg_score:.2%}")
            logging.info("======================")

        self._ui.textResult.setPlaceholderText("识别结果将显示在这里...")
        if result.has_rich_content:
            # 有富文本内容（表格、公式等），使用 HTML 显示
            self._ui.textResult.setHtml(result.html_text)
        elif result.raw_text:
            # 普通文本
            self._ui.textResult.setPlainText(result.raw_text)
        else:
            self._ui.textResult.setPlainText("未识别到文字")

        # 构建状态栏消息
        if result.raw_text:
            if result.text_with_scores:
                base_msg = f"识别完成，{block_count} 个文本块，平均置信度: {result.avg_score:.0%}"
                if result.low_confidence_items:
                    details = []
                    for text, confidence in result.low_confidence_items:
                        display_text = text[:20] + "..." if len(text) > 20 else text
                        details.append(f"'{display_text}' ({confidence:.0%})")
                    detail_str = "、".join(details)
                    self._statusbar.showMessage(
                        f"{base_msg}，{len(result.low_confidence_items)} 个低置信度: {detail_str}"
                    )
                else:
                    self._statusbar.showMessage(base_msg)
            else:
                self._statusbar.showMessage(f"识别完成，共 {char_count} 个字符")
        else:
            self._statusbar.showMessage("未识别到文字")

    @Slot(str)
    def _on_ocr_error(self, error_msg: str) -> None:
        """OCR识别失败"""
        logging.error(f"OCR 识别失败: {error_msg}")
        self._current_ocr_result = None
        self._ui.textResult.setPlaceholderText("识别结果将显示在这里...")
        self._ui.textResult.setPlainText(f"识别失败：{error_msg}")
        self._statusbar.showMessage(f"识别失败：{error_msg}")

    @Slot()
    def _on_copy_rich(self) -> None:
        """复制为富文本格式（支持 Word/Excel 的 CF_HTML 格式）"""
        if not self._current_ocr_result:
            return

        clipboard = QApplication.clipboard()
        mime_data = clipboard.mimeData()

        if self._current_ocr_result.has_rich_content:
            html_content = self._current_ocr_result.html_text

            # 设置标准 HTML 格式
            mime_data.setHtml(html_content)

            # 设置 CF_HTML 格式（Microsoft Office 专用）
            # 格式名称是 "HTML Format"，不是 "text/html"
            cf_html = self._create_cf_html(html_content)
            mime_data.setData("HTML Format", cf_html.encode("utf-8"))

            # 同时设置纯文本（作为备选）
            mime_data.setText(self._current_ocr_result.markdown_text)

            clipboard.setMimeData(mime_data)
            self._statusbar.showMessage("已复制富文本到剪贴板")
        else:
            # 没有富文本，复制纯文本
            clipboard.setText(self._current_ocr_result.raw_text)
            self._statusbar.showMessage("已复制纯文本到剪贴板")

        self._show_copy_toast()

    def _create_cf_html(self, html_fragment: str) -> str:
        """创建 CF_HTML 格式的剪贴板内容

        CF_HTML 是 Microsoft Office 使用的剪贴板格式，
        需要包含特殊的头部结构和字节偏移量。

        Args:
            html_fragment: HTML 片段内容

        Returns:
            CF_HTML 格式的完整字符串
        """
        # 构建 HTML 上下文
        html_template = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
<!--StartFragment-->{}<!--EndFragment-->
</body>
</html>"""

        full_html = html_template.format(html_fragment)

        # 计算偏移量（使用 UTF-8 字节计数）
        # 头部占位符长度（偏移量使用 10 位数字）
        header_template = (
            "Version:0.9\r\n"
            "StartHTML:0000000000\r\n"
            "EndHTML:0000000000\r\n"
            "StartFragment:0000000000\r\n"
            "EndFragment:0000000000\r\n"
        )

        # 头部实际长度
        header_len = len(header_template.encode("utf-8"))

        # 计算 StartFragment 位置（头部 + <!--StartFragment--> 之前的内容）
        start_fragment_marker = "<!--StartFragment-->"
        end_fragment_marker = "<!--EndFragment-->"
        start_fragment_pos = full_html.find(start_fragment_marker)
        end_fragment_pos = full_html.find(end_fragment_marker)

        # 字节偏移
        start_fragment_byte = header_len + len(full_html[:start_fragment_pos + len(start_fragment_marker)].encode("utf-8"))
        end_fragment_byte = header_len + len(full_html[:end_fragment_pos].encode("utf-8"))
        end_html_byte = header_len + len(full_html.encode("utf-8"))

        # 格式化偏移量（10 位数字）
        cf_html = (
            f"Version:0.9\r\n"
            f"StartHTML:{header_len:010d}\r\n"
            f"EndHTML:{end_html_byte:010d}\r\n"
            f"StartFragment:{start_fragment_byte:010d}\r\n"
            f"EndFragment:{end_fragment_byte:010d}\r\n"
            f"{full_html}"
        )

        return cf_html

    @Slot()
    def _on_copy_markdown(self) -> None:
        """复制为 Markdown 格式"""
        if not self._current_ocr_result:
            return

        clipboard = QApplication.clipboard()
        clipboard.setText(self._current_ocr_result.markdown_text)
        self._statusbar.showMessage("已复制 Markdown 到剪贴板")
        self._show_copy_toast()

    @Slot()
    def _on_copy_plain(self) -> None:
        """复制为纯文本格式"""
        if not self._current_ocr_result:
            return

        clipboard = QApplication.clipboard()
        clipboard.setText(self._current_ocr_result.raw_text)
        self._statusbar.showMessage("已复制纯文本到剪贴板")
        self._show_copy_toast()

    def _show_copy_toast(self) -> None:
        """显示复制成功提示"""
        # 调整提示标签位置（按钮上方居中）
        btn = self._ui.btnCopyRich
        toast = self._copy_toast
        toast.adjustSize()
        x = (btn.width() - toast.width()) // 2
        y = -toast.height() - 8
        toast.move(x, y)
        toast.show()
        # 1.5秒后自动隐藏
        QTimer.singleShot(1500, toast.hide)

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
        self._dependency_check_complete = True
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
        logging.info("正在关闭应用程序...")

        # 等待线程池完成（最多5秒）
        if not self._thread_pool.waitForDone(5000):
            logging.warning("部分任务未能在超时时间内完成，强制退出")

        # 清理 OCR 服务的管道缓存（释放内存和 GPU 资源）
        try:
            from vibeocr.services.ocr_service import OCRService
            OCRService._pipelines.clear()
            logging.info("OCR 管道缓存已清理")
        except Exception as e:
            logging.warning(f"清理 OCR 缓存失败: {e}")

        event.accept()
        logging.info("应用程序已关闭")
