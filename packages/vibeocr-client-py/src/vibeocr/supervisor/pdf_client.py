"""Async + sync HTTP v2 client for PDF session operations.

The supervisor owns the PDF child process (plan §6 / ADR §"Transport"); the
GUI never instantiates ``PdfBackendClient`` directly. This module exposes the
client surface the PySide PDF session manager / IPC workers use instead.

* :class:`PdfSupervisorClient` — async, built on ``httpx.AsyncClient``, mirrors
  the full ``PdfBackendClient`` business API (open/close/load_stream/render/
  mutate/text-layer/save/cancel). Method names and DTOs (``vibeocr.ipc.schemas``)
  are identical to the legacy client so the PySide transport swap is a drop-in.
* :class:`SyncPdfSupervisorClient` — sync wrapper driving the async client on a
  dedicated background event loop. PySide PDF workers are plain ``QThread`` and
  cannot await; this wrapper lets them call the same surface synchronously,
  including streaming operations (``load_stream`` / ``delete_text_layers_stream``)
  which yield ``ProgressEvent`` objects from the NDJSON response.

Loopback + Bearer token are pinned exactly like :class:`SupervisorClient`.
"""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

from vibeocr.ipc.schemas import (
    AddTextLayerRequest,
    BatchAddTextLayerRequest,
    DeletePagesRequest,
    DetectTextLayersRequest,
    DetectTextLayersResponse,
    HealthResponse,
    InsertBlankRequest,
    InsertFromRequest,
    MovePageRequest,
    MutateResponse,
    OpenRequest,
    OpenResponse,
    PageListRequest,
    PdfDocumentMirror,
    ProgressEvent,
    RenderPreviewRequest,
    RenderThumbnailRequest,
    ReorderRequest,
    RewriteTextLayerRequest,
    RotateRequest,
    SaveRequest,
    SaveResponse,
    UpdateBlockTextRequest,
)
from vibeocr.protocol.v2 import ErrorCode

from .errors import InferenceClientError


class PdfBackendError(InferenceClientError):
    """Backwards-compatible error for PDF backend transport failures.

    The legacy ``PdfBackendError`` was a ``RuntimeError`` subclass raised with
    a single message string (e.g. ``PdfBackendError("load 失败 (500)")``).
    PySide code both raises it that way and catches it by name. We keep the
    single-string call site working by mapping the message to
    ``ErrorCode.INTERNAL_ERROR`` while still being a typed
    :class:`InferenceClientError` subclass (so new code can read ``.code``).

    Note: the *legacy* ``vibeocr.services.pdf_backend_client.PdfBackendError``
    (still used inside the supervisor to talk to the PDF child) is a separate
    ``RuntimeError`` subclass. The two are NOT the same class — code that
    needs to catch both should catch ``Exception`` or import the specific one.
    The session manager catches this (new) class for the supervisor transport
    path; supervisor-internal code catches its own.
    """

    def __init__(self, message_or_code: Any, message: str | None = None, **kwargs: Any) -> None:
        if message is None:
            # Legacy single-string form: PdfBackendError("boom").
            super().__init__(ErrorCode.INTERNAL_ERROR, str(message_or_code), **kwargs)
        else:
            # Typed form: PdfBackendError(ErrorCode.X, "boom", ...).
            super().__init__(message_or_code, message, **kwargs)


# HTTP timeouts mirror the legacy PdfBackendClient: quick ops 60 s, long ops
# (render at 300 DPI / batch write / streaming load) 600 s, both with a 5 s
# connect bound so a wedged supervisor fails fast.
_HTTP_TIMEOUT = httpx.Timeout(60.0, connect=5.0)
_HTTP_LONG_TIMEOUT = httpx.Timeout(600.0, connect=5.0)


