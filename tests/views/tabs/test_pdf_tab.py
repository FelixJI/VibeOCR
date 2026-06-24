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
    def test_has_only_main_horizontal_splitter(self, pdf_tab):
        """改造后只有主水平 splitter，不再有右侧垂直 splitter。"""
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
        assert len(vert) == 0, "不应再有右侧垂直 splitter"

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
        """状态网格应包在 QScrollArea 中，多页不被截断。"""
        scrolls = pdf_tab.findChildren(QScrollArea)
        assert len(scrolls) >= 1
        # 其中至少一个 ScrollArea 的内容是 _layer_status_grid
        owns_list = any(
            s.widget() is pdf_tab._layer_status_grid for s in scrolls
        )
        assert owns_list

    def test_no_embedded_preview_canvas(self, pdf_tab):
        """内嵌预览画布应已移除。"""
        assert not hasattr(pdf_tab, "_preview_canvas")
        assert not hasattr(pdf_tab, "_right_splitter")

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
        tip = pdf_tab._layer_status_grid.item(0).toolTip()
        assert "第1页" in tip
        assert "已添加文字层" in tip
        assert "12个文本块" in tip
        doc.close()

    def test_status_list_row_count_matches_pages(self, pdf_tab):
        """状态网格格子数应等于页数，每个携带 page_index。"""
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
        assert pdf_tab._layer_status_grid.count() == 4
        for i in range(4):
            item = pdf_tab._layer_status_grid.item(i)
            assert item.data(Qt.ItemDataRole.UserRole) == i
        doc.close()


class TestPdfTabLayerStatusLinkage:
    """网格 ↔ 缩略图双向选中同步（按 page_index 匹配，重入保护防递归）。"""

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
                        index=0, text_preview="t", char_count=1,
                        bbox=(50.0, 50.0, 300.0, 100.0), color_id=0,
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

    def test_grid_selection_syncs_to_thumbnail(self, pdf_tab):
        """网格选中 → 缩略图选中相同 page_index。"""
        from PySide6.QtCore import QItemSelectionModel

        doc = self._setup_session(pdf_tab)
        try:
            grid = pdf_tab._layer_status_grid
            for row in range(grid.count()):
                if grid.item(row).data(Qt.ItemDataRole.UserRole) == 1:
                    grid.selectionModel().select(
                        grid.model().index(row, 0), QItemSelectionModel.ClearAndSelect
                    )
                    break
            selected = pdf_tab._get_selected_page_indices()
            assert selected == [1]
        finally:
            doc.close()

    def test_thumbnail_selection_syncs_to_grid(self, pdf_tab):
        """缩略图选中 → 网格选中相同 page_index。"""
        from PySide6.QtCore import QItemSelectionModel

        doc = self._setup_session(pdf_tab)
        try:
            lst = pdf_tab._thumbnail_list
            for row in range(lst.count()):
                if lst.item(row).data(Qt.ItemDataRole.UserRole) == 2:
                    lst.selectionModel().select(
                        lst.model().index(row, 0), QItemSelectionModel.ClearAndSelect
                    )
                    break
            grid = pdf_tab._layer_status_grid
            cur = grid.selectedItems()
            assert len(cur) == 1
            assert cur[0].data(Qt.ItemDataRole.UserRole) == 2
        finally:
            doc.close()

    def test_no_infinite_recursion_on_sync(self, pdf_tab):
        """双向同步不应触发递归（_syncing_selection 保护）。"""
        from PySide6.QtCore import QItemSelectionModel

        doc = self._setup_session(pdf_tab)
        try:
            grid = pdf_tab._layer_status_grid
            lst = pdf_tab._thumbnail_list
            # 反复交替触发，不应崩溃/栈溢出
            for _ in range(5):
                grid.selectionModel().select(
                    grid.model().index(0, 0), QItemSelectionModel.ClearAndSelect
                )
                lst.selectionModel().select(
                    lst.model().index(0, 0), QItemSelectionModel.ClearAndSelect
                )
            # 无 RecursionError 即通过
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
        pdf_tab._session_mgr.ocr_stats_ready.emit("sid", 0, 0)
        assert called == []
        text = pdf_tab._status_label.text()
        assert "已添加" not in text
        assert "未添加" in text


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


