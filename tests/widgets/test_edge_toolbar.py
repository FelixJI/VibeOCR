# tests/widgets/test_edge_toolbar.py
"""Tests for EdgeToolbar (桌面边缘隐身悬浮操作栏)."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from vibeocr.widgets.toolbar import EdgeToolbar


class TestEdgeToolbar:
    def test_is_widget(self, qapp):
        tb = EdgeToolbar()
        assert isinstance(tb, QWidget)

    def test_styled_background_enabled(self, qapp):
        """浅色背景依赖 WA_StyledBackground：否则背景透明，样式表 background-color 失效。"""
        tb = EdgeToolbar()
        assert tb.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)

    def test_stylesheet_has_light_background(self, qapp):
        """样式表须指定浅色背景（#fff），用于边缘隐身时的可见性与可读性。"""
        tb = EdgeToolbar()
        assert "#fff" in tb.styleSheet()
