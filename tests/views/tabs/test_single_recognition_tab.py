"""SingleRecognitionTab 测试"""

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

from vibeocr.views.tabs.base_tab import BaseOcrTab
from vibeocr.views.tabs.single_recognition_tab import SingleRecognitionTab


class TestSingleRecognitionTab:
    def test_creation(self, qapp):
        tab = SingleRecognitionTab()
        assert isinstance(tab, QWidget)

    def test_is_base_ocr_tab(self, qapp):
        tab = SingleRecognitionTab()
        assert isinstance(tab, BaseOcrTab)

    def test_has_preview_widget(self, qapp):
        tab = SingleRecognitionTab()
        assert tab._preview_widget is not None

    def test_has_result_widget(self, qapp):
        tab = SingleRecognitionTab()
        assert tab._result_widget is not None

    def test_has_preprocess_options(self, qapp):
        tab = SingleRecognitionTab()
        assert tab._preprocess_options is not None

    def test_has_action_buttons(self, qapp):
        tab = SingleRecognitionTab()
        assert tab._screenshot_btn is not None
        assert tab._file_btn is not None
        assert tab._paste_btn is not None

    def test_has_copy_image_button(self, qapp):
        tab = SingleRecognitionTab()
        assert tab._copy_image_btn is not None
        assert tab._copy_image_btn.text() == "复制图片"

    def test_copy_image_btn_disabled_by_default(self, qapp):
        tab = SingleRecognitionTab()
        assert tab._copy_image_btn.isEnabled() is False

    def test_copy_image_btn_enabled_after_set_pixmap(self, qapp, sample_pixmap):
        tab = SingleRecognitionTab()
        tab.set_pixmap(sample_pixmap)
        assert tab._copy_image_btn.isEnabled() is True

    def test_copy_image_btn_enabled_after_set_image_for_recognition(
        self, qapp, sample_pixmap
    ):
        # 真实流程（main_window）：set_image_for_recognition 与 set_pixmap 配合调用；
        # 仅前者不加载预览，故复制按钮以预览 original_pixmap 为准。
        tab = SingleRecognitionTab()
        tab.set_pixmap(sample_pixmap)
        tab.set_image_for_recognition(sample_pixmap)
        assert tab._copy_image_btn.isEnabled() is True

    def test_copy_image_btn_enabled_after_paste(self, qapp, sample_pixmap, monkeypatch):
        """模拟粘贴：让剪贴板返回 sample_pixmap（_on_paste 用 QGuiApplication）。"""

        class FakeClipboard:
            def pixmap(self):
                return sample_pixmap

        monkeypatch.setattr(QGuiApplication, "clipboard", lambda *a, **k: FakeClipboard())
        tab = SingleRecognitionTab()
        tab._on_paste()
        assert tab._copy_image_btn.isEnabled() is True

    def test_screenshot_btn_emits_signal(self, qapp):
        tab = SingleRecognitionTab()
        emitted = []
        tab.screenshot_requested.connect(lambda: emitted.append(True))
        tab._screenshot_btn.click()
        assert emitted


class TestSingleRecognitionTabCopyImage:
    """「复制图片」复制逻辑测试"""

    def test_on_copy_image_copies_original(self, qapp, sample_pixmap, monkeypatch):
        """点复制图片后，剪贴板收到的是原始图片（original_pixmap）。"""
        captured = {}

        class FakeClipboard:
            def setPixmap(self, pm):
                captured["pixmap"] = pm

        monkeypatch.setattr(QGuiApplication, "clipboard", lambda *a, **k: FakeClipboard())
        tab = SingleRecognitionTab()
        tab.set_pixmap(sample_pixmap)
        tab._on_copy_image()
        assert "pixmap" in captured
        # 复制的是原图（cacheKey 与 original_pixmap 一致）
        assert (
            captured["pixmap"].cacheKey()
            == tab._preview_widget.original_pixmap().cacheKey()
        )

    def test_on_copy_image_shows_toast(self, qapp, sample_pixmap, monkeypatch):
        class FakeClipboard:
            def setPixmap(self, pm):
                pass

        monkeypatch.setattr(QGuiApplication, "clipboard", lambda *a, **k: FakeClipboard())
        tab = SingleRecognitionTab()
        tab.set_pixmap(sample_pixmap)
        tab._on_copy_image()
        # 注意：未 show() 顶层窗口时 isVisible() 恒为 False，改用 isHidden() 判定
        assert tab._copy_toast.isHidden() is False
        assert "原图已复制" in tab._copy_toast.text()

    def test_on_copy_image_noop_without_pixmap(self, qapp, monkeypatch):
        called = {"yes": False}

        class FakeClipboard:
            def setPixmap(self, pm):
                called["yes"] = True

        monkeypatch.setattr(QGuiApplication, "clipboard", lambda *a, **k: FakeClipboard())
        tab = SingleRecognitionTab()  # 无图
        tab._on_copy_image()
        assert called["yes"] is False

    def test_copy_image_btn_triggers_on_copy(self, qapp, sample_pixmap, monkeypatch):
        called = {"yes": False}

        class FakeClipboard:
            def setPixmap(self, pm):
                called["yes"] = True

        monkeypatch.setattr(QGuiApplication, "clipboard", lambda *a, **k: FakeClipboard())
        tab = SingleRecognitionTab()
        tab.set_pixmap(sample_pixmap)
        tab._copy_image_btn.click()
        assert called["yes"] is True


