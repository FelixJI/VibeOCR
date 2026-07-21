from __future__ import annotations

import importlib
import json
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from vibeocr.application.contracts import CancelToken, OcrRequest, PdfOpenRequest
from vibeocr.worker_host.composition import (
    JsonSettingsAdapter,
    OcrServiceAdapter,
    PdfBackendAdapter,
    WorkerServiceComposition,
)
from vibeocr.worker_host.contracts import RpcEnvelope
from vibeocr.worker_host.shared_payload import SharedPayloadStore

if TYPE_CHECKING:
    from pathlib import Path


def test_composition_import_does_not_load_pyside6() -> None:
    for module in list(sys.modules):
        if module == "PySide6" or module.startswith("PySide6."):
            sys.modules.pop(module, None)
    importlib.reload(importlib.import_module("vibeocr.worker_host.composition"))
    assert "PySide6" not in sys.modules


def test_ocr_adapter_maps_existing_service_result() -> None:
    calls: list[tuple[bytes, dict[str, Any]]] = []

    class Service:
        def recognize(self, image: bytes, options: dict[str, Any]) -> Any:
            calls.append((image, options))
            return SimpleNamespace(
                copy_text="recognized",
                raw_text="raw",
                pipeline_type="OCR",
                content_list=[{"text": "recognized"}],
                text_blocks=[],
                preproc_angle=90,
                preproc_img_w=3508,
                preproc_img_h=2480,
            )

        def shutdown(self) -> None:
            pass

    adapter = OcrServiceAdapter(Service)
    result = adapter.recognize(
        OcrRequest(
            image_data=b"png",
            pipeline="OCR",
            language="ch",
            options={"use_doc_orientation_classify": False},
        ),
        CancelToken(),
    )
    assert result.text == "recognized"
    assert result.raw_blocks == [{"text": "recognized"}]
    assert result.preproc_angle == 90
    assert (result.preproc_img_w, result.preproc_img_h) == (3508, 2480)
    assert calls == [(
        b"png",
        {
            "pipeline": "OCR",
            "language": "ch",
            "use_doc_orientation_classify": False,
        },
    )]


def test_ocr_adapter_uses_one_service_batch_call() -> None:
    calls: list[tuple[list[bytes], dict[str, Any]]] = []

    class Service:
        def recognize_batch(
            self, images: list[bytes], options: dict[str, Any]
        ) -> list[Any | None]:
            calls.append((images, options))
            return [
                SimpleNamespace(copy_text="one", pipeline_type="OCR"),
                None,
            ]

    adapter = OcrServiceAdapter(Service)
    results = adapter.recognize_batch(
        [
            OcrRequest(image_data=b"one", pipeline="OCR", language="ch"),
            OcrRequest(image_data=b"two", pipeline="OCR", language="ch"),
        ],
        CancelToken(),
    )

    assert calls == [([b"one", b"two"], {"pipeline": "OCR", "language": "ch"})]
    assert results[0] is not None and results[0].text == "one"
    assert results[1] is None


def test_pdf_adapter_maps_backend_response(tmp_path: Path) -> None:
    source = tmp_path / "sample.pdf"
    source.write_bytes(b"%PDF")

    class Client:
        def open_session(self, path: str) -> Any:
            return SimpleNamespace(
                session_id="session-1",
                model=SimpleNamespace(file_path=path, pages=[{}, {}, {}]),
            )

        def stop(self) -> None:
            pass

    adapter = PdfBackendAdapter(Client)
    result = adapter.open(PdfOpenRequest(file_path=source), CancelToken())
    assert result.session_id == "session-1"
    assert result.page_count == 3
    assert result.file_path == source.resolve()


def test_pdf_command_forwards_fast_finalize_flag() -> None:
    calls: list[tuple[str, str | None, dict[str, Any] | None, bool]] = []

    class Client:
        def save(
            self,
            session_id: str,
            path: str | None,
            pdf_settings: dict[str, Any] | None,
            *,
            rewrite_text_layers: bool,
        ) -> dict[str, Any]:
            calls.append(
                (session_id, path, pdf_settings, rewrite_text_layers)
            )
            return {"path": "C:/doc.pdf", "diff": {}}

    adapter = PdfBackendAdapter(Client)
    result = adapter.command(
        "sid-1",
        "save",
        {
            "path": None,
            "pdf_settings": {"compress_on_save": True},
            "rewrite_text_layers": False,
        },
    )

    assert result["path"] == "C:/doc.pdf"
    assert calls == [
        ("sid-1", None, {"compress_on_save": True}, False)
    ]


