"""Main window view logic"""

from __future__ import annotations

import asyncio
import io
import logging
from pathlib import Path
from typing import Optional, TYPE_CHECKING

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
from PySide6.QtCore import (
    Slot, QThreadPool, QRunnable, Signal, QObject, QTimer, QBuffer
)
from PySide6.QtGui import QPixmap, QAction
from PySide6.QtUiTools import QUiLoader

from vibeocr.widgets.preview_widget import PreviewWidget
from vibeocr.widgets.screenshot_widget import ScreenshotWidget
from vibeocr.widgets.console_widget import ConsoleWidget
from vibeocr.services.log_service import setup_logging
from vibeocr.views.batch_recognition_tab import BatchRecognitionTab
from vibeocr.models.ocr_result import OCRResult
from vibeocr import env_manager
from vibeocr.machine_cache import is_cache_valid
from vibeocr.utils.qt_async import run_coroutine
from vibeocr.managers import DependencyManager, SubprocessManager
from vibeocr.core.constants import WindowsColors

# 延迟导入: OCR 服务模块导入很慢（~33s），延迟到首次使用时导入
if TYPE_CHECKING:
    from vibeocr.services.ocr_service import OCRService, OCRPreset, OCRPipeline, OCROptions


class MainWindow(QMainWindow):
    """主窗口"""

    # 状态更新信号（用于线程安全的状态栏更新）
    _status_update_signal = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._project_root = env_manager.get_project_root()
        self._ocr_ready = False
        self._dependency_check_complete = False  # 依赖检测是否完成
        self._preload_complete = False  # 预加载是否完成
        self._closing = False  # 是否正在关闭（防止关闭时重复启动 Worker）

        # 当前 OCR 结果（用于复制操作）
        self._current_ocr_result: OCRResult | None = None

        # 依赖管理器
        self._dependency_manager = DependencyManager(self._project_root, self)
        self._dependency_manager.check_completed.connect(self._on_dependency_check_finished)

        # 子进程管理器
        self._subprocess_manager = SubprocessManager(self._project_root, self)
        self._subprocess_manager.service_ready.connect(self._on_subprocess_worker_ready)
        self._subprocess_manager.progress_update.connect(self._on_subprocess_progress)

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
        # 延迟导入: OCR 服务模块
        from vibeocr.services.ocr_service import OCRService
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

        # 初始化 OCR 预设下拉框（包含截图组件和复制提示的初始化）
        self._init_preset_combo()

        # 添加批量识别标签页
        self._init_batch_tab()

        # 添加信息抽取标签页
        self._init_extraction_tab()

        # 添加文档理解标签页
        self._init_doc_understanding_tab()

    def _init_preset_combo(self) -> None:
        """初始化 OCR 管道和选项按钮"""
        # 延迟导入: OCRPipeline 枚举
        from vibeocr.services.ocr_service import OCRPipeline

        # 按钮样式：选中/未选中状态
        button_style = f"""
            QPushButton {{
                border: 1px solid {WindowsColors.BORDER};
                border-radius: 4px;
                padding: 4px 10px;
                background-color: {WindowsColors.BACKGROUND};
                color: {WindowsColors.TEXT};
            }}
            QPushButton:hover {{
                background-color: {WindowsColors.BACKGROUND_HOVER};
            }}
            QPushButton:checked {{
                background-color: {WindowsColors.PRIMARY};
                color: white;
                border-color: {WindowsColors.PRIMARY};
            }}
        """

        # 管道按钮映射
        self._pipeline_buttons = {
            OCRPipeline.OCR: self._ui.findChild(QWidget, "btnPipelineOCR"),
            OCRPipeline.TABLE_RECOGNITION: self._ui.findChild(QWidget, "btnPipelineTable"),
            OCRPipeline.FORMULA_RECOGNITION: self._ui.findChild(QWidget, "btnPipelineFormula"),
            OCRPipeline.PP_STRUCTURE_V3: self._ui.findChild(QWidget, "btnPipelineStructure"),
            OCRPipeline.PADDLEOCR_VL: self._ui.findChild(QWidget, "btnPipelinePaddleOCRVL"),
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

        # PaddleOCR-VL 特有选项按钮
        self._btn_vl_layout = self._ui.findChild(QWidget, "btnVlLayout")
        self._btn_vl_chart = self._ui.findChild(QWidget, "btnVlChart")
        self._btn_vl_seal = self._ui.findChild(QWidget, "btnVlSeal")
        self._btn_vl_format = self._ui.findChild(QWidget, "btnVlFormat")
        self._btn_vl_ocr_image = self._ui.findChild(QWidget, "btnVlOcrImage")

        # 应用样式并连接信号
        for pipeline, btn in self._pipeline_buttons.items():
            if btn:
                btn.setStyleSheet(button_style)
                btn.clicked.connect(lambda checked, p=pipeline: self._on_pipeline_clicked(p))

        # 预处理按钮样式
        preprocess_style = f"""
            QPushButton {{
                border: 1px solid {WindowsColors.BORDER};
                border-radius: 4px;
                padding: 4px 10px;
                background-color: {WindowsColors.BACKGROUND};
                color: {WindowsColors.TEXT};
            }}
            QPushButton:hover {{
                background-color: {WindowsColors.BACKGROUND_HOVER};
            }}
            QPushButton:checked {{
                background-color: {WindowsColors.ACCENT};
                color: white;
                border-color: {WindowsColors.ACCENT};
            }}
        """
        for btn in [self._btn_orient, self._btn_unwarp, self._btn_textline, self._btn_layout]:
            if btn:
                btn.setStyleSheet(preprocess_style)

        # 子产线按钮样式
        sub_button_style = f"""
            QPushButton {{
                border: 1px solid {WindowsColors.BORDER};
                border-radius: 4px;
                padding: 4px 10px;
                background-color: {WindowsColors.BACKGROUND};
                color: {WindowsColors.TEXT};
            }}
            QPushButton:hover {{
                background-color: {WindowsColors.BACKGROUND_HOVER};
            }}
            QPushButton:checked {{
                background-color: {WindowsColors.SUCCESS};
                color: white;
                border-color: {WindowsColors.SUCCESS};
            }}
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

    def _init_batch_tab(self) -> None:
        """初始化批量识别标签页"""
        # 创建批量识别标签页
        self._batch_tab = BatchRecognitionTab()

        # 添加到标签页控件
        tab_widget = self._ui.findChild(QWidget, "tabWidget")
        if tab_widget:
            tab_widget.addTab(self._batch_tab, "批量识别")
            logging.debug("批量识别标签页已添加")

    def _init_extraction_tab(self) -> None:
        """初始化信息抽取标签页"""
        from vibeocr.views.extraction_tab import ExtractionTab

        self._extraction_tab = ExtractionTab()
        tab_widget = self._ui.findChild(QWidget, "tabWidget")
        if tab_widget:
            tab_widget.addTab(self._extraction_tab, "信息抽取")
            logging.debug("信息抽取标签页已添加")

    def _init_doc_understanding_tab(self) -> None:
        """初始化文档理解标签页"""
        from vibeocr.views.doc_understanding_tab import DocUnderstandingTab

        self._doc_understanding_tab = DocUnderstandingTab()
        tab_widget = self._ui.findChild(QWidget, "tabWidget")
        if tab_widget:
            tab_widget.addTab(self._doc_understanding_tab, "文档理解")
            logging.debug("文档理解标签页已添加")


        """管道按钮点击时更新 UI"""
        self._update_button_visibility(pipeline)

    def _update_button_visibility(self, pipeline) -> None:
        """根据管道类型更新按钮可见性"""
        # 使用管道的 value 属性进行比较（避免导入 OCRPipeline）
        pipeline_value = pipeline.value if hasattr(pipeline, 'value') else pipeline

        # 子产线选项：版面解析和 PaddleOCR-VL 时显示
        if self._sub_pipeline_widget:
            self._sub_pipeline_widget.setVisible(pipeline_value in ["PP-StructureV3", "PaddleOCR-VL"])

        # 文本行方向按钮：仅通用 OCR 时显示
        if self._btn_textline:
            self._btn_textline.setVisible(pipeline_value == "OCR")

        # 版面检测按钮：仅表格和公式管道时显示
        if self._btn_layout:
            self._btn_layout.setVisible(pipeline_value in ["table_recognition", "formula_recognition"])

        # 版面解析子产线按钮：仅版面解析时显示
        pp_structure_buttons = [self._btn_sub_table, self._btn_sub_formula, self._btn_sub_seal, self._btn_sub_chart]
        vl_buttons = [self._btn_vl_layout, self._btn_vl_chart, self._btn_vl_seal, self._btn_vl_format, self._btn_vl_ocr_image]

        for btn in pp_structure_buttons:
            if btn:
                btn.setVisible(pipeline_value == "PP-StructureV3")

        # PaddleOCR-VL 特有选项按钮：仅 PaddleOCR-VL 时显示
        for btn in vl_buttons:
            if btn:
                btn.setVisible(pipeline_value == "PaddleOCR-VL")

        # 更新子产线标签文字
        label_sub = self._ui.findChild(QWidget, "labelSubPipelines")
        label_vl = self._ui.findChild(QWidget, "labelVlOptions")
        if label_sub:
            label_sub.setVisible(pipeline_value == "PP-StructureV3")
        if label_vl:
            label_vl.setVisible(pipeline_value == "PaddleOCR-VL")

        logging.debug(f"管道切换为 {pipeline.display_name}")

    def _get_current_pipeline(self):
        """获取当前选中的管道"""
        # 延迟导入: OCRPipeline 枚举
        from vibeocr.services.ocr_service import OCRPipeline
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

        # 设置页面
        self._connect_settings_signals()

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
        self._dependency_manager.check_dependencies()

    @Slot(bool, list)
    def _on_dependency_check_finished(self, ready: bool, missing: list) -> None:
        """依赖检查完成"""
        self._dependency_check_complete = True
        if ready:
            self._ocr_ready = True
            self._statusbar.showMessage("OCR功能已就绪")
            logging.info("OCR功能已就绪")
            
            # 启动子进程 Worker（依赖检测完成后立即启动）
            self._start_subprocess_worker()
        else:
            self._ocr_ready = False
            missing_str = ", ".join(missing)
            self._statusbar.showMessage(f"OCR功能未就绪: {missing_str}")

    def _start_subprocess_worker(self) -> None:
        """依赖检测完成后启动子进程 Worker

        使用 SubprocessManager 管理子进程生命周期。
        """
        if self._closing:
            logging.info("[MainWindow] 应用程序正在关闭，跳过启动子进程 Worker")
            return

        if self._subprocess_manager.is_ready:
            logging.info("[MainWindow] 子进程 Worker 已就绪，跳过启动")
            return

        logging.info("[MainWindow] 正在启动子进程 Worker...")
        self._statusbar.showMessage("正在启动 OCR 服务...")

        # 使用 SubprocessManager 启动
        self._subprocess_manager.start(use_gpu=True, start_timeout=120.0)
    
    @Slot(bool)
    def _on_subprocess_worker_ready(self, success: bool) -> None:
        """子进程 Worker 就绪回调"""
        if success:
            logging.info("[MainWindow] 子进程 Worker 已就绪")
            self._statusbar.showMessage("OCR 服务已就绪")

            # 获取服务实例
            service = self._subprocess_manager.service

            # 设置 OCR 服务到批量识别标签页
            if hasattr(self, '_batch_tab') and self._batch_tab:
                self._batch_tab.set_ocr_service(service)
                logging.info("[MainWindow] 批量识别标签页已连接 OCR 服务")

            # 设置 OCR 服务到信息抽取标签页
            if hasattr(self, '_extraction_tab') and self._extraction_tab:
                self._extraction_tab.set_ocr_service(service)
                logging.info("[MainWindow] 信息抽取标签页已连接 OCR 服务")

            # 设置 OCR 服务到文档理解标签页
            if hasattr(self, '_doc_understanding_tab') and self._doc_understanding_tab:
                self._doc_understanding_tab.set_ocr_service(service)
                logging.info("[MainWindow] 文档理解标签页已连接 OCR 服务")

            # 子进程就绪后，触发预加载（如果配置了预加载管道）
            self._start_subprocess_preload()
        else:
            logging.warning("[MainWindow] 子进程 Worker 启动失败")
            self._statusbar.showMessage("OCR 服务启动失败")
            # 显示错误提示
            QMessageBox.warning(
                self,
                "OCR 服务启动失败",
                "OCR 子进程服务启动失败。\n\n"
                "可能原因:\n"
                "1. 首次启动需要下载模型（请检查网络）\n"
                "2. GPU 驱动或 CUDA 版本不兼容\n"
                "3. 系统内存不足\n\n"
                "请查看控制台日志了解详情。"
            )

    @Slot(str, int)
    def _on_subprocess_progress(self, stage: str, percent: int) -> None:
        """子进程启动进度回调"""
        self._statusbar.showMessage(f"正在启动 OCR 服务: {stage} ({percent}%)")
    
    def _start_subprocess_preload(self) -> None:
        """在子进程中预加载用户配置的管道"""
        if not self._subprocess_manager.is_ready:
            return

        # 获取用户配置的预加载管道
        from vibeocr.machine_cache import get_preload_pipelines
        pipelines = get_preload_pipelines(self._project_root)

        if not pipelines:
            logging.info("[子进程预加载] 未配置预加载管道")
            return

        logging.info(f"[子进程预加载] 开始预加载管道: {pipelines}")

        # 使用 SubprocessManager 预加载
        self._subprocess_manager.preload_pipelines(pipelines)



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
        """Execute OCR recognition

        Supports two modes:
        1. Async subprocess mode (default): Execute OCR via subprocess with asyncio
        2. Direct mode: Execute OCR directly in main thread (for debugging)

        Mode switching controlled by environment variable VIBEOCR_USE_SUBPROCESS.
        """
        # Lazy import: OCR related types
        from vibeocr.services.ocr_service import OCROptions, OCRPipeline
        from vibeocr.services import USE_SUBPROCESS

        logging.info("Starting OCR recognition")
        self._ui.textResult.clear()
        self._ui.textResult.setPlaceholderText("Recognizing...")
        self._statusbar.showMessage("Recognizing...")

        # Force UI update to show "Recognizing" message
        QApplication.processEvents()

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

        # 获取 PaddleOCR-VL 特有选项
        vl_use_layout = self._btn_vl_layout.isChecked() if self._btn_vl_layout else True
        vl_use_chart = self._btn_vl_chart.isChecked() if self._btn_vl_chart else False
        vl_use_seal = self._btn_vl_seal.isChecked() if self._btn_vl_seal else False
        vl_format = self._btn_vl_format.isChecked() if self._btn_vl_format else False
        vl_ocr_image = self._btn_vl_ocr_image.isChecked() if self._btn_vl_ocr_image else False

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
            vl_use_layout_detection=vl_use_layout,
            vl_use_seal_recognition=vl_use_seal,
            vl_use_ocr_for_image_block=vl_ocr_image,
            vl_format_block_content=vl_format,
        )

        logging.info(f"OCR 管道: {pipeline.display_name}, 预处理: 方向={use_orient}, 去弯={use_unwarp}")
        if pipeline == OCRPipeline.PP_STRUCTURE_V3:
            logging.info(f"子产线: 表格={use_table}, 公式={use_formula}, 印章={use_seal}, 图表={use_chart}")

        # 将 QPixmap 转换为图像数据
        buffer = QBuffer()
        buffer.open(QBuffer.OpenModeFlag.ReadWrite)
        pixmap.save(buffer, "PNG")
        image_data = bytes(buffer.data().data())
        buffer.close()

        # 使用异步方式执行 OCR
        if USE_SUBPROCESS:
            # 异步子进程模式：使用 asyncio 协程
            run_coroutine(self._perform_ocr_async(image_data, options))
        else:
            # 直接模式（用于调试）- 保持同步执行
            try:
                pil_image = Image.open(io.BytesIO(image_data))
                import numpy as np
                image_array = np.array(pil_image)
                logging.info(f"[主线程OCR] 图像尺寸: {pil_image.size}, 数组形状: {image_array.shape}")
                logging.info("[主线程OCR] 开始识别...")
                from vibeocr.services import get_ocr_service
                ocr_service = get_ocr_service()
                result = ocr_service.recognize(image_array, options)
                logging.info(f"[主线程OCR] 识别完成，{len(result.raw_text)} 字符")
                self._on_ocr_finished(result)
            except Exception as e:
                logging.error(f"OCR 识别失败: {e}", exc_info=True)
                self._on_ocr_error(str(e))

    async def _perform_ocr_async(self, image_data: bytes, options) -> None:
        """异步执行 OCR 识别

        使用子进程服务的异步接口执行 OCR，不阻塞 UI 线程。

        Args:
            image_data: PNG 格式的图像数据
            options: OCR 选项
        """
        try:
            # 检查是否正在关闭
            if self._closing:
                logging.info("[异步OCR] 应用程序正在关闭，取消识别")
                return

            from vibeocr.services import get_ocr_service

            logging.info("[异步OCR] 开始异步识别...")
            logging.info("[异步OCR] 调用 get_ocr_service()...")
            ocr_service = get_ocr_service()
            logging.info(f"[异步OCR] get_ocr_service() 返回: {type(ocr_service).__name__}")

            # 再次检查关闭标志
            if self._closing:
                logging.info("[异步OCR] 应用程序正在关闭，取消识别")
                return

            # 检查服务是否就绪
            logging.info("[异步OCR] 检查服务是否就绪...")
            if hasattr(ocr_service, 'is_ready'):
                ready = ocr_service.is_ready()
                logging.info(f"[异步OCR] 服务就绪状态: {ready}")
                if not ready:
                    raise RuntimeError("OCR 服务未就绪，请稍后再试")

            # 使用异步接口
            logging.info("[异步OCR] 调用 recognize_async()...")
            result = await ocr_service.recognize_async(image_data, options)
            logging.info("[异步OCR] recognize_async() 返回")

            # 检查关闭标志（识别完成后）
            if self._closing:
                logging.info("[异步OCR] 应用程序已关闭，忽略识别结果")
                return

            logging.info(f"[异步OCR] 识别完成，{len(result.raw_text)} 字符")
            self._on_ocr_finished(result)

        except Exception as e:
            if self._closing:
                logging.info(f"[异步OCR] 识别过程中应用程序关闭，忽略错误: {e}")
                return
            logging.error(f"[异步OCR] 识别失败: {e}", exc_info=True)
            self._on_ocr_error(str(e))

    @Slot(object)
    def _on_ocr_finished(self, result: OCRResult) -> None:
        """OCR识别完成"""
        logging.info("[_on_ocr_finished] 收到 OCR 完成信号")
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
        logging.error(f"[_on_ocr_error] 收到 OCR 错误信号: {error_msg}")
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
            # 更新设置页面的缓存状态
            self._update_cache_status()
        else:
            self._ocr_ready = False
            missing_str = ", ".join(missing)
            self._statusbar.showMessage(f"依赖检测完成，缺失: {missing_str}")
            logging.warning(f"依赖缓存刷新完成，缺失: {missing_str}")

    # ============================================================
    # 设置页面相关方法
    # ============================================================

    def _connect_settings_signals(self) -> None:
        """连接设置页面的信号槽"""
        # 预加载设置
        chk_enable_preload = self._ui.findChild(QWidget, "chkEnablePreload")
        if chk_enable_preload:
            chk_enable_preload.toggled.connect(self._on_enable_preload_toggled)

        btn_preload_now = self._ui.findChild(QWidget, "btnPreloadNow")
        if btn_preload_now:
            btn_preload_now.clicked.connect(self._on_preload_now_clicked)

        # 缓存管理
        btn_refresh_cache = self._ui.findChild(QWidget, "btnRefreshCache")
        if btn_refresh_cache:
            btn_refresh_cache.clicked.connect(self._on_refresh_cache_clicked)

        btn_clear_cache = self._ui.findChild(QWidget, "btnClearCache")
        if btn_clear_cache:
            btn_clear_cache.clicked.connect(self._on_clear_cache_clicked)

        # LLM 配置相关信号
        btn_save_llm_config = self._ui.findChild(QWidget, "btnSaveLLMConfig")
        if btn_save_llm_config:
            btn_save_llm_config.clicked.connect(self._on_save_llm_config_clicked)

        # 模板管理相关信号
        btn_add_template = self._ui.findChild(QWidget, "btnAddTemplate")
        if btn_add_template:
            btn_add_template.clicked.connect(self._on_add_template_clicked)

        btn_edit_template = self._ui.findChild(QWidget, "btnEditTemplate")
        if btn_edit_template:
            btn_edit_template.clicked.connect(self._on_edit_template_clicked)

        btn_delete_template = self._ui.findChild(QWidget, "btnDeleteTemplate")
        if btn_delete_template:
            btn_delete_template.clicked.connect(self._on_delete_template_clicked)

        # 初始化设置页面状态
        self._init_settings_page()

    def _init_settings_page(self) -> None:
        """初始化设置页面状态"""
        # 更新缓存状态
        self._update_cache_status()

        # 更新预加载状态
        self._update_preload_status()

        # 隐藏并行加载相关选项（当前不可用）
        chk_parallel = self._ui.findChild(QWidget, "chkParallelPreload")
        if chk_parallel:
            chk_parallel.setVisible(False)
        parallel_options = self._ui.findChild(QWidget, "parallelOptions")
        if parallel_options:
            parallel_options.setVisible(False)

        # 加载 LLM 配置
        self._load_llm_config()

        # 加载模板列表
        self._load_template_list()

    def _load_llm_config(self) -> None:
        """加载 LLM 配置"""
        from vibeocr.models.llm_config import LLMConfig
        import json

        config_path = self._project_root / "config" / "llm_config.json"
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._llm_config = LLMConfig.from_dict(data)
            except Exception as e:
                logging.warning(f"加载 LLM 配置失败: {e}")
                self._llm_config = LLMConfig()
        else:
            self._llm_config = LLMConfig()

        # 更新 UI
        self._update_llm_config_ui()

    def _save_llm_config(self) -> None:
        """保存 LLM 配置"""
        import json

        config_path = self._project_root / "config" / "llm_config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(self._llm_config.to_dict(), f, ensure_ascii=False, indent=2)
            logging.info("LLM 配置已保存")
        except Exception as e:
            logging.error(f"保存 LLM 配置失败: {e}")

            raise

    def _update_llm_config_ui(self) -> None:
        """更新 LLM 配置 UI"""
        edit_mllm_url = self._ui.findChild(QWidget, "editMLLMUrl")
        edit_mllm_model = self._ui.findChild(QWidget, "editMLLMModel")
        edit_mllm_api_key = self._ui.findChild(QWidget, "editMLLMApiKey")

        edit_llm_url = self._ui.findChild(QWidget, "editLLMUrl")
        edit_llm_model = self._ui.findChild(QWidget, "editLLMModel")
        edit_llm_api_key = self._ui.findChild(QWidget, "editLLMApiKey")

        if hasattr(self, '_llm_config'):
            # MLLM 配置
            if edit_mllm_url:
                edit_mllm_url.setText(self._llm_config.service_url)
            if edit_mllm_model:
                edit_mllm_model.setText(self._llm_config.model_name)
            if edit_mllm_api_key:
                edit_mllm_api_key.setText(self._llm_config.api_key)

            # LLM 配置
            if edit_llm_url:
                edit_llm_url.setText(self._llm_config.service_url)
            if edit_llm_model:
                edit_llm_model.setText(self._llm_config.model_name)
            if edit_llm_api_key:
                edit_llm_api_key.setText(self._llm_config.api_key)


    def _on_enable_preload_toggled(self, checked: bool) -> None:
        """启用/禁用预加载"""
        preload_options = self._ui.findChild(QWidget, "preloadOptions")
        if preload_options:
            preload_options.setEnabled(checked)
        logging.info(f"[设置] 预加载功能: {'启用' if checked else '禁用'}")

    def _on_preload_now_clicked(self) -> None:
        """立即预加载按钮点击

        使用子进程服务执行预加载和测试图片预热。
        """
        if not self._ocr_ready:
            QMessageBox.warning(self, "无法预加载", "OCR 功能未就绪，请先安装依赖。")
            return

        # 检查子进程服务是否就绪
        if not self._subprocess_manager.is_ready:
            QMessageBox.warning(self, "无法预加载", "OCR 子进程服务尚未就绪，请稍后再试。")
            return

        # 获取选中的管道
        pipelines_to_preload = self._get_selected_preload_pipelines()

        if not pipelines_to_preload:
            QMessageBox.warning(self, "无法预加载", "请至少选择一个要预加载的管道。")
            return

        # 禁用按钮，显示进度
        btn_preload_now = self._ui.findChild(QWidget, "btnPreloadNow")
        if btn_preload_now:
            btn_preload_now.setEnabled(False)

        progress_bar = self._ui.findChild(QWidget, "progressPreload")
        if progress_bar:
            progress_bar.setVisible(True)
            progress_bar.setValue(0)
            progress_bar.setMaximum(len(pipelines_to_preload) * 2)  # 加载 + 预热

        # 延迟导入
        from vibeocr.services.ocr_service import OCRPipeline

        pipeline_names = [p.display_name for p in pipelines_to_preload]
        logging.info(f"[预加载] 开始预加载和预热管道: {pipeline_names}")

        # 更新状态
        self._update_preload_status("正在预加载和预热模型...")

        # 保存状态用于回调
        self._manual_preload_total = len(pipelines_to_preload)

        # 在子进程中执行预加载和预热
        self._start_manual_preload_with_warmup(pipelines_to_preload)

    def _get_selected_preload_pipelines(self) -> list:
        """获取选中的预加载管道"""
        from vibeocr.services.ocr_service import OCRPipeline

        pipelines = []

        chk_ocr = self._ui.findChild(QWidget, "chkPreloadOCR")
        if chk_ocr and chk_ocr.isChecked():
            pipelines.append(OCRPipeline.OCR)

        chk_table = self._ui.findChild(QWidget, "chkPreloadTable")
        if chk_table and chk_table.isChecked():
            pipelines.append(OCRPipeline.TABLE_RECOGNITION)

        chk_formula = self._ui.findChild(QWidget, "chkPreloadFormula")
        if chk_formula and chk_formula.isChecked():
            pipelines.append(OCRPipeline.FORMULA_RECOGNITION)

        chk_structure = self._ui.findChild(QWidget, "chkPreloadStructure")
        if chk_structure and chk_structure.isChecked():
            pipelines.append(OCRPipeline.PP_STRUCTURE_V3)

        chk_paddleocr_vl = self._ui.findChild(QWidget, "chkPreloadPaddleOCRVL")
        if chk_paddleocr_vl and chk_paddleocr_vl.isChecked():
            pipelines.append(OCRPipeline.PADDLEOCR_VL)

        return pipelines



    def _start_manual_preload_with_warmup(self, pipelines: list) -> None:
        """启动手动预加载和预热任务

        先加载管道，然后使用测试图片预热，确保模型真正就绪。

        Args:
            pipelines: 要预加载的管道列表
        """
        class PreloadWithWarmupTask(QRunnable):
            """预加载和预热任务"""
            def __init__(self, service, pipelines, main_window):
                super().__init__()
                self._service = service
                self._pipelines = pipelines
                self._main_window = main_window
                self._progress_bar = main_window._ui.findChild(QWidget, "progressPreload")

            def _update_progress(self, value: int):
                """更新进度条"""
                if self._progress_bar:
                    from PySide6.QtCore import QMetaObject, Qt, Q_ARG
                    QMetaObject.invokeMethod(
                        self._progress_bar,
                        "setValue",
                        Qt.ConnectionType.QueuedConnection,
                        Q_ARG(int, value)
                    )

            def run(self):
                try:
                    total = len(self._pipelines)
                    results = {}

                    for i, pipeline in enumerate(self._pipelines):
                        pipeline_name = pipeline.value if hasattr(pipeline, 'value') else str(pipeline)

                        # 阶段1: 加载管道 (进度 0-50%)
                        logging.info(f"[预加载] ({i+1}/{total}) 加载管道: {pipeline_name}")
                        self._update_progress(i * 2)

                        load_result = self._service.preload_pipelines([pipeline_name])
                        loaded = load_result.get(pipeline_name, False)

                        if loaded:
                            # 阶段2: 使用测试图片预热 (进度 50-100%)
                            logging.info(f"[预热] ({i+1}/{total}) 预热管道: {pipeline_name}")
                            self._update_progress(i * 2 + 1)

                            # 调用 warmup 接口
                            warmup_result = self._service.warmup_pipelines([pipeline_name])
                            warmed = warmup_result.get(pipeline_name, False)

                            results[pipeline_name] = warmed
                            logging.info(f"[预加载] 管道 {pipeline_name} 加载={'成功' if loaded else '失败'}, 预热={'成功' if warmed else '失败'}")
                        else:
                            results[pipeline_name] = False

                    self._update_progress(total * 2)

                    # 在主线程中更新UI
                    from PySide6.QtCore import QMetaObject, Qt, Q_ARG
                    QMetaObject.invokeMethod(
                        self._main_window,
                        "_on_manual_preload_finished",
                        Qt.ConnectionType.QueuedConnection,
                        Q_ARG(object, results)
                    )
                except Exception as e:
                    logging.error(f"[手动预加载] 失败: {e}")
                    QMetaObject.invokeMethod(
                        self._main_window,
                        "_on_manual_preload_finished",
                        Qt.ConnectionType.QueuedConnection,
                        Q_ARG(object, {})
                    )

        # 启动预加载和预热任务
        self._thread_pool.start(PreloadWithWarmupTask(self._subprocess_manager.service, pipelines, self))

    @Slot(dict)
    def _on_manual_preload_finished(self, results: dict) -> None:
        """手动预加载完成"""
        # 恢复按钮
        btn_preload_now = self._ui.findChild(QWidget, "btnPreloadNow")
        if btn_preload_now:
            btn_preload_now.setEnabled(True)

        # 隐藏进度条
        progress_bar = self._ui.findChild(QWidget, "progressPreload")
        if progress_bar:
            progress_bar.setVisible(False)

        # 更新状态
        success_count = sum(1 for v in results.values() if v)
        total_count = len(results)

        if success_count == total_count:
            self._update_preload_status(f"预加载完成，共 {success_count} 个管道")
            self._statusbar.showMessage("模型预加载完成")
        else:
            failed = [k for k, v in results.items() if not v]
            self._update_preload_status(f"预加载部分完成 ({success_count}/{total_count})，失败: {', '.join(failed)}")
            self._statusbar.showMessage(f"预加载部分完成 ({success_count}/{total_count})")

    def _on_refresh_cache_clicked(self) -> None:
        """刷新缓存按钮点击"""
        self._statusbar.showMessage("正在刷新缓存...")
        self._on_refresh_cache()

    def _on_clear_cache_clicked(self) -> None:
        """清除缓存按钮点击"""
        reply = QMessageBox.question(
            self,
            "确认清除缓存",
            "确定要清除缓存吗？\n\n"
            "这将清除依赖检测缓存和模型状态缓存。\n"
            "已下载的模型不会被删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            from vibeocr.machine_cache import clear_cache
            from vibeocr.model_cache_manager import invalidate_cache

            # 清除依赖缓存
            clear_cache(self._project_root)
            # 清除模型缓存
            invalidate_cache(self._project_root)

            self._update_cache_status("缓存已清除")
            self._statusbar.showMessage("缓存已清除")
            logging.info("缓存已手动清除")

    def _update_cache_status(self, status: str = None) -> None:
        """更新缓存状态显示"""
        label_cache_status = self._ui.findChild(QWidget, "labelCacheStatus")
        if not label_cache_status:
            return

        if status:
            label_cache_status.setText(f"缓存状态: {status}")
            return

        # 检查缓存状态
        from vibeocr.machine_cache import is_cache_valid
        from vibeocr.model_cache_manager import load_model_cache

        is_valid, cached_data = is_cache_valid(self._project_root)
        model_cache = load_model_cache(self._project_root)

        status_parts = []

        if is_valid:
            status_parts.append("依赖缓存有效")
        else:
            status_parts.append("依赖缓存无效")

        if model_cache:
            pipelines = model_cache.get("pipelines", {})
            ready_count = sum(1 for v in pipelines.values() if v)
            status_parts.append(f"模型缓存: {ready_count}/{len(pipelines)} 个管道就绪")
        else:
            status_parts.append("模型缓存不存在")

        label_cache_status.setText(f"缓存状态: {' | '.join(status_parts)}")

    def _update_preload_status(self, status: str = None) -> None:
        """更新预加载状态显示"""
        label_preload_status = self._ui.findChild(QWidget, "labelPreloadStatus")
        if not label_preload_status:
            return

        if status:
            label_preload_status.setText(status)
            return

        # 获取预加载状态
        from vibeocr.services.ocr_service import OCRService

        preloaded = OCRService.get_preloaded_pipelines()
        if preloaded:
            label_preload_status.setText(f"已预加载管道: {', '.join(preloaded)}")
        else:
            label_preload_status.setText("尚未预加载")

    def _on_save_llm_config_clicked(self) -> None:
        """保存 LLM 配置按钮点击"""
        from PySide6.QtWidgets import QLineEdit

        from vibeocr.models.llm_config import LLMConfig

        # 从 UI 获取配置值
        edit_mllm_url = self._ui.findChild(QLineEdit, "editMLLMUrl")
        edit_mllm_model = self._ui.findChild(QLineEdit, "editMLLMModel")
        edit_mllm_api_key = self._ui.findChild(QLineEdit, "editMLLMApiKey")

        if edit_mllm_url and edit_mllm_model:
            self._llm_config.service_url = edit_mllm_url.text()
            self._llm_config.model_name = edit_mllm_model.text()
            if edit_mllm_api_key:
                self._llm_config.api_key = edit_mllm_api_key.text()

        self._save_llm_config()
        self._statusbar.showMessage("LLM 配置已保存")

        # 更新信息抽取标签页的 LLM 状态
        if hasattr(self, '_extraction_tab') and self._extraction_tab:
            self._extraction_tab.update_llm_status(mllm_config=self._llm_config)

        logging.info("LLM 配置已保存")

    def _load_template_list(self) -> None:
        """加载模板列表到 UI"""
        from PySide6.QtWidgets import QListWidget
        from vibeocr.models.extraction_template import ExtractionTemplate, DEFAULT_TEMPLATES
        import json

        list_template = self._ui.findChild(QListWidget, "listTemplate")
        if not list_template:
            return

        list_template.clear()

        # 添加预设模板
        for template in DEFAULT_TEMPLATES:
            list_template.addItem(template.name)

        # 加载自定义模板
        config_path = self._project_root / "config" / "templates.json"
        if config_path.exists():
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        templates_data = json.load(f)
                    for template_data in templates_data:
                        template = ExtractionTemplate.from_dict(template_data)
                        list_template.addItem(f"[自定义] {template.name}")
                except Exception as e:
                    logging.warning(f"加载自定义模板失败: {e}")

    def _on_add_template_clicked(self) -> None:
        """添加模板按钮点击"""
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton, QListWidget
        from vibeocr.models.extraction_template import ExtractionTemplate
        import json

        dialog = QDialog(self)
        dialog.setWindowTitle("添加模板")
        dialog.setMinimumSize(300, 200)

        layout = QVBoxLayout(dialog)

        layout.addWidget(QLabel("模板名称:"))
        name_edit = QLineEdit()
        layout.addWidget(name_edit)

        layout.addWidget(QLabel("抽取字段（每行一个）:"))
        keys_edit = QLineEdit()
        keys_edit.setPlaceholderText("字段1\n字段2\n字段3")
        layout.addWidget(keys_edit)

        btn_add = QPushButton("添加")
        layout.addWidget(btn_add)

        def on_add():
            name = name_edit.text.strip()
            keys_text = keys_edit.toPlainText().strip()
            if not name or not keys_text:
                return

            keys = [k.strip() for k in keys_text.split("\n") if k.strip()]
            if not keys:
                return

            # 保存到配置文件
            config_path = self._project_root / "config" / "templates.json"
            templates = []
            if config_path.exists():
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        templates = json.load(f)
                except Exception:
                    pass

            templates.append({"name": name, "keys": keys})

            config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(templates, f, ensure_ascii=False, indent=2)

            # 刷新列表
            self._load_template_list()
            dialog.accept()

        btn_add.clicked.connect(on_add)
        dialog.exec()

    def _on_edit_template_clicked(self) -> None:
        """编辑模板按钮点击"""
        from PySide6.QtWidgets import QListWidget, QMessageBox
        list_template = self._ui.findChild(QListWidget, "listTemplate")
        if not list_template:
            return

        current_item = list_template.currentItem()
        if current_item < 0:
            QMessageBox.warning(self, "提示", "请先选择一个模板")
            return

        # TODO: 实现编辑模板对话框
        QMessageBox.information(self, "提示", "编辑模板功能待实现")

    def _on_delete_template_clicked(self) -> None:
        """删除模板按钮点击"""
        from PySide6.QtWidgets import QListWidget, QMessageBox
        import json

        list_template = self._ui.findChild(QListWidget, "listTemplate")
        if not list_template:
            return

        current_item = list_template.currentItem()
        if current_item < 0:
            QMessageBox.warning(self, "提示", "请先选择一个模板")
            return

        template_name = list_template.item(current_item).text()
        if template_name.startswith("[自定义]"):
            template_name = template_name.replace("[自定义] ", "")
        else:
            QMessageBox.warning(self, "提示", "预设模板不能删除")
            return

        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除模板 '{template_name}' 吗?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            # 从配置文件中删除
            config_path = self._project_root / "config" / "templates.json"
            if config_path.exists():
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        templates = json.load(f)

                    templates = [t for t in templates if t.get("name") != template_name]

                    with open(config_path, "w", encoding="utf-8") as f:
                        json.dump(templates, f, ensure_ascii=False, indent=2)

                    self._load_template_list()
                    self._statusbar.showMessage(f"模板 '{template_name}' 已删除")
                except Exception as e:
                    logging.error(f"删除模板失败: {e}")

    def closeEvent(self, event) -> None:
        """关闭窗口事件"""
        logging.info("正在关闭应用程序...")
        self._closing = True  # 标记正在关闭，防止重复启动 Worker

        # 关闭子进程管理器
        try:
            self._subprocess_manager.shutdown(timeout_ms=3000)
            logging.info("子进程管理器已关闭")
        except Exception as e:
            logging.warning(f"关闭子进程管理器失败: {e}")

        # 清理 OCR 资源
        try:
            from vibeocr.services import USE_SUBPROCESS

            if not USE_SUBPROCESS:
                # 直接模式：清理管道缓存
                from vibeocr.services.ocr_service import OCRService
                OCRService._pipelines.clear()
                logging.info("OCR 管道缓存已清理")
        except Exception as e:
            logging.warning(f"清理 OCR 资源失败: {e}")

        # 然后等待线程池完成（最多3秒）
        if not self._thread_pool.waitForDone(3000):
            logging.warning("部分任务未能在超时时间内完成，强制退出")

        event.accept()
        logging.info("应用程序已关闭")
