"""worker_host.composition 各 adapter 分支覆盖补充测试。

既有 test_composition.py 覆盖了 happy-path 与 OcrServiceAdapter 的选项映射。
本文件补齐以下未覆盖分支：

- OcrServiceAdapter: 取消令牌、空批量、结果数量不匹配、text_blocks dataclass
  序列化、export 成功/取消/失败、shutdown 幂等、recognize_batch 选项一致性校验。
- PdfBackendAdapter: render/rotate/delete_pages/add_text_layer/delete_text_layers/
  save/close/command 各操作、_wire 递归（dataclass/dict/list/tuple/None）。
- QrGenerateAdapter/QrGenerateSvgAdapter/QrDecodeAdapter: logo/label/invert
  pipeline、取消、懒加载、decode 字段映射。
- JsonSettingsAdapter: 缺省默认 TTL、非法 backend 回退、switch_backend 持久化、
  install_dependency 取消/空名。
- _PdfOcrBackendBridge: render_pages/recognize_pages/write_batch/compress/cancel。
"""

from __future__ import annotations

import dataclasses
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

from vibeocr.application.contracts import (
    CancelToken,
    OcrExportRequest,
    OcrRequest,
    PdfOpenRequest,
)
from vibeocr.worker_host.composition import (
    JsonSettingsAdapter,
    OcrServiceAdapter,
    PdfBackendAdapter,
    QrDecodeAdapter,
    QrGenerateAdapter,
    QrGenerateSvgAdapter,
    WorkerServiceComposition,
    _PdfOcrBackendBridge,
)

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cancelled() -> CancelToken:
    tok = CancelToken()
    tok.cancel()
    return tok


class _Image:
    """PIL.Image 替身：记录 save 调用的格式。"""

    def __init__(self) -> None:
        self.saved: list[str] = []

    def save(self, output, format: str = "PNG") -> None:  # noqa: A002
        self.saved.append(format)
        output.write(b"PNG-DATA")


# ---------------------------------------------------------------------------
# OcrServiceAdapter
# ---------------------------------------------------------------------------


