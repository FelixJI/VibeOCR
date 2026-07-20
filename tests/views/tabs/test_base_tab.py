"""BaseOcrTab 测试"""

from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QWidget

from vibeocr.views.tabs.base_tab import BaseOcrTab


class ConcreteTab(BaseOcrTab):
    """用于测试的具体 Tab 实现"""

    def _setup_ui(self) -> None:
        """设置 UI"""
        self._ui_setup = True

    def _connect_signals(self) -> None:
        """连接信号"""
        self._signals_connected = True

    def _on_start(self) -> None:
        """开始处理"""
        self._started = True


class TestBaseOcrTab:
    """BaseOcrTab 测试"""

    @pytest.fixture
    def tab(self, qapp):
        """创建测试 Tab"""
        tab = ConcreteTab()
        tab._setup_ui()
        tab._connect_signals()
        return tab

    def test_tab_creation(self, tab):
        """测试 Tab 创建"""
        assert tab._ocr_service is None
        assert tab._is_processing is False

    def test_tab_is_widget(self, tab):
        """测试 Tab 是 QWidget"""
        assert isinstance(tab, QWidget)

    def test_ocr_service_property(self, tab):
        """测试 OCR 服务属性"""
        assert tab.ocr_service is None

    def test_is_processing_property(self, tab):
        """测试处理状态属性"""
        assert tab.is_processing is False

    def test_set_ocr_service(self, tab):
        """测试设置 OCR 服务"""
        mock_service = Mock()

        tab.set_ocr_service(mock_service)

        assert tab._ocr_service is mock_service
        assert tab.ocr_service is mock_service

    def test_set_ocr_service_none(self, tab):
        """测试设置 OCR 服务为 None"""
        mock_service = Mock()
        tab.set_ocr_service(mock_service)

        tab.set_ocr_service(None)

        assert tab._ocr_service is None

    def test_set_processing(self, tab):
        """测试设置处理状态"""
        tab._set_processing(True)

        assert tab._is_processing is True
        assert tab.is_processing is True

    def test_on_service_called(self, tab):
        """测试服务变化回调被调用"""
        mock_service = Mock()

        tab.set_ocr_service(mock_service)

        # 基类的 _on_service_changed 默认什么都不做
        # 但我们验证它不会抛出异常

    def test_abstract_methods_implemented(self, tab):
        """测试抽象方法已实现"""
        assert hasattr(tab, "_ui_setup")
        assert hasattr(tab, "_signals_connected")

        tab._on_start()
        assert hasattr(tab, "_started")


class TestBaseOcrTabAbstract:
    """BaseOcrTab 抽象方法测试"""

    def test_base_class_can_be_instantiated(self, qapp):
        """测试基类可以被实例化（轻量基类设计）"""
        # BaseOcrTab 是轻量基类，不使用 ABCMeta
        tab = BaseOcrTab()
        assert tab is not None


class TestBaseOcrTabInheritance:
    """BaseOcrTab 继承测试"""

    def test_inheritance_chain(self, qapp):
        """测试继承链"""
        tab = ConcreteTab()
        tab._setup_ui()
        tab._connect_signals()

        assert isinstance(tab, BaseOcrTab)
        assert isinstance(tab, QWidget)

    def test_on_cancel_default_implementation(self, qapp):
        """测试默认取消实现"""
        tab = ConcreteTab()
        # 默认实现应该不抛出异常
        tab._on_cancel()


class TestBaseOcrTabServiceRouting:
    """管道路由测试"""

    def test_get_service_for_pipeline_parsing(self, qapp):
        from vibeocr.core.pipelines import OCRPipeline
        from vibeocr.models.ocr_options import OCROptions

        tab = ConcreteTab()
        mineru_mock = Mock()
        paddlex_mock = Mock()
        tab._ocr_service = mineru_mock
        tab._paddlex_service = paddlex_mock
        options = OCROptions(pipeline=OCRPipeline.DOCUMENT_PARSING)
        assert tab._get_service_for_pipeline(options) is mineru_mock

    def test_get_service_for_pipeline_ocr(self, qapp):
        from vibeocr.core.pipelines import OCRPipeline
        from vibeocr.models.ocr_options import OCROptions

        tab = ConcreteTab()
        mineru_mock = Mock()
        paddlex_mock = Mock()
        tab._ocr_service = mineru_mock
        tab._paddlex_service = paddlex_mock
        options = OCROptions(pipeline=OCRPipeline.OCR)
        assert tab._get_service_for_pipeline(options) is paddlex_mock

    def test_set_paddlex_service(self, qapp):
        tab = ConcreteTab()
        mock = Mock()
        tab.set_paddlex_service(mock)
        assert tab._paddlex_service is mock

    def test_set_paddlex_service_none(self, qapp):
        tab = ConcreteTab()
        tab.set_paddlex_service(Mock())
        tab.set_paddlex_service(None)
        assert tab._paddlex_service is None

    def test_shared_state_initialized(self, qapp):
        tab = ConcreteTab()
        assert tab._paddlex_service is None
        assert tab._current_ocr_result is None
        assert tab._preview_widget is None
        assert tab._result_widget is None
        assert tab._preprocess_options is None


