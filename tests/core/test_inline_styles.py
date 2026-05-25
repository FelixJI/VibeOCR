# tests/test_inline_styles.py
"""Tests for InlineStyles."""

from vibeocr.core.inline_styles import InlineStyles


class TestInlineStylesColors:
    def test_panel_background(self):
        assert InlineStyles.PANEL_BG.startswith("rgba")

    def test_panel_border(self):
        assert InlineStyles.PANEL_BORDER.startswith("rgba")

    def test_button_icon_colors(self):
        assert InlineStyles.BUTTON_BG.startswith("rgba")
        assert InlineStyles.BUTTON_HOVER.startswith("rgba")

    def test_confirm_button_color(self):
        assert InlineStyles.CONFIRM_BG.startswith("#")

    def test_selection_border_color(self):
        assert InlineStyles.SELECTION_BORDER.startswith("#")


class TestInlineStylesMethods:
    def test_panel_style_returns_css(self):
        style = InlineStyles.panel_style()
        assert "QWidget" in style
        assert "background-color" in style

    def test_tool_button_style_returns_css(self):
        style = InlineStyles.tool_button_style()
        assert "QToolButton" in style
        assert ":hover" in style
        assert ":checked" in style

    def test_recognition_button_style_returns_css(self):
        style = InlineStyles.recognition_button_style()
        assert "QPushButton" in style

    def test_action_button_style_returns_css(self):
        style = InlineStyles.action_button_style()
        assert "QPushButton" in style

    def test_confirm_button_style_returns_css(self):
        style = InlineStyles.confirm_button_style()
        assert "QPushButton" in style
        assert "font-weight: bold" in style
