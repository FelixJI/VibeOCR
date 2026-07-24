"""OCR HTTP Worker 端点测试（FastAPI TestClient）。

验证迁移后的 HTTP worker 全端点接线：OCR（recognize/batch/export）、QR
（generate/generate_svg/decode）、pipeline cache（status/set_ttl/release/
preload/warmup）、settings（snapshot/switch_backend/install_dependency）。
不启动真实 PaddleOCR/QR（mock adapter/service）。
"""

from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")
pytest.importorskip("uvicorn", reason="uvicorn not installed")

from vibeocr.application.contracts import OcrExportResult, OcrResult
from vibeocr.worker_host import ocr_worker_http


@pytest.fixture
def app(monkeypatch):
    """构造 worker app，所有 adapter/service getter 返回 mock（不加载真实依赖）。"""
    # 重置模块级缓存
    for attr in (
        "_ocr_adapter", "_qr_decode", "_qr_generate", "_qr_generate_svg",
        "_settings_adapter",
    ):
        monkeypatch.setattr(ocr_worker_http, attr, None)

    mock_adapter = MagicMock()
    mock_qr_decode = MagicMock()
    mock_qr_gen = MagicMock()
    mock_qr_svg = MagicMock()
    mock_settings = MagicMock()
    mock_service = MagicMock()

    mock_adapter._service = mock_service  # _ocr_service() 经 adapter._get_service

    monkeypatch.setattr(ocr_worker_http, "_get_ocr_adapter", lambda: mock_adapter)
    monkeypatch.setattr(ocr_worker_http, "_get_qr_decode", lambda: mock_qr_decode)
    monkeypatch.setattr(ocr_worker_http, "_get_qr_generate", lambda: mock_qr_gen)
    monkeypatch.setattr(ocr_worker_http, "_get_qr_generate_svg", lambda: mock_qr_svg)
    monkeypatch.setattr(ocr_worker_http, "_get_settings_adapter", lambda: mock_settings)
    monkeypatch.setattr(ocr_worker_http, "_ocr_service", lambda: mock_service)

    return {
        "app": ocr_worker_http._create_app(),
        "adapter": mock_adapter,
        "qr_decode": mock_qr_decode,
        "qr_gen": mock_qr_gen,
        "qr_svg": mock_qr_svg,
        "settings": mock_settings,
        "service": mock_service,
    }


def _make_wire_result(text: str = "hello") -> OcrResult:
    return OcrResult(
        text=text,
        raw_text=text,
        markdown_text=text,
        html_text=f"<p>{text}</p>",
        pipeline="OCR",
        text_blocks=[{"text": text}],
        text_with_scores=[[text, 0.95]],
        content_list=[],
        image_width=100,
        image_height=50,
    )


# ====================================================================
# health
# ====================================================================
def test_health_endpoint(app):
    from fastapi.testclient import TestClient

    with TestClient(app["app"]) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ====================================================================
