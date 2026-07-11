"""
Tests for OCRWorkerProcess and OCRServiceSubprocess.

Tests the subprocess-based OCR service implementation.
"""

import threading
from typing import Any

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
    # 可选依赖缺失时占位；测试由 @skipif(not HAS_SUBPROCESS_MODULES) 跳过，
    # 这些占位值运行时永不被调用。用 Any 类型满足静态绑定分析，
    # 避免后续类体内的构造/调用报 OptionalCall/MemberAccess。
    OCRServiceSubprocess: Any = None  # type: ignore[assignment]
    OCRWorkerProcess: Any = None  # type: ignore[assignment]
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

    def test_get_worker_env_dev_mode_no_frozen(self):
        """开发态（非 frozen）注入 src/ 到 PYTHONPATH，让子进程能 import vibeocr。

        子进程是独立的 sys.executable，不继承主进程 conftest 的 sys.path 修改，
        故必须显式把 src/（vibeocr 包父目录）注入 PYTHONPATH，否则子进程报
        ModuleNotFoundError: No module named 'vibeocr'。
        """
        worker = OCRWorkerProcess(worker_id=0, use_gpu=False)
        env = worker._get_worker_env()
        import os
        from pathlib import Path

        import vibeocr

        # 开发态必须注入 src/（vibeocr 包父目录）到 PYTHONPATH
        assert "PYTHONPATH" in env
        src_dir = str(Path(vibeocr.__file__).resolve().parent.parent)
        assert src_dir in env["PYTHONPATH"].split(os.pathsep)

    def test_get_worker_env_frozen_injects_meipass(self):
        """打包态注入 PYTHONPATH 指向 _MEIPASS，让子进程能 import vibeocr。"""
        import sys
        from unittest.mock import patch

        worker = OCRWorkerProcess(worker_id=0, use_gpu=False)
        fake_meipass = r"C:\fake\_MEIPASS"
        # 清理父进程 PYTHONPATH，避免干扰断言
        with patch.object(sys, "frozen", True, create=True), patch.object(
            sys, "_MEIPASS", fake_meipass, create=True
        ), patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("PYTHONPATH", None)
            env = worker._get_worker_env()

        assert env["PYTHONPATH"] == fake_meipass

    def test_get_worker_env_frozen_preserves_existing_pythonpath(self):
        """打包态注入 PYTHONPATH 时保留父进程已有的 PYTHONPATH。"""
        import sys
        from unittest.mock import patch

        worker = OCRWorkerProcess(worker_id=0, use_gpu=False)
        fake_meipass = r"C:\fake\_MEIPASS"
        existing = r"C:\some\other\path"
        with patch.object(sys, "frozen", True, create=True), patch.object(
            sys, "_MEIPASS", fake_meipass, create=True
        ), patch.dict("os.environ", {"PYTHONPATH": existing}, clear=False):
            env = worker._get_worker_env()

        # _MEIPASS 应排在前面，原有路径保留
        assert env["PYTHONPATH"].startswith(fake_meipass)
        assert existing in env["PYTHONPATH"]

    def test_startup_output_buffer_initially_empty(self):
        """初始化后启动期输出缓冲为空。"""
        worker = OCRWorkerProcess(worker_id=0, use_gpu=False)
        assert worker._startup_output == []

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

    def test_preload_polls_is_data_ready_after_writing(self):
        """回归 bug：发送 PREL 后应轮询 _is_data_ready 确认 Worker 已读取。

        旧实现用 time.sleep(0.1) 等待，Worker 加载慢时主进程 read_message
        重试会读到自己刚写的 PREL 并清除就绪标志，导致 Worker 永远收不到
        请求而死锁超时。修复后应调用 protocol._is_data_ready() 轮询。
        """
        from unittest.mock import MagicMock, patch

        from vibeocr.services.ocr_worker_process import (
            MSG_PRELOAD_DONE,
        )
        from vibeocr.utils.shared_memory_v2 import (
            MessageType,
            serialize_preload_result,
        )

        worker = OCRWorkerProcess(worker_id=0, use_gpu=False)

        # 构造 mock protocol：_is_data_ready 先 True 后 False（模拟 Worker 读取）
        mock_protocol = MagicMock()
        ready_sequence = iter([True, False])

        def fake_is_data_ready():
            try:
                return next(ready_sequence)
            except StopIteration:
                return False

        mock_protocol._is_data_ready.side_effect = fake_is_data_ready
        # read_message 返回 PRELOAD_DONE 携带成功结果
        mock_protocol.read_message.return_value = (
            MSG_PRELOAD_DONE,
            serialize_preload_result({"OCR": True}),
        )
        worker.protocol = mock_protocol

        # 让 is_ready 检查通过（无需真正启动子进程）
        with (
            patch.object(type(worker), "is_ready", new=True),
            patch.object(type(worker), "is_running", new=True),
        ):
            # 同时禁用真实的 _calculate_preload_timeout 干扰
            with patch.object(
                type(worker),
                "_calculate_preload_timeout",
                return_value=10.0,
            ):
                results = worker.preload_pipelines(["OCR"])

        # 关键断言：发送后调用了 _is_data_ready 轮询（而非依赖 sleep）
        assert mock_protocol._is_data_ready.called, (
            "preload_pipelines 应轮询 _is_data_ready 确认 Worker 已读取请求"
        )
        assert results == {"OCR": True}
        # write_message 应以 MSG_PRELOAD 类型发送一次
        assert mock_protocol.write_message.call_count == 1
        sent_type = mock_protocol.write_message.call_args[0][0]
        assert sent_type == MessageType.PRELOAD

    def test_preload_self_read_guard_raises_after_threshold(self):
        """回归 bug：兜底分支连续读到自身请求超过阈值应抛异常。

        正常流程（已轮询确认）不应走到兜底分支；若仍连续读到自身请求，
        说明状态异常，超过阈值应主动失败而非无限等待。
        """
        from unittest.mock import MagicMock, patch

        from vibeocr.services.ocr_worker_process import (
            MSG_PRELOAD,
            OCRWorkerProcessError,
        )

        worker = OCRWorkerProcess(worker_id=0, use_gpu=False)

        mock_protocol = MagicMock()
        # _is_data_ready 立即 False（轮询确认通过，不干扰本测试）
        mock_protocol._is_data_ready.return_value = False
        # read_message 每次都返回主进程自己的 PREL（模拟异常竞态）
        mock_protocol.read_message.return_value = (MSG_PRELOAD, b"\x00")
        worker.protocol = mock_protocol

        with (
            patch.object(type(worker), "is_ready", new=True),
            patch.object(type(worker), "is_running", new=True),
            patch.object(
                type(worker),
                "_calculate_preload_timeout",
                return_value=100.0,
            ),pytest.raises(OCRWorkerProcessError, match="多次读到自身请求")
        ):
            worker.preload_pipelines(["OCR"])

        # 应在第 5 次命中时抛出
        assert worker._preload_self_read_count >= 5