def _make_table_result():
    """构造一个表格管道风格的 OCRResult（content_list + text_blocks + table_body）。"""
    from vibeocr.models.ocr_result import OCRResult, TextBlock

    table_html = "<table><tr><td>A</td><td>B</td></tr></table>"
    return OCRResult(
        content_list=[{"type": "table", "table_body": table_html, "text": "A B"}],
        text_blocks=[
            TextBlock(
                text=table_html,
                score=0.9,
                bbox=(10, 10, 200, 80),
                label="table",
                content_index=0,
            )
        ],
        text_with_scores=[(table_html, 0.9)],
        raw_text=table_html,
        markdown_text="| A | B |\n|---|---|",
    )


def _make_formula_result():
    """构造一个公式管道风格的 OCRResult（content_list type=formula）。"""
    from vibeocr.models.ocr_result import OCRResult, TextBlock

    latex = "E=mc^2"
    return OCRResult(
        content_list=[{"type": "formula", "text": latex}],
        text_blocks=[
            TextBlock(
                text=latex, score=1.0, bbox=(10, 10, 200, 80),
                label="formula", content_index=0,
            )
        ],
        text_with_scores=[(latex, 1.0)],
        raw_text=latex,
        markdown_text=f"$${latex}$$",
    )


class TestResultBlockEditedTableDelegation:
    """右侧双击编辑表格块 → _on_result_block_edited 应委托 _on_table_block_edited，
    复用左侧网格编辑的正确同步逻辑（更新 table_body、保持块类型模式）。
    """

    def test_table_edit_delegates_to_table_handler(self, qapp):
        tab = SingleRecognitionTab()
        tab._current_ocr_result = _make_table_result()

        delegated: list = []
        tab._on_table_block_edited = lambda ci, html: delegated.append((ci, html))

        new_html = "<table><tr><td>X</td><td>Y</td></tr></table>"
        tab._on_result_block_edited(0, new_html)

        assert delegated == [(0, new_html)], "table 块应委托 _on_table_block_edited"
        # 委托后不应继续走文本分支：content_list 的 text 不应被改成 HTML
        assert tab._current_ocr_result.content_list[0]["text"] == "A B"

    def test_table_edit_updates_table_body_and_keeps_block_type_mode(self, qapp, monkeypatch):
        """端到端：右侧表格编辑后，content_list.table_body 被更新、
        左侧用 set_content_list（块类型模式）刷新而非 set_text_blocks。"""
        tab = SingleRecognitionTab()
        tab._current_ocr_result = _make_table_result()

        refreshed: list = []
        monkeypatch.setattr(
            tab._preview_widget, "set_content_list",
            lambda cl: refreshed.append(("content_list", cl)),
        )
        monkeypatch.setattr(
            tab._preview_widget, "set_text_blocks",
            lambda tb: refreshed.append(("text_blocks", tb)),
        )
        # update_block_text 触发 WebEngine JS，测试环境 stub 掉
        monkeypatch.setattr(tab._result_widget, "update_block_text", lambda *a, **k: None)

        new_html = "<table><tr><td>X</td></tr></table>"
        tab._on_result_block_edited(0, new_html)

        # table_body 是真正的数据源，应被更新为新 HTML
        assert tab._current_ocr_result.content_list[0]["table_body"] == new_html
        # text_block 也应是新 HTML（不是纯文本 innerText）
        assert tab._current_ocr_result.text_blocks[0].text == new_html
        assert tab._current_ocr_result.text_blocks[0].is_manually_edited is True
        # 左侧应走块类型模式刷新（set_content_list），而非切到置信度模式
        assert refreshed and refreshed[0][0] == "content_list"


