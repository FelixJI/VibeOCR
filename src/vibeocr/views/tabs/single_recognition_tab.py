"""单次识别标签页"""

import logging

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QSplitter, QVBoxLayout, QWidget

from vibeocr.views.tabs.base_tab import BaseOcrTab
from vibeocr.widgets.preprocess_options_widget import PreprocessOptionsWidget
from vibeocr.widgets.preview_widget import PreviewWidget
from vibeocr.widgets.result_view_widget import ResultViewWidget

logger = logging.getLogger(__name__)


class SingleRecognitionTab(BaseOcrTab):
    """单次识别标签页

    左侧：统一预览（图片/PDF/截图）
    右侧：管道选项 + 结果展示
    """

    SPLITTER_ID = "ocr_tab"

    screenshot_requested = Signal()
    file_open_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        self._connect_signals()
        self._init_options_from_preferences(batch=False)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(8, 8, 8, 8)

        self._splitter = QSplitter()

        # 左侧：统一预览
        self._preview_widget = PreviewWidget(
            empty_text="左键点击截图 · 右键点击选择文件\n\n支持图片、PDF 格式"
        )
        self._preview_widget.setMinimumWidth(300)
        self._splitter.addWidget(self._preview_widget)

        # 右侧：管道选项 + 结果展示
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setSpacing(6)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self._preprocess_options = PreprocessOptionsWidget()
        right_layout.addWidget(self._preprocess_options)

        self._result_widget = ResultViewWidget()
        right_layout.addWidget(self._result_widget, stretch=1)

        right_panel.setMinimumWidth(300)
        self._splitter.addWidget(right_panel)

        self._splitter.setSizes([400, 500])
        layout.addWidget(self._splitter, stretch=1)
        self.setLayout(layout)

    def _connect_signals(self):
        self._setup_hover_sync()
        self._preview_widget.block_text_edited.connect(self._on_block_text_edited)

        # 转发预览组件的截图/文件请求信号
        self._preview_widget.screenshot_requested.connect(self.screenshot_requested.emit)
        self._preview_widget.file_open_requested.connect(self.file_open_requested.emit)

    def _on_start(self):
        pass  # 单次识别由用户操作触发，无统一 start 按钮

    def set_pixmap(self, pixmap) -> None:
        """设置预览图片（由 MainWindow 委托调用）"""
        self._preview_widget.set_pixmap(pixmap)

    def pixmap(self):
        return self._preview_widget.pixmap()
