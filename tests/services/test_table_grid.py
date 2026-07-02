"""Tests for HTML table grid parsing/serialization utilities.

验证 ``parse_table_html_to_grid`` / ``grid_to_table_html`` 的解析→序列化往返
一致性、colspan 处理、HTML 实体转义等。这两个函数是左侧画布表格网格
编辑器的核心，且不依赖 paddle/Qt，可独立运行。
"""

from __future__ import annotations

import pytest

from vibeocr.services.ocr_service import (
    grid_to_table_html,
    normalize_table_html,
    parse_table_html_to_grid,
)


class TestParseTableHtmlToGrid:
    def test_simple_two_by_two(self) -> None:
        html = "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
        grid = parse_table_html_to_grid(html)
        assert grid == [["A", "B"], ["1", "2"]]

    def test_strips_inner_tags(self) -> None:
        html = (
            "<table><tr><th><b>Name</b></th></tr>"
            "<tr><td><span>X</span></td></tr></table>"
        )
        grid = parse_table_html_to_grid(html)
        assert grid == [["Name"], ["X"]]

    def test_br_becomes_newline(self) -> None:
        html = "<table><tr><td>line1<br>line2</td></tr></table>"
        grid = parse_table_html_to_grid(html)
        assert grid == [["line1\nline2"]]

    def test_html_entities_decoded(self) -> None:
        html = "<table><tr><td>a&amp;b</td><td>c&lt;d</td></tr></table>"
        grid = parse_table_html_to_grid(html)
        assert grid == [["a&b", "c<d"]]

    def test_colspan_repeats_text(self) -> None:
        # colspan=2 的单元格在后续列重复填充
        html = (
            "<table><tr><td colspan='2'>merged</td><td>c</td></tr></table>"
        )
        grid = parse_table_html_to_grid(html)
        assert grid == [["merged", "merged", "c"]]

    def test_ragged_rows_padded(self) -> None:
        # 列数不等的行右侧补空串
        html = "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td></tr></table>"
        grid = parse_table_html_to_grid(html)
        assert grid == [["A", "B"], ["1", ""]]

    def test_extracts_table_from_wrapper(self) -> None:
        # PaddleX pred_html 外层包 <html><body>
        html = "<html><body><table><tr><td>x</td></tr></table></body></html>"
        grid = parse_table_html_to_grid(html)
        assert grid == [["x"]]

    def test_empty_table_returns_empty(self) -> None:
        assert parse_table_html_to_grid("<table></table>") == []
        assert parse_table_html_to_grid("") == []

    def test_nbsp_decoded(self) -> None:
        html = "<table><tr><td>a&nbsp;b</td></tr></table>"
        grid = parse_table_html_to_grid(html)
        # &nbsp; → \xa0，规整空白时保留（非 ASCII 空白）
        assert grid == [["a\xa0b"]]


class TestGridToTableHtml:
    def test_first_row_th_rest_td(self) -> None:
        html = grid_to_table_html([["A", "B"], ["1", "2"]])
        assert "<th>A</th><th>B</th>" in html
        assert "<td>1</td><td>2</td>" in html
        assert html.startswith("<table>")
        assert html.endswith("</table>")

    def test_escapes_special_chars(self) -> None:
        html = grid_to_table_html([["a<b>c", "d&e"]])
        assert "a&lt;b&gt;c" in html
        assert "d&amp;e" in html

    def test_newline_to_br(self) -> None:
        html = grid_to_table_html([["line1\nline2"]])
        assert "line1<br>line2" in html

    def test_empty_grid(self) -> None:
        assert grid_to_table_html([]) == "<table></table>"

    def test_ragged_row_padded(self) -> None:
        html = grid_to_table_html([["A", "B"], ["1"]])
        # 第二行应补一个空 td
        assert "<td>1</td><td></td>" in html


class TestRoundTrip:
    """解析→序列化→解析 应保持网格内容一致。"""

    @pytest.mark.parametrize(
        "html",
        [
            "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>",
            "<table><tr><td>only</td></tr></table>",
            "<table><tr><th>H1</th><th>H2</th><th>H3</th></tr>"
            "<tr><td>a</td><td>b</td><td>c</td></tr></table>",
        ],
    )
    def test_roundtrip_preserves_content(self, html: str) -> None:
        grid1 = parse_table_html_to_grid(html)
        rebuilt = grid_to_table_html(grid1)
        grid2 = parse_table_html_to_grid(rebuilt)
        assert grid1 == grid2


