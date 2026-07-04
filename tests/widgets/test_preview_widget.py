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


class TestConfidenceModeTableDoubleClick:
    """置信度模式下双击表格块（label=='table'）应走表格网格编辑器，
    而非把原始 HTML 塞进 QLineEdit 内联编辑器。
    """

    @staticmethod
    def _pos(x: int, y: int):
        """构造带 x()/y() 方法的 pos 桩（_on_label_double_click 调用 pos.x()）。"""

        class _P:
            def x(self):
                return x

            def y(self):
                return y

        return _P()

    def test_table_block_routes_to_grid_editor(self, qapp, sample_pixmap):
        """label=='table' 的置信度块双击应调用 _start_table_edit(content_index)。"""
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        # 表格管道：text_block.text 是原始 HTML、label=='table'、content_index 指向
        # content_list 中带 table_body 的表格块。
        widget._text_blocks = [
            TextBlock(
                text="<table><tr><td>x</td></tr></table>",
                score=0.9,
                bbox=(10, 10, 200, 80),
                label="table",
                content_index=0,
            )
        ]
        widget._block_screen_rects = [(10, 10, 190, 70)]
        widget._content_list = [
            {"type": "table", "table_body": "<table><tr><td>x</td></tr></table>"}
        ]

        called: list[int] = []
        widget._start_table_edit = lambda ci: called.append(ci)
        widget._start_inline_edit = lambda idx: called.append(("inline", idx))

        # 双击落在表格 bbox 内（置信度模式命中）
        widget._on_label_double_click(self._pos(50, 40))
        assert called == [0], "应调用 _start_table_edit(0)，而非 _start_inline_edit"

    def test_text_block_routes_to_inline_edit(self, qapp, sample_pixmap):
        """label!='table' 的置信度块双击仍走 _start_inline_edit。"""
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget._text_blocks = [
            TextBlock(text="普通文本", score=0.9, bbox=(10, 10, 200, 80), label="text")
        ]
        widget._block_screen_rects = [(10, 10, 190, 70)]

        called: list = []
        widget._start_table_edit = lambda ci: called.append(("table", ci))
        widget._start_inline_edit = lambda idx: called.append(("inline", idx))

        widget._on_label_double_click(self._pos(50, 40))
        assert called == [("inline", 0)]


class TestDoubleClickOriginalImage:
    """双击空白区域（未命中任何 bbox）应打开原图查看器，而非静默忽略。"""

    @staticmethod
    def _pos(x: int, y: int):
        class _P:
            def x(self):
                return x

            def y(self):
                return y

        return _P()

    def test_empty_area_opens_viewer(self, qapp, sample_pixmap, monkeypatch):
        """无任何 text_block / content_list 时，双击图片区域应调用 _show_original_image。"""
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)

        called = []
        widget._show_original_image = lambda: called.append(True)

        widget._on_label_double_click(self._pos(50, 40))
        assert called == [True]

    def test_bbox_hit_does_not_open_viewer(self, qapp, sample_pixmap):
        """双击命中 bbox 时不应打开原图查看器。"""
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget._text_blocks = [
            TextBlock(text="Hello", score=0.9, bbox=(10, 10, 200, 80), label="text")
        ]
        widget._block_screen_rects = [(10, 10, 190, 70)]

        viewer_called = []
        widget._show_original_image = lambda: viewer_called.append(True)
        widget._start_inline_edit = lambda idx: None

        widget._on_label_double_click(self._pos(50, 40))
        assert viewer_called == [], "命中 bbox 时不应打开原图查看器"

    def test_content_list_hit_does_not_open_viewer(self, qapp, sample_pixmap):
        """双击命中 content_list 块时不应打开原图查看器。"""
        from PySide6.QtCore import QRectF

        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget._content_list = [{"type": "text", "text": "Hello"}]
        widget._type_screen_rects = [(0, QRectF(10, 10, 190, 70), "text")]

        viewer_called = []
        widget._show_original_image = lambda: viewer_called.append(True)
        widget._start_inline_edit = lambda idx: None

        widget._on_label_double_click(self._pos(50, 40))
        assert viewer_called == [], "命中 content_list 块时不应打开原图查看器"


