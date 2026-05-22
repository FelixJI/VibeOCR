"""Tests for result_view_widget block rendering functions."""

import re

from vibeocr.widgets.result_view_widget import (
    _render_block,
    _render_text,
    _render_title,
    _render_table,
    _render_equation,
    _render_list,
    _render_code,
    _render_fallback,
    BLOCK_BORDER_COLORS,
    BLOCK_TYPE_LABELS,
)


class TestRenderBlockTitleAttribute:
    """测试 _render_block 生成的 title 属性。"""

    def test_title_attribute_with_type_and_confidence(self):
        """有类型和置信度时，title 包含两者。"""
        block = {"type": "text", "text": "hello", "confidence": 0.92}
        html = _render_block(block, 0)
        assert 'title="类型: 文本 | 置信度: 92%"' in html

    def test_title_attribute_type_only(self):
        """只有类型没有置信度时，title 只显示类型。"""
        block = {"type": "title", "text": "Chapter 1", "level": 1}
        html = _render_block(block, 1)
        assert "类型: 标题" in html

    def test_title_attribute_confidence_only(self):
        """text 块有置信度时，title 包含置信度。"""
        block = {"text": "no type", "confidence": 0.75}
        html = _render_block(block, 2)
        assert "置信度: 75%" in html

    def test_title_always_present(self):
        """所有块都有 title 属性（至少包含类型）。"""
        block = {"text": "plain text"}
        html = _render_block(block, 3)
        assert "title=" in html

    def test_no_inline_confidence(self):
        """置信度信息只出现在 title 属性中，不内嵌显示。"""
        block = {"type": "text", "text": "hello", "confidence": 0.60}
        html = _render_block(block, 0)
        without_title = re.sub(r' title="[^"]*"', "", html)
        assert "置信度" not in without_title

    def test_table_block_title(self):
        """表格块显示类型标签。"""
        block = {"type": "table", "table_body": "<table><tr><td>data</td></tr></table>"}
        html = _render_block(block, 4)
        assert "类型: 表格" in html

    def test_equation_block_title(self):
        """公式块显示类型标签。"""
        block = {"type": "equation", "text": "E=mc^2"}
        html = _render_block(block, 5)
        assert "类型: 公式" in html

    def test_high_confidence_still_shows_in_title(self):
        """高置信度（>=0.95）也显示在 title 中。"""
        block = {"type": "text", "text": "confident", "confidence": 0.98}
        html = _render_block(block, 6)
        assert "置信度: 98%" in html

    def test_page_idx_in_title(self):
        """有 page_idx 时 title 包含页码信息。"""
        block = {"type": "text", "text": "hello", "page_idx": 3}
        html = _render_block(block, 0)
        assert "页码: 3" in html


class TestRenderBlockAttributes:
    """测试 _render_block 生成的 div 属性。"""

    def test_data_block_index(self):
        """块有正确的 data-block-index 属性。"""
        block = {"type": "text", "text": "hello"}
        html = _render_block(block, 42)
        assert 'data-block-index="42"' in html

    def test_id_attribute(self):
        """块有正确的 id 属性。"""
        block = {"type": "text", "text": "hello"}
        html = _render_block(block, 7)
        assert 'id="block-7"' in html

    def test_border_color_per_type(self):
        """不同类型有不同边框颜色。"""
        block = {"type": "table", "text": "t"}
        html = _render_block(block, 0)
        assert "#22c55e" in html

    def test_ocr_block_class(self):
        """块有 ocr-block CSS 类。"""
        block = {"type": "text", "text": "hello"}
        html = _render_block(block, 0)
        assert 'class="ocr-block"' in html


class TestRenderTextBlock:
    """测试 _render_text 函数。"""

    def test_plain_text(self):
        block = {"text": "hello world"}
        html = _render_text(block, 0)
        assert "<p>hello world</p>" == html

    def test_html_escaped(self):
        block = {"text": "<script>alert('xss')</script>"}
        html = _render_text(block, 0)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


