"""Tests for result_view_widget block rendering functions."""

import re
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from vibeocr.widgets.result_view_widget import (
    BLOCK_BORDER_COLORS,
    BLOCK_TYPE_LABELS,
    _build_full_html,
    _render_block,
    _render_code,
    _render_equation,
    _render_fallback,
    _render_list,
    _render_table,
    _render_text,
    _render_title,
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
        assert html == "<p>hello world</p>"

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


class TestTableNormalizationInRender:
    """_render_table 应规整化表格：剥离 inline style、补齐空单元格。"""

    def test_strips_inline_style_on_render(self):
        """渲染时 PaddleX 自带的 style 属性应被剥离。"""
        block = {
            "table_body": (
                '<table><tr><td style="background:#eee">A</td>'
                '<th style="color:red">B</th></tr></table>'
            )
        }
        html = _render_table(block, 0)
        assert "style" not in html
        assert "<td>A</td>" in html

    def test_fills_missing_cells_on_render(self):
        """不规则行应在渲染时补齐为矩形，避免 Excel 粘贴错位。"""
        block = {
            "table_body": (
                "<table><tr><th>H1</th><th>H2</th></tr>"
                "<tr><td>only</td></tr></table>"
            )
        }
        html = _render_table(block, 0)
        assert "<td>only</td><td></td>" in html

    def test_no_zebra_stripe_in_css(self):
        """CSS 中不应有斑马纹/底纹（避免原生 copy 带样式）。"""
        from pathlib import Path

        html = _build_full_html("<p>x</p>", Path("resources/katex"))
        assert "nth-child(even)" not in html
        # th 不应有 background
        th_rule = re.search(r"\.ocr-table th\s*\{[^}]*\}", html)
        assert th_rule is None or "background" not in th_rule.group(0)


class TestTableCopyAndSelectionJS:
    """验证表格 copy 拦截与单元格拖选的 JS 已注入页面。"""

    def _full_html(self) -> str:
        from pathlib import Path

        return _build_full_html("<p>x</p>", Path("resources/katex"))

    def test_copy_interceptor_present(self):
        html = self._full_html()
        assert "addEventListener('copy'" in html
        assert "_tableSelToOutput" in html

    def test_cell_selection_js_present(self):
        html = self._full_html()
        assert "_startCellSelect" in html
        assert "_applyTableSelHighlight" in html
        assert "sel-cell" in html

    def test_copy_outputs_clean_html_marker(self):
        """copy 拦截器应输出无属性的 <table>/<td>（setData text/html）。"""
        html = self._full_html()
        assert "setData('text/html'" in html
        assert "setData('text/plain'" in html


class TestResultViewExportButtons:
    """结果区工具栏导出/复制按钮测试。"""

    @pytest.fixture
    def app(self, qtbot):
        return QApplication.instance() or QApplication([])

    @pytest.fixture
    def widget(self, app, qtbot):
        from vibeocr.widgets.result_view_widget import ResultViewWidget

        w = ResultViewWidget()
        qtbot.addWidget(w)
        return w

    def _make_result(self, markdown_text="# 标题\n\n正文段落", raw_text="标题\n正文段落"):
        return SimpleNamespace(
            content_list=[],
            markdown_text=markdown_text,
            raw_text=raw_text,
            html_text="",
            text_with_scores=[],
            images={},
        )

    @staticmethod
    def _fake_clipboard(monkeypatch):
        """注入一个可读写的假剪贴板（避免依赖 Windows COM 剪贴板可用性）。"""

        class FakeClipboard:
            def __init__(self):
                self._text = ""

            def setText(self, text):
                self._text = text

            def text(self):
                return self._text

        fake = FakeClipboard()

        from PySide6.QtGui import QGuiApplication

        monkeypatch.setattr(QGuiApplication, "clipboard", lambda *a, **k: fake)
        return fake

    def test_copy_markdown_to_clipboard(self, widget, qtbot, monkeypatch):
        """复制为 Markdown：剪贴板内容 == markdown_text。"""
        result = self._make_result(markdown_text="# H1\n内容")
        # 绕过 WebEngine 渲染，直接设 _current_result
        widget._current_result = result
        fake = self._fake_clipboard(monkeypatch)

        widget._on_copy_markdown()
        assert fake.text() == "# H1\n内容"

    def test_copy_markdown_falls_back_to_raw(self, widget, qtbot, monkeypatch):
        """无 markdown_text 时回退到 raw_text。"""
        result = self._make_result(markdown_text="", raw_text="纯文本")
        widget._current_result = result
        fake = self._fake_clipboard(monkeypatch)

        widget._on_copy_markdown()
        assert fake.text() == "纯文本"

    def test_copy_markdown_no_result_is_noop(self, widget, qtbot, monkeypatch):
        """无结果时不报错、不写剪贴板。"""
        widget._current_result = None
        fake = self._fake_clipboard(monkeypatch)

        fake.setText("SENTINEL")
        widget._on_copy_markdown()
        assert fake.text() == "SENTINEL"

    def test_buttons_hidden_initially(self, widget):
        """初始（无结果）三个新按钮隐藏。

        用 isHidden() 而非 isVisible()：父窗口从未 show()，isVisible() 恒为
        False（弱断言）；isHidden() 仅在显式 hide() 后为 True，能真正验证
        _setup_ui 里的 btn.hide() 生效。
        """
        assert widget._copy_md_btn.isHidden() is True
        assert widget._export_docx_btn.isHidden() is True
        assert widget._export_xlsx_btn.isHidden() is True

    def test_export_docx_creates_file(self, widget, qtbot, monkeypatch, tmp_path):
        """导出 Word：mock 另存为对话框，断言生成 .docx 文件。"""
        result = self._make_result(raw_text="导出测试内容")
        widget._current_result = result

        out = tmp_path / "out.docx"
        # mock QFileDialog.getSaveFileName 返回 (路径, 过滤)
        monkeypatch.setattr(
            "vibeocr.widgets.result_view_widget.QFileDialog",
            type("F", (), {"getSaveFileName": staticmethod(lambda *a, **k: (str(out), ""))}),
            raising=False,
        )
        # mock QMessageBox 避免弹窗阻塞
        monkeypatch.setattr(
            "vibeocr.widgets.result_view_widget.QMessageBox",
            type(
                "M",
                (),
                {
                    "information": staticmethod(lambda *a, **k: None),
                    "warning": staticmethod(lambda *a, **k: None),
                },
            ),
            raising=False,
        )
        widget._on_export_file("docx")
        assert out.exists()
        # docx 是 zip 包，文件头 PK
        assert out.read_bytes()[:2] == b"PK"

    def test_export_xlsx_creates_file(self, widget, qtbot, monkeypatch, tmp_path):
        """导出 Excel：断言生成 .xlsx 文件。"""
        result = self._make_result(raw_text="表格导出测试")
        widget._current_result = result

        out = tmp_path / "out.xlsx"
        monkeypatch.setattr(
            "vibeocr.widgets.result_view_widget.QFileDialog",
            type("F", (), {"getSaveFileName": staticmethod(lambda *a, **k: (str(out), ""))}),
            raising=False,
        )
        monkeypatch.setattr(
            "vibeocr.widgets.result_view_widget.QMessageBox",
            type(
                "M",
                (),
                {
                    "information": staticmethod(lambda *a, **k: None),
                    "warning": staticmethod(lambda *a, **k: None),
                },
            ),
            raising=False,
        )
        widget._on_export_file("xlsx")
        assert out.exists()
        # xlsx 也是 zip 包
        assert out.read_bytes()[:2] == b"PK"

    def test_export_cancel_is_noop(self, widget, qtbot, monkeypatch, tmp_path):
        """用户取消对话框（返回空路径）不报错、不生成文件。"""
        result = self._make_result(raw_text="取消测试")
        widget._current_result = result

        monkeypatch.setattr(
            "vibeocr.widgets.result_view_widget.QFileDialog",
            type("F", (), {"getSaveFileName": staticmethod(lambda *a, **k: ("", ""))}),
            raising=False,
        )
        out = tmp_path / "should_not_exist.docx"
        widget._on_export_file("docx")
        assert not out.exists()

    def test_export_no_result_is_noop(self, widget, qtbot):
        """无结果时导出不报错。"""
        widget._current_result = None
        widget._on_export_file("docx")  # 不应抛异常
