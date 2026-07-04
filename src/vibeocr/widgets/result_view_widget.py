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
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from vibeocr.models.ocr_result import DISCARDED_BLOCK_TYPES

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    # QWebEngineView / QWebChannel 仅作类型注解引用，运行时延迟 import
    # （WebEngine 内置主包：Qt6WebEngineCore.dll 随 _internal/ 一起分发，
    # 延迟 import 仅为避免顶层立即触发 DLL 加载）。
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineWidgets import QWebEngineView

logger = logging.getLogger(__name__)


def _get_resources_dir() -> Path:
    """获取 resources 目录路径（打包态/开发态通用）

    委托 env_manager.get_bundled_resources_dir() 作为 SSOT：
    打包态 resources 由 ``--add-data`` 打入 ``sys._MEIPASS``（``_internal/resources``），
    而非 exe 同级；开发态位于仓库根。
    采用函数惰性求值，避免模块导入时触发 env_manager 的循环导入。
    """
    from vibeocr.env_manager import get_bundled_resources_dir

    return get_bundled_resources_dir()


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
    # PaddleX 公式管道（pipeline_formula / pipeline_pp_structure）输出 label="formula"，
    # 在渲染层归一到公式渲染（KaTeX），避免下游（导出/Markdown）受影响。
    "formula": "#f97316",
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
    "formula": "公式",
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
    from vibeocr.services.ocr_service import normalize_table_html

    parts: list[str] = []
    captions = block.get("table_caption") or []
    if captions:
        parts.append(
            f'<p style="color:#888;font-size:12px;">{html_lib.escape(captions[0])}</p>'
        )
    table_body = block.get("table_body", "")
    html_content = block.get("html", "")
    raw_table = table_body or html_content
    if raw_table:
        # 规整化：剥离 PaddleX 自带的 inline style（避免复制带底纹），
        # 并补齐空单元格（避免 Excel 粘贴错位）。
        clean_table = normalize_table_html(raw_table)
        parts.append(f'<div class="ocr-table">{clean_table}</div>')
    else:
        text = html_lib.escape(block.get("text", ""))
        parts.append(f"<p>{text}</p>")
    footnotes = block.get("table_footnote") or []
    if footnotes:
        parts.append(
            f'<p style="color:#888;font-size:11px;">{html_lib.escape(footnotes[0])}</p>'
        )
    return "\n".join(parts)


def _render_image(block: dict, index: int) -> str:
    parts: list[str] = []
    img_path = block.get("img_path", "")
    if img_path and img_path in _current_images:
        img_bytes = _current_images[img_path]
        b64 = base64.b64encode(img_bytes).decode()
        ext = img_path.rsplit(".", 1)[-1].lower()
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(
            ext, "image/png"
        )
        parts.append(
            f'<img src="data:{mime};base64,{b64}" style="max-width:100%;border-radius:4px;">'
        )
    else:
        img_idx = block.get("img_idx")
        if img_idx is not None:
            parts.append(f'<p style="color:#888;">[图片 #{img_idx}]</p>')
        else:
            text = html_lib.escape(block.get("text", ""))
            parts.append(
                f"<p>[图片] {text}</p>" if text else '<p style="color:#888;">[图片]</p>'
            )
    captions = block.get("image_caption") or []
    if captions:
        parts.append(
            f'<p style="color:#888;font-size:12px;">{html_lib.escape(captions[0])}</p>'
        )
    return "\n".join(parts)


def _render_chart(block: dict, index: int) -> str:
    parts: list[str] = []
    img_path = block.get("img_path", "")
    if img_path and img_path in _current_images:
        img_bytes = _current_images[img_path]
        b64 = base64.b64encode(img_bytes).decode()
        parts.append(
            f'<img src="data:image/png;base64,{b64}" style="max-width:100%;border-radius:4px;">'
        )
    content = block.get("content", "")
    if content:
        parts.append(
            f'<p style="color:#555;font-size:13px;">{html_lib.escape(content)}</p>'
        )
    if not parts:
        parts.append('<p style="color:#888;">[图表]</p>')
    return "\n".join(parts)


