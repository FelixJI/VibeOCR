"""HTML 表格规整化与转换工具（UI 层展示逻辑）。

纯函数、无 Qt 依赖、无 OCR 引擎依赖。将 PaddleX/MinerU 输出的表格 HTML
规整化为纯净的 ``<table>``（剥离 inline style、补齐空单元格），并支持
HTML → Markdown 表格转换。按 ADR §5.2，UI 负责「展示结果」，此模块属于
UI/工具层。

从 ``vibeocr.services.ocr_service`` 迁移（Phase 3 单图 OCR 切片）。
"""

from __future__ import annotations

import html as _html
import re as _re

_RE_TABLE = _re.compile(r"(<table\b.*?</table>)", _re.DOTALL | _re.IGNORECASE)
_RE_TR = _re.compile(r"<tr[^>]*>(.*?)</tr>", _re.DOTALL | _re.IGNORECASE)
_RE_TD = _re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", _re.DOTALL | _re.IGNORECASE)
# 单格匹配：捕获标签名（td/th）以区分表头、属性段（含 colspan/rowspan）、单元格内容
_RE_CELL = _re.compile(
    r"<(td|th)([^>]*)>(.*?)</\1>", _re.DOTALL | _re.IGNORECASE
)


def extract_table_html(html_str: str) -> str:
    """从 HTML 字符串中提取第一个 ``<table>`` 块。"""
    match = _RE_TABLE.search(html_str)
    return match.group(1) if match else html_str


def html_table_to_markdown(html: str) -> str:
    """将 ``<table>`` HTML 转换为 GFM Markdown 表格。"""
    rows: list[list[str]] = []
    for tr_match in _RE_TR.finditer(html):
        cells = []
        for td in _RE_TD.finditer(tr_match.group(1)):
            # 复用 _cell_text：剥标签（<br>→\n）、unescape 实体、规整空白。
            text = _cell_text(td.group(1))
            # GFM 表格单元格内换行需表示为 <br>（python-markdown 的
            # TableExtension 会吞掉单元格内的 \n），故把 \n 转回 <br>。
            # pipe 是 markdown 表格分隔符，必须转义。
            text = text.replace("\n", "<br>").replace("|", "\\|")
            cells.append(text)
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    max_cols = max(len(r) for r in rows)
    for r in rows:
        r.extend("" for _ in range(max_cols - len(r)))
    header = "| " + " | ".join(rows[0]) + " |"
    sep = "| " + " | ".join("---" for _ in range(max_cols)) + " |"
    body = "\n".join("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n".join(part for part in (header, sep, body) if part)


def _cell_text(inner: str) -> str:
    """剥离单元格内容里的 HTML 标签，规整空白并解码常见实体。"""
    # <br> / <br/> → 换行
    text = _re.sub(r"<br\s*/?>", "\n", inner, flags=_re.IGNORECASE)
    # 其余标签直接去掉
    text = _re.sub(r"<[^>]+>", "", text)
    text = _html.unescape(text)
    # 行内空白规整，但保留显式换行
    lines = [_re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def normalize_table_html(html: str) -> str:
    """规整化表格 HTML：剥离 inline style、补齐空单元格、统一标签。

    解决两类问题：
    1. **复制带底纹**：PaddleX pred_html 的单元格常带 ``style="background:..."``
       inline 属性，渲染→原生 Ctrl+C 会把样式带进剪贴板。这里剥离所有
       属性（含 style），输出纯净的 ``<td>``/``<th>``。
    2. **空单元格错位**：若某行单元格数不足（空 ``<td>`` 缺失或 HTML 不规则），
       Excel/Word 粘贴时会把后续单元格前移（如 A1 空、A2 有内容，结果 A2
       内容跑到 A1）。这里按最大列数补齐，保证每行规整矩形。

    本函数**保留原 td/th 标签类型**（不强制首行 th），仅清洗属性 + 补空格，
    适合渲染展示与复制。

    Args:
        html: 原始表格 HTML（含/不含 ``<html><body>`` 外壳均可）。

    Returns:
        规整化的 ``<table>...</table>``，所有单元格无属性、每行列数一致。
    """
    table_match = _RE_TABLE.search(html)
    table_html = table_match.group(1) if table_match else html

    # 解析为 [(tag, text), ...] 的行列表，保留原 td/th 标签
    rows: list[list[tuple[str, str]]] = []
    for tr_match in _RE_TR.finditer(table_html):
        row: list[tuple[str, str]] = []
        for cm in _RE_CELL.finditer(tr_match.group(1)):
            tag = cm.group(1).lower()  # td 或 th
            text = _cell_text(cm.group(3))
            row.append((tag, text))
        if row:  # 跳过空 <tr></tr>
            rows.append(row)

    if not rows:
        return "<table></table>"

    max_cols = max(len(r) for r in rows)
    rows_html: list[str] = []
    for row in rows:
        cells_html: list[str] = []
        for c_i in range(max_cols):
            if c_i < len(row):
                tag, text = row[c_i]
            else:
                # 列数不足：补空 td（不破坏 Excel 的列对齐）
                tag, text = "td", ""
            safe = _html.escape(text).replace("\n", "<br>")
            cells_html.append(f"<{tag}>{safe}</{tag}>")
        rows_html.append(f"<tr>{''.join(cells_html)}</tr>")
    return f"<table>{''.join(rows_html)}</table>"


# Backward-compat aliases matching the original private names.
_extract_table_html = extract_table_html
_html_table_to_markdown = html_table_to_markdown


__all__ = [
    "extract_table_html",
    "html_table_to_markdown",
    "normalize_table_html",
]
