# src/vibeocr/views/extraction_tab.py
"""信息抽取标签页

提供基于 PP-ChatOCRv4 产线的信息抽取功能。
"""

import logging
from pathlib import Path

from PySide6.QtCore import Signal, Slot
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import (
    QVBoxLayout,
    QWidget,
)

from vibeocr.models.extraction_options import ExtractionOptions
from vibeocr.models.extraction_template import DEFAULT_TEMPLATES
from vibeocr.views.tabs.base_tab import BaseOcrTab
from vibeocr.widgets.batch_file_list_widget import BatchFileListWidget
from vibeocr.widgets.console_widget import ConsoleWidget

logger = logging.getLogger(__name__)


class ExtractionTab(BaseOcrTab):
    """信息抽取标签页

    继承自 BaseOcrTab，提供基于 PP-ChatOCRv4 产线的信息抽取功能。

    布局：
    - 左侧：文件列表 + 字段配置
    - 右侧：PP-ChatOCRv4 选项 + LLM 状态 + 结果显示
    - 底部：进度条 + 导出选项
    """

    # 信号
    extraction_started = Signal()
    extraction_finished = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ocr_service = None
        self._worker = None
        self._results = {}  # 存储抽取结果

        self._setup_ui()
        self._connect_signals()
        self._load_templates()

    def _setup_ui(self):
        """设置 UI"""
        ui_path = Path(__file__).parent.parent / "ui" / "extraction_tab.ui"
        if ui_path.exists():
            loader = QUiLoader()
            loader.registerCustomWidget(BatchFileListWidget)
            self._ui = loader.load(str(ui_path), self)

            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(self._ui)
        else:
            # 如果 UI 文件不存在，动态创建
            self._create_ui_programmatically()

        # 添加 ConsoleWidget 到结果容器
        self._result_widget = ConsoleWidget()
        container = self.findChild(QWidget, "resultsContainer")
        if container:
            container_layout = container.layout()
            if not container_layout:
                container_layout = QVBoxLayout(container)
                container_layout.setContentsMargins(0, 0, 0, 0)
            container_layout.addWidget(self._result_widget)

        # 获取 UI 控件引用
        self._combo_template = self.findChild(QWidget, "comboTemplate")
        self._text_custom_keys = self.findChild(QWidget, "textCustomKeys")
        self._chk_doc_orientation = self.findChild(QWidget, "chkDocOrientation")
        self._chk_doc_unwarping = self.findChild(QWidget, "chkDocUnwarping")
        self._chk_general_ocr = self.findChild(QWidget, "chkGeneralOCR")
        self._chk_table_recognition = self.findChild(QWidget, "chkTableRecognition")
        self._chk_seal_recognition = self.findChild(QWidget, "chkSealRecognition")
        self._label_mllm_status = self.findChild(QWidget, "labelMLLMStatus")
        self._label_llm_status = self.findChild(QWidget, "labelLLMStatus")
        self._btn_start = self.findChild(QWidget, "btnStart")
        self._btn_cancel = self.findChild(QWidget, "btnCancel")
        self._progress_bar = self.findChild(QWidget, "progressBar")
        self._label_progress = self.findChild(QWidget, "labelProgress")
        self._radio_export_separate = self.findChild(QWidget, "radioExportSeparate")
        self._radio_export_merged = self.findChild(QWidget, "radioExportMerged")
        self._combo_format = self.findChild(QWidget, "comboFormat")
        self._btn_export = self.findChild(QWidget, "btnExport")
        self._file_list_widget = self.findChild(QWidget, "fileListWidget")

    def _create_ui_programmatically(self):
        """动态创建 UI（备用方案）"""
        # 简化实现，实际使用时应加载 .ui 文件
        pass

    def _connect_signals(self):
        """连接信号"""
        if self._btn_start:
            self._btn_start.clicked.connect(self._on_start)
        if self._btn_cancel:
            self._btn_cancel.clicked.connect(self._on_cancel)
        if self._btn_export:
            self._btn_export.clicked.connect(self._on_export)

        btn_settings = self.findChild(QWidget, "btnGoToSettings")
        if btn_settings:
            btn_settings.clicked.connect(self._on_go_to_settings)

    def _load_templates(self):
        """加载模板到下拉框"""
        if not self._combo_template:
            return

        self._combo_template.clear()
        self._combo_template.addItem("不使用模板")

        for template in DEFAULT_TEMPLATES:
            self._combo_template.addItem(template.name)

    def get_extraction_options(self) -> ExtractionOptions:
        """获取当前抽取选项"""
        return ExtractionOptions(
            use_doc_orientation=(
                self._chk_doc_orientation.isChecked()
                if self._chk_doc_orientation
                else True
            ),
            use_doc_unwarping=(
                self._chk_doc_unwarping.isChecked() if self._chk_doc_unwarping else True
            ),
            use_general_ocr=(
                self._chk_general_ocr.isChecked() if self._chk_general_ocr else True
            ),
            use_table_recognition=(
                self._chk_table_recognition.isChecked()
                if self._chk_table_recognition
                else True
            ),
            use_seal_recognition=(
                self._chk_seal_recognition.isChecked()
                if self._chk_seal_recognition
                else False
            ),
        )

    def get_extraction_keys(self) -> list[str]:
        """获取抽取字段列表"""
        keys = []

        # 从模板获取
        if self._combo_template:
            template_name = self._combo_template.currentText()
            if template_name != "不使用模板":
                for template in DEFAULT_TEMPLATES:
                    if template.name == template_name:
                        keys.extend(template.keys)
                        break

        # 从自定义输入获取
        if self._text_custom_keys:
            custom_text = self._text_custom_keys.toPlainText().strip()
            if custom_text:
                custom_keys = [k.strip() for k in custom_text.split("\n") if k.strip()]
                # 合并去重
                for k in custom_keys:
                    if k not in keys:
                        keys.append(k)

        return keys

    def is_export_merged(self) -> bool:
        """是否合并导出"""
        if self._radio_export_merged:
            return self._radio_export_merged.isChecked()
        return True

    def get_export_format(self) -> str:
        """获取导出格式"""
        if self._combo_format:
            return self._combo_format.currentText().lower()
        return "json"

    @Slot()
    def _on_start(self):
        """开始抽取"""
        files = (
            self._file_list_widget.get_selected_files()
            if self._file_list_widget
            else []
        )
        if not files:
            self._result_widget.append_text("请选择要处理的文件。")
            return

        keys = self.get_extraction_keys()
        if not keys:
            self._result_widget.append_text("请配置要抽取的字段。")
            return

        if not self._ocr_service:
            self._result_widget.append_text("OCR 服务未就绪。")
            return

        # TODO: 实现 Worker 启动逻辑
        logger.info(f"开始抽取，文件数: {len(files)}，字段数: {len(keys)}")

    @Slot()
    def _on_cancel(self):
        """取消抽取"""
        if self._worker:
            self._worker.cancel()
        self._reset_ui()

    @Slot()
    def _on_export(self):
        """导出结果"""
        if not self._results:
            self._result_widget.append_text("\n没有可导出的结果。")
            return

        # TODO: 实现导出逻辑
        logger.info(
            f"导出结果，格式: {self.get_export_format()}，合并: {self.is_export_merged()}"
        )

    @Slot()
    def _on_go_to_settings(self):
        """跳转到设置页面"""
        # 发出信号或调用主窗口方法
        pass

    def _reset_ui(self):
        """重置 UI 状态"""
        if self._btn_start:
            self._btn_start.setEnabled(True)
        if self._btn_cancel:
            self._btn_cancel.setEnabled(False)
        self._worker = None

    def set_ocr_service(self, service):
        """设置 OCR 服务"""
        self._ocr_service = service

    def update_llm_status(self, mllm_config=None, llm_config=None):
        """更新 LLM 状态显示"""
        if self._label_mllm_status:
            if mllm_config and mllm_config.is_configured():
                self._label_mllm_status.setText(
                    f"MLLM: ● 已连接 ({mllm_config.model_name})"
                )
            else:
                self._label_mllm_status.setText("MLLM: ○ 未配置")

        if self._label_llm_status:
            if llm_config and llm_config.is_configured():
                self._label_llm_status.setText(
                    f"LLM: ● 已连接 ({llm_config.model_name})"
                )
            else:
                self._label_llm_status.setText("LLM: ○ 未配置")