class TestOcrServiceAdapterCancelsAndEdges:
    def test_recognize_raises_before_call_when_cancelled(self) -> None:
        service = MagicMock()
        adapter = OcrServiceAdapter(lambda: service)
        with pytest.raises(RuntimeError, match="cancelled"):
            adapter.recognize(OcrRequest(image_data=b"x", pipeline="OCR"), _cancelled())
        service.recognize.assert_not_called()

    def test_recognize_raises_after_call_when_cancelled_mid_flight(self) -> None:
        # 覆盖 line 92: cancel 在 service 调用过程中被设置（第二次检查）。
        cancel = CancelToken()

        def fake_recognize(_image, _options):
            cancel.cancel()  # 模拟调用期间收到取消信号
            return SimpleNamespace(raw_text="t", pipeline_type="OCR")

        service = MagicMock()
        service.recognize.side_effect = fake_recognize
        adapter = OcrServiceAdapter(lambda: service)
        with pytest.raises(RuntimeError, match="cancelled"):
            adapter.recognize(OcrRequest(image_data=b"x", pipeline="OCR"), cancel)
        # service 已被调用
        service.recognize.assert_called_once()

    def test_recognize_without_language_omits_language_key(self) -> None:
        calls: list[Any] = []

        class Service:
            def recognize(self, image, options):
                calls.append(options)
                return SimpleNamespace(raw_text="t", pipeline_type="OCR")

        adapter = OcrServiceAdapter(Service)
        adapter.recognize(
            OcrRequest(image_data=b"x", pipeline="OCR", language=None),
            CancelToken(),
        )
        # adapter 传 OCROptions 对象（OCRService 契约），pipeline 注入。
        opts = calls[0]
        assert opts.pipeline.value == "OCR"
        # OCROptions 无 language 字段（language 是请求级参数，不经 options 传）。
        assert not hasattr(opts, "language")

    def test_recognize_batch_cancelled_before(self) -> None:
        service = MagicMock()
        adapter = OcrServiceAdapter(lambda: service)
        with pytest.raises(RuntimeError, match="cancelled"):
            adapter.recognize_batch(
                [OcrRequest(image_data=b"x", pipeline="OCR")], _cancelled()
            )
        service.recognize_batch.assert_not_called()

    def test_recognize_batch_empty_returns_empty(self) -> None:
        adapter = OcrServiceAdapter(lambda: MagicMock())
        assert adapter.recognize_batch([], CancelToken()) == []

    def test_recognize_batch_rejects_mixed_options(self) -> None:
        adapter = OcrServiceAdapter(lambda: MagicMock())
        with pytest.raises(ValueError, match="must share options"):
            adapter.recognize_batch(
                [
                    OcrRequest(image_data=b"a", pipeline="OCR"),
                    OcrRequest(image_data=b"b", pipeline="TABLE_RECOGNITION"),
                ],
                CancelToken(),
            )

    def test_recognize_batch_cancelled_after(self) -> None:
        service = MagicMock()
        service.recognize_batch.return_value = [
            SimpleNamespace(raw_text="a", pipeline_type="OCR"),
            SimpleNamespace(raw_text="b", pipeline_type="OCR"),
        ]
        adapter = OcrServiceAdapter(lambda: service)
        with pytest.raises(RuntimeError, match="cancelled"):
            adapter.recognize_batch(
                [
                    OcrRequest(image_data=b"a", pipeline="OCR"),
                    OcrRequest(image_data=b"b", pipeline="OCR"),
                ],
                _cancelled(),
            )

    def test_recognize_batch_count_mismatch(self) -> None:
        service = MagicMock()
        service.recognize_batch.return_value = [
            SimpleNamespace(raw_text="a", pipeline_type="OCR"),
        ]
        adapter = OcrServiceAdapter(lambda: service)
        with pytest.raises(RuntimeError, match="count mismatch"):
            adapter.recognize_batch(
                [
                    OcrRequest(image_data=b"a", pipeline="OCR"),
                    OcrRequest(image_data=b"b", pipeline="OCR"),
                ],
                CancelToken(),
            )

    def test_to_contract_result_serializes_dataclass_text_blocks(self) -> None:
        @dataclasses.dataclass
        class Block:
            text: str = "hi"
            bbox: tuple[int, ...] = (1, 2, 3, 4)

        result = SimpleNamespace(
            copy_text="hi",
            raw_text="hi",
            pipeline_type="OCR",
            text_blocks=[Block(), {"text": "dict"}],
            text_with_scores=[("a", 0.9), (1, 2)],  # 非法项被过滤
        )
        out = OcrServiceAdapter._to_contract_result(result, "OCR")
        assert out.text_blocks[0] == {"text": "hi", "bbox": (1, 2, 3, 4)}
        assert out.text_blocks[1] == {"text": "dict"}
        # 只有合法 (str, number) 项进入 text_with_scores
        assert out.text_with_scores == [["a", 0.9]]

    def test_to_contract_result_prefers_copy_text_and_uses_pipeline_value(self) -> None:
        from enum import Enum

        class P(Enum):
            OCR = "OCR"

        result = SimpleNamespace(copy_text="copy", raw_text="raw", pipeline_type=P.OCR)
        out = OcrServiceAdapter._to_contract_result(result, "TABLE")
        assert out.text == "copy"
        assert out.pipeline == "OCR"

    def test_export_cancelled_before(self, tmp_path: Path) -> None:
        adapter = OcrServiceAdapter(lambda: MagicMock())
        with pytest.raises(RuntimeError, match="cancelled"):
            adapter.export(
                OcrExportRequest(
                    raw_text="t",
                    markdown_text="",
                    html_text="",
                    raw_blocks=[],
                    output_path=tmp_path / "out.txt",
                    format="txt",
                ),
                _cancelled(),
            )

    def test_export_success_returns_bytes_written(self, tmp_path: Path) -> None:
        out_path = tmp_path / "out.txt"
        out_path.write_text("seed", encoding="utf-8")  # 让 stat().st_size 非零

        class Service:
            pass

        adapter = OcrServiceAdapter(Service)
        with patch(
            "vibeocr.services.export_service.ExportService.export", return_value=True
        ):
            result = adapter.export(
                OcrExportRequest(
                    raw_text="hello",
                    markdown_text="",
                    html_text="",
                    raw_blocks=[],
                    output_path=out_path,
                    format="txt",
                ),
                CancelToken(),
            )
        assert result.output_path == out_path
        assert result.bytes_written == out_path.stat().st_size

    def test_export_failure_raises(self, tmp_path: Path) -> None:
        adapter = OcrServiceAdapter(lambda: MagicMock())
        with patch(
            "vibeocr.services.export_service.ExportService.export", return_value=False
        ):
            with pytest.raises(RuntimeError, match="export failed"):
                adapter.export(
                    OcrExportRequest(
                        raw_text="hello",
                        markdown_text="",
                        html_text="",
                        raw_blocks=[],
                        output_path=tmp_path / "out.txt",
                        format="txt",
                    ),
                    CancelToken(),
                )

    def test_shutdown_is_idempotent_when_no_service_yet(self) -> None:
        adapter = OcrServiceAdapter(lambda: MagicMock())
        # 未触发懒加载，_service 为 None
        adapter.shutdown()
        assert adapter._service is None

    def test_shutdown_calls_service_shutdown(self) -> None:
        service = MagicMock()
        adapter = OcrServiceAdapter(lambda: service)
        adapter._get_service()  # 触发懒加载
        adapter.shutdown()
        service.shutdown.assert_called_once()
        assert adapter._service is None


