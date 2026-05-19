"""识别结果显示组件

使用 QWebEngineView 渲染结构化 OCR 结果，支持：
- 块类型注册表渲染（text/table/image/equation/list/code/chart）
- KaTeX 离线公式渲染
- 图片 data URI 内嵌显示
- QWebChannel 双向高亮通信
"""

from __future__ import annotations

import base64
import html as html_lib
import json
import logging
from pathlib import Path
from typing import Any, Callable

from vibeocr.models.ocr_result import DISCARDED_BLOCK_TYPES

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QVBoxLayout, QWidget

logger = logging.getLogger(__name__)

_RESOURCES_DIR = Path(__file__).resolve().parent.parent.parent / "resources"

# 块类型 → CSS 左边框颜色
BLOCK_BORDER_COLORS: dict[str, str] = {
    "text": "#3b82f6",
    "title": "#ef4444",
    "table": "#22c55e",
    "image": "#a855f7",
    "figure": "#a855f7",
    "chart": "#a855f7",
    "equation": "#f97316",
    "interline_equation": "#f97316",
    "inline_equation": "#f97316",
    "list": "#06b6d4",
    "code": "#8b5cf6",
    "seal": "#6b7280",
}

BLOCK_TYPE_LABELS: dict[str, str] = {
    "text": "文本",
    "title": "标题",
    "table": "表格",
    "image": "图片",
    "figure": "图片",
    "chart": "图表",
    "equation": "公式",
    "interline_equation": "公式",
    "inline_equation": "公式",
    "list": "列表",
    "code": "代码",
    "seal": "印章",
}

# 存储当前结果的 images 字典，供渲染函数访问
_current_images: dict[str, bytes] = {}


# ── 块类型渲染函数 ──────────────────────────────────────────

def _render_text(block: dict, index: int) -> str:
    text = html_lib.escape(block.get("text", ""))
    return f"<p>{text}</p>"


def _render_title(block: dict, index: int) -> str:
    level = min(block.get("text_level", block.get("level", 1)), 6)
    text = html_lib.escape(block.get("text", ""))
    return f"<h{level}>{text}</h{level}>"


def _render_table(block: dict, index: int) -> str:
    parts: list[str] = []
    captions = block.get("table_caption") or []
    if captions:
        parts.append(f'<p style="color:#888;font-size:12px;">{html_lib.escape(captions[0])}</p>')
    table_body = block.get("table_body", "")
    html_content = block.get("html", "")
    if table_body:
        parts.append(f'<div class="ocr-table">{table_body}</div>')
    elif html_content:
        parts.append(f'<div class="ocr-table">{html_content}</div>')
    else:
        text = html_lib.escape(block.get("text", ""))
        parts.append(f"<p>{text}</p>")
    footnotes = block.get("table_footnote") or []
    if footnotes:
        parts.append(f'<p style="color:#888;font-size:11px;">{html_lib.escape(footnotes[0])}</p>')
    return "\n".join(parts)


def _render_image(block: dict, index: int) -> str:
    parts: list[str] = []
    img_path = block.get("img_path", "")
    if img_path and img_path in _current_images:
        img_bytes = _current_images[img_path]
        b64 = base64.b64encode(img_bytes).decode()
        ext = img_path.rsplit(".", 1)[-1].lower()
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(ext, "image/png")
        parts.append(f'<img src="data:{mime};base64,{b64}" style="max-width:100%;border-radius:4px;">')
    else:
        img_idx = block.get("img_idx")
        if img_idx is not None:
            parts.append(f'<p style="color:#888;">[图片 #{img_idx}]</p>')
        else:
            text = html_lib.escape(block.get("text", ""))
            parts.append(f"<p>[图片] {text}</p>" if text else '<p style="color:#888;">[图片]</p>')
    captions = block.get("image_caption") or []
    if captions:
        parts.append(f'<p style="color:#888;font-size:12px;">{html_lib.escape(captions[0])}</p>')
    return "\n".join(parts)


def _render_chart(block: dict, index: int) -> str:
    parts: list[str] = []
    img_path = block.get("img_path", "")
    if img_path and img_path in _current_images:
        img_bytes = _current_images[img_path]
        b64 = base64.b64encode(img_bytes).decode()
        parts.append(f'<img src="data:image/png;base64,{b64}" style="max-width:100%;border-radius:4px;">')
    content = block.get("content", "")
    if content:
        parts.append(f'<p style="color:#555;font-size:13px;">{html_lib.escape(content)}</p>')
    if not parts:
        parts.append('<p style="color:#888;">[图表]</p>')
    return "\n".join(parts)


