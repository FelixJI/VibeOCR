"""剪贴板控制器

处理复制操作的逻辑，包括富文本、Markdown 和纯文本格式。
"""

import logging
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel, QWidget

if TYPE_CHECKING:
    from vibeocr.models.ocr_result import OCRResult

logger = logging.getLogger(__name__)


class ClipboardController:
    """剪贴板控制器

    处理 OCR 结果的复制操作，支持多种格式。

    Usage:
        controller = ClipboardController(
            status_callback=statusbar.showMessage,
            copy_button=ui.btnCopyRich,
        )
        controller.copy_rich(ocr_result)
    """

    def __init__(
        self,
        status_callback,
        copy_button: QWidget,
    ) -> None:
        self._status_callback = status_callback
        self._copy_button = copy_button
        self._current_result: OCRResult | None = None

        # 创建复制成功提示标签
        self._copy_toast = QLabel("已复制到剪贴板", copy_button)
        self._copy_toast.setStyleSheet("""
            QLabel {
                background-color: #333333;
                color: white;
                padding: 6px 12px;
                border-radius: 4px;
                font-size: 12px;
            }
        """)
        self._copy_toast.hide()

    def set_result(self, result: Optional["OCRResult"]) -> None:
        """设置当前 OCR 结果"""
        self._current_result = result

    def copy_rich(self) -> None:
        """复制为富文本格式（支持 Word/Excel 的 CF_HTML 格式）"""
        if not self._current_result:
            return

        clipboard = QApplication.clipboard()
        mime_data = clipboard.mimeData()

        if self._current_result.has_rich_content:
            html_content = self._current_result.html_text

            # 设置标准 HTML 格式
            mime_data.setHtml(html_content)

            # 设置 CF_HTML 格式（Microsoft Office 专用）
            cf_html = self._create_cf_html(html_content)
            mime_data.setData("HTML Format", cf_html.encode("utf-8"))

            # 同时设置纯文本（作为备选）
            mime_data.setText(self._current_result.markdown_text)

            clipboard.setMimeData(mime_data)
            self._status_callback("已复制富文本到剪贴板")
        else:
            # 没有富文本，复制纯文本
            clipboard.setText(self._current_result.raw_text)
            self._status_callback("已复制纯文本到剪贴板")

        self._show_copy_toast()

    def copy_markdown(self) -> None:
        """复制为 Markdown 格式"""
        if not self._current_result:
            return

        clipboard = QApplication.clipboard()
        clipboard.setText(self._current_result.markdown_text)
        self._status_callback("已复制 Markdown 到剪贴板")
        self._show_copy_toast()

    def copy_plain(self) -> None:
        """复制为纯文本格式"""
        if not self._current_result:
            return

        clipboard = QApplication.clipboard()
        clipboard.setText(self._current_result.raw_text)
        self._status_callback("已复制纯文本到剪贴板")
        self._show_copy_toast()

    def _create_cf_html(self, html_fragment: str) -> str:
        """创建 CF_HTML 格式的剪贴板内容

        CF_HTML 是 Microsoft Office 使用的剪贴板格式，
        需要包含特殊的头部结构和字节偏移量。

        Args:
            html_fragment: HTML 片段内容

        Returns:
            CF_HTML 格式的完整字符串
        """
        # 构建 HTML 上下文
        html_template = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
<!--StartFragment-->{}<!--EndFragment-->
</body>
</html>"""

        full_html = html_template.format(html_fragment)

        # 计算偏移量（使用 UTF-8 字节计数）
        # 头部占位符长度（偏移量使用 10 位数字）
        header_template = (
            "Version:0.9\r\n"
            "StartHTML:0000000000\r\n"
            "EndHTML:0000000000\r\n"
            "StartFragment:0000000000\r\n"
            "EndFragment:0000000000\r\n"
        )

        # 头部实际长度
        header_len = len(header_template.encode("utf-8"))

        # 计算 StartFragment 位置（头部 + <!--StartFragment--> 之前的内容）
        start_fragment_marker = "<!--StartFragment-->"
        end_fragment_marker = "<!--EndFragment-->"
        start_fragment_pos = full_html.find(start_fragment_marker)
        end_fragment_pos = full_html.find(end_fragment_marker)

        # 字节偏移
        start_fragment_byte = header_len + len(
            full_html[: start_fragment_pos + len(start_fragment_marker)].encode("utf-8")
        )
        end_fragment_byte = header_len + len(
            full_html[:end_fragment_pos].encode("utf-8")
        )
        end_html_byte = header_len + len(full_html.encode("utf-8"))

        # 格式化偏移量（10 位数字）
        cf_html = (
            f"Version:0.9\r\n"
            f"StartHTML:{header_len:010d}\r\n"
            f"EndHTML:{end_html_byte:010d}\r\n"
            f"StartFragment:{start_fragment_byte:010d}\r\n"
            f"EndFragment:{end_fragment_byte:010d}\r\n"
            f"{full_html}"
        )

        return cf_html

    def _show_copy_toast(self) -> None:
        """显示复制成功提示"""
        # 调整提示标签位置（按钮上方居中）
        toast = self._copy_toast
        toast.adjustSize()
        x = (self._copy_button.width() - toast.width()) // 2
        y = -toast.height() - 8
        toast.move(x, y)
        toast.show()
        # 1.5秒后自动隐藏
        QTimer.singleShot(1500, toast.hide)
