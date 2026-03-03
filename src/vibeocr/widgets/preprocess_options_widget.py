"""预处理选项组件

用于配置 PP-StructureV3 和 PaddleOCR-VL 的预处理参数。
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from vibeocr.models.batch_request import PreprocessOptions


class PreprocessOptionsWidget(QGroupBox):
    """预处理选项组件

    提供以下选项：
    - 管道选择（PP-StructureV3 / PaddleOCR-VL）
    - 文档方向分类
    - 文档扭曲矫正
    - 文本行方向分类
    - PaddleOCR-VL 特有选项
    """

    # 选项变更信号
    options_changed = Signal(object)  # PreprocessOptions

    def __init__(self, parent: QWidget = None):
        super().__init__("识别选项", parent)

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self):
        """设置 UI"""
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # 管道选择
        pipeline_layout = QHBoxLayout()
        pipeline_layout.addWidget(QLabel("识别管道:"))
        self._pipeline_combo = QComboBox()
        self._pipeline_combo.addItem("版面解析 (PP-StructureV3)", "PP-StructureV3")
        self._pipeline_combo.addItem("PaddleOCR-VL (端到端)", "PaddleOCR-VL")
        self._pipeline_combo.setToolTip(
            "选择识别管道：版面解析或 PaddleOCR-VL 端到端识别"
        )
        pipeline_layout.addWidget(self._pipeline_combo)
        pipeline_layout.addStretch()
        layout.addLayout(pipeline_layout)

        # 通用预处理选项
        self._doc_orientation_cb = QCheckBox("文档方向分类")
        self._doc_orientation_cb.setToolTip("自动检测并矫正文档方向 (0/90/180/270)")
        self._doc_orientation_cb.setChecked(True)
        layout.addWidget(self._doc_orientation_cb)

        self._doc_unwarping_cb = QCheckBox("文档扭曲矫正")
        self._doc_unwarping_cb.setToolTip("矫正弯曲或折叠的文档图像")
        self._doc_unwarping_cb.setChecked(True)
        layout.addWidget(self._doc_unwarping_cb)

        self._textline_orientation_cb = QCheckBox("文本行方向分类")
        self._textline_orientation_cb.setToolTip("矫正倾斜的文本行")
        self._textline_orientation_cb.setChecked(False)
        layout.addWidget(self._textline_orientation_cb)

        # PaddleOCR-VL 特有选项
        self._vl_options_group = QGroupBox("PaddleOCR-VL 选项")
        vl_layout = QVBoxLayout(self._vl_options_group)

        self._vl_layout_cb = QCheckBox("版面区域检测排序")
        self._vl_layout_cb.setToolTip("启用版面区域检测和排序")
        self._vl_layout_cb.setChecked(True)
        vl_layout.addWidget(self._vl_layout_cb)

        self._vl_chart_cb = QCheckBox("图表解析")
        self._vl_chart_cb.setToolTip("启用图表解析功能")
        self._vl_chart_cb.setChecked(False)
        vl_layout.addWidget(self._vl_chart_cb)

        self._vl_seal_cb = QCheckBox("印章识别 (v1.5)")
        self._vl_seal_cb.setToolTip("启用印章识别功能（v1.5 新增）")
        self._vl_seal_cb.setChecked(False)
        vl_layout.addWidget(self._vl_seal_cb)

        self._vl_format_cb = QCheckBox("Markdown 格式化")
        self._vl_format_cb.setToolTip("将结果格式化为 Markdown 格式")
        self._vl_format_cb.setChecked(False)
        vl_layout.addWidget(self._vl_format_cb)

        self._vl_ocr_image_cb = QCheckBox("图片内 OCR")
        self._vl_ocr_image_cb.setToolTip("对图片块中的文字进行 OCR 识别")
        self._vl_ocr_image_cb.setChecked(False)
        vl_layout.addWidget(self._vl_ocr_image_cb)

        layout.addWidget(self._vl_options_group)

        self.setLayout(layout)

        # 初始化可见性
        self._update_vl_options_visibility()

    def _connect_signals(self):
        """连接信号"""
        self._doc_orientation_cb.toggled.connect(self._on_option_changed)
        self._doc_unwarping_cb.toggled.connect(self._on_option_changed)
        self._textline_orientation_cb.toggled.connect(self._on_option_changed)
        self._pipeline_combo.currentIndexChanged.connect(self._on_pipeline_changed)

        # VL 选项
        self._vl_layout_cb.toggled.connect(self._on_option_changed)
        self._vl_chart_cb.toggled.connect(self._on_option_changed)
        self._vl_seal_cb.toggled.connect(self._on_option_changed)
        self._vl_format_cb.toggled.connect(self._on_option_changed)
        self._vl_ocr_image_cb.toggled.connect(self._on_option_changed)

    def _on_pipeline_changed(self):
        """管道选择变更"""
        self._update_vl_options_visibility()
        self._on_option_changed()

    def _update_vl_options_visibility(self):
        """更新 VL 选项的可见性"""
        pipeline = self._pipeline_combo.currentData()
        is_vl = pipeline == "PaddleOCR-VL"
        self._vl_options_group.setVisible(is_vl)

    def _on_option_changed(self):
        """选项变更处理"""
        options = self.get_options()
        self.options_changed.emit(options)

    def get_options(self) -> PreprocessOptions:
        """获取当前选项"""
        pipeline = self._pipeline_combo.currentData()
        return PreprocessOptions(
            use_doc_orientation_classify=self._doc_orientation_cb.isChecked(),
            use_doc_unwarping=self._doc_unwarping_cb.isChecked(),
            use_textline_orientation=self._textline_orientation_cb.isChecked(),
            pipeline=pipeline,
            vl_use_layout_detection=self._vl_layout_cb.isChecked(),
            vl_use_seal_recognition=self._vl_seal_cb.isChecked(),
            vl_use_ocr_for_image_block=self._vl_ocr_image_cb.isChecked(),
            vl_format_block_content=self._vl_format_cb.isChecked(),
        )

    def set_options(self, options: PreprocessOptions):
        """设置选项"""
        self._doc_orientation_cb.setChecked(options.use_doc_orientation_classify)
        self._doc_unwarping_cb.setChecked(options.use_doc_unwarping)
        self._textline_orientation_cb.setChecked(options.use_textline_orientation)

        # 设置管道
        index = self._pipeline_combo.findData(options.pipeline)
        if index >= 0:
            self._pipeline_combo.setCurrentIndex(index)

        # 设置 VL 选项
        self._vl_layout_cb.setChecked(options.vl_use_layout_detection)
        self._vl_seal_cb.setChecked(options.vl_use_seal_recognition)
        self._vl_ocr_image_cb.setChecked(options.vl_use_ocr_for_image_block)
        self._vl_format_cb.setChecked(options.vl_format_block_content)

        self._update_vl_options_visibility()