class TestBuildContentList:
    """_build_content_list 测试"""

    def test_from_content_list_with_bbox_merge(self, qapp):
        from vibeocr.models.ocr_result import OCRResult, TextBlock

        tab = ConcreteTab()
        result = OCRResult(
            content_list=[
                {"type": "text", "text": "Hello"},
                {"type": "table", "text": "data"},
            ],
            text_blocks=[
                TextBlock(
                    text="Hello", score=0.9, bbox=(0, 0, 100, 50), content_index=0
                ),
            ],
        )
        cl = tab._build_content_list(result)
        assert len(cl) == 2
        assert "bbox" in cl[0]

    def test_from_text_blocks_only(self, qapp):
        from vibeocr.models.ocr_result import OCRResult, TextBlock

        tab = ConcreteTab()
        result = OCRResult(
            text_blocks=[
                TextBlock(text="Hello", score=0.9, bbox=(0, 0, 100, 50)),
                TextBlock(text="World", score=0.8, bbox=(0, 60, 100, 110)),
            ],
        )
        cl = tab._build_content_list(result)
        assert len(cl) == 2
        assert cl[0]["type"] == "text"
        assert "bbox" in cl[0]

    def test_empty_result(self, qapp):
        from vibeocr.models.ocr_result import OCRResult

        tab = ConcreteTab()
        result = OCRResult()
        cl = tab._build_content_list(result)
        assert cl == []

    def test_table_block_no_fake_confidence(self, qapp):
        """表格/图片等结构识别块不应写入占位置信度（pipeline 里 score 是占位值，
        显示"置信度: 90%"会误导）。文本块保留真实置信度。"""
        from vibeocr.models.ocr_result import OCRResult, TextBlock

        tab = ConcreteTab()
        result = OCRResult(
            content_list=[
                {"type": "text", "text": "文本"},
                {"type": "table", "table_body": "<table></table>"},
            ],
            text_blocks=[
                TextBlock(
                    text="文本", score=0.85, bbox=(0, 0, 100, 50), content_index=0
                ),
                TextBlock(
                    text="<table></table>",
                    score=0.9,  # 占位值
                    bbox=(0, 60, 100, 110),
                    content_index=1,
                    label="table",
                ),
            ],
        )
        cl = tab._build_content_list(result)
        # 文本块：保留真实置信度
        assert cl[0].get("confidence") == 0.85
        # 表格块：不写入占位置信度
        assert "confidence" not in cl[1]

    def test_display_result_updates_state(self, qapp):
        from vibeocr.models.ocr_result import OCRResult, TextBlock

        tab = ConcreteTab()
        result = OCRResult(
            text_blocks=[TextBlock(text="Hello", score=0.9, bbox=(0, 0, 100, 50))],
        )
        tab._display_result(result)
        assert tab._current_ocr_result is result


