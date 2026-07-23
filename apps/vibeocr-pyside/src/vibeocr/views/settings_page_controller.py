"""设置页面控制器

处理设置页面的逻辑，包括预加载和缓存管理。
"""

import logging
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QThreadPool, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from vibeocr.env_manager import (
    check_dependencies_status_detailed,
    get_dependency_versions,
    get_direct_dependencies,
    get_embedded_python_executable,
    get_embedded_python_info,
    get_environment_mode,
)
from vibeocr.machine_cache import is_cache_valid
from vibeocr.pyside import settings_runtime
from vibeocr.views.background_tasks import DependencyUpdateCheckTask, FunctionTask
from vibeocr.widgets.backend_choice_dialog import BackendChoiceDialog

if TYPE_CHECKING:
    from vibeocr.contracts.pipelines import OCRPipeline

logger = logging.getLogger(__name__)

# QRunnable 运行期间的进程级强引用。窗口可先于慢 WMIC/PowerShell/RPC 完成销毁；
# 保留 wrapper 到结果回调，避免 Qt 线程池仍持有 C++ runnable 时 Python 对象被回收。
_BACKGROUND_TASKS: set[object] = set()


def _is_bundled() -> bool:
    """检测当前是否为 PyInstaller 打包态。"""
    return bool(getattr(sys, "frozen", False))


def _resolve_shortcut_icon_path() -> str:
    """解析快捷方式图标路径（.ico），兼容开发态与打包态。"""
    from vibeocr import env_manager

    icon = env_manager.get_bundled_resources_dir() / "app_icon.ico"
    return str(icon) if icon.exists() else ""


def _create_windows_shortcut(
    target: str,
    shortcut_path: str,
    description: str = "VibeOCR",
    icon_path: str = "",
    working_dir: str = "",
) -> bool:
    """在 Windows 上通过 PowerShell COM 创建 .lnk 快捷方式。"""
    # 确保目标目录存在
    try:
        Path(shortcut_path).parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    ps_lines = [
        "$WshShell = New-Object -ComObject WScript.Shell",
        f"$Shortcut = $WshShell.CreateShortcut('{shortcut_path}')",
        f"$Shortcut.TargetPath = '{target}'",
        f"$Shortcut.Description = '{description}'",
    ]
    if icon_path:
        ps_lines.append(f"$Shortcut.IconLocation = '{icon_path}'")
    if working_dir:
        ps_lines.append(f"$Shortcut.WorkingDirectory = '{working_dir}'")
    ps_lines.append("$Shortcut.Save()")

    script = "; ".join(ps_lines)
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0
    except Exception:
        logger.exception("PowerShell 创建快捷方式失败")
        return False


