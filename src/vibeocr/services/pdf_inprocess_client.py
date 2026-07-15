"""In-process PDF adapter used only inside the owning WorkerHost.

It preserves the former ``PdfBackendClient`` API while calling the UI-free
PDF implementation directly.  This removes the localhost/FastAPI child
process so one frontend owns exactly one WorkerHost process.
"""

from __future__ import annotations

import asyncio
from typing import Any

from vibeocr.ipc.schemas import (
    AddTextLayerRequest,
    BatchAddTextLayerPage,
    BatchAddTextLayerRequest,
    DeletePagesRequest,
    DetectTextLayersRequest,
    InsertBlankRequest,
    InsertFromRequest,
    MovePageRequest,
    OpenRequest,
    PageListRequest,
    ProgressEvent,
    ReorderRequest,
    RewriteTextLayerRequest,
    RotateRequest,
    SaveRequest,
    TextBlockMirror,
    UpdateBlockTextRequest,
)
from vibeocr.services import pdf_backend_process as backend


async def _response_bytes(response: Any) -> bytes:
    chunks: list[bytes] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else str(chunk).encode())
    return b"".join(chunks)


def _consume(response: Any) -> bytes:
    return asyncio.run(_response_bytes(response))


class InProcessPdfBackendClient:
    """Former PDF client shape implemented by direct WorkerHost calls."""

    def __init__(self) -> None:
        self._sessions: set[str] = set()

    def open_session(self, path: str):
        response = backend.session_open(OpenRequest(path=path))
        self._sessions.add(response.session_id)
        return response

    def close_session(self, sid: str) -> None:
        backend.session_close(sid)
        self._sessions.discard(sid)

    def get_model(self, sid: str):
        return backend.session_model(sid)

    def load_stream(self, sid: str):
        payload = _consume(backend.session_load(sid))
        for line in payload.splitlines():
            if line:
                yield ProgressEvent.model_validate_json(line)

    def render_thumbnail(self, sid: str, page: int, size: int = 160) -> bytes:
        from vibeocr.ipc.schemas import RenderThumbnailRequest

        return _consume(
            backend.render_thumbnail(sid, RenderThumbnailRequest(page=page, size=size))
        )

    def render_preview(self, sid: str, page: int, dpi: int = 150) -> bytes:
        from vibeocr.ipc.schemas import RenderPreviewRequest

        return _consume(
            backend.render_preview(sid, RenderPreviewRequest(page=page, dpi=dpi))
        )

    def detect_text_layers(self, sid: str, page: int):
        return backend.detect_text_layers(sid, DetectTextLayersRequest(page=page))

    def rotate(self, sid: str, pages: list[int], angle: int):
        return backend.rotate_pages(sid, RotateRequest(pages=pages, angle=angle))

    def delete_pages(self, sid: str, pages: list[int]):
        return backend.delete_pages(sid, DeletePagesRequest(pages=pages))

    def insert_blank(
        self,
        sid: str,
        after_index: int,
        width: float = 612.0,
        height: float = 792.0,
    ):
        return backend.insert_blank(
            sid,
            InsertBlankRequest(
                after_index=after_index, width=width, height=height
            ),
        )

    def insert_from(self, sid: str, source_path: str, after_index: int):
        return backend.insert_from(
            sid,
            InsertFromRequest(source_path=source_path, after_index=after_index),
        )

    def move_page(self, sid: str, from_index: int, to_index: int):
        return backend.move_page(
            sid, MovePageRequest(from_index=from_index, to_index=to_index)
        )

    def reorder(self, sid: str, new_order: list[int]):
        return backend.reorder(sid, ReorderRequest(new_order=new_order))

    def add_text_layer(
        self,
        sid: str,
        page: int,
        ocr_result: dict[str, Any],
        pdf_settings: dict[str, Any] | None = None,
        overwrite: bool = False,
    ):
        return backend.add_text_layer(
            sid,
            AddTextLayerRequest(
                page=page,
                ocr_result=ocr_result,
                pdf_settings=pdf_settings,
                overwrite=overwrite,
            ),
        )

    def add_text_layer_batch(
        self,
        sid: str,
        pages_data: list[dict[str, Any]],
        pdf_settings: dict[str, Any] | None = None,
        overwrite: bool = False,
        save: bool = False,
    ):
        pages = [BatchAddTextLayerPage.model_validate(item) for item in pages_data]
        return backend.add_text_layer_batch(
            sid,
            BatchAddTextLayerRequest(
                pages=pages,
                pdf_settings=pdf_settings,
                overwrite=overwrite,
                save=save,
            ),
        )

    def rewrite_text_layer(
        self,
        sid: str,
        page: int,
        text_blocks: list[Any],
        preproc_angle: int = 0,
        pdf_settings: dict[str, Any] | None = None,
    ):
        blocks = [
            item
            if isinstance(item, TextBlockMirror)
            else TextBlockMirror.model_validate(item)
            for item in text_blocks
        ]
        return backend.rewrite_text_layer(
            sid,
            RewriteTextLayerRequest(
                page=page,
                text_blocks=blocks,
                preproc_angle=preproc_angle,
                pdf_settings=pdf_settings,
            ),
        )

    def update_block_text(
        self, sid: str, page: int, block_index: int, new_text: str
    ):
        return backend.update_block_text(
            sid,
            UpdateBlockTextRequest(
                page=page, block_index=block_index, new_text=new_text
            ),
        )

    def delete_text_layers_stream(self, sid: str, pages: list[int]):
        payload = _consume(
            backend.delete_text_layers(sid, PageListRequest(pages=pages))
        )
        for line in payload.splitlines():
            if line:
                yield ProgressEvent.model_validate_json(line)

    def save(
        self,
        sid: str,
        path: str | None = None,
        pdf_settings: dict[str, Any] | None = None,
    ):
        return backend.save(sid, SaveRequest(path=path, pdf_settings=pdf_settings))

    def cancel(self, sid: str) -> None:
        backend.cancel(sid)

    def reset_cancel(self, sid: str) -> None:
        backend.reset_cancel(sid)

    def stop(self) -> None:
        for sid in tuple(self._sessions):
            try:
                self.close_session(sid)
            except Exception:
                pass


__all__ = ["InProcessPdfBackendClient"]