class TestDisplayResultContentListBackfill:
    """_display_result 应为通用 OCR（content_list 为空）构建并回填 content_list，
    使右侧结果区按块渲染（可编辑）而非走 <pre> 不可编辑分支。
    """

    def test_empty_content_list_backfilled_from_text_blocks(self, qapp):
        from vibeocr.models.ocr_result import OCRResult, TextBlock

        tab = ConcreteTab()
        result = OCRResult(
            raw_text="第一行\n第二行",
            text_with_scores=[("第一行", 0.95), ("第二行", 0.88)],
            text_blocks=[
                TextBlock(text="第一行", score=0.95, bbox=(10, 10, 100, 40)),
                TextBlock(text="第二行", score=0.88, bbox=(10, 50, 100, 80)),
            ],
            content_list=[],  # 通用 OCR 管道 content_list 为空
        )
        tab._display_result(result)
        # content_list 被回填
        assert len(result.content_list) == 2
        assert result.content_list[0]["type"] == "text"
        # text_blocks 补建了 content_index（编辑回调按此反查）
        assert result.text_blocks[0].content_index == 0
        assert result.text_blocks[1].content_index == 1

    def test_existing_content_index_not_overwritten(self, qapp):
        """结构化管道（table/formula）已设 content_index，不应被覆盖。"""
        from vibeocr.models.ocr_result import OCRResult, TextBlock

        tab = ConcreteTab()
        result = OCRResult(
            text_blocks=[
                TextBlock(
                    text="<table/>",
                    score=0.9,
                    bbox=(0, 0, 100, 50),
                    content_index=5,
                    label="table",
                )
            ],
            content_list=[{"type": "table", "table_body": "<table/>"}],
        )
        tab._display_result(result)
        # content_index=5 不应被改成 0
        assert result.text_blocks[0].content_index == 5

    def test_backfill_enables_block_rendering(self, qapp):
        """回填后 display_result 应走 .ocr-block 渲染（可编辑），而非 <pre>。"""
        from vibeocr.models.ocr_result import OCRResult, TextBlock
        from vibeocr.widgets.result_view_widget import _render_block

        tab = ConcreteTab()
        result = OCRResult(
            raw_text="文本",
            text_with_scores=[("文本", 0.9)],
            text_blocks=[TextBlock(text="文本", score=0.9, bbox=(0, 0, 10, 10))],
            content_list=[],
        )
        tab._display_result(result)
        # 模拟 display_result 的渲染分支
        body = "\n".join(_render_block(b, i) for i, b in enumerate(result.content_list))
        assert "ocr-block" in body
        assert "<pre" not in body


def test_large_content_preparation_keeps_gui_responsive(qapp, qtbot, monkeypatch):
    """五万块归一化必须在后台执行，调用槽本身保持在 150ms 内。"""
    import threading
    import time

    from tests.qt_responsiveness import assert_qt_event_loop_responsive
    from vibeocr.models.ocr_result import OCRResult, TextBlock

    tab = ConcreteTab()
    qtbot.addWidget(tab)
    result = OCRResult(
        text_blocks=[
            TextBlock(text=str(index), score=0.9, bbox=(0, 0, 10, 10))
            for index in range(50_000)
        ]
    )
    original = tab._build_content_list
    started = threading.Event()
    release = threading.Event()

    def slow_build(source, cancel_event=None):
        started.set()
        release.wait(timeout=2)
        return original(source, cancel_event)

    monkeypatch.setattr(tab, "_build_content_list", slow_build)
    before = time.perf_counter()
    tab._display_result(result)
    assert (time.perf_counter() - before) * 1000 < 150
    qtbot.waitUntil(started.is_set, timeout=1000)
    assert_qt_event_loop_responsive(qtbot, in_flight=lambda: tab._content_jobs.is_running)
    release.set()
    qtbot.waitUntil(lambda: not tab._content_jobs.is_running, timeout=3000)
    qtbot.waitUntil(lambda: len(result.content_list) == 50_000, timeout=3000)


def test_large_content_index_backfill_is_chunked_off_gui_scan(qapp, qtbot):
    """50k missing content indexes are planned in a worker and applied in chunks."""
    import threading

    from tests.qt_responsiveness import assert_qt_event_loop_responsive
    from vibeocr.models.ocr_result import OCRResult, TextBlock

    class ObservedTextBlocks(list):
        iteration_threads: list[int] = []

        def __iter__(self):
            type(self).iteration_threads.append(threading.get_ident())
            return super().__iter__()

    blocks = ObservedTextBlocks(
        TextBlock(text=f"block-{index}", score=0.9, bbox=None)
        for index in range(50_000)
    )
    result = OCRResult(text_blocks=blocks)
    ObservedTextBlocks.iteration_threads = []
    tab = ConcreteTab()
    qtbot.addWidget(tab)

    tab._display_result(result)

    qtbot.waitUntil(
        lambda: tab._pending_content_backfill is not None, timeout=3000
    )
    assert tab.drain_base_jobs(0) is False
    assert_qt_event_loop_responsive(
        qtbot, in_flight=lambda: tab._pending_content_backfill is not None
    )
    qtbot.waitUntil(
        lambda: result.text_blocks[-1].content_index == 49_999, timeout=3000
    )
    assert threading.get_ident() not in ObservedTextBlocks.iteration_threads
    assert tab._text_index_by_content[49_999] == 49_999
    assert tab.drain_base_jobs(0) is True


