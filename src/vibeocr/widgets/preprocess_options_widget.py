# src/vibeocr/widgets/preprocess_options_widget.py
"""预处理选项组件 - 选项卡式布局"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from vibeocr.core.pipelines import (
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
        self._pipeline_locked = False
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

        self._pipeline_lock_label = QLabel()
        self._pipeline_lock_label.setStyleSheet("color: #888; font-size: 11px;")
        self._pipeline_lock_label.setVisible(False)
        pipeline_layout.addWidget(self._pipeline_lock_label)

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

        # MineRU 文档解析选项组
        self._mineru_group = self._create_mineru_group()
        layout.addWidget(self._mineru_group)

        layout.addStretch()
        return widget

    def _create_mineru_group(self) -> QGroupBox:
        """创建 MineRU 文档解析选项组"""
        group = QGroupBox("文档解析选项")
        layout = QVBoxLayout(group)

        # 后端选择
        backend_layout = QHBoxLayout()
        backend_layout.addWidget(QLabel("解析后端:"))
        self._backend_combo = QComboBox()
        self._backend_combo.addItem("混合引擎（推荐）", "hybrid-auto-engine")
        self._backend_combo.addItem("VLM 智能引擎", "vlm-auto-engine")
        self._backend_combo.addItem("传统流水线", "pipeline")
        self._backend_combo.setToolTip(
            "VLM 智能引擎：使用视觉语言模型，效果最佳（失败自动回退混合引擎）\n"
            "混合引擎：兼顾兼容性和效果\n"
            "传统流水线：纯 CPU 可用，效果一般"
        )
        backend_layout.addWidget(self._backend_combo)
        backend_layout.addStretch()
        layout.addLayout(backend_layout)

        # 解析方法
        parse_method_layout = QHBoxLayout()
        parse_method_layout.addWidget(QLabel("解析方法:"))
        self._parse_method_combo = QComboBox()
        self._parse_method_combo.addItem("自动（提取 + 识别）", "auto")
        self._parse_method_combo.addItem("纯文本提取", "txt")
        self._parse_method_combo.addItem("强制 OCR 识别", "ocr")
        self._parse_method_combo.setToolTip(
            "自动：智能选择最佳方式\n"
            "纯文本提取：直接提取 PDF 内嵌文字，速度快\n"
            "强制 OCR 识别：将每页视为图片进行识别，适用于扫描件"
        )
        parse_method_layout.addWidget(self._parse_method_combo)
        parse_method_layout.addStretch()
        layout.addLayout(parse_method_layout)

        self._enable_formula_cb = QCheckBox("公式识别")
        self._enable_formula_cb.setToolTip("启用数学公式识别（LaTeX 输出）")
        self._enable_formula_cb.setChecked(True)
        layout.addWidget(self._enable_formula_cb)

        self._enable_table_cb = QCheckBox("表格识别")
        self._enable_table_cb.setToolTip("启用表格结构识别（HTML 输出）")
        self._enable_table_cb.setChecked(True)
        layout.addWidget(self._enable_table_cb)

        # 语言选择
        lang_layout = QHBoxLayout()
        lang_layout.addWidget(QLabel("文档语言:"))
        self._lang_combo = QComboBox()
        self._lang_combo.addItem("自动检测", "")
        self._lang_combo.addItem("中文", "zh")
        self._lang_combo.addItem("英文", "en")
        self._lang_combo.addItem("中英混合", "zh,en")
        self._lang_combo.addItem("日文", "ja")
        self._lang_combo.addItem("韩文", "ko")
        self._lang_combo.setToolTip("文档主要语言，自动检测适用于混合语言")
        lang_layout.addWidget(self._lang_combo)
        lang_layout.addStretch()
        layout.addLayout(lang_layout)

        # 页码范围
        page_layout = QHBoxLayout()
        page_layout.addWidget(QLabel("起始页:"))
        self._start_page_spin = QSpinBox()
        self._start_page_spin.setRange(0, 99999)
        self._start_page_spin.setValue(0)
        self._start_page_spin.setToolTip("起始页（从 0 开始）")
        page_layout.addWidget(self._start_page_spin)
        page_layout.addWidget(QLabel("结束页:"))
        self._end_page_check = QCheckBox("限制")
        self._end_page_spin = QSpinBox()
        self._end_page_spin.setRange(0, 99999)
        self._end_page_spin.setValue(99999)
        self._end_page_spin.setEnabled(False)
        self._end_page_check.toggled.connect(self._end_page_spin.setEnabled)
        page_layout.addWidget(self._end_page_check)
        page_layout.addWidget(self._end_page_spin)
        page_layout.addStretch()
        layout.addLayout(page_layout)

        return group

    def _connect_signals(self):
        """连接信号"""
        self._pipeline_combo.currentIndexChanged.connect(self._on_pipeline_changed)

        # 预处理选项
        self._doc_orientation_cb.toggled.connect(self._on_option_changed)
        self._doc_unwarping_cb.toggled.connect(self._on_option_changed)
        self._textline_orientation_cb.toggled.connect(self._on_option_changed)

        # MineRU 选项
        self._enable_formula_cb.toggled.connect(self._on_option_changed)
        self._enable_table_cb.toggled.connect(self._on_option_changed)
        self._backend_combo.currentIndexChanged.connect(self._on_option_changed)
        self._parse_method_combo.currentIndexChanged.connect(self._on_option_changed)
        self._lang_combo.currentIndexChanged.connect(self._on_option_changed)
        self._start_page_spin.valueChanged.connect(self._on_option_changed)
        self._end_page_check.toggled.connect(self._on_option_changed)
        self._end_page_spin.valueChanged.connect(self._on_option_changed)

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
                "parse_method",
                "backend",
                "enable_formula",
                "enable_table",
                "lang_list",
                "start_page_id",
                "end_page_id",
            ]
        )

        # 设置选项卡可见性
        self._tab_widget.setTabVisible(0, has_preprocess)
        self._tab_widget.setTabVisible(1, has_advanced)

        # 设置 MineRU 组可见性
        mineru_opts = ["parse_method", "backend", "enable_formula", "enable_table",
                       "lang_list", "start_page_id", "end_page_id"]
        self._mineru_group.setVisible(any(opt in supported for opt in mineru_opts))

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

        if is_option_supported(pipeline, "use_doc_orientation_classify"):
            kwargs["use_doc_orientation_classify"] = self._doc_orientation_cb.isChecked()
        if is_option_supported(pipeline, "use_doc_unwarping"):
            kwargs["use_doc_unwarping"] = self._doc_unwarping_cb.isChecked()
        if is_option_supported(pipeline, "use_textline_orientation"):
            kwargs["use_textline_orientation"] = self._textline_orientation_cb.isChecked()
        if is_option_supported(pipeline, "enable_formula"):
            kwargs["enable_formula"] = self._enable_formula_cb.isChecked()
        if is_option_supported(pipeline, "enable_table"):
            kwargs["enable_table"] = self._enable_table_cb.isChecked()
        if is_option_supported(pipeline, "backend"):
            kwargs["backend"] = self._backend_combo.currentData()
        if is_option_supported(pipeline, "parse_method"):
            kwargs["parse_method"] = self._parse_method_combo.currentData()
        if is_option_supported(pipeline, "lang_list"):
            lang_data = self._lang_combo.currentData()
            if lang_data:
                kwargs["lang_list"] = lang_data.split(",")
            else:
                kwargs["lang_list"] = []
        if is_option_supported(pipeline, "start_page_id"):
            kwargs["start_page_id"] = self._start_page_spin.value()
        if is_option_supported(pipeline, "end_page_id"):
            if self._end_page_check.isChecked():
                kwargs["end_page_id"] = self._end_page_spin.value()
            else:
                kwargs["end_page_id"] = None

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
            self._enable_formula_cb,
            self._enable_table_cb,
            self._backend_combo,
            self._parse_method_combo,
            self._lang_combo,
            self._start_page_spin,
            self._end_page_check,
            self._end_page_spin,
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

        # 设置 MineRU 选项
        self._enable_formula_cb.setChecked(options.enable_formula)
        self._enable_table_cb.setChecked(options.enable_table)

        # 设置 backend
        backend_idx = self._backend_combo.findData(options.backend)
        if backend_idx >= 0:
            self._backend_combo.setCurrentIndex(backend_idx)

        parse_method_idx = self._parse_method_combo.findData(options.parse_method)
        if parse_method_idx >= 0:
            self._parse_method_combo.setCurrentIndex(parse_method_idx)

        # 设置语言
        if options.lang_list:
            lang_str = ",".join(options.lang_list)
            lang_idx = self._lang_combo.findData(lang_str)
            if lang_idx >= 0:
                self._lang_combo.setCurrentIndex(lang_idx)
        else:
            self._lang_combo.setCurrentIndex(0)

        # 设置页码范围
        self._start_page_spin.setValue(options.start_page_id)
        if options.end_page_id is not None:
            self._end_page_check.setChecked(True)
            self._end_page_spin.setValue(options.end_page_id)
        else:
            self._end_page_check.setChecked(False)

        # 恢复信号
        for w in widgets:
            w.blockSignals(False)

        self._update_tab_visibility()

    # ── 管道锁定 ──

    def lock_to_document_parsing(self, reason: str = "") -> None:
        """锁定管道为「文档解析」，禁用切换。

        Args:
            reason: 锁定原因，显示在管道旁边（如 "当前文件仅支持文档解析"）
        """
        if self._pipeline_locked:
            return
        self._pipeline_locked = True

        # 切换到文档解析
        idx = self._pipeline_combo.findData(OCRPipeline.DOCUMENT_PARSING.value)
        if idx >= 0:
            self._pipeline_combo.blockSignals(True)
            self._pipeline_combo.setCurrentIndex(idx)
            self._pipeline_combo.blockSignals(False)

        self._pipeline_combo.setEnabled(False)

        if reason:
            self._pipeline_lock_label.setText(f"({reason})")
        else:
            self._pipeline_lock_label.setText("(仅文档解析)")
        self._pipeline_lock_label.setVisible(True)

        self._update_tab_visibility()

    def unlock_pipeline(self) -> None:
        """解除管道锁定，恢复自由选择。"""
        if not self._pipeline_locked:
            return
        self._pipeline_locked = False
        self._pipeline_combo.setEnabled(True)
        self._pipeline_lock_label.setVisible(False)

    @property
    def is_pipeline_locked(self) -> bool:
        return self._pipeline_locked
