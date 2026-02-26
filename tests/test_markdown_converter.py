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


class TestMarkdownToHtmlWithIndent:
    """测试 Markdown 转 HTML 的集成功能"""

    def test_chinese_paragraph_has_indent_class(self):
        """测试中文段落有 zh-paragraph 类"""
        from vibeocr.utils.markdown_converter import markdown_to_html
        html = markdown_to_html("这是中文段落")
        # 检查 body 内容中是否有 zh-paragraph div
        assert '<div class="zh-paragraph">' in html

    def test_english_paragraph_no_indent_class(self):
        """测试英文段落没有 zh-paragraph 类"""
        from vibeocr.utils.markdown_converter import markdown_to_html
        html = markdown_to_html("This is English paragraph")
        # 检查 body 内容中是否有 zh-paragraph div（排除 style 中的 CSS 类定义）
        assert '<div class="zh-paragraph">' not in html

    def test_nested_list_structure(self):
        """测试嵌套列表结构"""
        from vibeocr.utils.markdown_converter import markdown_to_html
        markdown = "- 一级\n  - 二级\n    - 三级"
        html = markdown_to_html(markdown)
        # 验证嵌套结构存在
        assert '<ul>' in html
        assert '</ul>' in html

    def test_latex_not_affected(self):
        """测试 LaTeX 公式不受影响"""
        from vibeocr.utils.markdown_converter import markdown_to_html
        markdown = "$$E=mc^2$$"
        html = markdown_to_html(markdown)
        assert 'latex-formula' in html

    def test_table_not_affected(self):
        """测试表格不受影响"""
        from vibeocr.utils.markdown_converter import markdown_to_html
        markdown = "| A | B |\n|---|---|\n| 1 | 2 |"
        html = markdown_to_html(markdown)
        assert '<table>' in html
