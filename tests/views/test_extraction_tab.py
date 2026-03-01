# tests/views/test_extraction_tab.py
import sys
from pathlib import Path

# 直接添加源码路径以避免通过 views/__init__.py 导入
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import pytest
from PySide6.QtWidgets import QApplication

from vibeocr.views.extraction_tab import ExtractionTab


class TestExtractionTab:
    def test_create_tab(self, qtbot):
        """测试创建标签页"""
        tab = ExtractionTab()
        qtbot.addWidget(tab)
        assert tab is not None

    def test_get_extraction_options(self, qtbot):
        """测试获取抽取选项"""
        tab = ExtractionTab()
        qtbot.addWidget(tab)

        options = tab.get_extraction_options()
        assert options.use_doc_orientation is True
        assert options.use_seal_recognition is False

    def test_get_keys_from_template(self, qtbot):
        """测试从模板获取字段"""
        tab = ExtractionTab()
        qtbot.addWidget(tab)

        # 选择发票模板
        tab._combo_template.setCurrentText("发票信息")
        keys = tab.get_extraction_keys()
        assert "发票号码" in keys
        assert "金额" in keys

    def test_get_keys_from_custom(self, qtbot):
        """测试自定义字段"""
        tab = ExtractionTab()
        qtbot.addWidget(tab)

        tab._text_custom_keys.setPlainText("姓名\n日期\n金额")
        keys = tab.get_extraction_keys()
        assert keys == ["姓名", "日期", "金额"]

    def test_export_mode(self, qtbot):
        """测试导出模式"""
        tab = ExtractionTab()
        qtbot.addWidget(tab)

        # 默认合并导出
        assert tab.is_export_merged() is True

        # 切换到单独导出
        tab._radio_export_separate.setChecked(True)
        assert tab.is_export_merged() is False
