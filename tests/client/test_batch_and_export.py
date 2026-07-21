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
