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

from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
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
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from vibeocr.env_manager import (
    check_embedded_environment_dependencies_fresh,
    get_dependency_versions,
    get_embedded_python_executable,
    get_embedded_python_info,
    get_environment_mode,
)
from vibeocr.machine_cache import is_cache_valid
from vibeocr.widgets.backend_choice_dialog import BackendChoiceDialog

if TYPE_CHECKING:
    from vibeocr.services.ocr_service import OCRPipeline

logger = logging.getLogger(__name__)


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
        os.makedirs(os.path.dirname(shortcut_path), exist_ok=True)
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
        self._manual_preload_total = 0
        self._manual_preload_task: object | None = None
        # 非模态重装对话框引用：show() 后须持有，否则被 GC 立即销毁；
        # 对话框 finished 时从列表移除，允许再次打开。
        self._active_dialogs: list = []

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
        spin_ttl = self._ui.findChild(QSpinBox, "spinPipelineTtl")
        if spin_ttl:
            spin_ttl.valueChanged.connect(self._on_pipeline_ttl_changed)

        btn_release_heavy = self._ui.findChild(QPushButton, "btnReleaseHeavy")
        if btn_release_heavy:
            btn_release_heavy.clicked.connect(self._on_release_heavy_clicked)

        btn_release_all = self._ui.findChild(QPushButton, "btnReleaseAll")
        if btn_release_all:
            btn_release_all.clicked.connect(self._on_release_all_clicked)

        self._restore_pipeline_ttl_state()

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
            logger.debug("[Toast] 显示失败（不允许影响主流程）")

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

        if _create_windows_shortcut(target, lnk, "VibeOCR", icon, wd):
            self._show_settings_toast("桌面快捷方式已创建")
        else:
            QMessageBox.warning(None, "创建失败", "创建桌面快捷方式失败，请检查权限。")

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

        if _create_windows_shortcut(target, lnk, "VibeOCR", icon, wd):
            self._show_settings_toast("开始菜单快捷方式已创建")
        else:
            QMessageBox.warning(None, "创建失败", "创建开始菜单快捷方式失败，请检查权限。")

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
        if container is None:
            return

        from vibeocr.widgets.backend_options_widget import BackendOptionsWidget

        self._backend_options = BackendOptionsWidget(self._project_root)
        layout = container.layout()
        if layout is not None:
            layout.addWidget(self._backend_options)

        # 后端切换时弹出保存成功提示
        self._backend_options.backend_changed.connect(
            lambda: self._show_settings_toast()
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
        self._update_cache_status()
        self._update_preload_status()
        self._restore_preload_checkbox_state()

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
        from vibeocr.core.pipelines import get_preloadable_pipelines

        return get_preloadable_pipelines()

    def _restore_preload_checkbox_state(self) -> None:
        """从配置恢复预加载 checkbox 状态（阻塞信号避免触发保存）"""
        from vibeocr.managers.config_manager import ConfigManager

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
        from vibeocr.managers.config_manager import ConfigManager

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
        self._manual_preload_total = len(pipelines_to_preload)
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
        from vibeocr.managers.config_manager import ConfigManager

        pipelines = self._get_selected_preload_pipelines()
        pipeline_names = [p.value for p in pipelines]

        if ConfigManager.instance().set_preload_pipelines(pipeline_names):
            self._show_settings_toast()
            logger.debug(f"[设置] 预加载管道配置已保存: {pipeline_names}")
        else:
            logger.error("[设置] 保存预加载管道配置失败")

    def _start_manual_preload_with_warmup(self, pipelines: list["OCRPipeline"]) -> None:
        """启动手动预加载和预热"""
        from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

        self._update_preload_status("正在预加载...")

        class _PreloadSignals(QObject):
            status_changed = Signal(str)
            finished = Signal(dict)

        class PreloadWithWarmupTask(QRunnable):
            def __init__(self, service, pipelines, controller):
                super().__init__()
                self._service = service
                self._pipelines = pipelines
                self._controller = controller
                self.signals = _PreloadSignals()

            def run(self):
                results = {}

                for pipeline in self._pipelines:
                    try:
                        self.signals.status_changed.emit(
                            f"正在预加载 {pipeline.display_name}..."
                        )
                        logger.debug(f"[预加载] 正在预加载 {pipeline.display_name}...")
                        success = self._service.preload_pipeline(pipeline)
                        if not success:
                            logger.warning(
                                f"[预加载] {pipeline.display_name} 预加载失败"
                            )
                            results[pipeline.name] = False
                            continue
                        results[pipeline.name] = True
                        logger.debug(f"[预加载] {pipeline.display_name} 预加载成功!")

                        self.signals.status_changed.emit(
                            f"正在预热 {pipeline.display_name}..."
                        )
                        logger.debug(f"[预热] 正在预热 {pipeline.display_name}...")
                        if self._warmup_pipeline(pipeline):
                            logger.debug(f"[预热] {pipeline.display_name} 预热成功!")
                    except Exception as e:
                        logger.error(f"预加载 {pipeline.name} 失败: {e}")
                        results[pipeline.name] = False

                success_count = sum(1 for v in results.values() if v)
                total = len(results)
                if success_count == total:
                    self.signals.status_changed.emit("预加载成功")
                    logger.debug(f"[预加载] 全部完成! 成功: {success_count}/{total}")
                elif success_count > 0:
                    self.signals.status_changed.emit(
                        f"预加载部分成功 ({success_count}/{total})"
                    )
                    logger.warning(f"[预加载] 部分完成: {success_count}/{total}")
                else:
                    self.signals.status_changed.emit("预加载失败")
                    logger.error("[预加载] 全部失败!")

                self.signals.finished.emit(results)

            def _warmup_pipeline(self, pipeline) -> bool:
                """预热管道"""
                try:
                    import io

                    from PIL import Image

                    from vibeocr.services.ocr_service import OCROptions

                    warmup_image = Image.new("RGB", (100, 100), color="white")
                    buffer = io.BytesIO()
                    warmup_image.save(buffer, format="PNG")
                    image_data = buffer.getvalue()

                    options = OCROptions(pipeline=pipeline)
                    self._service.recognize(image_data, options=options)
                    return True
                except Exception as e:
                    logger.warning(f"[预热] {pipeline.display_name} 预热失败: {e}")
                    return False

        task = PreloadWithWarmupTask(self._subprocess_manager.service, pipelines, self)
        task.signals.status_changed.connect(self._update_preload_status)
        task.signals.finished.connect(self._on_manual_preload_finished)
        self._manual_preload_task = task
        QThreadPool.globalInstance().start(task)

    def _on_manual_preload_finished(self, results: dict) -> None:
        """手动预加载完成回调（主线程槽函数）"""
        self._manual_preload_task = None

        btn_preload_now = self._ui.findChild(QWidget, "btnPreloadNow")
        if btn_preload_now:
            btn_preload_now.setEnabled(True)

        progress_bar = self._ui.findChild(QProgressBar, "progressPreload")
        if progress_bar:
            progress_bar.setVisible(False)

        if self._preload_complete_callback:
            self._preload_complete_callback()

        success_count = sum(1 for v in results.values() if v)
        total = len(results)
        logger.debug(f"[预加载] 完成: {success_count}/{total}")

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
        """刷新缓存按钮点击"""
        from vibeocr.machine_cache import refresh_cache

        self._update_cache_status("正在刷新缓存...")
        refresh_cache(self._project_root)
        self._update_cache_status("缓存已刷新")
        self._show_settings_toast("缓存已刷新")
        logger.debug("[缓存] 已刷新（依赖缓存 + 模型缓存）")

    def _open_reinstall_dialog(
        self, reinstall_python: bool = False, missing_only: bool = False
    ) -> None:
        """以非模态方式打开重装/补装对话框（不阻塞主窗口）。

        show() 后必须持有 dialog 引用以防 GC；finished 时刷新环境状态并移除引用。
        install_succeeded 联动 MainWindow 重新检测依赖（Bug A 修复）：装完依赖后
        由 MainWindow 触发 dependency_manager.check_dependencies，使截图界面立即可用，
        无需重启程序。
        """
        dialog = BackendChoiceDialog(
            self._project_root,
            reinstall_python=reinstall_python,
            missing_only=missing_only,
        )

        def _on_finished(_result: int) -> None:
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
        from vibeocr import env_manager

        current_backend = "gpu" if env_manager.resolve_use_gpu(self._project_root) else "cpu"
        self._open_install_dialog(missing_only=True, force_backend=current_backend)

    def _on_update_deps(self) -> None:
        """更新依赖按钮：检测是否有新版本，有则升级（全量安装，当前后端）。

        用户要求：① 新增独立入口；② 启动时检测到 version.json 规格比已装版本新也弹窗
        （覆盖安装场景）。本方法处理①的主动入口；启动弹窗在 MainWindow。
        """
        from vibeocr import env_manager

        python_exe = get_embedded_python_executable(self._project_root)
        if not python_exe.exists():
            QMessageBox.warning(
                None,
                "无法检测更新",
                "Python 运行时未安装，请先安装 OCR 依赖。",
            )
            return

        # 检测：返回需更新的包 {pkg: (installed_ver, required_spec)}
        try:
            updates = env_manager.detect_dependency_updates(self._project_root)
        except Exception as e:
            logger.exception("[依赖更新] 检测失败")
            QMessageBox.warning(None, "检测失败", f"检测依赖更新时出错：\n{e}")
            return

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
            return

        # 走全量安装（install_embedded_dependencies），后端用当前值
        current_backend = "gpu" if env_manager.resolve_use_gpu(self._project_root) else "cpu"
        self._open_install_dialog(missing_only=False, force_backend=current_backend)

    def _open_install_dialog(
        self, missing_only: bool = False, force_backend: str | None = None
    ) -> None:
        """以非模态方式打开安装进度对话框（补装/更新共用，不阻塞主窗口）。

        与 _open_reinstall_dialog 的区别：不弹 BackendChoiceDialog 选后端，
        直接用传入的 force_backend（通常来自 resolve_use_gpu 当前值）。
        """
        from vibeocr.widgets.install_dialog import InstallDialog

        dialog = InstallDialog(
            self._project_root,
            missing_only=missing_only,
            force_backend=force_backend,
        )

        def _on_finished(_result: int) -> None:
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

    def _refresh_env_maintenance_state(self) -> None:
        """刷新环境维护区状态：显示 Python 路径/就绪，依赖状态表格，非 portable 禁用按钮"""
        label = self._ui.findChild(QLabel, "labelEnvStatus")
        btn_py = self._ui.findChild(QPushButton, "btnReinstallPython")
        btn_deps = self._ui.findChild(QPushButton, "btnReinstallDeps")
        btn_missing = self._ui.findChild(QPushButton, "btnInstallMissing")
        btn_update = self._ui.findChild(QPushButton, "btnUpdateDeps")
        table = self._ui.findChild(QTableWidget, "tableDepsStatus")

        mode = get_environment_mode(self._project_root)
        info = get_embedded_python_info(self._project_root)

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

        # 填充依赖状态表格（仅 portable 模式）
        if table and mode == "portable":
            self._populate_deps_table(table)
        elif table:
            table.setRowCount(0)

    def _populate_deps_table(self, table: QTableWidget) -> None:
        """填充依赖状态表格（名称/状态/版本）"""
        # 依赖展示顺序与 OCR_CHECK_MODULES 一致
        from vibeocr.services.env_config import OCR_CHECK_MODULES

        display_names = {
            "paddlepaddle": "PaddlePaddle",
            "paddleocr": "PaddleOCR",
            "mineru": "MinerU",
            "torch": "PyTorch",
            "markdown": "Markdown",
        }
        ordered_pkgs = list(OCR_CHECK_MODULES.values())  # 保持插入顺序

        python_exe = get_embedded_python_executable(self._project_root)
        # 用 fresh 检测（忽略缓存）：设置页是用户查看实时状态的入口，
        # 走缓存会在"刚装完依赖但缓存未刷新"时显示过期的"未安装"。
        # 配合 env_manager 安装成功后主动写缓存，双保险保证状态及时准确。
        deps_status = check_embedded_environment_dependencies_fresh(self._project_root)
        versions = (
            get_dependency_versions(python_exe) if python_exe.exists() else {}
        )

        # 运行时双保险：禁用编辑（.ui 已设 NoEditTriggers，此处防遗漏）
        # + 各单元格 item 设不可编辑 flag + 列宽自适应。
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = table.horizontalHeader()
        if header is not None:
            from PySide6.QtWidgets import QHeaderView

            header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        from PySide6.QtCore import Qt

        item_flag_no_edit = Qt.ItemFlag.ItemIsEditable

        table.setRowCount(len(ordered_pkgs))
        for row, pkg in enumerate(ordered_pkgs):
            installed = deps_status.get(pkg, False)
            name_item = QTableWidgetItem(display_names.get(pkg, pkg))
            status_text = "✓ 已安装" if installed else "✗ 未安装"
            status_item = QTableWidgetItem(status_text)
            # 版本为空但状态已安装时显示占位，避免"已安装却无版本号"的困惑
            ver = versions.get(pkg, "")
            if installed and not ver:
                ver = "（版本未知）"
            ver_item = QTableWidgetItem(ver)
            # 单元格强制只读（防止 NoEditTriggers 被某处覆盖后仍可编辑）
            for item in (name_item, status_item, ver_item):
                item.setFlags(item.flags() & ~item_flag_no_edit)
            table.setItem(row, 0, name_item)
            table.setItem(row, 1, status_item)
            table.setItem(row, 2, ver_item)

    def _on_clear_cache_clicked(self) -> None:
        """清除缓存按钮点击"""
        from vibeocr.machine_cache import clear_cache

        reply = QMessageBox.question(
            None,
            "确认清除",
            "确定要清除所有缓存吗？\n这将删除机器配置缓存，下次启动时需要重新检测。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            clear_cache(self._project_root)
            self._update_cache_status("缓存已清除")
            logger.debug("[缓存] 已清除")

    def _update_cache_status(self, status: str | None = None) -> None:
        """更新缓存状态"""
        from vibeocr.machine_cache import get_cache_info

        label = self._ui.findChild(QLabel, "labelCacheStatus")
        if label:
            if status:
                label.setText(status)
            else:
                if is_cache_valid(self._project_root):
                    info = get_cache_info(self._project_root)
                    label.setText(f"缓存有效: {info}")
                else:
                    label.setText("无有效缓存")

    # --- 管道缓存生命周期管理 ---

    def _restore_pipeline_ttl_state(self) -> None:
        """从配置恢复 TTL spin 值。"""
        from vibeocr.managers.config_manager import ConfigManager

        spin = self._ui.findChild(QSpinBox, "spinPipelineTtl")
        if spin:
            ttl_sec = ConfigManager.instance().get_pipeline_ttl_seconds()
            spin.blockSignals(True)
            spin.setValue(max(1, ttl_sec // 60))  # 秒转分钟
            spin.blockSignals(False)

    def _on_pipeline_ttl_changed(self, minutes: int) -> None:
        """TTL spin 变化 → 保存配置 + 通知 worker。"""
        from vibeocr.managers.config_manager import ConfigManager

        ttl_sec = minutes * 60
        ConfigManager.instance().set_pipeline_ttl_seconds(ttl_sec)
        self._show_settings_toast()
        if self._subprocess_manager and self._subprocess_manager.is_ready:
            try:
                self._subprocess_manager.service.set_pipeline_ttl(ttl_sec)
            except Exception as e:
                logger.warning("[设置] 通知 worker TTL 更新失败: %s", e)

    def _on_release_heavy_clicked(self) -> None:
        """释放重管道按钮。"""
        self._release_pipelines(heavy_only=True)

    def _on_release_all_clicked(self) -> None:
        """全部释放按钮。"""
        self._release_pipelines(heavy_only=False)

    def _release_pipelines(self, heavy_only: bool) -> None:
        """执行释放（后台线程，照搬 preload 模式）。"""
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

        from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

        class _ReleaseSignals(QObject):
            finished = Signal(list)
            error = Signal(str)

        class ReleaseTask(QRunnable):
            def __init__(self, service, heavy_only, signals):
                super().__init__()
                self._service = service
                self._heavy_only = heavy_only
                self._signals = signals

            def run(self):
                try:
                    released = self._service.release_pipelines(
                        heavy_only=self._heavy_only
                    )
                    # MinerU 在主进程独立管理，单独释放
                    try:
                        from vibeocr.services.mineru_service import MinerUService

                        if MinerUService._api_process is not None:
                            MinerUService().shutdown()
                            released = [*released, "MinerU"]
                    except Exception:
                        pass
                    self._signals.finished.emit(released)
                except Exception as e:
                    self._signals.error.emit(str(e))

        signals = _ReleaseSignals()
        signals.finished.connect(lambda r: self._on_release_finished(r, heavy_only))
        signals.error.connect(self._on_release_error)

        task = ReleaseTask(self._subprocess_manager.service, heavy_only, signals)
        QThreadPool.globalInstance().start(task)

    def _on_release_finished(self, released: list, heavy_only: bool) -> None:
        """释放完成回调（主线程）。"""
        for name in ("btnReleaseHeavy", "btnReleaseAll"):
            btn = self._ui.findChild(QPushButton, name)
            if btn:
                btn.setEnabled(True)
        label = "重管道" if heavy_only else "全部"
        if released:
            self._update_release_status(f"已释放{label}管道: {', '.join(released)}")
        else:
            self._update_release_status(f"没有需要释放的{label}管道")

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