class TestAddTextLayerSoftGuard:
    """现有"添加文字层"按钮：选中页含已有文字层时弹三选一框。"""

    def _inject(self, pdf_tab, pages):
        import fitz

        from vibeocr.models.pdf_document import PdfDocument
        from vibeocr.models.pdf_session import PdfSession

        doc = fitz.open()
        for _ in pages:
            doc.new_page()
        pdf_doc = PdfDocument(file_path="x.pdf", pages=pages)
        session = PdfSession(file_path="x.pdf", doc=doc, pdf_document=pdf_doc)
        pdf_tab._session_mgr._sessions["x.pdf"] = session
        pdf_tab._session_mgr._active_path = "x.pdf"
        return session

    def _patch_confirm_yes(self, monkeypatch):
        """让确认 QMessageBox.question 自动返回 Yes，避免模态阻塞。"""
        import vibeocr.views.tabs.pdf_tab as mod
        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(
            mod.QMessageBox, "question",
            lambda *a, **k: QMessageBox.StandardButton.Yes,
        )

    def test_partial_layer_prompts_and_overwrite_false_skips(
        self, pdf_tab, monkeypatch
    ):
        """选中页中部分已有文字层：弹框（has=1,total=2），选"跳过"→ overwrite=False。"""
        from vibeocr.models.pdf_document import PdfPageInfo

        pages = [
            PdfPageInfo(page_index=0, has_text_layer=True),
            PdfPageInfo(page_index=1, has_text_layer=False),
        ]
        self._inject(pdf_tab, pages)
        # 选中两页
        from PySide6.QtCore import QItemSelectionModel

        sm = pdf_tab._thumbnail_list.selectionModel()
        for r in range(2):
            sm.select(
                pdf_tab._thumbnail_list.model().index(r, 0),
                QItemSelectionModel.Select,
            )

        captured = {}
        monkeypatch.setattr(
            pdf_tab,
            "_prompt_overwrite_choice",
            lambda has, total: captured.setdefault("args", (has, total)) or 0,  # 0=跳过
        )
        monkeypatch.setattr(
            type(pdf_tab._session_mgr), "is_ocr_ready",
            property(lambda self: True),
        )
        started = {}
        monkeypatch.setattr(
            pdf_tab._session_mgr, "start_ocr",
            lambda indices, **kw: started.update(kw),
        )
        self._patch_confirm_yes(monkeypatch)

        pdf_tab._on_add_text_layer()

        assert captured["args"] == (1, 2)
        assert started.get("overwrite") is False

    def test_partial_layer_choose_replace_uses_overwrite_true(
        self, pdf_tab, monkeypatch
    ):
        from vibeocr.models.pdf_document import PdfPageInfo
        from PySide6.QtCore import QItemSelectionModel

        pages = [
            PdfPageInfo(page_index=0, has_text_layer=True),
            PdfPageInfo(page_index=1, has_text_layer=False),
        ]
        self._inject(pdf_tab, pages)
        sm = pdf_tab._thumbnail_list.selectionModel()
        for r in range(2):
            sm.select(
                pdf_tab._thumbnail_list.model().index(r, 0),
                QItemSelectionModel.Select,
            )

        monkeypatch.setattr(pdf_tab, "_prompt_overwrite_choice", lambda has, total: 1)  # 先删后加
        monkeypatch.setattr(
            type(pdf_tab._session_mgr), "is_ocr_ready",
            property(lambda self: True),
        )
        started = {}
        monkeypatch.setattr(
            pdf_tab._session_mgr, "start_ocr",
            lambda indices, **kw: started.update(kw),
        )
        self._patch_confirm_yes(monkeypatch)

        pdf_tab._on_add_text_layer()
        assert started.get("overwrite") is True

    def test_all_without_layer_no_prompt(self, pdf_tab, monkeypatch):
        """选中页全部无文字层：不弹防重复框，直接 overwrite=False。"""
        from vibeocr.models.pdf_document import PdfPageInfo
        from PySide6.QtCore import QItemSelectionModel

        pages = [
            PdfPageInfo(page_index=0, has_text_layer=False),
            PdfPageInfo(page_index=1, has_text_layer=False),
        ]
        self._inject(pdf_tab, pages)
        sm = pdf_tab._thumbnail_list.selectionModel()
        for r in range(2):
            sm.select(
                pdf_tab._thumbnail_list.model().index(r, 0),
                QItemSelectionModel.Select,
            )

        prompted = {"n": 0}
        monkeypatch.setattr(
            pdf_tab, "_prompt_overwrite_choice",
            lambda has, total: prompted.__setitem__("n", prompted["n"] + 1) or 0,
        )
        monkeypatch.setattr(
            type(pdf_tab._session_mgr), "is_ocr_ready",
            property(lambda self: True),
        )
        monkeypatch.setattr(
            pdf_tab._session_mgr, "start_ocr", lambda indices, **kw: None
        )
        self._patch_confirm_yes(monkeypatch)

        pdf_tab._on_add_text_layer()
        assert prompted["n"] == 0

    def test_prompt_choice_cancel_aborts(self, pdf_tab, monkeypatch):
        from vibeocr.models.pdf_document import PdfPageInfo
        from PySide6.QtCore import QItemSelectionModel

        pages = [
            PdfPageInfo(page_index=0, has_text_layer=True),
            PdfPageInfo(page_index=1, has_text_layer=False),
        ]
        self._inject(pdf_tab, pages)
        sm = pdf_tab._thumbnail_list.selectionModel()
        for r in range(2):
            sm.select(
                pdf_tab._thumbnail_list.model().index(r, 0),
                QItemSelectionModel.Select,
            )

        monkeypatch.setattr(pdf_tab, "_prompt_overwrite_choice", lambda has, total: 2)  # 取消
        monkeypatch.setattr(
            type(pdf_tab._session_mgr), "is_ocr_ready",
            property(lambda self: True),
        )
        started = {"n": 0}
        monkeypatch.setattr(
            pdf_tab._session_mgr, "start_ocr",
            lambda indices, **kw: started.__setitem__("n", started["n"] + 1),
        )

        pdf_tab._on_add_text_layer()
        assert started["n"] == 0


