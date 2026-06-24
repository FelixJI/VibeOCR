"""二维码生成与识别标签页"""

import logging
from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QGuiApplication,
    QImage,
    QKeySequence,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from vibeocr.services.qrcode_decode_service import QrcodeDecodeService
from vibeocr.services.qrcode_service import QrcodeService
from vibeocr.ui import theme

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


def _qpixmap_to_pil(pixmap: QPixmap) -> Image.Image:
    """QPixmap → PIL.Image（RGB）。用 PNG 中转，不引入新依赖。"""
    from io import BytesIO

    from PySide6.QtCore import QBuffer

    buffer = QBuffer()
    buffer.open(QBuffer.OpenModeFlag.ReadWrite)
    pixmap.save(buffer, "PNG")
    buffer.seek(0)
    img = Image.open(BytesIO(bytes(buffer.data())))
    buffer.close()
    return img.convert("RGB")


def _decode_type_label(type_str: str) -> str:
    """把 pyzbar 的 type 字符串转成更友好的中文标签。"""
    t = type_str.upper()
    if "QR" in t:
        return "二维码"
    return f"条形码·{type_str}"


def _escape_for_richtext(text: str) -> str:
    """转义用于富文本属性值的字符（防止单引号/HTML 破坏）。"""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("'", "&#39;")
        .replace('"', "&quot;")
    )


class DropLabel(QLabel):
    """支持拖入图片数据的 QLabel。"""

    imageDropped = Signal(QPixmap)

    def dragEnterEvent(self, event):
        if event.mimeData().hasImage():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        pm = QPixmap(event.mimeData().imageData())
        if not pm.isNull():
            self.imageDropped.emit(pm)
            event.acceptProposedAction()
        else:
            event.ignore()


