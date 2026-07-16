"""Typed Python client coverage for all public WorkerHost feature families."""

from __future__ import annotations

from typing import Any

from vibeocr.worker_host.backend_client import BackendClient


async def test_pdf_typed_methods_map_protocol_payloads(monkeypatch) -> None:
    client = BackendClient()
    calls: list[tuple[str, dict[str, Any], float | None]] = []

    async def fake_call(
        method: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        calls.append((method, payload or {}, timeout))
        responses = {
            "pdf.open": {"session_id": "s1", "file_path": "C:/a.pdf", "page_count": 2},
            "pdf.close": {"closed": True},
            "pdf.command": {"result": {"pages": []}},
            "pdf.rotate": {"page_count": 2},
            "pdf.delete_pages": {"page_count": 1},
            "pdf.add_text_layer": {"written": True, "saved": True},
            "pdf.delete_text_layers": {"deleted_count": 2, "residual_pages": []},
            "pdf.save": {"saved_path": "C:/out.pdf"},
            "pdf.start_ocr": {"completed": 2, "failed": 0},
        }
        return responses[method]

    monkeypatch.setattr(client, "call", fake_call)

    assert (await client.open_pdf("C:/a.pdf"))["session_id"] == "s1"
    assert await client.close_pdf("s1") is True
    assert await client.pdf_command("s1", "model") == {"pages": []}
    assert await client.rotate_pdf_pages("s1", [0, 1], -90) == 2
    assert await client.delete_pdf_pages("s1", [1]) == 1
    assert (await client.add_pdf_text_layer("s1", 0, overwrite=True))["saved"] is True
    assert (await client.delete_pdf_text_layers("s1", [0, 1]))["deleted_count"] == 2
    assert await client.save_pdf("s1", "C:/out.pdf") == "C:/out.pdf"
    assert (
        await client.start_pdf_ocr(
            "s1", "C:/a.pdf", [0, 1], overwrite=False, timeout=123.0
        )
    )["completed"] == 2

    assert calls == [
        ("pdf.open", {"file_path": "C:/a.pdf"}, None),
        ("pdf.close", {"session_id": "s1"}, None),
        (
            "pdf.command",
            {"session_id": "s1", "operation": "model", "params": {}},
            None,
        ),
        (
            "pdf.rotate",
            {"session_id": "s1", "page_indices": [0, 1], "angle": -90},
            None,
        ),
        ("pdf.delete_pages", {"session_id": "s1", "page_indices": [1]}, None),
        (
            "pdf.add_text_layer",
            {
                "session_id": "s1",
                "page_index": 0,
                "overwrite": True,
                "save": True,
            },
            None,
        ),
        (
            "pdf.delete_text_layers",
            {"session_id": "s1", "page_indices": [0, 1]},
            None,
        ),
        (
            "pdf.save",
            {"session_id": "s1", "output_path": "C:/out.pdf"},
            None,
        ),
        (
            "pdf.start_ocr",
            {
                "session_id": "s1",
                "file_path": "C:/a.pdf",
                "page_indices": [0, 1],
                "overwrite": False,
                "sidecar_root": None,
            },
            123.0,
        ),
    ]
    await client.close()


async def test_pdf_render_reads_worker_owned_payload(monkeypatch) -> None:
    client = BackendClient()
    descriptor = {"name": "worker-payload"}

    async def fake_call(method, payload, *, timeout=None):
        assert method == "pdf.render_page"
        assert payload == {"session_id": "s1", "page_index": 3, "dpi": 144}
        return {"image": descriptor}

    async def fake_read(value):
        assert value is descriptor
        return b"png"

    monkeypatch.setattr(client, "call", fake_call)
    monkeypatch.setattr(client, "_read_worker_payload", fake_read)
    assert await client.render_pdf_page("s1", 3, dpi=144) == b"png"
    await client.close()


async def test_settings_and_export_typed_methods(monkeypatch) -> None:
    client = BackendClient()
    calls: list[tuple[str, dict[str, Any], float | None]] = []

    async def fake_call(method, payload=None, *, timeout=None):
        calls.append((method, payload or {}, timeout))
        return {"ok": True}

    monkeypatch.setattr(client, "call", fake_call)
    raw = {"text": "plain", "markdown_text": "**plain**", "raw_blocks": []}
    await client.export_ocr(
        raw, output_path="C:/result.txt", export_format="txt", overwrite=True
    )
    await client.settings_snapshot()
    await client.switch_backend("gpu")
    await client.install_dependency("paddle", source=None, timeout=600.0)

    assert calls == [
        (
            "ocr.export",
            {
                "raw_text": "plain",
                "markdown_text": "**plain**",
                "html_text": "",
                "raw_blocks": [],
                "output_path": "C:/result.txt",
                "format": "txt",
                "overwrite": True,
            },
            None,
        ),
        ("settings.snapshot", {}, None),
        ("settings.switch_backend", {"backend": "gpu"}, None),
        (
            "settings.install_dependency",
            {"name": "paddle", "source": None},
            600.0,
        ),
    ]
    await client.close()


async def test_client_owned_input_payload_is_released_on_rpc_error(monkeypatch) -> None:
    client = BackendClient()
    released: list[str] = []

    class Ref:
        name = "input-segment"

        @staticmethod
        def to_descriptor() -> dict[str, Any]:
            return {"name": "input-segment"}

    async def fake_put(*args, **kwargs):
        return Ref()

    async def fake_release(name: str) -> bool:
        released.append(name)
        return True

    async def fail_call(*args, **kwargs):
        raise RuntimeError("worker failed")

    monkeypatch.setattr(client._store, "put", fake_put)
    monkeypatch.setattr(client._store, "release_owned", fake_release)
    monkeypatch.setattr(client, "call", fail_call)

    try:
        await client.recognize(b"image")
    except RuntimeError:
        pass
    assert released == ["input-segment"]
    await client.close()


async def test_recognize_batch_uses_one_rpc_and_releases_all_payloads(monkeypatch) -> None:
    client = BackendClient()
    created: list[str] = []
    released: list[str] = []
    calls: list[tuple[str, dict[str, Any]]] = []

    class Ref:
        def __init__(self, name: str) -> None:
            self.name = name

        def to_descriptor(self) -> dict[str, Any]:
            return {"name": self.name}

    async def fake_put(image: bytes, *, media_type: str) -> Ref:
        name = image.decode()
        created.append(name)
        assert media_type == "image/png"
        return Ref(name)

    async def fake_release(name: str) -> bool:
        released.append(name)
        return True

    async def fake_call(method: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append((method, payload))
        return {
            "results": [
                {"text": "first", "pipeline": "OCR"},
                {"text": "second", "pipeline": "OCR"},
            ]
        }

    monkeypatch.setattr(client._store, "put", fake_put)
    monkeypatch.setattr(client._store, "release_owned", fake_release)
    monkeypatch.setattr(client, "call", fake_call)
    results = await client.recognize_batch(
        [b"first", b"second"], pipeline="OCR", language="ch"
    )
    assert [item["text"] for item in results] == ["first", "second"]
    assert created == ["first", "second"]
    assert released == ["first", "second"]
    assert calls == [
        (
            "ocr.recognize_batch",
            {
                "images": [{"name": "first"}, {"name": "second"}],
                "pipeline": "OCR",
                "language": "ch",
            },
        )
    ]
    await client.close()
