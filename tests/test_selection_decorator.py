"""Tests for SelectionDecorator — handle positions and resize calculations."""

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor

from vibeocr.widgets.editor.annotation_items import RectAnnotation
from vibeocr.widgets.editor.selection_decorator import SelectionDecorator


def _make_rect_item(x=0, y=0, w=100, h=80) -> RectAnnotation:
    return RectAnnotation(QRectF(x, y, w, h), pen_color=QColor(255, 0, 0))


class TestHandlePositions:
    def test_handle_positions_at_rect_edges(self, qapp):
        item = _make_rect_item(10, 20, 100, 80)
        dec = SelectionDecorator(item)
        handles = dec.handle_positions(QRectF(10, 20, 100, 80))
        assert len(handles) == 8
        assert handles[0] == QPointF(10, 20)
        assert handles[2] == QPointF(110, 20)
        assert handles[5] == QPointF(10, 100)
        assert handles[7] == QPointF(110, 100)
        assert handles[1] == QPointF(60, 20)
        assert handles[3] == QPointF(10, 60)
        assert handles[4] == QPointF(110, 60)
        assert handles[6] == QPointF(60, 100)

    def test_handle_at_origin(self, qapp):
        item = _make_rect_item(0, 0, 50, 50)
        dec = SelectionDecorator(item)
        handles = dec.handle_positions(QRectF(0, 0, 50, 50))
        assert handles[0] == QPointF(0, 0)
        assert handles[7] == QPointF(50, 50)


class TestResizeCalculation:
    def test_top_left_handle_resize(self, qapp):
        item = _make_rect_item(10, 10, 100, 80)
        dec = SelectionDecorator(item)
        original = QRectF(10, 10, 100, 80)
        new_rect = dec.calculate_resize(0, QPointF(20, 30), original)
        assert new_rect.topLeft() == QPointF(20, 30)
        assert new_rect.bottomRight() == QPointF(110, 90)
        assert new_rect.width() == 90
        assert new_rect.height() == 60

    def test_bottom_right_handle_resize(self, qapp):
        item = _make_rect_item(10, 10, 100, 80)
        dec = SelectionDecorator(item)
        original = QRectF(10, 10, 100, 80)
        new_rect = dec.calculate_resize(7, QPointF(150, 120), original)
        assert new_rect.topLeft() == QPointF(10, 10)
        assert new_rect.bottomRight() == QPointF(150, 120)

    def test_top_center_handle_only_changes_height(self, qapp):
        item = _make_rect_item(10, 10, 100, 80)
        dec = SelectionDecorator(item)
        original = QRectF(10, 10, 100, 80)
        new_rect = dec.calculate_resize(1, QPointF(60, 30), original)
        assert new_rect.left() == 10
        assert new_rect.width() == 100
        assert new_rect.top() == 30
        assert new_rect.bottom() == 90

    def test_middle_right_handle_only_changes_width(self, qapp):
        item = _make_rect_item(10, 10, 100, 80)
        dec = SelectionDecorator(item)
        original = QRectF(10, 10, 100, 80)
        new_rect = dec.calculate_resize(4, QPointF(150, 50), original)
        assert new_rect.top() == 10
        assert new_rect.height() == 80
        assert new_rect.right() == 150
        assert new_rect.left() == 10

    def test_min_size_enforced(self, qapp):
        item = _make_rect_item(10, 10, 100, 80)
        dec = SelectionDecorator(item)
        original = QRectF(10, 10, 100, 80)
        new_rect = dec.calculate_resize(7, QPointF(12, 12), original)
        assert new_rect.width() >= 10
        assert new_rect.height() >= 10

    def test_handle_hit_detection(self, qapp):
        item = _make_rect_item(0, 0, 100, 80)
        dec = SelectionDecorator(item)
        handles = dec.handle_positions(QRectF(0, 0, 100, 80))
        hit = dec.hit_test(QPointF(0, 0), handles)
        assert hit == 0
        hit = dec.hit_test(QPointF(100, 80), handles)
        assert hit == 7
        hit = dec.hit_test(QPointF(50, 40), handles)
        assert hit == -1
