"""Main window view logic"""

from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING

from PIL import Image
from PySide6.QtCore import (
    QBuffer,
    QRect,
    QThreadPool,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QAction, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QSpinBox,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from vibeocr import env_manager
from vibeocr.core.constants import WindowsColors
from vibeocr.machine_cache import is_cache_valid
from vibeocr.managers import (
    ConfigManager,
    DependencyManager,
    LayoutManager,
    SubprocessManager,
)
from vibeocr.services.log_service import setup_logging
from vibeocr.ui.ui_main_window import Ui_MainWindowWidget
from vibeocr.utils.qt_async import run_coroutine
from vibeocr.views.batch_recognition_tab import BatchRecognitionTab
from vibeocr.views.clipboard_controller import ClipboardController
from vibeocr.views.settings_page_controller import SettingsPageController
from vibeocr.widgets.console_widget import ConsoleWidget
from vibeocr.widgets.screenshot_edit_window import ScreenshotEditWindow
from vibeocr.widgets.screenshot_widget import ScreenshotWidget
from vibeocr.widgets.toolbar import EdgeToolbar

if TYPE_CHECKING:
    from vibeocr.models.ocr_result import OCRResult

# 延迟导入: OCR 服务模块导入很慢（~33s），延迟到首次使用时导入


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
        self._force_quit = False  # 是否强制退出（而非最小化到托盘）
        self._tray_icon = None  # 系统托盘图标
        self._app_settings = None  # 应用设置

        # 当前 OCR 结果（用于复制操作）
        self._current_ocr_result: OCRResult | None = None

        # 依赖管理器
        self._dependency_manager = DependencyManager(self._project_root, self)
        self._dependency_manager.check_completed.connect(
            self._on_dependency_check_finished
        )

        # 布局管理器
        self._layout_manager = LayoutManager(ConfigManager.instance())

        # 子进程管理器
        self._subprocess_manager = SubprocessManager(self._project_root, self)
        self._subprocess_manager.service_ready.connect(self._on_subprocess_worker_ready)
        self._subprocess_manager.progress_update.connect(self._on_subprocess_progress)
        self._subprocess_manager.preload_finished.connect(self._on_preload_finished)

        self._setup_ui()

        # 恢复布局
        self._restore_layout()
        self._setup_console()
        self._create_menus()
        self._connect_signals()
        self._thread_pool = QThreadPool()

        # 创建边缘工具栏
        self._edge_toolbar = EdgeToolbar()
        self._edge_toolbar.screenshot_requested.connect(self._on_screenshot)
        self._edge_toolbar.show_main_requested.connect(self._show_main_window)

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

    @Slot(str)
    def _on_log_status_update(self, message: str) -> None:
        """日志状态更新槽（用于显示 Worker 节点输出）"""
        # 只在 OCR 服务未就绪时更新状态栏（启动/预加载阶段）
        if not self._subprocess_manager.is_ready:
            self._statusbar.showMessage(message)

    def _setup_ui(self) -> None:
        """设置UI"""
        # QMainWindow 需要一个 centralWidget 来放置主内容
        self._central_widget = QWidget()
        self.setCentralWidget(self._central_widget)

        # 使用预编译的 Python UI 文件，设置到 centralWidget 上
        self._ui = Ui_MainWindowWidget()
        self._ui.setupUi(self._central_widget)

        # 设置主分割器 stretch（tabWidget 占据剩余空间，consoleContainer 保持固定）
        self._ui.mainSplitter.setStretchFactor(0, 1)  # tabWidget
        self._ui.mainSplitter.setStretchFactor(1, 0)  # consoleContainer

        # 设置 tabSettings 的 sizePolicy，使其可以缩小
        # 这样 TabWidget 不会因为设置页面的内容太多而变得很大
        self._ui.tabSettings.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored
        )

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

        # 将设置标签页移到最后
        self._move_settings_tab_to_end()

    def _move_settings_tab_to_end(self) -> None:
        """将设置标签页移动到最后位置"""
        tab_widget = self._ui.tabWidget
        settings_tab = self._ui.tabSettings

        # 获取设置标签页的当前索引
        settings_index = tab_widget.indexOf(settings_tab)
        if settings_index >= 0:
            # 使用 tabBar().moveTab 将设置标签页移到最后
            tab_widget.tabBar().moveTab(settings_index, tab_widget.count() - 1)
            logging.debug("设置标签页已移到最后")

    def _restore_layout(self) -> None:
        """恢复窗口和分割器布局"""
        # 恢复主窗口几何信息
        geometry = self._layout_manager.get_main_window_geometry()
        if geometry:
            self.restoreGeometry(geometry)
            logging.info("已恢复窗口布局")

        # 恢复主分割器状态（tabWidget 与控制台的比例）
        if hasattr(self._ui, "mainSplitter"):
            state = self._layout_manager.get_splitter_state("main_splitter")
            if state:
                self._ui.mainSplitter.restoreState(state)
                logging.info("已恢复主分割器状态")

        # 恢复 OCR 标签页分割器状态
        if hasattr(self._ui, "ocrSplitter"):
            state = self._layout_manager.get_splitter_state("ocr_tab")
            if state:
                self._ui.ocrSplitter.restoreState(state)
                logging.info("已恢复 OCR 分割器状态")
            else:
                # 无持久化数据时，设置默认尺寸：预览框 400px，结果面板占剩余
                total_width = self._ui.ocrSplitter.width()
                if total_width > 0:
                    self._ui.ocrSplitter.setSizes([400, total_width - 400])
                else:
                    self._ui.ocrSplitter.setSizes([400, 500])

    def _save_layout(self) -> None:
        """保存窗口和分割器布局"""
        # 保存主窗口几何信息
        self._layout_manager.set_main_window_geometry(self.saveGeometry())

        # 保存主分割器状态（tabWidget 与控制台的比例）
        if hasattr(self._ui, "mainSplitter"):
            self._layout_manager.set_splitter_state(
                "main_splitter", self._ui.mainSplitter.saveState()
            )

        # 保存 OCR 标签页分割器状态
        if hasattr(self._ui, "ocrSplitter"):
            self._layout_manager.set_splitter_state(
                "ocr_tab", self._ui.ocrSplitter.saveState()
            )

        # 保存批量识别标签页分割器状态
        if hasattr(self, "_batch_tab") and self._batch_tab:
            self._batch_tab.save_layout()

        # 保存到文件
        self._layout_manager.save()

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
            OCRPipeline.OCR: self._ui.btnPipelineOCR,
            OCRPipeline.TABLE_RECOGNITION: self._ui.btnPipelineTable,
            OCRPipeline.FORMULA_RECOGNITION: self._ui.btnPipelineFormula,
            OCRPipeline.DOCUMENT_PARSING: self._ui.btnPipelineStructure,
        }

        # 预处理按钮
        self._btn_orient = self._ui.btnOrient
        self._btn_unwarp = self._ui.btnUnwarp
        self._btn_textline = self._ui.btnTextline
        self._btn_layout = self._ui.btnLayout

        # 子产线按钮
        self._btn_sub_table = self._ui.btnSubTable
        self._btn_sub_formula = self._ui.btnSubFormula
        self._btn_sub_seal = self._ui.btnSubSeal
        self._btn_sub_chart = self._ui.btnSubChart

        # 应用样式并连接信号
        for pipeline, btn in self._pipeline_buttons.items():
            if btn:
                btn.setStyleSheet(button_style)
                btn.clicked.connect(
                    lambda checked, p=pipeline: self._on_pipeline_clicked(p)
                )

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
        for btn in [
            self._btn_orient,
            self._btn_unwarp,
            self._btn_textline,
            self._btn_layout,
        ]:
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
        for btn in [
            self._btn_sub_table,
            self._btn_sub_formula,
            self._btn_sub_seal,
            self._btn_sub_chart,
        ]:
            if btn:
                btn.setStyleSheet(sub_button_style)

        # 初始化子产线选项（默认隐藏，仅版面解析时显示）
        self._sub_pipeline_widget = self._ui.subPipelineOptions

        # 初始化按钮可见性
        self._update_button_visibility(OCRPipeline.OCR)

        # 从持久化选项恢复按钮状态
        self._restore_options_from_preferences()

        # 创建截图组件
        self._screenshot_widget = ScreenshotWidget()

        # 创建截图编辑窗口
        self._edit_window = ScreenshotEditWindow()

    def _init_batch_tab(self) -> None:
        """初始化批量识别标签页"""
        # 创建批量识别标签页
        self._batch_tab = BatchRecognitionTab()

        # 传递布局管理器
        self._batch_tab.set_layout_manager(self._layout_manager)

        # 添加到标签页控件
        self._ui.tabWidget.addTab(self._batch_tab, "批量识别")
        logging.debug("批量识别标签页已添加")

    def _on_pipeline_clicked(self, pipeline) -> None:
        """管道按钮点击时更新 UI 并同步到全局选项"""
        self._update_button_visibility(pipeline)
        self._sync_options_to_preferences()

    def _update_button_visibility(self, pipeline) -> None:
        """根据管道类型更新按钮可见性"""
        # 使用管道的 value 属性进行比较（避免导入 OCRPipeline）
        pipeline_value = pipeline.value if hasattr(pipeline, "value") else pipeline

        # 子产线选项：文档解析时显示
        if self._sub_pipeline_widget:
            self._sub_pipeline_widget.setVisible(
                pipeline_value == "MinerU"
            )

        # 文本行方向按钮：仅通用 OCR 时显示
        if self._btn_textline:
            self._btn_textline.setVisible(pipeline_value == "OCR")

        # 版面检测按钮：仅表格和公式管道时显示
        if self._btn_layout:
            self._btn_layout.setVisible(
                pipeline_value in ["table_recognition", "formula_recognition"]
            )

        # 子产线按钮：仅文档解析时显示
        sub_pipeline_buttons = [
            self._btn_sub_table,
            self._btn_sub_formula,
            self._btn_sub_seal,
            self._btn_sub_chart,
        ]
        for btn in sub_pipeline_buttons:
            if btn:
                btn.setVisible(pipeline_value == "MinerU")

        # 更新子产线标签文字
        label_sub = self._ui.labelSubPipelines
        if label_sub:
            label_sub.setVisible(pipeline_value == "MinerU")

        logging.debug(f"管道切换为 {pipeline.display_name}")

    def _get_current_pipeline(self):
        """获取当前选中的管道"""
        # 延迟导入: OCRPipeline 枚举
        from vibeocr.services.ocr_service import OCRPipeline

        for pipeline, btn in self._pipeline_buttons.items():
            if btn and btn.isChecked():
                return pipeline
        return OCRPipeline.OCR

    def _restore_options_from_preferences(self) -> None:
        """从 OCRPreferences 恢复按钮状态"""
        from vibeocr.utils.ocr_preferences import OCRPreferences

        prefs = OCRPreferences.instance(ConfigManager.instance())
        options = prefs.get_options()
        self._sync_buttons_from_options(options)

        # 监听选项变化信号，同步按钮组
        prefs.options_changed.connect(self._on_preferences_options_changed)

    def _sync_buttons_from_options(self, options) -> None:
        """从 OCROptions 同步按钮组状态（不触发信号）"""
        from vibeocr.services.ocr_service import OCRPipeline

        # 同步管道按钮
        pipeline = options.pipeline
        if isinstance(pipeline, str):
            pipeline = OCRPipeline(pipeline)
        for p, btn in self._pipeline_buttons.items():
            if btn:
                btn.blockSignals(True)
                btn.setChecked(p == pipeline)
                btn.blockSignals(False)
        self._update_button_visibility(pipeline)

        # 同步预处理按钮
        if self._btn_orient:
            self._btn_orient.blockSignals(True)
            self._btn_orient.setChecked(options.use_doc_orientation_classify)
            self._btn_orient.blockSignals(False)
        if self._btn_unwarp:
            self._btn_unwarp.blockSignals(True)
            self._btn_unwarp.setChecked(options.use_doc_unwarping)
            self._btn_unwarp.blockSignals(False)
        if self._btn_textline:
            self._btn_textline.blockSignals(True)
            self._btn_textline.setChecked(options.use_textline_orientation)
            self._btn_textline.blockSignals(False)

        # 同步子产线按钮
        if self._btn_sub_table:
            self._btn_sub_table.blockSignals(True)
            self._btn_sub_table.setChecked(getattr(options, "enable_table", True))
            self._btn_sub_table.blockSignals(False)
        if self._btn_sub_formula:
            self._btn_sub_formula.blockSignals(True)
            self._btn_sub_formula.setChecked(getattr(options, "enable_formula", True))
            self._btn_sub_formula.blockSignals(False)
        if self._btn_sub_seal:
            self._btn_sub_seal.blockSignals(True)
            self._btn_sub_seal.setChecked(False)
            self._btn_sub_seal.blockSignals(False)
        if self._btn_sub_chart:
            self._btn_sub_chart.blockSignals(True)
            self._btn_sub_chart.setChecked(False)
            self._btn_sub_chart.blockSignals(False)

    def _sync_options_to_preferences(self) -> None:
        """将当前按钮组状态同步到 OCRPreferences"""
        from vibeocr.utils.ocr_preferences import OCRPreferences

        options = self._build_options_from_ui()
        OCRPreferences.instance().set_options(options)

    def _on_preferences_options_changed(self, options) -> None:
        """OCRPreferences 选项变化时同步按钮组"""
        self._sync_buttons_from_options(options)

    def _setup_console(self) -> None:
        """初始化控制台"""
        # 创建控制台控件
        self._console = ConsoleWidget(self)

        # 连接低置信度计数变化信号
        self._console.low_confidence_count_changed.connect(
            self._on_low_confidence_changed
        )

        # 将控制台添加到 UI 中的容器
        container = self._ui.consoleContainer
        if container:
            container_layout = container.layout()
            if not container_layout:
                container_layout = QVBoxLayout(container)
                container_layout.setContentsMargins(0, 0, 0, 0)
            container_layout.addWidget(self._console)

        # 配置日志，保存 handler 以便连接状态信号
        self._log_handler = setup_logging(self._console.append_log)  # type: ignore[arg-type]
        # 连接日志状态信号到状态栏更新
        self._log_handler.status_signal.connect(self._on_log_status_update)
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

        # 截图组件 - 使用新的 selection_done 信号进入编辑流程
        self._screenshot_widget.selection_done.connect(self._on_selection_done)

        # 编辑窗口信号
        self._edit_window.confirmed.connect(self._on_edit_confirmed)
        self._edit_window.cancelled.connect(self._on_edit_cancelled)

        # 预览组件
        self._ui.previewWidget.screenshot_requested.connect(self._on_screenshot)
        self._ui.previewWidget.file_open_requested.connect(
            self._on_open_file_from_preview
        )

        # 剪贴板控制器
        self._clipboard_controller = ClipboardController(
            status_callback=self._statusbar.showMessage,
            copy_button=self._ui.btnCopyRich,
        )
        self._ui.btnCopyRich.clicked.connect(self._clipboard_controller.copy_rich)
        self._ui.btnCopyMarkdown.clicked.connect(
            self._clipboard_controller.copy_markdown
        )
        self._ui.btnCopyPlain.clicked.connect(self._clipboard_controller.copy_plain)

        # 设置页面控制器
        # 注意：传入 self (QMainWindow) 而不是 self._ui (Ui_MainWindowWidget)
        # 因为 findChild 是 QObject 的方法，Ui_MainWindowWidget 没有此方法
        self._settings_controller = SettingsPageController(
            ui=self,
            project_root=self._project_root,
            status_callback=self._statusbar.showMessage,
            ocr_ready_callback=lambda: self._ocr_ready,
            subprocess_manager=self._subprocess_manager,
            preload_complete_callback=self._on_preload_complete,
        )
        self._settings_controller.connect_signals()

        # 选项按钮变化时同步到全局选项
        for btn in [
            self._btn_orient,
            self._btn_unwarp,
            self._btn_textline,
            self._btn_layout,
            self._btn_sub_table,
            self._btn_sub_formula,
            self._btn_sub_seal,
            self._btn_sub_chart,
        ]:
            if btn:
                btn.toggled.connect(self._sync_options_to_preferences)

    def _on_preload_complete(self) -> None:
        """预加载完成回调"""
        self._preload_complete = True

    def _try_load_cache(self) -> None:
        """尝试从缓存加载依赖检测结果"""
        is_valid, cached_data = is_cache_valid(self._project_root)
        if is_valid and cached_data:
            dependencies = cached_data.get("dependencies", {})
            # 检查关键依赖
            paddlepaddle_ok = dependencies.get("paddlepaddle", False)
            paddlex_ok = dependencies.get("paddlex", False)
            mineru_ok = dependencies.get("mineru", False)
            if paddlepaddle_ok and paddlex_ok and mineru_ok:
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

            # 设置 MinerU 直接批量服务到批量识别标签页
            if hasattr(self, "_batch_tab") and self._batch_tab:
                from vibeocr.services.mineru_batch_service import MinerUBatchService

                mineru_batch = MinerUBatchService()
                self._batch_tab.set_ocr_service(mineru_batch)
                logging.info("[MainWindow] 批量识别标签页已连接 MinerU 直接批量服务")

            # 子进程就绪后，触发预加载（如果配置了预加载管道）
            # 预加载完成后再显示"OCR 服务已就绪"
            from vibeocr.managers.config_manager import ConfigManager

            pipelines = ConfigManager.instance().get_preload_pipelines()
            if pipelines:
                self._statusbar.showMessage("正在预热 OCR 模型...")
                self._start_subprocess_preload()
            else:
                # 没有配置预加载管道，直接显示就绪
                self._statusbar.showMessage("OCR 服务已就绪")
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
                "请查看控制台日志了解详情。",
            )

    @Slot(str)
    def _on_subprocess_progress(self, stage: str) -> None:
        """子进程启动进度回调"""
        self._statusbar.showMessage(f"正在启动 OCR 服务: {stage}")

    @Slot(dict)
    def _on_preload_finished(self, results: dict) -> None:
        """预加载完成回调"""
        success_count = sum(1 for v in results.values() if v)
        total_count = len(results)
        if success_count > 0:
            self._statusbar.showMessage(
                f"OCR 服务已就绪（{success_count}/{total_count} 个模型已预热）"
            )
        else:
            self._statusbar.showMessage("OCR 服务已就绪")
        logging.info(f"[MainWindow] 预加载完成: {results}")

    def _start_subprocess_preload(self) -> None:
        """在子进程中预加载用户配置的管道"""
        if not self._subprocess_manager.is_ready:
            return

        # 获取用户配置的预加载管道
        from vibeocr.managers.config_manager import ConfigManager

        pipelines = ConfigManager.instance().get_preload_pipelines()

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
                self, "提示", "OCR功能将不可用。\n您可以稍后通过菜单重新安装。"
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
            QMessageBox.information(
                self, "安装成功", "OCR依赖安装成功，现在可以使用OCR功能。"
            )
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

    @Slot()
    def _on_open_file_from_preview(self) -> None:
        """从预览区域打开文件（支持图片和 PDF）"""
        if not self._check_ocr_ready():
            return
        logging.info("打开文件对话框（图片/PDF）")

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择文件",
            "",
            "图片/PDF 文件 (*.png *.jpg *.jpeg *.bmp *.gif *.tiff *.pdf);;所有文件 (*)",
        )
        if not file_path:
            return

        if file_path.lower().endswith(".pdf"):
            self._load_pdf_as_pixmap(file_path)
        else:
            pixmap = QPixmap(file_path)
            if not pixmap.isNull():
                self._ui.previewWidget.set_pixmap(pixmap)
                self._run_ocr(pixmap)

    def _load_pdf_as_pixmap(self, file_path: str) -> None:
        """将 PDF 第一页转换为 QPixmap 并加载到预览"""
        try:
            from PySide6.QtPdf import QPdfDocument

            doc = QPdfDocument(self)
            error = doc.load(file_path)
            if error != QPdfDocument.Error.None_:
                QMessageBox.warning(
                    self, "打开失败", f"无法加载 PDF 文件:\n{file_path}"
                )
                return

            # 等待加载完成
            if doc.status() != QPdfDocument.Status.Ready:
                from PySide6.QtCore import QEventLoop

                loop = QEventLoop()
                doc.statusChanged.connect(
                    lambda status: (
                        loop.quit()
                        if status
                        in (
                            QPdfDocument.Status.Ready,
                            QPdfDocument.Status.Error,
                        )
                        else None
                    )
                )
                loop.exec()

            if doc.status() != QPdfDocument.Status.Ready:
                QMessageBox.warning(
                    self, "打开失败", f"PDF 加载失败: {doc.error()}"
                )
                return

            if doc.pageCount() == 0:
                QMessageBox.warning(self, "打开失败", "PDF 文件没有页面")
                return

            page_size = doc.pagePointSize(0)
            scale = 2.0  # 2x 渲染以获得清晰度
            render_size = page_size * scale

            qimage = doc.render(0, render_size.toSize())
            if qimage and not qimage.isNull():
                pixmap = QPixmap.fromImage(qimage)
                self._ui.previewWidget.set_pixmap(pixmap)
                self._run_ocr(pixmap)
                logging.info(
                    f"PDF 加载成功: {file_path}, "
                    f"页面尺寸: {page_size.width():.0f}x{page_size.height():.0f}pt"
                )
            else:
                QMessageBox.warning(self, "渲染失败", "PDF 页面渲染失败")

            doc.close()

        except ImportError:
            QMessageBox.warning(
                self,
                "不支持",
                "当前版本不支持 PDF 文件。\n请安装 PySide6 QtPdf 模块。",
            )
            logging.warning("QtPdf 模块不可用，无法加载 PDF")
        except Exception as e:
            QMessageBox.warning(
                self, "打开失败", f"加载 PDF 文件时出错:\n{e}"
            )
            logging.error(f"加载 PDF 失败: {e}", exc_info=True)

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
                "OCR依赖检测中，请稍候...\n\n检测完成后才能使用截图识别功能。",
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
        """截图完成（向后兼容，打开图片等直接流程使用）"""
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

    @Slot(QPixmap, QRect)
    def _on_selection_done(self, pixmap: QPixmap, screen_rect: QRect) -> None:
        """框选完成，打开编辑窗口"""
        if pixmap.isNull():
            self._screenshot_widget.finish_capture()
            self.showNormal()
            self.activateWindow()
            return

        logging.info(
            f"框选完成: {pixmap.width()}x{pixmap.height()} 像素, "
            f"选区 ({screen_rect.x()}, {screen_rect.y()}, "
            f"{screen_rect.width()}x{screen_rect.height()})"
        )
        self._screenshot_widget.finish_capture()
        self._edit_window.open_editor(pixmap, screen_rect)

    @Slot(QPixmap, object)
    def _on_edit_confirmed(self, pixmap: QPixmap, options) -> None:
        """编辑完成，同步选项到主界面并执行 OCR"""
        self._edit_window.hide()
        self.showNormal()
        self.activateWindow()

        # 将编辑窗口的选项同步到全局状态（信号会自动同步按钮组）
        if options:
            from vibeocr.utils.ocr_preferences import OCRPreferences

            OCRPreferences.instance().set_options(options)

        if not pixmap.isNull():
            dpr = pixmap.devicePixelRatio()
            width = pixmap.width()
            height = pixmap.height()
            logging.info(f"编辑确认: {width}x{height} 像素, DPR={dpr}")

            self._ui.previewWidget.set_pixmap(pixmap)
            self._run_ocr(pixmap, options)

    @Slot()
    def _on_edit_cancelled(self) -> None:
        """取消编辑"""
        self._edit_window.hide()
        self.showNormal()
        self.activateWindow()

    def _build_options_from_ui(self):
        """从主窗口 UI 按钮状态构建 OCROptions"""
        from vibeocr.services.ocr_service import OCROptions

        pipeline = self._get_current_pipeline()

        use_orient = self._btn_orient.isChecked() if self._btn_orient else False
        use_unwarp = self._btn_unwarp.isChecked() if self._btn_unwarp else False
        use_textline = self._btn_textline.isChecked() if self._btn_textline else True

        use_table = self._btn_sub_table.isChecked() if self._btn_sub_table else True
        use_formula = (
            self._btn_sub_formula.isChecked() if self._btn_sub_formula else True
        )

        return OCROptions(
            pipeline=pipeline,
            use_doc_orientation_classify=use_orient,
            use_doc_unwarping=use_unwarp,
            use_textline_orientation=use_textline,
            enable_table=use_table,
            enable_formula=use_formula,
        )

    def _run_ocr(self, pixmap: QPixmap, options=None) -> None:
        """Execute OCR recognition

        Supports two modes:
        1. Async subprocess mode (default): Execute OCR via subprocess with asyncio
        2. Direct mode: Execute OCR directly in main thread (for debugging)

        Args:
            pixmap: 待识别的图像
            options: OCR 选项（如果为 None，从主窗口 UI 按钮状态读取）
        """
        # Lazy import: OCR related types
        from vibeocr.services import USE_SUBPROCESS
        from vibeocr.services.ocr_service import OCRPipeline

        logging.info("Starting OCR recognition")
        self._ui.textResult.clear()
        self._ui.textResult.setPlaceholderText("Recognizing...")
        self._statusbar.showMessage("Recognizing...")

        # Force UI update to show "Recognizing" message
        QApplication.processEvents()

        if options is None:
            # 从主窗口 UI 按钮状态读取选项
            options = self._build_options_from_ui()

        pipeline = options.pipeline
        logging.info(
            f"OCR 管道: {pipeline.display_name}, "
            f"预处理: 方向={options.use_doc_orientation_classify}, "
            f"去弯={options.use_doc_unwarping}"
        )
        if pipeline == OCRPipeline.DOCUMENT_PARSING:
            logging.info(
                f"文档解析: 方法={options.parse_method}, "
                f"公式={options.enable_formula}, "
                f"表格={options.enable_table}"
            )

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
                logging.info(
                    f"[主线程OCR] 图像尺寸: {pil_image.size}, 数组形状: {image_array.shape}"
                )
                logging.info("[主线程OCR] 开始识别...")
                from vibeocr.services import get_ocr_service

                ocr_service = get_ocr_service()  # type: ignore[assignment]
                result = ocr_service.recognize(image_array, options)  # type: ignore[call-arg]
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
            logging.info(
                f"[异步OCR] get_ocr_service() 返回: {type(ocr_service).__name__}"
            )

            # 再次检查关闭标志
            if self._closing:
                logging.info("[异步OCR] 应用程序正在关闭，取消识别")
                return

            # 检查服务是否就绪
            logging.info("[异步OCR] 检查服务是否就绪...")
            if hasattr(ocr_service, "is_ready"):
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
        self._clipboard_controller.set_result(result)

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
        if result.raw_text or result.has_rich_content:
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
                # 没有置信度信息，显示字符数
                char_count = (
                    len(result.raw_text)
                    if result.raw_text
                    else len(result.markdown_text)
                    if result.markdown_text
                    else 0
                )
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
        from vibeocr.managers.dependency_manager import DependencyCheckTask
        from vibeocr.model_cache_manager import update_cache as update_model_cache

        logging.info("正在清除依赖缓存...")
        clear_cache(self._project_root)
        update_model_cache()
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
        """关闭窗口事件

        如果启用了托盘最小化且不是强制退出，则隐藏到托盘而不关闭。
        """
        # 检查是否应最小化到托盘
        if (
            not self._force_quit
            and self._app_settings
            and self._app_settings.minimize_to_tray
            and self._tray_icon is not None
        ):
            event.ignore()
            self.hide()
            logging.info("主窗口已最小化到系统托盘")
            return

        logging.info("正在关闭应用程序...")

        # 关闭边缘工具栏
        if hasattr(self, "_edge_toolbar") and self._edge_toolbar:
            self._edge_toolbar.close()

        # 保存应用设置
        if self._app_settings:
            self._app_settings.save()

        # 保存布局（在 _closing = True 之前）
        self._save_layout()

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

        # 清理 MinerU API 进程
        try:
            from vibeocr.services.mineru_service import MinerUService

            if MinerUService._api_process is not None:
                MinerUService().shutdown()
                logging.info("MinerU API 服务已关闭")
        except Exception as e:
            logging.warning(f"关闭 MinerU API 服务失败: {e}")

        # 然后等待线程池完成（最多3秒）
        if not self._thread_pool.waitForDone(3000):
            logging.warning("部分任务未能在超时时间内完成，强制退出")

        event.accept()
        logging.info("应用程序已关闭")

    # ============================================================
    # 系统托盘与边缘工具栏集成
    # ============================================================

    def set_app_settings(self, app_settings) -> None:
        """设置应用设置对象（由 main.py 调用）"""

        self._app_settings = app_settings  # type: ignore[assignment]
        self.apply_app_settings()
        self._init_app_settings_ui()

    def set_tray_icon(self, tray_icon) -> None:
        """设置系统托盘图标（由 main.py 调用）"""
        self._tray_icon = tray_icon

    def apply_app_settings(self) -> None:
        """应用当前设置到工具栏等组件"""
        if not self._app_settings:
            return
        # 边缘工具栏
        self._edge_toolbar.set_auto_hide(self._app_settings.auto_hide_toolbar)
        self._edge_toolbar.set_hide_delay(self._app_settings.hide_delay_ms)
        if self._app_settings.auto_hide_toolbar:
            self._edge_toolbar.show()
        # 更新设置页面复选框
        self._sync_app_settings_ui()

    def _init_app_settings_ui(self) -> None:
        """初始化设置页面中的应用设置复选框"""
        self._chk_auto_hide = self.findChild(QCheckBox, "chkAutoHideToolbar")
        self._chk_tray = self.findChild(QCheckBox, "chkMinimizeToTray")
        self._chk_autostart = self.findChild(QCheckBox, "chkAutoStart")
        self._spin_hide_delay = self.findChild(QSpinBox, "spinHideDelay")

        if self._chk_auto_hide:
            self._chk_auto_hide.toggled.connect(self._on_auto_hide_toggled)
        if self._chk_tray:
            self._chk_tray.toggled.connect(self._on_minimize_to_tray_toggled)
        if self._chk_autostart:
            self._chk_autostart.toggled.connect(self._on_autostart_toggled)
        if self._spin_hide_delay:
            self._spin_hide_delay.valueChanged.connect(self._on_hide_delay_changed)

        self._save_delay_timer = QTimer(self)
        self._save_delay_timer.setSingleShot(True)
        self._save_delay_timer.timeout.connect(self._do_save_hide_delay)

        self._sync_app_settings_ui()

    def _sync_app_settings_ui(self) -> None:
        """将当前设置值同步到设置页面 UI"""
        if not self._app_settings:
            return

        if self._chk_auto_hide:
            self._chk_auto_hide.blockSignals(True)
            self._chk_auto_hide.setChecked(self._app_settings.auto_hide_toolbar)
            self._chk_auto_hide.blockSignals(False)
        if self._chk_tray:
            self._chk_tray.blockSignals(True)
            self._chk_tray.setChecked(self._app_settings.minimize_to_tray)
            self._chk_tray.blockSignals(False)
        if self._chk_autostart:
            self._chk_autostart.blockSignals(True)
            self._chk_autostart.setChecked(self._app_settings.auto_start)
            self._chk_autostart.blockSignals(False)
        if self._spin_hide_delay:
            self._spin_hide_delay.blockSignals(True)
            self._spin_hide_delay.setValue(self._app_settings.hide_delay_ms)
            self._spin_hide_delay.setEnabled(self._app_settings.auto_hide_toolbar)
            self._spin_hide_delay.blockSignals(False)

    @Slot(bool)
    def _on_auto_hide_toggled(self, checked: bool) -> None:
        """自动隐藏复选框切换"""
        if self._app_settings:
            self._app_settings.auto_hide_toolbar = checked
            self._app_settings.save()
        self._edge_toolbar.set_auto_hide(checked)
        if checked:
            self._edge_toolbar.show()
        if self._spin_hide_delay:
            self._spin_hide_delay.setEnabled(checked)
        logging.info(f"自动隐藏工具栏: {'启用' if checked else '禁用'}")

    @Slot(int)
    def _on_hide_delay_changed(self, value: int) -> None:
        """隐藏延迟值改变（防抖保存）"""
        if self._app_settings:
            self._app_settings.hide_delay_ms = value
        self._edge_toolbar.set_hide_delay(value)
        self._save_delay_timer.start(300)

    def _do_save_hide_delay(self) -> None:
        """防抖延迟后实际保存设置"""
        if self._app_settings:
            self._app_settings.save()
            logging.info(f"工具栏隐藏延迟: {self._app_settings.hide_delay_ms}ms")

    @Slot(bool)
    def _on_minimize_to_tray_toggled(self, checked: bool) -> None:
        """最小化到托盘复选框切换"""
        if self._app_settings:
            self._app_settings.minimize_to_tray = checked
            self._app_settings.save()
        logging.info(f"最小化到系统托盘: {'启用' if checked else '禁用'}")

    @Slot(bool)
    def _on_autostart_toggled(self, checked: bool) -> None:
        """开机自启动复选框切换"""
        from vibeocr.utils.autostart import set_autostart

        success = set_autostart(checked)
        if success and self._app_settings:
            self._app_settings.auto_start = checked
            self._app_settings.save()
            logging.info(f"开机自启动: {'启用' if checked else '禁用'}")
        elif not success:
            logging.warning("设置开机自启动失败")
            # 恢复复选框状态
            if self._chk_autostart:
                self._chk_autostart.blockSignals(True)
                self._chk_autostart.setChecked(not checked)
                self._chk_autostart.blockSignals(False)
            QMessageBox.warning(
                self, "设置失败", "设置开机自启动失败，请检查系统权限。"
            )

    def _show_main_window(self) -> None:
        """显示并激活主窗口（由工具栏触发）"""
        self.showNormal()
        self.activateWindow()
        self.raise_()
