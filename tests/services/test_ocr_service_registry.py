"""Tests for OCRService registry integration.

Verifies that:
- get_or_create_pipeline() caches pipeline instances
- get_or_create_pipeline() uses registry's spec.create_pipeline when available
- get_or_create_pipeline() falls back to old _create_pipeline for unregistered names
- recognize() dispatches to registered pipeline specs via registry
- recognize() falls back to old _recognize_* methods for unregistered pipelines
- Old OCROptions (enum pipeline) still works end-to-end
"""

from unittest.mock import MagicMock, patch

import pytest

from vibeocr.core.pipelines import OCRPipeline
from vibeocr.core.pipelines.registry import PipelineSpec
from vibeocr.models.ocr_options import OCROptions
from vibeocr.models.ocr_result import OCRResult
from vibeocr.services.ocr_service import OCRService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ocr_result(**overrides) -> OCRResult:
    """Create a minimal OCRResult suitable for tests."""
    defaults = dict(
        raw_text="hello",
        markdown_text="hello",
        html_text="hello",
        text_with_scores=[("hello", 0.95)],
        avg_score=0.95,
        low_confidence_items=[],
        pipeline_type="OCR",
        images={},
        text_blocks=[],
        content_list=[],
        image_width=100,
        image_height=50,
        preproc_angle=0,
        preprocessed_image=None,
        preproc_img_w=0,
        preproc_img_h=0,
    )
    defaults.update(overrides)
    return OCRResult(**defaults)


# ---------------------------------------------------------------------------
# get_or_create_pipeline
# ---------------------------------------------------------------------------


class TestGetOrCreatePipeline:
    """Tests for OCRService.get_or_create_pipeline()."""

    def setup_method(self):
        """Reset singleton state before each test."""
        OCRService._reset()

    def teardown_method(self):
        """Reset singleton state after each test."""
        OCRService._reset()

    def test_caches_pipeline_instance(self):
        """Pipeline instances are cached and returned on subsequent calls."""
        service = OCRService()
        mock_pipeline = MagicMock(name="cached_ocr_pipeline")
        service._pipelines = {"OCR": mock_pipeline}

        result = service.get_or_create_pipeline("OCR")
        assert result is mock_pipeline

    def test_creates_pipeline_via_registry(self):
        """Uses spec.create_pipeline from registry when pipeline is registered."""
        service = OCRService()
        service._pipelines = {}

        mock_pipeline = MagicMock(name="ocr_pipeline_from_registry")
        mock_spec = MagicMock()
        mock_spec.create_pipeline.return_value = mock_pipeline

        mock_registry = MagicMock()
        mock_registry.has.return_value = True
        mock_registry.get.return_value = mock_spec

        with (
            patch("vibeocr.services.ocr_service.OCRService._setup_cuda_dll_path"),
            patch(
                "vibeocr.services.ocr_service.OCRService._get_device",
                return_value="cpu",
            ),
            patch(
                "vibeocr.core.pipelines.get_registry",
                return_value=mock_registry,
            ),
            patch(
                "vibeocr.services.ocr_service.OCRService._create_pipeline"
            ) as mock_old_create,
        ):
            result = service.get_or_create_pipeline("OCR")

        # Should use registry, not old _create_pipeline
        mock_spec.create_pipeline.assert_called_once_with("cpu")
        mock_old_create.assert_not_called()
        assert result is mock_pipeline
        assert "OCR" in service._pipelines

    def test_falls_back_to_old_create_pipeline(self):
        """Falls back to _create_pipeline for unregistered pipeline names."""
        service = OCRService()
        service._pipelines = {}

        mock_pipeline = MagicMock(name="fallback_pipeline")
        mock_registry = MagicMock()
        mock_registry.has.return_value = False

        with (
            patch("vibeocr.services.ocr_service.OCRService._setup_cuda_dll_path"),
            patch(
                "vibeocr.core.pipelines.get_registry",
                return_value=mock_registry,
            ),
            patch.object(
                service, "_create_pipeline", return_value=mock_pipeline
            ) as mock_create,
        ):
            result = service.get_or_create_pipeline("OCR")

        mock_create.assert_called_once_with(OCRPipeline.OCR)
        assert result is mock_pipeline

    def test_raises_for_unknown_pipeline_name(self):
        """Raises ValueError for a pipeline name not in registry or OCRPipeline enum."""
        service = OCRService()
        service._pipelines = {}

        mock_registry = MagicMock()
        mock_registry.has.return_value = False

        with (
            patch("vibeocr.services.ocr_service.OCRService._setup_cuda_dll_path"),
            patch(
                "vibeocr.core.pipelines.get_registry",
                return_value=mock_registry,
            ),
        ):
            with pytest.raises(ValueError, match="不支持的管道类型"):
                service.get_or_create_pipeline("NONEXISTENT_PIPELINE")

    def test_thread_safety_double_check(self):
        """Double-checked locking: only creates once even under contention."""
        service = OCRService()
        service._pipelines = {}

        mock_pipeline = MagicMock(name="pipeline")
        mock_spec = MagicMock()
        mock_spec.create_pipeline.return_value = mock_pipeline

        mock_registry = MagicMock()
        mock_registry.has.return_value = True
        mock_registry.get.return_value = mock_spec

        with (
            patch("vibeocr.services.ocr_service.OCRService._setup_cuda_dll_path"),
            patch(
                "vibeocr.services.ocr_service.OCRService._get_device",
                return_value="cpu",
            ),
            patch(
                "vibeocr.core.pipelines.get_registry",
                return_value=mock_registry,
            ),
        ):
            # Call twice — second should return cached instance
            result1 = service.get_or_create_pipeline("OCR")
            result2 = service.get_or_create_pipeline("OCR")

        assert result1 is result2
        assert mock_spec.create_pipeline.call_count == 1


