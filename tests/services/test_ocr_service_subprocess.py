"""
Tests for subprocess-based OCRService.

测试使用子进程的 OCR 服务实现。
"""

import os
import threading
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from vibeocr.env_manager import (
    get_embedded_python,
    get_embedded_venv_python,
    is_embedded_python_ready,
)

# 检查嵌入式 Python 是否可用
try:
    from vibeocr.services.ocr_service_subprocess import (
        OCRService,
        OCRServiceSubprocess,
        _OCRWorker,
        _SharedMemoryProtocol,
    )

    HAS_SUBPROCESS_OCR = True
except ImportError:
    HAS_SUBPROCESS_OCR = False

# 检查是否在 CI 环境中
IS_CI = os.environ.get("CI", "false").lower() == "true"


@pytest.mark.skipif(not HAS_SUBPROCESS_OCR, reason="subprocess OCR not available")
class TestSharedMemoryProtocol:
    """测试共享内存协议。"""

    def test_protocol_creation(self):
        """测试协议创建。"""
        protocol = _SharedMemoryProtocol("test_shm", 1024)
        assert protocol.shm_name == "test_shm"
        assert protocol.shm_size == 1024
        assert protocol.shm is None

    def test_protocol_create_memory(self):
        """测试创建共享内存。"""
        protocol = _SharedMemoryProtocol("test_shm_creation", 1024)
        shm = protocol.create()
        assert shm is not None
        assert shm.size == 1024

        # 清理
        protocol.close()
        protocol.unlink()

    def test_protocol_write_read_message(self):
        """测试消息读写。"""
        protocol = _SharedMemoryProtocol("test_shm_rw", 1024)
        protocol.create()

        # 写入测试数据
        test_data = b"Hello, World!"
        offset = protocol.write_message(10, test_data)

        # 读取测试数据
        read_data = protocol.read_message(10, len(test_data))
        assert read_data == test_data
        assert offset == 10 + len(test_data)

        # 清理
        protocol.close()
        protocol.unlink()


@pytest.mark.skipif(not HAS_SUBPROCESS_OCR, reason="subprocess OCR not available")
class TestOCRWorker:
    """测试单个 OCR Worker。"""

    def test_worker_initialization(self):
        """测试 worker 初始化。"""
        worker = _OCRWorker(worker_id=0, use_gpu=False, shm_size=1024 * 1024)
        assert worker.worker_id == 0
        assert worker.use_gpu is False
        assert worker.shm_size == 1024 * 1024
        assert not worker.busy
        assert worker.process is None

    def test_worker_get_embedded_python(self):
        """测试获取嵌入式 Python 路径。"""
        worker = _OCRWorker(worker_id=0, use_gpu=False)
        python_path = worker._get_embedded_python()
        assert python_path is not None
        assert isinstance(python_path, (str, Path))

    def test_worker_get_worker_script(self):
        """测试获取 worker 脚本路径。"""
        worker = _OCRWorker(worker_id=0, use_gpu=False)
        script_path = worker._get_worker_script()
        assert script_path is not None
        assert isinstance(script_path, (str, Path))
        # 检查脚本是否存在
        if not IS_CI:
            assert Path(script_path).exists(), f"Worker script not found: {script_path}"

    def test_worker_image_to_bytes(self):
        """测试图像转换为字节。"""
        worker = _OCRWorker(worker_id=0, use_gpu=False)

        # 测试 PIL Image
        img = Image.new("RGB", (100, 50), color="white")
        img_bytes = worker._image_to_bytes(img)
        assert isinstance(img_bytes, bytes)
        assert len(img_bytes) > 0

        # 测试 numpy 数组
        arr = np.array(img)
        arr_bytes = worker._image_to_bytes(arr)
        assert isinstance(arr_bytes, bytes)
        assert len(arr_bytes) > 0