class DecodeResultWidget(QWidget):
    """单条识别结果展示：序号 + 类型标签 + 内容/链接 + 操作按钮。"""

    open_url_requested = Signal(str)
    copy_requested = Signal(str)

    def __init__(
        self,
        index: int,
        data: str,
        type_label: str,
        is_url: bool,
        safe_data: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._data = data
        href_value = safe_data if safe_data is not None else data

        row = QHBoxLayout(self)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(8)

        idx_label = QLabel(f"{index}.")
        idx_label.setFixedWidth(20)
        row.addWidget(idx_label)

        type_tag = QLabel(type_label)
        type_tag.setStyleSheet(
            f"QLabel {{ background-color: {theme.Colors.hover_bg};"
            f" color: {theme.Colors.text};"
            f" border-radius: 6px; padding: 1px 6px;"
            f" font-size: {theme.Typography.caption}px; }}"
        )
        row.addWidget(type_tag)

        content_label = QLabel()
        content_label.setWordWrap(True)
        display = data if len(data) <= 80 else data[:77] + "..."
        if is_url:
            content_label.setText(
                f"<a href='{href_value}' style='color:{theme.Colors.accent}; text-decoration: underline;'>"
                f"{display}</a>"
            )
            content_label.setOpenExternalLinks(False)
            content_label.linkActivated.connect(self._on_link)

            open_btn = QPushButton("🔗打开")
            open_btn.setFixedHeight(22)
            open_btn.clicked.connect(lambda: self.open_url_requested.emit(self._data))
            row.addWidget(open_btn)
        else:
            content_label.setText(display)
            content_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
        row.addWidget(content_label, stretch=1)

        copy_btn = QPushButton("📋复制")
        copy_btn.setFixedHeight(22)
        copy_btn.clicked.connect(lambda: self.copy_requested.emit(self._data))
        row.addWidget(copy_btn)

    def _on_link(self, _href: str) -> None:
        # 忽略富文本回传的 href（可能被 _escape_for_richtext 转义），
        # 始终用原始 data，保证含 & 等字符的 URL 正确打开。
        self.open_url_requested.emit(self._data)


class QrcodeTab(QWidget):
    """二维码生成与识别标签页（左侧共享预览 + 右侧生成/识别子标签页）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = QrcodeService()
        self._decode_service = QrcodeDecodeService()
        self._current_image: Image.Image | None = None
        self._logo_path: str | None = None

        # 子页预览状态（切换时保存/恢复）
        self._gen_preview_pixmap: QPixmap | None = None
        self._decode_pending_pixmap: QPixmap | None = None
        self._decode_results: list = []  # list[DecodedItem]，由 _on_decode 填充

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(300)
        self._debounce_timer.timeout.connect(self._refresh_preview)

        self._setup_ui()
        self._connect_signals()

        # Ctrl+V 粘贴图片快捷键：仅在识别子页激活时启用
        self._decode_paste_shortcut = QShortcut(QKeySequence.Paste, self)
        self._decode_paste_shortcut.setEnabled(False)
        self._decode_paste_shortcut.activated.connect(self._on_paste_image)

        self._on_sub_tab_changed(0)  # 初始：生成子页

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)

        self._splitter = QSplitter()

        # ── 左侧：预览区（生成与识别共享） ──
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)

        self._preview_label = DropLabel("输入内容后自动生成预览")
        self._preview_label.setObjectName("previewLabel")
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setMinimumSize(200, 200)
        self._preview_label.setStyleSheet(
            f"QLabel {{ background-color: {theme.Colors.surface_alt};"
            f" border: 1px solid {theme.Colors.border};"
            f" border-radius: {theme.Radius.sm}px; }}"
        )
        self._preview_label.setAcceptDrops(False)  # 仅识别子页激活时开启
        self._preview_label.imageDropped.connect(self._on_image_input)
        left_layout.addWidget(self._preview_label, stretch=1)

        # 生成操作栏（保存/复制）—— 子页切换时显隐
        self._gen_action_bar_widget = QWidget()
        gen_action_bar = QHBoxLayout(self._gen_action_bar_widget)
        gen_action_bar.setContentsMargins(0, 0, 0, 0)
        gen_action_bar.setSpacing(6)

        self._btn_save = QPushButton("保存")
        self._btn_save.setObjectName("btnSave")
        self._btn_save.setFixedHeight(28)
        self._btn_copy = QPushButton("复制到剪贴板")
        self._btn_copy.setObjectName("btnCopy")
        self._btn_copy.setFixedHeight(28)

        gen_action_bar.addWidget(self._btn_save)
        gen_action_bar.addWidget(self._btn_copy)
        gen_action_bar.addStretch()
        left_layout.addWidget(self._gen_action_bar_widget)

        # 识别操作栏（粘贴/选择/识别/清空）—— 子页切换时显隐，初始隐藏
        self._decode_action_bar_widget = QWidget()
        dec_action_bar = QHBoxLayout(self._decode_action_bar_widget)
        dec_action_bar.setContentsMargins(0, 0, 0, 0)
        dec_action_bar.setSpacing(6)

        self._btn_paste_img = QPushButton("粘贴图片")
        self._btn_paste_img.setObjectName("btnPasteImg")
        self._btn_paste_img.setFixedHeight(28)
        self._btn_select_img = QPushButton("选择图片...")
        self._btn_select_img.setObjectName("btnSelectImg")
        self._btn_select_img.setFixedHeight(28)
        dec_action_bar.addWidget(self._btn_paste_img)
        dec_action_bar.addWidget(self._btn_select_img)
        dec_action_bar.addStretch()
        self._btn_decode = QPushButton("🔍 识别")
        self._btn_decode.setObjectName("btnDecode")
        self._btn_decode.setFixedHeight(28)
        self._btn_decode.setEnabled(False)  # 无图时禁用
        dec_action_bar.addWidget(self._btn_decode)
        self._btn_clear = QPushButton("清空")
        self._btn_clear.setObjectName("btnClear")
        self._btn_clear.setFixedHeight(28)
        dec_action_bar.addWidget(self._btn_clear)
        self._decode_action_bar_widget.setVisible(False)
        left_layout.addWidget(self._decode_action_bar_widget)

        self._splitter.addWidget(left_panel)

        # ── 右侧：嵌套子标签页 ──
        self._sub_tabs = QTabWidget()
        self._sub_tabs.setObjectName("subTabs")

        # 「生成」子页 = 原 QScrollArea 包裹的参数面板
        self._sub_tabs.addTab(self._build_generate_panel(), "生成")

        # 「识别」子页（Task 5 填充真实内容，先占位）
        self._sub_tabs.addTab(self._build_decode_panel(), "识别")

        self._splitter.addWidget(self._sub_tabs)
        self._splitter.setSizes([500, 300])

        layout.addWidget(self._splitter, stretch=1)

    def _build_generate_panel(self) -> QScrollArea:
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
        return scroll

    def _build_decode_panel(self) -> QWidget:
        """构建「识别」子页。"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        hint = QLabel(
            "支持粘贴图片 (Ctrl+V)、拖入图片到左侧预览区、\n或点击下方选择文件"
        )
        hint.setStyleSheet(
            f"color: {theme.Colors.text_muted}; font-size: {theme.Typography.caption}px;"
        )
        layout.addWidget(hint)

        # 识别结果区
        layout.addWidget(self._create_section_label("识别结果"))

        self._decode_result_list = QListWidget()
        self._decode_result_list.setObjectName("decodeResultList")
        layout.addWidget(self._decode_result_list, stretch=1)

        # 底部操作
        bottom_row = QHBoxLayout()
        self._btn_copy_all = QPushButton("复制全部")
        self._btn_copy_all.setObjectName("btnCopyAll")
        self._btn_copy_all.setFixedHeight(26)
        bottom_row.addWidget(self._btn_copy_all)
        bottom_row.addStretch()
        self._result_count_label = QLabel("识别到 0 条结果")
        self._result_count_label.setStyleSheet(f"color: {theme.Colors.text_muted};")
        bottom_row.addWidget(self._result_count_label)
        layout.addLayout(bottom_row)

        return panel

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
        self._sub_tabs.currentChanged.connect(self._on_sub_tab_changed)
        # 识别子页按钮
        self._btn_paste_img.clicked.connect(self._on_paste_image)
        self._btn_select_img.clicked.connect(self._on_select_image)
        self._btn_decode.clicked.connect(self._on_decode)
        self._btn_clear.clicked.connect(self._on_clear_decode)
        self._btn_copy_all.clicked.connect(self._on_copy_all)

    def _on_sub_tab_changed(self, index: int) -> None:
        """切换生成/识别子页时，保存/恢复预览状态并切换操作栏与拖入支持。"""
        is_decode = index == 1

        if is_decode:
            # 离开生成页：保存当前预览
            pm = self._preview_label.pixmap()
            self._gen_preview_pixmap = pm if (pm and not pm.isNull()) else None
            # 恢复识别页预览
            if self._decode_pending_pixmap is not None:
                self._preview_label.setPixmap(
                    _scale_pixmap_for_label(
                        self._decode_pending_pixmap, self._preview_label
                    )
                )
            else:
                self._preview_label.clear()
                self._preview_label.setText("粘贴、拖入或选择图片以识别")
        else:
            # 恢复生成页预览
            if self._current_image is not None:
                pixmap = _pil_to_qpixmap(self._current_image)
                self._preview_label.setPixmap(
                    _scale_pixmap_for_label(pixmap, self._preview_label)
                )
            else:
                self._preview_label.clear()
                self._preview_label.setText("输入内容后自动生成预览")

        self._gen_action_bar_widget.setVisible(not is_decode)
        self._decode_action_bar_widget.setVisible(is_decode)
        self._preview_label.setAcceptDrops(is_decode)
        if hasattr(self, "_decode_paste_shortcut"):
            self._decode_paste_shortcut.setEnabled(is_decode)

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
        return f"QPushButton {{ background-color: {color}; border: 1px solid {theme.Colors.border_strong}; padding: 4px; }}"

    # ── 生成子页 slots ──

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
                f"<span style='color:{theme.Colors.danger};'>生成失败：{e}</span>"
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

    # ── 识别子页 slots ──

    def _on_image_input(self, pixmap: QPixmap) -> None:
        """统一的图片输入入口（粘贴/拖入/选择文件）。"""
        if pixmap.isNull():
            return
        # 归一化 devicePixelRatio
        if pixmap.devicePixelRatio() != 1.0:
            pixmap = QPixmap(pixmap)
            pixmap.setDevicePixelRatio(1.0)
        self._decode_pending_pixmap = pixmap
        self._preview_label.setPixmap(
            _scale_pixmap_for_label(pixmap, self._preview_label)
        )
        self._btn_decode.setEnabled(True)
        # 清空上次结果
        self._decode_result_list.clear()
        self._decode_results = []
        self._result_count_label.setText("识别到 0 条结果")

    def _on_paste_image(self) -> None:
        clipboard = QGuiApplication.clipboard()
        pm = clipboard.pixmap()
        if not pm.isNull():
            self._on_image_input(pm)

    def _on_select_image(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择图片",
            "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif *.tiff *.tif *.webp *.jp2)"
            ";;所有文件 (*)",
        )
        if path:
            pm = QPixmap(path)
            if not pm.isNull():
                self._on_image_input(pm)

    def _on_clear_decode(self) -> None:
        self._decode_pending_pixmap = None
        self._decode_results = []
        self._decode_result_list.clear()
        self._btn_decode.setEnabled(False)
        self._result_count_label.setText("识别到 0 条结果")
        self._preview_label.clear()
        self._preview_label.setText("粘贴、拖入或选择图片以识别")

    def _on_decode(self) -> None:
        if self._decode_pending_pixmap is None:
            return
        self._btn_decode.setEnabled(False)
        self._btn_decode.setText("识别中...")
        from PySide6.QtWidgets import QApplication

        QApplication.processEvents()
        try:
            pil_img = _qpixmap_to_pil(self._decode_pending_pixmap)
            results = self._decode_service.decode(pil_img)
        except Exception as e:
            logger.error(f"识别失败: {e}", exc_info=True)
            self._decode_result_list.clear()
            item = QListWidgetItem()
            err_label = QLabel(f"<span style='color:{theme.Colors.danger};'>识别失败：{e}</span>")
            self._decode_result_list.addItem(item)
            self._decode_result_list.setItemWidget(item, err_label)
            item.setSizeHint(err_label.sizeHint())
            self._decode_results = []
            self._result_count_label.setText("识别到 0 条结果")
            self._btn_decode.setText("🔍 识别")
            self._btn_decode.setEnabled(True)
            return

        self._decode_results = results
        self._decode_result_list.clear()
        if not results:
            hint = QLabel(
                f"<span style='color:{theme.Colors.text_muted};'>未识别到二维码/条形码，请尝试更清晰的图片</span>"
            )
            item = QListWidgetItem()
            self._decode_result_list.addItem(item)
            self._decode_result_list.setItemWidget(item, hint)
            item.setSizeHint(hint.sizeHint())
        else:
            for idx, r in enumerate(results, start=1):
                safe_data = _escape_for_richtext(r.data)
                widget = DecodeResultWidget(
                    index=idx,
                    data=r.data,
                    type_label=_decode_type_label(r.type),
                    is_url=r.is_url,
                    safe_data=safe_data,
                )
                widget.open_url_requested.connect(self._on_open_url)
                widget.copy_requested.connect(self._on_copy_single)
                item = QListWidgetItem()
                self._decode_result_list.addItem(item)
                self._decode_result_list.setItemWidget(item, widget)
                item.setSizeHint(widget.sizeHint())

        self._result_count_label.setText(f"识别到 {len(results)} 条结果")
        self._btn_decode.setText("🔍 识别")
        self._btn_decode.setEnabled(True)

    def _on_open_url(self, url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))

    def _on_copy_single(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)

    def _on_copy_all(self) -> None:
        texts = [item.data for item in self._decode_results]
        QGuiApplication.clipboard().setText("\n".join(texts))

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
