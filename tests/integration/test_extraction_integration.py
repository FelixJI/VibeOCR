# tests/integration/test_extraction_integration.py
"""信息抽取功能集成测试"""

import sys
from pathlib import Path
from unittest.mock import Mock

# 直接添加源码路径以避免通过 views/__init__.py 导入
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


from vibeocr.models.extraction_options import ExtractionOptions
from vibeocr.models.extraction_template import DEFAULT_TEMPLATES, ExtractionTemplate
from vibeocr.models.llm_config import LLMConfig
from vibeocr.views.extraction_tab import ExtractionTab
from vibeocr.workers.extraction_worker import ExtractionWorker


class TestExtractionIntegration:
    """信息抽取功能集成测试"""

    def test_full_extraction_flow(self, qtbot, tmp_path):
        """测试完整抽取流程"""
        # 创建测试图片
        test_image = tmp_path / "test.png"
        test_image.write_bytes(b"fake image data")

        # 创建标签页
        tab = ExtractionTab()
        qtbot.addWidget(tab)

        # 模拟 OCR 服务
        mock_service = Mock()
        tab.set_ocr_service(mock_service)

        # 配置抽取字段
        tab._text_custom_keys.setPlainText("姓名\n日期")
        keys = tab.get_extraction_keys()
        assert "姓名" in keys
        assert "日期" in keys

        # 验证选项
        options = tab.get_extraction_options()
        assert isinstance(options, ExtractionOptions)

    def test_extraction_worker_with_files(self, qtbot, tmp_path):
        """测试 Worker 处理文件"""
        # 创建测试文件
        test_image = tmp_path / "test.png"
        test_image.write_bytes(b"fake image data")

        files = [{"path": str(test_image), "name": "test.png"}]
        keys = ["姓名", "日期"]
        options = ExtractionOptions()

        mock_service = Mock()
        worker = ExtractionWorker(
            service=mock_service, files=files, keys=keys, options=options
        )

        assert worker is not None
        assert not worker._cancelled

        worker.cancel()
        assert worker._cancelled

    def test_llm_config_integration(self, qtbot):
        """测试 LLM 配置集成"""
        config = LLMConfig(
            enabled=True,
            service_url="http://localhost:8080/v1/chat/completions",
            model_name="test-model",
            api_key="test-key",
        )

        assert config.is_configured() is True

        # 测试序列化
        data = config.to_dict()
        config2 = LLMConfig.from_dict(data)
        assert config2.service_url == config.service_url
        assert config2.model_name == config.model_name

    def test_template_integration(self, qtbot):
        """测试模板集成"""
        # 测试预设模板
        assert len(DEFAULT_TEMPLATES) >= 3

        invoice_template = None
        for t in DEFAULT_TEMPLATES:
            if t.name == "发票信息":
                invoice_template = t
                break

        assert invoice_template is not None
        assert "发票号码" in invoice_template.keys
        assert "金额" in invoice_template.keys

        # 测试自定义模板
        custom_template = ExtractionTemplate(name="测试模板", keys=["字段1", "字段2"])
        data = custom_template.to_dict()
        template2 = ExtractionTemplate.from_dict(data)
        assert template2.name == "测试模板"
        assert template2.keys == ["字段1", "字段2"]

    def test_extraction_tab_export_options(self, qtbot):
        """测试导出选项"""
        tab = ExtractionTab()
        qtbot.addWidget(tab)

        # 默认合并导出
        assert tab.is_export_merged() is True

        # 切换到单独导出
        if tab._radio_export_separate:
            tab._radio_export_separate.setChecked(True)
            assert tab.is_export_merged() is False

        # 测试导出格式
        if tab._combo_format:
            assert tab.get_export_format() == "json"
            tab._combo_format.setCurrentText("Excel")
            assert tab.get_export_format() == "excel"

    def test_extraction_tab_options(self, qtbot):
        """测试 PP-ChatOCRv4 选项"""
        tab = ExtractionTab()
        qtbot.addWidget(tab)

        # 默认选项
        options = tab.get_extraction_options()
        assert options.use_doc_orientation is True
        assert options.use_doc_unwarping is True
        assert options.use_general_ocr is True
        assert options.use_table_recognition is True
        assert options.use_seal_recognition is False

        # 修改选项
        if tab._chk_seal_recognition:
            tab._chk_seal_recognition.setChecked(True)
            options = tab.get_extraction_options()
            assert options.use_seal_recognition is True
