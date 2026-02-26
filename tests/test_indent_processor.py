# tests/test_indent_processor.py
import pytest
from vibeocr.utils.indent_processor import IndentConfig, IndentProcessor


class TestIndentConfig:
    def test_default_values(self):
        config = IndentConfig()
        assert config.chinese_indent == "2em"
        assert config.chinese_threshold == 0.05