class TestTableEditNoChangeNoSignal:
    """表格网格编辑器：用户未改动单元格内容时不应触发 table_text_edited
    （不应误标记 manually-edited 使 bbox 变黄）。
    """

    def test_unchanged_cells_no_signal(self, qapp, sample_pixmap, monkeypatch):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        # PaddleX 风格的 table_body（含 inline style 等噪声）
        table_body = '<table><tr><td style="color:red">A</td><td>B</td></tr></table>'
        widget._content_list = [{"type": "table", "table_body": table_body}]

        # parse_table_html_to_grid 解析出的网格
        grid = [["A", "B"]]

        # 拦截 ocr_service 内的解析/序列化函数
        import vibeocr.services.ocr_service as ocr_svc

        monkeypatch.setattr(ocr_svc, "parse_table_html_to_grid", lambda h: grid)
        monkeypatch.setattr(
            ocr_svc,
            "grid_to_table_html",
            lambda g: "<table><tr><td>A</td><td>B</td></tr></table>",
        )
        # 让对话框直接返回 Accepted（不弹窗）
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QDialog

        monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
        # 隔离 QSettings：模拟首次无历史几何，且不写真实注册表/磁盘。
        monkeypatch.setattr(QSettings, "value", lambda *a, **k: None)
        monkeypatch.setattr(QSettings, "setValue", lambda *a, **k: None)

        emitted: list = []
        widget.table_text_edited.connect(lambda i, h: emitted.append((i, h)))

        widget._start_table_edit(0)
        # 单元格内容未变（grid == [["A","B"]]，QTableWidget 初始也是 A/B）
        # → 不应 emit
        assert emitted == []


class TestTableEditGeometryPersistence:
    """编辑表格对话框尺寸持久化：打开时恢复上次几何，关闭时保存。"""

    def _make_widget(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget._content_list = [
            {"type": "table", "table_body": "<table><tr><td>A</td></tr></table>"}
        ]
        import vibeocr.services.ocr_service as ocr_svc

        ocr_svc.parse_table_html_to_grid = lambda h: [["A"]]
        ocr_svc.grid_to_table_html = lambda g: "<table><tr><td>A</td></tr></table>"
        return widget

    def test_restore_geometry_applied(self, qapp, sample_pixmap, monkeypatch):
        """QSettings 存在历史几何时，应调用 restoreGeometry 恢复。"""
        from PySide6.QtCore import QByteArray
        from PySide6.QtWidgets import QDialog

        widget = self._make_widget(qapp, sample_pixmap)

        # 一份真实的几何字节（非空），让 restoreGeometry 返回 True。
        saved_geom = QByteArray(b"\x01\x00\x00\x00\x00\x00")
        monkeypatch.setattr(
            "vibeocr.widgets.preview_widget.QSettings.value",
            lambda self, key, *a, **k: saved_geom,
        )
        restored: list = []
        monkeypatch.setattr(
            QDialog, "restoreGeometry", lambda self, g: restored.append(g) or True
        )
        monkeypatch.setattr(
            "vibeocr.widgets.preview_widget.QSettings.setValue", lambda *a, **k: None
        )
        monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected)

        widget._start_table_edit(0)
        assert restored and restored[0] is saved_geom

    def test_save_geometry_on_close(self, qapp, sample_pixmap, monkeypatch):
        """对话框关闭后（无论确认/取消）应把几何写入 QSettings。"""
        from PySide6.QtWidgets import QDialog

        widget = self._make_widget(qapp, sample_pixmap)

        saved: list = []
        monkeypatch.setattr(
            "vibeocr.widgets.preview_widget.QSettings.value", lambda *a, **k: None
        )
        monkeypatch.setattr(
            "vibeocr.widgets.preview_widget.QSettings.setValue",
            lambda self, key, val: saved.append((key, val)),
        )
        monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected)

        widget._start_table_edit(0)
        # 取消时也保存
        keys = [k for k, _ in saved]
        assert "table_edit_dialog/geometry" in keys


