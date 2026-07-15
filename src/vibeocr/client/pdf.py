"""Classic PDF client facade backed exclusively by the shared WorkerHost."""

from __future__ import annotations

from typing import Any, ClassVar

from vibeocr.client.session import get_backend_client
from vibeocr.ipc.schemas import (
    DetectTextLayersResponse,
    MutateResponse,
    OpenResponse,
    PdfDocumentMirror,
    ProgressEvent,
    SaveResponse,
)


class PdfClientError(RuntimeError):
    """A PDF request through WorkerHost failed."""


def _wire(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_wire(item) for item in value]
    return value


class PdfBackendClient:
    """Compatibility API for the Classic PDF view model.

    The class intentionally mirrors the former HTTP client's return models,
    but every operation is sent through the process-wide authenticated
    WorkerHost session.
    """

    _instance: ClassVar[PdfBackendClient | None] = None

    @classmethod
    def instance(cls) -> PdfBackendClient:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def _client():
        return get_backend_client()

    def start(self) -> None:
        """Ensure the process-wide WorkerHost session is ready."""
        self._client()

    def _command(
        self,
        sid: str,
        operation: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 600.0,
    ) -> Any:
        try:
            return self._client().pdf_command_sync(
                sid, operation, _wire(params or {}), timeout=timeout
            )
        except Exception as exc:
            raise PdfClientError(str(exc)) from exc

    def open_session(self, path: str) -> OpenResponse:
        try:
            opened = self._client().open_pdf_sync(path)
            sid = str(opened["session_id"])
            model = PdfDocumentMirror.model_validate(self._command(sid, "model"))
            return OpenResponse(session_id=sid, model=model)
        except Exception as exc:
            if isinstance(exc, PdfClientError):
                raise
            raise PdfClientError(str(exc)) from exc

    def close_session(self, sid: str) -> None:
        try:
            self._client().close_pdf_sync(sid)
        except Exception as exc:
            raise PdfClientError(str(exc)) from exc

    def get_model(self, sid: str) -> PdfDocumentMirror:
        return PdfDocumentMirror.model_validate(self._command(sid, "model"))

    def load_stream(self, sid: str):
        for item in self._command(sid, "load") or []:
            yield ProgressEvent.model_validate(item)

    def render_thumbnail(self, sid: str, page: int, size: int = 160) -> bytes:
        return self._client().render_pdf_page_sync(sid, page, size=size)

    def render_preview(self, sid: str, page: int, dpi: int = 150) -> bytes:
        return self._client().render_pdf_page_sync(sid, page, dpi=dpi)

    def detect_text_layers(self, sid: str, page: int) -> DetectTextLayersResponse:
        return DetectTextLayersResponse.model_validate(
            self._command(sid, "detect_text_layers", {"page": page})
        )

    def _mutate(self, sid: str, operation: str, params: dict[str, Any]) -> MutateResponse:
        return MutateResponse.model_validate(self._command(sid, operation, params))

    def rotate(self, sid: str, pages: list[int], angle: int) -> MutateResponse:
        return self._mutate(sid, "rotate", {"pages": pages, "angle": angle})

    def delete_pages(self, sid: str, pages: list[int]) -> MutateResponse:
        return self._mutate(sid, "delete_pages", {"pages": pages})

    def insert_blank(
        self,
        sid: str,
        after_index: int,
        width: float = 612.0,
        height: float = 792.0,
    ) -> MutateResponse:
        return self._mutate(
            sid,
            "insert_blank",
            {"after_index": after_index, "width": width, "height": height},
        )

    def insert_from(self, sid: str, source_path: str, after_index: int) -> MutateResponse:
        return self._mutate(
            sid,
            "insert_from",
            {"source_path": source_path, "after_index": after_index},
        )

    def move_page(self, sid: str, from_index: int, to_index: int) -> MutateResponse:
        return self._mutate(
            sid, "move_page", {"from_index": from_index, "to_index": to_index}
        )

    def reorder(self, sid: str, new_order: list[int]) -> MutateResponse:
        return self._mutate(sid, "reorder", {"new_order": new_order})

    def add_text_layer(
        self,
        sid: str,
        page: int,
        ocr_result: dict[str, Any],
        pdf_settings: dict[str, Any] | None = None,
        overwrite: bool = False,
    ) -> MutateResponse:
        return self._mutate(
            sid,
            "add_text_layer",
            {
                "page": page,
                "ocr_result": ocr_result,
                "pdf_settings": pdf_settings,
                "overwrite": overwrite,
            },
        )

    def add_text_layer_batch(
        self,
        sid: str,
        pages_data: list[dict[str, Any]],
        pdf_settings: dict[str, Any] | None = None,
        overwrite: bool = False,
        save: bool = False,
    ) -> MutateResponse:
        return self._mutate(
            sid,
            "add_text_layer_batch",
            {
                "pages": pages_data,
                "pdf_settings": pdf_settings,
                "overwrite": overwrite,
                "save": save,
            },
        )

    def rewrite_text_layer(
        self,
        sid: str,
        page: int,
        text_blocks: list[Any],
        preproc_angle: int = 0,
        pdf_settings: dict[str, Any] | None = None,
    ) -> MutateResponse:
        return self._mutate(
            sid,
            "rewrite_text_layer",
            {
                "page": page,
                "text_blocks": text_blocks,
                "preproc_angle": preproc_angle,
                "pdf_settings": pdf_settings,
            },
        )

    def update_block_text(
        self, sid: str, page: int, block_index: int, new_text: str
    ) -> MutateResponse:
        return self._mutate(
            sid,
            "update_block_text",
            {"page": page, "block_index": block_index, "new_text": new_text},
        )

    def delete_text_layers_stream(self, sid: str, pages: list[int]):
        for item in self._command(sid, "delete_text_layers", {"pages": pages}) or []:
            yield ProgressEvent.model_validate(item)

    def save(
        self,
        sid: str,
        path: str | None = None,
        pdf_settings: dict[str, Any] | None = None,
    ) -> SaveResponse:
        return SaveResponse.model_validate(
            self._command(
                sid, "save", {"path": path, "pdf_settings": pdf_settings}
            )
        )

    def cancel(self, sid: str) -> None:
        self._command(sid, "cancel", timeout=10.0)

    def reset_cancel(self, sid: str) -> None:
        self._command(sid, "reset_cancel", timeout=10.0)

    def stop(self) -> None:
        """The process-wide session owns lifecycle; individual tabs do not."""


__all__ = ["PdfBackendClient", "PdfClientError"]