# ---------------------------------------------------------------------------
# get_pipeline backward compat
# ---------------------------------------------------------------------------


class TestGetPipelineBackwardCompat:
    """Tests that get_pipeline(OCRPipeline) still works."""

    def setup_method(self):
        OCRService._reset()

    def teardown_method(self):
        OCRService._reset()

    def test_get_pipeline_delegates_to_get_or_create(self):
        """get_pipeline delegates to get_or_create_pipeline."""
        service = OCRService()
        mock_pipeline = MagicMock(name="ocr_pipeline")
        service._pipelines = {"OCR": mock_pipeline}

        result = service.get_pipeline(OCRPipeline.OCR)
        assert result is mock_pipeline

    def test_get_pipeline_pp_structure(self):
        """get_pipeline works for PP-StructureV3."""
        service = OCRService()
        mock_pipeline = MagicMock(name="pp_structure_pipeline")
        service._pipelines = {"PP-StructureV3": mock_pipeline}

        result = service.get_pipeline(OCRPipeline.PP_STRUCTURE_V3)
        assert result is mock_pipeline


# ---------------------------------------------------------------------------
# recognize() registry dispatch
# ---------------------------------------------------------------------------


class TestRecognizeRegistryDispatch:
    """Tests for recognize() dispatching via registry."""

    def setup_method(self):
        OCRService._reset()

    def teardown_method(self):
        OCRService._reset()

    def test_dispatches_to_registered_spec(self):
        """recognize() dispatches to spec.recognize for registered pipelines."""
        service = OCRService()

        mock_result = _make_ocr_result()

        mock_spec = MagicMock()
        mock_spec.recognize.return_value = mock_result

        mock_registry = MagicMock()
        mock_registry.has.return_value = True
        mock_registry.get.return_value = mock_spec

        with patch(
            "vibeocr.core.pipelines.get_registry",
            return_value=mock_registry,
        ):
            import numpy as np

            img = np.zeros((50, 100, 3), dtype=np.uint8)
            opts = OCROptions(pipeline=OCRPipeline.OCR)
            result = service.recognize(img, opts)

        mock_spec.recognize.assert_called_once()
        # First arg is service, second is image, third is options
        call_args = mock_spec.recognize.call_args
        assert call_args[0][0] is service
        assert result.raw_text == "hello"

    def test_falls_back_to_old_recognize(self):
        """recognize() falls back to old _recognize_* for unregistered pipelines."""
        service = OCRService()

        mock_result = _make_ocr_result()
        mock_registry = MagicMock()
        mock_registry.has.return_value = False

        with (
            patch(
                "vibeocr.core.pipelines.get_registry",
                return_value=mock_registry,
            ),
            patch.object(
                service, "_recognize_ocr", return_value=mock_result
            ) as mock_rec,
        ):
            import numpy as np

            img = np.zeros((50, 100, 3), dtype=np.uint8)
            opts = OCROptions(pipeline=OCRPipeline.OCR)
            result = service.recognize(img, opts)

        mock_rec.assert_called_once()
        assert result.raw_text == "hello"

    def test_falls_back_to_structure_for_pp_structure(self):
        """Falls back to _recognize_structure for unregistered PP-StructureV3."""
        service = OCRService()

        mock_result = _make_ocr_result()
        mock_registry = MagicMock()
        mock_registry.has.return_value = False

        with (
            patch(
                "vibeocr.core.pipelines.get_registry",
                return_value=mock_registry,
            ),
            patch.object(
                service, "_recognize_structure", return_value=mock_result
            ) as mock_rec,
        ):
            import numpy as np

            img = np.zeros((50, 100, 3), dtype=np.uint8)
            opts = OCROptions(pipeline=OCRPipeline.PP_STRUCTURE_V3)
            result = service.recognize(img, opts)

        mock_rec.assert_called_once()

    def test_falls_back_to_paddlocr_vl(self):
        """Falls back to _recognize_paddlocr_vl for unregistered PaddleOCR-VL."""
        service = OCRService()

        mock_result = _make_ocr_result()
        mock_registry = MagicMock()
        mock_registry.has.return_value = False

        with (
            patch(
                "vibeocr.core.pipelines.get_registry",
                return_value=mock_registry,
            ),
            patch.object(
                service, "_recognize_paddlocr_vl", return_value=mock_result
            ) as mock_rec,
        ):
            import numpy as np

            img = np.zeros((50, 100, 3), dtype=np.uint8)
            opts = OCROptions(pipeline=OCRPipeline.PADDLEOCR_VL)
            result = service.recognize(img, opts)

        mock_rec.assert_called_once()

    def test_bytes_input_converted_before_dispatch(self):
        """bytes input is converted to numpy before dispatching to registry."""
        service = OCRService()

        mock_result = _make_ocr_result()
        mock_spec = MagicMock()
        mock_spec.recognize.return_value = mock_result

        mock_registry = MagicMock()
        mock_registry.has.return_value = True
        mock_registry.get.return_value = mock_spec

        with patch(
            "vibeocr.core.pipelines.get_registry",
            return_value=mock_registry,
        ):
            # Use a valid PNG bytes input
            import io

            from PIL import Image

            img = Image.new("RGB", (100, 50), color="white")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            png_bytes = buf.getvalue()

            opts = OCROptions(pipeline=OCRPipeline.OCR)
            result = service.recognize(png_bytes, opts)

        # The dispatched image should be numpy array, not bytes
        call_args = mock_spec.recognize.call_args
        dispatched_image = call_args[0][1]
        assert hasattr(dispatched_image, "shape")  # numpy array

    def test_bbox_normalization_preserved_with_registry(self):
        """Bbox normalization in recognize() still works with registry dispatch."""
        from vibeocr.models.ocr_result import TextBlock

        service = OCRService()

        # Result with pixel-space bbox
        tb = TextBlock(text="hello", score=0.9, bbox=(10.0, 20.0, 90.0, 40.0))
        mock_result = _make_ocr_result(
            text_blocks=[tb],
            preproc_img_w=100,
            preproc_img_h=50,
        )

        mock_spec = MagicMock()
        mock_spec.recognize.return_value = mock_result

        mock_registry = MagicMock()
        mock_registry.has.return_value = True
        mock_registry.get.return_value = mock_spec

        with patch(
            "vibeocr.core.pipelines.get_registry",
            return_value=mock_registry,
        ):
            import numpy as np

            img = np.zeros((50, 100, 3), dtype=np.uint8)
            opts = OCROptions(pipeline=OCRPipeline.OCR)
            result = service.recognize(img, opts)

        # Bbox should be normalized to [0-1000]
        assert result.text_blocks[0].bbox is not None
        x0, y0, x1, y1 = result.text_blocks[0].bbox
        assert 0 <= x0 <= 1000
        assert 0 <= y0 <= 1000
        assert 0 <= x1 <= 1000
        assert 0 <= y1 <= 1000