def _pos(x: int, y: int):
    """构造带 x()/y() 方法的 pos 桩（_on_mouse_move / _on_block_*_click 调用 pos.x()）。"""

    class _P:
        def x(self):
            return x

        def y(self):
            return y

    return _P()


class TestTooltipConfidenceDisplay:
    """左侧置信度模式 tooltip：表格/图片/公式等占位 score 块应显示"无置信度"，
    而非误导性的百分比（如表格 score=0.9 显示"90%"）。普通文本块保留真实百分比。
    """

    def test_table_block_shows_no_confidence(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget._text_blocks = [
            TextBlock(
                text="<table></table>", score=0.9, bbox=(10, 10, 200, 80), label="table"
            )
        ]
        widget._block_screen_rects = [(10, 10, 190, 70)]

        widget._on_mouse_move(_pos(50, 40))
        assert "无置信度" in widget._image_label.toolTip()
        assert "90%" not in widget._image_label.toolTip()

    def test_formula_block_shows_no_confidence(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget._text_blocks = [
            TextBlock(text="E=mc^2", score=1.0, bbox=(10, 10, 200, 80), label="formula")
        ]
        widget._block_screen_rects = [(10, 10, 190, 70)]

        widget._on_mouse_move(_pos(50, 40))
        assert "无置信度" in widget._image_label.toolTip()
        assert "100%" not in widget._image_label.toolTip()

    def test_text_block_shows_real_confidence(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget._text_blocks = [
            TextBlock(text="普通文本", score=0.92, bbox=(10, 10, 200, 80), label="text")
        ]
        widget._block_screen_rects = [(10, 10, 190, 70)]

        widget._on_mouse_move(_pos(50, 40))
        tip = widget._image_label.toolTip()
        assert "92.0%" in tip
        assert "无置信度" not in tip

    def test_edited_flag_still_appended(self, qapp, sample_pixmap):
        """手动修改标记 [手动修改] 应继续追加在 tooltip 末尾。"""
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget._text_blocks = [
            TextBlock(
                text="<table></table>",
                score=0.9,
                bbox=(10, 10, 200, 80),
                label="table",
                is_manually_edited=True,
            )
        ]
        widget._block_screen_rects = [(10, 10, 190, 70)]

        widget._on_mouse_move(_pos(50, 40))
        tip = widget._image_label.toolTip()
        assert "无置信度" in tip
        assert "[手动修改]" in tip


class TestTooltipBlockTypeMode:
    """块类型模式（表格/公式管道实际渲染模式）下悬停 tooltip 应能命中并显示。

    回归：表格管道左侧在块类型模式渲染，置信度命中测试（_hit_test_block）恒返回
    -1，导致 tooltip 完全不出现。_on_mouse_move 现增加块类型模式回退。
    """

    def test_table_block_tooltip_in_block_type_mode(self, qapp, sample_pixmap):
        from PySide6.QtCore import QRectF

        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        # 块类型模式：表格管道的数据
        widget._content_list = [
            {"type": "table", "table_body": "<table></table>", "bbox": [0, 0, 1, 1]}
        ]
        widget._type_screen_rects = [(0, QRectF(10, 10, 190, 70), "table")]
        widget._text_blocks = [
            TextBlock(
                text="<table></table>",
                score=0.9,
                bbox=(10, 10, 200, 80),
                label="table",
                content_index=0,
            )
        ]
        widget._block_screen_rects = []  # 块类型模式：置信度命中矩形为空

        widget._on_mouse_move(_pos(50, 40))
        tip = widget._image_label.toolTip()
        assert "无置信度" in tip
        assert "90%" not in tip

    def test_formula_block_tooltip_in_block_type_mode(self, qapp, sample_pixmap):
        from PySide6.QtCore import QRectF

        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget._content_list = [{"type": "formula", "text": "E=mc^2", "bbox": [0, 0, 1, 1]}]
        widget._type_screen_rects = [(0, QRectF(10, 10, 190, 70), "formula")]
        widget._text_blocks = [
            TextBlock(
                text="E=mc^2", score=1.0, bbox=(10, 10, 200, 80), label="formula",
                content_index=0,
            )
        ]
        widget._block_screen_rects = []

        widget._on_mouse_move(_pos(50, 40))
        tip = widget._image_label.toolTip()
        assert "无置信度" in tip
        assert "100%" not in tip

    def test_no_tooltip_when_miss(self, qapp, sample_pixmap):
        from PySide6.QtCore import QRectF

        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget._content_list = [{"type": "table", "table_body": "<table></table>"}]
        widget._type_screen_rects = [(0, QRectF(10, 10, 50, 50), "table")]
        widget._text_blocks = []
        widget._block_screen_rects = []

        # 鼠标在矩形外
        widget._on_mouse_move(_pos(500, 500))
        assert widget._image_label.toolTip() == ""


class TestLegendModifiedEntry:
    """右上角图例：存在手动修改块时追加"修改后"橙色项；无则不追加。"""

    def test_legend_includes_modified_when_edited_present(self, qapp):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor

        from vibeocr.widgets.preview_widget import (
            BLOCK_BORDER_COLORS,
            EDIT_BORDER,
            UnifiedBBoxOverlay,
        )

        overlay = UnifiedBBoxOverlay()
        overlay._mode = "block_type"
        overlay._type_rects = [
            (
                0,
                QRectF(0, 0, 10, 10),
                "text",
                QColor(0, 0, 0),
                BLOCK_BORDER_COLORS["text"],
                None,
            )
        ]
        # 置信度模式数据：第 7 项（index 6）is_manually_edited = True
        overlay._conf_rects = [(0.0, 0.0, 10.0, 10.0, 0.9, "x", True)]

        labels = [lbl for lbl, _ in overlay._legend_entries()]
        assert "文本" in labels
        assert "修改后" in labels
        # "修改后"对应橙色 EDIT_BORDER
        edited = [c for lbl, c in overlay._legend_entries() if lbl == "修改后"]
        assert edited and edited[0].red() == EDIT_BORDER.red()
        assert edited[0].green() == EDIT_BORDER.green()

    def test_legend_excludes_modified_when_no_edit(self, qapp):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor

        from vibeocr.widgets.preview_widget import (
            BLOCK_BORDER_COLORS,
            UnifiedBBoxOverlay,
        )

        overlay = UnifiedBBoxOverlay()
        overlay._mode = "block_type"
        overlay._type_rects = [
            (
                0,
                QRectF(0, 0, 10, 10),
                "table",
                QColor(0, 0, 0),
                BLOCK_BORDER_COLORS["table"],
                None,
            )
        ]
        overlay._conf_rects = []  # 无修改块

        labels = [lbl for lbl, _ in overlay._legend_entries()]
        assert "表格" in labels
        assert "修改后" not in labels

    def test_formula_legend_uses_orange(self, qapp):
        """PaddleX 公式管道（type=formula）应在图例中显示橙色"公式"。"""
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor

        from vibeocr.widgets.preview_widget import (
            BLOCK_BORDER_COLORS,
            UnifiedBBoxOverlay,
        )

        overlay = UnifiedBBoxOverlay()
        overlay._mode = "block_type"
        overlay._type_rects = [
            (
                0,
                QRectF(0, 0, 10, 10),
                "formula",
                QColor(0, 0, 0),
                BLOCK_BORDER_COLORS["formula"],
                None,
            )
        ]
        entries = overlay._legend_entries()
        labels = [lbl for lbl, _ in entries]
        assert "公式" in labels
        formula_color = [c for lbl, c in entries if lbl == "公式"][0]
        # 橙色 ~ (249, 115, 22)，而非文本蓝 (59, 130, 246)
        assert formula_color.red() > 200
        assert formula_color.green() < 150




