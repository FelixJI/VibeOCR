"""Backend service package initialization.

Install the OCRWorkerProcess runtime-state shim before WorkerManager imports the
class.  The shim is stdlib-only at import time and loads MinerU support lazily
when TTL configuration is first applied.
"""

from vibeocr.services.ocr_worker_process import OCRWorkerProcess
from vibeocr.services.worker_runtime_state import (
    install_ocr_worker_runtime_state_patch,
)

install_ocr_worker_runtime_state_patch(OCRWorkerProcess)

__all__ = []
