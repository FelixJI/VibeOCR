"""OcrHttpClient 测试。

验证 UI 侧客户端的接线：recognize_sync 正确构造 multipart 请求、解析响应；
进程生命周期用 mock（不启动真实 worker 子进程）。端到端真实子进程测试见
test_ocr_worker_http_e2e（可选，需 PaddleOCR 环境）。
"""

from unittest.mock import MagicMock

import httpx
import pytest

pytest.importorskip("httpx", reason="httpx not installed")

from vibeocr.worker_host.ocr_http_client import (
    OcrHttpClient,
    OcrHttpError,
)


def _wire_result_dict() -> dict:
    """构造 worker /ocr/recognize 返回的 wire dict（对齐 _result_payload）。"""
    return {
        "text": "hello",
        "raw_text": "hello",
        "markdown_text": "hello",
        "html_text": "<p>hello</p>",
        "pipeline": "OCR",
        "raw_blocks": [],
        "text_blocks": [],
        "text_with_scores": [["hello", 0.95]],
        "content_list": [],
        "image_width": 100,
        "image_height": 50,
        "preproc_angle": 0,
        "preproc_img_w": 0,
        "preproc_img_h": 0,
    }


def _make_started_client() -> OcrHttpClient:
    """构造一个已标记 started 的 client（不启动真实进程）。

    _ensure_started 会在 _is_alive()=False 时重启真实 worker，这里直接 patch
    掉 _ensure_started 为 no-op，避免测试拉起子进程。
    """
    client = OcrHttpClient.__new__(OcrHttpClient)
    client._process = None
    client._base_url = "http://127.0.0.1:9999"
    client._job_guard = None
    client._lock = __import__("threading").RLock()
    client._started = True
    client._http_clients = {}
    client._log_thread = None
    client._use_gpu = True
    # 避免真实启动 worker：recognize_sync 调 _ensure_started，这里替换为 no-op。
    client._ensure_started = lambda: None  # type: ignore[method-assign]
    return client


def test_recognize_sync_reconstructs_ocr_result():
    """recognize_sync 构造 multipart 请求，返回重建后的 OCRResult。"""
    client = _make_started_client()
    mock_http = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = _wire_result_dict()
    mock_http.post.return_value = mock_resp
    client._http_clients[__import__("threading").get_ident()] = mock_http

    result = client.recognize_sync(
        b"fake-png",
        pipeline="OCR",
        language="ch",
        options={"use_doc_orientation_classify": True},
    )

    # 返回重建后的 OCRResult（而非原始 wire dict），供 UI 使用 has_content_list 等属性。
    from vibeocr.models.ocr_result import OCRResult, TextBlock

    assert isinstance(result, OCRResult)
    assert result.raw_text == "hello"
    assert result.pipeline_type == "OCR"
    assert result.text_with_scores == [("hello", 0.95)]
    mock_http.post.assert_called_once()
    call = mock_http.post.call_args
    assert call.args[0] == "/ocr/recognize"
    # multipart 含 image 文件字段
    assert "image" in call.kwargs["files"]
    # form data 含 pipeline/language/options_json
    data = call.kwargs["data"]
    assert data["pipeline"] == "OCR"
    assert data["language"] == "ch"
    assert '"use_doc_orientation_classify": true' in data["options_json"]


def test_recognize_sync_defaults_no_language_no_options():
    """不传 language/options 时 form data 不含这两个字段。"""
    client = _make_started_client()
    mock_http = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = _wire_result_dict()
    mock_http.post.return_value = mock_resp
    client._http_clients[__import__("threading").get_ident()] = mock_http

    client.recognize_sync(b"x")

    data = mock_http.post.call_args.kwargs["data"]
    assert data == {"pipeline": "OCR"}


def test_recognize_sync_raises_on_http_error():
    """worker 返回非 200 时抛 OcrHttpError（含状态码+响应片段）。"""
    client = _make_started_client()
    mock_http = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = '{"detail":"paddle failed"}'
    mock_http.post.return_value = mock_resp
    client._http_clients[__import__("threading").get_ident()] = mock_http

    with pytest.raises(OcrHttpError, match="500"):
        client.recognize_sync(b"x")


def test_recognize_sync_raises_on_transport_error():
    """网络层异常（连接失败等）抛 OcrHttpError。"""
    client = _make_started_client()
    mock_http = MagicMock()
    mock_http.post.side_effect = httpx.ConnectError("connection refused")
    client._http_clients[__import__("threading").get_ident()] = mock_http

    with pytest.raises(OcrHttpError, match="请求失败"):
        client.recognize_sync(b"x")


def test_singleton_instance():
    """instance() 返回同一单例。"""
    a = OcrHttpClient.instance()
    b = OcrHttpClient.instance()
    assert a is b