@pytest.mark.skipif(
    not HAS_SUBPROCESS_MODULES, reason="subprocess modules not available"
)
class TestOCRWorkerProcessLogForwarding:
    """OCRWorkerProcess 把子进程 stdout 转发委托给 SubprocessLogForwarder。

    转发逻辑本身的完整测试见 tests/utils/test_subprocess_log.py
    （三套子进程通道参数化共用）。这里仅验证 OCRWorkerProcess 正确接线：
    logger 名走 vibeocr.subprocess.ocr_worker，source_label 带 [Worker N] 前缀，
    行为与公共 forwarder 一致。
    """

    _LOGGER = "vibeocr.subprocess.ocr_worker"

    def test_structured_line_forwarded_at_info_with_worker_label(self, caplog):
        """结构化行按 INFO 转发，消息带 [Worker 0] 前缀。"""
        worker = OCRWorkerProcess(worker_id=0, use_gpu=False)
        line = "2024-01-15 10:30:45 [INFO] vibeocr.workers.ocr_worker: 就绪"

        with caplog.at_level("DEBUG", logger=self._LOGGER):
            worker._parse_and_forward_log(line)

        assert any(
            "就绪" in r.message and r.levelname == "INFO" and "[Worker 0]" in r.message
            for r in caplog.records
        )

    def test_raw_print_does_not_leak_content(self, caplog):
        """裸 print 不泄漏原始内容（委托 forwarder 的折叠行为）。"""
        worker = OCRWorkerProcess(worker_id=0, use_gpu=False)

        with caplog.at_level("DEBUG", logger=self._LOGGER):
            worker._parse_and_forward_log("/x86  敏感片段")
            worker.flush_raw_log_buffer()

        leaked = [r.message for r in caplog.records if "/x86" in r.message]
        assert leaked == [], f"裸 print 内容泄漏到日志: {leaked}"

    def test_worker_id_reflected_in_label(self, caplog):
        """不同 worker_id 产生不同 source_label。"""
        worker = OCRWorkerProcess(worker_id=3, use_gpu=False)

        with caplog.at_level("DEBUG", logger=self._LOGGER):
            worker._parse_and_forward_log("2024-01-15 10:30:45 [INFO] mod: hi")

        assert any("[Worker 3]" in r.message for r in caplog.records)



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

    def test_startup_failure_uses_buffered_output_not_unknown(self):
        """子进程早退时，错误信息应来自启动期输出缓冲，而非被吞成"未知错误"。

        回归测试：后台 read_stdout 线程会消费 stdout，导致 process.communicate()
        返回空，旧逻辑因此报"未知错误"而丢失真实错误（如 ModuleNotFoundError）。
        修复后优先使用 _startup_output。
        """
        from unittest.mock import MagicMock, patch

        worker = OCRWorkerProcess(worker_id=0, use_gpu=False, shm_size=1024)
        real_error = "ModuleNotFoundError: No module named 'vibeocr'"

        # 模拟进程已退出 + communicate() 返回空（stdout 已被后台线程消费）
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # 已退出
        mock_proc.communicate.return_value = (b"", b"")

        # 预填充启动期缓冲（模拟后台线程在进程退出前已捕获真实错误）。
        # read_stdout 线程读到内容会 append 到 _startup_output；这里直接预置。
        worker._startup_output = [real_error]

        # patch 掉共享内存创建、子进程启动、Windows Job Object 绑定
        # （JobObjectGuard 会调用原生 Job Object API，mock 进程无真实句柄会 segfault）
        mock_protocol = MagicMock()
        with patch(
            "vibeocr.services.ocr_worker_process.SharedMemoryProtocol",
            return_value=mock_protocol,
        ), patch(
            "vibeocr.services.ocr_worker_process.subprocess.Popen",
            return_value=mock_proc,
        ), patch(
            "vibeocr.services.ocr_worker_process.JobObjectGuard"
        ), pytest.raises(Exception) as exc_info:
            worker.start(timeout=0.1)

        # 真实错误应出现在异常信息里，而非"未知错误"
        assert real_error in str(exc_info.value)
        assert "未知错误" not in str(exc_info.value)


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


