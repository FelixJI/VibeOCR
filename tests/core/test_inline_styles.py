# tests/core/test_inline_styles.py
"""Tests for InlineStyles."""

from vibeocr.core.inline_styles import InlineStyles


class TestInlineStylesColors:
    def test_panel_background(self):
        assert InlineStyles.PANEL_BG.startswith("#")

    def test_panel_border(self):
        assert InlineStyles.PANEL_BORDER.startswith("#")

    def test_button_hover_color(self):
        assert InlineStyles.BUTTON_HOVER.startswith("#")

    def test_button_pressed_color(self):
        assert InlineStyles.BUTTON_PRESSED.startswith("#")

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

    def test_action_button_style_returns_css(self):
        style = InlineStyles.action_button_style()
        assert "QToolButton" in style
        assert ":hover" in style

    def test_confirm_button_style_returns_css(self):
        style = InlineStyles.confirm_button_style()
        assert "QToolButton" in style

    def test_cancel_button_style_returns_css(self):
        style = InlineStyles.cancel_button_style()
        assert "QToolButton" in style
        assert ":hover" in style

    def test_recognition_button_style_returns_css(self):
        style = InlineStyles.recognition_button_style()
        assert "QPushButton" in style

    def test_properties_panel_style_returns_css(self):
        style = InlineStyles.properties_panel_style()
        assert "QWidget#propsPanel" in style
