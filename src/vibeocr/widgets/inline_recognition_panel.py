# src/vibeocr/widgets/inline_recognition_panel.py
"""内联识别面板 - 快速选择识别类型并展开高级设置"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from vibeocr.core.inline_styles import InlineStyles
from vibeocr.core.pipelines import OCRPipeline
from vibeocr.models.ocr_options import OCROptions
from vibeocr.widgets.preprocess_options_widget import PreprocessOptionsWidget

_PIPELINE_BUTTON_CONFIG: list[tuple[OCRPipeline, str]] = [
    (OCRPipeline.OCR, "文字"),
    (OCRPipeline.TABLE_RECOGNITION, "表格"),
    (OCRPipeline.FORMULA_RECOGNITION, "公式"),
    (OCRPipeline.DOCUMENT_PARSING, "文档"),
]


class InlineRecognitionPanel(QWidget):
    """内联识别面板

    提供快速识别类型选择按钮，并可展开显示更多预处理选项。
    默认选中 OCR 管道。
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._settings_expanded: bool = False
        self._pipeline_buttons: dict[OCRPipeline, QPushButton] = {}
        self._current_pipeline: OCRPipeline = OCRPipeline.OCR

        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self):
        """构建 UI 布局"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(2)

        # 管道按钮
        for pipeline, label in _PIPELINE_BUTTON_CONFIG:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setProperty("pipeline", pipeline)
            btn.clicked.connect(lambda checked, p=pipeline: self._on_pipeline_clicked(p))
            layout.addWidget(btn)
            self._pipeline_buttons[pipeline] = btn

        # 更多设置按钮
        self._btn_more = QPushButton("更多 ▸")
        self._btn_more.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_more.clicked.connect(self._toggle_settings)
        layout.addWidget(self._btn_more)

        # 预处理选项组件（初始隐藏）
        self._settings_widget = PreprocessOptionsWidget()
        self._settings_widget.setVisible(False)
        self._settings_widget.options_changed.connect(self._on_settings_changed)
        layout.addWidget(self._settings_widget)

        # 设置默认选中 OCR
        self._pipeline_buttons[OCRPipeline.OCR].setChecked(True)

    def _apply_styles(self):
        """应用样式"""
        self.setStyleSheet(InlineStyles.panel_style())

        for btn in self._pipeline_buttons.values():
            btn.setStyleSheet(InlineStyles.recognition_button_style())

        self._btn_more.setStyleSheet(InlineStyles.recognition_button_style())

    def _on_pipeline_clicked(self, pipeline: OCRPipeline):
        """管道按钮点击处理"""
        self._current_pipeline = pipeline

        # 更新按钮选中状态
        for p, btn in self._pipeline_buttons.items():
            btn.setChecked(p == pipeline)

        # 同步到设置面板
        if self._settings_expanded:
            self._settings_widget.set_options(self.get_options())

    def _toggle_settings(self):
        """切换设置面板的展开/折叠"""
        self._settings_expanded = not self._settings_expanded
        self._settings_widget.setVisible(self._settings_expanded)

        if self._settings_expanded:
            self._btn_more.setText("更多 ▾")
        else:
            self._btn_more.setText("更多 ▸")

    def _on_settings_changed(self, options: OCROptions):
        """设置面板选项变更时的处理"""
        # 当设置面板更改了管道时，同步管道按钮
        self._current_pipeline = options.pipeline
        for p, btn in self._pipeline_buttons.items():
            btn.setChecked(p == options.pipeline)

    def get_options(self) -> OCROptions:
        """获取当前识别选项

        Returns:
            包含当前管道和预处理选项的 OCROptions 实例
        """
        if self._settings_expanded:
            options = self._settings_widget.get_options()
            options.pipeline = self._current_pipeline
            return options

        return OCROptions(pipeline=self._current_pipeline)

    def set_options(self, options: OCROptions):
        """设置识别选项

        Args:
            options: 要设置的 OCROptions 实例
        """
        self._current_pipeline = options.pipeline

        # 更新按钮选中状态
        for p, btn in self._pipeline_buttons.items():
            btn.setChecked(p == options.pipeline)

        # 同步到设置面板
        self._settings_widget.set_options(options)