def test_large_text_edit_defers_aggregate_rebuild(qapp, qtbot, monkeypatch):
    """五万块编辑槽只做增量修改，join/Markdown 聚合交给后台作业。"""
    import threading
    import time

    from vibeocr.models.ocr_result import OCRResult, TextBlock

    tab = ConcreteTab()
    qtbot.addWidget(tab)
    result = OCRResult(
        raw_text="before",
        markdown_text="before",
        html_text="before",
        text_blocks=[
            TextBlock(text=f"block-{index}", score=0.9, bbox=None)
            for index in range(50_000)
        ],
        content_list=[
            {"type": "text", "text": f"block-{index}"}
            for index in range(50_000)
        ],
    )
    for index, block in enumerate(result.text_blocks):
        block.content_index = index
    tab._current_ocr_result = result
    submitted = []
    monkeypatch.setattr(
        tab._result_rebuild_jobs, "submit", lambda operation: submitted.append(operation)
    )

    before = time.perf_counter()
    tab._on_block_text_edited(0, "changed")
    elapsed_ms = (time.perf_counter() - before) * 1000

    assert elapsed_ms < 150
    assert result.text_blocks[0].text == "changed"
    assert result.content_list[0]["text"] == "changed"
    assert len(submitted) == 1
    _result, raw, markdown, html = submitted[0](threading.Event())
    assert raw.startswith("changed\nblock-1")
    assert markdown == raw
    assert html == raw


def test_rapid_large_text_edits_rebuild_all_accepted_changes(
    qapp, qtbot
):
    """Latest-wins rebuilds must not start from aggregate text predating earlier edits."""
    from vibeocr.models.ocr_result import OCRResult, TextBlock

    tab = ConcreteTab()
    qtbot.addWidget(tab)
    texts = [f"block-{index}" for index in range(50_000)]
    aggregate = "\n".join(texts)
    result = OCRResult(
        raw_text=aggregate,
        markdown_text=aggregate,
        html_text=aggregate,
        text_blocks=[TextBlock(text=text, score=0.9, bbox=None) for text in texts],
        content_list=[{"type": "text", "text": text} for text in texts],
    )
    for index, block in enumerate(result.text_blocks):
        block.content_index = index
    tab._current_ocr_result = result

    tab._on_block_text_edited(0, "first-change")
    tab._on_block_text_edited(1, "second-change")

    qtbot.waitUntil(lambda: not tab._result_rebuild_jobs.is_running, timeout=3000)
    assert result.raw_text.startswith("first-change\nsecond-change\nblock-2")
    assert result.markdown_text.startswith("first-change\nsecond-change\nblock-2")
    assert result.html_text.startswith("first-change\nsecond-change\nblock-2")


def test_large_repeated_text_edit_never_replaces_the_wrong_block(qapp, qtbot):
    """Ambiguous aggregate replacement must fall back to current block order."""
    from vibeocr.models.ocr_result import OCRResult, TextBlock

    texts = ["same", "same", *(f"block-{index}" for index in range(2, 50_000))]
    aggregate = "\n".join(texts)
    result = OCRResult(
        raw_text=aggregate,
        markdown_text=aggregate,
        html_text=aggregate,
        text_blocks=[TextBlock(text=text, score=0.9, bbox=None) for text in texts],
        content_list=[{"type": "text", "text": text} for text in texts],
    )
    for index, block in enumerate(result.text_blocks):
        block.content_index = index
    tab = ConcreteTab()
    qtbot.addWidget(tab)
    tab._current_ocr_result = result

    tab._on_block_text_edited(1, "second-only")

    qtbot.waitUntil(lambda: not tab._result_rebuild_jobs.is_running, timeout=3000)
    expected_prefix = "same\nsecond-only\nblock-2"
    assert result.raw_text.startswith(expected_prefix)
    assert result.markdown_text.startswith(expected_prefix)
    assert result.html_text.startswith(expected_prefix)


