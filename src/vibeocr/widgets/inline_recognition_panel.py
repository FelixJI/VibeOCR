"""内联识别面板 - 快速选择识别类型"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from vibeocr.core.pipelines import (
    OCRPipeline,
    get_all_pipelines,
    get_pipeline_short_name,
    get_pipeline_supported_options,
)
from vibeocr.models.ocr_options import OCROptions
from vibeocr.ui import theme

_OPTION_DISPLAY_NAMES = {
    "use_doc_orientation_classify": "方向分类",
    "use_doc_unwarping": "扭曲矫正",
    "use_textline_orientation": "文本行方向",
    "use_table_recognition": "表格识别",
    "use_formula_recognition": "公式识别",
    "use_seal_recognition": "印章识别",
    "use_chart_recognition": "图表识别",
    "vl_use_layout_detection": "版面检测",
    "vl_use_chart_recognition": "图表识别",
    "vl_use_seal_recognition": "印章识别",
    "use_ocr_for_image_block": "图片文字识别",
    "use_wireless_table": "无线表格",
    "use_table_orientation_classify": "表格方向分类",
    "use_ocr_results_with_table_cells": "单元格文字",
    "use_e2e_wired_table_rec_model": "端到端有线表格",
    "use_e2e_wireless_table_rec_model": "端到端无线表格",
    "enable_formula": "公式识别",
    "enable_table": "表格识别",
}


class InlineRecognitionPanel(QWidget):
    """内联识别面板

    从管道注册表动态生成按钮，点击直接触发识别。
    按钮选项从 OCRPreferences 的 "screenshot" 源读取。
    """

    recognize_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._pipeline_buttons: dict[OCRPipeline, QPushButton] = {}
        self._current_pipeline: OCRPipeline = OCRPipeline.OCR
        self._current_options: OCROptions = OCROptions(pipeline=OCRPipeline.OCR)

        self._setup_ui()
        self._apply_styles()
        self._load_pipeline_options(self._current_pipeline)
        self._update_tooltips()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        for pipeline in get_all_pipelines():
            label = get_pipeline_short_name(pipeline)
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setProperty("pipeline", pipeline)
            btn.clicked.connect(
                lambda _checked, p=pipeline: self._on_pipeline_clicked(p)
            )
            layout.addWidget(btn)
            self._pipeline_buttons[pipeline] = btn

    def _apply_styles(self):
        self.setStyleSheet(
            f"QWidget {{ background: {theme.Colors.surface};"
            f" border: 1px solid {theme.Colors.border};"
            f" border-radius: {theme.Radius.lg}px; }}"
        )
        for btn in self._pipeline_buttons.values():
            btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {theme.Colors.text};"
                f" border: none; border-radius: {theme.Radius.sm}px; padding: 6px;"
                f" text-align: left; }}"
                f" QPushButton:hover {{ background: {theme.Colors.hover_bg}; }}"
                f" QPushButton:checked {{ background: {theme.Colors.accent};"
                f" color: white; }}"
            )

    def _load_pipeline_options(self, pipeline: OCRPipeline) -> None:
        """从 OCRPreferences 的 screenshot 源加载指定管道的选项"""
        try:
            from vibeocr.utils.ocr_preferences import OCRPreferences

            prefs = OCRPreferences.instance()
            self._current_options = prefs.get_pipeline_options("screenshot", pipeline)
        except RuntimeError:
            self._current_options = OCROptions(pipeline=pipeline)

    def _on_pipeline_clicked(self, pipeline: OCRPipeline):
        self._current_pipeline = pipeline
        self._load_pipeline_options(pipeline)
        for p, btn in self._pipeline_buttons.items():
            btn.setChecked(p == pipeline)
        self.recognize_requested.emit()

    def get_options(self) -> OCROptions:
        return OCROptions.from_dict(self._current_options.to_dict())

    def set_options(self, options: OCROptions):
        self._current_options = OCROptions.from_dict(options.to_dict())
        self._current_pipeline = options.pipeline
        for p, btn in self._pipeline_buttons.items():
            btn.setChecked(p == options.pipeline)

    def _update_tooltips(self) -> None:
        """为所有管道按钮生成 tooltip"""
        for pipeline, btn in self._pipeline_buttons.items():
            btn.setToolTip(self._build_pipeline_tooltip(pipeline))

    def _build_pipeline_tooltip(self, pipeline: OCRPipeline) -> str:
        """构建管道选项的 tooltip 文本"""
        try:
            from vibeocr.utils.ocr_preferences import OCRPreferences

            options = OCRPreferences.instance().get_pipeline_options(
                "screenshot", pipeline
            )
        except RuntimeError:
            options = OCROptions(pipeline=pipeline)

        supported = get_pipeline_supported_options(pipeline)
        parts = []
        for opt_name in supported:
            value = getattr(options, opt_name, None)
            if isinstance(value, bool):
                display = _OPTION_DISPLAY_NAMES.get(opt_name, opt_name)
                parts.append(f"{display}: {'开' if value else '关'}")
        return " | ".join(parts) if parts else ""
