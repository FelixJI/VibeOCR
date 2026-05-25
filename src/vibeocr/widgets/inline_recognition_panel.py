# src/vibeocr/widgets/inline_recognition_panel.py
"""内联识别面板 - 快速选择识别类型"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from vibeocr.core.inline_styles import InlineStyles
from vibeocr.core.pipelines import (
    OCRPipeline,
    get_all_pipelines,
    get_pipeline_short_name,
)
from vibeocr.models.ocr_options import OCROptions


class InlineRecognitionPanel(QWidget):
    """内联识别面板

    从管道注册表动态生成按钮，点击直接触发识别。
    """

    recognize_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._pipeline_buttons: dict[OCRPipeline, QPushButton] = {}
        self._current_pipeline: OCRPipeline = OCRPipeline.OCR

        self._setup_ui()
        self._apply_styles()

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
        self.setStyleSheet(InlineStyles.panel_style())
        for btn in self._pipeline_buttons.values():
            btn.setStyleSheet(InlineStyles.recognition_button_style())

    def _on_pipeline_clicked(self, pipeline: OCRPipeline):
        self._current_pipeline = pipeline
        for p, btn in self._pipeline_buttons.items():
            btn.setChecked(p == pipeline)
        self.recognize_requested.emit()

    def get_options(self) -> OCROptions:
        return OCROptions(pipeline=self._current_pipeline)

    def set_options(self, options: OCROptions):
        self._current_pipeline = options.pipeline
        for p, btn in self._pipeline_buttons.items():
            btn.setChecked(p == options.pipeline)