# ---------------------------------------------------------------------------
# PdfBackendAdapter
# ---------------------------------------------------------------------------


class TestPdfBackendAdapterDelegation:
    def _client(self) -> MagicMock:
        return MagicMock()

    def test_open_cancelled_before(self, tmp_path: Path) -> None:
        adapter = PdfBackendAdapter(self._client)
        with pytest.raises(RuntimeError, match="cancelled"):
            adapter.open(PdfOpenRequest(file_path=tmp_path / "x.pdf"), _cancelled())

    def test_open_cancelled_after_closes_session(self, tmp_path: Path) -> None:
        # 覆盖 line 228-231: cancel 在 open_session 调用过程中被设置。
        cancel = CancelToken()
        client = self._client()

        def fake_open_session(_path):
            cancel.cancel()
            return SimpleNamespace(
                session_id="s1",
                model=SimpleNamespace(
                    file_path=str(tmp_path / "x.pdf"), pages=[{}, {}]
                ),
            )

        client.open_session.side_effect = fake_open_session
        adapter = PdfBackendAdapter(lambda: client)
        with pytest.raises(RuntimeError, match="cancelled"):
            adapter.open(PdfOpenRequest(file_path=tmp_path / "x.pdf"), cancel)
        client.close_session.assert_called_once_with("s1")

    def test_open_falls_back_to_path_when_model_path_empty(
        self, tmp_path: Path
    ) -> None:
        source = tmp_path / "doc.pdf"
        source.write_bytes(b"%PDF")
        client = self._client()
        client.open_session.return_value = SimpleNamespace(
            session_id="s1",
            model=SimpleNamespace(file_path="", pages=[{}]),
        )
        adapter = PdfBackendAdapter(lambda: client)
        result = adapter.open(PdfOpenRequest(file_path=source), CancelToken())
        assert result.file_path == source.resolve()

    def test_close_suppresses_errors(self) -> None:
        client = self._client()
        client.close_session.side_effect = RuntimeError("boom")
        adapter = PdfBackendAdapter(lambda: client)
        # 异常被吞，始终返回 True
        assert adapter.close("s1") is True

    def test_render_page_with_dpi(self) -> None:
        client = self._client()
        client.render_preview.return_value = b"preview"
        adapter = PdfBackendAdapter(lambda: client)
        assert adapter.render_page("s1", 0, size=None, dpi=200) == b"preview"
        client.render_preview.assert_called_once_with("s1", 0, dpi=200)

    def test_render_page_thumbnail_with_size(self) -> None:
        client = self._client()
        client.render_thumbnail.return_value = b"thumb"
        adapter = PdfBackendAdapter(lambda: client)
        assert adapter.render_page("s1", 1, size=128, dpi=None) == b"thumb"
        client.render_thumbnail.assert_called_once_with("s1", 1, size=128)

    def test_render_page_thumbnail_default_size_when_none(self) -> None:
        client = self._client()
        client.render_thumbnail.return_value = b"thumb"
        adapter = PdfBackendAdapter(lambda: client)
        assert adapter.render_page("s1", 1, size=None, dpi=None) == b"thumb"
        client.render_thumbnail.assert_called_once_with("s1", 1, size=160)

    def test_rotate_returns_page_count(self) -> None:
        client = self._client()
        client.get_model.return_value = SimpleNamespace(pages=[{}, {}, {}, {}])
        adapter = PdfBackendAdapter(lambda: client)
        assert adapter.rotate("s1", [0, 1], 90) == 4
        client.rotate.assert_called_once_with("s1", [0, 1], 90)

    def test_delete_pages_returns_page_count(self) -> None:
        client = self._client()
        client.get_model.return_value = SimpleNamespace(pages=[{}])
        adapter = PdfBackendAdapter(lambda: client)
        assert adapter.delete_pages("s1", [0]) == 1

    def test_add_text_layer_success(self) -> None:
        client = self._client()
        adapter = PdfBackendAdapter(lambda: client)
        result = adapter.add_text_layer("s1", 0, overwrite=True, save=True)
        assert result == {"written": True, "saved": True}
        client.add_text_layer_batch.assert_called_once()

    def test_add_text_layer_suppresses_errors(self) -> None:
        client = self._client()
        client.add_text_layer_batch.side_effect = RuntimeError("x")
        adapter = PdfBackendAdapter(lambda: client)
        result = adapter.add_text_layer("s1", 0, overwrite=False, save=False)
        assert result == {"written": False, "saved": False}

    def test_delete_text_layers_respects_cancel(self) -> None:
        client = self._client()
        adapter = PdfBackendAdapter(lambda: client)
        result = adapter.delete_text_layers("s1", [0, 1, 2], _cancelled())
        assert result == {"deleted_count": 0, "residual_pages": []}
        client.delete_text_layers_stream.assert_not_called()

    def test_delete_text_layers_counts_deleted(self) -> None:
        client = self._client()
        adapter = PdfBackendAdapter(lambda: client)
        result = adapter.delete_text_layers("s1", [0, 1], CancelToken())
        assert result == {"deleted_count": 2, "residual_pages": []}

    def test_save_returns_path(self) -> None:
        client = self._client()
        client.save.return_value = SimpleNamespace(path="C:/o.pdf")
        adapter = PdfBackendAdapter(lambda: client)
        assert adapter.save("s1", None) == "C:/o.pdf"

    def test_command_unsupported_operation_raises(self) -> None:
        adapter = PdfBackendAdapter(self._client)
        with pytest.raises(ValueError, match="unsupported PDF operation"):
            adapter.command("s1", "evil", {})

    @pytest.mark.parametrize(
        ("operation", "params", "client_method"),
        [
            ("model", {}, "get_model"),
            (
                "detect_text_layers",
                {"page": 2},
                "detect_text_layers",
            ),
            ("rotate", {"pages": ["0", "1"], "angle": "90"}, "rotate"),
            ("delete_pages", {"pages": ["1"]}, "delete_pages"),
            (
                "insert_blank",
                {"after_index": "0", "width": "600", "height": "800"},
                "insert_blank",
            ),
            ("insert_from", {"source_path": "p", "after_index": "1"}, "insert_from"),
            ("move_page", {"from_index": "0", "to_index": "1"}, "move_page"),
            ("reorder", {"new_order": ["2", "0", "1"]}, "reorder"),
            (
                "add_text_layer",
                {"page": "0", "ocr_result": {"a": 1}, "overwrite": "true"},
                "add_text_layer",
            ),
            (
                "add_text_layer_batch",
                {"pages": [{"page": 0}], "save": "true"},
                "add_text_layer_batch",
            ),
            (
                "rewrite_text_layer",
                {"page": "0", "text_blocks": ["b"], "preproc_angle": "90"},
                "rewrite_text_layer",
            ),
            (
                "update_block_text",
                {"page": "0", "block_index": "1", "new_text": "t"},
                "update_block_text",
            ),
            ("cancel", {}, "cancel"),
            ("reset_cancel", {}, "reset_cancel"),
        ],
    )
    def test_command_forwards_each_operation(
        self, operation: str, params: dict[str, Any], client_method: str
    ) -> None:
        client = self._client()
        getattr(client, client_method).return_value = {"ok": True}
        adapter = PdfBackendAdapter(lambda: client)
        result = adapter.command("s1", operation, params)
        assert result == {"ok": True}
        getattr(client, client_method).assert_called_once()

    def test_command_load_lists_stream(self) -> None:
        client = self._client()
        client.load_stream.return_value = [{"a": 1}, {"b": 2}]
        adapter = PdfBackendAdapter(lambda: client)
        result = adapter.command("s1", "load", {})
        assert result == [{"a": 1}, {"b": 2}]
        client.load_stream.assert_called_once_with("s1")

    def test_command_delete_text_layers_lists_stream(self) -> None:
        client = self._client()
        client.delete_text_layers_stream.return_value = [{"done": 1}]
        adapter = PdfBackendAdapter(lambda: client)
        result = adapter.command("s1", "delete_text_layers", {"pages": ["0"]})
        assert result == [{"done": 1}]
        client.delete_text_layers_stream.assert_called_once_with("s1", [0])

    def test_command_save_forwards_rewrite_flag(self) -> None:
        client = self._client()
        client.save.return_value = {"path": "x", "saved": True}
        adapter = PdfBackendAdapter(lambda: client)
        adapter.command(
            "s1",
            "save",
            {"path": "out", "pdf_settings": {"c": True}, "rewrite_text_layers": False},
        )
        client.save.assert_called_once()
        # 最后一个 kwarg 是 rewrite_text_layers=False
        call = client.save.call_args
        assert call.kwargs["rewrite_text_layers"] is False

    def test_wire_handles_dataclass_dict_list_tuple_and_scalar(self) -> None:
        @dataclasses.dataclass
        class D:
            a: int
            b: list[int]

        obj = D(a=1, b=[2, 3])
        assert PdfBackendAdapter._wire(obj) == {"a": 1, "b": [2, 3]}
        assert PdfBackendAdapter._wire({"k": [1, (2, 3)]}) == {"k": [1, [2, 3]]}
        assert PdfBackendAdapter._wire((1, 2)) == [1, 2]
        assert PdfBackendAdapter._wire("plain") == "plain"

    def test_wire_handles_pydantic_like_model(self) -> None:
        class Model:
            def model_dump(self, mode: str) -> dict[str, Any]:
                return {"dumped": mode}

        assert PdfBackendAdapter._wire(Model()) == {"dumped": "json"}

    def test_shutdown_calls_stop(self) -> None:
        client = self._client()
        adapter = PdfBackendAdapter(lambda: client)
        adapter._get_client()  # 触发懒加载
        adapter.shutdown()
        client.stop.assert_called_once()
        assert adapter._client is None

    def test_shutdown_noop_when_never_started(self) -> None:
        adapter = PdfBackendAdapter(self._client)
        adapter.shutdown()
        assert adapter._client is None


