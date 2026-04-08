"""MinerU 服务测试"""

import json
from unittest.mock import MagicMock, patch

import pytest

from vibeocr.core.pipelines import OCRPipeline
from vibeocr.models.ocr_options import OCROptions
from vibeocr.models.ocr_result import OCRResult
from vibeocr.services.mineru_service import MinerUService


class TestMinerUService:
    """MinerU 服务测试"""

    def setup_method(self):
        """每个测试前重置单例"""
        MinerUService._instance = None
        MinerUService._initialized = False

    def test_singleton(self):
        s1 = MinerUService()
        s2 = MinerUService()
        assert s1 is s2

    def test_check_api_not_running(self):
        """API 未运行时返回 False"""
        with patch("urllib.request.urlopen", side_effect=Exception("connection refused")):
            service = MinerUService.__new__(MinerUService)
            service._api_url = "http://127.0.0.1:8000"
            assert not service._check_api_running()

    def test_check_api_running(self):
        """API 运行时返回 True"""
        mock_resp = MagicMock()
        mock_resp.status = 200
        with patch("urllib.request.urlopen", return_value=mock_resp):
            service = MinerUService.__new__(MinerUService)
            service._api_url = "http://127.0.0.1:8000"
            assert service._check_api_running()

    def test_parse_returns_ocr_result(self):
        """调用 parse 应返回 OCRResult"""
        service = MinerUService.__new__(MinerUService)
        service._api_url = "http://127.0.0.1:8000"
        service._output_dir = None
        service._api_process = None

        # Mock _check_api_running 和 _call_api
        mock_result = {
            "md_content": "# Test\nHello world",
            "content_list": [
                {"type": "title", "text": "Test"},
                {"type": "text", "text": "Hello world"},
            ],
        }
        service._check_api_running = MagicMock(return_value=True)
        service._call_api = MagicMock(return_value=mock_result)

        options = OCROptions(pipeline=OCRPipeline.DOCUMENT_PARSING)
        result = service.parse(b"fake_image_data", "image/png", options)

        assert isinstance(result, OCRResult)
        assert result.raw_text != ""
        assert result.markdown_text != ""
        assert result.pipeline_type == "MinerU"

    def test_parse_with_options(self):
        """解析时传递 MineRU 选项"""
        service = MinerUService.__new__(MinerUService)
        service._api_url = "http://127.0.0.1:8000"
        service._output_dir = None
        service._api_process = None

        mock_result = {"md_content": "test", "content_list": []}
        service._check_api_running = MagicMock(return_value=True)
        service._call_api = MagicMock(return_value=mock_result)

        options = OCROptions(
            pipeline=OCRPipeline.DOCUMENT_PARSING,
            parse_method="ocr",
            enable_formula=False,
            enable_table=False,
        )
        result = service.parse(b"data", "application/pdf", options)
        assert isinstance(result, OCRResult)

    def test_parse_pdf(self):
        """解析 PDF 文件"""
        service = MinerUService.__new__(MinerUService)
        service._api_url = "http://127.0.0.1:8000"
        service._output_dir = None
        service._api_process = None

        mock_result = {"md_content": "# PDF Content", "content_list": []}
        service._check_api_running = MagicMock(return_value=True)
        service._call_api = MagicMock(return_value=mock_result)

        result = service.parse(b"%PDF-1.4 fake", "application/pdf")
        assert isinstance(result, OCRResult)

    def test_shutdown(self):
        """关闭服务不报错"""
        service = MinerUService.__new__(MinerUService)
        service._api_process = None
        service.shutdown()  # 应该不报错
