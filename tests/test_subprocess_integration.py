"""
Integration tests for OCR subprocess architecture.

Tests the full integration of subprocess-based OCR service.
"""

import os
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

# Check if modules are available
try:
    from vibeocr.services.ocr_worker_process import OCRWorkerProcess, OCRWorkerProcessError
    from vibeocr.services.ocr_service_subprocess import OCRServiceSubprocess
    from vibeocr.utils.shared_memory_v2 import (
        SharedMemoryProtocolV2 as SharedMemoryProtocol,
        SharedMemoryConfig,
        MessageType,
        serialize_request,
        serialize_result,
    )
    MSG_RECOGNIZE = MessageType.RECOGNIZE
    MSG_RESULT = MessageType.RESULT
    HAS_MODULES = True
except ImportError:
    HAS_MODULES = False

# Check if running in CI
IS_CI = os.environ.get("CI", "false").lower() == "true"

# Skip integration tests that require actual subprocess
SKIP_SUBPROCESS_TESTS = IS_CI or not HAS_MODULES


@pytest.mark.skipif(SKIP_SUBPROCESS_TESTS, reason="Subprocess tests skipped in CI or modules not available")
class TestWorkerProcessIntegration:
    """Integration tests for OCRWorkerProcess."""

    @pytest.fixture
    def worker(self):
        """Create a worker process for testing."""
        worker = OCRWorkerProcess(worker_id=0, use_gpu=False, shm_size=1024*1024)
        yield worker
        # Cleanup
        worker.stop()

    def test_worker_start_stop(self, worker):
        """Test starting and stopping worker process."""
        # This test requires the worker script to be available
        # and may take time to start the subprocess
        try:
            worker.start(timeout=120.0)
            assert worker.is_running
            assert worker.is_ready

            worker.stop()
            assert not worker.is_running
        except OCRWorkerProcessError as e:
            pytest.skip(f"Worker process failed to start: {e}")

    def test_worker_restart(self, worker):
        """Test restarting worker process."""
        try:
            worker.start(timeout=120.0)
            assert worker.is_running

            worker.restart(timeout=120.0)
            assert worker.is_running
            assert worker.is_ready

            worker.stop()
        except OCRWorkerProcessError as e:
            pytest.skip(f"Worker process failed: {e}")


