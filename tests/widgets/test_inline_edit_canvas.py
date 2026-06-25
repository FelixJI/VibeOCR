"""Tests for InlineEditCanvas."""

from unittest.mock import MagicMock

from PySide6.QtCore import QPointF, QRect, QRectF
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QGraphicsRectItem

from vibeocr.widgets.inline_edit_canvas import InlineEditCanvas


def _make_pixmap(
    w: int, h: int, color: QColor | None = None, dpr: float = 1.0
) -> QPixmap:
    px = QPixmap(int(w * dpr), int(h * dpr))
    if color:
        px.fill(color)
    else:
        px.fill(QColor(128, 128, 128))
    px.setDevicePixelRatio(dpr)
    return px


def _make_mapper(dpr: float = 1.0, virtual_geometry: QRect | None = None) -> MagicMock:
    mapper = MagicMock()
    mapper.dpr_at.return_value = dpr
    mapper.virtual_geometry = virtual_geometry or QRect(0, 0, 9999, 9999)
    mapper.clip_to_virtual.side_effect = lambda r: r
    return mapper


class TestInlineEditCanvas:
    def test_initial_state(self, qapp):
        canvas = InlineEditCanvas()
        assert canvas._background_pixmap is None

    def test_set_background(self, qapp):
        canvas = InlineEditCanvas()
        pixmap = QPixmap(200, 100)
        pixmap.fill()
        canvas.set_background(pixmap)
        assert canvas._background_pixmap is not None
        assert canvas._background_item is not None

    def test_export_image(self, qapp):
        canvas = InlineEditCanvas()
        pixmap = QPixmap(200, 100)
        pixmap.fill()
        canvas.set_background(pixmap)
        exported = canvas.export_image()
        assert not exported.isNull()

    def test_export_without_background(self, qapp):
        canvas = InlineEditCanvas()
        exported = canvas.export_image()
        assert exported.isNull()

    def test_undo_stack_exists(self, qapp):
        canvas = InlineEditCanvas()
        assert canvas.undo_stack is not None


class TestUpdateCropRegion:
    def test_background_updates_to_new_region(self, qapp):
        canvas = InlineEditCanvas()

        # 原始屏幕 1000x800
        screen_pxm = _make_pixmap(1000, 800, QColor(100, 100, 100), dpr=1.0)

        # 初始裁剪区域 (100, 100, 300x200)
        initial_sel = QRect(100, 100, 300, 200)
        cropped = screen_pxm.copy(initial_sel)
        canvas.set_background(cropped)

        old_scene_rect = canvas._scene.sceneRect()
        assert old_scene_rect == QRectF(0, 0, 300, 200)

        # 更新到新裁剪区域 (50, 50, 400x300)
        new_sel = QRect(50, 50, 400, 300)
        canvas.update_crop_region(screen_pxm, new_sel, _make_mapper())

        new_scene_rect = canvas._scene.sceneRect()
        assert new_scene_rect == QRectF(0, 0, 400, 300)

    def test_annotations_translated_by_delta(self, qapp):
        canvas = InlineEditCanvas()
        screen_pxm = _make_pixmap(1000, 800, dpr=1.0)

        initial_sel = QRect(100, 100, 300, 200)
        cropped = screen_pxm.copy(initial_sel)
        canvas.set_background(cropped, crop_origin=QPointF(100, 100))

        # 在场景坐标 (50, 30) 添加一个矩形标注
        annotation = QGraphicsRectItem(QRectF(50, 30, 60, 40))
        canvas._scene.addItem(annotation)

        # 裁剪区域移动了 (20, 10)，即 new_sel 左上角从 (100,100) 变为 (120,110)
        # 标注应该平移 -(20, 10) = (-20, -10) 以保持屏幕绝对位置
        new_sel = QRect(120, 110, 300, 200)
        canvas.update_crop_region(screen_pxm, new_sel, _make_mapper())

        # 原场景坐标 (50, 30) → 新场景坐标 (30, 20)
        assert annotation.pos().x() == -20.0
        assert annotation.pos().y() == -10.0


class TestFillProperties:
    def test_default_fill_linked(self, qapp):
        canvas = InlineEditCanvas()
        assert canvas._fill_linked is True

    def test_default_fill_opacity(self, qapp):
        canvas = InlineEditCanvas()
        assert canvas._fill_opacity == 20

    def test_default_fill_color_follows_pen(self, qapp):
        canvas = InlineEditCanvas()
        assert canvas._fill_color.red() == canvas._pen_color.red()
        assert canvas._fill_color.green() == canvas._pen_color.green()
        assert canvas._fill_color.blue() == canvas._pen_color.blue()

    def test_set_pen_color_syncs_fill_when_linked(self, qapp):
        canvas = InlineEditCanvas()
        canvas.set_pen_color(QColor(0, 255, 0))
        assert canvas._fill_color.red() == 0
        assert canvas._fill_color.green() == 255
        assert canvas._fill_color.blue() == 0

    def test_set_pen_color_no_sync_when_unlinked(self, qapp):
        canvas = InlineEditCanvas()
        canvas.set_fill_linked(False)
        canvas.set_fill_color(QColor(0, 0, 255))
        canvas.set_pen_color(QColor(0, 255, 0))
        assert canvas._fill_color.red() == 0
        assert canvas._fill_color.green() == 0
        assert canvas._fill_color.blue() == 255

    def test_set_fill_linked_syncs_to_pen_color(self, qapp):
        canvas = InlineEditCanvas()
        canvas.set_fill_linked(False)
        canvas.set_fill_color(QColor(0, 0, 255))
        canvas.set_pen_color(QColor(0, 255, 0))
        canvas.set_fill_linked(True)
        assert canvas._fill_color.red() == 0
        assert canvas._fill_color.green() == 255
        assert canvas._fill_color.blue() == 0

    def test_set_fill_opacity(self, qapp):
        canvas = InlineEditCanvas()
        canvas.set_fill_opacity(80)
        assert canvas._fill_opacity == 80
