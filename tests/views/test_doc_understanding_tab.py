"""DocUnderstandingTab 单元测试

注意：直接导入 doc_understanding_tab 模块，避免 views/__init__.py 中的循环导入问题。
"""

import sys
from pathlib import Path

# 确保可以导入模块
src_path = Path(__file__).parent.parent.parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))


class TestDocUnderstandingTab:
    """测试 DocUnderstandingTab 组件。"""

    def test_tab_creation(self, qapp):
        """测试标签页可以被创建"""
        # 直接导入模块，避免 views/__init__.py 中的依赖问题
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "doc_understanding_tab",
            src_path / "vibeocr" / "views" / "doc_understanding_tab.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        DocUnderstandingTab = module.DocUnderstandingTab

        tab = DocUnderstandingTab()
        assert tab is not None

    def test_tab_has_chat_widget(self, qapp):
        """测试标签页包含 ChatWidget"""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "doc_understanding_tab",
            src_path / "vibeocr" / "views" / "doc_understanding_tab.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        DocUnderstandingTab = module.DocUnderstandingTab

        tab = DocUnderstandingTab()
        assert tab._chat_widget is not None

    def test_tab_conversation_history(self, qapp):
        """测试对话历史管理"""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "doc_understanding_tab",
            src_path / "vibeocr" / "views" / "doc_understanding_tab.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        DocUnderstandingTab = module.DocUnderstandingTab

        tab = DocUnderstandingTab()

        # 初始状态
        assert len(tab._conversation_history) == 0

    def test_tab_get_selected_model(self, qapp):
        """测试获取选中的模型"""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "doc_understanding_tab",
            src_path / "vibeocr" / "views" / "doc_understanding_tab.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        DocUnderstandingTab = module.DocUnderstandingTab

        tab = DocUnderstandingTab()
        model = tab.get_selected_model()
        assert model in ["PP-DocBee-2B", "PP-DocBee-7B", "PP-DocBee2-3B"]

    def test_tab_supported_formats(self, qapp):
        """测试支持的文件格式"""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "doc_understanding_tab",
            src_path / "vibeocr" / "views" / "doc_understanding_tab.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        DocUnderstandingTab = module.DocUnderstandingTab

        assert hasattr(DocUnderstandingTab, "SUPPORTED_FORMATS")
        assert ".png" in DocUnderstandingTab.SUPPORTED_FORMATS
        assert ".jpg" in DocUnderstandingTab.SUPPORTED_FORMATS
        assert ".pdf" in DocUnderstandingTab.SUPPORTED_FORMATS