class TestLayerStatusContextMenu:
    """状态列表右键菜单：为选中的无文字层页添加文字层。"""

    def _inject(self, pdf_tab, pages):
        import fitz

        from vibeocr.models.pdf_document import PdfDocument
        from vibeocr.models.pdf_session import PdfSession

        doc = fitz.open()
        for _ in pages:
            doc.new_page()
        pdf_doc = PdfDocument(file_path="x.pdf", pages=pages)
        session = PdfSession(file_path="x.pdf", doc=doc, pdf_document=pdf_doc)
        pdf_tab._session_mgr._sessions["x.pdf"] = session
        pdf_tab._session_mgr._active_path = "x.pdf"
        return session

    def test_context_menu_offers_action_for_pages_without_layer(
        self, pdf_tab, monkeypatch
    ):
        """无文字层页选中时，菜单应含"为 N 个无文字层页添加文字层"项。"""
        from PySide6.QtCore import QItemSelectionModel

        from vibeocr.models.pdf_document import PdfPageInfo

        pages = [PdfPageInfo(page_index=0, has_text_layer=False)]
        self._inject(pdf_tab, pages)
        pdf_tab._update_layer_status()
        # 用 selectionModel 显式选中第 0 行（setCurrentRow 不保证 selectedItems）
        sm = pdf_tab._layer_status_grid.selectionModel()
        sm.select(
            pdf_tab._layer_status_grid.model().index(0, 0),
            QItemSelectionModel.Select,
        )

        # 用 FakeMenu 捕获菜单项文本，避免 exec 阻塞
        actions_text = []

        class _FakeSignal:
            def connect(self, *a, **k):
                pass

        class _FakeAction:
            @property
            def triggered(self):
                return _FakeSignal()

        class FakeMenu:
            def __init__(self, *a, **k):
                pass

            def addAction(self, text, *args):
                actions_text.append(text if isinstance(text, str) else str(text))
                return _FakeAction()

            def addSeparator(self):
                actions_text.append("sep")

            def exec(self, *a, **k):
                return None

        import vibeocr.views.tabs.pdf_tab as mod

        monkeypatch.setattr(mod, "QMenu", FakeMenu)

        pdf_tab._on_layer_status_context_menu(
            pdf_tab._layer_status_grid.rect().center()
        )

        assert any("无文字层" in t for t in actions_text)

    def test_context_menu_no_action_when_all_have_layer(self, pdf_tab, monkeypatch):
        """选中页均有文字层时，菜单应提示而非提供添加项。"""
        from PySide6.QtCore import QItemSelectionModel

        from vibeocr.models.pdf_document import PdfPageInfo

        pages = [PdfPageInfo(page_index=0, has_text_layer=True)]
        self._inject(pdf_tab, pages)
        pdf_tab._update_layer_status()
        sm = pdf_tab._layer_status_grid.selectionModel()
        sm.select(
            pdf_tab._layer_status_grid.model().index(0, 0),
            QItemSelectionModel.Select,
        )

        actions_text = []

        class FakeMenu:
            def __init__(self, *a, **k):
                pass

            def addAction(self, text, *args):
                actions_text.append(text if isinstance(text, str) else str(text))

                class _A:
                    def triggered(self, *a, **k):
                        pass
                return _A()

            def addSeparator(self):
                pass

            def exec(self, *a, **k):
                return None

        import vibeocr.views.tabs.pdf_tab as mod

        monkeypatch.setattr(mod, "QMenu", FakeMenu)

        pdf_tab._on_layer_status_context_menu(
            pdf_tab._layer_status_grid.rect().center()
        )

        # 不应出现"添加文字层"的可执行项
        assert not any("无文字层页添加文字层" in t for t in actions_text)


