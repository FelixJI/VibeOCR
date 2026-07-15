"""Frontend adapter that maps the legacy batch worker shape to BackendClient."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from vibeocr.worker_host.sync_client import SyncBackendClient


class BatchBackendAdapter:
    """Expose ``recognize_batch/batch_cancel`` without importing services."""

    def __init__(self, client: SyncBackendClient) -> None:
        self._client = client

    def recognize_batch(self, images: list[bytes], options: Any) -> list[Any]:
        pipeline = getattr(options, "pipeline", "OCR")
        pipeline_name = str(getattr(pipeline, "value", pipeline))
        language = getattr(options, "language", None)
        return self._client.recognize_batch_sync(
            images,
            pipeline=pipeline_name,
            language=language,
        )

    def recognize(self, image: bytes, options: Any = None) -> Any:
        pipeline = getattr(options, "pipeline", "OCR")
        pipeline_name = str(getattr(pipeline, "value", pipeline))
        language = getattr(options, "language", None)
        return self._client.recognize_sync(
            image, pipeline=pipeline_name, language=language
        )

    @staticmethod
    def _warmup_png() -> bytes:
        import io

        from PIL import Image

        stream = io.BytesIO()
        Image.new("RGB", (32, 32), "white").save(stream, format="PNG")
        return stream.getvalue()

    def preload_pipeline(self, pipeline: Any) -> bool:
        try:
            self._client.recognize_sync(
                self._warmup_png(),
                pipeline=str(getattr(pipeline, "value", pipeline)),
            )
            return True
        except Exception:
            return False

    def preload_pipelines(self, pipelines: list[str]) -> dict[str, bool]:
        return {name: self.preload_pipeline(name) for name in pipelines}

    def warmup_pipelines(self, pipelines: list[str]) -> dict[str, bool]:
        return {name: self.preload_pipeline(name) for name in pipelines}

    def release_pipelines(self, heavy_only: bool = True) -> list[str]:
        # WorkerHost owns model lifetime; it will enforce its configured TTL.
        return []

    def set_pipeline_ttl(self, ttl_seconds: int) -> bool:
        # ConfigManager persists this value; a new WorkerHost snapshot consumes it.
        return ttl_seconds >= 0

    def set_task_queued_callback(self, callback: Any) -> None:
        del callback

    def set_cancel_event(self, event: Any) -> None:
        del event

    def shutdown(self) -> None:
        """The process-level BackendSession owns shutdown."""

    def batch_cancel(self) -> None:
        self._client.cancel_active()


__all__ = ["BatchBackendAdapter"]
