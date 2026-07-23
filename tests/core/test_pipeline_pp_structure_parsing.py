"""pipeline_pp_structure.py 数据处理纯函数测试。

pipeline_pp_structure.py 仅 15% 覆盖。聚焦 _html_table_to_markdown /
_extract_table_html / _build_ocr_result / _consume_generator_safely 这些
HTML 表格解析与结果构建纯逻辑。
"""

from __future__ import annotations

import gc

import pytest

from vibeocr.core.pipelines.pipeline_pp_structure import (
    PPStructureV3Options,
    _build_ocr_result,
    _consume_generator_safely,
    _extract_table_html,
    _html_table_to_markdown,
)


class TestExtractTableHtml:
    """_extract_table_html：从 HTML 提取第一个 <table>。"""

    def test_extracts_table_from_wrapper(self):
        html = '<div><table><tr><td>A</td></tr></table></div>'
        result = _extract_table_html(html)
        assert result == "<table><tr><td>A</td></tr></table>"

    def test_returns_input_when_no_table(self):
        """无 table 标签时返回原字符串"""
        html = "<div>no table here</div>"
        assert _extract_table_html(html) == html

    def test_case_insensitive(self):
        html = '<TABLE><TR><TD>X</TD></TR></TABLE>'
        result = _extract_table_html(f"<p>{html}</p>")
        assert "<TABLE>" in result


class TestHtmlTableToMarkdown:
    """_html_table_to_markdown：HTML 表格 → Markdown。"""

    def test_simple_table_with_header_and_body(self):
        html = "<table><tr><th>H1</th><th>H2</th></tr><tr><td>a</td><td>b</td></tr></table>"
        md = _html_table_to_markdown(html)
        lines = md.split("\n")
        assert lines[0] == "| H1 | H2 |"
        assert lines[1] == "| --- | --- |"
        assert lines[2] == "| a | b |"

    def test_pipe_character_escaped(self):
        """单元格内的 | 应被转义为 \\|"""
        html = "<table><tr><td>a|b</td></tr></table>"
        md = _html_table_to_markdown(html)
        assert r"a\|b" in md

    def test_uneven_columns_padded(self):
        """列数不齐时补空列"""
        html = "<table><tr><td>1</td><td>2</td></tr><tr><td>3</td></tr></table>"
        md = _html_table_to_markdown(html)
        # 第二行应补一列空
        body_line = md.split("\n")[2]
        assert body_line == "| 3 |  |"

    def test_empty_table_returns_empty(self):
        """无有效行时返回空串"""
        assert _html_table_to_markdown("<table></table>") == ""
        assert _html_table_to_markdown("") == ""

    def test_strips_inner_html_tags(self):
        """单元格内的 HTML 标签被剥离"""
        html = "<table><tr><td><b>bold</b></td></tr></table>"
        md = _html_table_to_markdown(html)
        assert "bold" in md
        assert "<b>" not in md

    def test_single_row_only_header(self):
        """只有一行（作为表头）→ 有 header+sep 无 body"""
        html = "<table><tr><td>only</td></tr></table>"
        md = _html_table_to_markdown(html)
        lines = md.split("\n")
        assert lines[0] == "| only |"
        assert lines[1] == "| --- |"


class TestBuildOcrResult:
    """_build_ocr_result（PP-Structure 版）。"""

    def test_minimal(self):
        result = _build_ocr_result("text")
        assert result.raw_text == "text"
        assert result.pipeline_type == "PP-StructureV3"
        assert result.avg_score == 0.0

    def test_with_scores_and_low_confidence(self):
        result = _build_ocr_result(
            "t", text_with_scores=[("ok", 0.9), ("low", 0.3)]
        )
        assert result.avg_score == pytest.approx(0.6)
        assert len(result.low_confidence_items) == 1


class TestConsumeGeneratorSafely:
    """_consume_generator_safely（PP-Structure 版，逻辑同 pipeline_ocr）。"""

    def test_consumes_generator(self):
        def gen():
            yield 1
            yield 2

        assert _consume_generator_safely(gen()) == [1, 2]

    def test_exception_returns_empty(self):
        def bad_gen():
            yield 1
            raise ValueError("err")

        assert _consume_generator_safely(bad_gen()) == []

    def test_gc_reenabled(self):
        gc.enable()
        _consume_generator_safely(iter([1]))
        assert gc.isenabled()


class TestPPStructureV3Options:
    """PPStructureV3Options dataclass 默认值。"""

    def test_defaults(self):
        opts = PPStructureV3Options()
        assert opts.pipeline == "PP-StructureV3"
        assert opts.use_table_recognition is True
        assert opts.use_formula_recognition is True
        assert opts.use_seal_recognition is False
        assert opts.use_chart_recognition is False
