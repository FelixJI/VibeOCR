"""Tests for annotation items — MosaicItem and BlurItem update_background."""

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QPixmap

from vibeocr.widgets.editor.annotation_items import BlurItem, MosaicItem


def _make_pixmap(w: int = 200, h: int = 200) -> QPixmap:
    px = QPixmap(w, h)
    px.fill(QColor(100, 150, 200))
    return px


class TestMosaicItemUpdateBackground:
    def test_update_background_regenerates(self, qapp):
        bg = _make_pixmap()
        item = MosaicItem(QRectF(10, 10, 80, 80), bg, strength=8)

        new_bg = _make_pixmap(300, 300)
        new_bg.fill(QColor(200, 50, 50))
        item.update_background(new_bg)

        assert item._background_pixmap is new_bg
        assert item._cached_mosaic is not None


class TestBlurItemUpdateBackground:
    def test_update_background_regenerates(self, qapp):
        bg = _make_pixmap()
        item = BlurItem(QRectF(10, 10, 80, 80), bg, radius=10)

        new_bg = _make_pixmap(300, 300)
        new_bg.fill(QColor(200, 50, 50))
        item.update_background(new_bg)

        assert item._background_pixmap is new_bg
        assert item._cached_blur is not None
