"""识别结果显示组件

结构化渲染 OCR 结果，支持块类型样式区分和悬停高亮。
"""

import logging

from PySide6.QtCore import QEvent, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QTextBrowser, QVBoxLayout, QWidget

from vibeocr.utils.markdown_converter import HTML_STYLE

logger = logging.getLogger(__name__)

# 块类型 -> CSS 左边框颜色
BLOCK_BORDER_CSS = {
    "text": "#3b82f6",
    "title": "#ef4444",
    "table": "#22c55e",
    "image": "#a855f7",
    "figure": "#a855f7",
    "equation": "#f97316",
    "interline_equation": "#f97316",
    "inline_equation": "#f97316",
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

# 高亮行样式
HIGHLIGHT_STYLE = "background-color: #fef08a; border-left: 3px solid #eab308;"


def _build_block_html(block: dict, index: int) -> str:
    """将单个 content_list 条目渲染为 HTML 块"""
    block_type = block.get("type", "text")
    text = block.get("text", "")
    html = block.get("html", "")
    border_color = BLOCK_BORDER_CSS.get(block_type, "#3b82f6")
    type_label = BLOCK_TYPE_LABELS.get(block_type, block_type)

    # 块类型标签
    label_html = (
        f'<span style="font-size:11px; color:#888; margin-right:6px;">'
        f"[{type_label}]</span>"
    )

    # 置信度标签（如有）
    confidence = block.get("confidence")
    conf_html = ""
    if confidence is not None:
        pct = f"{confidence * 100:.1f}%"
        conf_html = (
            f'<span style="font-size:11px; color:#888; margin-left:6px;">'
            f"{pct}</span>"
        )

    content_html = ""
    if block_type == "table" and html:
        content_html = html
    elif block_type in ("image", "figure"):
        img_idx = block.get("img_idx")
        if img_idx is not None:
            content_html = f'<p style="color:#888;">[图片 #{img_idx}]</p>'
        elif text:
            content_html = f"<p>{text}</p>"
    elif block_type in ("equation", "interline_equation", "inline_equation"):
        escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        content_html = (
            f'<div style="background-color:#f8f9fa; padding:8px 12px; '
            f'border-radius:4px; font-family:Consolas,Monaco,monospace; '
            f'font-size:13px; border-left:3px solid #0078d4;">'
            f"{escaped}</div>"
        )
    elif block_type == "title":
        level = block.get("level", 1)
        tag = f"h{min(level, 6)}"
        content_html = f"<{tag}>{text}</{tag}>"
    else:
        if text:
            content_html = f"<p>{text}</p>"

    return (
        f'<div class="ocr-block" data-block-index="{index}" '
        f'data-block-type="{block_type}" id="block-{index}" '
        f'style="padding:4px 8px; border-left:3px solid {border_color}; '
        f"margin:2px 0; border-radius:2px; cursor:pointer;\" "
        f"onmouseover=\"this.style.backgroundColor='#f0f9ff'\" "
        f"onmouseout=\"this.style.backgroundColor=''\">"
        f"{label_html}{content_html}{conf_html}"
        f"</div>"
    )


def _build_result_html(result) -> str:
    """从 OCRResult 构建完整 HTML"""
    content_list = getattr(result, "content_list", [])

    if content_list:
        blocks_html = []
        for i, block in enumerate(content_list):
            blocks_html.append(_build_block_html(block, i))
        body = "\n".join(blocks_html)
    elif result.has_rich_content:
        body = result.html_text
    else:
        escaped = (
            result.raw_text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br>")
        )
        body = f"<pre style='white-space:pre-wrap;'>{escaped}</pre>"

    return (
        f"{HTML_STYLE}"
        f"<body style='padding:8px;'>"
        f"{body}"
        f"</body>"
    )


class ResultViewWidget(QWidget):
    """识别结果显示组件

    结构化渲染 content_list，支持悬停检测和块高亮。
    """

    block_hovered = Signal(int)   # content_list 索引
    block_unhovered = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._content_list: list[dict] = []
        self._current_result = None
        self._highlighted_index: int = -1
        self._block_positions: dict[int, tuple[int, int]] = {}  # index -> (start, end) char pos

        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(False)
        self._browser.setPlaceholderText("识别结果将显示在这里...")
        self._browser.setStyleSheet(
            "QTextBrowser { border: none; background: white; }"
        )

        layout.addWidget(self._browser)

        # 事件过滤器用于悬停检测
        self._browser.viewport().installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        if obj == self._browser.viewport():
            if event.type() == QEvent.Type.MouseMove:
                self._on_mouse_move(event.pos())
            elif event.type() == QEvent.Type.Leave:
                self._on_mouse_leave()
        return super().eventFilter(obj, event)

    def _on_mouse_move(self, pos) -> None:
        """鼠标移动时检测所在块"""
        cursor = self._browser.cursorForPosition(pos)
        char_pos = cursor.position()

        # 查找对应的块索引
        for idx, (start, end) in self._block_positions.items():
            if start <= char_pos <= end:
                if idx != self._highlighted_index:
                    self.block_hovered.emit(idx)
                return

        # 不在任何块上
        if self._highlighted_index >= 0:
            self.block_unhovered.emit()

    def _on_mouse_leave(self) -> None:
        if self._highlighted_index >= 0:
            self.block_unhovered.emit()

    def display_result(self, result) -> None:
        """显示 OCR 结果"""
        self._current_result = result
        self._content_list = getattr(result, "content_list", [])
        self._highlighted_index = -1
        self._block_positions.clear()

        html = _build_result_html(result)
        self._browser.setHtml(html)

        # 构建块位置映射
        if self._content_list:
            self._build_block_positions()

    def _build_block_positions(self) -> None:
        """构建块索引到文档字符位置的映射"""
        self._block_positions.clear()

        for i in range(len(self._content_list)):
            cursor = self._browser.document().find(f'id="block-{i}"')
            if not cursor.isNull():
                block = cursor.block()
                block_start = block.position()
                block_end = block.position() + block.length()
                self._block_positions[i] = (block_start, block_end)

    def highlight_block(self, index: int) -> None:
        """高亮指定块"""
        if index == self._highlighted_index:
            return

        self.clear_highlight()
        self._highlighted_index = index

        if index < 0 or index >= len(self._content_list):
            return

        # 滚动到对应块
        anchor = f"block-{index}"
        self._browser.scrollToAnchor(anchor)

        # 应用额外高亮样式（通过 QTextCursor）
        if index in self._block_positions:
            start, end = self._block_positions[index]
            cursor = self._browser.textCursor()
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)

            # 使用 extra selection 方式高亮（不修改文档内容）
            from PySide6.QtGui import QTextCharFormat, QColor

            fmt = QTextCharFormat()
            fmt.setBackground(QColor(254, 240, 138))  # 黄色高亮
            cursor.mergeCharFormat(fmt)

    def clear_highlight(self) -> None:
        """清除高亮"""
        if self._highlighted_index < 0:
            return

        # 需要重新设置文档来清除格式
        if self._highlighted_index in self._block_positions:
            # 重新渲染以清除高亮格式
            if self._current_result:
                html = _build_result_html(self._current_result)
                self._browser.setHtml(html)
                self._build_block_positions()

        self._highlighted_index = -1

    def clear(self) -> None:
        """清空内容"""
        self._browser.clear()
        self._content_list = []
        self._current_result = None
        self._highlighted_index = -1
        self._block_positions.clear()

    def get_result(self):
        """获取当前显示的结果"""
        return self._current_result