def _render_equation(block: dict, index: int) -> str:
    latex = html_lib.escape(block.get("text", ""))
    return (
        f'<div class="math-block" data-latex="{latex}" '
        f'style="background:#f8f9fa;padding:8px 12px;border-radius:4px;'
        f'font-family:Consolas,Monaco,monospace;font-size:13px;'
        f'border-left:3px solid #0078d4;">'
        f"{latex}</div>"
    )


def _render_list(block: dict, index: int) -> str:
    items = block.get("list_items", [])
    li_html = "".join(f"<li>{html_lib.escape(item)}</li>" for item in items)
    return f'<ul style="padding-left:20px;">{li_html}</ul>'


def _render_code(block: dict, index: int) -> str:
    body = html_lib.escape(block.get("code_body", ""))
    sub = block.get("sub_type", "")
    lang_label = f'<span style="color:#888;font-size:11px;">[{html_lib.escape(sub)}]</span>' if sub else ""
    return (
        f"{lang_label}"
        f'<pre style="background:#1e1e1e;color:#d4d4d4;padding:12px;border-radius:4px;'
        f'overflow-x:auto;font-size:13px;"><code>{body}</code></pre>'
    )


def _render_seal(block: dict, index: int) -> str:
    img_path = block.get("img_path", "")
    if img_path and img_path in _current_images:
        img_bytes = _current_images[img_path]
        b64 = base64.b64encode(img_bytes).decode()
        return f'<img src="data:image/png;base64,{b64}" style="max-width:60%;border-radius:4px;">'
    return '<p style="color:#888;font-size:12px;">[印章]</p>'


def _render_fallback(block: dict, index: int) -> str:
    text = html_lib.escape(block.get("text", ""))
    return f"<p>{text}</p>" if text else ""


# 块类型注册表
BLOCK_RENDERERS: dict[str, Callable[[dict, int], str]] = {
    "text": _render_text,
    "table": _render_table,
    "image": _render_image,
    "chart": _render_chart,
    "equation": _render_equation,
    "interline_equation": _render_equation,
    "inline_equation": _render_equation,
    "list": _render_list,
    "code": _render_code,
    "seal": _render_seal,
}


def _render_block(block: dict, index: int) -> str:
    """根据块类型查找渲染器并生成 HTML"""
    block_type = block.get("type", "text")
    border_color = BLOCK_BORDER_COLORS.get(block_type, "#3b82f6")
    type_label = BLOCK_TYPE_LABELS.get(block_type, block_type)

    if block_type == "text" and "text_level" in block:
        renderer = _render_title
        type_label = "标题"
        border_color = BLOCK_BORDER_COLORS["title"]
    elif block_type == "title":
        renderer = _render_title
    elif block_type in BLOCK_RENDERERS:
        renderer = BLOCK_RENDERERS[block_type]
    else:
        renderer = _render_fallback

    content_html = renderer(block, index)
    if not content_html:
        return ""

    title_parts = [f"类型: {type_label}"]
    confidence = block.get("confidence")
    if confidence is not None:
        title_parts.append(f"置信度: {confidence * 100:.0f}%")
    page_idx = block.get("page_idx")
    if page_idx is not None:
        title_parts.append(f"页码: {page_idx}")
    title_attr = html_lib.escape(" | ".join(title_parts))

    return (
        f'<div class="ocr-block" data-block-index="{index}" '
        f'data-block-type="{html_lib.escape(block_type)}" id="block-{index}" '
        f'style="padding:4px 8px;border-left:3px solid {border_color};'
        f'margin:2px 0;border-radius:2px;cursor:pointer;" '
        f'title="{title_attr}">'
        f"{content_html}"
        f"</div>"
    )


