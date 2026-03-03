"""Tests for PreviewWidget."""

from PySide6.QtCore import Qt

from vibeocr.widgets.preview_widget import PreviewWidget


class TestPreviewWidget:
    """测试 PreviewWidget 组件。"""

    def test_set_pixmap(self, qapp, sample_pixmap):
        """设置图片后正确显示。"""
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)

        assert widget.pixmap() is not None
        assert widget.pixmap().width() == 100
        assert widget.pixmap().height() == 50

    def test_clear_pixmap(self, qapp, sample_pixmap):
        """清除后恢复默认状态。"""
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget.clear()

        assert widget.pixmap() is None

    def test_click_without_pixmap_emits_signal(self, qapp, qtbot):
        """无图片时点击触发 screenshot_requested 信号。"""
        widget = PreviewWidget()
        widget.show()
        qtbot.addWidget(widget)

        # 直接调用 _on_label_click 方法（因为 mousePressEvent 被替换了）
        with qtbot.waitSignal(widget.screenshot_requested, timeout=1000):
            # 创建模拟事件
            class MockEvent:
                def button(self):
                    return Qt.MouseButton.LeftButton

            widget._on_label_click(MockEvent())

    def test_click_with_pixmap_no_signal(self, qapp, sample_pixmap, qtbot):
        """有图片时点击不触发信号。"""
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget.show()
        qtbot.addWidget(widget)

        # 使用 assertNotSignal 检查信号不被触发
        with qtbot.assertNotEmitted(widget.screenshot_requested, wait=100):

            class MockEvent:
                def button(self):
                    return Qt.MouseButton.LeftButton

            widget._on_label_click(MockEvent())

    def test_image_changed_signal_on_set(self, qapp, sample_pixmap, qtbot):
        """设置图片时发送 image_changed 信号。"""
        widget = PreviewWidget()
        qtbot.addWidget(widget)

        with qtbot.waitSignal(widget.image_changed, timeout=1000):
            widget.set_pixmap(sample_pixmap)

    def test_image_changed_signal_on_clear(self, qapp, sample_pixmap, qtbot):
        """清除图片时发送 image_changed 信号。"""
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        qtbot.addWidget(widget)

        with qtbot.waitSignal(widget.image_changed, timeout=1000):
            widget.clear()
