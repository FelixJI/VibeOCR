"""设置页面控制器

处理设置页面的逻辑，包括预加载和缓存管理。
"""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QCheckBox,
    QLabel,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
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

        self._refresh_env_maintenance_state()

        self._init_screenshot_options(nav_list, stacked)
        self._init_pdf_options(nav_list, stacked)
        self._init_backend_options_in_group()
        self._init_settings_page()

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
        except RuntimeError:
            pass

    def _on_pdf_settings_changed(self, settings) -> None:
        try:
            from vibeocr.utils.ocr_preferences import OCRPreferences

            OCRPreferences.instance().set_pdf_settings(settings)
        except RuntimeError:
            pass

    def _init_settings_page(self) -> None:
        """初始化设置页面状态"""
        self._update_cache_status()
        self._update_preload_status()
        self._restore_preload_checkbox_state()

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
        for pipeline in self._get_preloadable_pipelines():
            chk = self._ui.findChild(QCheckBox, f"chkPreload_{pipeline.name}")
            if chk:
                chk.blockSignals(True)
                chk.setChecked(pipeline.value in saved)
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
        """补充安装缺失依赖按钮：确认后弹 BackendChoiceDialog(missing_only=True)"""
        reply = QMessageBox.question(
            None,
            "确认补充安装缺失依赖",
            "将检测并只安装缺失的 OCR 依赖（已安装的自动跳过，不重复下载）。\n\n"
            "适合上次安装中途失败后补装。\n\n是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._open_reinstall_dialog(missing_only=True)

    def _refresh_env_maintenance_state(self) -> None:
        """刷新环境维护区状态：显示 Python 路径/就绪，依赖状态表格，非 portable 禁用按钮"""
        label = self._ui.findChild(QLabel, "labelEnvStatus")
        btn_py = self._ui.findChild(QPushButton, "btnReinstallPython")
        btn_deps = self._ui.findChild(QPushButton, "btnReinstallDeps")
        btn_missing = self._ui.findChild(QPushButton, "btnInstallMissing")
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

        # 仅 portable 模式启用重装/补装按钮（开发态 .venv 由 uv 管理）
        enabled = mode == "portable"
        if btn_py:
            btn_py.setEnabled(enabled)
        if btn_deps:
            btn_deps.setEnabled(enabled)
        if btn_missing:
            btn_missing.setEnabled(enabled)

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