def _build_full_html(blocks_html: str, katex_dir: Path | None = None) -> str:
    """构建完整 HTML 页面（含 KaTeX、CSS、JS）"""
    katex_css = ""
    katex_js = ""
    if katex_dir and katex_dir.exists():
        katex_css_url = QUrl.fromLocalFile(str(katex_dir / "katex.min.css")).toString()
        katex_js_url = QUrl.fromLocalFile(str(katex_dir / "katex.min.js")).toString()
        katex_css = f'<link rel="stylesheet" href="{katex_css_url}">'
        katex_js = f'<script src="{katex_js_url}"></script>'

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
{katex_css}
<style>
body {{ margin:0; padding:8px; font-family:"Microsoft YaHei","Segoe UI",sans-serif; font-size:14px; }}
.ocr-block {{ transition: background-color 0.15s; }}
.ocr-block:hover {{ background-color: #f0f9ff; }}
.ocr-block.highlight {{ background-color: #fef08a !important; border-left-width: 4px !important; }}
.ocr-table {{ overflow-x: auto; }}
.ocr-table table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
.ocr-table td, .ocr-table th {{ border: 1px solid #d1d5db; padding: 6px 8px; }}
.ocr-table th {{ background: #f3f4f6; font-weight: 600; }}
.ocr-table tr:nth-child(even) {{ background: #f9fafb; }}
.manually-edited {{ border-left-color: #ff9800 !important; border-left-width: 4px !important; }}
[contenteditable="true"] {{ outline: 2px solid #1976d2; background-color: rgba(255,255,255,0.95); cursor: text; }}
</style>
</head>
<body>
<div id="content">
{blocks_html}
</div>
{katex_js}
<script>
// KaTeX 自动渲染
if (typeof katex !== 'undefined') {{
    document.querySelectorAll('.math-block').forEach(function(el) {{
        var latex = el.getAttribute('data-latex');
        if (latex) {{
            try {{
                katex.render(latex, el, {{ displayMode: true, throwOnError: false }});
            }} catch(e) {{
                // 保留原始 LaTeX 文本
            }}
        }}
    }});
}}

// 编辑状态
var _bridge = null;
var _editOriginals = {{}};
var _NON_EDITABLE = ['image', 'figure', 'chart', 'seal'];

function _finishTextEdit(block) {{
    var index = parseInt(block.getAttribute('data-block-index'));
    var newText = block.innerText.trim();
    block.removeAttribute('contenteditable');
    if (newText !== _editOriginals[index]) {{
        block.classList.add('manually-edited');
        if (_bridge) _bridge.onBlockEdited(index, newText);
    }}
    delete _editOriginals[index];
}}

function _finishTableEdit(block) {{
    var index = parseInt(block.getAttribute('data-block-index'));
    var tableEl = block.querySelector('.ocr-table');
    var newText = tableEl ? tableEl.innerText.trim() : '';
    block.querySelectorAll('.ocr-table td, .ocr-table th').forEach(function(cell) {{
        cell.removeAttribute('contenteditable');
    }});
    if (newText !== _editOriginals[index]) {{
        block.classList.add('manually-edited');
        if (_bridge) _bridge.onBlockEdited(index, newText);
    }}
    delete _editOriginals[index];
}}

function _startEquationEdit(block, index) {{
    var mathBlock = block.querySelector('.math-block');
    if (!mathBlock) return;
    var latex = mathBlock.getAttribute('data-latex') || '';
    _editOriginals[index] = latex;

    var existing = document.getElementById('eq-editor');
    if (existing) existing.remove();

    var textarea = document.createElement('textarea');
    textarea.id = 'eq-editor';
    textarea.value = latex;
    textarea.style.cssText = 'width:100%;min-height:60px;padding:8px;font-family:Consolas,Monaco,monospace;font-size:13px;border:2px solid #1976d2;border-radius:4px;background:white;resize:vertical;';

    mathBlock.innerHTML = '';
    mathBlock.appendChild(textarea);
    textarea.focus();
    textarea.select();

    textarea.addEventListener('blur', function() {{
        var newLatex = this.value.trim();
        mathBlock.setAttribute('data-latex', newLatex);
        if (typeof katex !== 'undefined') {{
            try {{ katex.render(newLatex, mathBlock, {{ displayMode: true, throwOnError: false }}); }}
            catch(e) {{ mathBlock.innerText = newLatex; }}
        }} else {{
            mathBlock.innerText = newLatex;
        }}
        if (newLatex !== _editOriginals[index]) {{
            block.classList.add('manually-edited');
            if (_bridge) _bridge.onBlockEdited(index, newLatex);
        }}
        delete _editOriginals[index];
    }});
}}

// 高亮通信
new QWebChannel(qt.webChannelTransport, function(channel) {{
    _bridge = channel.objects.bridge;
    document.querySelectorAll('.ocr-block').forEach(function(el) {{
        el.addEventListener('mouseenter', function() {{
            _bridge.onBlockHover(parseInt(this.getAttribute('data-block-index')));
        }});
        el.addEventListener('mouseleave', function() {{
            _bridge.onBlockLeave();
        }});
        el.addEventListener('click', function() {{
            _bridge.onBlockClick(parseInt(this.getAttribute('data-block-index')));
        }});
        el.addEventListener('dblclick', function(e) {{
            var blockType = this.getAttribute('data-block-type');
            if (_NON_EDITABLE.indexOf(blockType) >= 0) return;
            e.preventDefault();
            e.stopPropagation();
            var index = parseInt(this.getAttribute('data-block-index'));

            if (blockType === 'table') {{
                _editOriginals[index] = this.querySelector('.ocr-table').innerHTML;
                this.querySelectorAll('.ocr-table td, .ocr-table th').forEach(function(cell) {{
                    cell.setAttribute('contenteditable', 'true');
                }});
                var firstCell = this.querySelector('.ocr-table td, .ocr-table th');
                if (firstCell) firstCell.focus();
            }} else if (['equation', 'interline_equation', 'inline_equation'].indexOf(blockType) >= 0) {{
                _startEquationEdit(this, index);
            }} else {{
                _editOriginals[index] = this.innerText;
                this.setAttribute('contenteditable', 'true');
                this.focus();
            }}
        }});
    }});
}});

// 全局 blur 处理
document.addEventListener('focusout', function(e) {{
    if (e.target.matches && e.target.matches('.ocr-table td[contenteditable], .ocr-table th[contenteditable]')) {{
        var block = e.target.closest('.ocr-block');
        if (block) {{
            var table = e.target.closest('.ocr-table');
            setTimeout(function() {{
                if (!table.contains(document.activeElement)) {{
                    _finishTableEdit(block);
                }}
            }}, 50);
        }}
        return;
    }}
    var block = e.target.closest ? e.target.closest('.ocr-block[contenteditable="true"]') : null;
    if (block) _finishTextEdit(block);
}});

// Escape 取消编辑
document.addEventListener('keydown', function(e) {{
    if (e.key !== 'Escape') return;

    var block = document.querySelector('.ocr-block[contenteditable="true"]');
    if (block) {{
        var index = parseInt(block.getAttribute('data-block-index'));
        block.innerText = _editOriginals[index] || block.innerText;
        block.removeAttribute('contenteditable');
        delete _editOriginals[index];
        e.preventDefault();
        return;
    }}

    var eqEditor = document.getElementById('eq-editor');
    if (eqEditor) {{
        var eqBlock = eqEditor.closest('.ocr-block');
        var mathBlock = eqEditor.closest('.math-block');
        var eqIndex = eqBlock ? parseInt(eqBlock.getAttribute('data-block-index')) : -1;
        var origLatex = _editOriginals[eqIndex] || '';
        mathBlock.setAttribute('data-latex', origLatex);
        if (typeof katex !== 'undefined') {{
            try {{ katex.render(origLatex, mathBlock, {{ displayMode: true, throwOnError: false }}); }}
            catch(e2) {{ mathBlock.innerText = origLatex; }}
        }} else {{
            mathBlock.innerText = origLatex;
        }}
        delete _editOriginals[eqIndex];
        e.preventDefault();
    }}
}});

function highlightBlock(index) {{
    document.querySelectorAll('.ocr-block.highlight').forEach(function(el) {{
        el.classList.remove('highlight');
    }});
    var target = document.getElementById('block-' + index);
    if (target) {{
        target.classList.add('highlight');
        target.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
    }}
}}
</script>
</body>
</html>"""


class _Bridge(QObject):
    """QWebChannel 通信桥"""
    blockHovered = Signal(int)
    blockUnhovered = Signal()
    blockClicked = Signal(int)
    blockEdited = Signal(int, str)  # (block_index, new_text)

    @Slot(int)
    def onBlockHover(self, index: int):
        self.blockHovered.emit(index)

    @Slot()
    def onBlockLeave(self):
        self.blockUnhovered.emit()

    @Slot(int)
    def onBlockClick(self, index: int):
        self.blockClicked.emit(index)

    @Slot(int, str)
    def onBlockEdited(self, index: int, text: str):
        self.blockEdited.emit(index, text)


class ResultViewWidget(QWidget):
    """OCR 结果显示组件（QWebEngineView 版本）"""

    block_hovered = Signal(int)
    block_unhovered = Signal()
    block_clicked = Signal(int)
    block_edited = Signal(int, str)  # 新增：(block_index, new_text)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._current_result: Any = None
        self._highlighted_index: int = -1
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._web_view: QWebEngineView | None = None
        self._channel: QWebChannel | None = None
        self._bridge: _Bridge | None = None

    def _ensure_web_view(self) -> QWebEngineView:
        if self._web_view is not None:
            return self._web_view

        self._web_view = QWebEngineView(self)
        self._channel = QWebChannel(self._web_view)
        self._bridge = _Bridge(self)
        self._channel.registerObject("bridge", self._bridge)
        self._web_view.page().setWebChannel(self._channel)

        self._bridge.blockHovered.connect(self.block_hovered.emit)
        self._bridge.blockUnhovered.connect(self.block_unhovered.emit)
        self._bridge.blockClicked.connect(self.block_clicked.emit)
        self._bridge.blockEdited.connect(self.block_edited.emit)

        layout = self.layout()
        layout.addWidget(self._web_view)
        return self._web_view

    def display_result(self, result: Any) -> None:
        """显示 OCR 识别结果"""
        self._ensure_web_view()
        global _current_images
        self._current_result = result
        self._highlighted_index = -1

        content_list = getattr(result, "content_list", [])
        _current_images = getattr(result, "images", {})

        if content_list:
            blocks_html = []
            for i, block in enumerate(content_list):
                if block.get("type", "") in DISCARDED_BLOCK_TYPES:
                    continue
                blocks_html.append(_render_block(block, i))
            body = "\n".join(blocks_html)
        else:
            text = getattr(result, "raw_text", "")
            if text:
                body = f'<pre style="white-space:pre-wrap;">{html_lib.escape(text)}</pre>'
            else:
                body = '<p style="color:#888;">未识别到文字</p>'

        katex_dir = _RESOURCES_DIR / "katex"
        full_html = _build_full_html(body, katex_dir)
        base_url = QUrl.fromLocalFile(str(_RESOURCES_DIR) + "/")
        self._ensure_web_view().setHtml(full_html, base_url)

    def update_block_text(self, index: int, text: str) -> None:
        """从外部更新指定块的显示文本（如左侧编辑同步时调用）"""
        if not self._web_view:
            return
        escaped = json.dumps(text)
        js = f"""
    (function() {{
        var block = document.getElementById('block-{index}');
        if (!block) return;
        var blockType = block.getAttribute('data-block-type');
        if (blockType === 'table') return;
        var contentEl = block.querySelector('p, h1, h2, h3, h4, h5, h6, pre code, ul');
        if (contentEl) {{
            contentEl.innerText = {escaped};
        }} else {{
            block.innerText = {escaped};
        }}
        block.classList.add('manually-edited');
    }})();
    """
        self._web_view.page().runJavaScript(js)

    def highlight_block(self, index: int) -> None:
        """高亮指定块（-1 取消高亮）"""
        if index == self._highlighted_index:
            return
        self._highlighted_index = index
        if self._web_view:
            js = f"highlightBlock({index})" if index >= 0 else "highlightBlock(-1)"
            self._web_view.page().runJavaScript(js)

    def clear_highlight(self) -> None:
        self.highlight_block(-1)

    def cleanup(self) -> None:
        """显式销毁 QWebEngineView，避免进程退出时 QtWebEngine 崩溃。

        QWebEngineView 的原生渲染进程在 Python 解释器关闭阶段析构会触发
        STATUS_STACK_BUFFER_OVERRUN (0xC0000409)，必须在 Qt 事件循环
        仍在运行时主动销毁。
        """
        if self._web_view is not None:
            self._web_view.stop()
            self._web_view.setHtml("")
            self._web_view.setParent(None)

            import shiboken6

            if shiboken6.isValid(self._web_view):
                shiboken6.delete(self._web_view)

            self._web_view = None
            self._channel = None
            self._bridge = None

    def clear(self) -> None:
        self._current_result = None
        self._highlighted_index = -1
        if self._web_view:
            self._web_view.setHtml("")

    def get_result(self) -> Any:
        return self._current_result