@pytest.mark.skipif(
    not HAS_SUBPROCESS_MODULES, reason="subprocess modules not available"
)
class TestBatchCancelIndependentChannel:
    """批量取消通过 SHM cancel flag 独立通道，不经过 WorkerManager 调度。"""

    def test_request_batch_cancel_writes_cancel_flag_directly(self):
        """request_batch_cancel 直接写 SHM cancel flag，不调用 WorkerManager.execute"""
        from unittest.mock import MagicMock

        proc = OCRWorkerProcess.__new__(OCRWorkerProcess)
        proc.worker_id = 0
        proc.busy = False
        proc._ready = True  # is_ready 为 True
        mock_popen = MagicMock()
        mock_popen.poll.return_value = None  # running
        proc.process = mock_popen
        mock_proto = MagicMock()
        mock_proto.is_cancelled.return_value = False
        proc.protocol = mock_proto

        proc.request_batch_cancel()

        # 应直接调用 set_cancel_flag，不经过 execute
        mock_proto.set_cancel_flag.assert_called_once()

    def test_request_batch_cancel_skips_when_not_ready(self):
        """worker 未就绪时 request_batch_cancel 不抛异常，静默返回"""
        from unittest.mock import MagicMock

        proc = OCRWorkerProcess.__new__(OCRWorkerProcess)
        proc.worker_id = 0
        proc.protocol = MagicMock()
        proc._ready = False  # is_ready 为 False
        proc.process = None

        # 不应抛异常
        proc.request_batch_cancel()
        # 未就绪时不应写 cancel flag
        proc.protocol.set_cancel_flag.assert_not_called()

    def test_batch_cancel_does_not_call_worker_manager_execute(self):
        """OCRServiceSubprocess.batch_cancel 不经过 _paddlex_manager.execute

        旧实现会因 worker busy 而进入最长 300 秒等待，冻结 UI。
        修复后应直接向 busy worker 写 cancel flag。
        """
        from unittest.mock import MagicMock

        from vibeocr.services.worker_manager import WorkerInfo, WorkerState

        # Reset singleton
        OCRServiceSubprocess._instance = None
        svc = OCRServiceSubprocess(max_workers=1, use_gpu=False, auto_start=False)
        svc._initialized = True

        mock_manager = MagicMock()
        # 模拟一个 busy worker（commit 正在运行）
        mock_proc = MagicMock()
        mock_proc.is_ready = True
        mock_proc.busy = True
        mock_manager._workers = [WorkerInfo(worker_id=0, process=mock_proc, state=WorkerState.BUSY)]
        svc._paddlex_manager = mock_manager

        svc.batch_cancel()

        # 不应调用 execute（旧路径会因 busy 而阻塞）
        mock_manager.execute.assert_not_called()
        # 应直接向 busy worker 写 cancel flag
        mock_proc.request_batch_cancel.assert_called_once()

        svc._initialized = False
        OCRServiceSubprocess._instance = None