def _render_equation(block: dict, index: int) -> str:
    latex = html_lib.escape(block.get("text", ""))
    # 注意：不再在此处加 border-left。外层 _render_block 已为公式块加了
    # 橙色左边框（#f97316）作为类型标识；此处再叠加会形成"双条色标"，
    # 且蓝色（#0078d4）与文本蓝（#3b82f6）混淆，难以区分。
    return (
        f'<div class="math-block" data-latex="{latex}" '
        f'style="background:#f8f9fa;padding:8px 12px;border-radius:4px;'
        f'font-family:Consolas,Monaco,monospace;font-size:13px;">'
        f"{latex}</div>"
    )


def _render_list(block: dict, index: int) -> str:
    items = block.get("list_items", [])
    li_html = "".join(f"<li>{html_lib.escape(item)}</li>" for item in items)
    return f'<ul style="padding-left:20px;">{li_html}</ul>'


def _render_code(block: dict, index: int) -> str:
    body = html_lib.escape(block.get("code_body", ""))
    sub = block.get("sub_type", "")
    lang_label = (
        f'<span style="color:#888;font-size:11px;">[{html_lib.escape(sub)}]</span>'
        if sub
        else ""
    )
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
    # PaddleX 公式管道输出 type="formula"，归一到公式渲染（KaTeX）。
    "formula": _render_equation,
    "list": _render_list,
    "code": _render_code,
    "seal": _render_seal,
}


def _render_block(block: dict, index: int) -> str:
    """根据块类型查找渲染器并生成 HTML"""
    block_type = block.get("type", "text")
    border_color = BLOCK_BORDER_COLORS.get(block_type, "#3b82f6")
    type_label = BLOCK_TYPE_LABELS.get(block_type, block_type)

    renderer: Callable[[dict, int], str] = _render_fallback
    if block_type == "text" and "text_level" in block:
        renderer = _render_title
        type_label = "标题"
        border_color = BLOCK_BORDER_COLORS["title"]
    elif block_type == "title":
        renderer = _render_title
    elif block_type in BLOCK_RENDERERS:
        renderer = BLOCK_RENDERERS[block_type]

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
        f'margin:2px 0;border-radius:2px;" '
        f'title="{title_attr}">'
        f"{content_html}"
        f"</div>"
    )


def _build_full_html(blocks_html: str, katex_dir: Path | None = None) -> str:
    """构建完整 HTML 页面（含 KaTeX、CSS、JS）"""
    katex_css = ""
    katex_js_tag = ""  # 外部 KaTeX <script>（onload 触发渲染）
    if katex_dir and katex_dir.exists():
        # 必须用绝对路径：早期版本传相对路径 resources/katex/katex.min.js，
        # QUrl.fromLocalFile 会生成畸形 URL（file:resources/... 而非 file:///...），
        # Chromium WebEngine 无法加载 → KaTeX 不执行 → 公式显示为原始 LaTeX。
        katex_css_url = QUrl.fromLocalFile(str((katex_dir / "katex.min.css").resolve()))
        katex_js_url = QUrl.fromLocalFile(str((katex_dir / "katex.min.js").resolve()))
        katex_css = f'<link rel="stylesheet" href="{katex_css_url.toString()}">'
        # KaTeX 加载完成后再触发渲染（onload），避免外部脚本加载失败时
        # 阻塞其后内联脚本（编辑/光标逻辑）的执行。
        katex_js_tag = (
            f'<script src="{katex_js_url.toString()}" '
            f'onload="renderAllMath()"></script>'
        )

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
/* 光标：文本区显示 I-beam（提示可编辑），表格单元格默认箭头。
   不在 .ocr-block 内联 style 设 cursor:pointer（旧版这样做会压过编辑态样式表）。 */