def test_json_settings_adapter_reads_side_by_side_profile(tmp_path: Path) -> None:
    composition = WorkerServiceComposition(
        project_root=tmp_path,
        profile="winui-dev",
        ocr_factory=lambda: object(),
        pdf_factory=lambda: object(),
        qr_decode_factory=lambda: object(),
        qr_generate_factory=lambda: object(),
        backend_resolver=lambda: "cpu",
    )
    composition.paths.config_file.parent.mkdir(parents=True)
    composition.paths.config_file.write_text(
        json.dumps(
            {
                "backend": "gpu",
                "preload_pipelines": ["OCR", "TABLE_RECOGNITION"],
                "pipeline_ttls": {"OCR": 900, "PP-StructureV3": 300},
            }
        ),
        encoding="utf-8",
    )
    adapter = JsonSettingsAdapter(composition.paths, lambda: "cpu")
    snapshot = adapter.get_snapshot()
    assert snapshot.backend == "gpu"
    assert snapshot.preload_pipelines == ("OCR", "TABLE_RECOGNITION")
    assert snapshot.pipeline_ttls["OCR"] == 900
    assert snapshot.pipeline_ttls["PP-StructureV3"] == 300
    assert not (tmp_path / "config" / "app_settings.json").exists()


def test_production_composition_registers_all_domain_handlers(tmp_path: Path) -> None:
    factories_called: list[str] = []
    composition = WorkerServiceComposition(
        project_root=tmp_path,
        profile="winui-dev",
        ocr_factory=lambda: factories_called.append("ocr"),
        pdf_factory=lambda: factories_called.append("pdf"),
        qr_decode_factory=lambda: factories_called.append("decode"),
        qr_generate_factory=lambda: factories_called.append("generate"),
        backend_resolver=lambda: "cpu",
    )
    handlers = composition.handlers(SharedPayloadStore(owner="worker"))
    assert set(handlers) == {
        "ocr.recognize",
        "ocr.recognize_batch",
        "ocr.export",
        "pipeline_cache.status",
        "pipeline_cache.set_ttl",
        "pipeline_cache.release",
        "pipeline_cache.preload",
        "pipeline_cache.warmup",
        "pdf.open",
        "pdf.close",
        "pdf.command",
        "pdf.render_page",
        "pdf.rotate",
        "pdf.delete_pages",
        "pdf.add_text_layer",
        "pdf.delete_text_layers",
        "pdf.save",
        "pdf.start_ocr",
        "qrcode.decode",
        "qrcode.generate",
        "qrcode.generate_svg",
        "settings.snapshot",
        "settings.switch_backend",
        "settings.install_dependency",
    }
    assert factories_called == [], "domain services must remain lazy until first use"


def test_ocr_adapter_delegates_pipeline_cache_lifecycle() -> None:
    from vibeocr.worker_host.composition import OcrServiceAdapter

    class Service:
        def get_pipeline_cache_status(self):
            return {
                "ready": True,
                "pipeline_ttls": {"OCR": 300},
                "max_heavy": 2,
                "loaded_pipelines": ["OCR"],
                "last_used_unix_ms": {},
            }

        def set_pipeline_ttls(self, pipeline_ttls):
            return pipeline_ttls == {"OCR": 600}

        def release_pipelines(self, *, heavy_only):
            return ["PP-StructureV3"] if heavy_only else ["OCR"]

        def preload_pipelines(self, pipelines):
            return dict.fromkeys(pipelines, True)

        def warmup_pipelines(self, pipelines):
            return dict.fromkeys(pipelines, True)

    adapter = OcrServiceAdapter(Service)
    assert adapter.pipeline_cache_status()["loaded_pipelines"] == ["OCR"]
    assert adapter.set_pipeline_ttls({"OCR": 600}) is True
    assert adapter.release_pipelines() == ["PP-StructureV3"]
    assert adapter.preload_pipelines(["OCR"]) == {"OCR": True}
    assert adapter.warmup_pipelines(["OCR"]) == {"OCR": True}


@pytest.mark.asyncio
async def test_settings_domain_handler_is_live_in_dispatcher(tmp_path: Path) -> None:
    from vibeocr.worker_host.main import _build_dispatcher

    composition = WorkerServiceComposition(
        project_root=tmp_path,
        profile="winui-dev",
        backend_resolver=lambda: "cpu",
    )
    store = SharedPayloadStore(owner="worker")
    dispatcher = _build_dispatcher(
        store=store,
        domain_handlers=composition.handlers(store),
        backend="cpu",
    )
    request = RpcEnvelope(
        request_id="00000000-0000-4000-8000-000000000201",
        task_id="00000000-0000-4000-8000-000000000201",
        method="settings.snapshot",
        payload={},
        deadline_unix_ms=0,
    )
    response = await dispatcher.dispatch(request, deadline_unix_ms=0)
    assert response.result == {
        "backend": "cpu",
        "preload_pipelines": [],
        "pipeline_ttls": {
            "OCR": 0,
            "TABLE_RECOGNITION": 0,
            "FORMULA_RECOGNITION": 0,
            "PP-StructureV3": 300,
            "MinerU": 0,
            "PaddleOCR-VL": 300,
        },
    }
