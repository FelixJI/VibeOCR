"""Main window view logic"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, cast

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
from vibeocr.managers import (
    ConfigManager,
    DependencyManager,
    LayoutManager,
    SubprocessManager,
)
from vibeocr.services.log_service import setup_logging
from vibeocr.ui.ui_main_window import Ui_MainWindowWidget
from vibeocr.views.batch_recognition_tab import BatchRecognitionTab
from vibeocr.views.clipboard_controller import ClipboardController
from vibeocr.views.settings_page_controller import SettingsPageController
from vibeocr.views.tabs.pdf_tab import PdfTab
from vibeocr.views.tabs.single_recognition_tab import SingleRecognitionTab
from vibeocr.widgets.screen_capture_overlay import ScreenCaptureOverlay
from vibeocr.widgets.toolbar import EdgeToolbar

if TYPE_CHECKING:
    from vibeocr.models.ocr_result import OCRResult
    from vibeocr.services.ocr_service_base import OCRServiceBase

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
        self._ocr_status_callback_fn: Any = None  # OCR 状态回调
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
            self._status_update_signal.emit(message)

        self._status_update_signal.connect(self._on_status_update)
        # 延迟到首次使用时才 import OCRService（避免启动时 ~0.1s 的 import 开销）
        self._ocr_status_callback_fn = on_ocr_status

    def _ensure_ocr_status_callback(self) -> None:
        """确保 OCR 状态回调已设置（首次 import OCRService 时调用）"""
        if not hasattr(self, "_ocr_status_callback_fn"):
            return
        if self._ocr_status_callback_fn is not None:
            from vibeocr.services.ocr_service import OCRService

            OCRService.set_status_callback(self._ocr_status_callback_fn)
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

        # 初始化 OCR 预设下拉框（包含截图组件和复制提示的初始化）
        self._init_preset_combo()

        # 添加批量识别标签页
        self._init_batch_tab()

        # 添加二维码生成标签页
        self._init_qrcode_tab()

        # 添加 PDF 处理标签页
        self._init_pdf_tab()

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

    def _init_batch_tab(self) -> None:
        """初始化批量识别标签页"""
        # 创建批量识别标签页
        self._batch_tab = BatchRecognitionTab()

        # 传递布局管理器
        self._batch_tab.set_layout_manager(self._layout_manager)

        # 添加到标签页控件
        self._ui.tabWidget.addTab(self._batch_tab, "批量识别")
        logging.debug("批量识别标签页已添加")

    def _init_qrcode_tab(self) -> None:
        """初始化二维码标签页"""
        from vibeocr.views.tabs.qrcode_tab import QrcodeTab

        self._qrcode_tab = QrcodeTab()
        self._ui.tabWidget.insertTab(
            self._ui.tabWidget.indexOf(self._ui.tabSettings),
            self._qrcode_tab,
            "二维码",
        )
        logging.debug("二维码标签页已添加")

    def _init_pdf_tab(self) -> None:
        """初始化 PDF 处理标签页"""
        self._pdf_tab = PdfTab()
        self._ui.tabWidget.insertTab(
            self._ui.tabWidget.indexOf(self._ui.tabSettings),
            self._pdf_tab,
            "PDF 处理",
        )
        logging.debug("PDF 处理标签页已添加")

    def _init_about_tab(self) -> None:
        """初始化关于标签页"""
        from vibeocr.views.tabs.about_tab import AboutTab

        self._about_tab = AboutTab()
        self._ui.tabWidget.addTab(self._about_tab, "关于")
        logging.debug("关于标签页已添加")

    def _setup_console(self) -> None:
        """初始化日志"""
        self._log_handler = setup_logging()
        self._log_handler.status_signal.connect(self._on_log_status_update)
        logging.info("VibeOCR 启动")

    def _connect_signals(self) -> None:
        """连接信号槽"""
        # 快捷键（替代已删除的菜单）
        self._shortcut_open = QShortcut(QKeySequence("Ctrl+O"), self)
        self._shortcut_open.activated.connect(self._on_open_image)

        self._shortcut_screenshot = QShortcut(QKeySequence("Ctrl+S"), self)
        self._shortcut_screenshot.activated.connect(self._on_screenshot)

        self._shortcut_quit = QShortcut(QKeySequence("Ctrl+Q"), self)
        self._shortcut_quit.activated.connect(self.close)

        # 截图组件
        self._overlay.confirmed.connect(self._on_overlay_confirmed)
        self._overlay.copied.connect(self._on_overlay_copied)
        self._overlay.saved.connect(self._on_overlay_saved)
        self._overlay.cancelled.connect(self._on_overlay_cancelled)

        # 单次识别 Tab 的截图/文件请求由 MainWindow 处理
        self._single_tab.screenshot_requested.connect(self._on_screenshot)
        self._single_tab.file_open_requested.connect(self._on_open_file_from_preview)

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
        )
        self._settings_controller.connect_signals()

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

        Returns:
            是否存在待同步标记（True 表示已弹出升级对话框接管流程）
        """
        from vibeocr.services.env_config import get_pending_sync_path

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
        if not changed:
            # 空标记，清理后走常规流程
            self._delete_pending_sync()
            return False

        version = data.get("version", "")
        pkgs = ", ".join(changed.keys())
        logging.info(f"[依赖同步] 检测到待同步标记（目标版本 {version}）：{changed}")
        self._statusbar.showMessage(f"正在同步 OCR 依赖更新：{pkgs}")

        from vibeocr.widgets.install_dialog import InstallDialog

        dialog = InstallDialog(self._project_root, self)
        dialog.setWindowTitle("同步 OCR 依赖更新")
        dialog._title_label.setText(
            f"检测到新版本依赖（{version}），正在同步更新：\n{pkgs}"
        )
        dialog.finished.connect(self._on_sync_finished)
        dialog.exec()
        return True

    @Slot(int)
    def _on_sync_finished(self, result: int) -> None:
        """依赖同步对话框完成"""
        if result == 1:
            # 升级成功，删除一次性标记
            self._delete_pending_sync()
            self._statusbar.showMessage("OCR 依赖同步完成")
            logging.info("[依赖同步] 依赖同步完成，重新检查依赖")
            # 清空依赖检测缓存，强制重新检测（否则旧缓存可能仍判就绪）
            import vibeocr.env_manager as em

            em._dep_specs_cache = None
            self._dependency_manager.reset()
            self._dependency_manager.check_dependencies()
            # 同步会重装 python/ 内的包，Python 运行时状态可能变化，刷新设置页 label
            self._refresh_settings_env_state()
        else:
            # 升级失败：保留标记供下次启动重试（与 pending_backend 同模式）
            self._ocr_ready = False
            self._statusbar.showMessage("OCR 依赖同步失败，将在下次启动重试")
            logging.warning("[依赖同步] 同步失败，保留 pending_sync.json 供重试")

    def _delete_pending_sync(self) -> None:
        """删除 pending_sync.json 标记文件（同步成功或标记无效时调用）"""
        from vibeocr.services.env_config import get_pending_sync_path

        pending_path = get_pending_sync_path()
        try:
            pending_path.unlink(missing_ok=True)
        except Exception as e:
            logging.warning(f"[依赖同步] 删除 pending_sync.json 失败: {e}")

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
        """依赖检测完成后启动子进程 Worker

        使用 SubprocessManager 管理子进程生命周期。
        """
        if self._closing:
            logging.debug("[MainWindow] 应用程序正在关闭，跳过启动子进程 Worker")
            return

        if self._subprocess_manager.is_ready:
            logging.debug("[MainWindow] 子进程 Worker 已就绪，跳过启动")
            return

        logging.debug("[MainWindow] 正在启动子进程 Worker...")
        use_gpu = env_manager.resolve_use_gpu(self._project_root)
        device = "GPU" if use_gpu else "CPU"
        self._statusbar.showMessage(f"正在启动 OCR 服务({device})...")

        # 将决策同步到主进程环境变量。OCR 子进程会由 ocr_worker.run_worker
        # 自行设置该变量，但主进程此前从未设置，导致跑在主进程 QThread 里的
        # PdfOcrWorker 读到空值、误判为 CPU（日志误报 + batch 走 RAM 公式）。
        os.environ["VIBEOCR_USE_GPU"] = "true" if use_gpu else "false"

        # 使用 SubprocessManager 启动
        self._subprocess_manager.start(use_gpu=use_gpu, start_timeout=120.0)

    @Slot(bool)
    def _on_subprocess_worker_ready(self, success: bool) -> None:
        """子进程 Worker 就绪回调"""
        if success:
            logging.debug("[MainWindow] 子进程 Worker 已就绪")
            self._ensure_ocr_status_callback()

            # 服务注入
            from vibeocr.services import get_ocr_service
            from vibeocr.services.mineru_batch_service import MinerUBatchService

            paddlex_service = get_ocr_service(skip_auto_start=True)
            mineru_batch = MinerUBatchService()

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
                self._pdf_tab.set_ocr_service(cast("OCRServiceBase", paddlex_service))
                logging.debug("[MainWindow] PDF 处理标签页已连接服务")

            # 子进程就绪后，触发预加载（如果配置了预加载管道）
            # 预加载完成后再显示"OCR 服务已就绪"
            from vibeocr.managers.config_manager import ConfigManager

            cm = ConfigManager.instance()
            pipelines = cm.get_preload_pipelines()
            if pipelines and cm.get_preload_enabled():
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
        logging.debug(f"[MainWindow] 预加载完成: {results}")

    @Slot(str)
    def _on_recognition_queued(self, message: str) -> None:
        """识别请求因预加载排队"""
        self._statusbar.showMessage(message)
        if hasattr(self, "_single_tab") and self._single_tab:
            self._single_tab.show_waiting_message(message)

    def _start_subprocess_preload(self) -> None:
        """在子进程中预加载用户配置的管道"""
        if not self._subprocess_manager.is_ready:
            return

        # 下发用户配置的 TTL 到 worker（无论是否预加载）
        from vibeocr.managers.config_manager import ConfigManager

        try:
            ttl = ConfigManager.instance().get_pipeline_ttl_seconds()
            service = self._subprocess_manager.service
            if service is None:
                logging.warning("[子进程预加载] service 未就绪，跳过 TTL 下发")
                return
            service.set_pipeline_ttl(ttl)
            logging.debug("[子进程预加载] 已下发 TTL=%d 到 worker", ttl)
        except Exception as e:
            logging.warning("[子进程预加载] 下发 TTL 失败: %s", e)

        # 获取用户配置的预加载管道
        from vibeocr.core.pipelines import OCRPipeline

        cm = ConfigManager.instance()
        if not cm.get_preload_enabled():
            logging.debug("[子进程预加载] 预加载已禁用")
            return

        raw_pipelines = cm.get_preload_pipelines()

        # 过滤无效的管道名称
        valid_values = {p.value for p in OCRPipeline}
        pipelines = [p for p in raw_pipelines if p in valid_values]

        if set(raw_pipelines) != set(pipelines):
            invalid = set(raw_pipelines) - set(pipelines)
            logging.warning(f"[子进程预加载] 忽略无效管道: {invalid}")

        if not pipelines:
            logging.debug("[子进程预加载] 未配置预加载管道")
            return

        logging.debug(f"[子进程预加载] 开始预加载管道: {pipelines}")

        # 使用 SubprocessManager 预加载
        self._subprocess_manager.preload_pipelines(pipelines)

    def _show_install_dialog(self, missing: list) -> None:
        """显示后端选择 + 安装对话框（首启合并对话框）"""
        from vibeocr.widgets.backend_choice_dialog import BackendChoiceDialog

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
        return True

    @Slot()
    def _on_screenshot(self) -> None:
        """开始截图"""
        if not self._check_ocr_ready():
            return

        self.showMinimized()
        # 延迟启动截图，让窗口有时间最小化
        QTimer.singleShot(200, self._overlay.start_capture)

    @Slot(QPixmap, object)
    def _on_overlay_confirmed(self, pixmap: QPixmap, options) -> None:
        """截图确认，执行 OCR

        options 来自截图面板（screenshot 源），首次识别保持用截图源选项；
        同时经 set_image_for_recognition 启用「重新识别」按钮——
        之后点「重新识别」会改用界面面板选项（main 源）。
        """
        self.showNormal()
        self.activateWindow()
        if not pixmap.isNull():
            self._single_tab.set_image_for_recognition(pixmap)
            self._single_tab.set_pixmap(pixmap)
            self._single_tab.run_ocr(pixmap, options)

    @Slot(QPixmap)
    def _on_overlay_copied(self, pixmap: QPixmap) -> None:
        """截图复制完成"""
        self.showNormal()
        self.activateWindow()
        self._statusbar.showMessage("图片已复制到剪贴板")

    @Slot(str)
    def _on_overlay_saved(self, file_path: str) -> None:
        """截图保存完成"""
        self.showNormal()
        self.activateWindow()
        self._statusbar.showMessage(f"图片已保存: {file_path}")

    @Slot()
    def _on_overlay_cancelled(self) -> None:
        """截图取消"""
        self.showNormal()
        self.activateWindow()

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

        # 显式清理 QWebEngineView（避免退出时 QtWebEngine 渲染进程崩溃 0xC0000409）
        for tab in (
            getattr(self, "_single_tab", None),
            getattr(self, "_batch_tab", None),
        ):
            if tab and hasattr(tab, "_result_widget") and tab._result_widget:
                tab._result_widget.cleanup()

        # 处理挂起事件，确保 WebEngine 渲染进程完全终止
        from PySide6.QtCore import QCoreApplication

        QCoreApplication.processEvents()

        # 清理 PDF 会话（关闭所有 fitz.Document）
        if hasattr(self, "_pdf_tab") and self._pdf_tab:
            self._pdf_tab.shutdown()

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
            logging.debug("子进程管理器已关闭")
        except Exception as e:
            logging.warning(f"关闭子进程管理器失败: {e}")

        # 清理 OCR 资源
        try:
            from vibeocr.services import USE_SUBPROCESS

            if not USE_SUBPROCESS:
                # 直连/便携模式（仅调试逃生口）：清理主进程内持有的管道缓存。
                # 子进程模式（默认）无需此清理——管道在独立 worker 进程中，
                # 随 worker 生命周期管理。
                from vibeocr.services.ocr_service import OCRService

                OCRService._pipelines.clear()
                logging.debug("OCR 管道缓存已清理")
        except Exception as e:
            logging.warning(f"清理 OCR 资源失败: {e}")

        # 清理 MinerU API 进程
        try:
            from vibeocr.services.mineru_service import MinerUService

            if MinerUService._api_process is not None:
                MinerUService().shutdown()
                logging.debug("MinerU API 服务已关闭")
        except Exception as e:
            logging.warning(f"关闭 MinerU API 服务失败: {e}")

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
        logging.debug(f"最小化到系统托盘: {'启用' if checked else '禁用'}")

    @Slot(bool)
    def _on_autostart_toggled(self, checked: bool) -> None:
        """开机自启动复选框切换"""
        from vibeocr.utils.autostart import set_autostart

        success = set_autostart(checked)
        if success and self._app_settings:
            self._app_settings.auto_start = checked
            self._app_settings.save()
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