def test_large_table_edit_submits_without_gui_model_scan(
    qapp, qtbot, monkeypatch
):
    """A 50k table edit may mutate its target, but must defer full scans."""
    import threading
    import time

    from vibeocr.models.ocr_result import OCRResult, TextBlock

    class ObservedTextBlocks(list):
        iterations = 0

        def __iter__(self):
            type(self).iterations += 1
            return super().__iter__()

    class ObservedContent(list):
        iterations = 0

        def __iter__(self):
            type(self).iterations += 1
            return super().__iter__()

    table_html = "<table><tr><td>old</td></tr></table>"
    text_blocks = ObservedTextBlocks(
        [
            TextBlock(
                text=table_html if index == 0 else f"block-{index}",
                score=0.9,
                bbox=None,
                content_index=index,
                label="table" if index == 0 else "text",
            )
            for index in range(50_000)
        ]
    )
    content_list = ObservedContent(
        [
            {"type": "table", "table_body": table_html}
            if index == 0
            else {"type": "image"}
            for index in range(50_000)
        ]
    )
    tab = ConcreteTab()
    qtbot.addWidget(tab)
    tab._current_ocr_result = OCRResult(
        text_blocks=text_blocks,
        content_list=content_list,
    )
    submitted = []
    monkeypatch.setattr(
        tab._result_rebuild_jobs, "submit", lambda operation: submitted.append(operation)
    )

    before = time.perf_counter()
    tab._on_table_block_edited(
        0, "<table><tr><td>changed</td></tr></table>"
    )
    elapsed_ms = (time.perf_counter() - before) * 1000

    assert elapsed_ms < 150
    assert ObservedTextBlocks.iterations == 0
    assert ObservedContent.iterations == 0
    assert len(submitted) == 1
    _result, raw, markdown, html = submitted[0](threading.Event())
    assert raw.startswith("<table><tr><td>changed</td></tr></table>\nblock-1")
    assert "changed" in markdown
    assert "changed" in html


def test_rapid_large_table_edits_publish_latest_complete_aggregates(qapp, qtbot):
    """Latest-wins table rebuilds must publish the last accepted table HTML."""
    from vibeocr.models.ocr_result import OCRResult, TextBlock

    initial_html = "<table><tr><td>old</td></tr></table>"
    result = OCRResult(
        text_blocks=[
            TextBlock(
                text=initial_html if index == 0 else f"block-{index}",
                score=0.9,
                bbox=None,
                content_index=index,
                label="table" if index == 0 else "text",
            )
            for index in range(50_000)
        ],
        content_list=[
            {"type": "table", "table_body": initial_html}
            if index == 0
            else {"type": "image"}
            for index in range(50_000)
        ],
    )
    tab = ConcreteTab()
    qtbot.addWidget(tab)
    tab._current_ocr_result = result

    tab._on_table_block_edited(0, "<table><tr><td>first</td></tr></table>")
    tab._on_table_block_edited(0, "<table><tr><td>second</td></tr></table>")

    qtbot.waitUntil(lambda: not tab._result_rebuild_jobs.is_running, timeout=3000)
    assert result.raw_text.startswith(
        "<table><tr><td>second</td></tr></table>\nblock-1"
    )
    assert "second" in result.markdown_text
    assert "first" not in result.markdown_text
    assert "second" in result.html_text


def test_large_nonaligned_table_edit_uses_prepared_reverse_index(
    qapp, qtbot, monkeypatch
):
    """A nonaligned content index must not trigger a 50k GUI linear search."""
    import threading
    import time

    from vibeocr.models.ocr_result import OCRResult, TextBlock

    class ObservedTextBlocks(list):
        iteration_threads: list[int] = []

        def __iter__(self):
            type(self).iteration_threads.append(threading.get_ident())
            return super().__iter__()

    block_count = 50_000
    table_html = "<table><tr><td>old</td></tr></table>"
    text_blocks = ObservedTextBlocks(
        TextBlock(
            text=table_html if index == block_count - 1 else f"block-{index}",
            score=0.9,
            bbox=None,
            content_index=(index + 1) % block_count,
            label="table" if index == block_count - 1 else "text",
        )
        for index in range(block_count)
    )
    result = OCRResult(
        text_blocks=text_blocks,
        content_list=[
            {"type": "table", "table_body": table_html},
            *({"type": "image"} for _ in range(1, block_count)),
        ],
    )
    tab = ConcreteTab()
    qtbot.addWidget(tab)
    tab._display_result(result)
    qtbot.waitUntil(
        lambda: tab._content_index_result is result
        and not tab._content_jobs.is_running,
        timeout=3000,
    )
    ObservedTextBlocks.iteration_threads = []
    submitted = []
    monkeypatch.setattr(
        tab._result_rebuild_jobs, "submit", lambda operation: submitted.append(operation)
    )

    before = time.perf_counter()
    tab._on_table_block_edited(
        0, "<table><tr><td>nonaligned</td></tr></table>"
    )
    elapsed_ms = (time.perf_counter() - before) * 1000

    assert elapsed_ms < 150
    assert threading.get_ident() not in ObservedTextBlocks.iteration_threads
    assert result.text_blocks[-1].text == (
        "<table><tr><td>nonaligned</td></tr></table>"
    )
    assert len(submitted) == 1
