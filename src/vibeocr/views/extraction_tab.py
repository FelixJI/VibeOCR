# src/vibeocr/views/extraction_tab.py
"""信息抽取标签页

提供基于 PP-ChatOCRv4 产线的信息抽取功能。
"""

import logging

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from vibeocr.models.extraction_options import ExtractionOptions
from vibeocr.models.extraction_template import DEFAULT_TEMPLATES
from vibeocr.ui.ui_extraction_tab import Ui_ExtractionTab
from vibeocr.views.tabs.base_tab import BaseOcrTab

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
        # 使用预编译的 Python UI 文件，直接在 self 上设置
        self._ui = Ui_ExtractionTab()
        self._ui.setupUi(self)

        # 使用 QTextEdit 显示结果（纯文本，不需要日志格式）
        self._result_widget = QTextEdit()
        self._result_widget.setReadOnly(True)
        self._result_widget.setPlaceholderText("抽取结果将显示在这里...")
        if hasattr(self._ui, "resultsContainer"):
            container = self._ui.resultsContainer
            container_layout = container.layout()
            if not container_layout:
                container_layout = QVBoxLayout(container)
                container_layout.setContentsMargins(0, 0, 0, 0)
            container_layout.addWidget(self._result_widget)

        else:
            # 备用: 直接在 self 上查找
            container = self.findChild(QWidget, "resultsContainer")
            if container:
                container_layout = container.layout()
                if not container_layout:
                    container_layout = QVBoxLayout(container)
                    container_layout.setContentsMargins(0, 0, 0, 0)
                container_layout.addWidget(self._result_widget)

        # 获取 UI 控件引用
        self._combo_template = getattr(self._ui, "comboTemplate", None)
        self._text_custom_keys = getattr(self._ui, "textCustomKeys", None)
        self._chk_doc_orientation = getattr(self._ui, "chkDocOrientation", None)
        self._chk_doc_unwarping = getattr(self._ui, "chkDocUnwarping", None)
        self._chk_general_ocr = getattr(self._ui, "chkGeneralOCR", None)
        self._chk_table_recognition = getattr(self._ui, "chkTableRecognition", None)
        self._chk_seal_recognition = getattr(self._ui, "chkSealRecognition", None)
        self._label_mllm_status = getattr(self._ui, "labelMLLMStatus", None)
        self._label_llm_status = getattr(self._ui, "labelLLMStatus", None)
        self._btn_start = getattr(self._ui, "btnStart", None)
        self._btn_cancel = getattr(self._ui, "btnCancel", None)
        self._progress_bar = getattr(self._ui, "progressBar", None)
        self._label_progress = getattr(self._ui, "labelProgress", None)
        self._radio_export_separate = getattr(self._ui, "radioExportSeparate", None)
        self._radio_export_merged = getattr(self._ui, "radioExportMerged", None)
        self._combo_format = getattr(self._ui, "comboFormat", None)
        self._btn_export = getattr(self._ui, "btnExport", None)
        self._file_list_widget = getattr(self._ui, "fileListWidget", None)

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
            self._result_widget.append("请选择要处理的文件。")
            return

        keys = self.get_extraction_keys()
        if not keys:
            self._result_widget.append("请配置要抽取的字段。")
            return

        if not self._ocr_service:
            self._result_widget.append("OCR 服务未就绪。")
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
            self._result_widget.append("\n没有可导出的结果。")
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
