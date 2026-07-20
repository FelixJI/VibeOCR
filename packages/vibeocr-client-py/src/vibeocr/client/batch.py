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
        options_dict = options.to_dict() if hasattr(options, "to_dict") else {}
        kwargs: dict[str, Any] = {"pipeline": pipeline_name, "language": language}
        if options_dict:
            kwargs["options"] = options_dict
        return self._client.recognize_batch_sync(images, **kwargs)

    def recognize(self, image: bytes, options: Any = None) -> Any:
        pipeline = getattr(options, "pipeline", "OCR")
        pipeline_name = str(getattr(pipeline, "value", pipeline))
        language = getattr(options, "language", None)
        options_dict = options.to_dict() if hasattr(options, "to_dict") else {}
        kwargs: dict[str, Any] = {"pipeline": pipeline_name, "language": language}
        if options_dict:
            kwargs["options"] = options_dict
        return self._client.recognize_sync(image, **kwargs)

    def preload_pipeline(self, pipeline: Any) -> bool:
        pipeline_name = str(getattr(pipeline, "value", pipeline))
        return bool(self.preload_pipelines([pipeline_name]).get(pipeline_name, False))

    def preload_pipelines(self, pipelines: list[str]) -> dict[str, bool]:
        return self._client.preload_pipeline_cache_sync(pipelines)

    def warmup_pipelines(self, pipelines: list[str]) -> dict[str, bool]:
        return self._client.warmup_pipeline_cache_sync(pipelines)

    def release_pipelines(self, heavy_only: bool = True) -> list[str]:
        return self._client.release_pipeline_cache_sync(heavy_only=heavy_only)

    def set_pipeline_ttl(self, ttl_seconds: int) -> bool:
        return self._client.set_pipeline_cache_ttl_sync(ttl_seconds)

    def get_pipeline_cache_status(self) -> dict[str, Any]:
        return self._client.pipeline_cache_status_sync()

    def set_task_queued_callback(self, callback: Any) -> None:
        del callback

    def set_cancel_event(self, event: Any) -> None:
        del event

    def shutdown(self) -> None:
        """The process-level BackendSession owns shutdown."""

    def batch_cancel(self) -> None:
        self._client.cancel_active()


__all__ = ["BatchBackendAdapter"]
