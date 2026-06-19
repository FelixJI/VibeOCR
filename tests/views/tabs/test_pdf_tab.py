"""PdfTab UI 结构测试。"""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidget, QScrollArea, QSplitter

from vibeocr.views.tabs.pdf_tab import PdfTab


@pytest.fixture
def pdf_tab(qtbot):
    tab = PdfTab()
    qtbot.addWidget(tab)
    return tab


class TestPdfTabStructure:
    def test_has_two_nested_splitters(self, pdf_tab):
        splitters = pdf_tab.findChildren(QSplitter)
        horiz = [
            s for s in splitters
            if s.orientation() == Qt.Orientation.Horizontal
        ]
        vert = [
            s for s in splitters
            if s.orientation() == Qt.Orientation.Vertical
        ]
        assert len(horiz) >= 1, "应有横向主 splitter"
        assert len(vert) >= 1, "应有纵向右侧 splitter"

    def test_splitters_are_not_collapsible(self, pdf_tab):
        """setChildrenCollapsible(False) 应阻止用户把子部件拖没。"""
        # QSplitter 没有公开 childrenCollapsible() getter，验证间接效果：
        # 即便把尺寸拖到极端，子部件最小宽度仍 > 0（不会被折叠为 0）。
        pdf_tab._main_splitter.setSizes([1, 9999])
        sizes = pdf_tab._main_splitter.sizes()
        # 两个子部件都应保留非零尺寸（不可折叠）
        assert all(s > 0 for s in sizes)

    def test_thumbnail_list_has_no_fixed_width(self, pdf_tab):
        """缩略图列表不应被 setFixedWidth 钉死，否则 splitter 不可拖。"""
        lst = pdf_tab.findChild(QListWidget)
        assert lst is not None
        # 被 setFixedWidth 时 maximumWidth == minimumWidth == 200；
        # 现在只设了 minimumWidth(120)，maximumWidth 应保持默认大值。
        assert lst.maximumWidth() > 300

    def test_layer_status_in_scroll_area(self, pdf_tab):
        """状态标签应包在 QScrollArea 中，多页文字不被截断。"""
        scrolls = pdf_tab.findChildren(QScrollArea)
        assert len(scrolls) >= 1
        # 其中至少一个 ScrollArea 的内容是 _layer_status_label
        owns_label = any(
            s.widget() is pdf_tab._layer_status_label for s in scrolls
        )
        assert owns_label

    def test_embedded_preview_canvas_exists(self, pdf_tab):
        """PdfTab 应内嵌一个 PreviewCanvas 供完成后自动预览。"""
        from vibeocr.views.pdf_preview_window import PreviewCanvas

        assert isinstance(pdf_tab._preview_canvas, PreviewCanvas)

    def test_splitter_save_is_debounced(self, pdf_tab, monkeypatch):
        """splitterMoved 不应立即落盘，而是重启防抖定时器。"""
        calls = []
        monkeypatch.setattr(
            pdf_tab, "_persist_splitter_state", lambda: calls.append(1)
        )
        # 连续触发多次 splitterMoved（模拟拖动）
        for _ in range(5):
            pdf_tab._save_splitter_state()
        # 定时器未到期前不落盘
        assert calls == []
        # 触发定时器到期 → 仅落盘一次
        pdf_tab._splitter_save_timer.timeout.emit()
        assert calls == [1]


class TestPdfTabLayerStatus:
    def test_status_wording_for_text_layer(self, pdf_tab, tmp_path, monkeypatch):
        """_update_layer_status 对有文字层的页应输出“已添加文字层(N 个文本块)”。"""
        import fitz

        from vibeocr.models.pdf_document import (
            PdfDocument,
            PdfPageInfo,
            TextLayerInfo,
        )
        from vibeocr.models.pdf_session import PdfSession

        page_info = PdfPageInfo(
            page_index=0,
            has_text_layer=True,
            text_layers=[
                TextLayerInfo(
                    index=i,
                    text_preview="t",
                    char_count=1,
                    bbox=(0.0, 0.0, 1.0, 1.0),
                    color_id=i,
                )
                for i in range(12)
            ],
        )
        doc = fitz.open()
        doc.new_page()
        pdf_doc = PdfDocument(file_path="x.pdf", pages=[page_info])
        session = PdfSession(file_path="x.pdf", doc=doc, pdf_document=pdf_doc)
        # active_session 是只读 property（读 _active_path + _sessions），直接注入底层字段
        pdf_tab._session_mgr._sessions["x.pdf"] = session
        pdf_tab._session_mgr._active_path = "x.pdf"

        pdf_tab._update_layer_status()
        text = pdf_tab._layer_status_label.text()
        assert "第1页" in text
        assert "已添加文字层" in text
        assert "12 个文本块" in text
        # 旧的误导措辞不应再出现
        assert "层文字层" not in text
        doc.close()
