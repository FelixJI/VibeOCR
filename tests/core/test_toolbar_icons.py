# tests/test_toolbar_icons.py
"""Tests for toolbar icon rendering."""

import pytest
from PySide6.QtGui import QIcon

from vibeocr.ui.toolbar_icons import ICON_NAMES, toolbar_icon


class TestToolbarIcon:
    def test_returns_qicon(self, qapp):
        icon = toolbar_icon("mosaic")
        assert isinstance(icon, QIcon)

    def test_icon_not_null(self, qapp):
        icon = toolbar_icon("mosaic")
        assert not icon.isNull()

    def test_icon_custom_size(self, qapp):
        icon = toolbar_icon("mosaic", size=32)
        assert not icon.isNull()

    def test_all_icon_names_render(self, qapp):
        for name in ICON_NAMES:
            icon = toolbar_icon(name)
            assert not icon.isNull(), f"Icon '{name}' failed to render"

    def test_unknown_icon_raises(self, qapp):
        with pytest.raises(KeyError):
            toolbar_icon("nonexistent")
