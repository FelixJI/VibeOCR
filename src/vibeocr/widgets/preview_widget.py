"""Preview widget for image display, file loading and screenshot trigger"""

from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QPainter, QPen, QPixmap
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from vibeocr.models.ocr_result import DISCARDED_BLOCK_TYPES, TextBlock
from vibeocr.ui import theme

# 置信度阈值
LOW_CONFIDENCE_THRESHOLD = 0.80

# 无真实文本置信度的块类型：结构识别（表格/图片/图表/印章）与公式管道
# 在 pipeline 里 score 是占位值（0.9 / 1.0），不应在 tooltip 里显示为
# 误导性的百分比。与 base_tab._build_content_list 的白名单保持一致，
# 并补充 formula（score=1.0 占位）。键取自 TextBlock.label。
NO_CONFIDENCE_LABELS = frozenset(
    {"table", "image", "figure", "chart", "seal", "formula"}
)

# 置信度着色颜色
HIGH_CONF_FILL = QColor(76, 175, 80, 40)  # 淡绿色填充
HIGH_CONF_BORDER = QColor(76, 175, 80, 160)  # 淡绿色边框
LOW_CONF_FILL = QColor(244, 67, 54, 60)  # 红色填充
LOW_CONF_BORDER = QColor(244, 67, 54, 200)  # 红色边框
EDIT_FILL = QColor(255, 193, 7, 40)  # 琥珀色填充（手动修改）
EDIT_BORDER = QColor(255, 152, 0, 200)  # 橙色边框（手动修改）

# 块类型着色常量（来自 FilePreviewWidget）
BBOX_NORM = 1000.0

BLOCK_COLORS = {
    "text": QColor(59, 130, 246, 30),
    "title": QColor(239, 68, 68, 30),
    "table": QColor(34, 197, 94, 30),
    "image": QColor(168, 85, 247, 30),
    "figure": QColor(168, 85, 247, 30),
    "chart": QColor(236, 72, 153, 30),
    "equation": QColor(249, 115, 22, 30),
    "interline_equation": QColor(249, 115, 22, 30),
    "inline_equation": QColor(249, 115, 22, 30),
    # PaddleX 公式管道（pipeline_formula）输出 label/type="formula"，
    # 归一到橙色（与 equation 一致），避免回退到蓝色文本色与文字混淆。
    "formula": QColor(249, 115, 22, 30),
    "list": QColor(6, 182, 212, 30),
    "code": QColor(139, 92, 246, 30),
    "seal": QColor(107, 114, 128, 30),
}

BLOCK_BORDER_COLORS = {
    "text": QColor(59, 130, 246, 200),
    "title": QColor(239, 68, 68, 200),
    "table": QColor(34, 197, 94, 200),
    "image": QColor(168, 85, 247, 200),
    "figure": QColor(168, 85, 247, 200),
    "chart": QColor(236, 72, 153, 200),
    "equation": QColor(249, 115, 22, 200),
    "interline_equation": QColor(249, 115, 22, 200),
    "inline_equation": QColor(249, 115, 22, 200),
    "formula": QColor(249, 115, 22, 200),
    "list": QColor(6, 182, 212, 200),
    "code": QColor(139, 92, 246, 200),
    "seal": QColor(107, 114, 128, 200),
}

BLOCK_TYPE_LABELS = {
    "text": "文本",
    "title": "标题",
    "table": "表格",
    "image": "图片",
    "figure": "图片",
    "chart": "图表",
    "equation": "公式",
    "interline_equation": "公式",
    "inline_equation": "公式",
    "formula": "公式",
    "list": "列表",
    "code": "代码",
    "seal": "印章",
}