class TestPdfOcrBackendBridge:
    def test_reset_cancel_suppresses_errors(self) -> None:
        client = MagicMock()
        client.reset_cancel.side_effect = RuntimeError("x")
        bridge = _PdfOcrBackendBridge(client, "s1")
        bridge.reset_cancel("s1")  # 不抛

    def test_render_pages_renders_each(self) -> None:
        client = MagicMock()
        client.render_preview.side_effect = lambda sid, idx, dpi: f"b{idx}".encode()
        bridge = _PdfOcrBackendBridge(client, "s1")
        assert bridge.render_pages("s1", [0, 1], None) == [b"b0", b"b1"]

    def test_recognize_pages_returns_placeholders(self) -> None:
        bridge = _PdfOcrBackendBridge(MagicMock(), "s1")
        results = bridge.recognize_pages("s1", [b"a", b"b"], None)
        assert len(results) == 2
        assert results[0].page_index == 0
        assert results[1].page_index == 1

    def test_write_batch_saved_when_resp_saved(self) -> None:
        client = MagicMock()
        client.add_text_layer_batch.return_value = SimpleNamespace(
            extra={"saved": True}
        )
        bridge = _PdfOcrBackendBridge(client, "s1")
        outcome = bridge.write_batch(
            "s1", [(0, {}), (1, {})], overwrite=True, save=True, cancel_check=None
        )
        assert outcome.saved is True
        assert outcome.saved_pages == (0, 1)
        assert outcome.failed_pages == ()

    def test_write_batch_not_saved_when_resp_says_false(self) -> None:
        client = MagicMock()
        client.add_text_layer_batch.return_value = SimpleNamespace(
            extra={"saved": False}
        )
        bridge = _PdfOcrBackendBridge(client, "s1")
        outcome = bridge.write_batch(
            "s1", [(0, {})], overwrite=False, save=True, cancel_check=None
        )
        assert outcome.saved is False
        assert outcome.failed_pages == (0,)

    def test_write_batch_save_true_uses_extra_saved_flag(self) -> None:
        # save=True 时 saved 取决于 resp.extra["saved"]
        client = MagicMock()
        # extra 为 None → saved=False（即使 save=True）
        client.add_text_layer_batch.return_value = SimpleNamespace(extra=None)
        bridge = _PdfOcrBackendBridge(client, "s1")
        outcome = bridge.write_batch(
            "s1", [(0, {})], overwrite=False, save=True, cancel_check=None
        )
        assert outcome.saved is False

    def test_write_batch_save_false_forces_saved_false(self) -> None:
        client = MagicMock()
        client.add_text_layer_batch.return_value = SimpleNamespace(
            extra={"saved": True}
        )
        bridge = _PdfOcrBackendBridge(client, "s1")
        outcome = bridge.write_batch(
            "s1", [(0, {})], overwrite=False, save=False, cancel_check=None
        )
        # save=False → saved=False（else 分支）
        assert outcome.saved is False

    def test_write_batch_records_error_on_exception(self) -> None:
        client = MagicMock()
        client.add_text_layer_batch.side_effect = RuntimeError("write fail")
        bridge = _PdfOcrBackendBridge(client, "s1")
        outcome = bridge.write_batch(
            "s1", [(0, {})], overwrite=False, save=False, cancel_check=None
        )
        assert outcome.saved is False
        assert outcome.failed_pages == (0,)
        assert "backend write failed" in outcome.write_errors

    def test_compress_success(self) -> None:
        client = MagicMock()
        bridge = _PdfOcrBackendBridge(client, "s1")
        assert bridge.compress("s1", None) is True
        client.save.assert_called_once_with("s1", None, rewrite_text_layers=False)

    def test_compress_failure_returns_false(self) -> None:
        client = MagicMock()
        client.save.side_effect = RuntimeError("x")
        bridge = _PdfOcrBackendBridge(client, "s1")
        assert bridge.compress("s1", None) is False

    def test_cancel_suppresses_errors(self) -> None:
        client = MagicMock()
        client.cancel.side_effect = RuntimeError("x")
        bridge = _PdfOcrBackendBridge(client, "s1")
        bridge.cancel("s1")  # 不抛