@pytest.mark.skipif(not HAS_SUBPROCESS_OCR, reason="subprocess OCR not available")
class TestOCRServiceSubprocess:
    """测试子进程 OCR 服务。"""

    def test_singleton_pattern(self):
        """测试单例模式。"""
        # 重置单例
        OCRServiceSubprocess._instance = None

        service1 = OCRServiceSubprocess(max_workers=1, use_gpu=False)
        service2 = OCRServiceSubprocess(max_workers=2, use_gpu=True)
        assert service1 is service2

        # 清理
        service1.shutdown()
        OCRServiceSubprocess._instance = None

    def test_thread_safe_singleton(self):
        """测试线程安全的单例。"""
        # 重置单例
        OCRServiceSubprocess._instance = None

        instances = []
        lock = threading.Lock()

        def create_instance():
            service = OCRServiceSubprocess(max_workers=1, use_gpu=False)
            with lock:
                instances.append(service)

        threads = [threading.Thread(target=create_instance) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 所有实例应该是同一个
        assert all(inst is instances[0] for inst in instances)

        # 清理
        if instances:
            instances[0].shutdown()
        OCRServiceSubprocess._instance = None


@pytest.mark.skipif(not HAS_SUBPROCESS_OCR, reason="subprocess OCR not available")
@pytest.mark.skipif(IS_CI, reason="Skip in CI environment")
class TestOCRServiceIntegration:
    """集成测试 - 需要嵌入式 Python 环境。"""

    @pytest.fixture(scope="class")
    def embedded_python_available(self):
        """检查嵌入式 Python 是否可用。"""
        python_exe = get_embedded_python()
        if not Path(python_exe).exists():
            pytest.skip(f"Embedded Python not found: {python_exe}")
        return python_exe

    @pytest.fixture(scope="class")
    def ocr_service(self, embedded_python_available):
        """创建 OCR 服务实例。"""
        # 检查 worker 脚本是否存在
        project_root = Path(__file__).parent.parent
        worker_script = project_root / "src" / "vibeocr" / "workers" / "ocr_worker.py"
        if not worker_script.exists():
            pytest.skip(f"Worker script not found: {worker_script}")

        # 重置单例
        OCRService._instance = None

        # 创建服务
        service = OCRService()
        yield service

        # 清理
        service.shutdown()
        OCRService._instance = None

    def test_recognize_simple_text(self, ocr_service):
        """测试识别简单文本。"""
        # 创建包含文本的测试图像
        img = Image.new("RGB", (200, 100), color="white")
        from PIL import ImageDraw, ImageFont

        draw = ImageDraw.Draw(img)
        # 使用默认字体
        try:
            font = ImageFont.truetype("arial.ttf", 20)
        except OSError:
            font = ImageFont.load_default()

        draw.text((10, 10), "Hello World", fill="black", font=font)

        # 执行识别
        try:
            result = ocr_service.recognize(img)
            assert isinstance(result, str)
            # 可能识别出一些内容
            if result:
                assert len(result) > 0
        except RuntimeError as e:
            # 如果嵌入式环境没有正确配置，允许跳过
            if "Embedded Python not found" in str(e):
                pytest.skip("Embedded Python environment not properly configured")
            raise

    def test_recognize_empty_image(self, ocr_service):
        """测试识别空白图像。"""
        img = Image.new("RGB", (100, 50), color="white")
        result = ocr_service.recognize(img)
        assert isinstance(result, str)
        # 空白图像应该返回空字符串或极少内容


class TestEnvManagerHelpers:
    """测试环境管理器辅助函数。"""

    def test_get_embedded_python(self):
        """测试获取嵌入式 Python 路径。"""
        python_path = get_embedded_python()
        assert python_path is not None
        assert isinstance(python_path, Path)

    def test_get_embedded_venv_python(self):
        """测试获取虚拟环境 Python 路径。"""
        python_path = get_embedded_venv_python()
        assert python_path is not None
        assert isinstance(python_path, Path)

    def test_is_embedded_python_ready(self):
        """测试检查嵌入式 Python 是否准备好。"""
        ready = is_embedded_python_ready()
        assert isinstance(ready, bool)
        # 在开发环境中，可能是 False（如果还没安装嵌入式 Python）


@pytest.mark.skipif(not HAS_SUBPROCESS_OCR, reason="subprocess OCR not available")
class TestOCRServiceCompatibility:
    """测试 OCRService 兼容层。"""

    def test_ocr_service_singleton(self):
        """测试 OCRService 单例。"""
        # 重置单例
        OCRService._instance = None

        service1 = OCRService()
        service2 = OCRService()
        assert service1 is service2

        # 清理
        service1.shutdown()
        OCRService._instance = None

    def test_ocr_service_recognize_signature(self):
        """测试 recognize 方法签名兼容性。"""
        # 重置单例
        OCRService._instance = None

        service = OCRService()

        # 检查方法存在
        assert hasattr(service, "recognize")
        assert callable(service.recognize)

        # 清理
        service.shutdown()
        OCRService._instance = None