class UnifiedBBoxOverlay(QWidget):
    """统一 BBox 覆盖层，支持置信度着色和块类型着色两种模式"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        # 置信度模式数据: list of (x, y, w, h, score, text, is_manually_edited)
        self._conf_rects: list[tuple[float, float, float, float, float, str, bool]] = []
        # 块类型模式数据: list of (content_index, rect, block_type, fill, border, confidence)
        self._type_rects: list[
            tuple[int, QRectF, str, QColor, QColor, float | None]
        ] = []
        self._mode: str = "confidence"  # "confidence" or "block_type"
        self._hovered_index: int = -1
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def set_confidence_blocks(self, rects) -> None:
        self._mode = "confidence"
        self._conf_rects = rects
        self._hovered_index = -1
        self.update()

    def set_type_blocks(self, rects) -> None:
        self._mode = "block_type"
        self._type_rects = rects
        self._hovered_index = -1
        self.update()

    def set_hovered(self, index: int) -> None:
        if index != self._hovered_index:
            self._hovered_index = index
            self.update()

    def clear(self) -> None:
        self._conf_rects.clear()
        self._type_rects.clear()
        self._hovered_index = -1
        self.update()

    def paintEvent(self, event) -> None:
        if self._mode == "confidence":
            self._paint_confidence()
        else:
            self._paint_block_type()

    def _paint_confidence(self) -> None:
        if not self._conf_rects:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        for i, (x, y, w, h, score, _text, is_manually_edited) in enumerate(
            self._conf_rects
        ):
            rect = QRectF(x, y, w, h)
            is_low = score < LOW_CONFIDENCE_THRESHOLD
            is_hovered = i == self._hovered_index

            if is_manually_edited:
                fill = EDIT_FILL
                border = EDIT_BORDER
            elif is_low:
                fill = LOW_CONF_FILL
                border = LOW_CONF_BORDER
            else:
                fill = HIGH_CONF_FILL
                border = HIGH_CONF_BORDER

            if is_hovered:
                fill = QColor(fill)
                fill.setAlpha(min(fill.alpha() + 80, 200))

            painter.fillRect(rect, fill)
            pen = QPen(border, 2)
            painter.setPen(pen)
            painter.drawRect(rect)

        # 置信度模式下：若存在手动修改的块，绘制图例说明橙色含义
        # （橙色 = 手动修改）。普通高/低置信度颜色固定且语义明显，不入图例。
        self._paint_type_legend(painter)
        painter.end()

    def _paint_block_type(self) -> None:
        if not self._type_rects:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        for (
            cl_idx,
            rect,
            block_type,
            fill_color,
            border_color,
            confidence,
        ) in self._type_rects:
            is_hovered = cl_idx == self._hovered_index
            is_low_conf = (
                confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD
            )

            if is_low_conf:
                fill = QColor(LOW_CONF_FILL)
                border = QColor(LOW_CONF_BORDER)
            elif is_hovered:
                fill = QColor(fill_color)
                fill.setAlpha(min(fill.alpha() + 100, 220))
                border = QColor(border_color)
                border.setAlpha(255)
            else:
                fill = fill_color
                border = border_color

            painter.fillRect(rect, fill)
            pen = QPen(border, 2 if is_low_conf else 1)
            painter.setPen(pen)
            painter.drawRect(rect)

        # 类型用边框颜色编码，文字标识集中在右上角图例中，避免遮挡框选内容
        self._paint_type_legend(painter)
        painter.end()

    def _legend_entries(self) -> list[tuple[str, QColor]]:
        """计算图例条目：(标签, 色块颜色)。

        - 块类型模式：按中文标签去重收集当前画面出现的类型颜色。
        - 若存在任一手动修改块（置信度模式 _conf_rects 的 is_manually_edited），
          追加一项"修改后"（橙色 EDIT_BORDER），解释橙色含义。
        """
        seen: set[str] = set()
        entries: list[tuple[str, QColor]] = []
        for _idx, _rect, block_type, _fill, border_color, _conf in self._type_rects:
            if block_type in seen:
                continue
            seen.add(block_type)
            label = BLOCK_TYPE_LABELS.get(block_type, block_type)
            # figure/image 等同色同名的合并：按中文标签去重
            if any(lbl == label for lbl, _ in entries):
                continue
            swatch = QColor(border_color)
            swatch.setAlpha(255)
            entries.append((label, swatch))

        # 追加"修改后"图例：只要存在任一手动修改块就显示。
        # 置信度模式 _conf_rects 的第 7 项（index 6）是 is_manually_edited。
        if any(r[6] for r in self._conf_rects):
            edited_swatch = QColor(EDIT_BORDER)
            edited_swatch.setAlpha(255)
            entries.append(("修改后", edited_swatch))
        return entries

    def _paint_type_legend(self, painter: QPainter) -> None:
        """在画布右上角绘制类型图例，仅列出当前画面中出现的类型（按中文标签去重）。

        除块类型颜色外，若画面上存在"手动修改"的块（橙色 EDIT_BORDER），
        追加一项"修改后"图例，避免用户不知橙色含义。
        """
        entries = self._legend_entries()
        if not entries:
            return

        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        metrics = painter.fontMetrics()

        padding = 6
        swatch_size = 10
        swatch_gap = 5
        line_height = max(metrics.height(), swatch_size) + 2
        max_label_w = max(metrics.horizontalAdvance(label) for label, _ in entries)
        legend_w = padding * 2 + swatch_size + swatch_gap + max_label_w
        legend_h = padding * 2 + line_height * len(entries)

        margin = 8
        # 右上角，若空间不足则退到左上角
        legend_x = self.width() - margin - legend_w
        if legend_x < margin:
            legend_x = margin
        legend_y = margin
        legend_rect = QRectF(legend_x, legend_y, legend_w, legend_h)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 160))
        painter.drawRoundedRect(legend_rect, 4, 4)

        text_pen = QPen(QColor(255, 255, 255))
        for i, (label, color) in enumerate(entries):
            row_y = legend_y + padding + i * line_height
            sx = legend_x + padding
            sy = row_y + (line_height - swatch_size) / 2
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRect(QRectF(sx, sy, swatch_size, swatch_size))
            tx = sx + swatch_size + swatch_gap
            text_rect = QRectF(
                tx, row_y, legend_x + legend_w - padding - tx, line_height
            )
            painter.setPen(text_pen)
            painter.drawText(
                text_rect,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                label,
            )


class ImageViewerDialog(QDialog):
    """原图查看对话框，支持滚轮缩放和拖动滚动。"""

    _MIN_SCALE = 0.1
    _MAX_SCALE = 10.0

    def __init__(self, pixmap: QPixmap, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("查看原图")
        self.setMinimumSize(640, 480)

        self._pixmap = pixmap
        self._scale = 1.0  # 1.0 = 原始尺寸

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 工具栏
        toolbar = QWidget()
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(6, 4, 6, 4)

        self._zoom_out_btn = QPushButton("-")
        self._zoom_out_btn.setFixedWidth(30)
        self._zoom_out_btn.setToolTip("缩小")
        self._zoom_out_btn.clicked.connect(lambda: self._adjust_scale(0.8))

        self._zoom_label = QLabel("100%")
        self._zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._zoom_label.setMinimumWidth(60)

        self._zoom_in_btn = QPushButton("+")
        self._zoom_in_btn.setFixedWidth(30)
        self._zoom_in_btn.setToolTip("放大")
        self._zoom_in_btn.clicked.connect(lambda: self._adjust_scale(1.25))

        self._fit_btn = QPushButton("适应")
        self._fit_btn.setFixedWidth(50)
        self._fit_btn.setToolTip("适应窗口")
        self._fit_btn.clicked.connect(self._fit_to_window)

        self._orig_btn = QPushButton("1:1")
        self._orig_btn.setFixedWidth(40)
        self._orig_btn.setToolTip("原始大小")
        self._orig_btn.clicked.connect(lambda: self._set_scale(1.0))

        tb_layout.addWidget(self._zoom_out_btn)
        tb_layout.addWidget(self._zoom_label)
        tb_layout.addWidget(self._zoom_in_btn)
        tb_layout.addStretch()
        tb_layout.addWidget(self._fit_btn)
        tb_layout.addWidget(self._orig_btn)

        layout.addWidget(toolbar)

        # 滚动区域 + 图片标签
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(False)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._img_label = QLabel()
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll.setWidget(self._img_label)

        layout.addWidget(self._scroll, stretch=1)

        # 初始按窗口大小适应
        QTimer.singleShot(0, self._fit_to_window)

    def _update_display(self) -> None:
        scaled_w = int(self._pixmap.width() * self._scale)
        scaled_h = int(self._pixmap.height() * self._scale)
        scaled = self._pixmap.scaled(
            scaled_w,
            scaled_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._img_label.setPixmap(scaled)
        self._img_label.resize(scaled.size())
        self._zoom_label.setText(f"{self._scale:.0%}")

    def _set_scale(self, scale: float) -> None:
        self._scale = max(self._MIN_SCALE, min(self._MAX_SCALE, scale))
        self._update_display()

    def _adjust_scale(self, factor: float) -> None:
        self._set_scale(self._scale * factor)

    def _fit_to_window(self) -> None:
        vw = self._scroll.viewport().width()
        vh = self._scroll.viewport().height()
        pw = self._pixmap.width()
        ph = self._pixmap.height()
        if pw <= 0 or ph <= 0:
            return
        self._set_scale(min(vw / pw, vh / ph))

    def wheelEvent(self, event) -> None:
        """滚轮缩放。"""
        delta = event.angleDelta().y()
        if delta > 0:
            self._adjust_scale(1.15)
        elif delta < 0:
            self._adjust_scale(1 / 1.15)


class PreviewWidget(QWidget):
    """统一图片预览组件

    支持图片/PDF 加载、截图触发、BBox 高亮、翻页导航。
    支持两种覆盖层模式：
    - 置信度模式（单次识别）：通过 set_text_blocks 设置
    - 块类型模式（批量识别/文档解析）：通过 set_content_list 设置
    """

    screenshot_requested = Signal()
    file_open_requested = Signal()
    image_changed = Signal()
    block_clicked = Signal(int)
    block_text_edited = Signal(int, str)
    block_hovered = Signal(int)
    block_unhovered = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        empty_text: str = "左键点击截图 · 右键点击选择文件\n\n支持图片、PDF 格式",
    ) -> None:
        super().__init__(parent)
        self._empty_text = empty_text
        self._pixmap: QPixmap | None = None
        self._original_pixmap: QPixmap | None = None
        self._img_w: int = 0
        self._img_h: int = 0
        self._text_blocks: list[TextBlock] = []
        self._block_screen_rects: list[tuple[float, float, float, float]] = []
        # 块类型模式的命中矩形：list of (content_index, screen_rect, block_type)
        self._type_screen_rects: list[tuple[int, QRectF, str]] = []
        self._hovered_block: int | str = -1
        self._editing_index: int = -1
        self._content_list: list[dict] = []
        self._current_file: str = ""
        self._is_pdf = False
        self._highlight_block_index: int = -1

        # PDF
        self._pdf_doc: QPdfDocument | None = None
        self._current_page: int = 0
        self._total_pages: int = 0

        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 翻页导航栏
        self._nav_bar = QWidget()
        nav_layout = QHBoxLayout(self._nav_bar)
        nav_layout.setContentsMargins(4, 0, 4, 0)

        self._prev_btn = QPushButton("<")
        self._prev_btn.setFixedWidth(30)
        self._prev_btn.setEnabled(False)

        self._page_label = QLabel("0 / 0")
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._next_btn = QPushButton(">")
        self._next_btn.setFixedWidth(30)
        self._next_btn.setEnabled(False)

        nav_layout.addWidget(self._prev_btn)
        nav_layout.addStretch()
        nav_layout.addWidget(self._page_label)
        nav_layout.addStretch()
        nav_layout.addWidget(self._next_btn)

        layout.addWidget(self._nav_bar)

        # 预览区域（带滚动）
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setMinimumSize(200, 200)
        self._image_label.setStyleSheet(
            f"QLabel {{ background-color: {theme.Colors.surface_alt};"
            f" border: 2px dashed {theme.Colors.border}; }}"
        )
        self._image_label.setText(self._empty_text)
        self._image_label.setWordWrap(True)
        self._image_label.mousePressEvent = self._on_label_click  # type: ignore[method-assign]
        self._image_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._image_label.customContextMenuRequested.connect(self._on_context_menu)
        self._scroll_area.setWidget(self._image_label)

        layout.addWidget(self._scroll_area, stretch=1)

        # 覆盖层
        self._overlay = UnifiedBBoxOverlay(self._scroll_area.viewport())

        # 内联文本编辑器
        self._inline_editor = QLineEdit(self._image_label)
        self._inline_editor.setStyleSheet(
            f"QLineEdit {{ background-color: rgba(255,255,255,0.95);"
            f" border: 2px solid {theme.Colors.warning}; border-radius: 4px;"
            f" padding: 2px 6px; font-size: 13px; }}"
        )
        self._inline_editor.setFrame(False)
        self._inline_editor.hide()
        self._inline_editor.editingFinished.connect(self._on_inline_edit_finished)
        self._inline_editor.installEventFilter(self)

        # 事件过滤器用于悬停和点击检测
        self._image_label.setMouseTracking(True)
        self._image_label.installEventFilter(self)

        # 翻页信号
        self._prev_btn.clicked.connect(self._on_prev_page)
        self._next_btn.clicked.connect(self._on_next_page)

    # ── 事件过滤器 ──

    def eventFilter(self, obj, event) -> bool:
        if obj == self._image_label and self._pixmap:
            if event.type() == event.Type.MouseButtonDblClick:
                if event.button() == Qt.MouseButton.LeftButton:
                    self._on_label_double_click(event.pos())
            elif self._text_blocks:
                if event.type() == event.Type.MouseMove:
                    self._on_mouse_move(event.pos())
                elif event.type() == event.Type.MouseButtonPress:
                    if event.button() == Qt.MouseButton.LeftButton:
                        self._on_block_click(event.pos())
        elif obj == self._inline_editor and event.type() == event.Type.KeyPress:
            from PySide6.QtGui import QKeyEvent

            key_event: QKeyEvent = event
            if key_event.key() == Qt.Key.Key_Escape:
                self._cancel_inline_edit()
                return True
        return super().eventFilter(obj, event)

    def _on_mouse_move(self, pos) -> None:
        # 统一悬停键：置信度模式用 text_block 下标，块类型模式用
        # "t:" + content_list 索引，避免两种模式命中互相串扰。
        idx = self._hit_test_block(pos.x(), pos.y())
        if idx >= 0:
            hover_key = idx
        elif self._content_list:
            # 块类型模式回退：表格/公式等结构识别管道左侧在块类型模式渲染，
            # 置信度命中测试恒返回 -1，需用 _hit_test_type_block 命中 content_list。
            cl_idx, _bt = self._hit_test_type_block(pos.x(), pos.y())
            hover_key = f"t:{cl_idx}" if cl_idx >= 0 else -1
        else:
            hover_key = -1

        if hover_key != self._hovered_block:
            self._hovered_block = hover_key
            if idx >= 0:
                # 置信度模式命中
                self._overlay.set_hovered(idx)
                self.block_hovered.emit(idx)
                block = self._text_blocks[idx]
                self._image_label.setToolTip(
                    self._build_block_tooltip(
                        getattr(block, "label", "text"),
                        block.text,
                        block.score,
                        block.is_manually_edited,
                    )
                )
            elif isinstance(hover_key, str) and hover_key.startswith("t:"):
                # 块类型模式命中
                cl_idx = int(hover_key[2:])
                self._overlay.set_hovered(cl_idx)
                self.block_hovered.emit(cl_idx)
                tb_idx = self._find_text_block_by_content_index(cl_idx)
                block = (
                    self._text_blocks[tb_idx]
                    if tb_idx >= 0
                    else None
                )
                if block is not None:
                    self._image_label.setToolTip(
                        self._build_block_tooltip(
                            getattr(block, "label", "text"),
                            block.text,
                            block.score,
                            block.is_manually_edited,
                        )
                    )
                else:
                    # 无对应 text_block（如纯图片块）：用 content_list 元信息
                    cl_block = (
                        self._content_list[cl_idx]
                        if 0 <= cl_idx < len(self._content_list)
                        else {}
                    )
                    self._image_label.setToolTip(
                        self._build_block_tooltip(
                            cl_block.get("type", "text"),
                            cl_block.get("text", ""),
                            None,
                            False,
                        )
                    )
            else:
                self._overlay.set_hovered(-1)
                self.block_unhovered.emit()
                self._image_label.setToolTip("")

    @staticmethod
    def _build_block_tooltip(
        label: str, text: str, score: float | None, is_edited: bool
    ) -> str:
        """构造 bbox 悬停 tooltip。

        表格/图片/公式等结构识别块的 score 是占位值（0.9/1.0），显示为百分比
        会误导（如表格显示"90%"），改为"无置信度"；普通文本块保留真实百分比。
        """
        if label in NO_CONFIDENCE_LABELS or score is None:
            conf_line = "置信度: 无置信度"
        else:
            conf_line = f"置信度: {score:.1%}"
        tooltip = f"{(text or '')[:50]}\n{conf_line}"
        if is_edited:
            tooltip += "\n[手动修改]"
        return tooltip

    def _on_block_click(self, pos) -> None:
        idx = self._hit_test_block(pos.x(), pos.y())
        if idx >= 0:
            self.block_clicked.emit(idx)

    def _on_label_double_click(self, pos) -> None:
        """双击处理：优先 bbox 内联编辑，空白区域打开原图查看器。

        表格块（label/type=="table"）的 text 是原始 HTML，走内联 QLineEdit
        会把标签当纯文本显示，故表格块双击不做内联编辑（请在右侧结果视图
        编辑表格）。
        """
        # 优先置信度模式（单次识别结果）命中
        idx = self._hit_test_block(pos.x(), pos.y())
        if idx >= 0:
            block = self._text_blocks[idx]
            if getattr(block, "label", "") != "table":
                self._start_inline_edit(idx)
            return

        # 回退块类型模式（content_list）
        cl_idx, block_type = self._hit_test_type_block(pos.x(), pos.y())
        if cl_idx >= 0:
            if block_type != "table":
                # 块类型模式下普通文本块：尝试定位到对应 text_block 做内联编辑
                tb_idx = self._find_text_block_by_content_index(cl_idx)
                if tb_idx >= 0:
                    self._start_inline_edit(tb_idx)
            return

        # 未命中任何 bbox → 打开原图查看器
        self._show_original_image()

    def _show_original_image(self) -> None:
        """弹出原图查看对话框。"""
        pm = self._original_pixmap
        if pm is None or pm.isNull():
            return
        dialog = ImageViewerDialog(pm, self)
        dialog.resize(min(pm.width() + 40, 1200), min(pm.height() + 80, 900))
        dialog.exec()

    def _start_inline_edit(self, index: int) -> None:
        if index < 0 or index >= len(self._block_screen_rects):
            return
        bx, by, bw, bh = self._block_screen_rects[index]
        block = self._text_blocks[index]
        self._editing_index = index
        self._inline_editor.setText(block.text)
        self._inline_editor.setGeometry(
            int(bx), int(by), max(int(bw), 120), max(int(bh) + 4, 28)
        )
        self._inline_editor.show()
        self._inline_editor.setFocus()
        self._inline_editor.selectAll()

    def _on_inline_edit_finished(self) -> None:
        if self._editing_index < 0:
            return
        index = self._editing_index
        new_text = self._inline_editor.text()
        old_text = self._text_blocks[index].text
        self._inline_editor.hide()
        self._editing_index = -1
        if new_text != old_text:
            self.block_text_edited.emit(index, new_text)

    def _cancel_inline_edit(self) -> None:
        self._inline_editor.hide()
        self._editing_index = -1

    def _hit_test_block(self, x: int, y: int) -> int:
        for i, (bx, by, bw, bh) in enumerate(self._block_screen_rects):
            if bx <= x <= bx + bw and by <= y <= by + bh:
                return i
        return -1

    def _hit_test_type_block(
        self, x: int, y: int
    ) -> tuple[int, str]:
        """块类型模式命中测试，返回 (content_list 索引, block_type)。

        未命中返回 (-1, "")。用于双击表格块进入网格编辑、或双击普通文本块
        定位到对应 text_block 做内联编辑。
        """
        for cl_idx, rect, block_type in self._type_screen_rects:
            if rect.contains(x, y):
                return cl_idx, block_type
        return -1, ""

    def _find_text_block_by_content_index(self, cl_idx: int) -> int:
        """按 content_index 反查 text_blocks 的下标（用于块类型模式下
        命中普通文本块后复用置信度模式的内联编辑）。"""
        if cl_idx < 0:
            return -1
        for i, b in enumerate(self._text_blocks):
            if getattr(b, "content_index", None) == cl_idx:
                return i
        return -1

    # ── 标签点击（空状态触发截图/文件选择）──

    def _on_label_click(self, event) -> None:
        if self._pixmap is None and self._original_pixmap is None:
            if event.button() == Qt.MouseButton.LeftButton:
                self.screenshot_requested.emit()
            elif event.button() == Qt.MouseButton.RightButton:
                self.file_open_requested.emit()

    def _on_context_menu(self, pos) -> None:
        if self._pixmap is not None or self._original_pixmap is not None:
            return
        menu = QMenu(self._image_label)
        action_screenshot = QAction("截图识别", menu)
        action_open_file = QAction("选择文件（图片/PDF）", menu)
        action_screenshot.triggered.connect(self.screenshot_requested.emit)
        action_open_file.triggered.connect(self.file_open_requested.emit)
        menu.addAction(action_screenshot)
        menu.addAction(action_open_file)
        menu.exec(self._image_label.mapToGlobal(pos))

    # ── 图片设置 ──

    def set_pixmap(self, pixmap: QPixmap) -> None:
        """设置预览图片（截图或打开图片）"""
        self._text_blocks = []
        self._block_screen_rects = []
        self._content_list = []
        self._type_screen_rects = []
        self._hovered_block = -1
        self._highlight_block_index = -1
        self._overlay.clear()

        if pixmap.devicePixelRatio() != 1.0:
            pixmap = QPixmap(pixmap)
            pixmap.setDevicePixelRatio(1.0)
        self._pixmap = pixmap
        self._original_pixmap = pixmap
        self._img_w = pixmap.width()
        self._img_h = pixmap.height()
        self._total_pages = 1
        self._current_page = 0
        self._update_display()
        self._update_nav()
        self.image_changed.emit()

    def pixmap(self) -> QPixmap | None:
        return self._pixmap

    def original_pixmap(self) -> QPixmap | None:
        """返回原始图片（未预处理、未缩放）。

        OCR 预处理可能把 _pixmap（显示用）替换为预处理后图像，
        但 _original_pixmap 始终保留原图，供"复制原图"使用。
        """
        return self._original_pixmap

    def set_text_blocks(self, blocks: list[TextBlock]) -> None:
        """设置文本块用于置信度模式高亮"""
        self._text_blocks = blocks
        self._update_block_overlay()

    # ── 文件加载（PDF/图片）──

    def load_file(self, file_path: str) -> None:
        """从文件路径加载（自动检测 PDF/图片）"""
        self._current_file = file_path
        ext = Path(file_path).suffix.lower()
        self._is_pdf = ext == ".pdf"

        self._text_blocks = []
        self._block_screen_rects = []
        self._content_list = []
        self._type_screen_rects = []
        self._hovered_block = -1
        self._highlight_block_index = -1
        self._overlay.clear()

        if self._is_pdf:
            self._load_pdf(file_path)
        else:
            self._load_image_file(file_path)

    def _load_pdf(self, file_path: str) -> None:
        if self._pdf_doc is not None:
            self._pdf_doc.close()

        self._pdf_doc = QPdfDocument(self)
        error = self._pdf_doc.load(file_path)
        if error != QPdfDocument.Error.None_:
            self._image_label.setText(f"无法加载 PDF: {file_path}")
            self._total_pages = 0
            self._update_nav()
            return

        self._total_pages = self._pdf_doc.pageCount()
        self._current_page = 0
        self._render_current_page()
        self._update_nav()

    def _load_image_file(self, file_path: str) -> None:
        self._total_pages = 1
        self._current_page = 0
        self._original_pixmap = QPixmap(file_path)

        if self._original_pixmap.isNull():
            self._image_label.setText(f"无法加载图片: {file_path}")
        else:
            self._pixmap = self._original_pixmap
            self._update_display()

        self._update_nav()

    def _render_current_page(self) -> None:
        if self._pdf_doc is None:
            return
        if self._current_page >= self._pdf_doc.pageCount():
            return

        page_size = self._pdf_doc.pagePointSize(self._current_page)
        scale = 2.0
        render_size = (page_size * scale).toSize()
        qimage = self._pdf_doc.render(self._current_page, render_size)
        self._original_pixmap = QPixmap.fromImage(qimage)
        self._pixmap = self._original_pixmap
        self._update_display()

    # ── 翻页 ──

    def _on_prev_page(self) -> None:
        if self._current_page > 0:
            self._current_page -= 1
            if self._is_pdf:
                self._render_current_page()
            self._update_nav()
            self._reapply_highlight()

    def _on_next_page(self) -> None:
        if self._current_page < self._total_pages - 1:
            self._current_page += 1
            if self._is_pdf:
                self._render_current_page()
            self._update_nav()
            self._reapply_highlight()

    def _update_nav(self) -> None:
        has_pages = self._total_pages > 1
        self._prev_btn.setEnabled(has_pages and self._current_page > 0)
        self._next_btn.setEnabled(
            has_pages and self._current_page < self._total_pages - 1
        )
        self._page_label.setText(f"{self._current_page + 1} / {self._total_pages}")
        self._nav_bar.setVisible(self._total_pages > 0)

    def current_page(self) -> int:
        return self._current_page

    def page_count(self) -> int:
        return self._total_pages

    # ── content_list 和块类型着色 ──

    def set_content_list(self, content_list: list[dict]) -> None:
        """设置 content_list 用于块类型着色覆盖"""
        self._content_list = content_list
        self._update_type_overlay()

    def _update_type_overlay(self) -> None:
        """绘制所有 content_list 块的 bbox 覆盖层"""
        if not self._content_list or self._original_pixmap is None:
            self._overlay.set_type_blocks([])
            return

        disp_w, disp_h, offset_x, offset_y = self._compute_scale_factor()
        if disp_w <= 0 or disp_h <= 0:
            self._overlay.set_type_blocks([])
            return

        overlay_rects = []
        type_screen_rects: list[tuple[int, QRectF, str]] = []
        for i, block in enumerate(self._content_list):
            if block.get("type", "") in DISCARDED_BLOCK_TYPES:
                continue
            page_idx = block.get("page_idx", 0)
            if page_idx != self._current_page:
                continue
            bbox = block.get("bbox")
            if not bbox or len(bbox) < 4:
                continue
            block_type = block.get("type", "text")
            # MinerU content_list v1: titles are {"type": "text", "text_level": N}
            if block_type == "text" and "text_level" in block:
                block_type = "title"
            fill_color = BLOCK_COLORS.get(block_type, BLOCK_COLORS["text"])
            border_color = BLOCK_BORDER_COLORS.get(
                block_type, BLOCK_BORDER_COLORS["text"]
            )
            screen_rect = QRectF(
                bbox[0] / BBOX_NORM * disp_w + offset_x,
                bbox[1] / BBOX_NORM * disp_h + offset_y,
                (bbox[2] - bbox[0]) / BBOX_NORM * disp_w,
                (bbox[3] - bbox[1]) / BBOX_NORM * disp_h,
            )
            overlay_rects.append(
                (
                    i,
                    screen_rect,
                    block_type,
                    fill_color,
                    border_color,
                    block.get("confidence"),
                )
            )
            # 同步记录命中矩形，供块类型模式下的双击编辑命中测试使用
            type_screen_rects.append((i, screen_rect, block_type))

        self._type_screen_rects = type_screen_rects
        self._overlay.set_type_blocks(overlay_rects)
        self._overlay.setGeometry(self._scroll_area.viewport().rect())

    # ── 高亮 ──

    def highlight_block(self, index: int) -> None:
        """高亮指定块（同时支持置信度模式和块类型模式）"""
        # 块类型模式：查找 content_list 中的 bbox，翻页到对应页
        if self._content_list and 0 <= index < len(self._content_list):
            block = self._content_list[index]
            bbox = block.get("bbox")
            if not bbox or len(bbox) < 4:
                self._overlay.set_hovered(-1)
                return
            page_idx = block.get("page_idx", 0)
            if page_idx != self._current_page:
                self._current_page = page_idx
                if self._is_pdf:
                    self._render_current_page()
                self._update_nav()
                self._update_type_overlay()
            self._highlight_block_index = index
            self._overlay.set_hovered(index)
            return

        # 置信度模式：直接设置 overlay hovered index
        self._overlay.set_hovered(index)

    def clear_highlight(self) -> None:
        """清除悬停高亮（保留永久覆盖层）"""
        self._overlay.set_hovered(-1)
        self._highlight_block_index = -1

    def _reapply_highlight(self) -> None:
        """翻页后重新应用高亮和全块覆盖"""
        if self._content_list:
            self._update_type_overlay()
        else:
            self._update_block_overlay()
        if self._highlight_block_index >= 0:
            self.highlight_block(self._highlight_block_index)

    # ── 清除 ──

    def clear(self) -> None:
        """清除图片"""
        self._pixmap = None
        self._original_pixmap = None
        self._text_blocks = []
        self._block_screen_rects = []
        self._content_list = []
        self._type_screen_rects = []
        self._hovered_block = -1
        self._highlight_block_index = -1
        self._overlay.clear()
        self._image_label.clear()
        self._image_label.setText(self._empty_text)
        self._image_label.setStyleSheet(
            f"QLabel {{ background-color: {theme.Colors.surface_alt};"
            f" border: 2px dashed {theme.Colors.border}; }}"
        )
        if self._pdf_doc is not None:
            self._pdf_doc.close()
            self._pdf_doc = None
        self._current_file = ""
        self._total_pages = 0
        self._current_page = 0
        self._update_nav()
        self.image_changed.emit()

    # ── 显示更新 ──

    def _compute_scale_factor(self) -> tuple[float, float, float, float]:
        """基于 _original_pixmap 和 label 尺寸计算显示区域和偏移

        不依赖已设置的 scaled pixmap，消除时序问题。

        Returns: (disp_w, disp_h, offset_x, offset_y)
        """
        if not self._original_pixmap or self._original_pixmap.isNull():
            return 0, 0, 0, 0
        img_w = self._original_pixmap.width()
        img_h = self._original_pixmap.height()
        label_w = self._image_label.width()
        label_h = self._image_label.height()
        if label_w <= 0 or label_h <= 0 or img_w <= 0 or img_h <= 0:
            return 0, 0, 0, 0
        max_w = label_w - 20
        max_h = label_h - 20
        if max_w <= 0 or max_h <= 0:
            return 0, 0, 0, 0
        scale = min(max_w / img_w, max_h / img_h)
        disp_w = img_w * scale
        disp_h = img_h * scale
        offset_x = (label_w - disp_w) / 2
        offset_y = (label_h - disp_h) / 2
        return disp_w, disp_h, offset_x, offset_y

    def _update_display(self) -> None:
        if self._pixmap:
            viewport = self._scroll_area.viewport()
            dpr = self.devicePixelRatio()
            max_w = max(viewport.width() - 20, 200)
            max_h = max(viewport.height() - 20, 200)

            scaled = self._pixmap.scaled(
                int(max_w * dpr),
                int(max_h * dpr),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            scaled.setDevicePixelRatio(dpr)
            self._image_label.setPixmap(scaled)
            self._image_label.setStyleSheet(
                f"QLabel {{ background-color: {theme.Colors.surface};"
                f" border: 1px solid {theme.Colors.border}; }}"
            )
            QTimer.singleShot(0, self._update_overlay_deferred)

    def _update_overlay_deferred(self) -> None:
        """延迟一帧更新 overlay，确保布局已完成"""
        if self._content_list:
            self._update_type_overlay()
        elif self._text_blocks:
            self._update_block_overlay()
        self._overlay.setGeometry(self._scroll_area.viewport().rect())

    def _update_block_overlay(self) -> None:
        """根据当前文本块和图片显示计算置信度模式覆盖矩形"""
        self._overlay.clear()
        self._block_screen_rects.clear()
        self._type_screen_rects = []

        if not self._pixmap or not self._text_blocks:
            return

        disp_w, disp_h, offset_x, offset_y = self._compute_scale_factor()
        if disp_w <= 0 or disp_h <= 0:
            return

        overlay_rects = []
        for block in self._text_blocks:
            if block.bbox is None:
                self._block_screen_rects.append((0, 0, 0, 0))
                continue
            x0, y0, x1, y1 = block.bbox
            sx = x0 / 1000.0 * disp_w + offset_x
            sy = y0 / 1000.0 * disp_h + offset_y
            sw = (x1 - x0) / 1000.0 * disp_w
            sh = (y1 - y0) / 1000.0 * disp_h
            self._block_screen_rects.append((sx, sy, sw, sh))
            overlay_rects.append(
                (sx, sy, sw, sh, block.score, block.text, block.is_manually_edited)
            )

        self._overlay.set_confidence_blocks(overlay_rects)
        self._overlay.setGeometry(self._scroll_area.viewport().rect())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._original_pixmap and not self._original_pixmap.isNull():
            self._update_display()
            self._reapply_highlight()
        QTimer.singleShot(
            0, lambda: self._overlay.setGeometry(self._scroll_area.viewport().rect())
        )
