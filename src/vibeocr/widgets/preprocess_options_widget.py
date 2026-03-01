"""预处理选项组件

用于配置 PP-StructureV3 的预处理参数。
"""

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QCheckBox,
    QGroupBox,
)
from PySide6.QtCore import Signal

from vibeocr.models.batch_request import PreprocessOptions


class PreprocessOptionsWidget(QGroupBox):
    """预处理选项组件

    提供三个预处理选项的勾选框：
    - 文档方向分类
    - 文档扭曲矫正
    - 文本行方向分类
    """

    # 选项变更信号
    options_changed = Signal(object)  # PreprocessOptions

    def __init__(self, parent: QWidget = None):
        super().__init__("预处理选项", parent)

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self):
        """设置 UI"""
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # 文档方向分类
        self._doc_orientation_cb = QCheckBox("文档方向分类")
        self._doc_orientation_cb.setToolTip(
            "自动检测并矫正文档方向 (0/90/180/270)"
        )
        self._doc_orientation_cb.setChecked(True)
        layout.addWidget(self._doc_orientation_cb)

        # 文档扭曲矫正
        self._doc_unwarping_cb = QCheckBox("文档扭曲矫正")
        self._doc_unwarping_cb.setToolTip(
            "矫正弯曲或折叠的文档图像"
        )
        self._doc_unwarping_cb.setChecked(True)
        layout.addWidget(self._doc_unwarping_cb)

        # 文本行方向分类
        self._textline_orientation_cb = QCheckBox("文本行方向分类")
        self._textline_orientation_cb.setToolTip(
            "矫正倾斜的文本行"
        )
        self._textline_orientation_cb.setChecked(False)
        layout.addWidget(self._textline_orientation_cb)

        self.setLayout(layout)

    def _connect_signals(self):
        """连接信号"""
        self._doc_orientation_cb.toggled.connect(self._on_option_changed)
        self._doc_unwarping_cb.toggled.connect(self._on_option_changed)
        self._textline_orientation_cb.toggled.connect(self._on_option_changed)

    def _on_option_changed(self):
        """选项变更处理"""
        options = self.get_options()
        self.options_changed.emit(options)

    def get_options(self) -> PreprocessOptions:
        """获取当前选项"""
        return PreprocessOptions(
            use_doc_orientation_classify=self._doc_orientation_cb.isChecked(),
            use_doc_unwarping=self._doc_unwarping_cb.isChecked(),
            use_textline_orientation=self._textline_orientation_cb.isChecked(),
        )

    def set_options(self, options: PreprocessOptions):
        """设置选项"""
        self._doc_orientation_cb.setChecked(options.use_doc_orientation_classify)
        self._doc_unwarping_cb.setChecked(options.use_doc_unwarping)
        self._textline_orientation_cb.setChecked(options.use_textline_orientation)