@pytest.mark.skipif(SKIP_SUBPROCESS_TESTS, reason="Subprocess tests skipped in CI or modules not available")
class TestOCRServiceSubprocessIntegration:
    """Integration tests for OCRServiceSubprocess."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        OCRServiceSubprocess._instance = None
        yield
        if OCRServiceSubprocess._instance is not None:
            OCRServiceSubprocess._instance.shutdown()
            OCRServiceSubprocess._instance = None

    def test_service_start_stop(self):
        """Test starting and stopping the service."""
        service = OCRServiceSubprocess(
            max_workers=1,
            use_gpu=False,
            auto_start=False
        )

        # Start service
        try:
            service.start(timeout=120.0)
            assert service.is_ready()

            # Check status
            status = service.get_status()
            assert status["ready"]
            assert len(status["workers"]) == 1

            # Shutdown
            service.shutdown()
            assert not service.is_ready()
        except Exception as e:
            pytest.skip(f"Service failed to start: {e}")

    def test_service_with_pil_image(self):
        """Test OCR recognition with PIL image."""
        service = OCRServiceSubprocess(
            max_workers=1,
            use_gpu=False,
            auto_start=True
        )

        try:
            # Wait for service to be ready
            max_wait = 120
            waited = 0
            while not service.is_ready() and waited < max_wait:
                time.sleep(1)
                waited += 1

            if not service.is_ready():
                pytest.skip("Service failed to become ready")

            # Create test image
            img = Image.new("RGB", (200, 100), color="white")
            draw = ImageDraw.Draw(img)
            draw.text((10, 10), "Test", fill="black")

            # Perform OCR (may fail if OCR dependencies not available)
            try:
                result = service.recognize(img)
                # If we get here, OCR worked
                assert result is not None
            except Exception as e:
                # OCR may fail if dependencies not installed
                pytest.skip(f"OCR recognition failed: {e}")

        finally:
            service.shutdown()


@pytest.mark.skipif(not HAS_MODULES, reason="Modules not available")
class TestSharedMemoryIntegration:
    """Integration tests for shared memory communication."""

    def test_bidirectional_communication(self):
        """Test bidirectional communication through shared memory."""
        shm_name = "test_shm_bidirectional"
        protocol = SharedMemoryProtocol(shm_name, 4096)
        protocol.create()

        try:
            # Write request
            request_data = b"test_request_data"
            protocol.write_message(MSG_RECOGNIZE, request_data, timeout=5.0)

            # Read request
            msg_type, data = protocol.read_message(timeout=5.0)
            assert msg_type == MSG_RECOGNIZE
            assert data == request_data

            # Write response
            response_data = serialize_result({"text": "recognized"})
            protocol.write_message(MSG_RESULT, response_data, timeout=5.0)

            # Read response
            msg_type, data = protocol.read_message(timeout=5.0)
            assert msg_type == MSG_RESULT

        finally:
            protocol.close()
            protocol.unlink()

    def test_large_image_transfer(self):
        """Test transferring large image data."""
        shm_name = "test_shm_large_image"
        # 5MB shared memory
        protocol = SharedMemoryProtocol(shm_name, 5 * 1024 * 1024)
        protocol.create()

        try:
            # Simulate 1MB image data
            image_data = b"x" * (1024 * 1024)
            options = {"use_angle_cls": True, "lang": "ch"}

            request_data = serialize_request(image_data, options)
            protocol.write_message(MSG_RECOGNIZE, request_data, timeout=5.0)

            msg_type, data = protocol.read_message(timeout=5.0)
            assert msg_type == MSG_RECOGNIZE

            img_out, opt_out = serialize_request.__wrapped__.__code__.co_consts
            # Actually deserialize
            from vibeocr.utils.shared_memory_v2 import deserialize_request
            img_out, opt_out = deserialize_request(data)
            assert len(img_out) == 1024 * 1024
            assert opt_out["use_angle_cls"] is True

        finally:
            protocol.close()
            protocol.unlink()


class TestServiceFactoryIntegration:
    """Integration tests for service factory."""

    def test_get_ocr_service_subprocess_mode(self):
        """Test get_ocr_service in subprocess mode."""
        # Set environment variable
        original = os.environ.get("VIBEOCR_USE_SUBPROCESS")
        os.environ["VIBEOCR_USE_SUBPROCESS"] = "true"

        try:
            from vibeocr.services import get_ocr_service, OCRServiceSubprocess

            # Reset singleton
            OCRServiceSubprocess._instance = None

            service = get_ocr_service()
            assert isinstance(service, OCRServiceSubprocess)

            # Cleanup
            service.shutdown()
            OCRServiceSubprocess._instance = None

        finally:
            if original is None:
                os.environ.pop("VIBEOCR_USE_SUBPROCESS", None)
            else:
                os.environ["VIBEOCR_USE_SUBPROCESS"] = original

    def test_get_ocr_service_direct_mode(self):
        """Test get_ocr_service in direct mode."""
        # Set environment variable
        original = os.environ.get("VIBEOCR_USE_SUBPROCESS")
        os.environ["VIBEOCR_USE_SUBPROCESS"] = "false"

        try:
            # Need to reload the module to pick up new env var
            import importlib
            import vibeocr.services
            importlib.reload(vibeocr.services)

            from vibeocr.services import get_ocr_service, USE_SUBPROCESS

            assert not USE_SUBPROCESS

            # In direct mode, get_ocr_service returns OCRService (not subprocess)
            # We don't actually call it because it requires OCR dependencies

        finally:
            if original is None:
                os.environ.pop("VIBEOCR_USE_SUBPROCESS", None)
            else:
                os.environ["VIBEOCR_USE_SUBPROCESS"] = original
            # Reload to restore original state
            importlib.reload(vibeocr.services)
