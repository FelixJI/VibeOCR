"""Frontend adapter that maps the legacy batch worker shape to BackendClient.

适配的 client 可以是 ``SyncBackendClient``（SHM）或 ``OcrHttpClient``（HTTP）——
二者 ``*_sync`` 方法签名一致（duck typing），由 ``get_backend_client`` 按
``VIBEOCR_OCR_TRANSPORT`` 选择，本适配器无感知。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from vibeocr.pipeline_status import mark_pipeline_success
from vibeocr.utils.mime_types import guess_mime_from_bytes

if TYPE_CHECKING:
    from vibeocr.worker_host.ocr_http_client import OcrHttpClient
    from vibeocr.worker_host.sync_client import SyncBackendClient

# MinerU 走自己的服务（mineru-api），不进 OCR worker。其注册表 spec 按设计
# 会抛出 NotImplementedError("MinerU uses its own service")，因此调用方必须在此
# 上层分流，直接调主进程 MinerUService。
_MINERU_PIPELINE_NAMES = frozenset({"MinerU", "MinerU-DOC"})


class BatchBackendAdapter:
    """Expose ``recognize_batch/batch_cancel`` without importing services."""

    def __init__(self, client: SyncBackendClient | OcrHttpClient) -> None:
        self._client = client

    def recognize_batch(self, images: list[bytes], options: Any) -> list[Any]:
        pipeline = getattr(options, "pipeline", "OCR")
        pipeline_name = str(getattr(pipeline, "value", pipeline))
        if pipeline_name in _MINERU_PIPELINE_NAMES:
            return [self._recognize_one_mineru(img, options) for img in images]
        language = getattr(options, "language", None)
        options_dict = options.to_dict() if hasattr(options, "to_dict") else {}
        kwargs: dict[str, Any] = {"pipeline": pipeline_name, "language": language}
        if options_dict:
            kwargs["options"] = options_dict
        return self._client.recognize_batch_sync(images, **kwargs)

    def recognize(self, image: bytes, options: Any = None) -> Any:
        pipeline = getattr(options, "pipeline", "OCR")
        pipeline_name = str(getattr(pipeline, "value", pipeline))
        if pipeline_name in _MINERU_PIPELINE_NAMES:
            return self._recognize_one_mineru(image, options)
        language = getattr(options, "language", None)
        options_dict = options.to_dict() if hasattr(options, "to_dict") else {}
        kwargs: dict[str, Any] = {"pipeline": pipeline_name, "language": language}
        if options_dict:
            kwargs["options"] = options_dict
        return self._client.recognize_sync(image, **kwargs)

    def _recognize_one_mineru(self, image: bytes, options: Any) -> Any:
        """按张调用主进程 MinerUService 解析文档，失败返回 None 不中断整批。"""
        from vibeocr.env_manager import get_project_root
        from vibeocr.services.mineru_service import MinerUService

        mime_type = guess_mime_from_bytes(image)
        try:
            result = MinerUService().parse(image, mime_type, options)
        except Exception:
            return None
        try:
            mark_pipeline_success("MinerU", get_project_root())
        except Exception:
            pass
        return result

    def preload_pipeline(self, pipeline: Any) -> bool:
        pipeline_name = str(getattr(pipeline, "value", pipeline))
        return bool(self.preload_pipelines([pipeline_name]).get(pipeline_name, False))

    def preload_pipelines(self, pipelines: list[str]) -> dict[str, bool]:
        return self._client.preload_pipeline_cache_sync(pipelines)

    def warmup_pipelines(self, pipelines: list[str]) -> dict[str, bool]:
        return self._client.warmup_pipeline_cache_sync(pipelines)

    def release_pipelines(self, heavy_only: bool = True) -> list[str]:
        return self._client.release_pipeline_cache_sync(heavy_only=heavy_only)

    def set_pipeline_ttls(self, pipeline_ttls: dict[str, int]) -> bool:
        return self._client.set_pipeline_cache_ttl_sync(pipeline_ttls)

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
