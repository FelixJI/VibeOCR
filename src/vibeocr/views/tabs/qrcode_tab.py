"""二维码生成标签页"""

import logging
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from vibeocr.services.qrcode_service import QrcodeService

logger = logging.getLogger(__name__)

FORMAT_ITEMS = [
    ("QR Code", "qr"),
    ("Code 128", "code128"),
    ("Code 39", "code39"),
    ("EAN-13", "ean13"),
    ("EAN-8", "ean8"),
    ("UPC-A", "upc-a"),
    ("ISBN-13", "isbn13"),
    ("ITF", "itf"),
    ("Codabar", "codabar"),
    ("PZN", "pzn"),
    ("GS1-128", "gs1-128"),
]

LABEL_POS_MAP = {0: "bottom", 1: "top", 2: "none"}


def _pil_to_qpixmap(pil_image: Image.Image) -> QPixmap:
    if pil_image.mode == "RGBA":
        data = pil_image.tobytes("raw", "RGBA")
        qimage = QImage(
            data, pil_image.width, pil_image.height, QImage.Format.Format_RGBA8888
        )
    else:
        data = pil_image.tobytes("raw", "RGB")
        qimage = QImage(
            data, pil_image.width, pil_image.height, QImage.Format.Format_RGB888
        )
    return QPixmap.fromImage(qimage.copy())


