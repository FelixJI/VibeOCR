"""PreviewWidget 统一预览组件测试"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QScrollArea

from vibeocr.models.ocr_result import TextBlock
from vibeocr.widgets.preview_widget import PreviewWidget


class TestPreviewWidgetBasic:
    def test_creation(self, qapp):
        widget = PreviewWidget()
        assert widget._pixmap is None

    def test_set_pixmap(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        assert widget._pixmap is not None

    def test_original_pixmap_after_set(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        assert widget.original_pixmap() is not None
        assert not widget.original_pixmap().isNull()

    def test_original_pixmap_none_after_clear(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget.clear()
        assert widget.original_pixmap() is None

    def test_clear(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget.clear()
        assert widget._pixmap is None
        assert widget._text_blocks == []

    def test_custom_empty_text(self, qapp):
        widget = PreviewWidget(empty_text="自定义文案")
        assert widget._empty_text == "自定义文案"


class TestPreviewWidgetTextBlocks:
    def test_set_text_blocks(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        blocks = [
            TextBlock(text="Hello", score=0.95, bbox=(10, 20, 200, 50)),
            TextBlock(text="World", score=0.60, bbox=(10, 60, 200, 90)),
        ]
        widget.set_text_blocks(blocks)
        assert widget._text_blocks == blocks

    def test_set_content_list(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        content = [
            {"type": "text", "text": "Hello", "bbox": [10, 20, 200, 50]},
            {"type": "table", "text": "data", "bbox": [10, 60, 200, 90]},
        ]
        widget.set_content_list(content)
        assert widget._content_list == content


class TestPreviewWidgetFileLoading:
    def test_load_image_file(self, qapp, temp_image_file):
        widget = PreviewWidget()
        widget.load_file(str(temp_image_file))
        assert widget._original_pixmap is not None
        assert widget._total_pages == 1

    def test_has_scroll_area(self, qapp):
        widget = PreviewWidget()
        scroll_areas = widget.findChildren(QScrollArea)
        assert len(scroll_areas) >= 1


class TestPreviewWidgetHighlights:
    def test_highlight_block_no_crash(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget.highlight_block(0)
        widget.highlight_block(-1)

    def test_clear_highlight(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget.clear_highlight()
        assert widget._highlight_block_index == -1

    def test_highlight_block_with_content_list(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        content = [
            {"type": "text", "text": "Hello", "bbox": [10, 20, 200, 50]},
        ]
        widget.set_content_list(content)
        widget.highlight_block(0)
        assert widget._highlight_block_index == 0


class TestPreviewWidgetSignals:
    """信号相关测试（原 tests/test_preview_widget.py）"""

    def test_click_without_pixmap_emits_signal(self, qapp, qtbot):
        """无图片时点击触发 screenshot_requested 信号。"""
        widget = PreviewWidget()
        widget.show()
        qtbot.addWidget(widget)

        with qtbot.waitSignal(widget.screenshot_requested, timeout=1000):

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


class TestPreviewWidgetTableHitTest:
    """块类型模式下的表格块双击命中测试与表格编辑信号。

    _start_table_edit 会弹出模态 QDialog（exec()），无法在 headless 测试中
    完整驱动，因此这里聚焦可单测的命中逻辑与信号机制。
    """

    def test_hit_test_type_block_hits_table(self, qapp, sample_pixmap):
        """_hit_test_type_block 应命中预设的表格矩形并返回 block_type。"""
        from PySide6.QtCore import QRectF

        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        # 直接构造命中矩形，避免依赖布局时序（_update_type_overlay 需有效尺寸）
        widget._type_screen_rects = [
            (0, QRectF(10, 10, 100, 80), "table"),
            (1, QRectF(200, 10, 100, 50), "text"),
        ]
        # 命中表格区域
        cl_idx, block_type = widget._hit_test_type_block(50, 40)
        assert cl_idx == 0
        assert block_type == "table"
        # 命中文本区域
        cl_idx, block_type = widget._hit_test_type_block(230, 30)
        assert cl_idx == 1
        assert block_type == "text"

    def test_hit_test_type_block_miss(self, qapp, sample_pixmap):
        from PySide6.QtCore import QRectF

        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget._type_screen_rects = [(0, QRectF(10, 10, 50, 50), "table")]
        cl_idx, block_type = widget._hit_test_type_block(500, 500)
        assert cl_idx == -1
        assert block_type == ""

    def test_find_text_block_by_content_index(self, qapp, sample_pixmap):
        """_find_text_block_by_content_index 按 content_index 反查 text_blocks。"""
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        blocks = [
            TextBlock(text="A", score=0.9, bbox=None, content_index=0),
            TextBlock(text="B", score=0.9, bbox=None, content_index=2),
        ]
        widget.set_text_blocks(blocks)
        assert widget._find_text_block_by_content_index(2) == 1
        assert widget._find_text_block_by_content_index(0) == 0
        assert widget._find_text_block_by_content_index(99) == -1
        assert widget._find_text_block_by_content_index(-1) == -1

    def test_table_text_edited_signal_exists(self, qapp):
        """table_text_edited 信号应可正常 emit（验证信号已定义且签名正确）。"""
        widget = PreviewWidget()
        received: list[tuple[int, str]] = []
        widget.table_text_edited.connect(lambda i, h: received.append((i, h)))
        widget.table_text_edited.emit(3, "<table></table>")
        assert received == [(3, "<table></table>")]

    def test_type_screen_rects_cleared_on_set_pixmap(self, qapp, sample_pixmap):
        """切换图片时 _type_screen_rects 应被重置，避免残留命中数据。"""
        from PySide6.QtCore import QRectF

        widget = PreviewWidget()
        widget._type_screen_rects = [(0, QRectF(0, 0, 10, 10), "table")]
        widget.set_pixmap(sample_pixmap)
        assert widget._type_screen_rects == []
