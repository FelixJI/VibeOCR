"""Main window view logic"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Optional, TYPE_CHECKING
from queue import Queue

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
    Slot, QThreadPool, QRunnable, Signal, QObject, QTimer, QBuffer, QThread, QMutex, QWaitCondition
)
from PySide6.QtGui import QPixmap, QAction
from PySide6.QtUiTools import QUiLoader

from vibeocr.widgets.preview_widget import PreviewWidget
from vibeocr.widgets.screenshot_widget import ScreenshotWidget
from vibeocr.widgets.console_widget import ConsoleWidget
from vibeocr.services.log_service import setup_logging
from vibeocr.models.ocr_result import OCRResult
from vibeocr import env_manager
from vibeocr.machine_cache import is_cache_valid

# 延迟导入: OCR 服务模块导入很慢（~33s），延迟到首次使用时导入
if TYPE_CHECKING:
    from vibeocr.services.ocr_service import OCRService, OCRPreset, OCRPipeline, OCROptions


# ============================================================
# 专用 OCR 工作线程
# 解决 PaddlePaddle GPU 上下文的线程亲和性问题
# ============================================================

class OCRWorker(QObject):
    """专用 OCR 工作线程的处理器

    所有 OCR 操作都在这个对象所在的线程中执行，
    确保 PaddlePaddle GPU 上下文的创建和使用在同一线程中。

    使用方法：
    1. 创建 QThread
    2. 将 OCRWorker 移动到该线程
    3. 通过信号/槽与主线程通信
    """

    # 输出信号（从工作线程发送到主线程）
    ocr_finished = Signal(object)  # OCR 完成 (OCRResult)
    ocr_error = Signal(str)  # OCR 失败
    preload_progress = Signal(str, int, int)  # 预加载进度 (pipeline_name, current, total)
    preload_finished = Signal(dict)  # 预加载完成 {pipeline_name: success}
    ready = Signal()  # 工作线程已就绪

    # 输入信号（从主线程发送到工作线程）
    request_ocr = Signal(bytes, object)  # 请求 OCR (image_data, options)
    request_preload = Signal(list, bool, int)  # 请求预加载 (pipelines, parallel, max_workers)

    def __init__(self) -> None:
        super().__init__()
        self._ocr_service = None
        self._is_initialized = False
        # 注意：信号连接移到 moveToThread 之后，在 OCRWorkerThread.start() 中建立

    def _ensure_initialized(self) -> None:
        """确保 OCR 服务已初始化（在工作线程中调用）"""
        if self._is_initialized:
            return

        # 延迟导入: OCR 服务模块（在工作线程中首次导入）
        from vibeocr.services.ocr_service import OCRService
        self._ocr_service = OCRService()
        self._is_initialized = True
        logging.info("[OCRWorker] OCR 服务已初始化")

    @Slot(bytes, object)
    def _do_ocr(self, image_data: bytes, options: "OCROptions | None") -> None:
        """执行 OCR 识别（在工作线程中调用）"""
        try:
            logging.info("[OCRWorker] 开始执行 OCR...")
            self._ensure_initialized()

            # 延迟导入
            from vibeocr.services.ocr_service import OCROptions

            # 从字节数据创建 PIL Image
            logging.info("[OCRWorker] 解码图像数据...")
            buffer = io.BytesIO(image_data)
            pil_image = Image.open(buffer)
            logging.info(f"[OCRWorker] 图像尺寸: {pil_image.size}, 模式: {pil_image.mode}")

            # 转换为 numpy 数组
            import numpy as np
            image_array = np.array(pil_image)
            logging.info(f"[OCRWorker] 转换为 numpy 数组: shape={image_array.shape}")

            # 执行 OCR
            actual_options = options if options is not None else OCROptions()
            logging.info("[OCRWorker] 调用 OCR 服务...")
            result = self._ocr_service.recognize(image_array, actual_options)
            logging.info(f"[OCRWorker] OCR 完成，结果: {len(result.raw_text)} 字符")

            self.ocr_finished.emit(result)
            logging.info("[OCRWorker] 结果已发送")
        except Exception as e:
            logging.error(f"[OCRWorker] OCR 执行失败: {e}", exc_info=True)
            self.ocr_error.emit(str(e))

    @Slot(list, bool, int)
    def _do_preload(self, pipelines: list, parallel: bool, max_workers: int) -> None:
        """执行管道预加载（在工作线程中调用）"""
        try:
            self._ensure_initialized()

            from vibeocr.services.ocr_service import OCRService

            # 设置进度回调
            def on_progress(pipeline_name: str, current: int, total: int) -> None:
                self.preload_progress.emit(pipeline_name, current, total)

            OCRService.set_preload_progress_callback(on_progress)

            # 执行预加载
            if parallel:
                results = OCRService.preload_pipelines_parallel(pipelines, max_workers)
            else:
                results = OCRService.preload_pipelines_sequential(pipelines)

            self.preload_finished.emit(results)
        except Exception as e:
            logging.error(f"[OCRWorker] 预加载失败: {e}")
            self.preload_finished.emit({})
        finally:
            # 清理回调
            from vibeocr.services.ocr_service import OCRService
            OCRService.set_preload_progress_callback(None)

    @Slot()
    def on_thread_started(self) -> None:
        """线程启动时调用"""
        logging.info("[OCRWorker] 工作线程已启动")
        self.ready.emit()


class OCRWorkerThread:
    """OCR 工作线程管理器

    封装 QThread 和 OCRWorker 的创建和管理。
    """

    def __init__(self) -> None:
        self._thread: Optional[QThread] = None
        self._worker: Optional[OCRWorker] = None
        self._ready = False

    def start(self) -> None:
        """启动工作线程"""
        if self._thread is not None:
            return

        self._thread = QThread()
        self._thread.setObjectName("OCRWorkerThread")
        self._worker = OCRWorker()

        # 将 worker 移动到工作线程
        self._worker.moveToThread(self._thread)

        # 在 moveToThread 之后建立信号连接，使用显式的 QueuedConnection 确保跨线程调用正确
        from PySide6.QtCore import Qt
        self._worker.request_ocr.connect(
            self._worker._do_ocr,
            Qt.ConnectionType.QueuedConnection
        )
        self._worker.request_preload.connect(
            self._worker._do_preload,
            Qt.ConnectionType.QueuedConnection
        )

        # 连接线程启动信号
        self._thread.started.connect(self._worker.on_thread_started)

        # 启动线程
        self._thread.start()
        logging.info("[OCRWorkerThread] 工作线程已创建")

    @property
    def worker(self) -> Optional[OCRWorker]:
        """获取 worker 对象"""
        return self._worker

    @property
    def is_ready(self) -> bool:
        """检查线程是否就绪"""
        return self._ready

    def set_ready(self, ready: bool) -> None:
        """设置就绪状态"""
        self._ready = ready

    def stop(self) -> None:
        """停止工作线程"""
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(5000)  # 等待最多 5 秒
            self._thread = None
            self._worker = None
            logging.info("[OCRWorkerThread] 工作线程已停止")


# ============================================================
# 旧的 OCR 任务类（保留用于向后兼容，但不再使用）
# ============================================================

class OCRSignals(QObject):
    """OCR任务信号（用于线程安全通信）"""

    finished = Signal(object)  # 识别完成 (OCRResult)
    error = Signal(str)  # 识别失败


class OCRTask(QRunnable):
    """OCR识别任务（已弃用，保留用于向后兼容）"""

    def __init__(self, image_data: bytes, options: "OCROptions | None" = None) -> None:
        super().__init__()
        self._image_data = image_data
        self._options = options
        self.signals = OCRSignals()

    def run(self) -> None:
        """执行OCR识别"""
        try:
            from vibeocr.services.ocr_service import OCRService, OCROptions

            buffer = io.BytesIO(self._image_data)
            pil_image = Image.open(buffer)

            import numpy as np
            image_array = np.array(pil_image)

            ocr = OCRService()
            options = self._options or OCROptions()
            result = ocr.recognize(image_array, options)
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


class PipelinePreloadSignals(QObject):
    """管道预加载信号"""

    progress = Signal(str, int, int)  # (pipeline_name, current, total)
    finished = Signal(dict)  # {pipeline_name: success}


class PipelinePreloadTask(QRunnable):
    """管道预加载任务（在后台线程执行）"""

    def __init__(
        self,
        pipelines: list,
        parallel: bool = True,
        max_workers: int = 2
    ) -> None:
        super().__init__()
        self._pipelines = pipelines
        self._parallel = parallel
        self._max_workers = max_workers
        self.signals = PipelinePreloadSignals()

    def run(self) -> None:
        """执行管道预加载"""
        try:
            # 延迟导入: OCR 服务模块
            from vibeocr.services.ocr_service import OCRService

            # 设置进度回调
            def on_progress(pipeline_name: str, current: int, total: int) -> None:
                self.signals.progress.emit(pipeline_name, current, total)

            OCRService.set_preload_progress_callback(on_progress)

            # 执行预加载
            if self._parallel:
                results = OCRService.preload_pipelines_parallel(
                    self._pipelines,
                    max_workers=self._max_workers
                )
            else:
                results = OCRService.preload_pipelines_sequential(self._pipelines)

            self.signals.finished.emit(results)

        except Exception as e:
            logging.error(f"[预加载] 管道预加载任务失败: {e}")
            self.signals.finished.emit({})
        finally:
            # 清理回调
            from vibeocr.services.ocr_service import OCRService
            OCRService.set_preload_progress_callback(None)


class MainWindow(QMainWindow):
    """主窗口"""

    # 状态更新信号（用于线程安全的状态栏更新）
    _status_update_signal = Signal(str)
    # 预加载进度信号
    _preload_progress_signal = Signal(str, int, int)  # (pipeline_name, current, total)
    _preload_finished_signal = Signal(dict)  # {pipeline_name: success}

    def __init__(self) -> None:
        super().__init__()
        self._project_root = env_manager.get_project_root()
        self._ocr_ready = False
        self._dependency_check_complete = False  # 依赖检测是否完成
        self._preload_complete = False  # 预加载是否完成

        # 创建专用 OCR 工作线程
        self._ocr_worker_thread = OCRWorkerThread()

        self._setup_ui()
        self._setup_console()
        self._create_menus()
        self._connect_signals()
        self._thread_pool = QThreadPool()

        # 设置 OCRService 状态回调（用于显示模型下载进度）
        self._setup_ocr_status_callback()

        # 连接预加载信号
        self._preload_progress_signal.connect(self._on_preload_progress)
        self._preload_finished_signal.connect(self._on_preload_finished)

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
        # 延迟导入: OCRPipeline 枚举
        from vibeocr.services.ocr_service import OCRPipeline

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

    def _on_pipeline_clicked(self, pipeline) -> None:
        """管道按钮点击时更新 UI"""
        self._update_button_visibility(pipeline)

    def _update_button_visibility(self, pipeline) -> None:
        """根据管道类型更新按钮可见性"""
        # 使用管道的 value 属性进行比较（避免导入 OCRPipeline）
        pipeline_value = pipeline.value if hasattr(pipeline, 'value') else pipeline

        # 子产线选项：仅版面解析时显示
        if self._sub_pipeline_widget:
            self._sub_pipeline_widget.setVisible(pipeline_value == "PP-StructureV3")

        # 文本行方向按钮：仅通用 OCR 时显示
        if self._btn_textline:
            self._btn_textline.setVisible(pipeline_value == "OCR")

        # 版面检测按钮：仅表格和公式管道时显示
        if self._btn_layout:
            self._btn_layout.setVisible(pipeline_value in ["table_recognition", "formula_recognition"])

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

            # 启动专用 OCR 工作线程
            self._start_ocr_worker()
        else:
            self._ocr_ready = False
            missing_str = ", ".join(missing)
            self._statusbar.showMessage(f"OCR功能未就绪: {missing_str}")

    def _start_ocr_worker(self) -> None:
        """启动专用 OCR 工作线程"""
        if self._ocr_worker_thread.is_ready:
            return

        logging.info("[MainWindow] 正在启动 OCR 工作线程...")

        # 先启动工作线程（这会创建 worker）
        self._ocr_worker_thread.start()

        # 启动后再连接工作线程的信号
        worker = self._ocr_worker_thread.worker
        if worker:
            from PySide6.QtCore import Qt
            # 使用 QueuedConnection 确保信号在接收者线程中处理
            worker.ready.connect(self._on_ocr_worker_ready, Qt.ConnectionType.QueuedConnection)
            worker.ocr_finished.connect(self._on_ocr_finished, Qt.ConnectionType.QueuedConnection)
            worker.ocr_error.connect(self._on_ocr_error, Qt.ConnectionType.QueuedConnection)
            worker.preload_progress.connect(self._on_preload_progress_from_worker, Qt.ConnectionType.QueuedConnection)
            worker.preload_finished.connect(self._on_preload_finished_from_worker, Qt.ConnectionType.QueuedConnection)
            logging.info("[MainWindow] OCR 工作线程信号已连接")

            # 如果 worker 已经初始化完成（ready 信号可能已经发射过了），手动调用就绪处理
            if worker._is_initialized:
                logging.info("[MainWindow] Worker 已初始化，手动触发就绪处理")
                self._on_ocr_worker_ready()
        else:
            logging.error("[MainWindow] OCR 工作线程启动失败: worker 为 None")

    @Slot()
    def _on_ocr_worker_ready(self) -> None:
        """OCR 工作线程就绪"""
        self._ocr_worker_thread.set_ready(True)
        logging.info("[MainWindow] OCR 工作线程已就绪")

        # 工作线程就绪后，启动管道预加载
        self._start_pipeline_preload()

    @Slot(str, int, int)
    def _on_preload_progress_from_worker(self, pipeline_name: str, current: int, total: int) -> None:
        """从工作线程接收预加载进度"""
        # 延迟导入: OCRPipeline 枚举
        from vibeocr.services.ocr_service import OCRPipeline

        # 获取显示名称
        display_name = pipeline_name
        for p in OCRPipeline:
            if p.value == pipeline_name:
                display_name = p.display_name
                break

        message = f"正在预热 {display_name} 模型 ({current}/{total})..."
        self._statusbar.showMessage(message)
        logging.info(f"[预加载] {message}")

    @Slot(dict)
    def _on_preload_finished_from_worker(self, results: dict) -> None:
        """从工作线程接收预加载完成"""
        self._preload_complete = True

        success_count = sum(1 for v in results.values() if v)
        total_count = len(results)

        if success_count == total_count:
            self._statusbar.showMessage("OCR 模型预热完成")
            logging.info(f"[预加载] 完成: {success_count}/{total_count} 个管道加载成功")
        else:
            failed = [k for k, v in results.items() if not v]
            self._statusbar.showMessage(f"OCR 模型预热部分完成 ({success_count}/{total_count})")
            logging.warning(f"[预加载] 部分失败: {failed}")

        # 更新设置页面的预加载状态
        self._update_preload_status()

    def _start_pipeline_preload(self) -> None:
        """启动管道预加载（根据设置页面配置）

        使用专用 OCR 工作线程执行预加载，确保 PaddlePaddle GPU 上下文
        的创建和使用都在同一个线程中。
        """
        if self._preload_complete:
            logging.info("[预加载] 已完成，跳过")
            return

        # 检查工作线程是否就绪
        if not self._ocr_worker_thread.is_ready:
            logging.info("[预加载] 等待 OCR 工作线程就绪...")
            return

        # 检查是否启用预加载
        chk_enable_preload = self._ui.findChild(QWidget, "chkEnablePreload")
        if chk_enable_preload and not chk_enable_preload.isChecked():
            logging.info("[预加载] 预加载功能已禁用，跳过")
            self._update_preload_status("预加载已禁用")
            return

        # 获取选中的预加载管道
        pipelines_to_preload = self._get_selected_preload_pipelines()

        if not pipelines_to_preload:
            logging.info("[预加载] 未选择任何管道，跳过")
            self._update_preload_status("未选择预加载管道")
            return

        # 获取预加载设置
        chk_parallel = self._ui.findChild(QWidget, "chkParallelPreload")
        spin_workers = self._ui.findChild(QWidget, "spinMaxWorkers")

        parallel = chk_parallel.isChecked() if chk_parallel else False
        max_workers = spin_workers.value() if spin_workers else 2

        # 延迟导入: OCRPipeline 枚举
        from vibeocr.services.ocr_service import OCRPipeline

        pipeline_names = [p.display_name for p in pipelines_to_preload]
        logging.info(f"[预加载] 开始预加载管道: {pipeline_names}")
        logging.info(f"[预加载] 并行模式: {parallel}, 并行数: {max_workers}")
        self._statusbar.showMessage(f"正在预热模型 ({len(pipelines_to_preload)} 个管道)...")
        self._update_preload_status("正在预加载模型...")

        # 在专用 OCR 工作线程中执行预加载（通过信号触发）
        worker = self._ocr_worker_thread.worker
        if worker:
            worker.request_preload.emit(pipelines_to_preload, parallel, max_workers)

    @Slot(str, int, int)
    def _on_preload_progress(self, pipeline_name: str, current: int, total: int) -> None:
        """预加载进度回调"""
        # 延迟导入: OCRPipeline 枚举
        from vibeocr.services.ocr_service import OCRPipeline

        # 获取显示名称
        display_name = pipeline_name
        for p in OCRPipeline:
            if p.value == pipeline_name:
                display_name = p.display_name
                break

        message = f"正在预热 {display_name} 模型 ({current}/{total})..."
        self._statusbar.showMessage(message)
        logging.info(f"[预加载] {message}")

    @Slot(dict)
    def _on_preload_finished(self, results: dict) -> None:
        """预加载完成回调"""
        self._preload_complete = True

        success_count = sum(1 for v in results.values() if v)
        total_count = len(results)

        if success_count == total_count:
            self._statusbar.showMessage("OCR 模型预热完成")
            logging.info(f"[预加载] 完成: {success_count}/{total_count} 个管道加载成功")
        else:
            failed = [k for k, v in results.items() if not v]
            self._statusbar.showMessage(f"OCR 模型预热部分完成 ({success_count}/{total_count})")
            logging.warning(f"[预加载] 部分失败: {failed}")

        # 更新设置页面的预加载状态
        self._update_preload_status()

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
        1. Subprocess mode (default): Execute OCR via subprocess to avoid UI freezing
        2. Direct mode: Execute OCR directly in main thread (for debugging)

        Mode switching controlled by environment variable VIBEOCR_USE_SUBPROCESS.
        """
        # Lazy import: OCR related types
        from vibeocr.services.ocr_service import OCROptions, OCRPipeline
        from vibeocr.services import USE_SUBPROCESS, get_ocr_service

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

        try:
            # 将 QPixmap 转换为图像数据
            buffer = QBuffer()
            buffer.open(QBuffer.OpenModeFlag.ReadWrite)
            pixmap.save(buffer, "PNG")
            image_data = bytes(buffer.data().data())
            buffer.close()

            # 根据模式选择执行方式
            if USE_SUBPROCESS:
                # 子进程模式
                logging.info("[子进程OCR] 开始识别...")
                ocr_service = get_ocr_service()
                result = ocr_service.recognize(image_data, options)
                logging.info(f"[子进程OCR] 识别完成，{len(result.raw_text)} 字符")
            else:
                # 直接模式（用于调试）
                pil_image = Image.open(io.BytesIO(image_data))
                import numpy as np
                image_array = np.array(pil_image)
                logging.info(f"[主线程OCR] 图像尺寸: {pil_image.size}, 数组形状: {image_array.shape}")
                logging.info("[主线程OCR] 开始识别...")
                ocr_service = get_ocr_service()
                result = ocr_service.recognize(image_array, options)
                logging.info(f"[主线程OCR] 识别完成，{len(result.raw_text)} 字符")

            # 调用完成回调
            self._on_ocr_finished(result)

        except Exception as e:
            logging.error(f"OCR 识别失败: {e}", exc_info=True)
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

        chk_parallel = self._ui.findChild(QWidget, "chkParallelPreload")
        if chk_parallel:
            chk_parallel.toggled.connect(self._on_parallel_preload_toggled)

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

        # 初始化设置页面状态
        self._init_settings_page()

    def _init_settings_page(self) -> None:
        """初始化设置页面状态"""
        # 更新缓存状态
        self._update_cache_status()

        # 更新预加载状态
        self._update_preload_status()

        # 初始化并行选项的可见性
        chk_parallel = self._ui.findChild(QWidget, "chkParallelPreload")
        parallel_options = self._ui.findChild(QWidget, "parallelOptions")
        if parallel_options:
            parallel_options.setVisible(chk_parallel.isChecked() if chk_parallel else False)

    def _on_enable_preload_toggled(self, checked: bool) -> None:
        """启用/禁用预加载"""
        preload_options = self._ui.findChild(QWidget, "preloadOptions")
        if preload_options:
            preload_options.setEnabled(checked)
        logging.info(f"[设置] 预加载功能: {'启用' if checked else '禁用'}")

    def _on_parallel_preload_toggled(self, checked: bool) -> None:
        """启用/禁用并行加载"""
        parallel_options = self._ui.findChild(QWidget, "parallelOptions")
        if parallel_options:
            parallel_options.setVisible(checked)
        logging.info(f"[设置] 并行加载: {'启用' if checked else '禁用'}")

    def _on_preload_now_clicked(self) -> None:
        """立即预加载按钮点击

        使用专用 OCR 工作线程执行预加载。
        """
        if not self._ocr_ready:
            QMessageBox.warning(self, "无法预加载", "OCR 功能未就绪，请先安装依赖。")
            return

        # 检查工作线程是否就绪
        if not self._ocr_worker_thread.is_ready:
            QMessageBox.warning(self, "无法预加载", "OCR 工作线程尚未就绪，请稍后再试。")
            return

        # 获取选中的管道
        pipelines_to_preload = self._get_selected_preload_pipelines()

        if not pipelines_to_preload:
            QMessageBox.warning(self, "无法预加载", "请至少选择一个要预加载的管道。")
            return

        # 获取预加载设置
        chk_parallel = self._ui.findChild(QWidget, "chkParallelPreload")
        spin_workers = self._ui.findChild(QWidget, "spinMaxWorkers")

        parallel = chk_parallel.isChecked() if chk_parallel else False
        max_workers = spin_workers.value() if spin_workers else 2

        # 禁用按钮，显示进度
        btn_preload_now = self._ui.findChild(QWidget, "btnPreloadNow")
        if btn_preload_now:
            btn_preload_now.setEnabled(False)

        progress_bar = self._ui.findChild(QWidget, "progressPreload")
        if progress_bar:
            progress_bar.setVisible(True)
            progress_bar.setValue(0)
            progress_bar.setMaximum(len(pipelines_to_preload))

        # 延迟导入
        from vibeocr.services.ocr_service import OCRPipeline

        pipeline_names = [p.display_name for p in pipelines_to_preload]
        logging.info(f"[预加载] 开始预加载管道: {pipeline_names}")
        logging.info(f"[预加载] 并行模式: {parallel}, 并行数: {max_workers}")

        # 更新状态
        self._update_preload_status("正在预加载模型...")

        # 保存状态用于回调
        self._manual_preload_total = len(pipelines_to_preload)

        # 在专用 OCR 工作线程中执行预加载
        worker = self._ocr_worker_thread.worker
        if worker:
            # 临时连接手动预加载的信号
            worker.preload_progress.connect(self._on_manual_preload_progress)
            worker.preload_finished.connect(self._on_manual_preload_finished)
            worker.request_preload.emit(pipelines_to_preload, parallel, max_workers)

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

        return pipelines

    @Slot(str, int, int)
    def _on_manual_preload_progress(self, pipeline_name: str, current: int, total: int) -> None:
        """手动预加载进度"""
        progress_bar = self._ui.findChild(QWidget, "progressPreload")
        if progress_bar:
            progress_bar.setValue(current)

        # 延迟导入: OCRPipeline 枚举
        from vibeocr.services.ocr_service import OCRPipeline

        # 获取显示名称
        display_name = pipeline_name
        for p in OCRPipeline:
            if p.value == pipeline_name:
                display_name = p.display_name
                break

        self._update_preload_status(f"正在加载 {display_name} ({current}/{total})...")

    @Slot(dict)
    def _on_manual_preload_finished(self, results: dict) -> None:
        """手动预加载完成"""
        # 断开临时信号连接
        worker = self._ocr_worker_thread.worker
        if worker:
            try:
                worker.preload_progress.disconnect(self._on_manual_preload_progress)
                worker.preload_finished.disconnect(self._on_manual_preload_finished)
            except RuntimeError:
                pass  # 信号可能已断开

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

    def closeEvent(self, event) -> None:
        """关闭窗口事件"""
        logging.info("正在关闭应用程序...")

        # 停止专用 OCR 工作线程
        self._ocr_worker_thread.stop()

        # 等待线程池完成（最多5秒）
        if not self._thread_pool.waitForDone(5000):
            logging.warning("部分任务未能在超时时间内完成，强制退出")

        # 清理 OCR 服务资源
        try:
            from vibeocr.services import USE_SUBPROCESS

            if USE_SUBPROCESS:
                # 子进程模式：关闭子进程服务
                from vibeocr.services.ocr_service_subprocess import OCRServiceSubprocess
                if OCRServiceSubprocess._instance is not None:
                    OCRServiceSubprocess._instance.shutdown()
                    logging.info("OCR 子进程服务已关闭")
            else:
                # 直接模式：清理管道缓存
                from vibeocr.services.ocr_service import OCRService
                OCRService._pipelines.clear()
                logging.info("OCR 管道缓存已清理")
        except Exception as e:
            logging.warning(f"清理 OCR 资源失败: {e}")

        event.accept()
        logging.info("应用程序已关闭")
