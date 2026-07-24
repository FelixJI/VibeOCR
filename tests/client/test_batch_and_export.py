"""Frontend batch and export adapters only depend on the client SDK."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from vibeocr.client.batch import BatchBackendAdapter
from vibeocr.client.export import (
    export_result,
    get_output_filename,
    get_unique_output_path,
)
from vibeocr.contracts.pipelines import OCRPipeline


def test_batch_adapter_maps_options_and_cancel() -> None:
    client = MagicMock()
    client.recognize_batch_sync.return_value = ["a", "b"]
    adapter = BatchBackendAdapter(client)
    options = SimpleNamespace(pipeline=OCRPipeline.PP_STRUCTURE_V3, language="ch")

    assert adapter.recognize_batch([b"1", b"2"], options) == ["a", "b"]
    client.recognize_batch_sync.assert_called_once_with(
        [b"1", b"2"], pipeline="PP-StructureV3", language="ch"
    )
    adapter.batch_cancel()
    client.cancel_active.assert_called_once_with()


def test_batch_adapter_routes_mineru_to_main_process_service(monkeypatch) -> None:
    """MinerU 管道必须分流到主进程 MinerUService，不得发往 OCR worker。

    回归测试：ocr-worker-http 的注册表中 MinerU spec 按设计抛出
    NotImplementedError("MinerU uses its own service")，调用方必须在上层分流。
    """
    client = MagicMock()
    adapter = BatchBackendAdapter(client)
    options = SimpleNamespace(pipeline=OCRPipeline.DOCUMENT_PARSING)

    parse_calls: list[tuple[bytes, str, object]] = []

    class _FakeMinerUService:
        def parse(self, data, mime_type, opts):
            parse_calls.append((data, mime_type, opts))
            return SimpleNamespace(markdown_text=f"md:{mime_type}")

    monkeypatch.setattr(
        "vibeocr.services.mineru_service.MinerUService", _FakeMinerUService
    )

    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    pdf_bytes = b"%PDF-1.7\nfake-body"
    results = adapter.recognize_batch([png_bytes, pdf_bytes], options)

    client.recognize_batch_sync.assert_not_called()
    assert [call[1] for call in parse_calls] == ["image/png", "application/pdf"]
    assert [r.markdown_text for r in results] == [
        "md:image/png",
        "md:application/pdf",
    ]


def test_batch_adapter_mineru_single_failure_returns_none(monkeypatch) -> None:
    """MinerU 批量路径单张失败返回 None，不中断整批。"""
    client = MagicMock()
    adapter = BatchBackendAdapter(client)
    options = SimpleNamespace(pipeline=OCRPipeline.DOCUMENT_PARSING)

    class _FlakyMinerUService:
        def parse(self, data, mime_type, opts):
            if data.startswith(b"%PDF"):
                raise RuntimeError("boom")
            return SimpleNamespace(markdown_text="ok")

    monkeypatch.setattr(
        "vibeocr.services.mineru_service.MinerUService", _FlakyMinerUService
    )

    results = adapter.recognize_batch(
        [b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, b"%PDF-1.7"], options
    )

    assert results[0].markdown_text == "ok"
    assert results[1] is None


def test_batch_adapter_recognize_routes_mineru(monkeypatch) -> None:
    """单张 recognize 的 MinerU 分流同样走主进程服务。"""
    client = MagicMock()
    adapter = BatchBackendAdapter(client)
    options = SimpleNamespace(pipeline=OCRPipeline.DOCUMENT_PARSING)

    class _FakeMinerUService:
        def parse(self, data, mime_type, opts):
            return SimpleNamespace(markdown_text="single")

    monkeypatch.setattr(
        "vibeocr.services.mineru_service.MinerUService", _FakeMinerUService
    )

    result = adapter.recognize(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, options)

    client.recognize_sync.assert_not_called()
    assert result.markdown_text == "single"


def test_batch_adapter_forwards_full_ocr_options() -> None:
    from vibeocr.models.ocr_options import OCROptions

    client = MagicMock()
    client.recognize_batch_sync.return_value = []
    adapter = BatchBackendAdapter(client)
    options = OCROptions(
        use_doc_orientation_classify=False,
        use_doc_unwarping=True,
    )

    adapter.recognize_batch([b"png"], options)

    kwargs = client.recognize_batch_sync.call_args.kwargs
    assert kwargs["options"]["use_doc_orientation_classify"] is False
    assert kwargs["options"]["use_doc_unwarping"] is True


def test_batch_adapter_uses_real_pipeline_cache_client_surface() -> None:
    client = MagicMock()
    client.preload_pipeline_cache_sync.return_value = {"OCR": True}
    client.warmup_pipeline_cache_sync.return_value = {"OCR": True}
    client.release_pipeline_cache_sync.return_value = ["PP-StructureV3"]
    client.set_pipeline_cache_ttl_sync.return_value = True
    client.pipeline_cache_status_sync.return_value = {
        "ready": True,
        "pipeline_ttls": {"OCR": 0, "PP-StructureV3": 300},
        "max_heavy": 2,
        "loaded_pipelines": ["OCR"],
        "last_used_unix_ms": {},
    }
    adapter = BatchBackendAdapter(client)

    assert adapter.preload_pipelines(["OCR"]) == {"OCR": True}
    assert adapter.warmup_pipelines(["OCR"]) == {"OCR": True}
    assert adapter.release_pipelines(heavy_only=True) == ["PP-StructureV3"]
    assert adapter.set_pipeline_ttls({"OCR": 0, "PP-StructureV3": 600}) is True
    assert adapter.get_pipeline_cache_status()["loaded_pipelines"] == ["OCR"]

    client.preload_pipeline_cache_sync.assert_called_once_with(["OCR"])
    client.warmup_pipeline_cache_sync.assert_called_once_with(["OCR"])
    client.release_pipeline_cache_sync.assert_called_once_with(heavy_only=True)
    client.set_pipeline_cache_ttl_sync.assert_called_once_with(
        {"OCR": 0, "PP-StructureV3": 600}
    )
    client.pipeline_cache_status_sync.assert_called_once_with()


def test_export_helpers_and_rpc_dispatch(tmp_path) -> None:
    occupied = tmp_path / "result.docx"
    occupied.write_bytes(b"old")
    client = MagicMock()
    result = SimpleNamespace(
        raw_text="text",
        markdown_text="md",
        html_text="html",
        content_list=[{"type": "text", "text": "text"}],
    )

    assert get_output_filename("scan.png", "docx") == "scan.docx"
    output = get_unique_output_path(occupied)
    assert output.name == "result_1.docx"
    assert export_result(client, result, output, "docx") is True
    client.export_ocr_sync.assert_called_once_with(
        {
            "raw_text": "text",
            "markdown_text": "md",
            "html_text": "html",
            "content_list": [{"type": "text", "text": "text"}],
        },
        output_path=str(output),
        export_format="docx",
        overwrite=False,
    )