# OCR
# ====================================================================
def test_recognize_endpoint(app):
    from fastapi.testclient import TestClient

    app["adapter"].recognize.return_value = _make_wire_result()
    with TestClient(app["app"]) as client:
        resp = client.post(
            "/ocr/recognize",
            files={"image": ("t.png", b"x", "image/png")},
            data={"pipeline": "OCR", "language": "ch", "options_json": '{"a": true}'},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "hello"
    assert body["pipeline"] == "OCR"
    req = app["adapter"].recognize.call_args.args[0]
    assert req.image_data == b"x"
    assert req.pipeline == "OCR"
    assert req.options == {"a": True}


def test_recognize_batch_endpoint(app):
    from fastapi.testclient import TestClient

    app["adapter"].recognize_batch.return_value = [
        _make_wire_result("a"), None, _make_wire_result("c")
    ]
    with TestClient(app["app"]) as client:
        resp = client.post(
            "/ocr/recognize_batch",
            files=[
                ("images", ("1.png", b"a", "image/png")),
                ("images", ("2.png", b"b", "image/png")),
                ("images", ("3.png", b"c", "image/png")),
            ],
            data={"pipeline": "OCR"},
        )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 3
    assert results[0]["text"] == "a"
    assert results[1] is None
    assert results[2]["text"] == "c"


def test_export_endpoint(app, tmp_path):
    from fastapi.testclient import TestClient

    out = tmp_path / "out.txt"
    app["adapter"].export.return_value = OcrExportResult(
        output_path=out, bytes_written=42
    )
    with TestClient(app["app"]) as client:
        resp = client.post(
            "/ocr/export",
            json={
                "raw_text": "hi",
                "markdown_text": "hi",
                "html_text": "<p>hi</p>",
                "raw_blocks": [],
                "output_path": str(out),
                "format": "txt",
                "overwrite": True,
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["bytes_written"] == 42
    assert body["output_path"] == str(out)


def test_recognize_invalid_options_json(app):
    from fastapi.testclient import TestClient

    with TestClient(app["app"]) as client:
        resp = client.post(
            "/ocr/recognize",
            files={"image": ("t.png", b"x", "image/png")},
            data={"options_json": "not-json"},
        )
    assert resp.status_code == 400


def test_recognize_adapter_error_500(app):
    from fastapi.testclient import TestClient

    app["adapter"].recognize.side_effect = RuntimeError("paddle failed")
    with TestClient(app["app"]) as client:
        resp = client.post(
            "/ocr/recognize", files={"image": ("t.png", b"x", "image/png")}
        )
    assert resp.status_code == 500
    assert "paddle failed" in resp.json()["detail"]


# ====================================================================
# QR
# ====================================================================
def test_qr_generate_returns_png(app):
    from fastapi.testclient import TestClient

    app["qr_gen"].generate.return_value = b"\x89PNG fake"
    with TestClient(app["app"]) as client:
        resp = client.post(
            "/qrcode/generate", json={"data": "hello", "format": "qrcode"}
        )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == b"\x89PNG fake"


def test_qr_generate_barcode_format(app):
    from fastapi.testclient import TestClient

    app["qr_gen"].generate.return_value = b"PNG"
    with TestClient(app["app"]) as client:
        resp = client.post(
            "/qrcode/generate",
            json={"data": "x", "format": "barcode", "barcode_format": "code128"},
        )
    assert resp.status_code == 200
    opts = app["qr_gen"].generate.call_args.args[1]
    assert opts["format"] == "code128"


def test_qr_generate_requires_data(app):
    from fastapi.testclient import TestClient

    with TestClient(app["app"]) as client:
        resp = client.post("/qrcode/generate", json={"format": "qrcode"})
    assert resp.status_code == 400


def test_qr_generate_svg(app):
    from fastapi.testclient import TestClient

    app["qr_svg"].generate_svg.return_value = "<svg/>"
    with TestClient(app["app"]) as client:
        resp = client.post("/qrcode/generate_svg", json={"data": "x"})
    assert resp.status_code == 200
    assert resp.json()["svg"] == "<svg/>"


def test_qr_decode(app):
    from fastapi.testclient import TestClient

    app["qr_decode"].decode.return_value = [
        {"data": "https://x.com", "format": "qrcode", "is_url": True}
    ]
    with TestClient(app["app"]) as client:
        resp = client.post(
            "/qrcode/decode", files={"image": ("q.png", b"img", "image/png")}
        )
    assert resp.status_code == 200
    codes = resp.json()["codes"]
    assert codes[0]["data"] == "https://x.com"
    assert codes[0]["is_url"] is True


# ====================================================================
# pipeline cache
# ====================================================================
def test_cache_status(app):
    from fastapi.testclient import TestClient

    # status 走 OCRService.cache_manager.status()（OCRService 无 get_pipeline_cache_status）
    app["service"].cache_manager.status.return_value = {
        "ready": True, "loaded_pipelines": ["OCR"], "pipeline_ttls": {"OCR": 0},
    }
    with TestClient(app["app"]) as client:
        resp = client.get("/pipeline_cache/status")
    assert resp.status_code == 200
    assert resp.json()["loaded_pipelines"] == ["OCR"]


def test_cache_set_ttl(app):
    from fastapi.testclient import TestClient

    app["service"].set_pipeline_ttls.return_value = True
    with TestClient(app["app"]) as client:
        resp = client.post(
            "/pipeline_cache/set_ttl",
            json={"pipeline_ttls": {"OCR": 300, "PP-StructureV3": 0}},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["updated"] is True
    app["service"].set_pipeline_ttls.assert_called_once_with(
        {"OCR": 300, "PP-StructureV3": 0}
    )


def test_cache_set_ttl_best_effort_false(app):
    """set_ttl 失败（worker 忙）返回 updated=False，不抛 500。"""
    from fastapi.testclient import TestClient

    app["service"].set_pipeline_ttls.return_value = False
    with TestClient(app["app"]) as client:
        resp = client.post(
            "/pipeline_cache/set_ttl", json={"pipeline_ttls": {"OCR": 0}}
        )
    assert resp.status_code == 200
    assert resp.json()["updated"] is False


def test_cache_release(app):
    from fastapi.testclient import TestClient

    app["service"].release_pipelines.return_value = ["PP-StructureV3"]
    with TestClient(app["app"]) as client:
        resp = client.post("/pipeline_cache/release", json={"heavy_only": True})
    assert resp.status_code == 200
    assert resp.json()["released"] == ["PP-StructureV3"]


def test_cache_preload(app):
    from fastapi.testclient import TestClient

    # preload 走 OCRService.preload_pipelines_sequential（接收 list[OCRPipeline]）
    app["service"].preload_pipelines_sequential.return_value = {
        "OCR": True, "TABLE_RECOGNITION": False
    }
    with TestClient(app["app"]) as client:
        resp = client.post(
            "/pipeline_cache/preload",
            json={"pipelines": ["OCR", "TABLE_RECOGNITION"]},
        )
    assert resp.status_code == 200
    assert resp.json()["results"] == {"OCR": True, "TABLE_RECOGNITION": False}
    # 确认传的是枚举（转 str 后值对齐）
    called_arg = app["service"].preload_pipelines_sequential.call_args.args[0]
    assert [getattr(e, "value", e) for e in called_arg] == [
        "OCR", "TABLE_RECOGNITION"
    ]


def test_cache_preload_unknown_name_400(app):
    from fastapi.testclient import TestClient

    with TestClient(app["app"]) as client:
        resp = client.post(
            "/pipeline_cache/preload", json={"pipelines": ["UNKNOWN_PIPE"]}
        )
    assert resp.status_code == 400


def test_cache_warmup(app):
    from fastapi.testclient import TestClient

    app["service"].warmup_pipelines.return_value = {"OCR": True}
    with TestClient(app["app"]) as client:
        resp = client.post("/pipeline_cache/warmup", json={"pipelines": ["OCR"]})
    assert resp.status_code == 200
    assert resp.json()["results"]["OCR"] is True


# ====================================================================
# settings
# ====================================================================
def test_settings_snapshot(app):
    from fastapi.testclient import TestClient

    snap = MagicMock()
    snap.backend = "gpu"
    snap.preload_pipelines = ("OCR",)
    snap.pipeline_ttls = {"OCR": 0}
    app["settings"].get_snapshot.return_value = snap
    with TestClient(app["app"]) as client:
        resp = client.get("/settings/snapshot")
    assert resp.status_code == 200
    body = resp.json()
    assert body["backend"] == "gpu"
    assert body["preload_pipelines"] == ["OCR"]


def test_switch_backend(app):
    from fastapi.testclient import TestClient

    app["settings"].switch_backend.return_value = "cpu"
    with TestClient(app["app"]) as client:
        resp = client.post("/settings/switch_backend", json={"backend": "cpu"})
    assert resp.status_code == 200
    assert resp.json()["backend"] == "cpu"


def test_install_dependency(app):
    from fastapi.testclient import TestClient

    app["settings"].install_dependency.return_value = {"ok": True, "name": "mineru"}
    with TestClient(app["app"]) as client:
        resp = client.post(
            "/settings/install_dependency",
            json={"name": "mineru", "source": "domestic"},
        )
    assert resp.status_code == 200
    assert resp.json()["name"] == "mineru"
