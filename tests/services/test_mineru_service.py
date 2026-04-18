"""MinerU 服务测试"""

import base64
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from vibeocr.models.ocr_result import OCRResult
from vibeocr.services.mineru_service import MinerUService


def _make_api_response(md_content="", content_list=None, images=None):
    """构造模拟的 API 响应"""
    file_result = {}
    if md_content:
        file_result["md_content"] = md_content
    if content_list is not None:
        file_result["content_list"] = json.dumps(content_list)
    if images is not None:
        file_result["images"] = images
    return {"results": {"input": file_result}}


class TestMinerUService:
    """MinerU 服务测试"""

    def setup_method(self):
        """每个测试前重置单例"""
        MinerUService._instance = None
        MinerUService._initialized = False
        MinerUService._api_process = None
        MinerUService._api_url = ""

    def test_singleton(self):
        with patch.object(MinerUService, "_ensure_api_running"):
            s1 = MinerUService()
            s2 = MinerUService()
        assert s1 is s2

    def test_parse_returns_ocr_result(self):
        """调用 parse 应返回 OCRResult"""
        service = MinerUService.__new__(MinerUService)
        service._api_url = "http://127.0.0.1:9999"
        service._api_process = None

        api_response = _make_api_response(
            md_content="# Test\nHello world",
            content_list=[{"type": "text", "text": "Hello world"}],
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = api_response
        mock_resp.raise_for_status = MagicMock()

        with (
            patch.object(service, "_ensure_api_running"),
            patch("vibeocr.services.mineru_service.httpx") as mock_httpx,
        ):
            mock_httpx.post.return_value = mock_resp
            result = service.parse(b"fake_pdf_data", "application/pdf")

        assert isinstance(result, OCRResult)
        assert "Test" in result.raw_text
        assert result.pipeline_type == "MinerU"

    def test_parse_with_images(self):
        """解析包含图片的响应"""
        img_bytes = b"\x89PNG\r\n\x1a\nfake"
        b64_img = base64.b64encode(img_bytes).decode()

        service = MinerUService.__new__(MinerUService)
        service._api_url = "http://127.0.0.1:9999"
        service._api_process = None

        api_response = _make_api_response(
            md_content="# Test",
            images={"img_0.png": f"data:image/png;base64,{b64_img}"},
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = api_response
        mock_resp.raise_for_status = MagicMock()

        with (
            patch.object(service, "_ensure_api_running"),
            patch("vibeocr.services.mineru_service.httpx") as mock_httpx,
        ):
            mock_httpx.post.return_value = mock_resp
            result = service.parse(b"fake_pdf_data", "application/pdf")

        assert "img_0.png" in result.images
        assert result.images["img_0.png"] == img_bytes

    def test_parse_sends_correct_params(self):
        """parse 应发送正确的 API 参数"""
        service = MinerUService.__new__(MinerUService)
        service._api_url = "http://127.0.0.1:9999"
        service._api_process = None

        api_response = _make_api_response(md_content="# Result")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = api_response
        mock_resp.raise_for_status = MagicMock()

        with (
            patch.object(service, "_ensure_api_running"),
            patch("vibeocr.services.mineru_service.httpx") as mock_httpx,
        ):
            mock_httpx.post.return_value = mock_resp
            service.parse(b"fake_image_data", "image/png")

        call_args = mock_httpx.post.call_args
        assert call_args.kwargs["files"]["files"][0] == "input.png"
        assert call_args.kwargs["data"]["return_md"] == "true"
        assert call_args.kwargs["data"]["return_content_list"] == "true"
        assert call_args.kwargs["data"]["return_images"] == "true"

    def test_ensure_api_running_starts_process(self):
        """_ensure_api_running 应在 API 未运行时启动进程"""
        MinerUService._api_url = ""
        MinerUService._api_process = None

        mock_process = MagicMock()
        mock_process.poll.return_value = None

        mock_health_resp = MagicMock()
        mock_health_resp.status_code = 200

        with (
            patch.object(MinerUService, "_resolve_python_executable",
                         return_value=Path("/fake/python.exe")),
            patch("vibeocr.services.mineru_service.subprocess.Popen") as mock_popen,
            patch("vibeocr.services.mineru_service.httpx") as mock_httpx,
            patch("vibeocr.services.mineru_service.socket"),
        ):
            mock_popen.return_value = mock_process
            mock_httpx.get.return_value = mock_health_resp
            MinerUService._ensure_api_running(MinerUService())

        assert MinerUService._api_url != ""
        mock_popen.assert_called_once()

    def test_start_api_uses_python_module(self):
        """_start_api 应使用 python -m 方式启动"""
        MinerUService._api_url = ""
        MinerUService._api_process = None

        mock_process = MagicMock()
        mock_process.poll.return_value = None

        mock_health_resp = MagicMock()
        mock_health_resp.status_code = 200

        with (
            patch.object(MinerUService, "_resolve_python_executable",
                         return_value=Path("/fake/python.exe")),
            patch("vibeocr.services.mineru_service.subprocess.Popen") as mock_popen,
            patch("vibeocr.services.mineru_service.httpx") as mock_httpx,
            patch("vibeocr.services.mineru_service.socket"),
        ):
            mock_popen.return_value = mock_process
            mock_httpx.get.return_value = mock_health_resp
            MinerUService._start_api(MinerUService())

        cmd = mock_popen.call_args[0][0]
        assert "-m" in cmd
        assert "mineru.cli.fast_api" in cmd
        assert "--host" in cmd
        assert "--port" in cmd

    def test_shutdown(self):
        """关闭服务不报错"""
        MinerUService._api_process = None
        MinerUService._api_url = ""
        service = MinerUService.__new__(MinerUService)
        service.shutdown()

    def test_shutdown_terminates_process(self):
        """关闭服务时应终止子进程"""
        mock_process = MagicMock()
        mock_process.wait.return_value = 0

        MinerUService._api_process = mock_process
        MinerUService._api_url = "http://127.0.0.1:9999"
        service = MinerUService.__new__(MinerUService)

        service.shutdown()

        mock_process.terminate.assert_called_once()
        assert MinerUService._api_process is None
        assert MinerUService._api_url == ""