class TestNormalizeTableHtml:
    """normalize_table_html：剥离 inline style、补齐空单元格、保留标签。

    这是解决"复制带底纹"和"空单元格错位"两个问题的核心。
    """

    def test_strips_inline_style(self) -> None:
        """PaddleX 自带 style 属性应被剥离。"""
        html = (
            '<table><tr><td style="background:#eee;border:1px">A</td>'
            '<th style="color:red">B</th></tr></table>'
        )
        out = normalize_table_html(html)
        assert "style" not in out
        assert "<td>A</td>" in out
        assert "<th>B</th>" in out

    def test_fills_missing_cells_to_rectangular(self) -> None:
        """A1 空、A2 有内容场景：第二行只有一列，应补齐为两列空 td。

        回归：修复前若 HTML 某行单元格数不足，Excel 粘贴会把后续单元格
        前移（A2 内容跑到 A1）。规整后每行列数一致，Excel 列对齐正确。
        """
        # 第一行 2 列，第二行只有 1 列
        html = "<table><tr><th>H1</th><th>H2</th></tr><tr><td>only</td></tr></table>"
        out = normalize_table_html(html)
        # 第二行应补一个空 td
        assert "<tr><td>only</td><td></td></tr>" in out

    def test_preserves_empty_cell_explicitly(self) -> None:
        """空单元格应显式保留为 <td></td>，不能被丢弃。"""
        html = (
            "<table><tr><td></td><td>filled</td></tr>"
            "<tr><td>x</td><td></td></tr></table>"
        )
        out = normalize_table_html(html)
        # 空单元格仍在
        assert out.count("<td></td>") == 2

    def test_preserves_td_th_tags(self) -> None:
        """与 grid_to_table_html 不同：normalize 保留原 td/th，不强制首行 th。"""
        html = "<table><tr><td>not-header</td><td>also-td</td></tr></table>"
        out = normalize_table_html(html)
        assert "<td>not-header</td>" in out
        assert "<th>" not in out

    def test_strips_style_from_wrapper_html(self) -> None:
        """PaddleX pred_html 外层包 <html><body>，内部 table 带 style。"""
        html = (
            "<html><body><table>"
            '<tr><td style="background:#ccc">A</td></tr>'
            "</table></body></html>"
        )
        out = normalize_table_html(html)
        assert out.startswith("<table>")
        assert "style" not in out
        assert "background" not in out

    def test_html_entities_roundtrip(self) -> None:
        """实体应正确解码后重新转义，不产生双重转义。"""
        html = "<table><tr><td>a&amp;b</td><td>c&lt;d</td></tr></table>"
        out = normalize_table_html(html)
        assert "<td>a&amp;b</td>" in out
        assert "<td>c&lt;d</td>" in out

    def test_all_rows_same_column_count(self) -> None:
        """规整后每行的 td+th 数量必须一致（矩形）。"""
        html = (
            "<table>"
            "<tr><th>A</th><th>B</th><th>C</th></tr>"
            "<tr><td>1</td></tr>"
            "<tr><td>x</td><td>y</td></tr>"
            "</table>"
        )
        out = normalize_table_html(html)
        # 用 parse 验证每行列数都是 3
        grid = parse_table_html_to_grid(out)
        assert all(len(row) == 3 for row in grid)
        assert len(grid) == 3

    def test_empty_table(self) -> None:
        assert normalize_table_html("<table></table>") == "<table></table>"
        assert normalize_table_html("") == "<table></table>"

    def test_no_style_attribute_in_output(self) -> None:
        """综合：多个 style、class、colspan 等属性都应被剥离，只留标签+文本。"""
        html = (
            '<table><tr><td class="c" colspan="2" style="bg:1">text</td></tr>'
            '<tr><th id="h" style="font:bold">h</th><td>2</td></tr></table>'
        )
        out = normalize_table_html(html)
        # 输出中不应有任何属性
        import re

        # 匹配 <td ...> 或 <th ...> 中是否有额外属性
        tags = re.findall(r"<t[dh][^>]*>", out)
        for tag in tags:
            # 只允许纯 <td> 或 <th>，无属性
            assert tag in ("<td>", "<th>"), f"unexpected attrs in {tag}"
