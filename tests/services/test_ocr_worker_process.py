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

    def test_init_has_no_job_guard(self):
        """初始化后 _job_guard 为 None。"""
        worker = OCRWorkerProcess(worker_id=0, use_gpu=False)
        assert worker._job_guard is None

    def test_stop_closes_job_guard_if_present(self):
        """stop 时若 _job_guard 存在则关闭并置 None（即使 process 为 None）。"""
        worker = OCRWorkerProcess(worker_id=0, use_gpu=False)
        from unittest.mock import MagicMock

        mock_guard = MagicMock()
        worker._job_guard = mock_guard
        worker.stop()
        mock_guard.close.assert_called_once()
        assert worker._job_guard is None


@pytest.mark.skipif(
    not HAS_SUBPROCESS_MODULES, reason="subprocess modules not available"
)
class TestParseAndForwardLog:
    """子进程 stdout 转发为日志的行为。

    背景：PaddleX/transformers 等库会向 stdout 直接 print 识别结果/文本内容
    （如 "/x86" 这类用户文档片段），这些裸 print 不带标准日志格式，
    此前会被原样转发到日志，导致用户文档内容泄漏。
    期望：结构化日志行仍按级别转发；裸 print 只输出概括（行数），
    不输出具体内容。
    """

    def test_structured_line_forwarded_at_its_level(self, caplog):
        """标准日志格式（带时间戳+级别）按原级别转发，内容保留。"""
        worker = OCRWorkerProcess(worker_id=0, use_gpu=False)
        line = (
            "2024-01-15 10:30:45 [INFO] vibeocr.workers.ocr_worker: OCR 服务初始化完成"
        )

        with caplog.at_level("DEBUG", logger="vibeocr.services.ocr_worker_process"):
            worker._parse_and_forward_log(line)

        assert any(
            "OCR 服务初始化完成" in r.message and r.levelname == "INFO"
            for r in caplog.records
        )

    def test_structured_warning_line_forwarded_at_warning_level(self, caplog):
        """WARNING 级别的标准行按 WARNING 转发。"""
        worker = OCRWorkerProcess(worker_id=0, use_gpu=False)
        line = "2024-01-15 10:30:45 [WARNING] foo: 模型加载较慢"

        with caplog.at_level("DEBUG", logger="vibeocr.services.ocr_worker_process"):
            worker._parse_and_forward_log(line)

        assert any(
            "模型加载较慢" in r.message and r.levelname == "WARNING"
            for r in caplog.records
        )

    def test_raw_print_does_not_leak_content(self, caplog):
        """裸 print（无标准日志格式）不得把原始内容写进日志。

        模拟库 print 出识别到的文本片段 "/x86"。这些内容绝不能出现在日志里。
        """
        worker = OCRWorkerProcess(worker_id=0, use_gpu=False)
        line = "/x86  这是用户文档里的敏感文本片段"

        with caplog.at_level("DEBUG", logger="vibeocr.services.ocr_worker_process"):
            worker._parse_and_forward_log(line)

        leaked = [r.message for r in caplog.records if "/x86" in r.message]
        assert leaked == [], f"裸 print 内容泄漏到日志: {leaked}"

    def test_raw_print_summarized_as_count(self, caplog):
        """连续多条裸 print 只输出一条概括（行数），不逐条 dump。"""
        worker = OCRWorkerProcess(worker_id=0, use_gpu=False)
        raw_lines = [
            "/x86  内容1",
            "some raw paddle debug 一二三",
            "另一行裸输出",
        ]

        with caplog.at_level("DEBUG", logger="vibeocr.services.ocr_worker_process"):
            for line in raw_lines:
                worker._parse_and_forward_log(line)
            # 触发 flush（例如来了一个结构化行，或显式 flush）
            worker.flush_raw_log_buffer()

        # 内容绝不出现在任何日志记录里
        assert all("内容1" not in r.message for r in caplog.records)
        assert all("一二三" not in r.message for r in caplog.records)
        # 至少有一条概括记录，且提到行数 3
        summary = [r.message for r in caplog.records if "3" in r.message]
        assert summary, "应有概括记录（行数）"

    def test_structured_line_after_raw_flushes_summary(self, caplog):
        """结构化行到来时，先 flush 之前的裸 print 概括，再转发结构化行。"""
        worker = OCRWorkerProcess(worker_id=0, use_gpu=False)
        structured = "2024-01-15 10:30:45 [INFO] mod: 完成"

        with caplog.at_level("DEBUG", logger="vibeocr.services.ocr_worker_process"):
            worker._parse_and_forward_log("裸输出A")
            worker._parse_and_forward_log("裸输出B")
            worker._parse_and_forward_log(structured)

        msgs = [r.message for r in caplog.records]
        levels = [r.levelname for r in caplog.records]
        # 第一条是概括（不含裸内容），最后一条是结构化 INFO
        assert "裸输出A" not in msgs[0]
        assert "完成" in msgs[-1]
        assert levels[-1] == "INFO"
        # 概括记录提到了 2 行
        assert "2" in msgs[0]

    def test_newline_only_raw_print_is_ignored(self, caplog):
        """空行/纯空白的裸 print 不计入概括。"""
        worker = OCRWorkerProcess(worker_id=0, use_gpu=False)

        with caplog.at_level("DEBUG", logger="vibeocr.services.ocr_worker_process"):
            worker._parse_and_forward_log("   ")
            worker.flush_raw_log_buffer()

        assert caplog.records == []


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
        # PaddleX WorkerManager + MinerUBatchService
        assert hasattr(service, "_paddlex_manager")
        assert hasattr(service, "_mineru_batch")

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
        # Single PaddleX WorkerManager (MinerU uses MinerUBatchService, not a WorkerManager)
        assert status["max_workers"] == 1
        # Workers not started yet, list should be empty
        assert len(status["workers"]) == 0

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

        # Verify managers are cleaned up
        assert not service._initialized
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


