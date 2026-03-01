# tests/models/test_extraction_models.py
import pytest
from vibeocr.models.extraction_options import ExtractionOptions
from vibeocr.models.extraction_template import ExtractionTemplate
from vibeocr.models.llm_config import LLMConfig


class TestExtractionOptions:
    def test_default_values(self):
        """测试默认值"""
        options = ExtractionOptions()
        assert options.use_doc_orientation is True
        assert options.use_doc_unwarping is True
        assert options.use_general_ocr is True
        assert options.use_table_recognition is True
        assert options.use_seal_recognition is False

    def test_to_dict(self):
        """测试序列化为字典"""
        options = ExtractionOptions(use_seal_recognition=True)
        result = options.to_dict()
        assert result["use_doc_orientation_classify"] is True
        assert result["use_seal_recognition"] is True

    def test_from_dict(self):
        """测试从字典反序列化"""
        data = {"use_seal_recognition": True, "use_general_ocr": False}
        options = ExtractionOptions.from_dict(data)
        assert options.use_seal_recognition is True
        assert options.use_general_ocr is False


class TestExtractionTemplate:
    def test_create_template(self):
        """测试创建模板"""
        template = ExtractionTemplate(name="发票信息", keys=["发票号码", "金额"])
        assert template.name == "发票信息"
        assert len(template.keys) == 2

    def test_to_dict(self):
        """测试序列化"""
        template = ExtractionTemplate(name="测试", keys=["字段1", "字段2"])
        result = template.to_dict()
        assert result["name"] == "测试"
        assert result["keys"] == ["字段1", "字段2"]

    def test_from_dict(self):
        """测试反序列化"""
        data = {"name": "测试模板", "keys": ["a", "b", "c"]}
        template = ExtractionTemplate.from_dict(data)
        assert template.name == "测试模板"
        assert template.keys == ["a", "b", "c"]


class TestLLMConfig:
    def test_default_values(self):
        """测试默认值"""
        config = LLMConfig()
        assert config.enabled is False
        assert config.service_url == ""
        assert config.model_name == ""
        assert config.api_type == "openai"

    def test_mllm_config(self):
        """测试 MLLM 配置"""
        config = LLMConfig(
            enabled=True,
            service_url="http://127.0.0.1:8080/v1/chat/completions",
            model_name="PP-DocBee2",
            api_type="openai"
        )
        assert config.enabled is True
        assert config.is_mllm is True

    def test_is_configured(self):
        """测试是否已配置"""
        empty_config = LLMConfig()
        assert empty_config.is_configured() is False

        valid_config = LLMConfig(
            enabled=True,
            service_url="http://localhost:8080",
            model_name="model"
        )
        assert valid_config.is_configured() is True
