"""文件预览组件

显示 PDF 或图片文件，支持 BBox 高亮覆盖层。
"""

import logging
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

# BBox 坐标归一化范围
BBOX_NORM = 1000.0

# 块类型 -> 高亮颜色
BLOCK_COLORS = {
    "text": QColor(59, 130, 246, 80),       # 蓝色
    "title": QColor(239, 68, 68, 80),       # 红色
    "table": QColor(34, 197, 94, 80),       # 绿色
    "image": QColor(168, 85, 247, 80),      # 紫色
    "figure": QColor(168, 85, 247, 80),
    "equation": QColor(249, 115, 22, 80),   # 橙色
    "interline_equation": QColor(249, 115, 22, 80),
    "inline_equation": QColor(249, 115, 22, 80),
}

BLOCK_BORDER_COLORS = {
    "text": QColor(59, 130, 246, 200),
    "title": QColor(239, 68, 68, 200),
    "table": QColor(34, 197, 94, 200),
    "image": QColor(168, 85, 247, 200),
    "figure": QColor(168, 85, 247, 200),
    "equation": QColor(249, 115, 22, 200),
    "interline_equation": QColor(249, 115, 22, 200),
    "inline_equation": QColor(249, 115, 22, 200),
}

BLOCK_TYPE_LABELS = {
    "text": "文本",
    "title": "标题",
    "table": "表格",
    "image": "图片",
    "figure": "图片",
    "equation": "公式",
    "interline_equation": "公式",
    "inline_equation": "公式",
}