class PdfSupervisorClient:
    """Async HTTP v2 client for PDF session ops. Use as an async context manager.

    The lifecycle mirrors :class:`SupervisorClient`: pin loopback, attach the
    session Bearer token, lazily create one ``httpx.AsyncClient``. Method names
    and return DTOs are identical to the legacy ``PdfBackendClient`` so PySide
    workers can swap transports with no signature change.
    """

    def __init__(
        self, *, base_url: str, session_token: str, instance_id: str | None = None
    ) -> None:
        if not base_url.startswith("http://127.0.0.1"):
            raise PdfBackendError(
                ErrorCode.FORBIDDEN_LOOPBACK,
                "pdf supervisor client refuses non-loopback base url",
            )
        self._base_url = base_url.rstrip("/")
        self._token = session_token
        self.instance_id = instance_id
        self._client: httpx.AsyncClient | None = None

    @property
    def base_url(self) -> str:
        return self._base_url

    async def __aenter__(self) -> PdfSupervisorClient:
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=_HTTP_TIMEOUT,
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError(
                "PdfSupervisorClient must be used as an async context manager"
            )
        return self._client

    def _error_from_response(self, resp: httpx.Response) -> PdfBackendError:
        try:
            body = resp.json()
            from vibeocr.protocol.v2 import parse_error_payload

            payload = parse_error_payload(body)
            return PdfBackendError.from_payload(payload)  # type: ignore[attr-defined]
        except Exception:
            return PdfBackendError(
                ErrorCode.INTERNAL_ERROR,
                f"unexpected pdf response status={resp.status_code}",
                retryable=False,
                detail={"status_code": resp.status_code},
            )

    def _raise_on_error(self, resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            raise self._error_from_response(resp)

    # ---- session lifecycle --------------------------------------------

    async def start(self) -> None:
        """No-op kept for API parity with the legacy PdfBackendClient.

        The supervisor process owns the PDF child; the supervisor spawns it on
        first ``open_session``. Calling this is harmless.
        """

    async def health(self) -> HealthResponse:
        client = self._require_client()
        resp = await client.get("/v2/pdf/health")
        self._raise_on_error(resp)
        return HealthResponse.model_validate(resp.json())

    async def open_session(self, path: str) -> OpenResponse:
        client = self._require_client()
        resp = await client.post(
            "/v2/pdf/sessions/open",
            json=OpenRequest(path=path).model_dump(),
        )
        self._raise_on_error(resp)
        return OpenResponse.model_validate(resp.json())

    async def close_session(self, sid: str) -> None:
        client = self._require_client()
        resp = await client.post(f"/v2/pdf/sessions/{sid}/close")
        self._raise_on_error(resp)

    async def get_model(self, sid: str) -> PdfDocumentMirror:
        client = self._require_client()
        resp = await client.post(f"/v2/pdf/sessions/{sid}/model")
        self._raise_on_error(resp)
        return PdfDocumentMirror.model_validate(resp.json())

    async def load_stream(self, sid: str) -> AsyncIterator[ProgressEvent]:
        """Stream per-page text-layer detection. Yields one ProgressEvent per page."""
        client = self._require_client()
        try:
            async with client.stream(
                "POST", f"/v2/pdf/sessions/{sid}/load", timeout=_HTTP_LONG_TIMEOUT
            ) as resp:
                self._raise_on_error(resp)
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    yield ProgressEvent.model_validate_json(line)
        except httpx.HTTPError as e:
            raise PdfBackendError(
                ErrorCode.INTERNAL_ERROR, f"load 流式调用失败: {e}"
            ) from e

    # ---- render -------------------------------------------------------

    async def render_thumbnail(self, sid: str, page: int, size: int = 160) -> bytes:
        client = self._require_client()
        resp = await client.post(
            f"/v2/pdf/sessions/{sid}/render_thumbnail",
            json=RenderThumbnailRequest(page=page, size=size).model_dump(),
            timeout=_HTTP_TIMEOUT,
        )
        self._raise_on_error(resp)
        return resp.content

    async def render_preview(self, sid: str, page: int, dpi: int = 150) -> bytes:
        client = self._require_client()
        resp = await client.post(
            f"/v2/pdf/sessions/{sid}/render_preview",
            json=RenderPreviewRequest(page=page, dpi=dpi).model_dump(),
            timeout=_HTTP_LONG_TIMEOUT,
        )
        self._raise_on_error(resp)
        return resp.content

    async def detect_text_layers(
        self, sid: str, page: int
    ) -> DetectTextLayersResponse:
        client = self._require_client()
        resp = await client.post(
            f"/v2/pdf/sessions/{sid}/detect_text_layers",
            json=DetectTextLayersRequest(page=page).model_dump(),
        )
        self._raise_on_error(resp)
        return DetectTextLayersResponse.model_validate(resp.json())

    # ---- page mutations ----------------------------------------------

    async def rotate(self, sid: str, pages: list[int], angle: int) -> MutateResponse:
        return await self._mutate(
            sid,
            "rotate",
            RotateRequest(pages=pages, angle=angle).model_dump(),
        )

    async def delete_pages(self, sid: str, pages: list[int]) -> MutateResponse:
        return await self._mutate(
            sid, "delete_pages", DeletePagesRequest(pages=pages).model_dump()
        )

    async def insert_blank(
        self,
        sid: str,
        after_index: int,
        width: float = 612.0,
        height: float = 792.0,
    ) -> MutateResponse:
        return await self._mutate(
            sid,
            "insert_blank",
            InsertBlankRequest(
                after_index=after_index, width=width, height=height
            ).model_dump(),
        )

    async def insert_from(
        self, sid: str, source_path: str, after_index: int
    ) -> MutateResponse:
        return await self._mutate(
            sid,
            "insert_from",
            InsertFromRequest(
                source_path=source_path, after_index=after_index
            ).model_dump(),
        )

    async def move_page(
        self, sid: str, from_index: int, to_index: int
    ) -> MutateResponse:
        return await self._mutate(
            sid,
            "move_page",
            MovePageRequest(from_index=from_index, to_index=to_index).model_dump(),
        )

    async def reorder(self, sid: str, new_order: list[int]) -> MutateResponse:
        return await self._mutate(
            sid, "reorder", ReorderRequest(new_order=new_order).model_dump()
        )

    async def _mutate(self, sid: str, op: str, body: dict[str, Any]) -> MutateResponse:
        client = self._require_client()
        resp = await client.post(f"/v2/pdf/sessions/{sid}/{op}", json=body)
        self._raise_on_error(resp)
        return MutateResponse.model_validate(resp.json())

    # ---- text layer ---------------------------------------------------

    async def add_text_layer(
        self,
        sid: str,
        page: int,
        ocr_result: dict[str, Any],
        pdf_settings: dict[str, Any] | None = None,
        overwrite: bool = False,
    ) -> MutateResponse:
        return await self._mutate(
            sid,
            "add_text_layer",
            AddTextLayerRequest(
                page=page,
                ocr_result=ocr_result,
                pdf_settings=pdf_settings,
                overwrite=overwrite,
            ).model_dump(),
        )

    async def add_text_layer_batch(
        self,
        sid: str,
        pages_data: list[dict[str, Any]],
        pdf_settings: dict[str, Any] | None = None,
        overwrite: bool = False,
        save: bool = False,
    ) -> MutateResponse:
        client = self._require_client()
        body = BatchAddTextLayerRequest(
            pages=[
                {"page": p["page"], "ocr_result": p["ocr_result"]} for p in pages_data
            ],
            pdf_settings=pdf_settings,
            overwrite=overwrite,
            save=save,
        ).model_dump()
        resp = await client.post(
            f"/v2/pdf/sessions/{sid}/add_text_layer_batch",
            json=body,
            timeout=_HTTP_LONG_TIMEOUT,
        )
        self._raise_on_error(resp)
        return MutateResponse.model_validate(resp.json())

    async def rewrite_text_layer(
        self,
        sid: str,
        page: int,
        text_blocks: list[Any],
        preproc_angle: int = 0,
        pdf_settings: dict[str, Any] | None = None,
    ) -> MutateResponse:
        return await self._mutate(
            sid,
            "rewrite_text_layer",
            RewriteTextLayerRequest(
                page=page,
                text_blocks=text_blocks,
                preproc_angle=preproc_angle,
                pdf_settings=pdf_settings,
            ).model_dump(),
        )

    async def update_block_text(
        self, sid: str, page: int, block_index: int, new_text: str
    ) -> MutateResponse:
        return await self._mutate(
            sid,
            "update_block_text",
            UpdateBlockTextRequest(
                page=page, block_index=block_index, new_text=new_text
            ).model_dump(),
        )

    async def delete_text_layers_stream(
        self, sid: str, pages: list[int]
    ) -> AsyncIterator[ProgressEvent]:
        """Stream per-page text-layer deletion."""
        client = self._require_client()
        try:
            async with client.stream(
                "POST",
                f"/v2/pdf/sessions/{sid}/delete_text_layers",
                json=PageListRequest(pages=pages).model_dump(),
                timeout=_HTTP_LONG_TIMEOUT,
            ) as resp:
                self._raise_on_error(resp)
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    yield ProgressEvent.model_validate_json(line)
        except httpx.HTTPError as e:
            raise PdfBackendError(
                ErrorCode.INTERNAL_ERROR, f"delete_text_layers 流式调用失败: {e}"
            ) from e

    # ---- save ---------------------------------------------------------

    async def save(
        self,
        sid: str,
        path: str | None = None,
        pdf_settings: dict[str, Any] | None = None,
        *,
        rewrite_text_layers: bool = True,
    ) -> SaveResponse:
        client = self._require_client()
        body = SaveRequest(
            path=path,
            pdf_settings=pdf_settings,
            rewrite_text_layers=rewrite_text_layers,
        ).model_dump()
        resp = await client.post(
            f"/v2/pdf/sessions/{sid}/save", json=body, timeout=_HTTP_LONG_TIMEOUT
        )
        self._raise_on_error(resp)
        return SaveResponse.model_validate(resp.json())

    # ---- cancel -------------------------------------------------------

    async def cancel(self, sid: str) -> None:
        client = self._require_client()
        resp = await client.post(f"/v2/pdf/sessions/{sid}/cancel")
        self._raise_on_error(resp)

    async def reset_cancel(self, sid: str) -> None:
        client = self._require_client()
        resp = await client.post(f"/v2/pdf/sessions/{sid}/reset_cancel")
        self._raise_on_error(resp)


class _BackgroundLoop:
    """A dedicated event loop running on a daemon thread.

    PySide PDF workers are plain ``QThread`` instances with no running asyncio
    loop; they need to call the async supervisor client synchronously. We drive
    coroutines on a shared background loop and block the caller until they
    complete. The loop outlives any single worker (httpx connection pooling,
    mid-stream cancel safety) and is torn down only at process exit.
    """

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run, name="pdf-supervisor-loop", daemon=True
        )
        self._thread.start()
        self._lock = threading.Lock()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run(self, coro: Any, timeout: float | None = None) -> Any:
        with self._lock:
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            return future.result(timeout=timeout)

    def iterate_stream(
        self, async_gen_factory: Any, timeout_per_item: float | None = None
    ) -> Iterator[Any]:
        """Drive an async generator from a sync caller, yielding each item.

        ``async_gen_factory`` is a zero-arg callable returning the async
        generator (so a fresh one is built inside the loop thread). We pull
        items one at a time by awaiting ``__anext__`` on the loop; ``timeout``
        bounds each pull so a hung stream surfaces rather than deadlocks.
        """
        # Build the generator inside the loop thread so __aiter__ runs there.
        gen_holder: dict[str, Any] = {}

        async def _seed() -> None:
            gen_holder["gen"] = async_gen_factory()

        self.run(_seed())

        async def _pull(gen: Any) -> Any:
            try:
                return await gen.__anext__(), False
            except StopAsyncIteration:
                return None, True

        gen = gen_holder["gen"]
        while True:
            value, done = self.run(
                _pull(gen), timeout=timeout_per_item or _HTTP_LONG_TIMEOUT.read
                if isinstance(_HTTP_LONG_TIMEOUT.read, (int, float))
                else None
            )
            if done:
                return
            yield value


