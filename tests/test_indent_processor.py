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