class BBoxOverlay(QWidget):
    """透明覆盖层，绘制 BBox 高亮矩形"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._rects: list[tuple[QRectF, str, QColor, QColor]] = []
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def set_highlight(
        self,
        rect: QRectF,
        block_type: str = "text",
    ) -> None:
        fill_color = BLOCK_COLORS.get(block_type, BLOCK_COLORS["text"])
        border_color = BLOCK_BORDER_COLORS.get(block_type, BLOCK_BORDER_COLORS["text"])
        self._rects = [(rect, block_type, fill_color, border_color)]
        self.update()

    def clear(self) -> None:
        self._rects.clear()
        self.update()

    def paintEvent(self, event) -> None:
        if not self._rects:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        for rect, block_type, fill_color, border_color in self._rects:
            # 填充
            painter.fillRect(rect, fill_color)
            # 边框
            pen = QPen(border_color, 2)
            painter.setPen(pen)
            painter.drawRect(rect)
            # 类型标签
            label = BLOCK_TYPE_LABELS.get(block_type, block_type)
            label_rect = QRectF(rect.topLeft(), rect.topLeft() + QPointF(60, 20))
            painter.fillRect(label_rect, border_color)
            painter.setPen(QPen(QColor(255, 255, 255)))
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, label)

        painter.end()


class FilePreviewWidget(QWidget):
    """文件预览组件

    支持 PDF 和图片文件预览，BBox 高亮覆盖，翻页导航。
    """

    block_hovered = Signal(int)   # content_list 索引
    block_unhovered = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._content_list: list[dict] = []
        self._current_file: str = ""
        self._is_pdf = False

        # PDF
        self._pdf_doc: QPdfDocument | None = None
        self._current_page = 0
        self._total_pages = 0

        # 图片
        self._original_pixmap: QPixmap | None = None

        # 高亮覆盖
        self._highlight_block_index: int = -1

        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(0, 0, 0, 0)

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
        self._scroll_area.setWidget(self._image_label)

        layout.addWidget(self._scroll_area, stretch=1)

        # 高亮覆盖层
        self._overlay = BBoxOverlay(self._scroll_area.viewport())

        # 占位文本
        self._image_label.setText("选择文件以预览")

        self._connect_signals()

    def _connect_signals(self) -> None:
        self._prev_btn.clicked.connect(self._on_prev_page)
        self._next_btn.clicked.connect(self._on_next_page)

    def load_file(self, file_path: str) -> None:
        """加载文件（自动检测 PDF/图片）"""
        self._current_file = file_path
        ext = Path(file_path).suffix.lower()
        self._is_pdf = ext == ".pdf"

        self._overlay.clear()
        self._highlight_block_index = -1

        if self._is_pdf:
            self._load_pdf(file_path)
        else:
            self._load_image(file_path)

    def _load_pdf(self, file_path: str) -> None:
        """加载 PDF 文件"""
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

    def _load_image(self, file_path: str) -> None:
        """加载图片文件"""
        self._total_pages = 1
        self._current_page = 0
        self._original_pixmap = QPixmap(file_path)

        if self._original_pixmap.isNull():
            self._image_label.setText(f"无法加载图片: {file_path}")
        else:
            self._update_image_display()

        self._update_nav()

    def _render_current_page(self) -> None:
        """渲染当前 PDF 页面"""
        if self._pdf_doc is None:
            return

        if self._current_page >= self._pdf_doc.pageCount():
            return

        page_size = self._pdf_doc.pagePointSize(self._current_page)
        scale = 2.0
        render_size = (page_size * scale).toSize()
        qimage = self._pdf_doc.render(self._current_page, render_size)
        self._original_pixmap = QPixmap.fromImage(qimage)
        self._update_image_display()

    def _update_image_display(self) -> None:
        """更新图片显示（适应大小）"""
        if self._original_pixmap is None or self._original_pixmap.isNull():
            return

        viewport = self._scroll_area.viewport()
        max_w = max(viewport.width() - 20, 200)
        max_h = max(viewport.height() - 20, 200)

        scaled = self._original_pixmap.scaled(
            max_w, max_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._image_label.setPixmap(scaled)

    def _update_nav(self) -> None:
        """更新翻页导航状态"""
        has_pages = self._total_pages > 1
        self._prev_btn.setEnabled(has_pages and self._current_page > 0)
        self._next_btn.setEnabled(
            has_pages and self._current_page < self._total_pages - 1
        )
        self._page_label.setText(f"{self._current_page + 1} / {self._total_pages}")
        self._nav_bar.setVisible(self._total_pages > 0)

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

    def set_content_list(self, content_list: list[dict]) -> None:
        """设置内容列表（用于高亮映射）"""
        self._content_list = content_list

    def highlight_block(self, index: int) -> None:
        """高亮指定块（根据 content_list 索引）"""
        if not self._content_list or index < 0 or index >= len(self._content_list):
            self._overlay.clear()
            self._highlight_block_index = -1
            return

        block = self._content_list[index]
        bbox = block.get("bbox")
        if not bbox or len(bbox) < 4:
            self._overlay.clear()
            return

        # 如果块在不同页，翻页
        page_idx = block.get("page_idx", 0)
        if page_idx != self._current_page:
            self._current_page = page_idx
            if self._is_pdf:
                self._render_current_page()
            self._update_nav()

        self._highlight_block_index = index
        self._apply_highlight(bbox, block.get("type", "text"))

    def _apply_highlight(self, bbox: list, block_type: str) -> None:
        """应用高亮覆盖到当前显示"""
        if self._original_pixmap is None:
            return

        # 获取当前显示的 pixmap 尺寸
        current_pixmap = self._image_label.pixmap()
        if current_pixmap is None:
            return

        # 映射 bbox [0-1000] 到当前 pixmap 显示尺寸
        disp_w = current_pixmap.width()
        disp_h = current_pixmap.height()

        # 原始文档尺寸 vs 归一化 [0-1000]
        orig_w = self._original_pixmap.width()
        orig_h = self._original_pixmap.height()

        # bbox 归一化到原始尺寸
        x0 = bbox[0] / BBOX_NORM * orig_w
        y0 = bbox[1] / BBOX_NORM * orig_h
        x1 = bbox[2] / BBOX_NORM * orig_w
        y1 = bbox[3] / BBOX_NORM * orig_h

        # 缩放到当前显示尺寸
        scale_x = disp_w / orig_w
        scale_y = disp_h / orig_h

        screen_rect = QRectF(
            x0 * scale_x, y0 * scale_y,
            (x1 - x0) * scale_x, (y1 - y0) * scale_y,
        )

        # 考虑 label 对齐偏移
        label_w = self._image_label.width()
        label_h = self._image_label.height()
        offset_x = (label_w - disp_w) / 2
        offset_y = (label_h - disp_h) / 2
        screen_rect.translate(offset_x, offset_y)

        self._overlay.set_highlight(screen_rect, block_type)

    def _reapply_highlight(self) -> None:
        """翻页后重新应用高亮"""
        if self._highlight_block_index >= 0:
            self.highlight_block(self._highlight_block_index)
        else:
            self._overlay.clear()

    def clear_highlight(self) -> None:
        """清除高亮"""
        self._overlay.clear()
        self._highlight_block_index = -1

    def current_page(self) -> int:
        return self._current_page

    def page_count(self) -> int:
        return self._total_pages

    def clear_preview(self) -> None:
        """清空预览"""
        self._image_label.clear()
        self._image_label.setText("选择文件以预览")
        self._overlay.clear()
        self._content_list = []
        self._current_file = ""
        self._highlight_block_index = -1
        if self._pdf_doc is not None:
            self._pdf_doc.close()
            self._pdf_doc = None
        self._original_pixmap = None
        self._total_pages = 0
        self._current_page = 0
        self._update_nav()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._original_pixmap and not self._original_pixmap.isNull():
            self._update_image_display()
            self._reapply_highlight()
        self._overlay.setGeometry(self._scroll_area.viewport().rect())