class SettingsPageController:
    """设置页面控制器

    处理设置页面的所有逻辑，与 UI 控件通过 findChild 方式交互。
    """

    def __init__(
        self,
        ui: QWidget,
        project_root: Path,
        status_callback: Callable[[str], None],
        ocr_ready_callback: Callable[[], bool],
        subprocess_manager,
        preload_complete_callback: Callable[[], None] | None = None,
        install_succeeded_callback: Callable[[], None] | None = None,
        gpu_capability_callback: Callable[[bool], None] | None = None,
        dependency_update_task: DependencyUpdateCheckTask | None = None,
        defer_backend_initialization: bool = False,
        defer_machine_cache_status: bool = False,
    ) -> None:
        self._ui = ui
        self._project_root = project_root
        self._status_callback = status_callback
        self._ocr_ready_callback = ocr_ready_callback
        self._subprocess_manager = subprocess_manager
        self._preload_complete_callback = preload_complete_callback
        # 设置页重装/补装依赖成功后的联动回调（由 MainWindow 提供）。
        # 回归（Bug A）：旧逻辑设置页 BackendChoiceDialog 只连 finished 刷新表格，
        # 没联动 MainWindow._ocr_ready / 子进程 Worker，导致装完仍提示"未就绪"。
        # 现由 MainWindow 传入一个触发 dependency_manager.check_dependencies
        # 的回调，使设置页安装成功后与首启路径行为一致（检测完成回调里自动
        # 设 _ocr_ready + 启动 Worker + 消费 pending_backend）。
        self._install_succeeded_callback = install_succeeded_callback
        self._gpu_capability_callback = gpu_capability_callback
        self._dependency_update_task = dependency_update_task or DependencyUpdateCheckTask(
            project_root, ui
        )
        self._owns_dependency_update_task = dependency_update_task is None
        self._runtime_has_gpu: bool | None = None
        self._defer_backend_initialization = defer_backend_initialization
        self._defer_machine_cache_status = defer_machine_cache_status
        self._pending_update_install = False
        self._manual_dependency_update_waiting = False
        self._manual_preload_task: object | None = None
        self._backend_options = None
        self._closing = False
        self._cache_tasks: set[object] = set()
        self._cache_generation = 0
        self._env_refresh_generation = 0
        self._machine_cache_generation = 0
        self._cache_refresh_running = False
        self._shortcut_running = False
        self._ttl_sync_timer = QTimer(ui)
        self._ttl_sync_timer.setSingleShot(True)
        self._ttl_sync_timer.setInterval(300)
        self._ttl_sync_timer.timeout.connect(self._sync_configured_pipeline_ttls)
        # 非模态重装对话框引用：show() 后须持有，否则被 GC 立即销毁；
        # 对话框 finished 时从列表移除，允许再次打开。
        self._active_dialogs: list = []

        # 控制器不是 QObject，独立测试/嵌入场景可能不会显式调用 shutdown；
        # 宿主 widget 销毁时先冻结后台回调，避免迟到结果访问已释放的 Qt 对象。
        ui.destroyed.connect(self.request_shutdown)

        self._dependency_update_task.completed.connect(
            self._on_dependency_update_check_completed
        )
        self._dependency_update_task.failed.connect(
            self._on_dependency_update_check_failed
        )

    def request_shutdown(self) -> None:
        """Release background workers owned by settings-page widgets.

        取消手动预加载任务（协作取消）并断开 signal，避免迟到回调访问
        已销毁的 UI。再关闭 GPU 检测线程。
        """
        self._closing = True
        if self._owns_dependency_update_task:
            self._dependency_update_task.close()
        self._ttl_sync_timer.stop()
        for dialog in tuple(self._active_dialogs):
            request_shutdown = getattr(dialog, "request_shutdown", None)
            if callable(request_shutdown):
                request_shutdown()
            else:
                close = getattr(dialog, "close", None)
                if callable(close):
                    close()
        # 不清空正在运行的 QRunnable 引用：QThreadPool 结束前销毁其 Python
        # wrapper/Signals 可能导致 use-after-free。完成回调会先 discard，再由
        # _closing 守卫跳过所有 UI 操作。

        # 取消正在运行的手动预加载任务（运行在全局 QThreadPool 上）
        if self._manual_preload_task is not None:
            cancel_preload = getattr(
                self._subprocess_manager, "request_preload_shutdown", None
            )
            if callable(cancel_preload):
                cancel_preload()
            self._manual_preload_task = None

        backend_options = self._backend_options
        if backend_options is not None:
            backend_options.request_gpu_detection_shutdown()

    def drain(self, timeout_ms: int) -> bool:
        """Compatibility drain covering every settings-owned native/UI task."""
        import time

        from PySide6.QtCore import QCoreApplication, QThread

        deadline = time.monotonic() + max(0, timeout_ms) / 1000
        while True:
            if self.is_drained():
                return True
            if timeout_ms <= 0 or time.monotonic() >= deadline:
                return False
            # 独立调用场景没有 MainWindow poll timer；推进 queued completion，
            # 让 cache/dialog/update 引用在 owner GUI 线程上安全释放。
            QCoreApplication.processEvents()
            QThread.msleep(5)

    def is_drained(self) -> bool:
        """Poll all settings-owned native jobs without waiting on the GUI thread."""
        backend_options = self._backend_options
        gpu_drained = backend_options is None or bool(
            backend_options.is_gpu_detection_drained()
        )
        # The completion callback removes each task on the GUI thread.  Waiting
        # for the set to become empty also drains queued callbacks that capture UI.
        cache_drained = not self._cache_tasks
        update_drained = (
            not self._owns_dependency_update_task
            or self._dependency_update_task.is_drained()
        )
        preload_probe = getattr(self._subprocess_manager, "is_preload_drained", None)
        preload_drained = not callable(preload_probe) or bool(preload_probe())
        from vibeocr.utils.dialog_workers import are_dialog_workers_drained

        dialogs_drained = are_dialog_workers_drained()
        return (
            gpu_drained
            and cache_drained
            and update_drained
            and preload_drained
            and dialogs_drained
        )

    def shutdown(self, timeout_ms: int = 3000) -> bool:
        """Compatibility entry point for callers outside MainWindow."""
        self.request_shutdown()
        return self.drain(timeout_ms)

    def connect_signals(self) -> None:
        """连接设置页面的信号槽"""
        nav_list = self._ui.findChild(QListWidget, "settingsNavList")
        stacked = self._ui.findChild(QStackedWidget, "settingsStackedWidget")
        if nav_list and stacked:
            nav_list.currentRowChanged.connect(stacked.setCurrentIndex)

        chk_enable_preload = self._ui.findChild(QCheckBox, "chkEnablePreload")
        if chk_enable_preload:
            chk_enable_preload.toggled.connect(self._on_enable_preload_toggled)

        btn_preload_now = self._ui.findChild(QPushButton, "btnPreloadNow")
        if btn_preload_now:
            btn_preload_now.clicked.connect(self._on_preload_now_clicked)

        self._subprocess_manager.preload_progress.connect(
            self._on_manual_preload_progress
        )
        self._subprocess_manager.preload_finished.connect(
            self._on_manual_preload_finished
        )

        self._init_log_level_control()

        for pipeline in self._get_preloadable_pipelines():
            chk = self._ui.findChild(QCheckBox, f"chkPreload_{pipeline.name}")
            if chk:
                chk.toggled.connect(self._save_preload_pipelines_config)

        btn_refresh_cache = self._ui.findChild(QPushButton, "btnRefreshCache")
        if btn_refresh_cache:
            btn_refresh_cache.clicked.connect(self._on_refresh_cache_clicked)

        btn_clear_cache = self._ui.findChild(QPushButton, "btnClearCache")
        if btn_clear_cache:
            btn_clear_cache.clicked.connect(self._on_clear_cache_clicked)

        # --- 管道缓存生命周期管理 ---
        # 旧的 spinPipelineTtl / chkEnablePipelineTtl 已被每管道 TTL ComboBox
        # 取代（_init_pipeline_ttl_combos 在 _init_settings_page 内构造并接线）。
        btn_refresh_pipeline_cache = self._ui.findChild(
            QPushButton, "btnRefreshPipelineCache"
        )
        if btn_refresh_pipeline_cache:
            btn_refresh_pipeline_cache.clicked.connect(
                self._on_refresh_pipeline_cache_clicked
            )

        btn_release_heavy = self._ui.findChild(QPushButton, "btnReleaseHeavy")
        if btn_release_heavy:
            btn_release_heavy.clicked.connect(self._on_release_heavy_clicked)

        btn_release_all = self._ui.findChild(QPushButton, "btnReleaseAll")
        if btn_release_all:
            btn_release_all.clicked.connect(self._on_release_all_clicked)

        # --- 环境维护：重装 Python 运行时 / 重装 OCR 依赖 / 补充安装缺失依赖 ---
        btn_reinstall_python = self._ui.findChild(QPushButton, "btnReinstallPython")
        if btn_reinstall_python:
            btn_reinstall_python.clicked.connect(self._on_reinstall_python)

        btn_reinstall_deps = self._ui.findChild(QPushButton, "btnReinstallDeps")
        if btn_reinstall_deps:
            btn_reinstall_deps.clicked.connect(self._on_reinstall_deps)

        btn_install_missing = self._ui.findChild(QPushButton, "btnInstallMissing")
        if btn_install_missing:
            btn_install_missing.clicked.connect(self._on_install_missing)

        btn_update_deps = self._ui.findChild(QPushButton, "btnUpdateDeps")
        if btn_update_deps:
            btn_update_deps.clicked.connect(self._on_update_deps)

        self._refresh_env_maintenance_state()

        self._init_shortcut_buttons()

        self._init_screenshot_options(nav_list, stacked)
        self._init_pdf_options(nav_list, stacked)
        if not self._defer_backend_initialization:
            self._init_backend_options_in_group()
        self._init_settings_page()

        # 所有子页（静态 .ui 页 + 动态插入页）就绪后统一包滚动条，
        # 规范设置界面滚动行为：内容超出窗口高度时出垂直滚动条而非被裁剪。
        self._wrap_settings_pages_in_scroll()

    # ----------------------------------------------------------------
    # Toast 提示
    # ----------------------------------------------------------------

    def _show_settings_toast(self, text: str = "保存成功") -> None:
        """在所属窗口顶部居中显示 Toast 通知。"""
        try:
            from vibeocr.widgets.toast_widget import show_toast

            # 查找顶层窗口作为 toast 父控件，避免被 Tab 裁剪
            window = self._ui
            if hasattr(window, "window"):
                window = window.window()
            show_toast(window, text)
        except Exception:
            # 不允许影响主流程，但必须记录完整堆栈——
            # 旧实现用 logger.debug 无 exc_info，吞掉真正的异常类型与 traceback，
            # 导致 [Toast] 显示失败 日志无法定位根因（用户实测：更新依赖按钮无反应，
            # 唯一线索就是这行被吞的异常）。现用 logger.exception 落盘完整堆栈。
            logger.exception("[Toast] 显示失败（不影响主流程，但请上报此堆栈）")

    # ----------------------------------------------------------------
    # 快捷方式创建
    # ----------------------------------------------------------------

    def _init_shortcut_buttons(self) -> None:
        """在「应用设置」分组底部动态添加快捷方式按钮。

        按钮仅在 Windows 打包态可点击；开发态灰显并提示。
        """
        group = self._ui.findChild(QWidget, "groupAppSettings")
        if group is None:
            return

        layout = group.layout()
        if layout is None:
            return

        # 水平按钮行
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)

        self._btn_desktop = QPushButton("发送快捷方式到桌面")
        self._btn_desktop.setToolTip("在桌面上创建 VibeOCR 快捷方式")
        self._btn_desktop.clicked.connect(self._on_create_desktop_shortcut)
        row_layout.addWidget(self._btn_desktop)

        self._btn_startmenu = QPushButton("发送快捷方式到开始菜单")
        self._btn_startmenu.setToolTip("在开始菜单中创建 VibeOCR 快捷方式")
        self._btn_startmenu.clicked.connect(self._on_create_start_menu_shortcut)
        row_layout.addWidget(self._btn_startmenu)

        row_layout.addStretch()

        # 非打包态禁用按钮并修改文案
        if not _is_bundled():
            self._btn_desktop.setEnabled(False)
            self._btn_desktop.setToolTip("仅在打包版本中可用")
            self._btn_startmenu.setEnabled(False)
            self._btn_startmenu.setToolTip("仅在打包版本中可用")

        layout.addWidget(row)

    def _on_create_desktop_shortcut(self) -> None:
        """在桌面创建 VibeOCR 快捷方式。"""
        if not _is_bundled():
            self._show_settings_toast("仅在打包版本中可用")
            return

        desktop = Path(os.environ.get("USERPROFILE", "")) / "Desktop"
        lnk = str(desktop / "VibeOCR.lnk")
        target = sys.executable
        icon = _resolve_shortcut_icon_path()
        wd = str(Path(sys.executable).parent)

        self._start_shortcut_creation(
            target, lnk, icon, wd, success_text="桌面快捷方式已创建"
        )

    def _on_create_start_menu_shortcut(self) -> None:
        """在开始菜单创建 VibeOCR 快捷方式。"""
        if not _is_bundled():
            self._show_settings_toast("仅在打包版本中可用")
            return

        start_menu = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "VibeOCR"
        lnk = str(start_menu / "VibeOCR.lnk")
        target = sys.executable
        icon = _resolve_shortcut_icon_path()
        wd = str(Path(sys.executable).parent)

        self._start_shortcut_creation(
            target, lnk, icon, wd, success_text="开始菜单快捷方式已创建"
        )

    def _start_shortcut_creation(
        self,
        target: str,
        shortcut_path: str,
        icon: str,
        working_dir: str,
        *,
        success_text: str,
    ) -> None:
        """在线程池创建快捷方式，同一时刻只允许一个 PowerShell 操作。"""
        if self._closing or self._shortcut_running:
            return
        self._shortcut_running = True
        self._set_shortcut_buttons_enabled(False)

        def operation() -> bool:
            return _create_windows_shortcut(
                target, shortcut_path, "VibeOCR", icon, working_dir
            )

        def finished(success: bool) -> None:
            self._shortcut_running = False
            self._set_shortcut_buttons_enabled(True)
            if success:
                self._show_settings_toast(success_text)
            else:
                QMessageBox.warning(
                    None,
                    "创建失败",
                    "创建快捷方式失败或操作超时，请检查权限。",
                )

        def failed(error: str) -> None:
            logger.warning("创建快捷方式后台任务失败: %s", error)
            finished(False)

        self._run_cache_operation(operation, finished, failed)

    def _set_shortcut_buttons_enabled(self, enabled: bool) -> None:
        if not _is_bundled():
            enabled = False
        for name in ("_btn_desktop", "_btn_startmenu"):
            button = getattr(self, name, None)
            if button is not None:
                button.setEnabled(enabled)

    # ----------------------------------------------------------------
    # 截图 / PDF 选项页初始化
    # ----------------------------------------------------------------

    def _init_screenshot_options(
        self, nav_list: QListWidget | None, stacked: QStackedWidget | None
    ) -> None:
        """初始化截图面板选项页面。

        按管道分组展示预处理参数（无管道下拉框）：识别类型由截图工具栏按钮
        唯一决定，此处仅配置各管道的预处理参数。
        """
        if not nav_list or not stacked:
            return

        from vibeocr.widgets.screenshot_options_widget import (
            ScreenshotOptionsWidget,
        )

        # 添加导航项和页面
        nav_list.addItem("截图选项")

        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(16, 16, 16, 16)
        page_layout.setSpacing(12)

        self._screenshot_options = ScreenshotOptionsWidget()
        page_layout.addWidget(self._screenshot_options)

        spacer = QSpacerItem(
            20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
        )
        page_layout.addItem(spacer)

        stacked.addWidget(page)

        # 截图选项变更时弹出保存成功提示
        self._screenshot_options.options_changed.connect(
            lambda _: self._show_settings_toast()
        )

        # ScreenshotOptionsWidget 自管持久化（构造时 load、变更时直接写
        # screenshot 源），此处无需连接信号。

    def _init_pdf_options(
        self, nav_list: QListWidget | None, stacked: QStackedWidget | None
    ) -> None:
        """初始化 PDF 选项页面。"""
        if not nav_list or not stacked:
            return

        from vibeocr.utils.ocr_preferences import OCRPreferences
        from vibeocr.widgets.pdf_options_widget import PdfOptionsWidget

        nav_list.addItem("PDF 选项")

        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(16, 16, 16, 16)
        page_layout.setSpacing(12)

        self._pdf_options = PdfOptionsWidget()
        page_layout.addWidget(self._pdf_options)

        spacer = QSpacerItem(
            20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
        )
        page_layout.addItem(spacer)
        stacked.addWidget(page)

        # 恢复保存的设置
        try:
            prefs = OCRPreferences.instance()
            # 管道选项
            default_pipeline = self._pdf_options.pipeline_options.get_current_pipeline()
            self._pdf_options.pipeline_options.set_options(
                prefs.get_pipeline_options("pdf", default_pipeline)
            )
            # 全局设置
            self._pdf_options.set_settings(prefs.get_pdf_settings())
        except RuntimeError:
            pass

        # 连接管道选项信号
        self._pdf_switching = False
        self._pdf_options.pipeline_options.pipeline_switching.connect(
            self._on_pdf_pipeline_switching
        )
        self._pdf_options.pipeline_options.pipeline_switched.connect(
            self._on_pdf_pipeline_switched
        )
        self._pdf_options.pipeline_options.options_changed.connect(
            self._on_pdf_option_changed
        )

        # 连接全局设置信号
        self._pdf_options.settings_changed.connect(self._on_pdf_settings_changed)

    def _init_backend_options_in_group(self) -> None:
        """把推理后端组件放入「应用设置」页的「推理后端与依赖」分组内。

        推理后端（GPU/CPU 选择）与 OCR 依赖安装本质上是同一件事——后端决定
        要装哪些依赖，依赖表格/重装按钮负责查看与维护这些依赖。故合并到同一
        分组，不再单列导航项。
        """
        container = self._ui.findChild(QWidget, "backendOptionsContainer")
        if container is None or self._backend_options is not None:
            return

        from vibeocr.widgets.backend_options_widget import BackendOptionsWidget

        self._backend_options = BackendOptionsWidget(
            self._project_root,
            gpu_capability_callback=self._on_gpu_capability_resolved,
        )
        layout = container.layout()
        if layout is not None:
            layout.addWidget(self._backend_options)

        # 后端切换时弹出保存成功提示
        self._backend_options.backend_changed.connect(
            lambda: self._show_settings_toast()
        )

    def initialize_deferred_backend_options(self) -> None:
        """机器缓存已在后台预热后，再构造 GPU 设置组件。"""
        if not self._closing:
            self._init_backend_options_in_group()

    def apply_deferred_machine_cache_status(self, valid: bool) -> None:
        """应用 MainWindow 后台缓存校验结果，不再次触发机器码探测。"""
        if not self._closing:
            self._update_cache_status("缓存有效" if valid else "无有效缓存")

    def _on_gpu_capability_resolved(self, has_gpu: bool) -> None:
        """记录既有后台探测结果，并向 MainWindow 广播。"""
        if self._closing:
            return
        self._runtime_has_gpu = bool(has_gpu)
        if self._gpu_capability_callback is not None:
            self._gpu_capability_callback(bool(has_gpu))
        if self._pending_update_install:
            self._pending_update_install = False
            self._open_install_dialog(
                missing_only=False,
                force_backend="gpu" if has_gpu else "cpu",
            )

    def _on_pdf_pipeline_switching(self, old_pipeline, options) -> None:
        self._pdf_switching = True
        try:
            from vibeocr.utils.ocr_preferences import OCRPreferences

            OCRPreferences.instance().set_pdf_pipeline_options(options)
        except RuntimeError:
            pass

    def _on_pdf_pipeline_switched(self, new_pipeline) -> None:
        try:
            from vibeocr.utils.ocr_preferences import OCRPreferences

            loaded = OCRPreferences.instance().get_pipeline_options("pdf", new_pipeline)
            self._pdf_options.pipeline_options.set_options(loaded)
        except RuntimeError:
            pass
        self._pdf_switching = False

    def _on_pdf_option_changed(self, options) -> None:
        if self._pdf_switching:
            return
        try:
            from vibeocr.utils.ocr_preferences import OCRPreferences

            OCRPreferences.instance().set_pdf_pipeline_options(options)
            self._show_settings_toast()
        except RuntimeError:
            pass

    def _on_pdf_settings_changed(self, settings) -> None:
        try:
            from vibeocr.utils.ocr_preferences import OCRPreferences

            OCRPreferences.instance().set_pdf_settings(settings)
            self._show_settings_toast()
        except RuntimeError:
            pass

    def _init_settings_page(self) -> None:
        """初始化设置页面状态"""
        if self._defer_machine_cache_status:
            self._update_cache_status("正在检查缓存...")
        else:
            # 独立嵌入场景没有 MainWindow 的共享启动快照；避免仅为初始文案
            # 主动触发 WMIC，用户点击"刷新缓存"时再走后台 operation。
            self._update_cache_status("缓存状态尚未刷新")
        self._update_preload_status()
        self._restore_preload_checkbox_state()
        # 引入拆分后的 labelPipelineCacheStatus（运行时层），再构造每管道 TTL
        # ComboBox。前者由 _on_pipeline_cache_status 写入，后者由用户操作触发。
        self._init_pipeline_cache_status_label()
        self._init_pipeline_ttl_combos()

    def _init_log_level_control(self) -> None:
        """在应用设置页加入持久化日志级别选择。"""
        if self._ui.findChild(QComboBox, "comboLogLevel") is not None:
            return
        layout = self._ui.findChild(QVBoxLayout, "appSettingsLayout")
        if layout is None:
            return
        row = QWidget(self._ui)
        row.setObjectName("logLevelRow")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel("日志级别：", row)
        combo = QComboBox(row)
        combo.setObjectName("comboLogLevel")
        combo.addItem("普通（推荐）", "INFO")
        combo.addItem("调试（详细）", "DEBUG")
        combo.addItem("仅警告与错误", "WARNING")
        combo.setToolTip("普通模式会过滤 HTTP、模型框架等底层调试输出")
        row_layout.addWidget(label)
        row_layout.addWidget(combo)
        row_layout.addStretch(1)
        layout.addWidget(row)

        saved = settings_runtime.get_log_level()
        index = combo.findData(saved)
        combo.setCurrentIndex(max(0, index))
        combo.currentIndexChanged.connect(self._on_log_level_changed)

    def _on_log_level_changed(self) -> None:
        combo = self._ui.findChild(QComboBox, "comboLogLevel")
        if combo is None:
            return
        level = str(combo.currentData() or "INFO")
        if settings_runtime.set_log_level(level):
            self._show_settings_toast("日志级别已更新；WorkerHost 将在下次重连时应用")

    # ----------------------------------------------------------------
    # 每管道 TTL ComboBox（替代旧 spinPipelineTtl + chkEnablePipelineTtl）
    # ----------------------------------------------------------------

    #: TTL 预设档：显示文本 → 秒数。0 仅禁用闲置回收，并非无条件常驻。
    _TTL_PRESETS: list[tuple[str, int]] = [
        ("不因闲置 TTL 回收", 0),
        ("1 分钟", 60),
        ("3 分钟", 180),
        ("5 分钟", 300),
        ("10 分钟", 600),
        ("15 分钟", 900),
        ("30 分钟", 1800),
    ]

    def _init_pipeline_ttl_combos(self) -> None:
        """在「模型管理 → 运行时缓存」分组内追加每管道 TTL ComboBox。

        原型由 spinPipelineTtl + chkEnablePipelineTtl（单 TTL 适用于所有管道）改为
        6 个独立 ComboBox，分别对应 OCRPipeline 枚举的每一项。每个 ComboBox 携带
        相同的 7 档预设（_TTL_PRESETS），选中项经 ConfigManager.set_pipeline_ttl
        持久化，并通过 _sync_configured_pipeline_ttls 批量下发到 worker。

        幂等：重复调用时若已存在 comboTtl_OCR 则直接返回。
        """
        layout = self._ui.findChild(QVBoxLayout, "runtimeCacheLayout")
        if layout is None:
            logger.warning(
                "[TTL Combos] runtimeCacheLayout 未找到，跳过 ComboBox 创建"
            )
            return
        if self._ui.findChild(QComboBox, "comboTtl_OCR") is not None:
            logger.debug("[TTL Combos] 已存在 comboTtl_OCR，跳过（幂等）")
            return

        from vibeocr.contracts.pipelines import OCRPipeline, get_pipeline_display_name
        from vibeocr.pyside.runtime import ConfigManager

        ttls = ConfigManager.instance().get_pipeline_ttls()
        created_count = 0
        for pipeline in OCRPipeline:
            row = QWidget(self._ui)
            row.setObjectName(f"ttlRow_{pipeline.value}")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            label = QLabel(get_pipeline_display_name(pipeline), row)
            combo = QComboBox(row)
            combo.setObjectName(f"comboTtl_{pipeline.value}")
            for display_text, secs in self._TTL_PRESETS:
                # 第二参数写入 UserRole data，_restore_pipeline_ttl_combos 用
                # findData 反查索引（比匹配显示文本更稳）。
                combo.addItem(display_text, secs)
            self._select_ttl_combo(combo, ttls.get(pipeline.value, 0))
            no_idle_eviction_tip = (
                "不因闲置 TTL 回收，但仍受显存并存上限、显式释放、"
                "应用退出和进程终止影响。"
            )
            combo.setToolTip(no_idle_eviction_tip)
            # MinerU 使用独立 API 进程；有限 TTL 到期会真实停止该进程。
            if pipeline == OCRPipeline.DOCUMENT_PARSING:
                mineru_tip = (
                    f"{no_idle_eviction_tip}"
                    "设置有限 TTL 后，MinerU 闲置到期会停止 API 进程；"
                    "下次使用时会重新启动。"
                )
                label.setToolTip(mineru_tip)
                combo.setToolTip(mineru_tip)
            # 默认绑定 pipeline.value；lambda 显式捕获避免闭包晚绑定陷阱。
            combo.currentIndexChanged.connect(
                lambda _idx, name=pipeline.value, c=combo: (
                    self._on_pipeline_ttl_combo_changed(name, c)
                )
            )
            row_layout.addWidget(label)
            row_layout.addWidget(combo)
            row_layout.addStretch(1)
            layout.addWidget(row)
            created_count += 1
        logger.info(
            "[TTL Combos] 已创建 %d 个 ComboBox (layout count=%d)",
            created_count,
            layout.count(),
        )

    def _select_ttl_combo(self, combo: QComboBox, ttl: int) -> None:
        """根据 TTL 秒数选中 ComboBox 项（UserRole data 精确匹配，无匹配回退持久）。"""
        index = combo.findData(ttl)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _restore_pipeline_ttl_combos(self) -> None:
        """从配置恢复所有 TTL ComboBox 选中项（阻塞信号避免触发下发）。"""
        from vibeocr.contracts.pipelines import OCRPipeline
        from vibeocr.pyside.runtime import ConfigManager

        ttls = ConfigManager.instance().get_pipeline_ttls()
        for pipeline in OCRPipeline:
            combo = self._ui.findChild(QComboBox, f"comboTtl_{pipeline.value}")
            if combo is None:
                continue
            current_ttl = ttls.get(pipeline.value, 0)
            combo.blockSignals(True)
            self._select_ttl_combo(combo, current_ttl)
            combo.blockSignals(False)

    def _on_pipeline_ttl_combo_changed(
        self, pipeline_name: str, combo: QComboBox
    ) -> None:
        """单个管道 TTL 改变 → 写配置 + 防抖下发 worker + toast。

        UI 线程边界：本函数仅调用 ConfigManager（本地 JSON 读写，非阻塞）与
        _ttl_sync_timer.start（异步触发 _sync_configured_pipeline_ttls，后者再走
        _run_cache_operation 线程池）。**禁止**直接调用 env_manager.* 或同步 RPC。
        """
        idx = combo.currentIndex()
        if idx < 0 or idx >= len(self._TTL_PRESETS):
            return
        _display, ttl = self._TTL_PRESETS[idx]
        from vibeocr.pyside.runtime import ConfigManager

        if not ConfigManager.instance().set_pipeline_ttl(pipeline_name, ttl):
            logger.warning("[TTL] 写入配置失败: %s=%d", pipeline_name, ttl)
            return
        self._show_settings_toast()
        # 防抖：连续切换档位时只下发最后一次到 worker（_ttl_sync_timer 在
        # __init__ 已 connect 到 _sync_configured_pipeline_ttls）。
        self._ttl_sync_timer.start()

    def _wrap_settings_pages_in_scroll(self) -> None:
        """把 settingsStackedWidget 的每个子页包进 QScrollArea。

        修复：原页面直接塞进 QStackedWidget 无滚动区，窗口高度不足时内容被裁剪
        （用户反馈"部分内容显示区域很矮/看不见"）。包一层 setWidgetResizable
        的 QScrollArea 后，垂直超出自动出滚动条，水平不滚动（宽度跟随窗口）。

        所有子页（pageModelManagement / pageAppSettings / 截图选项 / PDF 选项）
        统一处理。原页 widget 从 stacked 移除、用 scroll 替换占位，
        索引与导航行（currentRowChanged→setCurrentIndex）保持一一对应。
        """
        from PySide6.QtCore import Qt

        stacked = self._ui.findChild(QStackedWidget, "settingsStackedWidget")
        if stacked is None:
            return

        # 先 snapshot 所有原页（按索引顺序），再统一清空 + 按顺序回填 scroll。
        # 循环中边遍历边 insert/remove 会导致索引错位、widget(i) 返回 None
        # （AttributeError 根因）。snapshot 后先全部脱离 stacked，再顺序加回，
        # 保证索引与导航行（currentRowChanged→setCurrentIndex）一一对应。
        pages: list[QWidget] = []
        for i in range(stacked.count()):
            page = stacked.widget(i)
            if page is not None:
                pages.append(page)

        # 全部从 stacked 移除（setParent(None) 同时解除父子关系）
        for page in pages:
            stacked.removeWidget(page)

        # 按原顺序加回（每个包一层 scroll，已包裹的幂等跳过）
        for page in pages:
            if isinstance(page, QScrollArea):
                stacked.addWidget(page)
                continue
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)  # 内容宽度跟随 scroll，不出水平滚动条
            scroll.setFrameShape(QFrame.Shape.NoFrame)  # 无边框，视觉与原页一致
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            page.setParent(None)  # 解除与原 stacked 的父子关系
            scroll.setWidget(page)
            page.setAutoFillBackground(False)
            stacked.addWidget(scroll)

    @staticmethod
    def _get_preloadable_pipelines():
        """获取可预加载的管道列表"""
        from vibeocr.contracts.pipelines import get_preloadable_pipelines

        return get_preloadable_pipelines()

    def _restore_preload_checkbox_state(self) -> None:
        """从配置恢复预加载 checkbox 状态（阻塞信号避免触发保存）"""
        from vibeocr.pyside.runtime import ConfigManager

        cm = ConfigManager.instance()
        saved = cm.get_preload_pipelines()
        # 大小写不敏感匹配，兼容历史小写配置（如 'table_recognition'）
        saved_lower = {s.lower() for s in saved}
        for pipeline in self._get_preloadable_pipelines():
            chk = self._ui.findChild(QCheckBox, f"chkPreload_{pipeline.name}")
            if chk:
                chk.blockSignals(True)
                chk.setChecked(pipeline.value.lower() in saved_lower)
                chk.blockSignals(False)

        chk_enable = self._ui.findChild(QCheckBox, "chkEnablePreload")
        if chk_enable:
            chk_enable.blockSignals(True)
            chk_enable.setChecked(cm.get_preload_enabled())
            chk_enable.blockSignals(False)
            self._on_enable_preload_toggled(cm.get_preload_enabled())

    def _on_enable_preload_toggled(self, checked: bool) -> None:
        """启用/禁用预加载"""
        preload_options = self._ui.findChild(QWidget, "preloadOptions")
        if preload_options:
            preload_options.setEnabled(checked)
        from vibeocr.pyside.runtime import ConfigManager

        ConfigManager.instance().set_preload_enabled(checked)
        self._show_settings_toast()
        logger.debug(f"[设置] 预加载功能: {'启用' if checked else '禁用'}")

    def _on_preload_now_clicked(self) -> None:
        """立即预加载按钮点击"""
        if not self._ocr_ready_callback():
            QMessageBox.warning(None, "无法预加载", "OCR 功能未就绪，请先安装依赖。")
            return

        if not self._subprocess_manager.is_ready:
            QMessageBox.warning(
                None, "无法预加载", "OCR 子进程服务尚未就绪，请稍后再试。"
            )
            return

        pipelines_to_preload = self._get_selected_preload_pipelines()

        if not pipelines_to_preload:
            QMessageBox.warning(None, "无法预加载", "请至少选择一个要预加载的管道。")
            return

        btn_preload_now = self._ui.findChild(QPushButton, "btnPreloadNow")
        if btn_preload_now:
            btn_preload_now.setEnabled(False)

        progress_bar = self._ui.findChild(QProgressBar, "progressPreload")
        if progress_bar:
            progress_bar.setVisible(True)
            progress_bar.setValue(0)
            progress_bar.setMaximum(len(pipelines_to_preload) * 2)

        pipeline_names = [p.display_name for p in pipelines_to_preload]
        logger.debug(f"[预加载] 开始预加载和预热管道: {pipeline_names}")

        self._update_preload_status("正在预加载和预热模型...")
        self._start_manual_preload_with_warmup(pipelines_to_preload)

    def _get_selected_preload_pipelines(self) -> list["OCRPipeline"]:
        """获取选中的预加载管道"""
        pipelines = []
        for pipeline in self._get_preloadable_pipelines():
            chk = self._ui.findChild(QCheckBox, f"chkPreload_{pipeline.name}")
            if chk and chk.isChecked():
                pipelines.append(pipeline)
        return pipelines

    def _save_preload_pipelines_config(self) -> None:
        """保存预加载管道配置"""
        from vibeocr.pyside.runtime import ConfigManager

        pipelines = self._get_selected_preload_pipelines()
        pipeline_names = [p.value for p in pipelines]

        if ConfigManager.instance().set_preload_pipelines(pipeline_names):
            self._show_settings_toast()
            logger.debug(f"[设置] 预加载管道配置已保存: {pipeline_names}")
        else:
            logger.error("[设置] 保存预加载管道配置失败")

    def _start_manual_preload_with_warmup(self, pipelines: list["OCRPipeline"]) -> None:
        """启动手动预加载和预热"""
        from vibeocr.pyside.runtime import ConfigManager

        values = [pipeline.value for pipeline in pipelines]
        self._manual_preload_requested = set(values)
        self._manual_preload_task = True
        started = self._subprocess_manager.preload_pipelines(
            values,
            pipeline_ttls=ConfigManager.instance().get_pipeline_ttls(),
        )
        if not started:
            self._manual_preload_task = None
            self._manual_preload_requested = set()
            button = self._ui.findChild(QPushButton, "btnPreloadNow")
            if button:
                button.setEnabled(True)
            progress = self._ui.findChild(QProgressBar, "progressPreload")
            if progress:
                progress.setVisible(False)
            self._update_preload_status("已有预加载任务正在运行，请稍后再试")
        return

    def _on_manual_preload_finished(self, results: dict) -> None:
        """手动预加载完成回调（主线程槽函数）"""
        if self._manual_preload_task is None:
            return
        self._manual_preload_task = None

        btn_preload_now = self._ui.findChild(QWidget, "btnPreloadNow")
        if btn_preload_now:
            btn_preload_now.setEnabled(True)

        progress_bar = self._ui.findChild(QProgressBar, "progressPreload")
        if progress_bar:
            progress_bar.setVisible(False)

        preload = results.get("preload", {}) if isinstance(results, dict) else {}
        warmup = results.get("warmup", {}) if isinstance(results, dict) else {}
        requested = getattr(self, "_manual_preload_requested", set())
        all_loaded = bool(requested) and all(preload.get(name) for name in requested)
        all_warmed = all_loaded and all(warmup.get(name) for name in requested)
        if all_warmed:
            self._update_preload_status(f"预加载和预热成功（{len(requested)} 个模型）")
        elif all_loaded:
            self._update_preload_status("模型已加载，但部分预热失败；首次识别可能稍慢")
        else:
            self._update_preload_status("预加载失败，请查看日志")
        self._manual_preload_requested = set()

        if all_warmed and self._preload_complete_callback:
            self._preload_complete_callback()

    def _on_manual_preload_progress(
        self, current: int, total: int, pipeline_name: str
    ) -> None:
        if self._manual_preload_task is None:
            return
        progress = self._ui.findChild(QProgressBar, "progressPreload")
        if progress:
            progress.setMaximum(max(1, total))
            progress.setValue(current)
        self._update_preload_status(
            f"正在加载模型 {current}/{total}：{pipeline_name}"
        )

    def _update_preload_status(self, status: str | None = None) -> None:
        """更新预加载状态"""
        label = self._ui.findChild(QLabel, "labelPreloadStatus")
        if label:
            if status:
                label.setText(status)
            else:
                if self._subprocess_manager.is_ready:
                    label.setText("就绪")
                else:
                    label.setText("服务未就绪")

    # ============================================================
    # 缓存管理
    # ============================================================

    def _on_refresh_cache_clicked(self) -> None:
        """在线程池刷新机器缓存，避免机器码探测阻塞 GUI。"""
        if self._closing or self._cache_refresh_running:
            return
        self._cache_refresh_running = True
        button = self._ui.findChild(QPushButton, "btnRefreshCache")
        if button:
            button.setEnabled(False)
        self._update_cache_status("正在重新检测环境（可能需要数十秒）...")
        self._machine_cache_generation += 1
        generation = self._machine_cache_generation

        def finished(result: tuple[bool, str]) -> None:
            self._cache_refresh_running = False
            if button:
                button.setEnabled(True)
            if generation != self._machine_cache_generation:
                return
            success, info = result
            if success:
                self._apply_cache_status(generation, True, info, "缓存已刷新")
                self._show_settings_toast(
                    "机器/依赖缓存已重置（下次启动时重新检测）"
                )
                logger.debug("[缓存] 已刷新机器/依赖缓存")
            else:
                self._apply_cache_status(generation, False, "", "缓存刷新失败")

        def failed(error: str) -> None:
            self._cache_refresh_running = False
            if button:
                button.setEnabled(True)
            if generation == self._machine_cache_generation:
                self._update_cache_status(f"缓存刷新失败：{error}")

        self._run_cache_operation(self._refresh_machine_cache_operation, finished, failed)

    def _refresh_machine_cache_operation(self) -> tuple[bool, str]:
        """真正重检测：清环境检测字段 → 触发完整检测 → 读回 cache info。

        在 _run_cache_operation 后台线程执行。完整检测耗时数十秒
        （40+ subprocess + paddle import），UI 通过按钮 disable + 进度文案提示。

        注意：**不清 pipeline_success 字段**——它是运行时累积的状态（哪些
        管道曾成功跑过），不属于"环境检测"范畴。清掉会导致 _decide_recognize_timeout
        误判"模型未缓存"，给 OCR 600s 超时。用 reset_cache_to_empty 会清
        deps/hardware_info 但不保留 pipeline_success，故这里手动保留。
        """
        from vibeocr import env_manager
        from vibeocr.machine_cache import get_cache_info, load_cache, save_cache

        # 1. 备份 pipeline_success（运行时状态，不应被环境重检测清掉）
        cached = load_cache(self._project_root) or {}
        preserved_pipeline_success = cached.get("pipeline_success", {})
        preserved_network = cached.get("network", {})
        # 2. 清环境检测字段，强制下次检测为"全量"
        from vibeocr.machine_cache import reset_cache_to_empty

        reset_cache_to_empty(self._project_root)
        # 3. 跑完整检测（use_cache=False 强制走 _check_imports 全量探测）
        env_manager.check_embedded_environment_dependencies(
            self._project_root,
            use_cache=False,
        )
        # 4. 还原保留的运行时状态字段
        if preserved_pipeline_success or preserved_network:
            new_cached = load_cache(self._project_root) or {}
            if preserved_pipeline_success:
                new_cached["pipeline_success"] = preserved_pipeline_success
            if preserved_network:
                new_cached["network"] = preserved_network
            save_cache(self._project_root, new_cached)
        return True, get_cache_info(self._project_root)

    def _open_reinstall_dialog(
        self, reinstall_python: bool = False, missing_only: bool = False
    ) -> None:
        """以非模态方式打开重装/补装对话框（不阻塞主窗口）。

        show() 后必须持有 dialog 引用以防 GC；finished 时刷新环境状态并移除引用。
        install_succeeded 联动 MainWindow 重新检测依赖（Bug A 修复）：装完依赖后
        由 MainWindow 触发 dependency_manager.check_dependencies，使截图界面立即可用，
        无需重启程序。
        """
        self._subprocess_manager.invalidate_worker_host()
        dialog = BackendChoiceDialog(
            self._project_root,
            reinstall_python=reinstall_python,
            missing_only=missing_only,
        )

        def _on_finished(_result: int) -> None:
            # 成功路径由 install_succeeded 刷新一次；取消/失败才在这里刷新。
            if _result != 1:
                self._refresh_env_maintenance_state()
            # 移除引用，允许对话框被回收（用户也可再次打开新的）
            try:
                self._active_dialogs.remove(dialog)
            except ValueError:
                pass

        def _on_install_succeeded() -> None:
            # 装完依赖联动 MainWindow：刷新设置页状态 + 触发重新检测依赖
            # （检测完成回调里自动设 _ocr_ready、启动子进程 Worker、消费 pending_backend）。
            # 不直接设 _ocr_ready=True：让真实检测反映"装了但间接依赖没装完"等
            # 异常状态，避免假就绪。
            self._refresh_env_maintenance_state()
            if self._install_succeeded_callback is not None:
                self._install_succeeded_callback()

        dialog.finished.connect(_on_finished)
        dialog.install_succeeded.connect(_on_install_succeeded)
        self._active_dialogs.append(dialog)
        dialog.show()

    def _on_reinstall_python(self) -> None:
        """重装 Python 运行时按钮：确认后弹 BackendChoiceDialog(reinstall_python=True)"""
        reply = QMessageBox.question(
            None,
            "确认重装 Python 运行时",
            "将删除 python/ 目录（含所有 OCR 依赖）后重新下载安装 Python 运行时。\n\n"
            "删除范围：仅 python/ 目录。\n"
            "不受影响：用户配置、模型缓存、日志、机器检测缓存。\n\n"
            "是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._open_reinstall_dialog(reinstall_python=True)

    def _on_reinstall_deps(self) -> None:
        """重装 OCR 依赖按钮：确认后弹 BackendChoiceDialog(reinstall_python=False)"""
        reply = QMessageBox.question(
            None,
            "确认重装 OCR 依赖",
            "将使用 pip 重新安装 OCR 依赖（paddle/torch/mineru）。\n\n"
            "此操作不删除任何文件，仅重装 pip 包。\n\n"
            "是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._open_reinstall_dialog(reinstall_python=False)

    def _on_install_missing(self) -> None:
        """补充安装缺失依赖按钮。

        走当前推理后端（不再二次提示选择 GPU/CPU）：补装只是补齐缺失/损坏的依赖，
        后端（GPU/CPU）在首启或设置页「推理后端」已确定，补装时重选无意义且易误操作。
        故直接读 resolve_use_gpu 作为 force_backend，跳过 BackendChoiceDialog。
        重装 OCR 依赖 / 重装 Python 运行时会清空环境，仍保留 BackendChoiceDialog。
        """
        reply = QMessageBox.question(
            None,
            "确认补充安装缺失依赖",
            "将检测并只安装缺失的 OCR 依赖（已安装的自动跳过，不重复下载）。\n\n"
            "将使用当前推理后端，不重新选择。\n"
            "适合上次安装中途失败后补装。\n\n是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # 读当前后端作为补装后端，避免二次提示
        current_backend = self._runtime_backend_or_none()
        if current_backend is None:
            self._show_settings_toast("正在检测推理后端，请稍后再试")
            return
        self._open_install_dialog(missing_only=True, force_backend=current_backend)

    def _on_update_deps(self) -> None:
        """更新依赖按钮：检测是否有新版本，有则升级（全量安装，当前后端）。

        用户要求：① 新增独立入口；② 启动时检测到 version.json 规格比已装版本新也弹窗
        （覆盖安装场景）。本方法处理①的主动入口；启动弹窗在 MainWindow。
        """
        logger.info("[依赖更新] 按钮被点击，开始检测")
        python_exe = get_embedded_python_executable(self._project_root)
        if not python_exe.exists():
            logger.warning("[依赖更新] 嵌入式 Python 不存在：%s", python_exe)
            QMessageBox.warning(
                None,
                "无法检测更新",
                "Python 运行时未安装，请先安装 OCR 依赖。",
            )
            return

        button = self._ui.findChild(QPushButton, "btnUpdateDeps")
        if button:
            button.setEnabled(False)
            button.setText("正在检测更新...")
        self._manual_dependency_update_waiting = True
        self._dependency_update_task.request("settings")

    def _on_dependency_update_check_completed(
        self, source: str, result: object
    ) -> None:
        if self._closing:
            return
        if self._manual_dependency_update_waiting:
            self._manual_dependency_update_waiting = False
            button = self._ui.findChild(QPushButton, "btnUpdateDeps")
            if button:
                button.setEnabled(True)
                button.setText("更新依赖")
        if source != "settings":
            return
        updates = dict(result) if isinstance(result, dict) else {}
        logger.info(
            "[依赖更新] 检测完成，待更新包数=%d：%s", len(updates), updates
        )
        if not updates:
            self._show_settings_toast("依赖已是最新")
            return

        # 列出待更新包让用户确认
        lines = []
        for pkg, (installed, required) in updates.items():
            lines.append(f"  • {pkg}：{installed or '（未安装）'} → {required}")
        detail = "\n".join(lines)
        reply = QMessageBox.question(
            None,
            "确认更新依赖",
            f"检测到以下依赖有新版本可用：\n\n{detail}\n\n"
            "将下载并升级（使用当前推理后端）。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            logger.info("[依赖更新] 用户在确认对话框选择 No，已取消")
            return

        # 走全量安装（install_embedded_dependencies），后端用当前值
        current_backend = self._runtime_backend_or_none()
        if current_backend is None:
            self._pending_update_install = True
            self._show_settings_toast("等待后台 GPU 探测完成后开始更新")
            return
        logger.info("[依赖更新] 用户确认，开始打开安装对话框（后端=%s）", current_backend)
        self._open_install_dialog(missing_only=False, force_backend=current_backend)

    def _on_dependency_update_check_failed(self, source: str, error: str) -> None:
        if self._closing:
            return
        if self._manual_dependency_update_waiting:
            self._manual_dependency_update_waiting = False
            button = self._ui.findChild(QPushButton, "btnUpdateDeps")
            if button:
                button.setEnabled(True)
                button.setText("更新依赖")
        if source != "settings":
            return
        logger.warning("[依赖更新] 检测失败: %s", error)
        QMessageBox.warning(None, "检测失败", f"检测依赖更新时出错：\n{error}")

    def _runtime_backend_or_none(self) -> str | None:
        """只消费后台 GPU worker 已回填的运行时后端，不在 GUI 线程探测。"""
        if self._runtime_has_gpu is None:
            backend_options = self._backend_options
            if backend_options is None or not hasattr(
                backend_options, "current_backend"
            ):
                return None
            # 探测完成前该值来自 _load_cached_state；缓存缺失时安全回退 CPU，
            # 不会调用 resolve_use_gpu / nvidia-smi。
            return str(backend_options.current_backend())
        return "gpu" if self._runtime_has_gpu else "cpu"

    def _open_install_dialog(
        self,
        missing_only: bool = False,
        force_backend: str | None = None,
        single_pkg: str | None = None,
        packages: list[str] | None = None,
    ) -> None:
        """以非模态方式打开安装进度对话框（补装/更新/单包/批量重装共用，不阻塞主窗口）。

        与 _open_reinstall_dialog 的区别：不弹 BackendChoiceDialog 选后端，
        直接用传入的 force_backend（通常来自 resolve_use_gpu 当前值）。
        single_pkg 指定时进入单包重装模式；packages 指定时进入批量重装模式
        （二者互斥，均不弹后端选择）。设置页依赖树"重装选中项"走 packages。
        """
        from vibeocr.widgets.install_dialog import InstallDialog

        self._subprocess_manager.invalidate_worker_host()
        dialog = InstallDialog(
            self._project_root,
            missing_only=missing_only,
            force_backend=force_backend,
            single_pkg=single_pkg,
            packages=packages,
        )

        def _on_finished(_result: int) -> None:
            if _result != 1:
                self._refresh_env_maintenance_state()
            try:
                self._active_dialogs.remove(dialog)
            except ValueError:
                pass

        def _on_install_succeeded() -> None:
            self._refresh_env_maintenance_state()
            if self._install_succeeded_callback is not None:
                self._install_succeeded_callback()

        dialog.finished.connect(_on_finished)
        if hasattr(dialog, "install_succeeded"):
            dialog.install_succeeded.connect(_on_install_succeeded)
        self._active_dialogs.append(dialog)
        dialog.show()
        logger.info(
            "[依赖更新] 安装对话框已 show()（missing_only=%s, backend=%s, "
            "single_pkg=%s, packages=%s）",
            missing_only,
            force_backend,
            single_pkg,
            packages,
        )

    def _on_reinstall_single_dep(self, pkg: str) -> None:
        """单包重装入口（依赖表格"重装"按钮）。

        不二次确认——单包重装只装一个包，影响范围小，直接弹进度对话框。
        """
        self._open_install_dialog(single_pkg=pkg)

    def _on_reinstall_selected(self) -> None:
        """批量重装入口（依赖树"重装选中项"按钮）。

        取依赖树中选中的顶层节点对应的包（子节点忽略——间接依赖通过重装承载
        顶层包修复）。批量影响范围比单包大，需二次确认。
        """
        from PySide6.QtCore import Qt

        tree = self._ui.findChild(QTreeWidget, "treeDepsStatus")
        if tree is None:
            return
        # 只取顶层节点（parent() is None）；子节点是间接依赖，重装顶层包会重新
        # 解析传递树，逐个选 leaf 反而低效且可能漏 leaf 自身的间接依赖。
        selected_pkgs: list[str] = []
        for item in tree.selectedItems():
            if item.parent() is None:
                pkg = item.data(0, Qt.ItemDataRole.UserRole)
                if pkg:
                    selected_pkgs.append(pkg)
        if not selected_pkgs:
            return

        # 去重保序
        seen: set[str] = set()
        unique = [p for p in selected_pkgs if not (p in seen or seen.add(p))]

        reply = QMessageBox.question(
            None,
            "确认批量重装",
            f"将重装以下 {len(unique)} 个依赖包：\n\n{', '.join(unique)}\n\n"
            "顶层包会重新解析其传递依赖。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._open_install_dialog(packages=unique)

    def _refresh_env_maintenance_state(self) -> None:
        """异步刷新环境维护区，避免多轮 Python 子进程探测阻塞 GUI。"""
        label = self._ui.findChild(QLabel, "labelEnvStatus")
        tree = self._ui.findChild(QTreeWidget, "treeDepsStatus")
        if label:
            label.setText("正在检测 Python 运行时和依赖...")
        if tree:
            tree.clear()
        self._env_refresh_generation += 1
        generation = self._env_refresh_generation
        mode_fn = get_environment_mode
        info_fn = get_embedded_python_info
        python_fn = get_embedded_python_executable
        status_fn = check_dependencies_status_detailed
        versions_fn = get_dependency_versions
        direct_deps_fn = get_direct_dependencies

        def operation() -> dict:
            from vibeocr.pyside.runtime import OCR_CHECK_MODULES

            mode = mode_fn(self._project_root)
            info = info_fn(self._project_root)
            snapshot: dict = {"mode": mode, "info": info}
            if mode == "portable":
                python_exe = python_fn(self._project_root)
                deps_status = status_fn(self._project_root)
                versions = versions_fn(python_exe) if python_exe.exists() else {}
                direct_deps = {
                    pkg: direct_deps_fn(python_exe, pkg)
                    for pkg in OCR_CHECK_MODULES.values()
                    if deps_status.get(pkg, (False, False, None))[0]
                }
                snapshot.update(
                    deps_status=deps_status,
                    versions=versions,
                    direct_deps=direct_deps,
                )
            return snapshot

        self._run_cache_operation(
            operation,
            lambda snapshot: self._apply_env_maintenance_state(
                generation, snapshot
            ),
            lambda error: self._on_env_refresh_error(generation, error),
        )

    def _on_env_refresh_error(self, generation: int, error: str) -> None:
        if generation != self._env_refresh_generation:
            return
        label = self._ui.findChild(QLabel, "labelEnvStatus")
        if label:
            label.setText(f"运行时检测失败：{error}")
        logger.warning("环境维护状态刷新失败: %s", error)

    def _apply_env_maintenance_state(self, generation: int, snapshot: dict) -> None:
        if generation != self._env_refresh_generation:
            return
        label = self._ui.findChild(QLabel, "labelEnvStatus")
        btn_py = self._ui.findChild(QPushButton, "btnReinstallPython")
        btn_deps = self._ui.findChild(QPushButton, "btnReinstallDeps")
        btn_missing = self._ui.findChild(QPushButton, "btnInstallMissing")
        btn_update = self._ui.findChild(QPushButton, "btnUpdateDeps")
        btn_reinstall_sel = self._ui.findChild(QPushButton, "btnReinstallSelected")
        tree = self._ui.findChild(QTreeWidget, "treeDepsStatus")

        mode = snapshot.get("mode", "none")
        info = snapshot.get("info", {})

        if label:
            if mode == "portable":
                status = "已安装" if info.get("ready") else "未安装"
                label.setText(f"Python 运行时：{status}\n路径：{info.get('path', '未知')}")
            elif mode == "venv":
                label.setText("开发模式（.venv），请用 uv sync 管理环境")
            else:
                label.setText("Python 运行时：未安装")

        # 仅 portable 模式启用重装/补装/更新按钮（开发态 .venv 由 uv 管理）
        enabled = mode == "portable"
        if btn_py:
            btn_py.setEnabled(enabled)
        if btn_deps:
            btn_deps.setEnabled(enabled)
        if btn_missing:
            btn_missing.setEnabled(enabled)
        if btn_update:
            btn_update.setEnabled(enabled)
        # "重装选中项"初始禁用，由依赖树选择变化驱动启用状态
        if btn_reinstall_sel:
            btn_reinstall_sel.setEnabled(False)

        # 填充依赖状态树（仅 portable 模式）
        if tree and mode == "portable":
            self._populate_deps_tree(tree, snapshot)
            # 连接一次选择/按钮信号（用标志位避免重复 connect 触发 disconnect 噪音）。
            # 首次调用 connect，后续 refresh 只重填树内容，信号连接保持有效。
            if not getattr(self, "_deps_tree_signals_connected", False):
                tree.itemSelectionChanged.connect(
                    lambda: self._update_reinstall_selected_btn(tree, btn_reinstall_sel)
                )
                if btn_reinstall_sel:
                    btn_reinstall_sel.clicked.connect(self._on_reinstall_selected)
                self._deps_tree_signals_connected = True
        elif tree:
            tree.clear()

    def _update_reinstall_selected_btn(
        self, tree: QTreeWidget, btn: QPushButton | None
    ) -> None:
        """根据依赖树选中状态更新"重装选中项"按钮的 enabled 和计数文本"""
        if btn is None:
            return
        count = sum(1 for it in tree.selectedItems() if it.parent() is None)
        btn.setEnabled(count > 0)
        btn.setText(f"重装选中项 ({count})" if count > 0 else "重装选中项")

    def _populate_deps_tree(self, tree: QTreeWidget, snapshot: dict | None = None) -> None:
        """填充依赖状态树（依赖/状态/版本）

        顶层 OCR 依赖作为可展开父节点，点击展开其**直接依赖**（由
        importlib.metadata.requires 动态推导，覆盖所有顶层包而非仅 paddlex[ocr]）。
        状态列三态：完整安装 / 已安装，缺 xxx / 未安装。
        多选顶层节点 + 上方"重装选中项"按钮批量重装（替代旧版每行一个重装按钮）。
        """
        from PySide6.QtCore import Qt

        from vibeocr.pyside.runtime import OCR_CHECK_MODULES

        display_names = {
            "paddlepaddle": "PaddlePaddle",
            "paddleocr": "PaddleOCR",
            "mineru": "MinerU",
            "torch": "PyTorch",
            "markdown": "Markdown",
        }

        python_exe = get_embedded_python_executable(self._project_root)
        snapshot = snapshot or {}
        deps_status = snapshot.get("deps_status")
        if deps_status is None:
            deps_status = check_dependencies_status_detailed(self._project_root)
        versions = snapshot.get("versions")
        if versions is None:
            versions = get_dependency_versions(python_exe) if python_exe.exists() else {}
        direct_deps_snapshot = snapshot.get("direct_deps", {})

        tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tree.clear()

        toplevel_pkgs = list(OCR_CHECK_MODULES.values())
        for pkg in toplevel_pkgs:
            installed, usable, missing_module = deps_status.get(
                pkg, (False, False, None)
            )
            name = display_names.get(pkg, pkg)
            status_text = self._format_dep_status(installed, usable, missing_module)
            ver = versions.get(pkg, "")
            if installed and not ver:
                ver = "（版本未知）"
            top_item = QTreeWidgetItem([name, status_text, ver])
            # 包名存 UserRole，供"重装选中项"读取（只对顶层节点有意义）
            top_item.setData(0, Qt.ItemDataRole.UserRole, pkg)
            # 有问题的顶层节点默认展开，让用户一眼看到诊断
            top_item.setExpanded(not usable)
            tree.addTopLevelItem(top_item)

            # 动态推导顶层包的直接依赖（一层），作为可展开子节点。
            # 只在包已安装时查 requires；未装时无 metadata 可查，子节点留空。
            direct_deps: list[str] = []
            if installed:
                direct_deps = direct_deps_snapshot.get(pkg)
                if direct_deps is None:
                    direct_deps = get_direct_dependencies(python_exe, pkg)
            for dep in direct_deps:
                child = QTreeWidgetItem([f"  └ {dep}", "", ""])
                child.setData(0, Qt.ItemDataRole.UserRole, dep)
                # 子节点禁用选中（避免批量重装误选间接依赖；修复走顶层包）
                child.setFlags(Qt.ItemFlag.NoItemFlags)
                top_item.addChild(child)

    @staticmethod
    def _format_dep_status(
        installed: bool, usable: bool, missing_module: str | None
    ) -> str:
        """把 (installed, usable, missing_module) 三元组格式化为状态列文本"""
        if usable:
            return "✓ 完整安装"
        if installed and missing_module:
            return f"⚠ 已安装，缺 {missing_module}"
        if installed:
            return "⚠ 安装残缺"
        return "✗ 未安装"

    def _on_clear_cache_clicked(self) -> None:
        """清除缓存按钮点击。

        保留 pipeline_success（运行时累积状态，清掉会导致 OCR 误判未缓存）。
        """
        reply = QMessageBox.question(
            None,
            "确认清除",
            "确定要清除环境检测缓存吗？\n"
            "下次启动时需要重新检测依赖与硬件。\n"
            "（管道运行状态会保留，不影响 OCR 超时判定）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            from vibeocr.machine_cache import (
                load_cache,
                reset_cache_to_empty,
                save_cache,
            )

            # 保留运行时状态字段，只清环境检测数据
            cached = load_cache(self._project_root) or {}
            preserved_pipeline_success = cached.get("pipeline_success", {})
            preserved_network = cached.get("network", {})
            reset_cache_to_empty(self._project_root)
            if preserved_pipeline_success or preserved_network:
                new_cached = load_cache(self._project_root) or {}
                if preserved_pipeline_success:
                    new_cached["pipeline_success"] = preserved_pipeline_success
                if preserved_network:
                    new_cached["network"] = preserved_network
                save_cache(self._project_root, new_cached)
            self._update_cache_status("缓存已清除")
            logger.debug("[缓存] 已清除（保留 pipeline_success）")

    def _update_cache_status(self, status: str | None = None) -> None:
        """更新缓存状态；校验机器码的路径始终在线程池执行。"""
        from vibeocr.machine_cache import get_cache_info

        label = self._ui.findChild(QLabel, "labelCacheStatus")
        if label is None:
            return
        if status:
            label.setText(status)
            return

        label.setText("正在检查缓存...")
        self._machine_cache_generation += 1
        generation = self._machine_cache_generation

        def operation() -> tuple[bool, str]:
            valid, _cached = is_cache_valid(self._project_root)
            return valid, get_cache_info(self._project_root) if valid else ""

        self._run_cache_operation(
            operation,
            lambda result: self._apply_cache_status(
                generation, result[0], result[1]
            ),
            lambda error: self._apply_cache_status(
                generation, False, "", f"缓存检查失败：{error}"
            ),
        )

    def _apply_cache_status(
        self,
        generation: int,
        valid: bool,
        info: str,
        status: str | None = None,
    ) -> None:
        if generation != self._machine_cache_generation:
            return
        label = self._ui.findChild(QLabel, "labelCacheStatus")
        if label:
            label.setText(status or (f"缓存有效: {info}" if valid else "无有效缓存"))

    # --- 管道缓存生命周期管理 ---

    def _init_pipeline_cache_status_label(self) -> None:
        """在「运行时缓存」分组内追加 labelPipelineCacheStatus。

        原型由 labelCacheStatus 同时承载机器缓存与管道运行时状态，复制粘贴的
        文案让用户难以分辨"无有效缓存"指代哪一层。Task 7 拆分为两个标签：
          - ``labelCacheStatus``：仅机器缓存（依赖/模型缓存路径、机器码探测）
          - ``labelPipelineCacheStatus``：仅管道运行时（loaded_pipelines /
            max_heavy / 每管道 TTL 摘要），由 _on_pipeline_cache_status 写入。
        labelReleaseStatus 继续承载动作反馈（refresh/release 完成/失败）。
        """
        layout = self._ui.findChild(QVBoxLayout, "runtimeCacheLayout")
        if layout is None:
            return
        if (
            self._ui.findChild(QLabel, "labelPipelineCacheStatus") is not None
        ):
            return
        label = QLabel(self._ui)
        label.setObjectName("labelPipelineCacheStatus")
        label.setWordWrap(True)
        label.setText("运行时缓存状态：尚未读取")
        layout.addWidget(label)

    def _run_cache_operation(self, operation, on_success, on_error) -> None:
        """在线程池执行同步缓存 RPC，并隔离关闭后的迟到结果。"""
        task = FunctionTask(operation)
        self._cache_tasks.add(task)
        _BACKGROUND_TASKS.add(task)

        def finish(result) -> None:
            self._cache_tasks.discard(task)
            _BACKGROUND_TASKS.discard(task)
            if not self._closing:
                on_success(result)

        def fail(error: str) -> None:
            self._cache_tasks.discard(task)
            _BACKGROUND_TASKS.discard(task)
            if not self._closing:
                on_error(error)

        task.signals.finished.connect(finish)
        task.signals.error.connect(fail)
        QThreadPool.globalInstance().start(task)

    def _sync_configured_pipeline_ttls(self) -> None:
        """把配置中的 pipeline_ttls 整批下发到 worker（防抖 slot）。

        UI 线程边界：仅做 service 句柄读取 + _run_cache_operation 派发，
        真正的 RPC 在 QRunnable 内执行，不阻塞 GUI。
        """
        if not self._subprocess_manager or not self._subprocess_manager.is_ready:
            self._update_release_status("运行时缓存状态：OCR 服务未连接")
            return
        from vibeocr.pyside.runtime import ConfigManager

        ttls = ConfigManager.instance().get_pipeline_ttls()
        self._cache_generation += 1
        generation = self._cache_generation
        service = self._subprocess_manager.service

        def operation() -> dict:
            if not service.set_pipeline_ttls(ttls):
                raise RuntimeError("Worker 未接受 TTL 更新")
            return service.get_pipeline_cache_status()

        self._run_cache_operation(
            operation,
            lambda status: self._on_pipeline_cache_status(
                status, generation=generation, prefix="TTL 已更新"
            ),
            lambda error: self._on_pipeline_cache_error(error, generation),
        )

    def _on_refresh_pipeline_cache_clicked(self) -> None:
        if not self._subprocess_manager or not self._subprocess_manager.is_ready:
            self._update_release_status("运行时缓存状态：OCR 服务未连接")
            return
        self._cache_generation += 1
        generation = self._cache_generation
        service = self._subprocess_manager.service
        self._update_release_status("正在读取推理进程缓存状态...")
        self._run_cache_operation(
            service.get_pipeline_cache_status,
            lambda status: self._on_pipeline_cache_status(
                status, generation=generation
            ),
            lambda error: self._on_pipeline_cache_error(error, generation),
        )

    def _on_pipeline_cache_status(
        self, status: dict, *, generation: int, prefix: str = "运行时缓存状态"
    ) -> None:
        if generation != self._cache_generation:
            return
        loaded = [str(item) for item in status.get("loaded_pipelines", [])]
        max_heavy = int(status.get("max_heavy", 0))
        # Task 5 起 status 字段从 ttl_seconds(int) 改为 pipeline_ttls(dict)。
        # 兼容旧 worker：若仍返回 ttl_seconds，回退为单值摘要。
        pipeline_ttls_raw = status.get("pipeline_ttls")
        if isinstance(pipeline_ttls_raw, dict):
            ttl_summary = ", ".join(
                f"{name}={'不因闲置回收' if int(v) == 0 else f'{int(v) // 60}分钟'}"
                for name, v in pipeline_ttls_raw.items()
            ) or "（无配置）"
        else:
            legacy = int(status.get("ttl_seconds", 0))
            ttl_summary = "禁用" if legacy <= 0 else f"{legacy // 60}分钟"
        names = "、".join(loaded) if loaded else "无"
        status_text = (
            f"{prefix}：驻留 {len(loaded)} 个（{names}）；TTL {ttl_summary}；"
            f"重模型上限 {max_heavy}"
        )
        # 写入拆分后的 labelPipelineCacheStatus（运行时层），不再混淆进
        # labelCacheStatus（机器缓存层）。
        label = self._ui.findChild(QLabel, "labelPipelineCacheStatus")
        if label is not None:
            label.setText(status_text)
        else:
            # 回退：尚未构造时（理论上不会发生，因为 _init_settings_page 先建标签）
            self._update_release_status(status_text)
        # 成功后清动作反馈 label（之前停在"正在读取..."，给用户"还在刷新"错觉）
        self._update_release_status("就绪")

    def _on_pipeline_cache_error(self, error: str, generation: int) -> None:
        if generation != self._cache_generation:
            return
        # 双 label 反馈：状态 label（用户盯着的位置）+ 动作反馈 label。
        # 历史问题：失败只写 labelReleaseStatus，导致用户看到的
        # labelPipelineCacheStatus 一直停在"尚未读取"，误以为没响应。
        friendly = self._friendly_cache_error(error)
        status_label = self._ui.findChild(QLabel, "labelPipelineCacheStatus")
        if status_label is not None:
            status_label.setText(friendly)
        self._update_release_status(f"运行时缓存操作失败：{error}")

    @staticmethod
    def _friendly_cache_error(error: str) -> str:
        """把底层超时错误翻译成用户能理解的状态文案。"""
        msg = str(error)
        if "TimeoutError" in msg or "超时" in msg or "timed out" in msg.lower():
            return (
                "读取驻留状态失败：worker 未在 10 秒内响应"
                "（可能正在加载模型、执行 OCR 或已停止响应）"
            )
        return f"读取驻留状态失败：{msg}"

    def _on_release_heavy_clicked(self) -> None:
        """释放重管道按钮。"""
        self._release_pipelines(heavy_only=True)

    def _on_release_all_clicked(self) -> None:
        """全部释放按钮。"""
        self._release_pipelines(heavy_only=False)

    def _release_pipelines(self, heavy_only: bool) -> None:
        """后台释放并读取真实 worker 状态。"""
        if not self._subprocess_manager or not getattr(
            self._subprocess_manager, "is_ready", False
        ):
            QMessageBox.warning(None, "无法释放", "OCR 服务尚未就绪。")
            return

        label = "重管道" if heavy_only else "全部管道"
        reply = QMessageBox.question(
            None,
            "确认释放",
            f"确定要释放{label}吗？正在进行的任务将在当前批次完成后受影响。",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        for name in ("btnReleaseHeavy", "btnReleaseAll"):
            btn = self._ui.findChild(QPushButton, name)
            if btn:
                btn.setEnabled(False)
        self._update_release_status(f"正在释放{label}...")

        service = self._subprocess_manager.service

        def operation():
            released = service.release_pipelines(heavy_only=heavy_only)
            return released, service.get_pipeline_cache_status()

        self._run_cache_operation(
            operation,
            lambda result: self._on_release_finished(
                result[0], heavy_only, status=result[1]
            ),
            self._on_release_error,
        )

    def _on_release_finished(
        self, released: list, heavy_only: bool, status: dict | None = None
    ) -> None:
        """释放完成回调（主线程）。"""
        for name in ("btnReleaseHeavy", "btnReleaseAll"):
            btn = self._ui.findChild(QPushButton, name)
            if btn:
                btn.setEnabled(True)
        label = "重管道" if heavy_only else "全部"
        message = (
            f"已释放{label}管道: {', '.join(released)}"
            if released
            else f"没有需要释放的{label}管道"
        )
        if status is None:
            self._update_release_status(message)
            return
        self._cache_generation += 1
        self._on_pipeline_cache_status(
            status, generation=self._cache_generation, prefix=message
        )

    def _on_release_error(self, error: str) -> None:
        """释放失败回调。"""
        for name in ("btnReleaseHeavy", "btnReleaseAll"):
            btn = self._ui.findChild(QPushButton, name)
            if btn:
                btn.setEnabled(True)
        self._update_release_status(f"释放失败: {error}")

    def _update_release_status(self, status: str) -> None:
        """更新释放状态标签。"""
        label = self._ui.findChild(QLabel, "labelReleaseStatus")
        if label:
            label.setText(status)