.ocr-block p, .ocr-block h1, .ocr-block h2, .ocr-block h3,
.ocr-block h4, .ocr-block h5, .ocr-block h6, .ocr-block li {{ cursor: text; }}
.ocr-table {{ overflow-x: auto; }}
.ocr-table table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
.ocr-table td, .ocr-table th {{ border: 1px solid #d1d5db; padding: 6px 8px; }}
.ocr-table th {{ font-weight: 600; }}
/* 不加 th 背景与斑马纹：避免原生 Ctrl+C 把底纹带进剪贴板（Excel/Word 粘贴出灰底）。
   视觉区分靠边框 + th 加粗即可；复制时另有 copy 拦截器输出无样式 HTML。 */
.ocr-table td.sel-cell, .ocr-table th.sel-cell {{ background-color: rgba(25,118,210,0.18) !important; }}
.manually-edited {{ border-left-color: #ff9800 !important; border-left-width: 4px !important; }}
/* 编辑态：!important 压过任何继承/内联 cursor，确保进入编辑时光标变 I-beam。 */
[contenteditable="true"] {{ outline: 2px solid #1976d2; background-color: rgba(255,255,255,0.95); cursor: text !important; }}
</style>
</head>
<body>
<div id="content">
{blocks_html}
</div>
<script>
// 公式渲染函数：由 KaTeX <script onload> 触发，也可被编辑后手动调用。
// 放在内联脚本最前面定义，确保 KaTeX 加载完成时函数已存在。
function renderAllMath() {{
    if (typeof katex === 'undefined') return;
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
    // 传回表格 HTML（不是 innerText 纯文本），与 update_block_text 重建
    // .ocr-table.innerHTML 的契约一致；Python 侧据此更新 table_body。
    var newHtml = tableEl ? tableEl.innerHTML.trim() : '';
    block.querySelectorAll('.ocr-table td, .ocr-table th').forEach(function(cell) {{
        cell.removeAttribute('contenteditable');
    }});
    if (newHtml !== _editOriginals[index]) {{
        block.classList.add('manually-edited');
        if (_bridge) _bridge.onBlockEdited(index, newHtml);
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

// ── 块事件绑定（顶层立即执行，不依赖 QWebChannel）──
// 关键：早期版本把 addEventListener 全部放在 `new QWebChannel(...)` 回调里，
// 但页面未加载 qwebchannel.js → QWebChannel 构造函数未定义 → 回调永不执行 →
// dblclick/click 监听器从未绑定（表现为结果区无法编辑、无点击高亮）。
// 现改为顶层绑定事件；QWebChannel 回调只负责赋值 _bridge，所有 _bridge.*
// 调用都用 if(_bridge) 守卫，bridge 不可用时编辑/光标/复制照常工作。
document.querySelectorAll('.ocr-block').forEach(function(el) {{
    el.addEventListener('mouseenter', function() {{
        if (_bridge) _bridge.onBlockHover(parseInt(this.getAttribute('data-block-index')));
    }});
    el.addEventListener('mouseleave', function() {{
        if (_bridge) _bridge.onBlockLeave();
    }});
    el.addEventListener('click', function() {{
        if (_bridge) _bridge.onBlockClick(parseInt(this.getAttribute('data-block-index')));
    }});
    el.addEventListener('dblclick', function(e) {{
        var blockType = this.getAttribute('data-block-type');
        if (_NON_EDITABLE.indexOf(blockType) >= 0) return;
        e.preventDefault();
        e.stopPropagation();
        var index = parseInt(this.getAttribute('data-block-index'));

        if (blockType === 'table') {{
            // 基线与 _finishTableEdit 的比较值统一用 innerHTML（表格 HTML），
            // 保证"未改动不标黄"，且 onBlockEdited 回传的就是新表格 HTML，
            // 与 Python 侧 table_body 更新 / update_block_text 重建契约一致。
            var tableEl = this.querySelector('.ocr-table');
            _editOriginals[index] = tableEl ? tableEl.innerHTML.trim() : '';
            this.querySelectorAll('.ocr-table td, .ocr-table th').forEach(function(cell) {{
                cell.setAttribute('contenteditable', 'true');
            }});
            var firstCell = this.querySelector('.ocr-table td, .ocr-table th');
            if (firstCell) firstCell.focus();
        }} else if (['equation', 'interline_equation', 'inline_equation', 'formula'].indexOf(blockType) >= 0) {{
            _startEquationEdit(this, index);
        }} else {{
            _editOriginals[index] = this.innerText;
            this.setAttribute('contenteditable', 'true');
            this.focus();
        }}
    }});
}});

// 高亮通信：仅赋值 _bridge。失败（qwebchannel.js 缺失）不影响上方编辑逻辑。
if (typeof QWebChannel !== 'undefined') {{
    new QWebChannel(qt.webChannelTransport, function(channel) {{
        _bridge = channel.objects.bridge;
    }});
}}

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

function getCopyText() {{
    var sel = window.getSelection();
    if (sel && sel.toString().trim().length > 0) {{
        return sel.toString();
    }}
    var blocks = document.querySelectorAll('.ocr-block');
    var parts = [];
    blocks.forEach(function(b) {{
        var t = b.innerText.trim();
        if (t) parts.push(t);
    }});
    return parts.join('\\n\\n');
}}

// ── 表格单元格级拖选（Word/Excel 式）──
// 当前选中状态：null 表示无单元格选中（回退原生选区）
var _tableSel = null;  // {{ table, r0, c0, r1, c1 }}

function _cellIndex(cell) {{
    // 计算 td/th 在其 table 中的 (row, col)，考虑跨行/跨列已由规整化补齐
    var tr = cell.parentNode;
    var row = Array.prototype.indexOf.call(tr.parentNode.children, tr);
    var col = Array.prototype.indexOf.call(tr.children, cell);
    return {{ row: row, col: col }};
}}

function _clearTableSelHighlight() {{
    document.querySelectorAll('.ocr-table .sel-cell').forEach(function(c) {{
        c.classList.remove('sel-cell');
    }});
}}

function _applyTableSelHighlight(sel) {{
    _clearTableSelHighlight();
    if (!sel) return;
    var rows = sel.table.querySelectorAll('tr');
    var r0 = Math.min(sel.r0, sel.r1), r1 = Math.max(sel.r0, sel.r1);
    var c0 = Math.min(sel.c0, sel.c1), c1 = Math.max(sel.c0, sel.c1);
    for (var r = r0; r <= r1; r++) {{
        var cells = rows[r] ? rows[r].children : [];
        for (var c = c0; c <= c1; c++) {{
            if (cells[c]) cells[c].classList.add('sel-cell');
        }}
    }}
}}

function _startCellSelect(cell, e) {{
    // contenteditable 编辑中的单元格不拦截（让用户正常编辑文字）
    if (cell.getAttribute('contenteditable') === 'true') return;
    var table = cell.closest('table');
    if (!table) return;
    var pos = _cellIndex(cell);
    _tableSel = {{ table: table, r0: pos.row, c0: pos.col, r1: pos.row, c1: pos.col }};
    _applyTableSelHighlight(_tableSel);
    e.preventDefault();  // 阻止原生文本选区
}}

function _extendCellSelect(cell) {{
    if (!_tableSel) return;
    var pos = _cellIndex(cell);
    _tableSel.r1 = pos.row;
    _tableSel.c1 = pos.col;
    _applyTableSelHighlight(_tableSel);
}}

// mousedown：在单元格上启动拖选
document.addEventListener('mousedown', function(e) {{
    var cell = e.target.closest('.ocr-table td, .ocr-table th');
    if (cell) _startCellSelect(cell, e);
}});

// mousemove（按下时）：扩展选区
document.addEventListener('mousemove', function(e) {{
    if (!_tableSel || (e.buttons & 1) === 0) return;  // 仅左键按下时
    var cell = e.target.closest('.ocr-table td, .ocr-table th');
    if (cell && _tableSel.table.contains(cell)) _extendCellSelect(cell);
}});

// 点击表格外的区域：清除单元格选中
document.addEventListener('mousedown', function(e) {{
    if (!e.target.closest('.ocr-table')) {{
        if (_tableSel) {{
            _tableSel = null;
            _clearTableSelHighlight();
        }}
    }}
}});

// ── 从选中区域构建干净 HTML + Tab 分隔文本（复制用）──
function _tableSelToOutput(sel) {{
    var rows = sel.table.querySelectorAll('tr');
    var r0 = Math.min(sel.r0, sel.r1), r1 = Math.max(sel.r0, sel.r1);
    var c0 = Math.min(sel.c0, sel.c1), c1 = Math.max(sel.c0, sel.c1);
    var trHtml = [], lines = [];
    for (var r = r0; r <= r1; r++) {{
        var cells = rows[r] ? rows[r].children : [];
        var ch = [], texts = [];
        for (var c = c0; c <= c1; c++) {{
            var cell = cells[c];
            var text = cell ? cell.innerText : '';
            texts.push(text);
            // 保留原标签（td/th），不加任何属性 → Excel/Word 粘贴无底纹
            var tag = cell ? cell.tagName.toLowerCase() : 'td';
            ch.push('<' + tag + '>' + text.replace(/&/g,'&amp;').replace(/</g,'&lt;') + '</' + tag + '>');
        }}
        trHtml.push('<tr>' + ch.join('') + '</tr>');
        lines.push(texts.join('\\t'));
    }}
    return {{ html: '<table>' + trHtml.join('') + '</table>', text: lines.join('\\n') }};
}}

// ── 拦截 copy：表格选中时输出无样式 HTML + Tab 文本 ──
document.addEventListener('copy', function(e) {{
    if (!_tableSel) return;  // 无单元格选中 → 走原生 copy（普通文本块）
    var out = _tableSelToOutput(_tableSel);
    e.clipboardData.setData('text/html', out.html);
    e.clipboardData.setData('text/plain', out.text);
    e.preventDefault();
}});
</script>
{katex_js_tag}
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
    # WebEngine 不可用时触发（保留信号：内置打包后通常不会触发，
    # 但作为 import 失败时的防御性通知机制保留）。
    webengine_missing = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._current_result: Any = None
        self._highlighted_index: int = -1
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # 工具栏（复制按钮）
        toolbar = QWidget()
        toolbar.setFixedHeight(28)
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(0, 0, 4, 0)
        tb_layout.setSpacing(4)
        tb_layout.addStretch()

        self._copy_btn = QPushButton("复制文本")
        self._copy_md_btn = QPushButton("复制MD")
        self._export_docx_btn = QPushButton("导出Word")
        self._export_xlsx_btn = QPushButton("导出Excel")
        for btn in (
            self._copy_btn,
            self._copy_md_btn,
            self._export_docx_btn,
            self._export_xlsx_btn,
        ):
            btn.setFixedHeight(24)
            btn.setStyleSheet("QPushButton { padding: 2px 12px; font-size: 12px; }")
            btn.hide()
        tb_layout.addWidget(self._copy_btn)
        tb_layout.addWidget(self._copy_md_btn)
        tb_layout.addWidget(self._export_docx_btn)
        tb_layout.addWidget(self._export_xlsx_btn)
        layout.addWidget(toolbar)

        # 复制成功浮层提示
        self._copy_toast = QLabel("已复制到剪贴板", self)
        self._copy_toast.setStyleSheet(
            "QLabel { background-color: #1f2937; color: #ffffff;"
            " padding: 6px 12px; border-radius: 4px; font-size: 12px; }"
        )
        self._copy_toast.hide()

        # 延迟创建：WebEngine 内置主包，import 通常成功；惰性创建避免启动即加载。
        self._web_view: QWebEngineView | None = None
        self._channel: QWebChannel | None = None
        self._bridge: _Bridge | None = None

        self._copy_btn.clicked.connect(self._on_copy_text)
        self._copy_md_btn.clicked.connect(self._on_copy_markdown)
        self._export_docx_btn.clicked.connect(lambda: self._on_export_file("docx"))
        self._export_xlsx_btn.clicked.connect(lambda: self._on_export_file("xlsx"))

    def _ensure_web_view(self) -> QWebEngineView | None:
        """惰性创建并返回 QWebEngineView；WebEngine 未就绪时返回 None。

        WebEngine（Qt6WebEngineCore.dll 等）内置主包，随 _internal/PySide6/ 分发。
        import 通常成功，返回 None 仅作为 DLL 加载异常时的防御性回退
        （调用方据此显示占位提示）。
        """
        if self._web_view is not None:
            return self._web_view

        # 运行时延迟 import：避免模块顶层加载触发 WebEngine DLL
        try:
            from PySide6.QtWebChannel import QWebChannel
            from PySide6.QtWebEngineWidgets import QWebEngineView
        except (ImportError, OSError) as e:
            # Qt6WebEngineCore.dll 缺失或损坏 → ImportError/OSError
            logger.warning(f"WebEngine 不可用，结果页无法渲染: {e}")
            return None

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
        assert layout is not None
        layout.addWidget(self._web_view)
        return self._web_view

    def _on_copy_text(self) -> None:
        """复制选中文本或全部文本到剪贴板"""
        if not self._web_view:
            return
        self._web_view.page().runJavaScript("getCopyText()", self._do_copy_text)

    def _do_copy_text(self, text: str | None) -> None:
        if text:
            QGuiApplication.clipboard().setText(text)
            self._show_copy_toast()

    def _on_copy_markdown(self) -> None:
        """复制 Markdown 到剪贴板（直读 _current_result，不走 WebEngine JS）。"""
        if self._current_result is None:
            return
        md = getattr(self._current_result, "markdown_text", "") or getattr(
            self._current_result, "raw_text", ""
        )
        if not md:
            return
        QGuiApplication.clipboard().setText(md)
        self._show_copy_toast("Markdown 已复制")

    def _on_export_file(self, fmt: str) -> None:
        """导出为 Word/Excel 文件（另存为对话框 + ExportService）。"""
        if self._current_result is None:
            return
        from pathlib import Path

        from vibeocr.services.export_service import ExportService

        filter_label = {
            "docx": "Word 文档 (*.docx)",
            "xlsx": "Excel 工作簿 (*.xlsx)",
        }[fmt]
        default_name = ExportService.get_output_filename("ocr_result", fmt)
        path, _ = QFileDialog.getSaveFileName(
            self, f"导出 {fmt.upper()}", default_name, filter_label
        )
        if not path:
            return
        ok = ExportService.export(self._current_result, Path(path), fmt)
        if ok:
            QMessageBox.information(self, "导出成功", f"已导出到：\n{path}")
        else:
            QMessageBox.warning(self, "导出失败", "导出失败，请重试或查看日志。")

    def _show_copy_toast(self, message: str = "已复制到剪贴板") -> None:
        """显示复制成功浮层"""
        self._copy_toast.setText(message)
        self._copy_toast.adjustSize()
        # 显示在 widget 右上角
        x = self.width() - self._copy_toast.width() - 12
        y = 4
        self._copy_toast.move(x, y)
        self._copy_toast.raise_()
        self._copy_toast.show()
        QTimer.singleShot(1500, self._copy_toast.hide)

    def display_result(self, result: Any) -> None:
        """显示 OCR 识别结果"""
        # 先记录结果并显示 WebEngine 无关的按钮（复制MD/导出），
        # 这样即便 WebEngine 不可用，用户仍可复制 Markdown、导出 Word/Excel。
        self._current_result = result
        self._copy_md_btn.show()
        self._export_docx_btn.show()
        self._export_xlsx_btn.show()

        web_view = self._ensure_web_view()
        if web_view is None:
            # WebEngine 未就绪：发出信号供上层弹下载引导（上层连接此信号）。
            self.webengine_missing.emit()
            return
        self._copy_btn.show()
        global _current_images
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
                body = (
                    f'<pre style="white-space:pre-wrap;">{html_lib.escape(text)}</pre>'
                )
            else:
                body = '<p style="color:#888;">未识别到文字</p>'

        resources_dir = _get_resources_dir()
        katex_dir = resources_dir / "katex"
        full_html = _build_full_html(body, katex_dir)
        base_url = QUrl.fromLocalFile(str(resources_dir) + "/")
        web_view.setHtml(full_html, base_url)

    def update_block_text(self, index: int, text: str) -> None:
        """从外部更新指定块的显示文本（如左侧编辑同步时调用）。

        对 table 块，``text`` 应为新的 ``<table>`` HTML，会重建 ``.ocr-table``
        容器的 innerHTML（替代早期直接 return 不刷新的行为）。
        """
        if not self._web_view:
            return
        escaped = json.dumps(text)
        js = f"""
    (function() {{
        var block = document.getElementById('block-{index}');
        if (!block) return;
        var blockType = block.getAttribute('data-block-type');
        if (blockType === 'table') {{
            // 表格：重建 .ocr-table 容器内容
            var tableBox = block.querySelector('.ocr-table');
            if (tableBox) {{
                tableBox.innerHTML = {escaped};
            }}
        }} else {{
            var contentEl = block.querySelector('p, h1, h2, h3, h4, h5, h6, pre code, ul');
            if (contentEl) {{
                contentEl.innerText = {escaped};
            }} else {{
                block.innerText = {escaped};
            }}
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
        self._copy_btn.hide()
        self._copy_md_btn.hide()
        self._export_docx_btn.hide()
        self._export_xlsx_btn.hide()
        if self._web_view:
            self._web_view.setHtml("")

    def get_result(self) -> Any:
        return self._current_result