def _make_plain_text_result():
    """构造一个通用 OCR（纯文本）风格的 OCRResult。

    content_list 为空（仅 text_blocks）—— 通用 OCR 管道在 _display_result 之前
    的真实形态。段落处理选项只对这类结果有意义（结构化结果走块类型渲染）。
    """
    from vibeocr.models.ocr_result import OCRResult, TextBlock

    return OCRResult(
        text_blocks=[
            TextBlock(text="第一行", score=0.95, bbox=(10, 10, 200, 40), label="text"),
            TextBlock(text="第二行", score=0.93, bbox=(10, 50, 200, 80), label="text"),
        ],
        text_with_scores=[("第一行", 0.95), ("第二行", 0.93)],
        raw_text="第一行\n第二行",
        markdown_text="第一行\n第二行",
        image_height=100,
    )


class TestTextOptionsLiveUpdate:
    """识别完成后切换「文本块处理」选项，应实时重算 raw_text 并刷新结果区。

    根因：TextBlockOptionsWidget.options_changed 信号此前从未被
    SingleRecognitionTab 连接，切换选项只在重新识别后才生效。
    """

    def test_line_mode_change_recomputes_raw_text(self, qapp, monkeypatch):
        tab = SingleRecognitionTab()
        tab._current_ocr_result = _make_plain_text_result()
        # 记录识别完成时的原始标志（_display_result 会回填 content_list 使
        # has_content_list 变 True，但段落处理应只作用于纯文本结果）。
        tab._plain_text_at_recognition = True

        # 切换为「合并成一段」：raw_text 应变为两块直接拼接（无换行）
        tab._text_options_widget._mode_combo.setCurrentIndex(1)  # 合并成一段

        assert tab._current_ocr_result.raw_text == "第一行第二行"

    def test_line_mode_change_refreshes_result_view(self, qapp, monkeypatch):
        """切换选项后结果区应重新渲染（display_result 被调用）。"""
        tab = SingleRecognitionTab()
        tab._current_ocr_result = _make_plain_text_result()
        tab._plain_text_at_recognition = True

        refreshed = {"count": 0}
        monkeypatch.setattr(
            tab._result_widget, "display_result",
            lambda *a, **k: refreshed.__setitem__("count", refreshed["count"] + 1),
        )

        tab._text_options_widget._mode_combo.setCurrentIndex(1)

        assert refreshed["count"] >= 1, "切换段落选项应刷新结果区"

    def test_structured_result_not_affected(self, qapp, monkeypatch):
        """结构化结果（表格/公式）走块类型渲染，不读 raw_text，
        切换段落选项不应改其 raw_text（避免误伤复制/导出链路）。"""
        tab = SingleRecognitionTab()
        tab._current_ocr_result = _make_table_result()
        tab._plain_text_at_recognition = False  # 结构化结果

        original_raw = tab._current_ocr_result.raw_text
        tab._text_options_widget._mode_combo.setCurrentIndex(1)

        assert tab._current_ocr_result.raw_text == original_raw

    def test_no_result_does_not_crash(self, qapp):
        """尚未识别时切换选项不应抛异常。"""
        tab = SingleRecognitionTab()
        tab._current_ocr_result = None
        # 不应抛异常
        tab._text_options_widget._mode_combo.setCurrentIndex(1)


class TestResultBlockEditedFormulaSync:
    """右侧编辑公式块 → _on_result_block_edited 走文本分支，但 has_content_list
    时左侧应走 set_content_list（保持块类型模式），且 tb.is_manually_edited=True。
    """

    def test_formula_edit_uses_set_content_list(self, qapp, monkeypatch):
        tab = SingleRecognitionTab()
        tab._current_ocr_result = _make_formula_result()

        refreshed: list = []
        monkeypatch.setattr(
            tab._preview_widget, "set_content_list",
            lambda cl: refreshed.append(("content_list", cl)),
        )
        monkeypatch.setattr(
            tab._preview_widget, "set_text_blocks",
            lambda tb: refreshed.append(("text_blocks", tb)),
        )

        tab._on_result_block_edited(0, "F=ma")

        assert tab._current_ocr_result.content_list[0]["text"] == "F=ma"
        assert tab._current_ocr_result.text_blocks[0].text == "F=ma"
        assert tab._current_ocr_result.text_blocks[0].is_manually_edited is True
        # has_content_list → 块类型模式刷新
        assert refreshed and refreshed[0][0] == "content_list"