def _scale_pixmap_for_label(pixmap: QPixmap, label: QLabel) -> QPixmap:
    """缩放 pixmap 使其完整显示在 label 内，适配高分屏。"""
    dpr = label.devicePixelRatio()
    target_w = int(label.width() * dpr)
    target_h = int(label.height() * dpr)
    scaled = pixmap.scaled(
        target_w,
        target_h,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    scaled.setDevicePixelRatio(dpr)
    return scaled


class QrcodeTab(QWidget):
    """二维码生成标签页"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = QrcodeService()
        self._current_image: Image.Image | None = None
        self._logo_path: str | None = None

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(300)
        self._debounce_timer.timeout.connect(self._refresh_preview)

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)

        self._splitter = QSplitter()

        # ── 左侧：预览区 ──
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)

        self._preview_label = QLabel("输入内容后自动生成预览")
        self._preview_label.setObjectName("previewLabel")
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setMinimumSize(200, 200)
        self._preview_label.setStyleSheet(
            "QLabel { background-color: #f5f5f5; border: 1px solid #ddd; border-radius: 4px; }"
        )
        left_layout.addWidget(self._preview_label, stretch=1)

        action_bar = QHBoxLayout()
        action_bar.setSpacing(6)

        self._btn_save = QPushButton("保存")
        self._btn_save.setObjectName("btnSave")
        self._btn_save.setFixedHeight(28)
        self._btn_copy = QPushButton("复制到剪贴板")
        self._btn_copy.setObjectName("btnCopy")
        self._btn_copy.setFixedHeight(28)

        action_bar.addWidget(self._btn_save)
        action_bar.addWidget(self._btn_copy)
        action_bar.addStretch()
        left_layout.addLayout(action_bar)

        self._splitter.addWidget(left_panel)

        # ── 右侧：参数面板（QScrollArea 包裹）──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setMinimumWidth(260)
        scroll.setMaximumWidth(360)

        params_widget = QWidget()
        params_layout = QVBoxLayout(params_widget)
        params_layout.setContentsMargins(8, 4, 8, 4)
        params_layout.setSpacing(8)

        # ── 1. 输入内容 ──
        params_layout.addWidget(self._create_section_label("输入内容"))

        self._format_combo = QComboBox()
        for name, _ in FORMAT_ITEMS:
            self._format_combo.addItem(name)
        params_layout.addWidget(self._format_combo)

        self._text_input = QPlainTextEdit()
        self._text_input.setPlaceholderText("输入要编码的内容...")
        self._text_input.setMaximumHeight(80)
        params_layout.addWidget(self._text_input)

        self._btn_paste = QPushButton("从剪贴板粘贴")
        self._btn_paste.setFixedHeight(26)
        params_layout.addWidget(self._btn_paste)

        # ── 2. 尺寸与纠错 ──
        params_layout.addWidget(self._create_section_label("尺寸与纠错"))

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("尺寸:"))
        self._size_spin = self._create_spin_box(100, 2000, 600)
        size_row.addWidget(self._size_spin)
        size_row.addStretch()
        params_layout.addLayout(size_row)

        ec_row = QHBoxLayout()
        self._ec_label = QLabel("纠错等级:")
        ec_row.addWidget(self._ec_label)
        self._ec_group = _create_button_group(self)
        for btn in self._ec_group.buttons():
            ec_row.addWidget(btn)
        params_layout.addLayout(ec_row)

        # ── 3. 颜色设置 ──
        params_layout.addWidget(self._create_section_label("颜色设置"))

        color_row = QHBoxLayout()
        self._fg_btn = QPushButton("前景色")
        self._fg_color = "#000000"
        self._fg_btn.setStyleSheet(self._color_btn_style(self._fg_color))
        color_row.addWidget(self._fg_btn)

        self._bg_btn = QPushButton("背景色")
        self._bg_color = "#FFFFFF"
        self._bg_btn.setStyleSheet(self._color_btn_style(self._bg_color))
        color_row.addWidget(self._bg_btn)

        self._invert_check = QCheckBox("反色")
        color_row.addWidget(self._invert_check)
        params_layout.addLayout(color_row)

        # ── 4. Logo 嵌入（仅二维码）──
        params_layout.addWidget(self._create_section_label("Logo 嵌入"))

        logo_row = QHBoxLayout()
        self._logo_check = QCheckBox("启用")
        logo_row.addWidget(self._logo_check)
        self._logo_select_btn = QPushButton("选择图片")
        self._logo_select_btn.setEnabled(False)
        logo_row.addWidget(self._logo_select_btn)
        params_layout.addLayout(logo_row)

        logo_size_row = QHBoxLayout()
        logo_size_row.addWidget(QLabel("Logo 大小比例:"))
        self._logo_ratio_spin = self._create_spin_box(5, 50, 20)
        self._logo_ratio_spin.setSuffix("%")
        logo_size_row.addWidget(self._logo_ratio_spin)
        logo_size_row.addStretch()
        params_layout.addLayout(logo_size_row)
        self._logo_section_widgets = [
            self._logo_check,
            self._logo_select_btn,
            self._logo_ratio_spin,
        ]

        # ── 5. 文字说明 ──
        params_layout.addWidget(self._create_section_label("文字说明"))

        self._label_text_input = QLineEdit()
        self._label_text_input.setPlaceholderText("自定义说明文字（留空使用原始内容）")
        params_layout.addWidget(self._label_text_input)

        label_pos_row = QHBoxLayout()
        label_pos_row.addWidget(QLabel("位置:"))
        self._label_pos_combo = QComboBox()
        self._label_pos_combo.addItems(["下方", "上方", "无"])
        label_pos_row.addWidget(self._label_pos_combo)
        label_pos_row.addStretch()
        params_layout.addLayout(label_pos_row)

        label_font_row = QHBoxLayout()
        label_font_row.addWidget(QLabel("字体大小:"))
        self._label_font_spin = self._create_spin_box(8, 48, 12)
        label_font_row.addWidget(self._label_font_spin)
        label_font_row.addStretch()
        params_layout.addLayout(label_font_row)

        params_layout.addStretch()

        scroll.setWidget(params_widget)
        self._splitter.addWidget(scroll)
        self._splitter.setSizes([500, 300])

        layout.addWidget(self._splitter, stretch=1)

    def _connect_signals(self) -> None:
        self._format_combo.currentIndexChanged.connect(self._on_format_changed)
        self._text_input.textChanged.connect(self._schedule_refresh)
        self._size_spin.valueChanged.connect(self._schedule_refresh)
        self._ec_group.buttonClicked.connect(self._schedule_refresh)
        self._invert_check.stateChanged.connect(self._schedule_refresh)
        self._logo_check.stateChanged.connect(self._on_logo_check_changed)
        self._logo_select_btn.clicked.connect(self._on_logo_select)
        self._logo_ratio_spin.valueChanged.connect(self._schedule_refresh)
        self._label_text_input.textChanged.connect(self._schedule_refresh)
        self._label_pos_combo.currentIndexChanged.connect(self._schedule_refresh)
        self._label_font_spin.valueChanged.connect(self._schedule_refresh)
        self._fg_btn.clicked.connect(self._on_pick_fg_color)
        self._bg_btn.clicked.connect(self._on_pick_bg_color)
        self._btn_paste.clicked.connect(self._on_paste_from_clipboard)
        self._btn_save.clicked.connect(self._on_save)
        self._btn_copy.clicked.connect(self._on_copy)

    # ── helpers ──

    @staticmethod
    def _create_section_label(text: str) -> QLabel:
        label = QLabel(f"<b>{text}</b>")
        label.setContentsMargins(0, 4, 0, 0)
        return label

    @staticmethod
    def _create_spin_box(min_val: int, max_val: int, default: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(min_val, max_val)
        spin.setValue(default)
        spin.setFixedWidth(80)
        return spin

    @staticmethod
    def _color_btn_style(color: str) -> str:
        return f"QPushButton {{ background-color: {color}; border: 1px solid #999; padding: 4px; }}"

    # ── slots ──

    def _on_format_changed(self, index: int) -> None:
        is_qr = FORMAT_ITEMS[index][1] == "qr"
        self._ec_label.setVisible(is_qr)
        for btn in self._ec_group.buttons():
            btn.setVisible(is_qr)
        for w in self._logo_section_widgets:
            w.setVisible(is_qr)
        self._schedule_refresh()

    def _on_logo_check_changed(self, state: int) -> None:
        self._logo_select_btn.setEnabled(bool(state))
        self._logo_ratio_spin.setEnabled(bool(state))
        self._schedule_refresh()

    def _on_logo_select(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 Logo 图片",
            "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif);;所有文件 (*)",
        )
        if path:
            self._logo_path = path
            self._logo_select_btn.setText(Path(path).name)
            self._schedule_refresh()

    def _on_pick_fg_color(self) -> None:
        from PySide6.QtWidgets import QColorDialog

        color = QColorDialog.getColor(QColor(self._fg_color), self, "选择前景色")
        if color.isValid():
            self._fg_color = color.name()
            self._fg_btn.setStyleSheet(self._color_btn_style(self._fg_color))
            self._schedule_refresh()

    def _on_pick_bg_color(self) -> None:
        from PySide6.QtWidgets import QColorDialog

        color = QColorDialog.getColor(QColor(self._bg_color), self, "选择背景色")
        if color.isValid():
            self._bg_color = color.name()
            self._bg_btn.setStyleSheet(self._color_btn_style(self._bg_color))
            self._schedule_refresh()

    def _on_paste_from_clipboard(self) -> None:
        from PySide6.QtGui import QGuiApplication

        text = QGuiApplication.clipboard().text()
        if text:
            self._text_input.setPlainText(text)

    def _schedule_refresh(self) -> None:
        self._debounce_timer.start()

    def _build_options(self) -> dict:
        fmt_key = FORMAT_ITEMS[self._format_combo.currentIndex()][1]
        ec_btn = self._ec_group.checkedButton()
        ec_val = ec_btn.property("ec_value") if ec_btn else "M"

        options = self._service.default_options()
        options["format"] = fmt_key
        options["size"] = self._size_spin.value()
        options["error_correction"] = ec_val
        options["fg_color"] = self._fg_color
        options["bg_color"] = self._bg_color
        options["invert"] = self._invert_check.isChecked()
        options["logo_path"] = self._logo_path if self._logo_check.isChecked() else None
        options["logo_ratio"] = self._logo_ratio_spin.value() / 100.0
        options["label_text"] = self._label_text_input.text()
        options["label_position"] = LABEL_POS_MAP.get(
            self._label_pos_combo.currentIndex(), "bottom"
        )
        options["label_font_size"] = self._label_font_spin.value()
        return options

    def _refresh_preview(self) -> None:
        text = self._text_input.toPlainText().strip()
        if not text:
            self._preview_label.setText("输入内容后自动生成预览")
            self._current_image = None
            return

        try:
            options = self._build_options()
            img = self._service.generate(text, options)

            if options.get("logo_path"):
                img = self._service.apply_logo(
                    img, options["logo_path"], options["logo_ratio"]
                )

            label_text = options.get("label_text") or text
            img = self._service.apply_text_label(
                img, label_text, options["label_position"], options["label_font_size"]
            )

            if options.get("invert"):
                img = self._service.invert_colors(img)

            self._current_image = img
            pixmap = _pil_to_qpixmap(img)
            self._preview_label.setPixmap(
                _scale_pixmap_for_label(pixmap, self._preview_label)
            )
        except Exception as e:
            logger.error(f"生成预览失败: {e}", exc_info=True)
            self._preview_label.setText(
                f"<span style='color:#f44336;'>生成失败：{e}</span>"
            )
            self._current_image = None

    def _on_save(self) -> None:
        if self._current_image is None:
            return

        from PySide6.QtWidgets import QFileDialog

        options = self._build_options()
        is_qr = options["format"] == "qr"

        filters = "PNG (*.png);;JPG (*.jpg)"
        if is_qr and not options.get("logo_path"):
            filters += ";;SVG (*.svg)"

        path, _ = QFileDialog.getSaveFileName(self, "保存", "", filters)
        if not path:
            return

        try:
            if path.lower().endswith(".svg"):
                text = self._text_input.toPlainText().strip()
                svg_content = self._service.generate_svg(text, options)
                Path(path).write_text(svg_content, encoding="utf-8")
            else:
                fmt = "JPEG" if path.lower().endswith((".jpg", ".jpeg")) else "PNG"
                self._current_image.save(path, fmt)
        except Exception as e:
            logger.error(f"保存失败: {e}", exc_info=True)
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(self, "保存失败", str(e))

    def _on_copy(self) -> None:
        if self._current_image is None:
            return

        from PySide6.QtGui import QGuiApplication

        pixmap = _pil_to_qpixmap(self._current_image)
        QGuiApplication.clipboard().setPixmap(pixmap)
        logger.debug("二维码已复制到剪贴板")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._current_image is not None:
            self._refresh_preview()


def _create_button_group(parent: QWidget):
    from PySide6.QtWidgets import QButtonGroup

    group = QButtonGroup(parent)
    for text, val in [("L", "L"), ("M", "M"), ("Q", "Q"), ("H", "H")]:
        rb = QRadioButton(text)
        rb.setProperty("ec_value", val)
        group.addButton(rb)
        if val == "M":
            rb.setChecked(True)
    return group