@pytest.mark.skipif(
    not HAS_SUBPROCESS_MODULES, reason="subprocess modules not available"
)
class TestStopShutdownOrder:
    """stop() 优雅关闭顺序：MSG_SHUTDOWN 先于 guard.close，join stdout reader。

    根因：旧 stop() 先关 Job guard（内核 kill 子进程），再发 MSG_SHUTDOWN，
    后者已无机会执行。Job Object 应作为超时兜底，不作为第一步。
    """

    def test_stop_sends_shutdown_before_guard_close(self):
        """stop 的调用顺序：write_message(SHUTDOWN) 在 guard.close() 之前"""
        from unittest.mock import MagicMock

        from vibeocr.services.ocr_worker_process import OCRWorkerProcess

        proc = OCRWorkerProcess.__new__(OCRWorkerProcess)
        proc.worker_id = 0
        proc._job_guard = MagicMock()
        mock_popen = MagicMock()
        mock_popen.poll.return_value = None  # running
        proc.process = mock_popen
        proc.protocol = MagicMock()
        proc._ready = True
        proc.busy = False
        proc._stdout_thread = None

        call_order = []
        proc.protocol.write_message = lambda *a, **k: call_order.append("shutdown_msg")
        proc._job_guard.close = lambda: call_order.append("guard_close")
        proc.process.wait = lambda timeout=0: call_order.append("wait")
        proc.protocol.unlink = lambda: call_order.append("unlink")
        proc.protocol.close = lambda: call_order.append("proto_close")

        proc.stop(timeout=0.5)

        # MSG_SHUTDOWN 必须在 guard_close 之前
        assert "shutdown_msg" in call_order
        assert "guard_close" in call_order
        assert call_order.index("shutdown_msg") < call_order.index("guard_close"), (
            f"shutdown 必须先于 guard close，实际顺序: {call_order}"
        )

    def test_stop_joins_stdout_reader(self):
        """stop 应 join stdout reader 线程"""
        from unittest.mock import MagicMock

        from vibeocr.services.ocr_worker_process import OCRWorkerProcess

        proc = OCRWorkerProcess.__new__(OCRWorkerProcess)
        proc.worker_id = 0
        proc._job_guard = None
        mock_popen = MagicMock()
        mock_popen.poll.return_value = 0  # already exited
        proc.process = mock_popen
        proc.protocol = MagicMock()
        proc._ready = False
        proc.busy = False
        mock_reader = MagicMock()
        mock_reader.is_alive.return_value = False
        proc._stdout_thread = mock_reader

        proc.stop(timeout=0.5)

        mock_reader.join.assert_called_once()

    def test_stop_closes_guard_even_when_process_already_exited(self):
        """进程已退出时 stop 仍关闭 guard（若存在）"""
        from unittest.mock import MagicMock

        from vibeocr.services.ocr_worker_process import OCRWorkerProcess

        proc = OCRWorkerProcess.__new__(OCRWorkerProcess)
        proc.worker_id = 0
        mock_guard = MagicMock()
        proc._job_guard = mock_guard
        mock_popen = MagicMock()
        mock_popen.poll.return_value = 0  # exited
        proc.process = mock_popen
        proc.protocol = MagicMock()
        proc._ready = False
        proc.busy = False
        proc._stdout_thread = None

        proc.stop(timeout=0.5)

        mock_guard.close.assert_called_once()
        assert proc._job_guard is None

    def test_stop_clears_protocol_and_state(self):
        """stop 清理 protocol/process/_ready/busy"""
        from unittest.mock import MagicMock

        from vibeocr.services.ocr_worker_process import OCRWorkerProcess

        proc = OCRWorkerProcess.__new__(OCRWorkerProcess)
        proc.worker_id = 0
        proc._job_guard = None
        mock_popen = MagicMock()
        mock_popen.poll.return_value = 0
        proc.process = mock_popen
        proc.protocol = MagicMock()
        proc._ready = True
        proc.busy = True
        proc._stdout_thread = None

        proc.stop(timeout=0.5)

        assert proc.protocol is None
        assert proc.process is None
        assert proc._ready is False
        assert proc.busy is False
