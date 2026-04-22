"""Tests for result_view_widget HTML generation functions."""

from vibeocr.widgets.result_view_widget import _build_block_html, _build_text_blocks_html


class TestBuildBlockHtml:
    """测试 MinerU content_list 块的 HTML 生成。"""

    def test_title_attribute_with_type_and_confidence(self):
        """有类型和置信度时，title 包含两者。"""
        block = {"type": "text", "text": "hello", "confidence": 0.92}
        html = _build_block_html(block, 0)
        assert 'title="类型: 文本 | 置信度: 92%"' in html

    def test_title_attribute_type_only(self):
        """只有类型没有置信度时，title 只显示类型。"""
        block = {"type": "title", "text": "Chapter 1", "level": 1}
        html = _build_block_html(block, 1)
        assert 'title="类型: 标题"' in html

    def test_title_attribute_confidence_only(self):
        """只有置信度没有类型字段时，title 只显示置信度。"""
        block = {"text": "no type", "confidence": 0.75}
        html = _build_block_html(block, 2)
        assert 'title="置信度: 75%"' in html

    def test_no_title_attribute(self):
        """既没有类型也没有置信度时，不加 title。"""
        block = {"text": "plain text"}
        html = _build_block_html(block, 3)
        assert "title=" not in html

    def test_no_inline_confidence(self):
        """置信度信息不再内嵌显示。"""
        block = {"type": "text", "text": "hello", "confidence": 0.60}
        html = _build_block_html(block, 0)
        assert "置信度" not in html.replace('title="', "REMOVED")

    def test_table_block_title(self):
        """表格块显示类型标签。"""
        block = {"type": "table", "html": "<table><tr><td>data</td></tr></table>"}
        html = _build_block_html(block, 4)
        assert 'title="类型: 表格"' in html

    def test_equation_block_title(self):
        """公式块显示类型标签。"""
        block = {"type": "equation", "text": "E=mc^2"}
        html = _build_block_html(block, 5)
        assert "类型: 公式" in html

    def test_high_confidence_still_shows_in_title(self):
        """高置信度（>=0.95）也显示在 title 中。"""
        block = {"type": "text", "text": "confident", "confidence": 0.98}
        html = _build_block_html(block, 6)
        assert "置信度: 98%" in html


class TestBuildTextBlocksHtml:
    """测试 PaddleOCR text_blocks 的 HTML 生成。"""

    def test_title_attribute_with_score(self):
        """每个块都有 title 显示置信度。"""
        from vibeocr.models.ocr_result import TextBlock

        blocks = [
            TextBlock(text="hello", score=0.85, bbox=None),
            TextBlock(text="world", score=0.99, bbox=None),
        ]
        html = _build_text_blocks_html(blocks)
        assert 'title="置信度: 85%"' in html
        assert 'title="置信度: 99%"' in html

    def test_no_inline_confidence(self):
        """不再内嵌显示置信度。"""
        from vibeocr.models.ocr_result import TextBlock

        blocks = [TextBlock(text="low", score=0.50, bbox=None)]
        html = _build_text_blocks_html(blocks)
        assert "置信度" not in html.replace('title="', "REMOVED")