# ---------------------------------------------------------------------------
# QR adapters
# ---------------------------------------------------------------------------


class _FakeQrService:
    """记录调用并支持可控 pipeline。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.logo_calls: list[tuple[Any, str, float]] = []
        self.label_calls: list[tuple[Any, str, str, int]] = []
        self.invert_calls = 0
        self.svg_calls: list[tuple[str, dict[str, Any]]] = []
        self.decode_items: list[SimpleNamespace] = []

    def default_options(self) -> dict[str, Any]:
        return {"size": 200, "logo_ratio": 0.2, "label_position": "bottom"}

    def generate(self, data: str, options: dict[str, Any]) -> Any:
        self.calls.append((data, options))
        return _Image()

    def apply_logo(self, image: Any, logo_path: str, ratio: float) -> Any:
        self.logo_calls.append((image, logo_path, ratio))
        return image

    def apply_text_label(
        self, image: Any, text: str, position: str, font_size: int
    ) -> Any:
        self.label_calls.append((image, text, position, font_size))
        return image

    def invert_colors(self, image: Any) -> Any:
        self.invert_calls += 1
        return image

    def generate_svg(self, data: str, options: dict[str, Any]) -> str:
        self.svg_calls.append((data, options))
        return "<svg/>"

    def decode_bytes(self, data: bytes) -> list[Any]:
        return self.decode_items


class TestQrGenerateAdapter:
    def test_cancelled_before(self) -> None:
        svc = _FakeQrService()
        adapter = QrGenerateAdapter(lambda: svc)
        with pytest.raises(RuntimeError, match="cancelled"):
            adapter.generate("d", {}, _cancelled())
        assert svc.calls == []

    def test_full_pipeline_with_logo_label_invert(self) -> None:
        svc = _FakeQrService()
        adapter = QrGenerateAdapter(lambda: svc)
        result = adapter.generate(
            "data",
            {
                "logo_path": "logo.png",
                "label_text": "hello",
                "label_font_size": 14,
                "invert": True,
            },
            CancelToken(),
        )
        assert result == b"PNG-DATA"
        assert len(svc.logo_calls) == 1
        assert svc.logo_calls[0][1] == "logo.png"
        assert svc.label_calls[0][2] == "bottom"
        assert svc.label_calls[0][3] == 14
        assert svc.invert_calls == 1

    def test_label_skipped_when_position_none(self) -> None:
        svc = _FakeQrService()
        adapter = QrGenerateAdapter(lambda: svc)
        adapter.generate(
            "d",
            {"label_text": "x", "label_position": "none"},
            CancelToken(),
        )
        assert svc.label_calls == []

    def test_label_skipped_when_empty(self) -> None:
        svc = _FakeQrService()
        adapter = QrGenerateAdapter(lambda: svc)
        adapter.generate("d", {}, CancelToken())
        assert svc.label_calls == []

    def test_service_cached_across_calls(self) -> None:
        factory_calls: list[int] = []
        svc = _FakeQrService()

        def factory() -> Any:
            factory_calls.append(1)
            return svc

        adapter = QrGenerateAdapter(factory)
        adapter.generate("a", {}, CancelToken())
        adapter.generate("b", {}, CancelToken())
        assert factory_calls == [1]  # 只创建一次


class TestQrGenerateSvgAdapter:
    def test_cancelled_before(self) -> None:
        svc = _FakeQrService()
        adapter = QrGenerateSvgAdapter(lambda: svc)
        with pytest.raises(RuntimeError, match="cancelled"):
            adapter.generate_svg("d", {}, _cancelled())

    def test_generates_and_merges_options(self) -> None:
        svc = _FakeQrService()
        adapter = QrGenerateSvgAdapter(lambda: svc)
        result = adapter.generate_svg("data", {"size": 300}, CancelToken())
        assert result == "<svg/>"
        # caller options 覆盖 default
        assert svc.svg_calls[0][1]["size"] == 300
        assert svc.svg_calls[0][1]["logo_ratio"] == 0.2  # 来自默认


class TestQrDecodeAdapter:
    def test_cancelled_before(self) -> None:
        svc = _FakeQrService()
        adapter = QrDecodeAdapter(lambda: svc)
        with pytest.raises(RuntimeError, match="cancelled"):
            adapter.decode(b"x", _cancelled())

    def test_decode_maps_fields(self) -> None:
        svc = _FakeQrService()
        svc.decode_items = [
            SimpleNamespace(data="http://x", type="QRCODE", is_url=True),
            SimpleNamespace(data="plain", type="CODE128", is_url=False),
        ]
        adapter = QrDecodeAdapter(lambda: svc)
        result = adapter.decode(b"img", CancelToken())
        assert result == [
            {"data": "http://x", "format": "QRCODE", "is_url": True},
            {"data": "plain", "format": "CODE128", "is_url": False},
        ]

    def test_service_cached(self) -> None:
        svc = _FakeQrService()
        adapter = QrDecodeAdapter(lambda: svc)
        adapter.decode(b"a", CancelToken())
        adapter.decode(b"b", CancelToken())
        assert adapter._service is svc


# ---------------------------------------------------------------------------
# JsonSettingsAdapter
# ---------------------------------------------------------------------------


def _make_composition(tmp_path: Path) -> WorkerServiceComposition:
    return WorkerServiceComposition(
        project_root=tmp_path,
        profile="winui-dev",
        ocr_factory=lambda: object(),
        pdf_factory=lambda: object(),
        qr_decode_factory=lambda: object(),
        qr_generate_factory=lambda: object(),
        backend_resolver=lambda: "cpu",
    )


class TestJsonSettingsAdapterBranches:
    def test_get_snapshot_missing_config_uses_defaults(self, tmp_path: Path) -> None:
        composition = _make_composition(tmp_path)
        adapter = JsonSettingsAdapter(composition.paths, lambda: "gpu")
        snapshot = adapter.get_snapshot()
        # 无配置文件 → backend 回退 resolver
        assert snapshot.backend == "gpu"
        assert snapshot.preload_pipelines == ()
        # PP-StructureV3/PaddleOCR-VL 默认 300，其余 0
        assert snapshot.pipeline_ttls["PP-StructureV3"] == 300
        assert snapshot.pipeline_ttls["PaddleOCR-VL"] == 300
        assert snapshot.pipeline_ttls["OCR"] == 0

    def test_get_snapshot_invalid_json_falls_back(self, tmp_path: Path) -> None:
        composition = _make_composition(tmp_path)
        composition.paths.config_file.parent.mkdir(parents=True)
        composition.paths.config_file.write_text("{ not json", encoding="utf-8")
        adapter = JsonSettingsAdapter(composition.paths, lambda: "cpu")
        snapshot = adapter.get_snapshot()
        assert snapshot.backend == "cpu"

    def test_get_snapshot_invalid_backend_falls_back_to_resolver(
        self, tmp_path: Path
    ) -> None:
        composition = _make_composition(tmp_path)
        composition.paths.config_file.parent.mkdir(parents=True)
        composition.paths.config_file.write_text(
            json.dumps({"backend": "tpu"}), encoding="utf-8"
        )
        adapter = JsonSettingsAdapter(composition.paths, lambda: "cpu")
        assert adapter.get_snapshot().backend == "cpu"

    def test_get_snapshot_normalizes_non_list_preload_and_ttls(
        self, tmp_path: Path
    ) -> None:
        composition = _make_composition(tmp_path)
        composition.paths.config_file.parent.mkdir(parents=True)
        composition.paths.config_file.write_text(
            json.dumps(
                {
                    "backend": "gpu",
                    "preload_pipelines": "not-a-list",  # 非法 → 空
                    "pipeline_ttls": "also-not-dict",  # 非法 → 默认
                }
            ),
            encoding="utf-8",
        )
        adapter = JsonSettingsAdapter(composition.paths, lambda: "cpu")
        snapshot = adapter.get_snapshot()
        assert snapshot.preload_pipelines == ()
        assert snapshot.pipeline_ttls["OCR"] == 0

    def test_get_snapshot_filters_non_string_preload(self, tmp_path: Path) -> None:
        composition = _make_composition(tmp_path)
        composition.paths.config_file.parent.mkdir(parents=True)
        composition.paths.config_file.write_text(
            json.dumps(
                {
                    "backend": "cpu",
                    "preload_pipelines": ["OCR", 123, "TABLE_RECOGNITION"],
                }
            ),
            encoding="utf-8",
        )
        adapter = JsonSettingsAdapter(composition.paths, lambda: "cpu")
        snapshot = adapter.get_snapshot()
        assert snapshot.preload_pipelines == ("OCR", "TABLE_RECOGNITION")

    def test_get_snapshot_clamps_negative_and_skips_bool_ttl(
        self, tmp_path: Path
    ) -> None:
        composition = _make_composition(tmp_path)
        composition.paths.config_file.parent.mkdir(parents=True)
        composition.paths.config_file.write_text(
            json.dumps(
                {
                    "backend": "cpu",
                    "pipeline_ttls": {"OCR": -50, "TABLE_RECOGNITION": True},
                }
            ),
            encoding="utf-8",
        )
        adapter = JsonSettingsAdapter(composition.paths, lambda: "cpu")
        snapshot = adapter.get_snapshot()
        # 负值被 max(0, ·) 夹到 0
        assert snapshot.pipeline_ttls["OCR"] == 0
        # bool 视为非法 → 默认 0
        assert snapshot.pipeline_ttls["TABLE_RECOGNITION"] == 0

    def test_switch_backend_rejects_invalid_target(self, tmp_path: Path) -> None:
        composition = _make_composition(tmp_path)
        adapter = JsonSettingsAdapter(composition.paths, lambda: "cpu")
        with pytest.raises(RuntimeError, match="unsupported backend"):
            adapter.switch_backend("tpu")

    def test_switch_backend_persists_and_returns(self, tmp_path: Path) -> None:
        composition = _make_composition(tmp_path)
        composition.paths.config_file.parent.mkdir(parents=True)
        composition.paths.config_file.write_text(
            json.dumps({"backend": "cpu", "other": 1}), encoding="utf-8"
        )
        adapter = JsonSettingsAdapter(composition.paths, lambda: "cpu")
        result = adapter.switch_backend("gpu")
        assert result == "gpu"
        data = json.loads(composition.paths.config_file.read_text(encoding="utf-8"))
        assert data["backend"] == "gpu"
        assert data["other"] == 1  # 原字段保留

    def test_switch_backend_creates_parent_dir(self, tmp_path: Path) -> None:
        composition = _make_composition(tmp_path)
        adapter = JsonSettingsAdapter(composition.paths, lambda: "cpu")
        adapter.switch_backend("cpu")
        assert composition.paths.config_file.exists()

    def test_switch_backend_handles_write_failure(self, tmp_path: Path) -> None:
        composition = _make_composition(tmp_path)
        adapter = JsonSettingsAdapter(composition.paths, lambda: "cpu")
        with patch.object(
            type(composition.paths.config_file),
            "write_text",
            side_effect=OSError("disk full"),
        ):
            with pytest.raises(RuntimeError, match="failed to persist"):
                adapter.switch_backend("cpu")

    def test_install_dependency_cancelled(self, tmp_path: Path) -> None:
        composition = _make_composition(tmp_path)
        adapter = JsonSettingsAdapter(composition.paths, lambda: "cpu")
        with pytest.raises(RuntimeError, match="cancelled"):
            adapter.install_dependency("torch", "domestic", _cancelled())

    def test_install_dependency_requires_name(self, tmp_path: Path) -> None:
        composition = _make_composition(tmp_path)
        adapter = JsonSettingsAdapter(composition.paths, lambda: "cpu")
        with pytest.raises(RuntimeError, match="name is required"):
            adapter.install_dependency("", None, CancelToken())

    def test_install_dependency_invalid_source_defaults_domestic(
        self, tmp_path: Path
    ) -> None:
        composition = _make_composition(tmp_path)
        adapter = JsonSettingsAdapter(composition.paths, lambda: "cpu")
        with patch("vibeocr.env_manager.install_single_dependency") as mock_install:
            mock_install.return_value = (True, "ok")
            result = adapter.install_dependency("torch", "weird-source", CancelToken())
        assert result["installed"] is True
        # network_type 回退到 domestic
        assert mock_install.call_args.kwargs["network_type"] == "domestic"