class TestLayerStatusGrid:
    """文字层状态网格化（QListWidget IconMode + delegate）。"""

    def _inject(self, pdf_tab, pages):
        import fitz

        from vibeocr.models.pdf_document import PdfDocument
        from vibeocr.models.pdf_session import PdfSession

        doc = fitz.open()
        for _ in pages:
            doc.new_page()
        pdf_doc = PdfDocument(file_path="x.pdf", pages=pages)
        session = PdfSession(file_path="x.pdf", doc=doc, pdf_document=pdf_doc)
        pdf_tab._session_mgr._sessions["x.pdf"] = session
        pdf_tab._session_mgr._active_path = "x.pdf"
        return session

    def test_grid_exists_and_is_icon_mode(self, pdf_tab):
        """应有 _layer_status_grid（QListWidget IconMode）替代旧的列表。"""
        grid = getattr(pdf_tab, "_layer_status_grid", None)
        assert grid is not None, "应有 _layer_status_grid"
        assert isinstance(grid, QListWidget)
        assert grid.viewMode() == QListWidget.ViewMode.IconMode

    def test_grid_cell_count_equals_pages(self, pdf_tab):
        """网格格子数应等于页数，每个携带 page_index。"""
        from vibeocr.models.pdf_document import PdfPageInfo

        pages = [PdfPageInfo(page_index=i) for i in range(5)]
        self._inject(pdf_tab, pages)
        pdf_tab._update_layer_status()
        grid = pdf_tab._layer_status_grid
        assert grid.count() == 5
        for i in range(5):
            item = grid.item(i)
            assert item.data(Qt.ItemDataRole.UserRole) == i

    def test_grid_has_summary_label(self, pdf_tab):
        """网格上方应有汇总 Label（共 N 页 / 有文字层 X / 无文字层 Y）。"""
        from vibeocr.models.pdf_document import PdfPageInfo

        pages = [
            PdfPageInfo(page_index=0, has_text_layer=True),
            PdfPageInfo(page_index=1, has_text_layer=False),
            PdfPageInfo(page_index=2, has_text_layer=True),
        ]
        self._inject(pdf_tab, pages)
        pdf_tab._update_layer_status()
        text = pdf_tab._layer_summary_label.text()
        assert "共 3 页" in text
        assert "有文字层 2 页" in text
        assert "无文字层 1 页" in text

    def test_grid_tooltip_shows_block_count(self, pdf_tab):
        """有文字层格子的 tooltip 含块数。"""
        from vibeocr.models.pdf_document import PdfPageInfo, TextLayerInfo

        pages = [
            PdfPageInfo(
                page_index=0,
                has_text_layer=True,
                text_layers=[
                    TextLayerInfo(
                        index=i, text_preview="t", char_count=1,
                        bbox=(0.0, 0.0, 1.0, 1.0), color_id=i,
                    )
                    for i in range(7)
                ],
            ),
        ]
        self._inject(pdf_tab, pages)
        pdf_tab._update_layer_status()
        tip = pdf_tab._layer_status_grid.item(0).toolTip()
        assert "7" in tip
        assert "文字层" in tip

    def test_grid_no_layer_cell_tooltip(self, pdf_tab):
        """无文字层格子的 tooltip 应提示"无文字层"。"""
        from vibeocr.models.pdf_document import PdfPageInfo

        pages = [PdfPageInfo(page_index=0, has_text_layer=False)]
        self._inject(pdf_tab, pages)
        pdf_tab._update_layer_status()
        tip = pdf_tab._layer_status_grid.item(0).toolTip()
        assert "无文字层" in tip

    def test_delegate_uses_theme_colors_for_states(self, pdf_tab):
        """delegate 应按状态选色：选中=accent、有层=success、无层=text_subtle。"""
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QColor, QPainter, QPixmap
        from PySide6.QtWidgets import QStyle, QStyleOptionViewItem

        from vibeocr.ui.theme import Colors
        from vibeocr.views.tabs.pdf_tab import (
            _HAS_LAYER_ROLE,
            _LAYER_ROLE,
            LayerStatusDelegate,
        )

        delegate = LayerStatusDelegate()

        def _bg_for(state_flags: QStyle.StateFlag, has_layer: bool) -> QColor:
            """用像素采样法读出格子的填充色（左上角偏内一点）。"""
            opt = QStyleOptionViewItem()
            opt.rect = QRect(0, 0, 40, 40)
            opt.state = state_flags
            idx = _StubIndex({(_LAYER_ROLE, 0), (_HAS_LAYER_ROLE, has_layer)})
            pm = QPixmap(40, 40)
            pm.fill(QColor(0, 0, 0))
            painter = QPainter(pm)
            try:
                delegate.paint(painter, opt, idx)
            finally:
                painter.end()
            return QColor(pm.toImage().pixel(8, 8))

        sel = _bg_for(
            QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_Selected, True
        )
        has = _bg_for(QStyle.StateFlag.State_Enabled, True)
        none_bg = _bg_for(QStyle.StateFlag.State_Enabled, False)
        assert sel.name() == QColor(Colors.accent).name()
        assert has.name() == QColor(Colors.success).name()
        assert none_bg.name() == QColor(Colors.text_subtle).name()


