"""Backend service package initialization.

Install two lifecycle guards before WorkerManager and the OCR worker consume
their classes:

- restart-safe TTL/residency tracking for ``OCRWorkerProcess``;
- removal of BatchQueueManager's redundant strong model reference in the
  production service-backed path, so deleting a cache entry can actually free
  the underlying Paddle object.
"""

import functools
from typing import Any

from vibeocr.services.ocr_worker_process import OCRWorkerProcess
from vibeocr.services.worker_runtime_state import (
    install_ocr_worker_runtime_state_patch,
)
from vibeocr.workers.batch_queue_manager import BatchQueueManager

install_ocr_worker_runtime_state_patch(OCRWorkerProcess)

_BATCH_PATCH_MARKER = "_vibeocr_nonretaining_pipeline_patch_v1"
if not getattr(BatchQueueManager, _BATCH_PATCH_MARKER, False):
    _original_batch_init = BatchQueueManager.__init__

    @functools.wraps(_original_batch_init)
    def _batch_init(self: Any, *args: Any, **kwargs: Any) -> None:
        _original_batch_init(self, *args, **kwargs)
        if getattr(self, "service", None) is not None:
            # Production inference resolves the current model through
            # ``service``/the registry on every batch.  Keeping the constructor
            # model here would outlive PipelineCacheManager._pipelines.pop() and
            # make TTL/FIFO release only cosmetic.
            self.pipeline = None

    BatchQueueManager.__init__ = _batch_init
    setattr(BatchQueueManager, _BATCH_PATCH_MARKER, True)

__all__ = []
