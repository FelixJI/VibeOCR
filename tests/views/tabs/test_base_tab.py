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
        from vibeocr.models.ocr_options import OCROptions
        from vibeocr.core.pipelines import OCRPipeline

        tab = ConcreteTab()
        mineru_mock = Mock()
        paddlex_mock = Mock()
        tab._ocr_service = mineru_mock
        tab._paddlex_service = paddlex_mock
        options = OCROptions(pipeline=OCRPipeline.DOCUMENT_PARSING)
        assert tab._get_service_for_pipeline(options) is mineru_mock

    def test_get_service_for_pipeline_ocr(self, qapp):
        from vibeocr.models.ocr_options import OCROptions
        from vibeocr.core.pipelines import OCRPipeline

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
            content_list=[{"type": "text", "text": "Hello"}, {"type": "table", "text": "data"}],
            text_blocks=[
                TextBlock(text="Hello", score=0.9, bbox=(0, 0, 100, 50), content_index=0),
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

    def test_display_result_updates_state(self, qapp):
        from vibeocr.models.ocr_result import OCRResult, TextBlock

        tab = ConcreteTab()
        result = OCRResult(
            text_blocks=[TextBlock(text="Hello", score=0.9, bbox=(0, 0, 100, 50))],
        )
        tab._display_result(result)
        assert tab._current_ocr_result is result