class TestRenderTitleBlock:
    """测试 _render_title 函数和 text 块 text_level 提升为标题。"""

    def test_title_via_text_level(self):
        """text 块有 text_level 字段时被渲染为标题。"""
        block = {"type": "text", "text_level": 2, "text": "Heading"}
        html = _render_block(block, 0)
        assert "<h2>Heading</h2>" in html

    def test_title_via_level(self):
        """title 块使用 level 字段。"""
        block = {"type": "title", "level": 1, "text": "Chapter"}
        html = _render_block(block, 0)
        assert "<h1>Chapter</h1>" in html

    def test_title_level_capped_at_6(self):
        """标题级别不超过 h6。"""
        block = {"type": "text", "text_level": 99, "text": "Deep"}
        html = _render_title(block, 0)
        assert "<h6>Deep</h6>" in html


class TestRenderTable:
    """测试 _render_table 函数。"""

    def test_table_body(self):
        block = {"table_body": "<table><tr><td>data</td></tr></table>"}
        html = _render_table(block, 0)
        assert 'class="ocr-table"' in html

    def test_table_with_caption_and_footnote(self):
        block = {
            "table_body": "<table><tr><td>d</td></tr></table>",
            "table_caption": ["My Table"],
            "table_footnote": ["Note 1"],
        }
        html = _render_table(block, 0)
        assert "My Table" in html
        assert "Note 1" in html

    def test_table_fallback_html_field(self):
        """兼容旧数据的 html 字段。"""
        block = {"type": "table", "html": "<table><tr><td>data</td></tr></table>"}
        html = _render_table(block, 0)
        assert 'class="ocr-table"' in html


class TestRenderEquation:
    """测试 _render_equation 函数。"""

    def test_equation_rendering(self):
        block = {"text": "E=mc^2"}
        html = _render_equation(block, 0)
        assert 'class="math-block"' in html
        assert "data-latex=" in html

    def test_latex_escaped_in_attribute(self):
        block = {"text": "a < b"}
        html = _render_equation(block, 0)
        assert 'data-latex="a &lt; b"' in html


class TestRenderList:
    """测试 _render_list 函数。"""

    def test_list_items(self):
        block = {"list_items": ["one", "two", "three"]}
        html = _render_list(block, 0)
        assert "<ul" in html
        assert "<li>one</li>" in html
        assert "<li>two</li>" in html
        assert "<li>three</li>" in html


class TestRenderCode:
    """测试 _render_code 函数。"""

    def test_code_with_sub_type(self):
        block = {"code_body": "print('hello')", "sub_type": "python"}
        html = _render_code(block, 0)
        assert "[python]" in html
        assert "print(&#x27;hello&#x27;)" in html or "print(&#39;hello&#39;)" in html

    def test_code_without_sub_type(self):
        block = {"code_body": "echo hi"}
        html = _render_code(block, 0)
        assert "[" not in html
        assert "echo hi" in html


class TestRenderFallback:
    """测试 _render_fallback 函数。"""

    def test_unknown_type_uses_fallback(self):
        block = {"type": "unknown_type", "text": "some text"}
        html = _render_block(block, 0)
        assert "some text" in html

    def test_empty_text_returns_empty(self):
        block = {}
        html = _render_fallback(block, 0)
        assert html == ""


class TestBorderColorLookup:
    """测试边框颜色和类型标签查找。"""

    def test_all_known_types_have_colors(self):
        for t in [
            "text",
            "title",
            "table",
            "image",
            "figure",
            "chart",
            "equation",
            "interline_equation",
            "inline_equation",
            "list",
            "code",
            "seal",
        ]:
            assert t in BLOCK_BORDER_COLORS

    def test_all_known_types_have_labels(self):
        for t in [
            "text",
            "title",
            "table",
            "image",
            "figure",
            "chart",
            "equation",
            "interline_equation",
            "inline_equation",
            "list",
            "code",
            "seal",
        ]:
            assert t in BLOCK_TYPE_LABELS
