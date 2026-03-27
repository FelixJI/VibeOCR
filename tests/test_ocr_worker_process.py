"""
Tests for OCRWorkerProcess and OCRServiceSubprocess.

Tests the subprocess-based OCR service implementation.
"""

import threading

import numpy as np
import pytest
from PIL import Image

# Check if modules are available
try:
    from vibeocr.services.ocr_service_subprocess import OCRServiceSubprocess
    from vibeocr.services.ocr_worker_process import (
        OCRWorkerProcess,
    )

    HAS_SUBPROCESS_MODULES = True
except ImportError:
    HAS_SUBPROCESS_MODULES = False


@pytest.mark.skipif(
    not HAS_SUBPROCESS_MODULES, reason="subprocess modules not available"
)
class TestOCRWorkerProcess:
    """Tests for OCRWorkerProcess class."""

    def test_init(self):
        """Test worker process initialization."""
        worker = OCRWorkerProcess(worker_id=0, use_gpu=False, shm_size=1024 * 1024)

        assert worker.worker_id == 0
        assert worker.use_gpu is False
        assert worker.shm_size == 1024 * 1024
        assert worker.process is None
        assert not worker.busy
        assert not worker.is_running
        assert not worker.is_ready

    def test_init_with_gpu(self):
        """Test worker process initialization with GPU enabled."""
        worker = OCRWorkerProcess(worker_id=1, use_gpu=True)

        assert worker.worker_id == 1
        assert worker.use_gpu is True

    def test_is_running_false_initially(self):
        """Test that is_running is False before start."""
        worker = OCRWorkerProcess(worker_id=0, use_gpu=False)
        assert not worker.is_running

    def test_is_ready_false_initially(self):
        """Test that is_ready is False before start."""
        worker = OCRWorkerProcess(worker_id=0, use_gpu=False)
        assert not worker.is_ready

    def test_repr(self):
        """Test string representation."""
        worker = OCRWorkerProcess(worker_id=0, use_gpu=False)
        repr_str = repr(worker)
        assert "OCRWorkerProcess" in repr_str
        assert "id=0" in repr_str
        assert "stopped" in repr_str

    def test_get_python_executable(self):
        """Test getting Python executable path."""
        worker = OCRWorkerProcess(worker_id=0, use_gpu=False)
        python_exe = worker._get_python_executable()

        assert python_exe is not None
        assert isinstance(python_exe, str)
        # Should be the current Python interpreter
        import sys

        assert python_exe == sys.executable


@pytest.mark.skipif(
    not HAS_SUBPROCESS_MODULES, reason="subprocess modules not available"
)
class TestOCRWorkerProcessLifeCycle:
    """Tests for worker process lifecycle (require actual subprocess)."""

    @pytest.fixture(autouse=True)
    def cleanup(self):
        """Cleanup after each test."""
        yield
        # Any cleanup if needed

    def test_stop_without_start(self):
        """Test that stop() works even if process was never started."""
        worker = OCRWorkerProcess(worker_id=0, use_gpu=False)
        # Should not raise
        worker.stop()
        assert not worker.is_running

    def test_stop_multiple_times(self):
        """Test that stop() can be called multiple times."""
        worker = OCRWorkerProcess(worker_id=0, use_gpu=False)
        worker.stop()
        worker.stop()
        worker.stop()
        assert not worker.is_running


