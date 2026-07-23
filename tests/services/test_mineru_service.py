"""MinerU 服务测试"""

import base64
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# mineru_service 依赖 httpx；缺该依赖时整文件跳过而非 collection error
pytest.importorskip("httpx", reason="httpx not installed")

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
        MinerUService._job_guard = None

    def test_singleton(self):
        with patch.object(MinerUService, "_ensure_api_running"):
            s1 = MinerUService()
            s2 = MinerUService()
        assert s1 is s2

    def test_shutdown_closes_job_guard_if_present(self):
        """shutdown 时若 _job_guard 存在则关闭并置 None。"""
        service = MinerUService.__new__(MinerUService)
        service._api_url = ""
        MinerUService._api_process = None
        mock_guard = MagicMock()
        MinerUService._job_guard = mock_guard

        service.shutdown()

        mock_guard.close.assert_called_once()
        assert MinerUService._job_guard is None

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
        assert "Hello world" in result.raw_text
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

    def test_parse_sends_lang_list(self):
        """parse 应发送 lang_list 参数"""
        service = MinerUService.__new__(MinerUService)
        service._api_url = "http://127.0.0.1:9999"
        service._api_process = None

        api_response = _make_api_response(md_content="# R")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = api_response

        from vibeocr.core.pipelines import OCRPipeline
        from vibeocr.models.ocr_options import OCROptions

        options = OCROptions(
            pipeline=OCRPipeline.DOCUMENT_PARSING,
            lang_list=["zh", "en"],
            start_page_id=2,
            end_page_id=10,
        )

        with (
            patch.object(service, "_ensure_api_running"),
            patch("vibeocr.services.mineru_service.httpx") as mock_httpx,
        ):
            mock_httpx.post.return_value = mock_resp
            service.parse(b"data", "application/pdf", options)

        data = mock_httpx.post.call_args.kwargs["data"]
        assert data["lang_list"] == "zh,en"
        assert data["start_page_id"] == "2"
        assert data["end_page_id"] == "10"

    def test_parse_empty_lang_list_not_sent(self):
        """空 lang_list 不应传实际值"""
        service = MinerUService.__new__(MinerUService)
        service._api_url = "http://127.0.0.1:9999"
        service._api_process = None

        api_response = _make_api_response(md_content="# R")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = api_response

        from vibeocr.models.ocr_options import OCROptions

        options = OCROptions(lang_list=[])

        with (
            patch.object(service, "_ensure_api_running"),
            patch("vibeocr.services.mineru_service.httpx") as mock_httpx,
        ):
            mock_httpx.post.return_value = mock_resp
            service.parse(b"data", "application/pdf", options)

        data = mock_httpx.post.call_args.kwargs["data"]
        assert data.get("lang_list", "") == ""

    def test_parse_checks_response_status(self):
        """非 completed 状态应抛异常"""
        service = MinerUService.__new__(MinerUService)
        service._api_url = "http://127.0.0.1:9999"
        service._api_process = None

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "failed", "results": {}}

        with (
            patch.object(service, "_ensure_api_running"),
            patch("vibeocr.services.mineru_service.httpx") as mock_httpx,
        ):
            mock_httpx.post.return_value = mock_resp
            with pytest.raises(RuntimeError, match="failed"):
                service.parse(b"data", "application/pdf")

    def test_default_backend_is_hybrid(self):
        """默认后端应为 hybrid-engine"""
        service = MinerUService.__new__(MinerUService)
        service._api_url = "http://127.0.0.1:9999"
        service._api_process = None

        api_response = _make_api_response(md_content="# R")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = api_response

        with (
            patch.object(service, "_ensure_api_running"),
            patch("vibeocr.services.mineru_service.httpx") as mock_httpx,
        ):
            mock_httpx.post.return_value = mock_resp
            service.parse(b"data", "application/pdf")

        data = mock_httpx.post.call_args.kwargs["data"]
        assert data["backend"] == "hybrid-engine"
        assert data["effort"] == "medium"

    def test_fallback_chain_starts_from_hybrid(self):
        """回退链应从 hybrid-engine 开始"""
        service = MinerUService.__new__(MinerUService)
        service._api_url = "http://127.0.0.1:9999"
        service._api_process = None

        error_resp = MagicMock()
        error_resp.status_code = 500
        error_resp.json.return_value = {"message": "fail"}
        error_resp.text = '{"message": "fail"}'

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = _make_api_response(md_content="# OK")

        with (
            patch.object(service, "_ensure_api_running"),
            patch("vibeocr.services.mineru_service.httpx") as mock_httpx,
        ):
            mock_httpx.post.side_effect = [error_resp, ok_resp]
            service.parse(b"data", "application/pdf")

        calls = mock_httpx.post.call_args_list
        assert calls[0].kwargs["data"]["backend"] == "hybrid-engine"
        assert calls[1].kwargs["data"]["backend"] == "vlm-engine"

    def test_ensure_api_running_starts_process(self):
        """_ensure_api_running 应在 API 未运行时启动进程"""
        MinerUService._api_url = ""
        MinerUService._api_process = None

        mock_process = MagicMock()
        mock_process.poll.return_value = None
        # JobObjectGuard.assign_from_popen 会读 popen.pid 并传给 ctypes OpenProcess；
        # MagicMock 的 .pid 是另一个 MagicMock，ctypes 强转 c_uint32 时触发无限
        # __index__ → 子 mock 递归，最终 C 层栈溢出崩溃整个进程。给一个真实 int pid。
        mock_process.pid = 12345

        mock_health_resp = MagicMock()
        mock_health_resp.status_code = 200

        with (
            patch.object(
                MinerUService,
                "_resolve_python_executable",
                return_value=Path("/fake/python.exe"),
            ),
            patch("vibeocr.services.mineru_service.subprocess.Popen") as mock_popen,
            patch("vibeocr.services.mineru_service.httpx") as mock_httpx,
            patch("vibeocr.services.mineru_service.socket"),
            # JobObjectGuard.assign_from_popen 调真实 OpenProcess(pid=12345) 会因
            # 无效句柄失败并记 warning；mock 掉守卫避免噪声并隔离被测逻辑。
            patch("vibeocr.services.mineru_service.JobObjectGuard") as mock_guard_cls,
            # NetworkDetector.__init__ 会触发 generate_machine_id() 调 wmic
            # （subprocess.Popen），与被 mock 的 Popen 冲突导致 assert_called_once 失败。
            # mock 掉 NetworkDetector 消除该副作用。
            patch("vibeocr.network_detector.NetworkDetector"),
        ):
            mock_guard_cls.return_value.assign_from_popen.return_value = True
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
        # 同 test_ensure_api_running_starts_process：避免 ctypes 强转 MagicMock.pid
        # 导致的栈溢出。
        mock_process.pid = 12345

        mock_health_resp = MagicMock()
        mock_health_resp.status_code = 200

        with (
            patch.object(
                MinerUService,
                "_resolve_python_executable",
                return_value=Path("/fake/python.exe"),
            ),
            patch("vibeocr.services.mineru_service.subprocess.Popen") as mock_popen,
            patch("vibeocr.services.mineru_service.httpx") as mock_httpx,
            patch("vibeocr.services.mineru_service.socket"),
            patch("vibeocr.services.mineru_service.JobObjectGuard"),
            # 同上：避免 generate_machine_id 的 wmic Popen 干扰断言。
            patch("vibeocr.network_detector.NetworkDetector"),
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


class TestBuildOcrResult:
    """测试 _build_ocr_result 数据层重构"""

    def _make_service(self):
        service = MinerUService.__new__(MinerUService)
        service._api_url = "http://127.0.0.1:9999"
        service._api_process = None
        return service

    def test_raw_text_is_plain_from_content_list(self):
        """raw_text 应为从 content_list 提取的纯文本"""
        service = self._make_service()
        content_list = [
            {"type": "text", "text": "Hello ", "bbox": [0, 0, 100, 50], "page_idx": 0},
            {
                "type": "text",
                "text": "world",
                "text_level": 1,
                "bbox": [0, 60, 100, 80],
                "page_idx": 0,
            },
            {
                "type": "table",
                "table_body": "<tr><td>Data</td></tr>",
                "bbox": [0, 100, 500, 200],
                "page_idx": 0,
            },
            {
                "type": "equation",
                "text": "E=mc^2",
                "bbox": [0, 210, 200, 240],
                "page_idx": 0,
            },
            {
                "type": "list",
                "list_items": ["item1", "item2"],
                "bbox": [0, 250, 200, 350],
                "page_idx": 0,
            },
        ]
        api_resp = _make_api_response(
            md_content="# Hello world\n\n| Data |\n",
            content_list=content_list,
        )
        result = service._build_ocr_result(api_resp, "input.pdf", data=b"fake")
        assert "#" not in result.raw_text
        assert "Hello" in result.raw_text
        assert "world" in result.raw_text
        assert "Data" in result.raw_text
        assert "item1" in result.raw_text
        assert "item2" in result.raw_text

    def test_text_blocks_have_page_idx(self):
        service = self._make_service()
        content_list = [
            {"type": "text", "text": "P0", "bbox": [0, 0, 100, 50], "page_idx": 0},
            {"type": "text", "text": "P1", "bbox": [0, 0, 100, 50], "page_idx": 1},
        ]
        api_resp = _make_api_response(md_content="t", content_list=content_list)
        result = service._build_ocr_result(api_resp, "input.pdf", data=None)
        assert len(result.text_blocks) == 2
        assert result.text_blocks[0].page_idx == 0
        assert result.text_blocks[1].page_idx == 1

    def test_text_blocks_bbox_is_normalized(self):
        service = self._make_service()
        content_list = [
            {"type": "text", "text": "t", "bbox": [10, 20, 990, 500], "page_idx": 0},
        ]
        api_resp = _make_api_response(md_content="t", content_list=content_list)
        result = service._build_ocr_result(api_resp, "input.png", data=b"fake")
        assert result.text_blocks[0].bbox == (10.0, 20.0, 990.0, 500.0)

    def test_text_blocks_skip_no_bbox(self):
        service = self._make_service()
        content_list = [
            {"type": "text", "text": "with", "bbox": [0, 0, 100, 50], "page_idx": 0},
            {"type": "text", "text": "without", "page_idx": 0},
        ]
        api_resp = _make_api_response(md_content="t", content_list=content_list)
        result = service._build_ocr_result(api_resp, "input.pdf", data=None)
        assert len(result.text_blocks) == 1
        assert result.text_blocks[0].text == "with"

    def test_table_block_text_from_table_body(self):
        service = self._make_service()
        content_list = [
            {
                "type": "table",
                "table_body": "<tr><td>A</td><td>B</td></tr>",
                "bbox": [0, 0, 500, 100],
                "page_idx": 0,
            },
        ]
        api_resp = _make_api_response(md_content="t", content_list=content_list)
        result = service._build_ocr_result(api_resp, "input.pdf", data=None)
        assert result.text_blocks[0].text == "A B"

    def test_text_with_scores_from_text_blocks(self):
        service = self._make_service()
        content_list = [
            {"type": "text", "text": "A", "bbox": [0, 0, 50, 20], "page_idx": 0},
            {"type": "text", "text": "B", "bbox": [0, 30, 50, 50], "page_idx": 0},
        ]
        api_resp = _make_api_response(md_content="A B", content_list=content_list)
        result = service._build_ocr_result(api_resp, "input.pdf", data=None)
        assert len(result.text_with_scores) == 2
        assert result.text_with_scores[0] == ("A", 1.0)
        assert result.avg_score == 1.0


class TestMinerUIntegration:
    """集成测试 — 验证多页 PDF 数据完整性"""

    def _make_service(self):
        service = MinerUService.__new__(MinerUService)
        service._api_url = "http://127.0.0.1:9999"
        service._api_process = None
        return service

    def test_multi_page_pdf_result_structure(self):
        """多页 PDF 应生成含正确 page_idx 的 text_blocks"""
        service = self._make_service()
        content_list = [
            {
                "type": "text",
                "text": "Title",
                "text_level": 1,
                "bbox": [10, 10, 500, 60],
                "page_idx": 0,
            },
            {
                "type": "text",
                "text": "Para on page 0",
                "bbox": [10, 80, 800, 200],
                "page_idx": 0,
            },
            {
                "type": "table",
                "table_body": "<tr><td>A</td></tr>",
                "bbox": [10, 220, 800, 400],
                "page_idx": 0,
            },
            {
                "type": "image",
                "img_path": "images/img_0.png",
                "image_caption": ["Fig 1"],
                "bbox": [10, 10, 800, 500],
                "page_idx": 1,
            },
            {
                "type": "equation",
                "text": "E=mc^2",
                "bbox": [10, 520, 400, 580],
                "page_idx": 1,
            },
            {
                "type": "text",
                "text": "Last page",
                "bbox": [10, 600, 500, 650],
                "page_idx": 2,
            },
        ]
        b64_img = base64.b64encode(b"\x89PNG fake").decode()
        api_resp = _make_api_response(
            md_content="# Title\nPara on page 0\n\n| A |\n\n## Page 2",
            content_list=content_list,
            images={"images/img_0.png": f"data:image/png;base64,{b64_img}"},
        )
        result = service._build_ocr_result(api_resp, "input.pdf", data=None)

        # text_blocks 数量和 page_idx
        assert len(result.text_blocks) == 6
        pages = [b.page_idx for b in result.text_blocks]
        assert pages == [0, 0, 0, 1, 1, 2]

        # content_list 保持原样透传
        assert len(result.content_list) == 6
        assert result.content_list[3]["img_path"] == "images/img_0.png"

        # images 解码正确
        assert "images/img_0.png" in result.images

        # raw_text 包含所有页的纯文本
        assert "Title" in result.raw_text
        assert "Para on page 0" in result.raw_text
        assert "A" in result.raw_text
        assert "Last page" in result.raw_text

        # markdown_text 保持 Markdown
        assert "#" in result.markdown_text

    def test_raw_text_excludes_markdown_syntax(self):
        """raw_text 不应包含 Markdown 语法"""
        service = self._make_service()
        content_list = [
            {
                "type": "text",
                "text": "Heading",
                "text_level": 2,
                "bbox": [0, 0, 100, 30],
                "page_idx": 0,
            },
            {
                "type": "equation",
                "text": "x^2",
                "bbox": [0, 40, 100, 60],
                "page_idx": 0,
            },
            {
                "type": "list",
                "list_items": ["first", "second"],
                "bbox": [0, 70, 100, 120],
                "page_idx": 0,
            },
        ]
        api_resp = _make_api_response(
            md_content="## Heading\n\n$x^2$\n\n- first\n- second",
            content_list=content_list,
        )
        result = service._build_ocr_result(api_resp, "input.pdf", data=None)

        # raw_text has content from all types
        assert "Heading" in result.raw_text
        assert "x^2" in result.raw_text
        assert "first" in result.raw_text
        assert "second" in result.raw_text
        # But NOT markdown syntax
        assert "##" not in result.raw_text
        assert "$" not in result.raw_text

    def test_bbox_preserves_normalized_coordinates(self):
        """bbox 应保持 [0,1000] 归一化坐标，不转换为像素"""
        service = self._make_service()
        content_list = [
            {
                "type": "text",
                "text": "test",
                "bbox": [100, 200, 900, 800],
                "page_idx": 0,
            },
        ]
        api_resp = _make_api_response(md_content="test", content_list=content_list)
        result = service._build_ocr_result(api_resp, "input.png", data=b"fake_png")

        assert result.text_blocks[0].bbox == (100.0, 200.0, 900.0, 800.0)


class TestDiscardedBlocksFilter:
    """测试废弃块（header/footer/page_number 等）过滤"""

    def _make_service(self):
        service = MinerUService.__new__(MinerUService)
        service._api_url = "http://127.0.0.1:9999"
        service._api_process = None
        return service

    def test_raw_text_excludes_discarded_blocks(self):
        """raw_text 不应包含 header/footer/page_number 等废弃块"""
        service = self._make_service()
        content_list = [
            {
                "type": "header",
                "text": "期刊名称",
                "bbox": [100, 10, 900, 40],
                "page_idx": 0,
            },
            {
                "type": "text",
                "text": "正文内容",
                "bbox": [100, 100, 900, 200],
                "page_idx": 0,
            },
            {
                "type": "footer",
                "text": "页脚文字",
                "bbox": [100, 950, 900, 990],
                "page_idx": 0,
            },
            {
                "type": "page_number",
                "text": "1",
                "bbox": [450, 980, 550, 999],
                "page_idx": 0,
            },
            {
                "type": "page_footnote",
                "text": "脚注内容",
                "bbox": [100, 900, 900, 940],
                "page_idx": 0,
            },
            {
                "type": "aside_text",
                "text": "旁注文字",
                "bbox": [0, 100, 90, 200],
                "page_idx": 0,
            },
        ]
        api_resp = _make_api_response(md_content="正文内容", content_list=content_list)
        result = service._build_ocr_result(api_resp, "input.pdf", data=None)

        assert "正文内容" in result.raw_text
        assert "期刊名称" not in result.raw_text
        assert "页脚文字" not in result.raw_text
        assert "1" not in result.raw_text.split("\n")
        assert "脚注内容" not in result.raw_text
        assert "旁注文字" not in result.raw_text

    def test_text_blocks_excludes_discarded_blocks(self):
        """text_blocks 不应包含废弃块的 TextBlock"""
        service = self._make_service()
        content_list = [
            {
                "type": "header",
                "text": "Header",
                "bbox": [10, 10, 990, 40],
                "page_idx": 0,
            },
            {
                "type": "text",
                "text": "Body",
                "bbox": [10, 100, 990, 200],
                "page_idx": 0,
            },
            {
                "type": "footer",
                "text": "Footer",
                "bbox": [10, 950, 990, 990],
                "page_idx": 0,
            },
        ]
        api_resp = _make_api_response(md_content="Body", content_list=content_list)
        result = service._build_ocr_result(api_resp, "input.pdf", data=None)

        assert len(result.text_blocks) == 1
        assert result.text_blocks[0].text == "Body"

    def test_content_list_preserved_unchanged(self):
        """content_list 应保持原样透传（不过滤）"""
        service = self._make_service()
        content_list = [
            {"type": "header", "text": "H", "bbox": [0, 0, 100, 30], "page_idx": 0},
            {"type": "text", "text": "T", "bbox": [0, 50, 100, 80], "page_idx": 0},
        ]
        api_resp = _make_api_response(md_content="T", content_list=content_list)
        result = service._build_ocr_result(api_resp, "input.pdf", data=None)

        # content_list 包含所有块（含废弃块）
        assert len(result.content_list) == 2
        assert result.content_list[0]["type"] == "header"


class TestBuildOcrResultV2:
    """测试 V2 格式 content_list 的处理"""

    def _make_service(self):
        service = MinerUService.__new__(MinerUService)
        service._api_url = "http://127.0.0.1:9999"
        service._api_process = None
        return service

    def test_v2_content_list_produces_text_blocks(self):
        """V2 格式的 content_list 应正确生成 text_blocks"""
        service = self._make_service()
        v2_content_list = [
            [
                {
                    "type": "title",
                    "content": {
                        "title_content": [{"type": "text", "content": "Introduction"}],
                        "level": 1,
                    },
                    "bbox": [83, 121, 917, 156],
                },
                {
                    "type": "paragraph",
                    "content": {
                        "paragraph_content": [
                            {"type": "text", "content": "Body text here"}
                        ]
                    },
                    "bbox": [83, 200, 917, 300],
                },
            ],
        ]
        api_resp = _make_api_response(
            md_content="# Introduction\nBody text here",
            content_list=v2_content_list,
        )
        result = service._build_ocr_result(api_resp, "input.pdf", data=None)
        assert len(result.text_blocks) == 2
        assert "Introduction" in result.raw_text
        assert "Body text here" in result.raw_text
        # page_idx 应从 V2 按页分组推断
        assert result.text_blocks[0].page_idx == 0
        assert result.text_blocks[1].page_idx == 0

    def test_v2_multi_page_content_list(self):
        """V2 多页格式应正确分配 page_idx"""
        service = self._make_service()
        v2_content_list = [
            [
                {
                    "type": "title",
                    "content": {
                        "title_content": [{"type": "text", "content": "P0 Title"}],
                        "level": 1,
                    },
                    "bbox": [0, 0, 100, 30],
                },
            ],
            [
                {
                    "type": "paragraph",
                    "content": {
                        "paragraph_content": [{"type": "text", "content": "P1 Body"}]
                    },
                    "bbox": [0, 50, 100, 80],
                },
            ],
        ]
        api_resp = _make_api_response(
            md_content="# P0 Title\nP1 Body",
            content_list=v2_content_list,
        )
        result = service._build_ocr_result(api_resp, "input.pdf", data=None)
        assert len(result.text_blocks) == 2
        assert result.text_blocks[0].page_idx == 0
        assert result.text_blocks[1].page_idx == 1

    def test_v2_discarded_blocks_filtered(self):
        """V2 格式的 page_header 等应在 text_blocks 中过滤"""
        service = self._make_service()
        v2_content_list = [
            [
                {
                    "type": "page_header",
                    "content": {
                        "page_header_content": [{"type": "text", "content": "Header"}]
                    },
                    "bbox": [0, 0, 100, 30],
                },
                {
                    "type": "paragraph",
                    "content": {
                        "paragraph_content": [{"type": "text", "content": "Body"}]
                    },
                    "bbox": [0, 50, 100, 80],
                },
            ],
        ]
        api_resp = _make_api_response(md_content="Body", content_list=v2_content_list)
        result = service._build_ocr_result(api_resp, "input.pdf", data=None)
        assert len(result.text_blocks) == 1
        assert result.text_blocks[0].text == "Body"
        # raw_text 也不应包含 header
        assert "Header" not in result.raw_text

    def test_legacy_format_still_works(self):
        """修改后 legacy 格式仍应正确工作"""
        service = self._make_service()
        content_list = [
            {
                "type": "text",
                "text": "Hello world",
                "bbox": [10, 20, 100, 50],
                "page_idx": 0,
            },
        ]
        api_resp = _make_api_response(
            md_content="Hello world", content_list=content_list
        )
        result = service._build_ocr_result(api_resp, "input.pdf", data=None)
        assert len(result.text_blocks) == 1
        assert result.text_blocks[0].text == "Hello world"


class TestMinerUModelCheck:
    """MinerU 模型完整性校验测试(_check_models_available)"""

    def test_models_available_returns_true_when_returncode_zero(self):
        """子进程返回 0 时,判定模型就绪"""
        from unittest.mock import MagicMock

        service = MinerUService.__new__(MinerUService)
        fake_python = MagicMock()
        fake_python.exists.return_value = True

        with patch.object(service, "_resolve_python_executable", return_value=fake_python):
            with patch("vibeocr.env_manager.detect_network_source") as mock_net:
                mock_net.return_value = "international"
                with patch("vibeocr.services.mineru_service.subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(returncode=0, stdout=b"ok")
                    assert service._check_models_available() is True

    def test_models_available_returns_false_when_returncode_nonzero(self):
        """子进程返回非 0 时,判定模型缺失"""
        from unittest.mock import MagicMock

        service = MinerUService.__new__(MinerUService)
        fake_python = MagicMock()
        fake_python.exists.return_value = True

        with patch.object(service, "_resolve_python_executable", return_value=fake_python):
            with patch("vibeocr.env_manager.detect_network_source") as mock_net:
                mock_net.return_value = "international"
                with patch("vibeocr.services.mineru_service.subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(returncode=1, stdout=b"err")
                    assert service._check_models_available() is False

    def test_models_available_returns_false_on_timeout(self):
        """子进程超时时(可能在下载),判定模型缺失"""
        import subprocess as sp
        from unittest.mock import MagicMock

        service = MinerUService.__new__(MinerUService)
        fake_python = MagicMock()
        fake_python.exists.return_value = True

        with patch.object(service, "_resolve_python_executable", return_value=fake_python):
            with patch("vibeocr.env_manager.detect_network_source") as mock_net:
                mock_net.return_value = "international"
                with patch("vibeocr.services.mineru_service.subprocess.run") as mock_run:
                    mock_run.side_effect = sp.TimeoutExpired(cmd="mineru", timeout=30)
                    assert service._check_models_available() is False

    def test_ensure_api_running_raises_when_models_missing(self):
        """模型缺失时,_ensure_api_running 抛 RuntimeError 而非等 120s"""

        service = MinerUService.__new__(MinerUService)
        # 最小化初始化:跳过 __init__ 的单例逻辑
        service._lock = __import__("threading").Lock()
        MinerUService._api_url = None
        MinerUService._api_process = None

        with patch.object(service, "_check_api_running", return_value=False):
            with patch.object(service, "_check_models_available", return_value=False):
                with pytest.raises(RuntimeError, match="模型未下载"):
                    service._ensure_api_running()

    def test_probe_command_is_non_interactive(self):
        """探测命令必须非交互式:带 -m/--model_type 且 stdin=DEVNULL。

        回归守卫:MinerU CLI ``models_download`` 在缺 ``--model_type`` 时会
        触发 ``click.prompt`` 交互选择 (pipeline/vlm/all),非 TTY 子进程下被
        Click ``Aborted!`` -> returncode=1,导致已下载的模型被误判为缺失
        (见 2026-07-23 线上日志)。本测试锁定命令参数契约,防止回退。
        """
        import subprocess as sp

        service = MinerUService.__new__(MinerUService)
        fake_python = MagicMock()
        fake_python.exists.return_value = True
        fake_python.__str__ = lambda self: "python"  # type: ignore[assignment]

        with patch.object(service, "_resolve_python_executable", return_value=fake_python):
            with patch("vibeocr.env_manager.detect_network_source") as mock_net:
                mock_net.return_value = "international"
                with patch("vibeocr.services.mineru_service.subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(returncode=0, stdout=b"ok")
                    service._check_models_available()

        call = mock_run.call_args
        assert call is not None, "subprocess.run 未被调用"
        cmd = call.args[0]
        # 必须显式指定 model_type,否则 MinerU CLI 进入交互 prompt
        assert "-m" in cmd or "--model_type" in cmd, (
            f"探测命令缺 -m/--model_type,会触发 MinerU 交互 prompt: {cmd}"
        )
        # stdin 必须 DEVNULL:即使将来 CLI 行为变化,也不会因继承非 TTY stdin 而阻塞
        assert call.kwargs.get("stdin") == sp.DEVNULL, (
            f"探测命令未设 stdin=DEVNULL,非 TTY 下可能阻塞: {call.kwargs}"
        )

