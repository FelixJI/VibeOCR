# src/vibeocr/widgets/preprocess_options_widget.py
"""预处理选项组件 - 选项卡式布局"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from vibeocr.core.pipelines import (
    DEFAULT_DOC_UNDERSTANDING_MODEL,
    DOC_UNDERSTANDING_MODELS,
    OCRPipeline,
    get_all_pipelines,
    get_pipeline_display_name,
)
from vibeocr.models.ocr_options import OCROptions


class PreprocessOptionsWidget(QGroupBox):
    """预处理选项组件

    选项卡式布局，根据管道动态显示选项。
    """

    options_changed = Signal(object)  # OCROptions

    def __init__(self, parent: QWidget | None = None):
        super().__init__("识别选项", parent)
        self._current_options = OCROptions()
        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self):
        """设置 UI"""
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # 管道选择
        pipeline_layout = QHBoxLayout()
        pipeline_layout.addWidget(QLabel("管道:"))
        self._pipeline_combo = QComboBox()
        self._populate_pipeline_combo()
        pipeline_layout.addWidget(self._pipeline_combo)
        pipeline_layout.addStretch()
        layout.addLayout(pipeline_layout)

        # 选项卡
        self._tab_widget = QTabWidget()
        layout.addWidget(self._tab_widget)

        # 预处理选项卡
        self._preprocess_tab = self._create_preprocess_tab()
        self._tab_widget.addTab(self._preprocess_tab, "预处理")

        # 高级选项卡
        self._advanced_tab = self._create_advanced_tab()
        self._tab_widget.addTab(self._advanced_tab, "高级")

        # 模型选项卡
        self._model_tab = self._create_model_tab()
        self._tab_widget.addTab(self._model_tab, "模型")

        # 初始更新可见性
        self._update_tab_visibility()

    def _populate_pipeline_combo(self):
        """填充管道下拉框"""
        self._pipeline_combo.clear()
        for pipeline in get_all_pipelines():
            self._pipeline_combo.addItem(
                get_pipeline_display_name(pipeline),
                pipeline.value,
            )

    def _create_preprocess_tab(self) -> QWidget:
        """创建预处理选项卡"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        self._doc_orientation_cb = QCheckBox("文档方向分类")
        self._doc_orientation_cb.setToolTip("自动检测并矫正文档方向 (0/90/180/270度)")
        self._doc_orientation_cb.setChecked(True)
        layout.addWidget(self._doc_orientation_cb)

        self._doc_unwarping_cb = QCheckBox("文档扭曲矫正")
        self._doc_unwarping_cb.setToolTip("矫正文档的扭曲、倾斜、透视变形")
        self._doc_unwarping_cb.setChecked(True)
        layout.addWidget(self._doc_unwarping_cb)

        self._textline_orientation_cb = QCheckBox("文本行方向分类")
        self._textline_orientation_cb.setToolTip("检测文本行方向 (0/180度)")
        self._textline_orientation_cb.setChecked(False)
        layout.addWidget(self._textline_orientation_cb)

        layout.addStretch()
        return widget

    def _create_advanced_tab(self) -> QWidget:
        """创建高级选项卡"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # PP-StructureV3 选项组
        self._pp_structure_group = self._create_pp_structure_group()
        layout.addWidget(self._pp_structure_group)

        # PaddleOCR-VL 选项组
        self._vl_group = self._create_vl_group()
        layout.addWidget(self._vl_group)

        layout.addStretch()
        return widget

    def _create_pp_structure_group(self) -> QGroupBox:
        """创建 PP-StructureV3 选项组"""
        group = QGroupBox("版面解析子产线")
        layout = QVBoxLayout(group)

        self._table_recognition_cb = QCheckBox("表格识别")
        self._table_recognition_cb.setChecked(True)
        layout.addWidget(self._table_recognition_cb)

        self._formula_recognition_cb = QCheckBox("公式识别")
        self._formula_recognition_cb.setChecked(True)
        layout.addWidget(self._formula_recognition_cb)

        self._seal_recognition_cb = QCheckBox("印章识别")
        self._seal_recognition_cb.setChecked(False)
        layout.addWidget(self._seal_recognition_cb)

        self._chart_recognition_cb = QCheckBox("图表识别")
        self._chart_recognition_cb.setChecked(False)
        layout.addWidget(self._chart_recognition_cb)

        return group

    def _create_vl_group(self) -> QGroupBox:
        """创建 PaddleOCR-VL 选项组"""
        group = QGroupBox("PaddleOCR-VL 选项")
        layout = QVBoxLayout(group)

        self._vl_layout_cb = QCheckBox("版面区域检测排序")
        self._vl_layout_cb.setChecked(True)
        layout.addWidget(self._vl_layout_cb)

        self._vl_seal_cb = QCheckBox("印章识别")
        self._vl_seal_cb.setChecked(False)
        layout.addWidget(self._vl_seal_cb)

        self._vl_ocr_image_cb = QCheckBox("图片文字识别")
        self._vl_ocr_image_cb.setChecked(False)
        layout.addWidget(self._vl_ocr_image_cb)

        self._vl_markdown_cb = QCheckBox("Markdown 格式输出")
        self._vl_markdown_cb.setChecked(False)
        layout.addWidget(self._vl_markdown_cb)

        return group

    def _create_model_tab(self) -> QWidget:
        """创建模型选项卡"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # 文档理解模型选择
        self._doc_model_group = QGroupBox("文档理解模型")
        doc_model_layout = QVBoxLayout(self._doc_model_group)

        model_select_layout = QHBoxLayout()
        model_select_layout.addWidget(QLabel("VLM 模型:"))
        self._doc_model_combo = QComboBox()
        for model in DOC_UNDERSTANDING_MODELS:
            self._doc_model_combo.addItem(model)
        self._doc_model_combo.setCurrentText(DEFAULT_DOC_UNDERSTANDING_MODEL)
        model_select_layout.addWidget(self._doc_model_combo)
        model_select_layout.addStretch()
        doc_model_layout.addLayout(model_select_layout)

        layout.addWidget(self._doc_model_group)

        layout.addStretch()
        return widget

    def _connect_signals(self):
        """连接信号"""
        self._pipeline_combo.currentIndexChanged.connect(self._on_pipeline_changed)

        # 预处理选项
        self._doc_orientation_cb.toggled.connect(self._on_option_changed)
        self._doc_unwarping_cb.toggled.connect(self._on_option_changed)
        self._textline_orientation_cb.toggled.connect(self._on_option_changed)

        # PP-StructureV3 选项
        self._table_recognition_cb.toggled.connect(self._on_option_changed)
        self._formula_recognition_cb.toggled.connect(self._on_option_changed)
        self._seal_recognition_cb.toggled.connect(self._on_option_changed)
        self._chart_recognition_cb.toggled.connect(self._on_option_changed)

        # VL 选项
        self._vl_layout_cb.toggled.connect(self._on_option_changed)
        self._vl_seal_cb.toggled.connect(self._on_option_changed)
        self._vl_ocr_image_cb.toggled.connect(self._on_option_changed)
        self._vl_markdown_cb.toggled.connect(self._on_option_changed)

        # 模型选择
        self._doc_model_combo.currentTextChanged.connect(self._on_option_changed)

    def _on_pipeline_changed(self):
        """管道选择变更"""
        self._update_tab_visibility()
        self._on_option_changed()

    def _update_tab_visibility(self):
        """根据管道更新选项卡可见性"""
        pipeline = self.get_current_pipeline()
        supported = self._get_supported_options(pipeline)

        # 预处理选项卡
        has_preprocess = any(
            opt in supported
            for opt in [
                "use_doc_orientation_classify",
                "use_doc_unwarping",
                "use_textline_orientation",
            ]
        )

        # 高级选项卡
        has_advanced = any(
            opt in supported
            for opt in [
                "use_table_recognition",
                "use_formula_recognition",
                "use_seal_recognition",
                "use_chart_recognition",
                "vl_use_layout_detection",
            ]
        )

        # 模型选项卡
        has_model = "doc_understanding_model" in supported

        # 设置选项卡可见性
        self._tab_widget.setTabVisible(0, has_preprocess)
        self._tab_widget.setTabVisible(1, has_advanced)
        self._tab_widget.setTabVisible(2, has_model)

        # 设置 PP-StructureV3 组可见性
        pp_opts = [
            "use_table_recognition",
            "use_formula_recognition",
            "use_seal_recognition",
            "use_chart_recognition",
        ]
        self._pp_structure_group.setVisible(any(opt in supported for opt in pp_opts))

        # 设置 VL 组可见性
        vl_opts = [
            "vl_use_layout_detection",
            "vl_use_seal_recognition",
            "vl_use_ocr_for_image_block",
            "vl_format_block_content",
        ]
        self._vl_group.setVisible(any(opt in supported for opt in vl_opts))

        # 如果当前选项卡不可见，切换到第一个可见的
        for i in range(self._tab_widget.count()):
            if self._tab_widget.isTabVisible(i):
                self._tab_widget.setCurrentIndex(i)
                break

    def _get_supported_options(self, pipeline: OCRPipeline) -> list[str]:
        """获取管道支持的选项列表"""
        from vibeocr.core.pipelines import get_pipeline_supported_options

        return get_pipeline_supported_options(pipeline)

    def _on_option_changed(self):
        """选项变更"""
        options = self.get_options()
        self._current_options = options
        self.options_changed.emit(options)

    def get_current_pipeline(self) -> OCRPipeline:
        """获取当前选择的管道"""
        value = self._pipeline_combo.currentData()
        return OCRPipeline(value)

    def get_options(self) -> OCROptions:
        """获取当前选项（仅包含当前管道支持的选项）"""
        from vibeocr.core.pipelines import is_option_supported

        pipeline = self.get_current_pipeline()

        kwargs: dict = {"pipeline": pipeline}

        # 仅添加当前管道支持的选项，不支持的保持默认值
        if is_option_supported(pipeline, "use_doc_orientation_classify"):
            kwargs["use_doc_orientation_classify"] = self._doc_orientation_cb.isChecked()
        if is_option_supported(pipeline, "use_doc_unwarping"):
            kwargs["use_doc_unwarping"] = self._doc_unwarping_cb.isChecked()
        if is_option_supported(pipeline, "use_textline_orientation"):
            kwargs["use_textline_orientation"] = self._textline_orientation_cb.isChecked()
        if is_option_supported(pipeline, "use_table_recognition"):
            kwargs["use_table_recognition"] = self._table_recognition_cb.isChecked()
        if is_option_supported(pipeline, "use_formula_recognition"):
            kwargs["use_formula_recognition"] = self._formula_recognition_cb.isChecked()
        if is_option_supported(pipeline, "use_seal_recognition"):
            kwargs["use_seal_recognition"] = self._seal_recognition_cb.isChecked()
        if is_option_supported(pipeline, "use_chart_recognition"):
            kwargs["use_chart_recognition"] = self._chart_recognition_cb.isChecked()
        if is_option_supported(pipeline, "vl_use_layout_detection"):
            kwargs["vl_use_layout_detection"] = self._vl_layout_cb.isChecked()
        if is_option_supported(pipeline, "vl_format_block_content"):
            kwargs["vl_format_block_content"] = self._vl_markdown_cb.isChecked()
        if is_option_supported(pipeline, "vl_use_seal_recognition"):
            kwargs["vl_use_seal_recognition"] = self._vl_seal_cb.isChecked()
        if is_option_supported(pipeline, "vl_use_ocr_for_image_block"):
            kwargs["vl_use_ocr_for_image_block"] = self._vl_ocr_image_cb.isChecked()
        if is_option_supported(pipeline, "doc_understanding_model"):
            kwargs["doc_understanding_model"] = self._doc_model_combo.currentText()

        return OCROptions(**kwargs)

    def set_options(self, options: OCROptions):
        """设置选项（不触发 options_changed 信号）"""
        self._current_options = options

        # 阻止所有控件信号，防止级联触发
        widgets = [
            self._pipeline_combo,
            self._doc_orientation_cb,
            self._doc_unwarping_cb,
            self._textline_orientation_cb,
            self._table_recognition_cb,
            self._formula_recognition_cb,
            self._seal_recognition_cb,
            self._chart_recognition_cb,
            self._vl_layout_cb,
            self._vl_markdown_cb,
            self._vl_seal_cb,
            self._vl_ocr_image_cb,
            self._doc_model_combo,
        ]
        for w in widgets:
            w.blockSignals(True)

        # 设置管道
        index = self._pipeline_combo.findData(options.pipeline.value)
        if index >= 0:
            self._pipeline_combo.setCurrentIndex(index)

        # 设置预处理选项
        self._doc_orientation_cb.setChecked(options.use_doc_orientation_classify)
        self._doc_unwarping_cb.setChecked(options.use_doc_unwarping)
        self._textline_orientation_cb.setChecked(options.use_textline_orientation)

        # 设置 PP-StructureV3 选项
        self._table_recognition_cb.setChecked(options.use_table_recognition)
        self._formula_recognition_cb.setChecked(options.use_formula_recognition)
        self._seal_recognition_cb.setChecked(options.use_seal_recognition)
        self._chart_recognition_cb.setChecked(options.use_chart_recognition)

        # 设置 VL 选项
        self._vl_layout_cb.setChecked(options.vl_use_layout_detection)
        self._vl_markdown_cb.setChecked(options.vl_format_block_content)
        self._vl_seal_cb.setChecked(options.vl_use_seal_recognition)
        self._vl_ocr_image_cb.setChecked(options.vl_use_ocr_for_image_block)

        # 设置模型
        index = self._doc_model_combo.findText(options.doc_understanding_model)
        if index >= 0:
            self._doc_model_combo.setCurrentIndex(index)

        # 恢复信号
        for w in widgets:
            w.blockSignals(False)

        self._update_tab_visibility()