@pytest.mark.skipif(
    not HAS_SUBPROCESS_MODULES, reason="subprocess modules not available"
)
class TestRecognizeBatchSubprocess:
    """Tests for OCRServiceSubprocess.recognize_batch (sub-batching + RCBG)."""

    def _make_service(self, shm_size=None):
        """Create a non-started service with a mocked manager."""
        OCRServiceSubprocess._instance = None
        service = OCRServiceSubprocess(max_workers=1, use_gpu=False, auto_start=False)
        if shm_size is not None:
            service.shm_size = shm_size
        service._paddlex_manager = RecordingManager()
        return service

    def teardown_method(self):
        OCRServiceSubprocess._instance = None

    def _noise_image(self, n: int = 4096):
        """An effectively incompressible image (~n*n*3 raw bytes → large PNG)."""
        rng = np.random.default_rng(seed=42)
        return rng.integers(0, 256, size=(n, n, 3), dtype=np.uint8)

    def test_empty_input_returns_empty(self):
        """Empty images list returns [] without calling the worker."""
        service = self._make_service()
        assert service.recognize_batch([]) == []
        assert service._paddlex_manager.subbatches == []

    def test_single_subbatch_routes_to_worker_recognize_batch(self):
        """All images fit one subbatch → single w.recognize_batch call."""
        service = self._make_service()
        imgs = [self._noise_image(4) for _ in range(2)]
        got = service.recognize_batch(imgs, {"pipeline": "OCR"})

        assert [r.raw_text for r in got] == ["r0", "r1"]
        # exactly one sub-batch dispatched
        assert len(service._paddlex_manager.subbatches) == 1
        assert len(service._paddlex_manager.subbatches[0]) == 2

    def test_subbatch_split_when_over_budget(self):
        """Images exceeding SHM budget are split into multiple sub-batches."""
        # tiny shm so each noise page (large PNG) becomes its own sub-batch
        service = self._make_service(shm_size=4096)
        imgs = [self._noise_image(32) for _ in range(3)]  # each PNG >> 4KB
        got = service.recognize_batch(imgs, {"pipeline": "OCR"})

        # results in original order, one per page
        assert len(got) == 3
        assert all(r is not None for r in got)
        # 3 sub-batches (each page alone, since each over budget)
        assert len(service._paddlex_manager.subbatches) == 3

    def test_results_order_preserved_across_subbatches(self):
        """Result order matches input order even when split into sub-batches."""
        # small budget + large noise pages → forced multi-batch
        service = self._make_service(shm_size=8192)
        imgs = [self._noise_image(32) for _ in range(4)]
        got = service.recognize_batch(imgs, {"pipeline": "OCR"})

        assert len(got) == 4
        # no None holes (all pages produced a result)
        assert all(r is not None for r in got)
        # multiple sub-batches
        assert len(service._paddlex_manager.subbatches) >= 2

    def test_subbatch_failure_keeps_going(self):
        """A failing sub-batch yields None for its pages, others succeed."""
        # each noise page alone (over budget) → 3 sub-batches, fail the 2nd
        service = self._make_service(shm_size=4096)
        service._paddlex_manager.fail_on = {1: RuntimeError("worker boom")}
        imgs = [self._noise_image(32) for _ in range(3)]
        got = service.recognize_batch(imgs, {"pipeline": "OCR"})

        assert len(got) == 3
        assert got[0] is not None
        assert got[1] is None  # the failed sub-batch page
        assert got[2] is not None

    def test_single_page_does_not_deadlock(self):
        """A single page that alone exceeds budget still sends (no infinite loop)."""
        service = self._make_service(shm_size=512)
        imgs = [self._noise_image(32)]  # PNG >> 512 bytes
        got = service.recognize_batch(imgs, {"pipeline": "OCR"})
        assert len(got) == 1
        assert got[0] is not None
        assert len(service._paddlex_manager.subbatches) == 1


# --- helpers for the subprocess recognize_batch tests ---


class RecordingManager:
    """Mock WorkerManager.

    Records each dispatched sub-batch (list of PNG bytes) and returns one
    OCRResult per image (tagged r0, r1, ...). Optionally raises on a given
    sub-batch index to simulate worker failure.
    """

    def __init__(self):
        self.subbatches: list[list[bytes]] = []
        self.fail_on: dict[int, Exception] = {}

    def execute(self, task, timeout=60.0, retry_count=0):
        idx = len(self.subbatches)
        # discover sub-batch size by invoking task against a recording worker
        worker = RecordingWorker()
        result = task(worker)
        sub_imgs = worker.last_subbatch or []
        self.subbatches.append(list(sub_imgs))
        if idx in self.fail_on:
            raise self.fail_on[idx]
        return result


class RecordingWorker:
    """Mock worker proxy: records the sub-batch it received."""

    def __init__(self):
        self.last_subbatch: list[bytes] | None = None

    def recognize_batch(self, images, options_dict, timeout=60.0):
        self.last_subbatch = list(images)
        # return one tagged result per input image
        return [_mk_result(f"r{i}") for i in range(len(images))]

    def recognize(self, image_data, options_dict, timeout=60.0):
        return _mk_result("r")


def _mk_result(text: str = "ok"):
    from vibeocr.models.ocr_result import OCRResult

    return OCRResult(raw_text=text, text_blocks=[])
