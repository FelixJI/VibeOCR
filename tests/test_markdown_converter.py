"""测试 Markdown 转换器"""

import pytest


class TestCSSStyles:
    """测试 CSS 样式"""

    def test_css_contains_chinese_indent_style(self):
        """测试 CSS 包含中文段落缩进样式"""
        from vibeocr.utils.markdown_converter import HTML_STYLE
        assert '.zh-paragraph' in HTML_STYLE
        assert 'text-indent' in HTML_STYLE

    def test_css_contains_list_indent_style(self):
        """测试 CSS 包含列表嵌套缩进样式"""
        from vibeocr.utils.markdown_converter import HTML_STYLE
        assert 'margin-left' in HTML_STYLE
        assert 'li p' in HTML_STYLE
