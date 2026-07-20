"""Main window view logic"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import (
    QPoint,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QSpinBox,
    QStatusBar,
    QWidget,
)

from vibeocr import env_manager
from vibeocr.machine_cache import is_cache_valid, update_cache_field
from vibeocr.pyside.runtime import (
    ConfigManager,
    DependencyManager,
    LayoutManager,
    SubprocessManager,
    setup_logging,
)
from vibeocr.ui.ui_main_window import Ui_MainWindowWidget
from vibeocr.views.clipboard_controller import ClipboardController
from vibeocr.views.settings_page_controller import SettingsPageController
from vibeocr.views.tabs.single_recognition_tab import SingleRecognitionTab
from vibeocr.widgets.screen_capture_overlay import ScreenCaptureOverlay
from vibeocr.widgets.toast_widget import show_toast
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
        self._preload_in_progress = False  # 预加载是否进行中（状态栏三态区分）
        self._closing = False  # 是否正在关闭（防止关闭时重复启动 Worker）
        self._force_quit = False  # 是否强制退出（而非最小化到托盘）
        self._tray_icon = None  # 系统托盘图标
        self._ocr_status_callback_fn: Any = None  # OCR 状态回调
        self._app_settings = None  # 应用设置

        # 懒加载 Tab：批量/二维码/PDF 在启动期仅插占位空页，首次切换时才真正构造，
        # 把 MainWindow 构造耗时从 ~1.5s 砍到 <0.5s（首屏仅需单次识别 Tab）。
        # 构造后属性由 None 变为真实 widget；下游已用 hasattr/getattr 防御 None。
        self._batch_tab: Any = None
        self._qrcode_tab: Any = None
        self._pdf_tab: Any = None
        # 占位页 -> 构造方法 的映射，供 currentChanged 触发懒构造
        self._lazy_tab_builders: dict[int, tuple[str, Any]] = {}
        # OCR 服务句柄缓存（_on_subprocess_worker_ready 时写入），供懒构造的 Tab
        # 构造后补发服务注入
        self._paddlex_service: Any = None
        self._mineru_batch_service: Any = None

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
        self._subprocess_manager.preload_progress.connect(self._on_preload_progress)
        self._subprocess_manager.recognition_queued.connect(self._on_recognition_queued)

        self._setup_ui()

        # 恢复布局
        self._restore_layout()
        self._setup_console()
        self._init_about_tab()
        self._connect_signals()

        # 创建边缘工具栏
        self._edge_toolbar = EdgeToolbar()
        self._edge_toolbar.screenshot_requested.connect(self._on_screenshot)
        self._edge_toolbar.show_main_requested.connect(self._show_main_window)
        self._edge_toolbar.position_changed.connect(self._on_toolbar_position_changed)
        self._edge_toolbar.pipeline_screenshot_requested.connect(
            self._on_pipeline_screenshot
        )

        # 设置 OCRService 状态回调（用于显示模型下载进度）
        self._setup_ocr_status_callback()

        # 启动时立即读取缓存，如果有有效缓存则直接更新状态
        self._try_load_cache()
        # 异步检查嵌入式依赖（在UI显示后）
        QTimer.singleShot(100, self._check_embedded_dependencies)
        # 异步计算运行时 GPU 能力并广播到所有 PreprocessOptionsWidget（CPU 后端下
        # 禁用文档解析/VL 管道）。延迟到 UI 显示后，避免 nvidia-smi 阻塞启动。
        QTimer.singleShot(200, self._apply_gpu_gating_to_all)
        # 后台校验 OCR_CHECK_MODULES 与 pyproject.toml 一致性（仅开发期告警，
        # 防止新增 OCR 依赖时漏更新清单/漏 bump CACHE_VERSION）。延迟 2s 避免抢启动资源。
        QTimer.singleShot(2000, self._check_dep_check_consistency)

    def _apply_gpu_gating_to_all(self) -> None:
        """计算运行时 GPU 能力并对所有已创建的 PreprocessOptionsWidget 应用门控。

        get_runtime_gpu_capability 首次调用可能触发 nvidia-smi（~5s，无缓存时），
        故用 singleShot 延迟到 UI 显示后执行，避免阻塞启动。结果写入进程级缓存，
        此后懒加载构造的 PreprocessOptionsWidget（如截图 inline 面板）会自动从
        缓存读取并应用（见其 __init__）。
        """
        if self._closing:
            return
        try:
            has_gpu = env_manager.get_runtime_gpu_capability(self._project_root)
        except Exception:
            logging.exception("[GPU 门控] 获取运行时 GPU 能力失败，跳过")
            return

        from vibeocr.widgets.preprocess_options_widget import (
            PreprocessOptionsWidget,
        )
        from vibeocr.widgets.screenshot_options_widget import (
            ScreenshotOptionsWidget,
        )

        for widget in self.findChildren(PreprocessOptionsWidget):
            widget.apply_gpu_gating(has_gpu)
        for widget in self.findChildren(ScreenshotOptionsWidget):
            widget.apply_gpu_gating(has_gpu)

    def _check_dep_check_consistency(self) -> None:
        """后台校验 OCR_CHECK_MODULES 与 pyproject.toml 一致性。

        仅开发期诊断工具：发现漂移时 logger.warning，不阻塞、不报错。
        防止新增 OCR 依赖时漏更新 OCR_CHECK_MODULES 或漏 bump CACHE_VERSION，
        导致检测漏项或旧缓存误判。
        """
        if self._closing:
            return
        try:
            from vibeocr.pyside.runtime import validate_dep_check_consistency

            warnings = validate_dep_check_consistency(self._project_root)
        except Exception:
            logging.debug("[依赖清单] 一致性校验异常，跳过", exc_info=True)
            return
        for w in warnings:
            logging.warning("[依赖清单] %s", w)

    def _setup_ocr_status_callback(self) -> None:
        """设置 OCR 状态回调，用于在状态栏显示模型下载进度"""

        def on_ocr_status(stage: str, message: str) -> None:
            """OCR 状态回调（可能从后台线程调用）"""
            self._status_update_signal.emit(message)

        self._status_update_signal.connect(self._on_status_update)
        # 延迟到首次使用时才 import OCRService（避免启动时 ~0.1s 的 import 开销）
        self._ocr_status_callback_fn = on_ocr_status

    def _ensure_ocr_status_callback(self) -> None:
        """WorkerHost 通过 RPC 事件报告状态；不再注册进程内 OCR 回调。"""
        self._ocr_status_callback_fn = None

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

        # 用 SingleRecognitionTab 替换 tabOCR
        self._single_tab = SingleRecognitionTab()
        tab_index = self._ui.tabWidget.indexOf(self._ui.tabOCR)
        self._ui.tabWidget.removeTab(tab_index)
        self._ui.tabWidget.insertTab(tab_index, self._single_tab, "单次识别")

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
        # 启动初期即提示，避免状态栏空白让用户以为程序无响应
        self._statusbar.showMessage("正在检测 OCR 环境...")

        # 初始化 OCR 预设下拉框（包含截图组件和复制提示的初始化）
        self._init_preset_combo()

        # 批量/二维码/PDF 标签页：先插占位空页，首次切换时才真正构造（懒加载，
        # 避免启动期同步构建三个重型 Tab 拖慢窗口出现）。
        self._add_lazy_tab("批量识别", "batch", self._build_batch_tab)
        self._add_lazy_tab("二维码", "qrcode", self._build_qrcode_tab)
        self._add_lazy_tab("PDF 处理", "pdf", self._build_pdf_tab)

        # 将设置标签页移到最后
        self._move_settings_tab_to_end()

        # removeTab(当前tab) 后 Qt 自动选中 tabSettings，需要重置回第一个 tab
        self._ui.tabWidget.setCurrentIndex(0)

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
            logging.debug("已恢复窗口布局")

        # 恢复上次选中的标签页
        tab_index = self._layout_manager.get_tab_index()
        if tab_index is not None and 0 <= tab_index < self._ui.tabWidget.count():
            self._ui.tabWidget.setCurrentIndex(tab_index)
            logging.debug(f"已恢复标签页索引: {tab_index}")

        # 恢复单次识别标签页分割器状态
        if hasattr(self, "_single_tab") and hasattr(self._single_tab, "_splitter"):
            state = self._layout_manager.get_splitter_state("ocr_tab")
            if state:
                self._single_tab._splitter.restoreState(state)
                logging.debug("已恢复 OCR 分割器状态")
            else:
                total_width = self._single_tab._splitter.width()
                if total_width > 0:
                    self._single_tab._splitter.setSizes([400, total_width - 400])
                else:
                    self._single_tab._splitter.setSizes([400, 500])

        # 恢复二维码生成标签页分割器状态
        if hasattr(self, "_qrcode_tab") and self._qrcode_tab:
            state = self._layout_manager.get_splitter_state("qrcode_tab")
            if state:
                self._qrcode_tab._splitter.restoreState(state)
                logging.debug("已恢复二维码分割器状态")

    def _save_layout(self) -> None:
        """保存窗口和分割器布局"""
        # 保存主窗口几何信息
        self._layout_manager.set_main_window_geometry(self.saveGeometry())

        # 保存单次识别标签页分割器状态
        if hasattr(self, "_single_tab") and hasattr(self._single_tab, "_splitter"):
            self._layout_manager.set_splitter_state(
                "ocr_tab", self._single_tab._splitter.saveState()
            )

        # 保存批量识别标签页分割器状态
        if hasattr(self, "_batch_tab") and self._batch_tab:
            self._batch_tab.save_layout()

        # 保存二维码生成标签页分割器状态
        if hasattr(self, "_qrcode_tab") and self._qrcode_tab:
            self._layout_manager.set_splitter_state(
                "qrcode_tab", self._qrcode_tab._splitter.saveState()
            )

        # 保存当前标签页索引
        self._layout_manager.set_tab_index(self._ui.tabWidget.currentIndex())

        # 保存到文件
        self._layout_manager.save()

    def _init_preset_combo(self) -> None:
        """初始化截图组件"""
        self._overlay = ScreenCaptureOverlay()
        # 记录截图开始前主窗口的最小化状态，用于截图结束后恢复窗口状态。
        self._main_window_minimized_before_capture = False

    def _add_lazy_tab(
        self, title: str, role: str, builder: Any, at_end: bool = False
    ) -> None:
        """插入一个占位空页，注册懒构造回调。

        启动期只插一个空 QWidget（零成本），首次切换到该页时由
        ``_on_lazy_tab_changed`` 触发 ``builder`` 真正构造内容并替换占位页。

        - ``at_end=False``（默认）：占位页插在设置页之前，保持功能页顺序
          （批量→二维码→PDF→设置）。
        - ``at_end=True``：占位页插到末尾（设置页之后），用于关于页（关于页
          居末尾，符合「关于」居末的惯例）。

        Args:
            title: 标签页标题。
            role: 角色标识（"batch"/"qrcode"/"pdf"/"about"），用于构造后回填属性名。
            builder: 无参可调用，返回真实 tab widget。
            at_end: 是否插到末尾（设置页之后）。
        """
        placeholder = QWidget()
        tw = self._ui.tabWidget
        if at_end:
            insert_at = tw.count()
        else:
            settings_idx = tw.indexOf(self._ui.tabSettings)
            insert_at = settings_idx if settings_idx >= 0 else tw.count()
        idx = tw.insertTab(insert_at, placeholder, title)
        self._lazy_tab_builders[idx] = (role, builder)

    def _build_batch_tab(self) -> Any:
        """构造批量识别标签页（懒加载时调用）。

        import 延迟到此处：BatchRecognitionTab 模块顶层拉起 pdf_session_manager
        → pydantic/httpx 等重链，启动期不需要，避免拖慢 main_window 模块加载。
        """
        from vibeocr.views.batch_recognition_tab import BatchRecognitionTab

        tab = BatchRecognitionTab()
        tab.set_layout_manager(self._layout_manager)
        return tab

    def _build_qrcode_tab(self) -> Any:
        """构造二维码标签页（懒加载时调用）。"""
        from vibeocr.views.tabs.qrcode_tab import QrcodeTab

        return QrcodeTab()

    def _build_pdf_tab(self) -> Any:
        """构造 PDF 处理标签页（懒加载时调用）。

        import 延迟到此处：PdfTab 顶层拉起 pdf_session_manager → pydantic/httpx，
        启动期不需要。
        """
        from vibeocr.views.tabs.pdf_tab import PdfTab

        return PdfTab()

    def _on_lazy_tab_changed(self, index: int) -> None:
        """tabWidget.currentChanged 回调：若目标页是未构造的占位页，则懒构造并替换。

        构造完成后恢复该页的分割器布局（与 _restore_layout 中即时恢复一致）。
        构造失败时保留占位页并记录错误，不阻塞应用。
        """
        entry = self._lazy_tab_builders.pop(index, None)
        if entry is None:
            return  # 已构造或非懒加载页
        role, builder = entry
        try:
            widget = builder()
        except Exception:
            logging.exception(f"[懒加载] 构造 {role} 标签页失败")
            # 失败时放回映射，允许用户再次切换重试
            self._lazy_tab_builders[index] = (role, builder)
            return

        # 回填属性，使下游 hasattr/getattr 防御逻辑生效
        attr_map = {
            "batch": "_batch_tab",
            "qrcode": "_qrcode_tab",
            "pdf": "_pdf_tab",
            "about": "_about_tab",
        }
        attr = attr_map.get(role)
        if attr:
            setattr(self, attr, widget)

        # 用真实 widget 替换占位页（保持同一 index 与标题）。
        # 替换期间临时阻塞信号：removeTab/insertTab 会改变 currentIndex 从而
        # 再次触发 currentChanged，避免误触发其他懒加载页或重复进入本回调。
        tab_widget = self._ui.tabWidget
        title = tab_widget.tabText(index)
        prev_blocked = tab_widget.blockSignals(True)
        try:
            tab_widget.removeTab(index)
            tab_widget.insertTab(index, widget, title)
            tab_widget.setCurrentIndex(index)
        finally:
            tab_widget.blockSignals(prev_blocked)
        logging.debug(f"懒加载标签页已构造: {role}")

        # 构造后恢复分割器布局（与 _restore_layout 逻辑对齐）
        self._restore_lazy_tab_layout(role, widget)

        # 若 OCR 服务已就绪，需把服务句柄下发给懒构造的 tab（原本在
        # _on_subprocess_worker_ready 时同步下发，懒构造的 tab 错过了那次下发）
        self._maybe_dispatch_ocr_service_to_lazy_tab(role, widget)

    def _restore_lazy_tab_layout(self, role: str, widget: Any) -> None:
        """懒构造的 tab 在替换占位页后恢复其分割器布局。

        batch 的分割器恢复由 set_layout_manager 内部完成（构造时已调用），
        故此处仅处理 qrcode。pdf 无需恢复分割器。
        """
        try:
            if role == "qrcode" and hasattr(widget, "_splitter"):
                state = self._layout_manager.get_splitter_state("qrcode_tab")
                if state:
                    widget._splitter.restoreState(state)
        except Exception:
            logging.debug(f"[懒加载] 恢复 {role} 布局失败（忽略）", exc_info=True)

    def _maybe_dispatch_ocr_service_to_lazy_tab(self, role: str, widget: Any) -> None:
        """若 OCR 服务已就绪，向懒构造的 tab 下发服务句柄。

        正常流程中服务在 _on_subprocess_worker_ready 时下发给所有 tab，但懒构造的
        tab 在那时还不存在。这里在构造后补发，确保懒构造的 tab 也能立即识别。
        """
        if not getattr(self, "_ocr_ready", False):
            return
        try:
            mineru_batch = getattr(self, "_mineru_batch_service", None)
            paddlex_service = getattr(self, "_paddlex_service", None)
            if role == "batch":
                if mineru_batch is not None and hasattr(widget, "set_ocr_service"):
                    widget.set_ocr_service(mineru_batch)
                if paddlex_service is not None and hasattr(widget, "set_paddlex_service"):
                    widget.set_paddlex_service(paddlex_service)
            elif role == "pdf":
                if paddlex_service is not None and hasattr(widget, "set_ocr_service"):
                    widget.set_ocr_service(paddlex_service)
        except Exception:
            logging.debug(f"[懒加载] 向 {role} 下发 OCR 服务失败（忽略）", exc_info=True)

    def _init_about_tab(self) -> None:
        """初始化关于标签页（懒加载：首次切换到关于页才构造）。

        关于页置于末尾（设置页之后），符合「关于」居末的惯例。AboutTab 构造会同步
        读取 CHANGELOG.md 并解析 Markdown（QTextBrowser.setMarkdown），是启动期可省的
        CPU 开销，延迟到用户真正查看关于页时再构造。
        """
        self._about_tab: Any = None
        self._add_lazy_tab("关于", "about", self._build_about_tab, at_end=True)

    def _build_about_tab(self) -> Any:
        """构造关于标签页（懒加载时调用）。"""
        from vibeocr.views.tabs.about_tab import AboutTab

        return AboutTab(status_callback=self._statusbar.showMessage)

    def _setup_console(self) -> None:
        """初始化日志"""
        self._log_handler = setup_logging(ConfigManager.instance().get_log_level())
        self._log_handler.status_signal.connect(self._on_log_status_update)
        logging.info("VibeOCR 启动")

    def _connect_signals(self) -> None:
        """连接信号槽"""
        # 懒加载 Tab：用户切换到占位页时触发真实构造
        self._ui.tabWidget.currentChanged.connect(self._on_lazy_tab_changed)
        # _restore_layout 可能在信号连接前已 setCurrentIndex 到懒加载占位页，
        # 此时 currentChanged 不会重发。这里补一次：若当前页仍是未构造的占位页，
        # 立即触发构造，确保恢复的标签页可见、可用。
        cur = self._ui.tabWidget.currentIndex()
        if cur in self._lazy_tab_builders:
            self._on_lazy_tab_changed(cur)

        # 快捷键（替代已删除的菜单）
        self._shortcut_open = QShortcut(QKeySequence("Ctrl+O"), self)
        self._shortcut_open.activated.connect(self._on_open_image)

        self._shortcut_screenshot = QShortcut(QKeySequence("Ctrl+S"), self)
        self._shortcut_screenshot.activated.connect(self._on_screenshot)

        self._shortcut_quit = QShortcut(QKeySequence("Ctrl+Q"), self)
        self._shortcut_quit.activated.connect(self.close)

        # 截图组件
        overlay = self._overlay
        if overlay is not None:
            overlay.confirmed.connect(self._on_overlay_confirmed)
            overlay.copied.connect(self._on_overlay_copied)
            overlay.saved.connect(self._on_overlay_saved)
            overlay.cancelled.connect(self._on_overlay_cancelled)

        # 单次识别 Tab 的截图/文件请求由 MainWindow 处理
        self._single_tab.screenshot_requested.connect(self._on_screenshot)
        self._single_tab.file_open_requested.connect(self._on_open_file_from_preview)
        # 截图来源识别完成时，重新把主窗口提到前台（见 _bring_main_window_to_front）
        self._single_tab.bring_to_front_requested.connect(
            self._bring_main_window_to_front
        )

        # 剪贴板控制器（连接到 UI 中的复制按钮）
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
        self._settings_controller = SettingsPageController(
            ui=self,
            project_root=self._project_root,
            status_callback=self._statusbar.showMessage,
            ocr_ready_callback=lambda: self._ocr_ready,
            subprocess_manager=self._subprocess_manager,
            preload_complete_callback=self._on_preload_complete,
            # 设置页重装/补装依赖成功后联动重新检测（Bug A 修复）：
            # 旧逻辑设置页装完只刷新表格，不联动 _ocr_ready/Worker，截图界面
            # 仍提示"未就绪"。现复用 dependency_manager.check_dependencies，
            # 检测完成回调（_on_dependency_check_finished）自动设 _ocr_ready、
            # 启动子进程 Worker、消费 pending_backend，与首启路径行为一致。
            install_succeeded_callback=self._on_settings_install_succeeded,
        )
        self._settings_controller.connect_signals()

    def _on_preload_complete(self) -> None:
        """预加载完成回调"""
        self._preload_complete = True

    def _on_settings_install_succeeded(self) -> None:
        """设置页重装/补装依赖成功后的联动回调（Bug A 修复）

        由 SettingsPageController._open_reinstall_dialog 在对话框 emit
        install_succeeded 时调用。复用 DependencyManager.check_dependencies
        重新检测便携环境——检测完成回调（_on_dependency_check_finished）会
        自动设置 _ocr_ready、启动子进程 Worker、消费 pending_backend，使
        截图界面立即生效，无需重启程序。

        不直接设 _ocr_ready=True：让真实检测（双层 _probe_module）正确反映
        "装了但 mineru 间接依赖没装完"等异常状态，避免假就绪。
        """
        if self._closing:
            return
        logging.info("[设置安装] 依赖安装成功，重新检测以联动截图功能")
        # reset 确保重入安全（若上一次检测仍在进行，避免 _is_checking 短路）
        self._dependency_manager.check_dependencies()

    def _try_load_cache(self) -> None:
        """尝试从缓存加载依赖检测结果"""
        is_valid, cached_data = is_cache_valid(self._project_root)
        if is_valid and cached_data:
            dependencies = cached_data.get("dependencies", {})
            # 检查关键依赖
            paddlepaddle_ok = dependencies.get("paddlepaddle", False)
            paddlex_ok = dependencies.get("paddleocr", False)
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
        # 优先消费"待同步"标记：更新流程中 updater 写入 pending_sync.json，
        # 标记新版依赖需升级。存在时先升级 python/（复用 InstallDialog + 正确的
        # GPU/CUDA/镜像逻辑），完成后再走常规依赖检查。避免用裸 pip 装 CPU 版。
        if self._check_pending_sync():
            return  # 同步对话框接管，完成后会重新触发依赖检查

        already_ready = self._ocr_ready
        self._dependency_check_complete = True
        if ready:
            self._ocr_ready = True
            # 仅在未从缓存设置过就绪状态时更新状态栏（避免覆盖缓存提示）
            if not already_ready:
                self._statusbar.showMessage("OCR功能已就绪")
            logging.info("OCR功能已就绪")

            # 覆盖安装检测：version.json 的 dep_versions 可能比已装版本新（用户直接
            # 覆盖文件升级、无 pending_sync.json）。便携环境就绪时检测并提示更新。
            # 与 pending_sync 互斥（pending_sync 已 return 接管），开发态不触发。
            self._maybe_prompt_dependency_updates()

            # 检测是否有待生效的后端切换（重启消费 pending_backend）
            needs_switch, target = self._check_pending_backend()
            if needs_switch and target:
                self._show_switch_dialog(target)
                return  # 切换完成后再启动 worker

            # 启动子进程 Worker（依赖检测完成后立即启动）
            self._start_subprocess_worker()
        else:
            self._ocr_ready = False
            missing_str = ", ".join(missing)
            self._statusbar.showMessage(f"OCR功能未就绪: {missing_str}")

            # 首启场景：Python 运行时未安装意味着用户首次运行，且无任何 OCR
            # 依赖。此时仅更新状态栏会让用户无所适从（安装入口原本只在用户
            # 点截图/打开图片时才经 _check_ocr_ready 弹出）。这里主动弹出首启
            # 安装引导，引导用户先安装 Python 运行时 + OCR 依赖。
            # 用 singleShot 延迟，避免在依赖检查回调线程上下文直接弹模态对话框。
            if any("Python 运行时" in m for m in missing):
                QTimer.singleShot(300, self._start_install)

    def _check_pending_sync(self) -> bool:
        """检测并消费"依赖版本待同步"标记（updater 写入的 pending_sync.json）

        程序内更新时，updater 替换应用文件后会把变更的 dep_versions 写入
        pending_sync.json，但它无法 import vibeocr（独立 --onefile 打包），
        故不直接 pip。由覆盖后的新版 VibeOCR 在此消费：用 InstallDialog 跑
        install_embedded_dependencies（含 GPU/CUDA tag/镜像/PyPI 回退的完整逻辑）
        升级 python/，避免裸 pip 走 PyPI 把 paddle/torch 装成 CPU 版。

        pending_sync 字段（向后兼容）：
        - dep_versions：变化的包 → {"version", "op"} 或旧式裸 str（展示用 key 即可）
        - removed：被移除的包名列表（同步成功后调 uninstall_removed_deps 清理）
        - attempts：失败重试计数（达 SYNC_MAX_ATTEMPTS 提示重装 Python）

        Returns:
            是否存在待同步标记（True 表示已弹出升级对话框接管流程）
        """
        from vibeocr.pyside.runtime import get_pending_sync_path

        pending_path = get_pending_sync_path()
        if not pending_path.exists():
            return False

        try:
            import json

            data = json.loads(pending_path.read_text(encoding="utf-8"))
        except Exception as e:
            logging.warning(f"[依赖同步] 读取 pending_sync.json 失败，删除标记: {e}")
            self._delete_pending_sync()
            return False

        changed = data.get("dep_versions", {})
        removed = data.get("removed", [])
        if not changed and not removed:
            # 空标记，清理后走常规流程
            self._delete_pending_sync()
            return False

        version = data.get("version", "")
        # 展示用包名列表：取 dict/str 的 key 即可，兼容两种格式
        pkgs = ", ".join(changed.keys()) if changed else ""
        removed_str = ", ".join(removed) if removed else ""
        display_parts = [p for p in (pkgs, removed_str) if p]
        display = "、".join(display_parts) if display_parts else ""

        logging.info(
            f"[依赖同步] 检测到待同步标记（目标版本 {version}）："
            f"changed={changed} removed={removed}"
        )
        self._statusbar.showMessage(f"正在同步 OCR 依赖更新：{display}")

        from vibeocr.widgets.install_dialog import InstallDialog

        dialog = InstallDialog(self._project_root, self)
        dialog.setWindowTitle("同步 OCR 依赖更新")

        title_lines = [f"检测到新版本依赖（{version}），正在同步更新："]
        if pkgs:
            title_lines.append(f"升级：{pkgs}")
        if removed_str:
            title_lines.append(f"清理：{removed_str}")
        dialog._title_label.setText("\n".join(title_lines))

        # 缓存 removed 供 _on_sync_finished 升级成功后清理
        self._pending_removed = list(removed)
        dialog.finished.connect(self._on_sync_finished)
        dialog.exec()
        return True

    @Slot(int)
    def _on_sync_finished(self, result: int) -> None:
        """依赖同步对话框完成"""
        if result == 1:
            # 升级成功，先清理已移除的依赖（P4），再删除一次性标记
            removed = getattr(self, "_pending_removed", [])
            if removed:
                self._cleanup_removed_deps(removed)
                self._pending_removed = []
            self._delete_pending_sync()
            self._statusbar.showMessage("OCR 依赖同步完成")
            logging.info("[依赖同步] 依赖同步完成，重新检查依赖")
            # 清空依赖检测缓存，强制重新检测（否则旧缓存可能仍判就绪）
            import vibeocr.env_manager as em

            em._dep_specs_cache = None
            self._dependency_manager.check_dependencies()
            # 同步会重装 python/ 内的包，Python 运行时状态可能变化，刷新设置页 label
            self._refresh_settings_env_state()
        else:
            # 升级失败：递增 attempts 计数，达阈值提示重装 Python（P2）
            attempts = self._increment_sync_attempts()
            from vibeocr.pyside.runtime import SYNC_MAX_ATTEMPTS

            self._ocr_ready = False
            if attempts >= SYNC_MAX_ATTEMPTS:
                msg = (
                    f"OCR 依赖同步已失败 {attempts} 次，建议在「设置 → 环境」中"
                    "重装 Python 运行时后再试"
                )
                self._statusbar.showMessage(msg)
                logging.warning(
                    "[依赖同步] 同步失败次数达阈值 %d，已提示用户重装 Python",
                    attempts,
                )
            else:
                self._statusbar.showMessage(
                    f"OCR 依赖同步失败（第 {attempts} 次），将在下次启动重试"
                )
                logging.warning(
                    "[依赖同步] 同步失败（第 %d 次），保留 pending_sync.json 供重试",
                    attempts,
                )

    def _increment_sync_attempts(self) -> int:
        """递增 pending_sync.json 的 attempts 字段并返回新值。

        读取失败或字段缺失时按 1 处理（首次失败）。写入失败仅记录警告，
        不阻断流程（最坏情况是提示滞后一次）。
        """
        from vibeocr.pyside.runtime import get_pending_sync_path

        pending_path = get_pending_sync_path()
        try:
            import json

            data = (
                json.loads(pending_path.read_text(encoding="utf-8"))
                if pending_path.exists()
                else {}
            )
            attempts = int(data.get("attempts", 1)) + 1
            data["attempts"] = attempts
            pending_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return attempts
        except Exception as e:
            logging.warning(f"[依赖同步] 递增 attempts 失败: {e}")
            return 1

    def _cleanup_removed_deps(self, removed: list[str]) -> None:
        """升级成功后清理已移除的依赖（P4）。

        在独立线程跑 uninstall_removed_deps，避免阻塞 UI。
        失败仅记录日志（不阻断主流程，残留包不影响运行）。
        """
        import threading

        from vibeocr.env_manager import uninstall_removed_deps

        def _run() -> None:
            try:
                ok, msg = uninstall_removed_deps(self._project_root, removed)
                if ok:
                    logging.info(f"[依赖同步] 依赖清理完成: {msg}")
                else:
                    logging.warning(f"[依赖同步] 依赖清理未完全成功: {msg}")
            except Exception as e:
                logging.warning(f"[依赖同步] 依赖清理异常: {e}")

        threading.Thread(target=_run, daemon=True).start()

    def _delete_pending_sync(self) -> None:
        """删除 pending_sync.json 标记文件（同步成功或标记无效时调用）"""
        from vibeocr.pyside.runtime import get_pending_sync_path

        pending_path = get_pending_sync_path()
        try:
            pending_path.unlink(missing_ok=True)
        except Exception as e:
            logging.warning(f"[依赖同步] 删除 pending_sync.json 失败: {e}")

    def _maybe_prompt_dependency_updates(self) -> None:
        """启动时检测 OCR 依赖是否有版本更新，有则弹窗提示用户升级。

        覆盖安装场景（用户直接覆盖文件升级 app，无 pending_sync.json）下，
        version.json 的 dep_versions 可能比便携 Python 里已装的版本新。
        本方法在环境就绪后检测，发现可更新包时弹 QMessageBox 让用户选择是否升级。

        互斥：pending_sync 已由 _check_pending_sync 接管时会 return，不会走到这里。
        开发态（.venv）不触发（detect_dependency_updates 仅便携模式生效，
        开发态由 uv 管理环境）。
        每个进程生命周期内只提示一次（_deps_update_prompted 标志）。
        """
        # 防止依赖检查多次回调导致重复弹窗
        if getattr(self, "_deps_update_prompted", False):
            return
        try:
            import vibeocr.env_manager as em

            updates = em.detect_dependency_updates(self._project_root)
        except Exception as e:
            logging.warning(f"[依赖更新] 启动检测失败: {e}")
            return
        if not updates:
            return

        self._deps_update_prompted = True
        lines = []
        for pkg, (installed, required) in updates.items():
            lines.append(f"  • {pkg}：{installed or '（未安装）'} → {required}")
        detail = "\n".join(lines)

        from PySide6.QtWidgets import QMessageBox

        reply = QMessageBox.question(
            self,
            "检测到依赖更新",
            f"检测到以下 OCR 依赖有新版本可用：\n\n{detail}\n\n"
            "是否立即更新？（将使用当前推理后端下载升级）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # 走全量安装升级，后端用当前值；复用 InstallDialog 进度 UI
        current_backend = "gpu" if em.resolve_use_gpu(self._project_root) else "cpu"
        from vibeocr.widgets.install_dialog import InstallDialog

        dialog = InstallDialog(
            self._project_root,
            parent=self,
            force_backend=current_backend,
        )
        dialog.setWindowTitle("更新 OCR 依赖")
        dialog._title_label.setText("正在更新 OCR 依赖...")
        dialog.finished.connect(self._on_deps_update_finished)
        dialog.exec()

    @Slot(int)
    def _on_deps_update_finished(self, result: int) -> None:
        """依赖更新对话框完成：刷新设置页状态 + 重新检测依赖。"""
        self._refresh_settings_env_state()
        if result == 1:
            # 升级成功：重置 specs 缓存 + 重新检测依赖（让设置页表格反映新版本）
            import vibeocr.env_manager as em

            em._dep_specs_cache = None
            self._dependency_manager.check_dependencies()

    def _check_pending_backend(self) -> tuple[bool, str | None]:
        """检测是否有待生效的后端切换（重启消费 pending_backend）

        Returns:
            (是否需要切换, 目标后端 "gpu"/"cpu"/None)
        """
        is_valid, cached_data = is_cache_valid(self._project_root)
        if not (is_valid and cached_data):
            return False, None

        pending = cached_data.get("pending_backend")
        if not pending:
            return False, None

        # 当前实际后端：读 hardware_info.has_gpu（switch_paddle_backend 会更新它）
        hardware_info = cached_data.get("hardware_info") or {}
        current = "gpu" if hardware_info.get("has_gpu") else "cpu"

        if pending == current:
            # 一致，清除标记，无需切换
            update_cache_field(self._project_root, "pending_backend", None)
            logging.info("[后端切换] pending_backend 与当前一致，已清除标记")
            return False, None

        logging.info(
            "[后端切换] 检测到 pending_backend=%s（当前 %s），将切换", pending, current
        )
        return True, pending

    def _show_switch_dialog(self, target: str) -> None:
        """显示后端切换对话框（重启消费 pending_backend）"""
        from vibeocr.widgets.switch_dialog import SwitchDialog

        name = "GPU" if target == "gpu" else "CPU"
        self._statusbar.showMessage(f"正在切换到 {name} 后端...")

        def _on_switch_finished(result: int) -> None:
            if result == 1:
                # 切换成功，清除 pending 标记
                update_cache_field(self._project_root, "pending_backend", None)
                self._statusbar.showMessage("后端切换完成，正在启动 OCR 服务")
                self._start_subprocess_worker()
            else:
                self._statusbar.showMessage("后端切换失败，请在设置页重试")
                self._ocr_ready = False

        dialog = SwitchDialog(self._project_root, target, self)
        dialog.finished.connect(_on_switch_finished)
        dialog.exec()

    def _start_subprocess_worker(self) -> None:
        """依赖检测完成后启动唯一的 PySide WorkerHost 会话。"""
        if self._closing:
            logging.debug("[MainWindow] 应用程序正在关闭，跳过启动 WorkerHost")
            return

        logging.debug("[MainWindow] 正在启动共享 WorkerHost...")
        use_gpu = env_manager.resolve_use_gpu(self._project_root)
        device = "GPU" if use_gpu else "CPU"
        self._statusbar.showMessage(f"正在启动 OCR 服务({device})...")

        # 将决策同步到主进程环境变量。OCR 子进程会由 ocr_worker.run_worker
        # 自行设置该变量，但主进程此前从未设置，导致跑在主进程 QThread 里的
        # PdfOcrWorker 读到空值、误判为 CPU（日志误报 + batch 走 RAM 公式）。
        os.environ["VIBEOCR_USE_GPU"] = "true" if use_gpu else "false"

        # WorkerHost 的进程启动、ready 握手和 typed client 初始化均可能耗时数十秒；
        # 交给 SubprocessManager 的线程池，完成后通过 service_ready 回到 Qt 主线程。
        self._subprocess_manager.start_worker_host()

    @Slot(bool)
    def _on_subprocess_worker_ready(self, success: bool) -> None:
        """子进程 Worker 就绪回调"""
        if self._closing:
            logging.debug("[MainWindow] 忽略关闭后的 WorkerHost ready 结果")
            return
        if success:
            logging.debug("[MainWindow] 子进程 Worker 已就绪")
            # 启动里程碑 T4：WorkerHost ready
            from vibeocr.startup_metrics import StartupEvent, record_startup

            record_startup(StartupEvent.WORKER_READY)
            self._ensure_ocr_status_callback()

            # 迁移期 PDF 前端状态机仍使用 recognize_batch 形状；适配器本身
            # 只委托到同一个 BackendSession，不再创建旧 OCR 子进程。
            paddlex_service = self._subprocess_manager.service
            if paddlex_service is None:
                logging.error("[MainWindow] WorkerHost ready 但服务适配器缺失")
                self._on_subprocess_worker_ready(False)
                return
            mineru_batch = paddlex_service
            # 缓存服务句柄，供懒构造的 Tab 在 _on_lazy_tab_changed 时补发（懒构造的
            # Tab 错过了此处注入，构造后需自行获取服务句柄）
            self._paddlex_service = paddlex_service
            self._mineru_batch_service = mineru_batch

            # 单次识别 Tab 服务注入
            if hasattr(self, "_single_tab") and self._single_tab:
                self._single_tab.set_paddlex_service(paddlex_service)
                self._single_tab.set_ocr_service(mineru_batch)

            # 批量识别 Tab 服务注入
            if hasattr(self, "_batch_tab") and self._batch_tab:
                self._batch_tab.set_ocr_service(mineru_batch)
                self._batch_tab.set_paddlex_service(paddlex_service)
                logging.debug("[MainWindow] 批量识别标签页已连接批量服务")

            # PDF 处理 Tab 服务注入
            if hasattr(self, "_pdf_tab") and self._pdf_tab:
                self._pdf_tab.set_ocr_service(paddlex_service)
                logging.debug("[MainWindow] PDF 处理标签页已连接服务")

            self._preload_in_progress = False
            self._statusbar.showMessage("OCR 服务已就绪（模型按需加载）")
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
                "请查看控制台日志了解详情。",
            )

    @Slot(str)
    def _on_subprocess_progress(self, stage: str) -> None:
        """子进程启动进度回调"""
        self._statusbar.showMessage(f"正在启动 OCR 服务: {stage}")

    @Slot(dict)
    def _on_preload_finished(self, results: dict) -> None:
        """预加载完成回调。

        results 形如 {"preload": {pipeline: bool}, "warmup": {pipeline: bool}}。
        preload=管道定义已加载；warmup=CUDA/模型权重已初始化（虚拟识别）。
        预热失败不影响使用（首次真实识别会按需初始化，仅首次稍慢）。
        """
        self._preload_in_progress = False
        preload = results.get("preload", {}) if isinstance(results, dict) else {}
        warmup = results.get("warmup", {}) if isinstance(results, dict) else {}
        preload_ok = sum(1 for v in preload.values() if v)
        preload_total = len(preload)
        warmup_ok = sum(1 for v in warmup.values() if v)
        warmup_total = len(warmup)
        self._preload_complete = (
            preload_total > 0
            and preload_ok == preload_total
            and warmup_total == preload_total
            and warmup_ok == warmup_total
        )

        if preload_total > 0 and preload_ok > 0:
            if warmup_total > 0 and warmup_ok < warmup_total:
                # 预加载成功但预热部分/全部失败：明确告知，避免"已预热"误导
                self._statusbar.showMessage(
                    f"OCR 服务已就绪（模型已加载 {preload_ok}/{preload_total}，"
                    f"预热 {warmup_ok}/{warmup_total} 失败，首次识别会按需初始化）"
                )
            else:
                self._statusbar.showMessage(
                    f"OCR 服务已就绪（{preload_ok}/{preload_total} 个模型已预热）"
                )
        else:
            self._statusbar.showMessage("OCR 服务已就绪")
        logging.debug(
            f"[MainWindow] 预加载完成: preload={preload}, warmup={warmup}"
        )
        # 启动里程碑 T5/T6：OCR backend ready + 首次可交互
        from vibeocr.startup_metrics import StartupEvent, flush_startup, record_startup

        record_startup(StartupEvent.BACKEND_READY)  # T5
        record_startup(StartupEvent.INTERACTIVE)  # T6：预加载完成后用户可交互
        flush_startup()  # 若 VIBEOCR_STARTUP_TRACE 设置则写 JSONL
        if os.environ.get("VIBEOCR_SELF_TEST_SMOKE") == "t6":
            os._exit(0)

    @Slot(int, int, str)
    def _on_preload_progress(self, current: int, total: int, pipeline_name: str) -> None:
        """预加载逐管道进度回调"""
        from vibeocr.contracts.pipelines import OCRPipeline, get_pipeline_display_name

        # 管道名转中文显示名（如 "OCR" -> "通用 OCR"）
        try:
            pipeline = OCRPipeline(pipeline_name)
            display_name = get_pipeline_display_name(pipeline)
        except ValueError:
            display_name = pipeline_name
        self._statusbar.showMessage(
            f"正在预热模型 {current}/{total}：{display_name}..."
        )

    @Slot(str)
    def _on_recognition_queued(self, message: str) -> None:
        """识别请求因预加载排队"""
        self._statusbar.showMessage(message)
        if hasattr(self, "_single_tab") and self._single_tab:
            self._single_tab.show_waiting_message(message)
        # PDF tab 的 OCR（添加文字层/自动摆正）也走同一 worker，
        # 遇到 worker 忙（预热中）同样会排队，需告知用户"在排队"而非"卡住"。
        if hasattr(self, "_pdf_tab") and self._pdf_tab:
            self._pdf_tab.on_ocr_queued(message)

    def _start_subprocess_preload(self) -> None:
        """在子进程中下发 TTL 并（可选）预加载用户配置的管道

        全部操作在 SubprocessManager 的后台线程执行，避免阻塞 GUI 主线程。
        TTL 无论是否启用预加载都会下发。
        """
        if not self._subprocess_manager.is_ready:
            return

        # 读取用户配置的 TTL（无论是否预加载都需要下发）
        from vibeocr.pyside.runtime import ConfigManager

        try:
            ttl = ConfigManager.instance().get_pipeline_ttl_seconds()
        except Exception as e:
            logging.warning("[子进程预加载] 读取 TTL 配置失败: %s", e)
            ttl = None

        # 读取用户配置的预加载管道
        from vibeocr.contracts.pipelines import OCRPipeline

        cm = ConfigManager.instance()
        if not cm.get_preload_enabled():
            logging.debug("[子进程预加载] 预加载已禁用，仅下发 TTL")
            # 仍然下发 TTL（后台线程）
            self._subprocess_manager.preload_pipelines([], ttl_seconds=ttl)
            return

        raw_pipelines = cm.get_preload_pipelines()

        # 过滤无效的管道名称（大小写不敏感匹配，兼容历史小写配置）
        valid_values = {p.value for p in OCRPipeline}
        value_lower_map = {p.value.lower(): p.value for p in OCRPipeline}
        pipelines: list[str] = []
        invalid: set[str] = set()
        for p in raw_pipelines:
            if p in valid_values:
                pipelines.append(p)
            elif p.lower() in value_lower_map:
                # 历史小写配置自动归一化到标准值
                pipelines.append(value_lower_map[p.lower()])
            else:
                invalid.add(p)

        if invalid:
            logging.warning(f"[子进程预加载] 忽略无效管道: {invalid}")

        logging.debug(f"[子进程预加载] 开始预加载管道: {pipelines}")

        # TTL 下发与预加载均在后台线程执行
        self._preload_in_progress = self._subprocess_manager.preload_pipelines(
            pipelines, ttl_seconds=ttl
        )

    def _show_install_dialog(self, missing: list) -> None:
        """显示后端选择 + 安装对话框（首启合并对话框）"""
        from vibeocr.widgets.backend_choice_dialog import BackendChoiceDialog

        self._subprocess_manager.invalidate_worker_host()
        dialog = BackendChoiceDialog(self._project_root, self)
        dialog.finished.connect(self._on_install_finished)
        dialog.install_succeeded.connect(self._on_install_succeeded)
        dialog.exec()

    def _start_install(self) -> None:
        """开始安装依赖（保留入口，直接走首启合并对话框）"""
        self._show_install_dialog([])

    @Slot(int)
    def _on_install_finished(self, result: int) -> None:
        """安装完成"""
        if result == 1:
            self._statusbar.showMessage("OCR依赖安装成功")
            # 安装成功后启动子进程 Worker
            self._start_subprocess_worker()
            # 双保险刷新设置页（覆盖只发 finished 不发 install_succeeded 的路径）
            self._refresh_settings_env_state()
        else:
            self._statusbar.showMessage("OCR依赖安装失败")

    @Slot()
    def _on_install_succeeded(self) -> None:
        """安装成功后标记就绪"""
        self._ocr_ready = True
        self._statusbar.showMessage("OCR依赖安装成功，首次识别将自动下载模型")
        # 安装完成后 Python 运行时状态已变，刷新设置页环境维护区 label
        # （首启时 label 在 Python 未装时写下"未安装"，此处避免重启才更新）
        self._refresh_settings_env_state()

    def _refresh_settings_env_state(self) -> None:
        """刷新设置页"环境维护区"状态（Python 路径/就绪）。

        安装/同步/重装成功后调用，避免 label 停留在启动时计算的"未安装"，
        导致用户必须重启程序才看到正确状态。
        """
        controller = getattr(self, "_settings_controller", None)
        if controller is not None:
            try:
                controller._refresh_env_maintenance_state()
            except Exception as e:
                logging.warning("[MainWindow] 刷新设置页环境状态失败: %s", e)

    @Slot()
    def _on_open_image(self) -> None:
        """打开图片文件"""
        if not self._check_ocr_ready():
            return
        if self._single_tab.is_processing:
            self._statusbar.showMessage("上一次识别尚未完成，请稍候", 2000)
            return
        logging.debug("打开图片文件对话框")

        from vibeocr.utils.mime_types import (
            FILE_FILTER_DOCUMENTS,
            FILE_FILTER_IMAGES,
            is_document_file,
        )

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "打开文件",
            "",
            f"{FILE_FILTER_IMAGES};;{FILE_FILTER_DOCUMENTS};;所有文件 (*)",
        )
        if file_path:
            if is_document_file(file_path):
                self._single_tab._preview_widget.clear()
                self._single_tab.process_file(file_path)
                return

            pixmap = QPixmap(file_path)
            if not pixmap.isNull():
                self._single_tab.set_image_for_recognition(pixmap)
                self._single_tab.set_pixmap(pixmap)
                self._single_tab.run_ocr(pixmap)

    @Slot()
    def _on_open_file_from_preview(self) -> None:
        """从预览区域打开文件（支持图片和 PDF）"""
        if not self._check_ocr_ready():
            return
        if self._single_tab.is_processing:
            self._statusbar.showMessage("上一次识别尚未完成，请稍候", 2000)
            return
        logging.debug("打开文件对话框（图片/PDF）")

        from vibeocr.utils.mime_types import FILE_FILTER_ALL, is_document_file

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择文件",
            "",
            f"{FILE_FILTER_ALL};;所有文件 (*)",
        )
        if not file_path:
            return

        if is_document_file(file_path):
            self._single_tab._preview_widget.clear()
            self._single_tab.process_file(file_path)
            return

        pixmap = QPixmap(file_path)
        if not pixmap.isNull():
            self._single_tab.set_image_for_recognition(pixmap)
            self._single_tab.set_pixmap(pixmap)
            self._single_tab.run_ocr(pixmap)

    def _check_ocr_ready(self) -> bool:
        """检查OCR功能是否可用"""
        if not self._dependency_check_complete:
            QMessageBox.information(
                self,
                "正在检测依赖",
                "OCR依赖检测中，请稍候...\n\n检测完成后才能使用截图识别功能。",
            )
            return False

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

        # 依赖已就绪，但 OCR 子进程服务尚未启动完成 —— 拦截截图，
        # 避免在 Worker 启动/预加载期间触发识别导致报错或长时间等待。
        if not self._subprocess_manager.is_ready:
            QMessageBox.information(
                self,
                "服务启动中",
                "OCR 服务正在启动，请稍候片刻再试...",
            )
            return False
        return True

    @Slot()
    def _on_screenshot(self) -> None:
        """开始截图"""
        if not self._check_ocr_ready():
            return

        # 记录截图前主窗口的最小化状态，截图结束后据此恢复（不抢焦点）。
        self._main_window_minimized_before_capture = self.isMinimized()
        self.showMinimized()
        # 延迟启动截图，让窗口有时间最小化
        QTimer.singleShot(200, self._start_fresh_overlay_capture)

    @Slot(str)
    def _on_pipeline_screenshot(self, pipeline_name: str) -> None:
        """从工具栏快捷管道按钮触发截图识别

        与 _on_screenshot 相同，但预先设置管道名称，截图选区完成后
        直接进入对应管道识别，跳过截图编辑界面。
        """
        if not self._check_ocr_ready():
            return

        self._main_window_minimized_before_capture = self.isMinimized()
        self.showMinimized()
        # 预设管道，截图选区完成后自动用该管道识别
        QTimer.singleShot(200, lambda: self._start_fresh_overlay_capture(pipeline_name))

    def _start_fresh_overlay_capture(self, pipeline_name: str | None = None) -> None:
        """每次截图创建全新的 ScreenCaptureOverlay 实例并启动。

        早期复用单个 overlay 实例（hide()/show() 之间），分层窗口
        （WA_TranslucentBackground）的后备存储在会话间保留上一轮画面，
        导致下次 show() 时「一闪而过上一次截图界面」。尝试在 show 前
        repaint() 清屏、show 后再 repaint() 均无效——分层窗口的合成像素
        不随隐藏窗口的 repaint 更新。

        根治方案：每次截图新建 overlay（新原生窗口、空后备存储），
        _cleanup 时 deleteLater() 释放。代价是每轮一次轻量窗口创建，
        远小于残留帧带来的体验问题。
        """
        # 释放上一轮（若未正常 _cleanup，防御性清理）
        if self._overlay is not None:
            try:
                self._overlay.finish_capture()
            except Exception:
                logging.exception("清理旧截图覆盖层失败")
            self._overlay.deleteLater()
            self._overlay = None

        self._overlay = ScreenCaptureOverlay()
        self._overlay.confirmed.connect(self._on_overlay_confirmed)
        self._overlay.copied.connect(self._on_overlay_copied)
        self._overlay.saved.connect(self._on_overlay_saved)
        self._overlay.cancelled.connect(self._on_overlay_cancelled)

        if pipeline_name is not None:
            self._overlay.set_pending_pipeline(pipeline_name)
        self._overlay.start_capture()

    def _restore_main_window(self, *, activate: bool) -> None:
        """截图结束后恢复主窗口状态。

        静默操作（复制/保存/取消）：仅当截图前主窗口未被最小化时恢复可见，
        截图前已最小化则保持最小化（不抢焦点）。
        识别操作（activate=True）：用户明确想看结果，无论截图前是否最小化都恢复
        可见——否则工具栏/托盘触发截图后窗口永远不出现。

        Args:
            activate: True 时额外激活窗口并置顶（仅识别路径）。
        """
        if activate or not self._main_window_minimized_before_capture:
            self.showNormal()
        if activate:
            self.activateWindow()
            self.raise_()

    def _bring_main_window_to_front(self) -> None:
        """识别完成后把主窗口重新提到前台。

        截图确认时（_on_overlay_confirmed）已激活过一次主窗口，但 OCR 是异步的，
        可能耗时数秒（首次还需下载模型）。这期间用户或系统切走窗口后，开始前
        那次激活已失效——表现为「识别后主界面不弹出」。SingleRecognitionTab 在
        截图来源识别完成时发出 bring_to_front_requested，本槽在结果就绪后再次
        showNormal + activateWindow + raise_，确保窗口真正前置。

        Windows 上 activateWindow 对非前台进程常只闪烁任务栏，故延迟一拍重试，
        规避 overlay 刚关闭导致前台锁丢失的竞态。
        """
        if self._closing:
            return
        self.showNormal()
        self.activateWindow()
        self.raise_()
        # 延迟重试一次：跨进程前台权限在 overlay/其它窗口刚关闭后可能尚未归还，
        # 立即 activateWindow 会失败；下一事件循环重试成功率更高。
        QTimer.singleShot(0, self.activateWindow)

    @Slot(QPixmap, object)
    def _on_overlay_confirmed(self, pixmap: QPixmap, options) -> None:
        """截图确认，执行 OCR

        options 来自截图面板（screenshot 源），首次识别保持用截图源选项；
        同时经 set_image_for_recognition 启用「重新识别」按钮——
        之后点「重新识别」会改用界面面板选项（main 源）。
        """
        # 识别需要立即展示 OCR 结果，故激活并置顶主窗口。
        self._restore_main_window(activate=True)
        # 异步化后事件循环在 OCR 期间照常转动，用户可能在上一次识别未完成时
        # 再次触发截图确认；此时静默忽略并提示，避免旧结果覆盖新图。
        if self._single_tab.is_processing:
            self._statusbar.showMessage("上一次识别尚未完成，请稍候", 2000)
            return
        if not pixmap.isNull():
            self._single_tab.set_image_for_recognition(pixmap)
            self._single_tab.set_pixmap(pixmap)
            # from_screenshot=True：识别完成时让 tab 发 bring_to_front_requested，
            # MainWindow 在结果就绪后再次前置（OCR 期间窗口可能被切走）。
            self._single_tab.run_ocr(pixmap, options, from_screenshot=True)

    @Slot(QPixmap)
    def _on_overlay_copied(self, pixmap: QPixmap) -> None:
        """截图复制完成"""
        # 复制为静默操作，仅恢复可见性、不抢焦点。
        self._restore_main_window(activate=False)
        self._statusbar.showMessage("图片已复制到剪贴板")

    @Slot(str)
    def _on_overlay_saved(self, file_path: str) -> None:
        """截图保存完成"""
        # 保存为静默操作，仅恢复可见性、不抢焦点。
        self._restore_main_window(activate=False)
        self._statusbar.showMessage(f"图片已保存: {file_path}")

    @Slot()
    def _on_overlay_cancelled(self) -> None:
        """截图取消"""
        # 取消为静默操作，仅恢复可见性、不抢焦点。
        self._restore_main_window(activate=False)

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
            logging.debug("主窗口已最小化到系统托盘")
            return

        logging.debug("正在关闭应用程序...")

        # 从这里开始拒绝任何迟到的 ready/安装/识别回调重新启动共享 WorkerHost。
        # 托盘隐藏分支已在上方返回，因此此处可以安全进入不可逆关闭态。
        self._closing = True

        # 异步识别可能仍在 qasync loop 上运行；在清理任何 widget 之前先标记关闭态
        # 并取消进行中的识别 task，否则 _on_ocr_finished/_on_ocr_error 回调会在
        # _result_widget.cleanup() 之后写入已销毁的 web view。
        if hasattr(self, "_single_tab") and self._single_tab is not None:
            self._single_tab.set_closing(True)
        qrcode_tab = getattr(self, "_qrcode_tab", None)
        if qrcode_tab is not None and hasattr(qrcode_tab, "set_closing"):
            qrcode_tab.set_closing(True)

        # 批处理线程会继续向结果 WebView 发信号；必须先请求取消并有界 drain，
        # 再销毁 WebView、关闭 PDF 和共享 WorkerHost。
        batch_tab = getattr(self, "_batch_tab", None)
        if batch_tab is not None and hasattr(batch_tab, "shutdown"):
            batch_tab.shutdown(timeout_ms=0)

        if hasattr(self, "_settings_controller"):
            if hasattr(self._settings_controller, "request_shutdown"):
                self._settings_controller.request_shutdown()
            else:
                self._settings_controller.shutdown()

        # PDF 页签只发取消请求，实际等待纳入下方统一 wall-clock 预算。
        if hasattr(self, "_pdf_tab") and self._pdf_tab:
            if hasattr(self._pdf_tab, "request_shutdown"):
                self._pdf_tab.request_shutdown()
            else:
                self._pdf_tab.shutdown()

        # 关闭边缘工具栏
        if hasattr(self, "_edge_toolbar") and self._edge_toolbar:
            self._edge_toolbar.close()

        # 保存应用设置
        if self._app_settings:
            self._app_settings.save()

        # 保存布局
        self._save_layout()

        # 统一 drain 各后台子系统。所有步骤共享一个绝对截止时间，前一步快速完成
        # 会把时间留给后续步骤；不再叠加多个互不知情的 1s/2s/5s 等待。
        from vibeocr.pyside.runtime import Constants, ShutdownCoordinator

        coord = ShutdownCoordinator()

        settings_controller = getattr(self, "_settings_controller", None)
        if settings_controller is not None and hasattr(settings_controller, "drain"):
            coord.register(
                "settings",
                lambda: settings_controller.drain(
                    timeout_ms=Constants.Timeout.Ms.SETTINGS_DRAIN
                ),
                max_timeout_ms=Constants.Timeout.Ms.SETTINGS_SHUTDOWN,
            )

        pdf_tab = getattr(self, "_pdf_tab", None)
        if pdf_tab is not None and hasattr(pdf_tab, "drain"):
            coord.register(
                "pdf",
                lambda: pdf_tab.drain(timeout_ms=Constants.Timeout.Ms.PDF_DRAIN),
                max_timeout_ms=Constants.Timeout.Ms.PDF_SHUTDOWN,
            )

        if batch_tab is not None and hasattr(batch_tab, "drain"):
            coord.register(
                "batch",
                lambda: batch_tab.drain(
                    timeout_ms=Constants.Timeout.Ms.BATCH_DRAIN
                ),
                max_timeout_ms=Constants.Timeout.Ms.BATCH_SHUTDOWN,
            )

        # async runner cancel（仅在有运行中事件循环时才有意义）
        try:
            from vibeocr.utils.qt_async import get_async_runner

            runner = get_async_runner()
            if runner.active_count > 0:
                coord.register(
                    "async_runner",
                    runner.cancel_all,
                    max_timeout_ms=Constants.Timeout.Ms.ASYNC_RUNNER_SHUTDOWN,
                )
        except Exception:
            pass

        # Single/QR/batch tabs share exactly one authenticated WorkerHost.
        from vibeocr.client.session import shutdown_backend_client

        coord.register(
            "backend_session",
            shutdown_backend_client,
            max_timeout_ms=Constants.Timeout.Ms.BACKEND_SESSION_SHUTDOWN,
        )
        coord.register(
            "subprocess",
            lambda: self._subprocess_manager.shutdown(  # type: ignore[arg-type]
                timeout_ms=Constants.Timeout.Ms.SUBPROCESS_SHUTDOWN
            ),
            max_timeout_ms=Constants.Timeout.Ms.SUBPROCESS_SHUTDOWN,
        )
        coord.coordinate(timeout_ms=Constants.Timeout.Ms.APP_SHUTDOWN_TOTAL)

        # 所有会写结果的后台任务已请求取消/尽力 drain 后，再销毁 WebEngine。
        for tab in (
            getattr(self, "_single_tab", None),
            getattr(self, "_batch_tab", None),
        ):
            if tab and hasattr(tab, "_result_widget") and tab._result_widget:
                tab._result_widget.cleanup()

        from PySide6.QtCore import QCoreApplication

        QCoreApplication.processEvents()

        event.accept()
        logging.debug("应用程序已关闭")

    # ============================================================
    # 系统托盘与边缘工具栏集成
    # ============================================================

    def set_app_settings(self, app_settings) -> None:
        """设置应用设置对象（由 main.py 调用）"""

        self._app_settings = app_settings  # type: ignore[assignment]
        self._init_app_settings_ui()
        self.apply_app_settings()

    def set_tray_icon(self, tray_icon) -> None:
        """设置系统托盘图标（由 main.py 调用）"""
        self._tray_icon = tray_icon

    def apply_app_settings(self) -> None:
        """应用当前设置到工具栏等组件"""
        if not self._app_settings:
            return
        # 工具栏显示/隐藏
        if self._app_settings.show_toolbar:
            pos = self._app_settings.toolbar_pos
            if pos and "x" in pos and "y" in pos:
                self._edge_toolbar.move(pos["x"], pos["y"])
            else:
                self._edge_toolbar.set_initial_position()
            self._edge_toolbar._detect_edge()
            self._edge_toolbar.show()
        else:
            self._edge_toolbar.hide()
        # 自动隐藏和延迟
        self._edge_toolbar.set_auto_hide(self._app_settings.auto_hide_toolbar)
        self._edge_toolbar.set_hide_delay(self._app_settings.hide_delay_ms)
        # 更新设置页面复选框
        self._sync_app_settings_ui()

    def _init_app_settings_ui(self) -> None:
        """初始化设置页面中的应用设置复选框"""
        self._chk_show_toolbar = self.findChild(QCheckBox, "chkShowToolbar")
        self._chk_auto_hide = self.findChild(QCheckBox, "chkAutoHideToolbar")
        self._chk_tray = self.findChild(QCheckBox, "chkMinimizeToTray")
        self._chk_autostart = self.findChild(QCheckBox, "chkAutoStart")
        self._spin_hide_delay = self.findChild(QSpinBox, "spinHideDelay")

        if self._chk_show_toolbar:
            self._chk_show_toolbar.toggled.connect(self._on_show_toolbar_toggled)
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

        self._save_pos_timer = QTimer(self)
        self._save_pos_timer.setSingleShot(True)
        self._save_pos_timer.timeout.connect(self._do_save_toolbar_pos)

        self._sync_app_settings_ui()

    def _sync_app_settings_ui(self) -> None:
        """将当前设置值同步到设置页面 UI"""
        if not self._app_settings:
            return

        show = self._app_settings.show_toolbar
        auto_hide = self._app_settings.auto_hide_toolbar

        if self._chk_show_toolbar:
            self._chk_show_toolbar.blockSignals(True)
            self._chk_show_toolbar.setChecked(show)
            self._chk_show_toolbar.blockSignals(False)
        if self._chk_auto_hide:
            self._chk_auto_hide.blockSignals(True)
            self._chk_auto_hide.setChecked(auto_hide)
            self._chk_auto_hide.setEnabled(show)
            self._chk_auto_hide.blockSignals(False)
        if self._spin_hide_delay:
            self._spin_hide_delay.blockSignals(True)
            self._spin_hide_delay.setValue(self._app_settings.hide_delay_ms)
            self._spin_hide_delay.setEnabled(show and auto_hide)
            self._spin_hide_delay.blockSignals(False)
        if self._chk_tray:
            self._chk_tray.blockSignals(True)
            self._chk_tray.setChecked(self._app_settings.minimize_to_tray)
            self._chk_tray.blockSignals(False)
        if self._chk_autostart:
            self._chk_autostart.blockSignals(True)
            self._chk_autostart.setChecked(self._app_settings.auto_start)
            self._chk_autostart.blockSignals(False)

    @Slot(bool)
    def _on_auto_hide_toggled(self, checked: bool) -> None:
        """自动隐藏复选框切换"""
        if self._app_settings:
            self._app_settings.auto_hide_toolbar = checked
            self._app_settings.save()
        self._edge_toolbar.set_auto_hide(checked)
        if self._spin_hide_delay:
            self._spin_hide_delay.setEnabled(checked)
        show_toast(self, "保存成功")
        logging.debug(f"自动隐藏工具栏: {'启用' if checked else '禁用'}")

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
            show_toast(self, "保存成功")
            logging.debug(f"工具栏隐藏延迟: {self._app_settings.hide_delay_ms}ms")

    @Slot(bool)
    def _on_show_toolbar_toggled(self, checked: bool) -> None:
        """显示工具栏复选框切换"""
        if self._app_settings:
            self._app_settings.show_toolbar = checked
            self._app_settings.save()
        if checked:
            pos = self._app_settings.toolbar_pos if self._app_settings else None
            if pos and "x" in pos and "y" in pos:
                self._edge_toolbar.move(pos["x"], pos["y"])
            else:
                self._edge_toolbar.set_initial_position()
            self._edge_toolbar.show()
        else:
            self._edge_toolbar.hide()
        if self._chk_auto_hide:
            self._chk_auto_hide.setEnabled(checked)
        if self._spin_hide_delay and self._app_settings:
            self._spin_hide_delay.setEnabled(
                checked and self._app_settings.auto_hide_toolbar
            )
        show_toast(self, "保存成功")
        logging.debug(f"显示边缘工具栏: {'启用' if checked else '禁用'}")

    @Slot(QPoint)
    def _on_toolbar_position_changed(self, pos: QPoint) -> None:
        """工具栏拖拽位置变更（防抖保存）"""
        if self._app_settings:
            self._app_settings.toolbar_pos = {"x": pos.x(), "y": pos.y()}
            self._save_pos_timer.start(500)

    def _do_save_toolbar_pos(self) -> None:
        """防抖延迟后实际保存工具栏位置"""
        if self._app_settings:
            self._app_settings.save()
            logging.debug(f"工具栏位置已保存: {self._app_settings.toolbar_pos}")

    @Slot(bool)
    def _on_minimize_to_tray_toggled(self, checked: bool) -> None:
        """最小化到托盘复选框切换"""
        if self._app_settings:
            self._app_settings.minimize_to_tray = checked
            self._app_settings.save()
        # 动态更新关闭窗口时是否退出程序
        from PySide6.QtWidgets import QApplication

        QApplication.setQuitOnLastWindowClosed(not checked)
        show_toast(self, "保存成功")
        logging.debug(f"最小化到系统托盘: {'启用' if checked else '禁用'}")

    @Slot(bool)
    def _on_autostart_toggled(self, checked: bool) -> None:
        """开机自启动复选框切换"""
        from vibeocr.utils.autostart import set_autostart

        success = set_autostart(checked)
        if success and self._app_settings:
            self._app_settings.auto_start = checked
            self._app_settings.save()
            show_toast(self, "保存成功")
            logging.debug(f"开机自启动: {'启用' if checked else '禁用'}")
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

    def bring_to_front(self) -> None:
        """将主窗口提到前台（由单实例守卫触发）。

        第二实例启动时检测到已运行实例，通过 SingleInstanceGuard 通知本实例
        调用此方法。复用 _show_main_window 的恢复逻辑（含最小化到托盘场景：
        showNormal 会取消最小化并 show 隐藏窗口）。
        """
        self._show_main_window()