# ====================================================================
# 铺开的端点方法测试（接线验证：正确构造请求 + 解析响应）
# ====================================================================
def _client_with_mock_post(json_body=None, content=b""):
    """构造已启动 client + mock httpx post，返回 (client, mock_http)。"""
    import threading

    client = _make_started_client()
    mock_http = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = json_body or {}
    mock_resp.content = content
    mock_resp.text = ""
    mock_http.post.return_value = mock_resp
    mock_http.get.return_value = mock_resp
    client._http_clients[threading.get_ident()] = mock_http
    return client, mock_http


def test_recognize_batch_sync():
    client, mock_http = _client_with_mock_post(
        json_body={"results": [{"text": "a", "raw_text": "a", "pipeline": "OCR",
                                "text_with_scores": [], "text_blocks": []}, None]}
    )
    result = client.recognize_batch_sync([b"a", b"b"], pipeline="OCR")
    from vibeocr.models.ocr_result import OCRResult

    assert isinstance(result[0], OCRResult)
    assert result[0].raw_text == "a"
    assert result[1] is None
    call = mock_http.post.call_args
    assert call.args[0] == "/ocr/recognize_batch"
    assert len(call.kwargs["files"]) == 2


def test_export_ocr_sync():
    client, mock_http = _client_with_mock_post(
        json_body={"output_path": "/o.txt", "bytes_written": 5}
    )
    result = client.export_ocr_sync(
        {"raw_text": "hi"}, output_path="/o.txt", export_format="txt"
    )
    assert result["bytes_written"] == 5
    payload = mock_http.post.call_args.kwargs["json"]
    assert payload["format"] == "txt"
    assert payload["output_path"] == "/o.txt"


def test_generate_qrcode_sync_returns_bytes():
    client, _ = _client_with_mock_post(content=b"\x89PNG")
    assert client.generate_qrcode_sync("hi") == b"\x89PNG"


def test_generate_qrcode_svg_sync_returns_str():
    client, _ = _client_with_mock_post(json_body={"svg": "<svg/>"})
    assert client.generate_qrcode_svg_sync("hi") == "<svg/>"


def test_decode_qrcode_sync():
    client, _ = _client_with_mock_post(
        json_body={"codes": [{"data": "x", "format": "qrcode", "is_url": False}]}
    )
    codes = client.decode_qrcode_sync(b"img")
    assert codes[0]["data"] == "x"


def test_pipeline_cache_status_sync():
    client, _ = _client_with_mock_post(
        json_body={"ready": True, "loaded_pipelines": ["OCR"]}
    )
    assert client.pipeline_cache_status_sync()["ready"] is True


def test_set_pipeline_cache_ttl_sync_true():
    client, mock_http = _client_with_mock_post(json_body={"updated": True})
    assert client.set_pipeline_cache_ttl_sync({"OCR": 300}) is True
    assert mock_http.post.call_args.kwargs["json"]["pipeline_ttls"] == {"OCR": 300}


def test_set_pipeline_cache_ttl_sync_false_best_effort():
    """worker 忙时 updated=False，不抛错。"""
    client, _ = _client_with_mock_post(json_body={"updated": False})
    assert client.set_pipeline_cache_ttl_sync({"OCR": 0}) is False


def test_release_pipeline_cache_sync():
    client, _ = _client_with_mock_post(json_body={"released": ["PP-StructureV3"]})
    assert client.release_pipeline_cache_sync(heavy_only=True) == ["PP-StructureV3"]


def test_preload_pipeline_cache_sync():
    client, _ = _client_with_mock_post(
        json_body={"results": {"OCR": True}}
    )
    assert client.preload_pipeline_cache_sync(["OCR"]) == {"OCR": True}


def test_warmup_pipeline_cache_sync():
    client, _ = _client_with_mock_post(json_body={"results": {"OCR": True}})
    assert client.warmup_pipeline_cache_sync(["OCR"]) == {"OCR": True}


def test_settings_snapshot_sync():
    client, _ = _client_with_mock_post(
        json_body={"backend": "gpu", "preload_pipelines": ["OCR"]}
    )
    assert client.settings_snapshot_sync()["backend"] == "gpu"


def test_switch_backend_sync():
    client, _ = _client_with_mock_post(json_body={"backend": "cpu"})
    assert client.switch_backend_sync("cpu")["backend"] == "cpu"


def test_install_dependency_sync():
    client, mock_http = _client_with_mock_post(json_body={"ok": True})
    client.install_dependency_sync("mineru", source="domestic")
    payload = mock_http.post.call_args.kwargs["json"]
    assert payload["name"] == "mineru"
    assert payload["source"] == "domestic"


def test_get_json_raises_on_non_200():
    import threading

    client = _make_started_client()
    mock_http = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "boom"
    mock_http.get.return_value = mock_resp
    client._http_clients[threading.get_ident()] = mock_http
    with pytest.raises(OcrHttpError, match="500"):
        client.pipeline_cache_status_sync()

