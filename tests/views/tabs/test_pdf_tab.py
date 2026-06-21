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
        """状态列表应包在 QScrollArea 中，多页文字不被截断。"""
        scrolls = pdf_tab.findChildren(QScrollArea)
        assert len(scrolls) >= 1
        # 其中至少一个 ScrollArea 的内容是 _layer_status_list
        owns_list = any(
            s.widget() is pdf_tab._layer_status_list for s in scrolls
        )
        assert owns_list

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
        row_text = pdf_tab._layer_status_list.item(0).text()
        assert "第1页" in row_text
        assert "已添加文字层" in row_text
        assert "12 个文本块" in row_text
        # 旧的误导措辞不应再出现
        assert "层文字层" not in row_text
        doc.close()

    def test_status_list_row_count_matches_pages(self, pdf_tab):
        """状态列表行数应等于页数，每行携带 page_index。"""
        import fitz

        from PySide6.QtCore import Qt

        from vibeocr.models.pdf_document import PdfDocument, PdfPageInfo
        from vibeocr.models.pdf_session import PdfSession

        pages = [PdfPageInfo(page_index=i) for i in range(4)]
        doc = fitz.open()
        for _ in range(4):
            doc.new_page()
        pdf_doc = PdfDocument(file_path="x.pdf", pages=pages)
        session = PdfSession(file_path="x.pdf", doc=doc, pdf_document=pdf_doc)
        pdf_tab._session_mgr._sessions["x.pdf"] = session
        pdf_tab._session_mgr._active_path = "x.pdf"

        pdf_tab._update_layer_status()
        assert pdf_tab._layer_status_list.count() == 4
        for i in range(4):
            item = pdf_tab._layer_status_list.item(i)
            assert item.data(Qt.ItemDataRole.UserRole) == i
        doc.close()


class TestPdfTabLayerStatusLinkage:
    """点击状态行应联动左侧缩略图与内嵌预览。"""

    def _setup_session(self, pdf_tab):
        import fitz

        from vibeocr.models.pdf_document import (
            PdfDocument,
            PdfPageInfo,
            TextLayerInfo,
        )
        from vibeocr.models.pdf_session import PdfSession

        pages = [
            PdfPageInfo(
                page_index=2,
                has_text_layer=True,
                text_layers=[
                    TextLayerInfo(
                        index=0,
                        text_preview="t",
                        char_count=1,
                        bbox=(50.0, 50.0, 300.0, 100.0),
                        color_id=0,
                    )
                ],
            ),
            PdfPageInfo(page_index=0),
            PdfPageInfo(page_index=1),
        ]
        doc = fitz.open()
        for _ in range(3):
            doc.new_page()
        pdf_doc = PdfDocument(file_path="x.pdf", pages=pages)
        session = PdfSession(file_path="x.pdf", doc=doc, pdf_document=pdf_doc)
        pdf_tab._session_mgr._sessions["x.pdf"] = session
        pdf_tab._session_mgr._active_path = "x.pdf"
        pdf_tab._refresh_thumbnails()
        pdf_tab._update_layer_status()
        return doc

    def test_click_status_row_selects_thumbnail(self, pdf_tab):
        """点击状态列表第 K 行 → 缩略图列表选中对应 page_index 的行。"""
        doc = self._setup_session(pdf_tab)
        try:
            # 状态列表第 0 行的 page_index=2 → 缩略图中 page_index=2 的行
            from PySide6.QtCore import Qt

            status_item = pdf_tab._layer_status_list.item(0)
            page_idx = status_item.data(Qt.ItemDataRole.UserRole)
            assert page_idx == 2

            pdf_tab._on_layer_status_clicked(status_item)
            selected = pdf_tab._get_selected_page_indices()
            assert selected == [2]
        finally:
            doc.close()

    def test_click_status_row_refreshes_preview(self, pdf_tab, monkeypatch):
        """点击状态行 → 调用 _show_embedded_preview_for_page 刷新内嵌预览。"""
        doc = self._setup_session(pdf_tab)
        try:
            called = []
            monkeypatch.setattr(
                pdf_tab,
                "_show_embedded_preview_for_page",
                lambda idx: called.append(idx),
            )
            status_item = pdf_tab._layer_status_list.item(0)
            pdf_tab._on_layer_status_clicked(status_item)
            assert called == [2]
        finally:
            doc.close()

    def test_thumbnail_selection_syncs_status_list(self, pdf_tab):
        """缩略图选中变化 → 状态列表当前行同步（反向联动）。"""
        doc = self._setup_session(pdf_tab)
        try:
            from PySide6.QtCore import Qt

            # 在缩略图列表里找到 page_index=1 的行并选中
            target_row = None
            for row in range(pdf_tab._thumbnail_list.count()):
                item = pdf_tab._thumbnail_list.item(row)
                if item.data(Qt.ItemDataRole.UserRole) == 1:
                    target_row = row
                    break
            assert target_row is not None
            pdf_tab._thumbnail_list.setCurrentRow(target_row)

            # 状态列表中应选中 page_index=1 对应的行
            cur = pdf_tab._layer_status_list.currentItem()
            assert cur is not None
            assert cur.data(Qt.ItemDataRole.UserRole) == 1
        finally:
            doc.close()