@pytest.mark.skipif(
    not HAS_SUBPROCESS_MODULES, reason="subprocess modules not available"
)
class TestOCRServiceSubprocess:
    """Tests for OCRServiceSubprocess class."""

    def test_singleton_pattern(self):
        """Test singleton pattern."""
        # Reset singleton
        OCRServiceSubprocess._instance = None

        service1 = OCRServiceSubprocess(max_workers=1, use_gpu=False, auto_start=False)
        service2 = OCRServiceSubprocess(max_workers=2, use_gpu=True, auto_start=False)

        assert service1 is service2

        # Cleanup
        service1.shutdown()
        OCRServiceSubprocess._instance = None

    def test_singleton_thread_safety(self):
        """Test thread-safe singleton creation."""
        # Reset singleton
        OCRServiceSubprocess._instance = None

        instances = []
        lock = threading.Lock()

        def create_instance():
            service = OCRServiceSubprocess(
                max_workers=1, use_gpu=False, auto_start=False
            )
            with lock:
                instances.append(service)

        threads = [threading.Thread(target=create_instance) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All instances should be the same
        assert all(inst is instances[0] for inst in instances)

        # Cleanup
        if instances:
            instances[0].shutdown()
        OCRServiceSubprocess._instance = None

    def test_init_with_auto_start_false(self):
        """Test initialization with auto_start=False."""
        # Reset singleton
        OCRServiceSubprocess._instance = None

        service = OCRServiceSubprocess(max_workers=1, use_gpu=False, auto_start=False)

        assert service.max_workers == 1
        assert service.use_gpu is False
        assert len(service.workers) == 1

        # Cleanup
        service.shutdown()
        OCRServiceSubprocess._instance = None

    def test_is_ready_before_start(self):
        """Test is_ready returns False before start."""
        # Reset singleton
        OCRServiceSubprocess._instance = None

        service = OCRServiceSubprocess(max_workers=1, use_gpu=False, auto_start=False)

        assert not service.is_ready()

        # Cleanup
        service.shutdown()
        OCRServiceSubprocess._instance = None

    def test_get_status(self):
        """Test get_status method."""
        # Reset singleton
        OCRServiceSubprocess._instance = None

        service = OCRServiceSubprocess(max_workers=1, use_gpu=False, auto_start=False)

        status = service.get_status()

        assert "max_workers" in status
        assert "use_gpu" in status
        assert "ready" in status
        assert "workers" in status
        assert status["max_workers"] == 1
        assert len(status["workers"]) == 1

        # Cleanup
        service.shutdown()
        OCRServiceSubprocess._instance = None

    def test_shutdown(self):
        """Test shutdown method."""
        # Reset singleton
        OCRServiceSubprocess._instance = None

        service = OCRServiceSubprocess(max_workers=1, use_gpu=False, auto_start=False)

        # Should not raise
        service.shutdown()

        assert len(service.workers) == 0
        OCRServiceSubprocess._instance = None

    def test_reset_instance(self):
        """Test reset_instance class method."""
        # Reset and create
        OCRServiceSubprocess._instance = None
        _service = OCRServiceSubprocess(max_workers=1, use_gpu=False, auto_start=False)

        # Reset should clear instance
        OCRServiceSubprocess.reset_instance()
        assert OCRServiceSubprocess._instance is None


@pytest.mark.skipif(
    not HAS_SUBPROCESS_MODULES, reason="subprocess modules not available"
)
class TestOCRServiceSubprocessImagePreparation:
    """Tests for image preparation methods."""

    def test_prepare_image_data_bytes(self):
        """Test preparing image data from bytes."""
        # Reset singleton
        OCRServiceSubprocess._instance = None

        service = OCRServiceSubprocess(max_workers=1, use_gpu=False, auto_start=False)

        test_bytes = b"test_image_data"
        result = service._prepare_image_data(test_bytes)
        assert result == test_bytes

        # Cleanup
        service.shutdown()
        OCRServiceSubprocess._instance = None

    def test_prepare_image_data_pil(self):
        """Test preparing image data from PIL Image."""
        # Reset singleton
        OCRServiceSubprocess._instance = None

        service = OCRServiceSubprocess(max_workers=1, use_gpu=False, auto_start=False)

        img = Image.new("RGB", (100, 50), color="white")
        result = service._prepare_image_data(img)
        assert isinstance(result, bytes)
        assert len(result) > 0

        # Cleanup
        service.shutdown()
        OCRServiceSubprocess._instance = None

    def test_prepare_image_data_numpy(self):
        """Test preparing image data from numpy array."""
        # Reset singleton
        OCRServiceSubprocess._instance = None

        service = OCRServiceSubprocess(max_workers=1, use_gpu=False, auto_start=False)

        arr = np.zeros((50, 100, 3), dtype=np.uint8)
        arr.fill(255)  # White image
        result = service._prepare_image_data(arr)
        assert isinstance(result, bytes)
        assert len(result) > 0

        # Cleanup
        service.shutdown()
        OCRServiceSubprocess._instance = None

    def test_prepare_options_dict_none(self):
        """Test preparing options from None."""
        # Reset singleton
        OCRServiceSubprocess._instance = None

        service = OCRServiceSubprocess(max_workers=1, use_gpu=False, auto_start=False)

        result = service._prepare_options_dict(None)
        assert result == {}

        # Cleanup
        service.shutdown()
        OCRServiceSubprocess._instance = None

    def test_prepare_options_dict_dict(self):
        """Test preparing options from dict."""
        # Reset singleton
        OCRServiceSubprocess._instance = None

        service = OCRServiceSubprocess(max_workers=1, use_gpu=False, auto_start=False)

        options = {"use_angle_cls": True, "lang": "ch"}
        result = service._prepare_options_dict(options)
        assert result == options

        # Cleanup
        service.shutdown()
        OCRServiceSubprocess._instance = None
