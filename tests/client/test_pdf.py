"""PdfBackendClient (client/pdf.py) delegation wrappers 测试。

client/pdf.py 是 Classic PDF view model 对 WorkerHost 的兼容外观层。
所有方法都是薄包装：组装 params → _command/_client → 校验 schema 返回。
本文件覆盖 _wire、_command 异常映射、各 mutate/render/detect 方法的参数转发，
以及单例 instance()/start()/stop()。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from vibeocr.client.pdf import PdfBackendClient, PdfClientError, _wire
from vibeocr.ipc.schemas import (
    DetectTextLayersResponse,
    ModelDiff,
    MutateResponse,
    OpenResponse,
    PdfDocumentMirror,
    ProgressEvent,
    SaveResponse,
)

# ---------------------------------------------------------------------------
# _wire 递归序列化
# ---------------------------------------------------------------------------


class TestWire:
    def test_passthrough_scalar(self) -> None:
        assert _wire(42) == 42
        assert _wire("s") == "s"
        assert _wire(None) is None

    def test_dict_recurses_and_stringifies_keys(self) -> None:
        assert _wire({1: "a", "b": [1, 2]}) == {"1": "a", "b": [1, 2]}

    def test_list_and_tuple_recurse(self) -> None:
        assert _wire([{"a": 1}, (2, 3)]) == [{"a": 1}, [2, 3]]

    def test_pydantic_model_uses_model_dump(self) -> None:
        diff = ModelDiff(structural_change=True)
        assert _wire(diff) == diff.model_dump(mode="json")


# ---------------------------------------------------------------------------
# _command 异常映射 + 默认 timeout
# ---------------------------------------------------------------------------


class TestCommandErrorMapping:
    def test_command_wraps_exception_in_pdfclienterror(self) -> None:
        client = PdfBackendClient()
        backend = MagicMock()
        backend.pdf_command_sync.side_effect = ValueError("boom")
        with patch("vibeocr.client.pdf.get_backend_client", return_value=backend):
            with pytest.raises(PdfClientError, match="boom"):
                client._command("sid", "model")
        # 默认 timeout=600.0
        backend.pdf_command_sync.assert_called_once()
        assert backend.pdf_command_sync.call_args.kwargs["timeout"] == 600.0

    def test_command_passes_none_params_as_empty_dict(self) -> None:
        client = PdfBackendClient()
        backend = MagicMock()
        backend.pdf_command_sync.return_value = {"ok": True}
        with patch("vibeocr.client.pdf.get_backend_client", return_value=backend):
            client._command("sid", "model", None)
        backend.pdf_command_sync.assert_called_once_with(
            "sid", "model", {}, timeout=600.0
        )

    def test_command_uses_custom_timeout(self) -> None:
        client = PdfBackendClient()
        backend = MagicMock()
        backend.pdf_command_sync.return_value = {"ok": True}
        with patch("vibeocr.client.pdf.get_backend_client", return_value=backend):
            client._command("sid", "cancel", timeout=10.0)
        assert backend.pdf_command_sync.call_args.kwargs["timeout"] == 10.0


# ---------------------------------------------------------------------------
# open_session / close_session / get_model / load_stream
# ---------------------------------------------------------------------------


class TestSessionLifecycle:
    def test_open_session_builds_open_response(self) -> None:
        client = PdfBackendClient()
        backend = MagicMock()
        backend.open_pdf_sync.return_value = {"session_id": "s1"}
        model_dict = {"file_path": "x.pdf", "pages": []}
        backend.pdf_command_sync.return_value = model_dict
        with patch("vibeocr.client.pdf.get_backend_client", return_value=backend):
            response = client.open_session("doc.pdf")
        assert isinstance(response, OpenResponse)
        assert response.session_id == "s1"
        assert isinstance(response.model, PdfDocumentMirror)

    def test_open_session_wraps_non_pdfclienterror(self) -> None:
        client = PdfBackendClient()
        backend = MagicMock()
        backend.open_pdf_sync.side_effect = KeyError("missing")
        with patch("vibeocr.client.pdf.get_backend_client", return_value=backend):
            with pytest.raises(PdfClientError):
                client.open_session("doc.pdf")

    def test_open_session_preserves_existing_pdfclienterror(self) -> None:
        client = PdfBackendClient()
        backend = MagicMock()
        backend.pdf_command_sync.side_effect = RuntimeError("wire fail")
        with patch("vibeocr.client.pdf.get_backend_client", return_value=backend):
            with pytest.raises(PdfClientError, match="wire fail"):
                client.open_session("doc.pdf")

    def test_close_session_wraps_error(self) -> None:
        client = PdfBackendClient()
        backend = MagicMock()
        backend.close_pdf_sync.side_effect = RuntimeError("x")
        with patch("vibeocr.client.pdf.get_backend_client", return_value=backend):
            with pytest.raises(PdfClientError, match="x"):
                client.close_session("sid")

    def test_get_model_validates(self) -> None:
        client = PdfBackendClient()
        client._command = MagicMock(  # type: ignore[method-assign]
            return_value={"file_path": "a", "pages": []}
        )
        model = client.get_model("sid")
        assert isinstance(model, PdfDocumentMirror)
        assert model.file_path == "a"

    def test_load_stream_yields_progress_events(self) -> None:
        client = PdfBackendClient()
        client._command = MagicMock(  # type: ignore[method-assign]
            return_value=[
                {"phase": "load", "current": 0, "total": 2},
                {"phase": "load", "current": 1, "total": 2},
            ]
        )
        events = list(client.load_stream("sid"))
        assert len(events) == 2
        assert all(isinstance(e, ProgressEvent) for e in events)

    def test_load_stream_empty(self) -> None:
        client = PdfBackendClient()
        client._command = MagicMock(return_value=None)  # type: ignore[method-assign]
        assert list(client.load_stream("sid")) == []


# ---------------------------------------------------------------------------
# render / detect / mutate delegations
# ---------------------------------------------------------------------------


class TestRenderAndDetect:
    def test_render_thumbnail_delegates_to_backend(self) -> None:
        client = PdfBackendClient()
        backend = MagicMock()
        backend.render_pdf_page_sync.return_value = b"thumb"
        with patch("vibeocr.client.pdf.get_backend_client", return_value=backend):
            assert client.render_thumbnail("sid", 2, size=128) == b"thumb"
        backend.render_pdf_page_sync.assert_called_once_with("sid", 2, size=128)

    def test_render_preview_delegates_with_dpi(self) -> None:
        client = PdfBackendClient()
        backend = MagicMock()
        backend.render_pdf_page_sync.return_value = b"preview"
        with patch("vibeocr.client.pdf.get_backend_client", return_value=backend):
            assert client.render_preview("sid", 1, dpi=300) == b"preview"
        backend.render_pdf_page_sync.assert_called_once_with("sid", 1, dpi=300)

    def test_detect_text_layers_validates_response(self) -> None:
        client = PdfBackendClient()
        client._command = MagicMock(  # type: ignore[method-assign]
            return_value={"text_layers": []}
        )
        response = client.detect_text_layers("sid", 3)
        assert isinstance(response, DetectTextLayersResponse)
        client._command.assert_called_once_with(
            "sid", "detect_text_layers", {"page": 3}
        )


class TestMutateDelegations:
    def _client_with_command(self, return_value: Any) -> PdfBackendClient:
        client = PdfBackendClient()
        client._command = MagicMock(return_value=return_value)  # type: ignore[method-assign]
        return client

    def test_rotate(self) -> None:
        client = self._client_with_command({"diff": {}, "extra": None})
        response = client.rotate("sid", [0, 1], 90)
        assert isinstance(response, MutateResponse)
        client._command.assert_called_once_with(
            "sid", "rotate", {"pages": [0, 1], "angle": 90}
        )

    def test_delete_pages(self) -> None:
        client = self._client_with_command({"diff": {}})
        response = client.delete_pages("sid", [2])
        assert isinstance(response, MutateResponse)
        client._command.assert_called_once_with("sid", "delete_pages", {"pages": [2]})

    def test_insert_blank_defaults(self) -> None:
        client = self._client_with_command({"diff": {}})
        client.insert_blank("sid", 0)
        client._command.assert_called_once_with(
            "sid",
            "insert_blank",
            {"after_index": 0, "width": 612.0, "height": 792.0},
        )

    def test_insert_blank_custom_dims(self) -> None:
        client = self._client_with_command({"diff": {}})
        client.insert_blank("sid", 1, width=100.0, height=200.0)
        client._command.assert_called_once_with(
            "sid",
            "insert_blank",
            {"after_index": 1, "width": 100.0, "height": 200.0},
        )

    def test_insert_from(self) -> None:
        client = self._client_with_command({"diff": {}})
        client.insert_from("sid", "src.pdf", 3)
        client._command.assert_called_once_with(
            "sid",
            "insert_from",
            {"source_path": "src.pdf", "after_index": 3},
        )

    def test_move_page(self) -> None:
        client = self._client_with_command({"diff": {}})
        client.move_page("sid", 0, 2)
        client._command.assert_called_once_with(
            "sid", "move_page", {"from_index": 0, "to_index": 2}
        )

    def test_reorder(self) -> None:
        client = self._client_with_command({"diff": {}})
        client.reorder("sid", [2, 0, 1])
        client._command.assert_called_once_with(
            "sid", "reorder", {"new_order": [2, 0, 1]}
        )

    def test_add_text_layer(self) -> None:
        client = self._client_with_command({"diff": {}})
        client.add_text_layer(
            "sid", 1, {"text": "x"}, pdf_settings={"c": True}, overwrite=True
        )
        client._command.assert_called_once_with(
            "sid",
            "add_text_layer",
            {
                "page": 1,
                "ocr_result": {"text": "x"},
                "pdf_settings": {"c": True},
                "overwrite": True,
            },
        )

    def test_add_text_layer_batch(self) -> None:
        client = self._client_with_command({"diff": {}})
        client.add_text_layer_batch(
            "sid",
            [{"page": 0}],
            pdf_settings=None,
            overwrite=False,
            save=True,
        )
        client._command.assert_called_once_with(
            "sid",
            "add_text_layer_batch",
            {
                "pages": [{"page": 0}],
                "pdf_settings": None,
                "overwrite": False,
                "save": True,
            },
        )

    def test_rewrite_text_layer(self) -> None:
        client = self._client_with_command({"diff": {}})
        client.rewrite_text_layer(
            "sid", 0, [{"b": 1}], preproc_angle=90, pdf_settings=None
        )
        client._command.assert_called_once_with(
            "sid",
            "rewrite_text_layer",
            {
                "page": 0,
                "text_blocks": [{"b": 1}],
                "preproc_angle": 90,
                "pdf_settings": None,
            },
        )

    def test_update_block_text(self) -> None:
        client = self._client_with_command({"diff": {}})
        client.update_block_text("sid", 0, 2, "new")
        client._command.assert_called_once_with(
            "sid",
            "update_block_text",
            {"page": 0, "block_index": 2, "new_text": "new"},
        )

    def test_delete_text_layers_stream_yields(self) -> None:
        client = PdfBackendClient()
        client._command = MagicMock(  # type: ignore[method-assign]
            return_value=[{"phase": "delete", "current": 1, "total": 1}]
        )
        events = list(client.delete_text_layers_stream("sid", [0, 1]))
        assert len(events) == 1
        assert isinstance(events[0], ProgressEvent)
        client._command.assert_called_once_with(
            "sid", "delete_text_layers", {"pages": [0, 1]}
        )

    def test_delete_text_layers_stream_empty(self) -> None:
        client = PdfBackendClient()
        client._command = MagicMock(return_value=None)  # type: ignore[method-assign]
        assert list(client.delete_text_layers_stream("sid", [0])) == []


# ---------------------------------------------------------------------------
# save / cancel / reset_cancel
# ---------------------------------------------------------------------------


class TestSaveAndCancel:
    def test_save_with_fast_finalize_flag(self) -> None:
        client = PdfBackendClient()
        client._command = MagicMock(  # type: ignore[method-assign]
            return_value=SaveResponse(path="C:/doc.pdf", diff=ModelDiff())
        )
        response = client.save(
            "sid-1",
            None,
            {"compress_on_save": True},
            rewrite_text_layers=False,
        )
        assert response.path == "C:/doc.pdf"
        client._command.assert_called_once_with(
            "sid-1",
            "save",
            {
                "path": None,
                "pdf_settings": {"compress_on_save": True},
                "rewrite_text_layers": False,
            },
        )

    def test_save_default_rewrite_text_layers(self) -> None:
        client = PdfBackendClient()
        client._command = MagicMock(  # type: ignore[method-assign]
            return_value=SaveResponse(path="x", diff=ModelDiff())
        )
        client.save("sid", "out.pdf")
        params = client._command.call_args.args[2]
        assert params["rewrite_text_layers"] is True

    def test_cancel_uses_short_timeout(self) -> None:
        client = PdfBackendClient()
        client._command = MagicMock()  # type: ignore[method-assign]
        client.cancel("sid")
        client._command.assert_called_once_with("sid", "cancel", timeout=10.0)

    def test_reset_cancel_uses_short_timeout(self) -> None:
        client = PdfBackendClient()
        client._command = MagicMock()  # type: ignore[method-assign]
        client.reset_cancel("sid")
        client._command.assert_called_once_with("sid", "reset_cancel", timeout=10.0)


# ---------------------------------------------------------------------------
# lifecycle: instance / start / stop
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_instance_is_singleton(self) -> None:
        PdfBackendClient._instance = None  # reset
        a = PdfBackendClient.instance()
        b = PdfBackendClient.instance()
        assert a is b
        PdfBackendClient._instance = None  # cleanup

    def test_start_calls_client(self) -> None:
        client = PdfBackendClient()
        backend = MagicMock()
        with patch(
            "vibeocr.client.pdf.get_backend_client", return_value=backend
        ) as mock_get:
            client.start()
        mock_get.assert_called_once()

    def test_stop_is_noop(self) -> None:
        # stop() 不应抛异常（lifecycle 由全局会话管理）
        PdfBackendClient().stop()
