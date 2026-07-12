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
            )

        def shutdown(self) -> None:
            pass

    adapter = OcrServiceAdapter(Service)
    result = adapter.recognize(
        OcrRequest(image_data=b"png", pipeline="OCR", language="ch"),
        CancelToken(),
    )
    assert result.text == "recognized"
    assert result.raw_blocks == [{"text": "recognized"}]
    assert calls == [(b"png", {"pipeline": "OCR", "language": "ch"})]


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
                "pipeline_ttl_seconds": 900,
            }
        ),
        encoding="utf-8",
    )
    adapter = JsonSettingsAdapter(composition.paths, lambda: "cpu")
    snapshot = adapter.get_snapshot()
    assert snapshot.backend == "gpu"
    assert snapshot.preload_pipelines == ("OCR", "TABLE_RECOGNITION")
    assert snapshot.ttl_seconds == 900
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
        "pdf.open",
        "qrcode.decode",
        "qrcode.generate",
        "settings.snapshot",
    }
    assert factories_called == [], "domain services must remain lazy until first use"


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
        "ttl_seconds": 300,
    }
