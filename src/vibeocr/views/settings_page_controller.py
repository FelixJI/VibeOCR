"""设置页面控制器

处理设置页面的逻辑，包括 LLM 配置、模板管理、预加载和缓存管理。
"""

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from vibeocr.models.llm_config import LLMConfig
    from vibeocr.services.ocr_service import OCRPipeline

logger = logging.getLogger(__name__)


class SettingsPageController:
    """设置页面控制器

    处理设置页面的所有逻辑，与 UI 控件通过 findChild 方式交互。

    Usage:
        controller = SettingsPageController(
            ui=main_window._ui,
            project_root=main_window._project_root,
            status_callback=main_window._statusbar.showMessage,
            ocr_ready_callback=lambda: main_window._ocr_ready,
            subprocess_manager=main_window._subprocess_manager,
        )
        controller.connect_signals()
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

        self._llm_config: LLMConfig | None = None
        self._manual_preload_total = 0

    def connect_signals(self) -> None:
        """连接设置页面的信号槽"""
        # 预加载设置
        chk_enable_preload = self._ui.findChild(QCheckBox, "chkEnablePreload")
        if chk_enable_preload:
            chk_enable_preload.toggled.connect(self._on_enable_preload_toggled)

        btn_preload_now = self._ui.findChild(QPushButton, "btnPreloadNow")
        if btn_preload_now:
            btn_preload_now.clicked.connect(self._on_preload_now_clicked)

        # 缓存管理
        btn_refresh_cache = self._ui.findChild(QPushButton, "btnRefreshCache")
        if btn_refresh_cache:
            btn_refresh_cache.clicked.connect(self._on_refresh_cache_clicked)

        btn_clear_cache = self._ui.findChild(QPushButton, "btnClearCache")
        if btn_clear_cache:
            btn_clear_cache.clicked.connect(self._on_clear_cache_clicked)

        # LLM 配置相关信号
        btn_save_llm_config = self._ui.findChild(QPushButton, "btnSaveLLMConfig")
        if btn_save_llm_config:
            btn_save_llm_config.clicked.connect(self._on_save_llm_config_clicked)

        # 模板管理相关信号
        btn_add_template = self._ui.findChild(QPushButton, "btnAddTemplate")
        if btn_add_template:
            btn_add_template.clicked.connect(self._on_add_template_clicked)

        btn_edit_template = self._ui.findChild(QPushButton, "btnEditTemplate")
        if btn_edit_template:
            btn_edit_template.clicked.connect(self._on_edit_template_clicked)

        btn_delete_template = self._ui.findChild(QPushButton, "btnDeleteTemplate")
        if btn_delete_template:
            btn_delete_template.clicked.connect(self._on_delete_template_clicked)

        # 初始化设置页面状态
        self._init_settings_page()

    # ============================================================
    # 初始化
    # ============================================================

    def _init_settings_page(self) -> None:
        """初始化设置页面状态"""
        self._update_cache_status()
        self._update_preload_status()

        # 隐藏并行加载相关选项（当前不可用）
        chk_parallel = self._ui.findChild(QWidget, "chkParallelPreload")
        if chk_parallel:
            chk_parallel.setVisible(False)
        parallel_options = self._ui.findChild(QWidget, "parallelOptions")
        if parallel_options:
            parallel_options.setVisible(False)

        self._load_llm_config()
        self._load_template_list()

    # ============================================================
    # LLM 配置
    # ============================================================

    @property
    def llm_config(self) -> Optional["LLMConfig"]:
        """获取 LLM 配置"""
        return self._llm_config

    def _load_llm_config(self) -> None:
        """加载 LLM 配置"""
        from vibeocr.models.llm_config import LLMConfig

        config_path = self._project_root / "config" / "llm_config.json"
        if config_path.exists():
            try:
                with open(config_path, encoding="utf-8") as f:
                    data = json.load(f)
                self._llm_config = LLMConfig.from_dict(data)
            except Exception as e:
                logger.warning(f"加载 LLM 配置失败: {e}")
                self._llm_config = LLMConfig()
        else:
            self._llm_config = LLMConfig()

        self._update_llm_config_ui()

    def _save_llm_config(self) -> None:
        """保存 LLM 配置"""
        config_path = self._project_root / "config" / "llm_config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(self._llm_config.to_dict(), f, ensure_ascii=False, indent=2)
            logger.info("LLM 配置已保存")
        except Exception as e:
            logger.error(f"保存 LLM 配置失败: {e}")
            raise

    def _update_llm_config_ui(self) -> None:
        """更新 LLM 配置 UI"""
        edit_mllm_url = self._ui.findChild(QLineEdit, "editMLLMUrl")
        edit_mllm_model = self._ui.findChild(QLineEdit, "editMLLMModel")
        edit_mllm_api_key = self._ui.findChild(QLineEdit, "editMLLMApiKey")

        edit_llm_url = self._ui.findChild(QLineEdit, "editLLMUrl")
        edit_llm_model = self._ui.findChild(QLineEdit, "editLLMModel")
        edit_llm_api_key = self._ui.findChild(QLineEdit, "editLLMApiKey")

        if self._llm_config:
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

    def _on_save_llm_config_clicked(self) -> None:
        """保存 LLM 配置按钮点击"""

        edit_mllm_url = self._ui.findChild(QLineEdit, "editMLLMUrl")
        edit_mllm_model = self._ui.findChild(QLineEdit, "editMLLMModel")
        edit_mllm_api_key = self._ui.findChild(QLineEdit, "editMLLMApiKey")

        if edit_mllm_url and edit_mllm_model:
            self._llm_config.service_url = edit_mllm_url.text()
            self._llm_config.model_name = edit_mllm_model.text()
            if edit_mllm_api_key:
                self._llm_config.api_key = edit_mllm_api_key.text()

        self._save_llm_config()
        self._status_callback("LLM 配置已保存")
        logger.info("LLM 配置已保存")

    # ============================================================
    # 预加载
    # ============================================================

    def _on_enable_preload_toggled(self, checked: bool) -> None:
        """启用/禁用预加载"""
        preload_options = self._ui.findChild(QWidget, "preloadOptions")
        if preload_options:
            preload_options.setEnabled(checked)
        logger.info(f"[设置] 预加载功能: {'启用' if checked else '禁用'}")

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
        logger.info(f"[预加载] 开始预加载和预热管道: {pipeline_names}")

        self._update_preload_status("正在预加载和预热模型...")
        self._manual_preload_total = len(pipelines_to_preload)
        self._start_manual_preload_with_warmup(pipelines_to_preload)

    def _get_selected_preload_pipelines(self) -> list["OCRPipeline"]:
        """获取选中的预加载管道"""
        from vibeocr.services.ocr_service import OCRPipeline

        pipelines = []

        chk_ocr = self._ui.findChild(QCheckBox, "chkPreloadOCR")
        if chk_ocr and chk_ocr.isChecked():
            pipelines.append(OCRPipeline.OCR)

        chk_table = self._ui.findChild(QCheckBox, "chkPreloadTable")
        if chk_table and chk_table.isChecked():
            pipelines.append(OCRPipeline.TABLE_RECOGNITION)

        chk_formula = self._ui.findChild(QCheckBox, "chkPreloadFormula")
        if chk_formula and chk_formula.isChecked():
            pipelines.append(OCRPipeline.FORMULA_RECOGNITION)

        chk_structure = self._ui.findChild(QCheckBox, "chkPreloadStructure")
        if chk_structure and chk_structure.isChecked():
            pipelines.append(OCRPipeline.STUCTURE_RECOGNITION)

        chk_paddlocr_vl = self._ui.findChild(QCheckBox, "chkPreloadPaddleOCRVL")
        if chk_paddlocr_vl and chk_paddlocr_vl.isChecked():
            pipelines.append(OCRPipeline.PADDLEOCR_VL)

        return pipelines

    def _start_manual_preload_with_warmup(self, pipelines: list["OCRPipeline"]) -> None:
        """启动手动预加载和预热"""
        from PySide6.QtCore import QRunnable, QThreadPool

        class PreloadWithWarmupTask(QRunnable):
            def __init__(self, service, pipelines, controller):
                super().__init__()
                self._service = service
                self._pipelines = pipelines
                self._controller = controller

            def _update_progress(self, value: int):
                progress_bar = self._controller._ui.findChild(
                    QWidget, "progressPreload"
                )
                if progress_bar:
                    progress_bar.setValue(value)

            def run(self):

                results = {}
                progress = 0

                for pipeline in self._pipelines:
                    try:
                        self._service.preload_pipeline(pipeline)
                        results[pipeline.name] = True
                        progress += 1
                        self._update_progress(progress)

                        # 预热
                        self._warmup_pipeline(pipeline)
                        progress += 1
                        self._update_progress(progress)
                    except Exception as e:
                        logger.error(f"预加载 {pipeline.name} 失败: {e}")
                        results[pipeline.name] = False

                # 回调
                if self._controller._preload_complete_callback:
                    self._controller._preload_complete_callback()

            def _warmup_pipeline(self, pipeline):
                """预热管道"""
                try:
                    # 使用小图片预热
                    import io

                    from PIL import Image

                    warmup_image = Image.new("RGB", (100, 100), color="white")
                    buffer = io.BytesIO()
                    warmup_image.save(buffer, format="PNG")
                    image_data = buffer.getvalue()

                    self._service.recognize(image_data, pipeline=pipeline)
                except Exception as e:
                    logger.warning(f"预热 {pipeline.name} 失败: {e}")

        task = PreloadWithWarmupTask(self._subprocess_manager.service, pipelines, self)
        QThreadPool.globalInstance().start(task)

    def _on_manual_preload_finished(self, results: dict) -> None:
        """手动预加载完成回调"""
        btn_preload_now = self._ui.findChild(QWidget, "btnPreloadNow")
        if btn_preload_now:
            btn_preload_now.setEnabled(True)

        progress_bar = self._ui.findChild(QWidget, "progressPreload")
        if progress_bar:
            progress_bar.setVisible(False)

        success_count = sum(1 for v in results.values() if v)
        total = len(results)

        if success_count == total:
            self._update_preload_status(f"预加载完成 ({success_count}/{total})")
        else:
            self._update_preload_status(f"预加载部分完成 ({success_count}/{total})")

        logger.info(f"[预加载] 完成: {success_count}/{total}")

    def _update_preload_status(self, status: str | None = None) -> None:
        """更新预加载状态"""
        label = self._ui.findChild(QWidget, "labelPreloadStatus")
        if label:
            if status:
                label.setText(status)
            else:
                # 默认状态
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
        logger.info("[缓存] 已刷新")

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
            logger.info("[缓存] 已清除")

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

    # ============================================================
    # 模板管理
    # ============================================================

    def _load_template_list(self) -> None:
        """加载模板列表到 UI"""
        from vibeocr.models.extraction_template import DEFAULT_TEMPLATES

        list_template = self._ui.findChild(QListWidget, "listTemplate")
        if not list_template:
            return

        list_template.clear()

        for template in DEFAULT_TEMPLATES:
            list_template.addItem(template.name)

        config_path = self._project_root / "config" / "templates.json"
        if config_path.exists():
            try:
                with open(config_path, encoding="utf-8") as f:
                    templates_data = json.load(f)
                from vibeocr.models.extraction_template import ExtractionTemplate

                for template_data in templates_data:
                    template = ExtractionTemplate.from_dict(template_data)
                    list_template.addItem(f"[自定义] {template.name}")
            except Exception as e:
                logger.warning(f"加载自定义模板失败: {e}")

    def _on_add_template_clicked(self) -> None:
        """添加模板按钮点击"""

        dialog = QDialog(self._ui)
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
            name = name_edit.text().strip()
            keys_text = keys_edit.text().strip()
            if not name or not keys_text:
                return

            keys = [k.strip() for k in keys_text.split("\n") if k.strip()]
            if not keys:
                return

            config_path = self._project_root / "config" / "templates.json"
            templates = []
            if config_path.exists():
                try:
                    with open(config_path, encoding="utf-8") as f:
                        templates = json.load(f)
                except Exception:
                    pass

            templates.append({"name": name, "keys": keys})
            config_path.parent.mkdir(parents=True, exist_ok=True)

            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(templates, f, ensure_ascii=False, indent=2)

            self._load_template_list()
            dialog.accept()
            self._status_callback(f"模板 '{name}' 已添加")

        btn_add.clicked.connect(on_add)
        dialog.exec()

    def _on_edit_template_clicked(self) -> None:
        """编辑模板按钮点击"""
        list_template = self._ui.findChild(QListWidget, "listTemplate")
        if not list_template:
            return

        current_item = list_template.currentItem()
        if not current_item:
            QMessageBox.information(None, "提示", "请先选择一个模板。")
            return

        template_name = current_item.text()
        if not template_name.startswith("[自定义]"):
            QMessageBox.information(None, "提示", "只能编辑自定义模板。")
            return

        template_name = template_name.replace("[自定义] ", "")

        config_path = self._project_root / "config" / "templates.json"
        if not config_path.exists():
            return

        try:
            with open(config_path, encoding="utf-8") as f:
                templates = json.load(f)
        except Exception:
            return

        template_data = None
        for t in templates:
            if t.get("name") == template_name:
                template_data = t
                break

        if not template_data:
            return

        dialog = QDialog(self._ui)
        dialog.setWindowTitle("编辑模板")
        dialog.setMinimumSize(300, 200)

        layout = QVBoxLayout(dialog)

        layout.addWidget(QLabel("模板名称:"))
        name_edit = QLineEdit(template_name)
        layout.addWidget(name_edit)

        layout.addWidget(QLabel("抽取字段（每行一个）:"))
        keys_edit = QLineEdit("\n".join(template_data.get("keys", [])))
        layout.addWidget(keys_edit)

        btn_save = QPushButton("保存")
        layout.addWidget(btn_save)

        def on_save():
            new_name = name_edit.text().strip()
            keys_text = keys_edit.text().strip()
            if not new_name or not keys_text:
                return

            keys = [k.strip() for k in keys_text.split("\n") if k.strip()]
            if not keys:
                return

            for t in templates:
                if t.get("name") == template_name:
                    t["name"] = new_name
                    t["keys"] = keys
                    break

            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(templates, f, ensure_ascii=False, indent=2)

            self._load_template_list()
            dialog.accept()
            self._status_callback(f"模板 '{new_name}' 已更新")

        btn_save.clicked.connect(on_save)
        dialog.exec()

    def _on_delete_template_clicked(self) -> None:
        """删除模板按钮点击"""
        list_template = self._ui.findChild(QListWidget, "listTemplate")
        if not list_template:
            return

        current_item = list_template.currentItem()
        if not current_item:
            QMessageBox.information(None, "提示", "请先选择一个模板。")
            return

        template_name = current_item.text()
        if not template_name.startswith("[自定义]"):
            QMessageBox.information(None, "提示", "只能删除自定义模板。")
            return

        template_name = template_name.replace("[自定义] ", "")

        reply = QMessageBox.question(
            None,
            "确认删除",
            f"确定要删除模板 '{template_name}' 吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply != QMessageBox.Yes:
            return

        config_path = self._project_root / "config" / "templates.json"
        if not config_path.exists():
            return

        try:
            with open(config_path, encoding="utf-8") as f:
                templates = json.load(f)

            templates = [t for t in templates if t.get("name") != template_name]

            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(templates, f, ensure_ascii=False, indent=2)

            self._load_template_list()
            self._status_callback(f"模板 '{template_name}' 已删除")
        except Exception as e:
            logger.error(f"删除模板失败: {e}")
