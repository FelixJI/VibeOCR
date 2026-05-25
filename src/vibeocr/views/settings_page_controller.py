"""设置页面控制器

处理设置页面的逻辑，包括预加载和缓存管理。
"""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QCheckBox,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QListWidget,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

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
    ) -> None:
        self._ui = ui
        self._project_root = project_root
        self._status_callback = status_callback
        self._ocr_ready_callback = ocr_ready_callback
        self._subprocess_manager = subprocess_manager
        self._preload_complete_callback = preload_complete_callback
        self._manual_preload_total = 0
        self._manual_preload_task: object | None = None

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

        self._init_screenshot_options(nav_list, stacked)
        self._init_settings_page()

    def _init_screenshot_options(
        self, nav_list: QListWidget | None, stacked: QStackedWidget | None
    ) -> None:
        """初始化截图面板选项页面"""
        if not nav_list or not stacked:
            return

        from vibeocr.utils.ocr_preferences import OCRPreferences
        from vibeocr.widgets.preprocess_options_widget import PreprocessOptionsWidget

        # 添加导航项和页面
        nav_list.addItem("截图选项")

        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(16, 16, 16, 16)
        page_layout.setSpacing(12)

        self._screenshot_options = PreprocessOptionsWidget()
        page_layout.addWidget(self._screenshot_options)

        spacer = QSpacerItem(
            20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
        )
        page_layout.addItem(spacer)

        stacked.addWidget(page)

        # 初始化选项
        try:
            prefs = OCRPreferences.instance()
            default_pipeline = self._screenshot_options.get_current_pipeline()
            self._screenshot_options.set_options(
                prefs.get_pipeline_options("screenshot", default_pipeline)
            )
        except RuntimeError:
            pass

        # 连接信号
        self._screenshot_switching = False
        self._screenshot_options.pipeline_switching.connect(
            self._on_screenshot_pipeline_switching
        )
        self._screenshot_options.pipeline_switched.connect(
            self._on_screenshot_pipeline_switched
        )
        self._screenshot_options.options_changed.connect(
            self._on_screenshot_option_changed
        )

    def _on_screenshot_pipeline_switching(self, old_pipeline, options) -> None:
        self._screenshot_switching = True
        try:
            from vibeocr.utils.ocr_preferences import OCRPreferences
            OCRPreferences.instance().set_pipeline_options(
                "screenshot", old_pipeline, options
            )
        except RuntimeError:
            pass

    def _on_screenshot_pipeline_switched(self, new_pipeline) -> None:
        try:
            from vibeocr.utils.ocr_preferences import OCRPreferences
            loaded = OCRPreferences.instance().get_pipeline_options(
                "screenshot", new_pipeline
            )
            self._screenshot_options.set_options(loaded)
        except RuntimeError:
            pass
        self._screenshot_switching = False

    def _on_screenshot_option_changed(self, options) -> None:
        if self._screenshot_switching:
            return
        try:
            from vibeocr.utils.ocr_preferences import OCRPreferences
            OCRPreferences.instance().set_pipeline_options(
                "screenshot", options.pipeline, options
            )
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

        saved = ConfigManager.instance().get_preload_pipelines()
        for pipeline in self._get_preloadable_pipelines():
            chk = self._ui.findChild(QCheckBox, f"chkPreload_{pipeline.name}")
            if chk:
                chk.blockSignals(True)
                chk.setChecked(pipeline.value in saved)
                chk.blockSignals(False)

        chk_enable = self._ui.findChild(QCheckBox, "chkEnablePreload")
        if chk_enable:
            chk_enable.blockSignals(True)
            chk_enable.setChecked(len(saved) > 0)
            chk_enable.blockSignals(False)
            self._on_enable_preload_toggled(len(saved) > 0)

    def _on_enable_preload_toggled(self, checked: bool) -> None:
        """启用/禁用预加载"""
        preload_options = self._ui.findChild(QWidget, "preloadOptions")
        if preload_options:
            preload_options.setEnabled(checked)
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
                            logger.warning(f"[预加载] {pipeline.display_name} 预加载失败")
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
        label = self._ui.findChild(QWidget, "labelPreloadStatus")
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

    def _on_clear_cache_clicked(self) -> None:
        """清除缓存按钮点击"""
        from vibeocr.machine_cache import clear_cache

        reply = QMessageBox.question(
            None,
            "确认清除",
            "确定要清除所有缓存吗？\n这将删除机器配置缓存，下次启动时需要重新检测。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            clear_cache(self._project_root)
            self._update_cache_status("缓存已清除")
            logger.debug("[缓存] 已清除")

    def _update_cache_status(self, status: str | None = None) -> None:
        """更新缓存状态"""
        from vibeocr.machine_cache import get_cache_info, is_cache_valid

        label = self._ui.findChild(QWidget, "labelCacheStatus")
        if label:
            if status:
                label.setText(status)
            else:
                if is_cache_valid(self._project_root):
                    info = get_cache_info(self._project_root)
                    label.setText(f"缓存有效: {info}")
                else:
                    label.setText("无有效缓存")