_BG_LOOP: _BackgroundLoop | None = None
_BG_LOOP_LOCK = threading.Lock()


def _get_bg_loop() -> _BackgroundLoop:
    global _BG_LOOP
    with _BG_LOOP_LOCK:
        if _BG_LOOP is None:
            _BG_LOOP = _BackgroundLoop()
        return _BG_LOOP


class SyncPdfSupervisorClient:
    """Sync wrapper over :class:`PdfSupervisorClient` for QThread callers.

    Each method runs the underlying coroutine on a shared background asyncio
    loop and blocks the worker thread until it returns. Streaming operations
    (``load_stream`` / ``delete_text_layers_stream``) yield sync iterators.

    Constructed once per PySide PDF session manager and held for the app
    lifetime; the underlying ``httpx.AsyncClient`` is created lazily on first
    use and closed via :meth:`close`.
    """

    def __init__(
        self, *, base_url: str, session_token: str, instance_id: str | None = None
    ) -> None:
        self._async = PdfSupervisorClient(
            base_url=base_url, session_token=session_token, instance_id=instance_id
        )
        self._entered = False

    def _ensure_entered(self) -> PdfSupervisorClient:
        if not self._entered:
            # Enter the async context manager once on the background loop so the
            # httpx transport lives there. Subsequent calls reuse it.
            _get_bg_loop().run(self._async.__aenter__())
            self._entered = True
        return self._async

    @property
    def base_url(self) -> str:
        return self._async.base_url

    def close(self) -> None:
        if self._entered:
            try:
                _get_bg_loop().run(self._async.__aexit__(None, None, None))
            finally:
                self._entered = False

    # The wrapper methods delegate by building the coro and driving it on the
    # background loop. Each keeps the same signature/return type as the legacy
    # PdfBackendClient so PySide workers are unchanged.

    def start(self) -> None:
        self._ensure_entered()

    def health(self) -> HealthResponse:
        return _get_bg_loop().run(self._ensure_entered().health())

    def open_session(self, path: str) -> OpenResponse:
        return _get_bg_loop().run(self._ensure_entered().open_session(path))

    def close_session(self, sid: str) -> None:
        _get_bg_loop().run(self._ensure_entered().close_session(sid))

    def get_model(self, sid: str) -> PdfDocumentMirror:
        return _get_bg_loop().run(self._ensure_entered().get_model(sid))

    def load_stream(self, sid: str) -> Iterator[ProgressEvent]:
        client = self._ensure_entered()

        return _get_bg_loop().iterate_stream(lambda: client.load_stream(sid))

    def render_thumbnail(self, sid: str, page: int, size: int = 160) -> bytes:
        return _get_bg_loop().run(
            self._ensure_entered().render_thumbnail(sid, page, size=size)
        )

    def render_preview(self, sid: str, page: int, dpi: int = 150) -> bytes:
        return _get_bg_loop().run(
            self._ensure_entered().render_preview(sid, page, dpi=dpi)
        )

    def detect_text_layers(self, sid: str, page: int) -> DetectTextLayersResponse:
        return _get_bg_loop().run(
            self._ensure_entered().detect_text_layers(sid, page)
        )

    def rotate(self, sid: str, pages: list[int], angle: int) -> MutateResponse:
        return _get_bg_loop().run(
            self._ensure_entered().rotate(sid, pages, angle)
        )

    def delete_pages(self, sid: str, pages: list[int]) -> MutateResponse:
        return _get_bg_loop().run(
            self._ensure_entered().delete_pages(sid, pages)
        )

    def insert_blank(
        self,
        sid: str,
        after_index: int,
        width: float = 612.0,
        height: float = 792.0,
    ) -> MutateResponse:
        return _get_bg_loop().run(
            self._ensure_entered().insert_blank(sid, after_index, width, height)
        )

    def insert_from(
        self, sid: str, source_path: str, after_index: int
    ) -> MutateResponse:
        return _get_bg_loop().run(
            self._ensure_entered().insert_from(sid, source_path, after_index)
        )

    def move_page(
        self, sid: str, from_index: int, to_index: int
    ) -> MutateResponse:
        return _get_bg_loop().run(
            self._ensure_entered().move_page(sid, from_index, to_index)
        )

    def reorder(self, sid: str, new_order: list[int]) -> MutateResponse:
        return _get_bg_loop().run(self._ensure_entered().reorder(sid, new_order))

    def add_text_layer(
        self,
        sid: str,
        page: int,
        ocr_result: dict[str, Any],
        pdf_settings: dict[str, Any] | None = None,
        overwrite: bool = False,
    ) -> MutateResponse:
        return _get_bg_loop().run(
            self._ensure_entered().add_text_layer(
                sid, page, ocr_result, pdf_settings, overwrite
            )
        )

    def add_text_layer_batch(
        self,
        sid: str,
        pages_data: list[dict[str, Any]],
        pdf_settings: dict[str, Any] | None = None,
        overwrite: bool = False,
        save: bool = False,
    ) -> MutateResponse:
        return _get_bg_loop().run(
            self._ensure_entered().add_text_layer_batch(
                sid, pages_data, pdf_settings, overwrite, save
            )
        )

    def rewrite_text_layer(
        self,
        sid: str,
        page: int,
        text_blocks: list[Any],
        preproc_angle: int = 0,
        pdf_settings: dict[str, Any] | None = None,
    ) -> MutateResponse:
        return _get_bg_loop().run(
            self._ensure_entered().rewrite_text_layer(
                sid, page, text_blocks, preproc_angle, pdf_settings
            )
        )

    def update_block_text(
        self, sid: str, page: int, block_index: int, new_text: str
    ) -> MutateResponse:
        return _get_bg_loop().run(
            self._ensure_entered().update_block_text(sid, page, block_index, new_text)
        )

    def delete_text_layers_stream(
        self, sid: str, pages: list[int]
    ) -> Iterator[ProgressEvent]:
        client = self._ensure_entered()

        return _get_bg_loop().iterate_stream(
            lambda: client.delete_text_layers_stream(sid, pages)
        )

    def save(
        self,
        sid: str,
        path: str | None = None,
        pdf_settings: dict[str, Any] | None = None,
        *,
        rewrite_text_layers: bool = True,
    ) -> SaveResponse:
        return _get_bg_loop().run(
            self._ensure_entered().save(
                sid, path, pdf_settings, rewrite_text_layers=rewrite_text_layers
            )
        )

    def cancel(self, sid: str) -> None:
        _get_bg_loop().run(self._ensure_entered().cancel(sid))

    def reset_cancel(self, sid: str) -> None:
        _get_bg_loop().run(self._ensure_entered().reset_cancel(sid))


__all__ = [
    "PdfBackendError",
    "PdfSupervisorClient",
    "SyncPdfSupervisorClient",
]