class _StubIndex:
    """QModelIndex 替身：按 (role) 返回预设 data。"""

    def __init__(self, pairs):
        self._data = {role: val for role, val in pairs}

    def data(self, role):
        return self._data.get(role)


class TestOcrPerPageFeedback:
    """OCR 逐页完成即时反馈：格子逐页变绿，缩略图不重渲染。"""

    def _inject(self, pdf_tab, pages):
        import fitz

        from vibeocr.models.pdf_document import PdfDocument
        from vibeocr.models.pdf_session import PdfSession

        doc = fitz.open()
        for _ in pages:
            doc.new_page()
        pdf_doc = PdfDocument(file_path="x.pdf", pages=pages)
        session = PdfSession(file_path="x.pdf", doc=doc, pdf_document=pdf_doc)
        pdf_tab._session_mgr._sessions["x.pdf"] = session
        pdf_tab._session_mgr._active_path = "x.pdf"
        pdf_tab._update_layer_status()
        return session

    def test_ocr_page_result_updates_grid_cell_to_green(self, pdf_tab):
        """ocr_page_done 后该格子 has_layer 应为 True（变绿）。"""
        from vibeocr.models.pdf_document import PdfPageInfo

        pages = [PdfPageInfo(page_index=0, has_text_layer=False)]
        session = self._inject(pdf_tab, pages)
        # 模拟 OCR 完成第 0 页：has_text_layer 被置 True
        session.pdf_document.pages[0].has_text_layer = True

        pdf_tab._session_mgr.ocr_page_done.emit("x.pdf", 0, object())

        item = pdf_tab._layer_status_grid.item(0)
        # _HAS_LAYER_ROLE = UserRole + 1
        assert item.data(Qt.ItemDataRole.UserRole + 1) is True

    def test_ocr_page_result_does_not_render_thumbnail(self, pdf_tab, monkeypatch):
        """OCR 完成一页不应重新渲染缩略图（隐形层无视觉变化）。"""
        from PySide6.QtGui import QPixmap

        from vibeocr.models.pdf_document import PdfPageInfo

        pages = [PdfPageInfo(page_index=0, has_text_layer=False)]
        session = self._inject(pdf_tab, pages)
        session.pdf_document.pages[0].has_text_layer = True

        import vibeocr.views.tabs.pdf_tab as mod

        called = []
        monkeypatch.setattr(
            mod.PdfService,
            "render_page",
            lambda *a, **k: called.append(1) or QPixmap(10, 10),
        )
        pdf_tab._session_mgr.ocr_page_done.emit("x.pdf", 0, object())
        assert called == []

    def test_ocr_page_result_updates_summary_label(self, pdf_tab):
        """ocr_page_done 后汇总 Label 应反映新的计数。"""
        from vibeocr.models.pdf_document import PdfPageInfo

        pages = [
            PdfPageInfo(page_index=0, has_text_layer=False),
            PdfPageInfo(page_index=1, has_text_layer=False),
        ]
        session = self._inject(pdf_tab, pages)
        # 第 0 页 OCR 完成
        session.pdf_document.pages[0].has_text_layer = True
        pdf_tab._session_mgr.ocr_page_done.emit("x.pdf", 0, object())
        text = pdf_tab._layer_summary_label.text()
        assert "有文字层 1 页" in text
        assert "无文字层 1 页" in text
