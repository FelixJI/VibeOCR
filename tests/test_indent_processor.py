# tests/test_indent_processor.py
import pytest
from vibeocr.utils.indent_processor import IndentConfig, IndentProcessor


class TestIndentConfig:
    def test_default_values(self):
        config = IndentConfig()
        assert config.chinese_indent == "2em"
        assert config.chinese_threshold == 0.05


class TestIsChineseText:
    @pytest.fixture
    def processor(self):
        return IndentProcessor()

    def test_pure_chinese(self, processor):
        assert processor.is_chinese_text("这是中文段落") is True

    def test_pure_english(self, processor):
        assert processor.is_chinese_text("This is English paragraph") is False

    def test_mixed_above_threshold(self, processor):
        # "中文内容" 4个中文字符，总长度约20，占比20%>5%
        assert processor.is_chinese_text("这是一些中文和 some English") is True

    def test_mixed_below_threshold(self, processor):
        # 1个中文字符，总长度约30，占比约3%<5%
        assert processor.is_chinese_text("This is a long English paragraph 中 end") is False

    def test_empty_string(self, processor):
        assert processor.is_chinese_text("") is False

    def test_whitespace_only(self, processor):
        assert processor.is_chinese_text("   ") is False

    def test_boundary_exactly_5_percent(self, processor):
        # 5个中文字符，总长度100，占比正好5%
        text = "一二三四五" + "a" * 95
        assert processor.is_chinese_text(text) is True


class TestProcessMarkdown:
    @pytest.fixture
    def processor(self):
        return IndentProcessor()

    def test_chinese_paragraph_wrapped(self, processor):
        markdown = "这是中文段落"
        result = processor.process_markdown(markdown)
        assert '<div class="zh-paragraph">' in result
        assert "这是中文段落" in result

    def test_english_paragraph_not_wrapped(self, processor):
        markdown = "This is English"
        result = processor.process_markdown(markdown)
        assert '<div class="zh-paragraph">' not in result
        assert result == "This is English"

    def test_multiple_paragraphs(self, processor):
        markdown = "中文段落\n\nEnglish paragraph"
        result = processor.process_markdown(markdown)
        assert '<div class="zh-paragraph">中文段落</div>' in result
        assert "English paragraph" in result

    def test_preserves_code_blocks(self, processor):
        markdown = "```\ncode here\n```"
        result = processor.process_markdown(markdown)
        assert '<div class="zh-paragraph">' not in result

    def test_preserves_tables(self, processor):
        markdown = "| 列1 | 列2 |\n|---|---|\n| 值1 | 值2 |"
        result = processor.process_markdown(markdown)
        # 表格不应被包装
        assert result == markdown

    def test_empty_input(self, processor):
        assert processor.process_markdown("") == ""

    def test_list_items_not_wrapped(self, processor):
        markdown = "- 列表项1\n- 列表项2"
        result = processor.process_markdown(markdown)
        # 列表项不应被包装为段落
        assert '<div class="zh-paragraph">' not in result
