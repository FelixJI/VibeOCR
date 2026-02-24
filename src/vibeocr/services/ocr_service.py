"""OCR Service using PaddleX"""

import logging
import os
import threading
from typing import Any, Optional

# Disable OneDNN and force CPU mode for compatibility
os.environ.setdefault("FLAGS_enable_onednn_backend", "0")
os.environ.setdefault("FLAGS_use_mkldnn", "0")

import numpy as np
from PIL import Image
from paddlex import create_pipeline

_logger = logging.getLogger(__name__)


class OCRService:
    """OCR recognition service (thread-safe singleton)"""

    _instance: Optional["OCRService"] = None
    _pipeline: Any = None
    _device: Optional[str] = None
    _lock = threading.Lock()

    def __new__(cls) -> "OCRService":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:  # Double-check
                    cls._instance = super().__new__(cls)
        return cls._instance

    def _create_pipeline(self, device: str) -> Any:
        """Create OCR pipeline"""
        pipeline = create_pipeline(
            pipeline="OCR",
            device=device,
        )
        _logger.info("OCR pipeline initialized on device: %s", device)
        return pipeline

    def _is_gpu_error(self, error: Exception) -> bool:
        """Check if error is GPU-related"""
        err_str = str(error).lower()
        return any(keyword in err_str for keyword in ["cudnn", "cuda", "gpu", "cudart"])

    @property
    def pipeline(self) -> Any:
        """Lazy-load OCR pipeline (thread-safe, auto fallback to CPU if GPU unavailable)"""
        if self._pipeline is None:
            with self._lock:
                if self._pipeline is None:  # Double-check
                    # Try GPU first, fallback to CPU on failure
                    for device in ["gpu:0", "cpu"]:
                        try:
                            self._pipeline = self._create_pipeline(device)
                            self._device = device
                            break
                        except RuntimeError as e:
                            if self._is_gpu_error(e) and "gpu" in device.lower():
                                _logger.warning(
                                    "GPU not available, falling back to CPU: %s", e
                                )
                                continue
                            raise
                    else:
                        raise RuntimeError(
                            "Failed to initialize OCR pipeline on any device"
                        )
        return self._pipeline

    def _reset_pipeline_to_cpu(self) -> None:
        """Reset pipeline to CPU mode"""
        with self._lock:
            _logger.warning("Resetting pipeline to CPU mode due to GPU error")
            self._pipeline = self._create_pipeline("cpu")
            self._device = "cpu"

    def recognize(self, image: Image.Image | np.ndarray | str) -> str:
        """
        Perform OCR recognition on image

        Args:
            image: PIL Image, numpy array, or image path

        Returns:
            Recognized text content
        """

        def _do_recognize(img: Image.Image | np.ndarray | str) -> str:
            """Execute OCR recognition and return text"""
            output = self.pipeline.predict(
                input=img,
                use_doc_orientation_classify=True,
                use_doc_unwarping=True,
                use_textline_orientation=True,
            )

            texts = []
            for res in output:
                if hasattr(res, "rec_texts"):
                    texts.extend(res.rec_texts)
                elif hasattr(res, "ocr_text"):
                    texts.append(res.ocr_text)
                elif isinstance(res, dict):
                    rec_texts = res.get("rec_texts", [])
                    texts.extend(rec_texts)

            return "\n".join(texts) if texts else ""

        try:
            return _do_recognize(image)
        except RuntimeError as e:
            # If GPU error and not already in CPU mode, fallback to CPU
            if self._is_gpu_error(e) and self._device != "cpu":
                _logger.warning("GPU error during predict, falling back to CPU: %s", e)
                self._reset_pipeline_to_cpu()
                return _do_recognize(image)
            raise