class TestPdfTabOcrCompletion:
    def test_completion_summary_with_skips(self, pdf_tab, monkeypatch):
        """skipped>0 时应弹出 information 提示含“成功 N 块 / 跳过 K 块”。"""
        import vibeocr.views.tabs.pdf_tab as mod

        called = []
        monkeypatch.setattr(
            mod.QMessageBox, "information", lambda *a, **k: called.append(a)
        )
        monkeypatch.setattr(
            pdf_tab, "_show_embedded_preview", lambda: None
        )
        pdf_tab._session_mgr.ocr_stats_ready.emit("sid", 5, 2)
        assert len(called) == 1
        msg = called[0][2]
        assert "成功写入 5 块" in msg
        assert "跳过 2 块" in msg

    def test_completion_no_skip_sets_status_label(self, pdf_tab, monkeypatch):
        """skipped==0 时不弹框，只在状态栏轻量提示。"""
        import vibeocr.views.tabs.pdf_tab as mod

        called = []
        monkeypatch.setattr(
            mod.QMessageBox, "information", lambda *a, **k: called.append(a)
        )
        monkeypatch.setattr(
            pdf_tab, "_show_embedded_preview", lambda: None
        )
        pdf_tab._session_mgr.ocr_stats_ready.emit("sid", 3, 0)
        assert called == []
        assert "文字层已添加" in pdf_tab._status_label.text()
        assert "3 块" in pdf_tab._status_label.text()

    def test_completion_nothing_written_does_not_claim_added(
        self, pdf_tab, monkeypatch
    ):
        """written==0 且 skipped==0 时不应误报“已添加”。"""
        import vibeocr.views.tabs.pdf_tab as mod

        called = []
        monkeypatch.setattr(
            mod.QMessageBox, "information", lambda *a, **k: called.append(a)
        )
        monkeypatch.setattr(
            pdf_tab, "_show_embedded_preview", lambda: None
        )
        pdf_tab._session_mgr.ocr_stats_ready.emit("sid", 0, 0)
        assert called == []
        text = pdf_tab._status_label.text()
        assert "已添加" not in text
        assert "未添加" in text

    def test_show_embedded_preview_populates_canvas(
        self, pdf_tab, monkeypatch, tmp_path
    ):
        """_show_embedded_preview 应把页面渲染进内嵌画布（E2E：problem #3）。"""
        import fitz

        from vibeocr.models.pdf_document import (
            PdfDocument,
            PdfPageInfo,
            TextLayerInfo,
        )
        from vibeocr.models.pdf_session import PdfSession

        # 构造带文字层（1 个文本块）的真实 PDF + session
        page_info = PdfPageInfo(
            page_index=0,
            has_text_layer=True,
            text_layers=[
                TextLayerInfo(
                    index=0,
                    text_preview="测试",
                    char_count=2,
                    bbox=(50.0, 50.0, 300.0, 100.0),
                    color_id=0,
                )
            ],
        )
        doc = fitz.open()
        doc.new_page()
        pdf_doc = PdfDocument(file_path="x.pdf", pages=[page_info])
        session = PdfSession(file_path="x.pdf", doc=doc, pdf_document=pdf_doc)
        pdf_tab._session_mgr._sessions["x.pdf"] = session
        pdf_tab._session_mgr._active_path = "x.pdf"

        pdf_tab._show_embedded_preview()

        # 画布应已接收 pixmap 且高亮层已设置
        assert pdf_tab._preview_canvas._pixmap is not None
        assert pdf_tab._preview_canvas._pixmap.width() > 0
        assert pdf_tab._preview_canvas._highlight_layers == page_info.text_layers
        assert pdf_tab._preview_canvas._render_dpi == 150
        assert pdf_tab._preview_canvas._source == "pdf"
        doc.close()


class TestAddTextLayerForPagesWithoutLayer:
    """新按钮：一键为当前文件所有无文字层页添加文字层。"""

    def _inject_session(self, pdf_tab, doc, pdf_doc):
        from vibeocr.models.pdf_session import PdfSession

        session = PdfSession(file_path="x.pdf", doc=doc, pdf_document=pdf_doc)
        pdf_tab._session_mgr._sessions["x.pdf"] = session
        pdf_tab._session_mgr._active_path = "x.pdf"
        return session

    def test_button_exists(self, pdf_tab):
        btn = getattr(pdf_tab, "_btn_add_text_layer_no_layer", None)
        assert btn is not None
        assert "无文字层" in btn.text()

    def test_all_have_layer_shows_info(self, pdf_tab, monkeypatch):
        """所有页都有文字层时点击按钮应弹 information 提示，不启动 OCR。"""
        import fitz

        from vibeocr.models.pdf_document import PdfDocument, PdfPageInfo

        page_info = PdfPageInfo(page_index=0, has_text_layer=True)
        doc = fitz.open()
        doc.new_page()
        pdf_doc = PdfDocument(file_path="x.pdf", pages=[page_info])
        self._inject_session(pdf_tab, doc, pdf_doc)

        called = {"info": False, "start": False}
        import vibeocr.views.tabs.pdf_tab as mod

        monkeypatch.setattr(
            mod.QMessageBox, "information", lambda *a, **k: called.__setitem__("info", True)
        )
        monkeypatch.setattr(
            pdf_tab._session_mgr, "start_ocr",
            lambda *a, **k: called.__setitem__("start", True),
        )

        pdf_tab._on_add_text_layer_for_pages_without_layer()

        assert called["info"] is True
        assert called["start"] is False
        doc.close()

    def test_no_active_session_returns_silently(self, pdf_tab, monkeypatch):
        """未打开文件时点击按钮应静默返回（不报错、不弹框）。"""
        called = {"info": False}
        import vibeocr.views.tabs.pdf_tab as mod

        monkeypatch.setattr(
            mod.QMessageBox, "information", lambda *a, **k: called.__setitem__("info", True)
        )
        pdf_tab._on_add_text_layer_for_pages_without_layer()
        assert called["info"] is False
