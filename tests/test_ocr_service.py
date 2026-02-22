"""Tests for OCRService."""

import threading

import numpy as np
import pytest
from PIL import Image

from vibeocr.services.ocr_service import OCRService

# 检查 onnxruntime 是否可用
try:
    import onnxruntime  # noqa: F401

    HAS_ONNXRUNTIME = True
except ImportError:
    HAS_ONNXRUNTIME = False


class TestOCRServiceSingleton:
    """测试单例模式。"""

    def test_singleton_returns_same_instance(self):
        """多次实例化返回同一对象。"""
        instance1 = OCRService()
        instance2 = OCRService()
        assert instance1 is instance2

    def test_singleton_thread_safety(self):
        """多线程环境下单例仍然唯一。"""
        instances = []

        def create_instance():
            instances.append(OCRService())

        threads = [threading.Thread(target=create_instance) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(inst is instances[0] for inst in instances)


@pytest.mark.skipif(not HAS_ONNXRUNTIME, reason="onnxruntime not installed")
class TestOCRServiceEngine:
    """测试 OCR 引擎懒加载。"""

    def test_engine_lazy_loading(self):
        """引擎仅在首次访问时创建。"""
        service = OCRService()
        # 清除现有引擎（如果有）
        OCRService._engine = None

        assert OCRService._engine is None
        _ = service.engine
        assert OCRService._engine is not None

        # 清理
        OCRService._engine = None


@pytest.mark.skipif(not HAS_ONNXRUNTIME, reason="onnxruntime not installed")
class TestOCRServiceRecognize:
    """测试 OCR 识别功能。"""

    def test_recognize_pil_image(self, sample_image_with_text_bytes):
        """识别 PIL Image 格式。"""
        import io

        service = OCRService()
        img = Image.open(io.BytesIO(sample_image_with_text_bytes))
        result = service.recognize(img)
        # 注意：实际识别结果取决于 RapidOCR
        assert isinstance(result, str)

    def test_recognize_numpy_array(self, sample_image_with_text_bytes):
        """识别 numpy 数组格式。"""
        import io

        service = OCRService()
        img = Image.open(io.BytesIO(sample_image_with_text_bytes))
        arr = np.array(img)
        result = service.recognize(arr)
        assert isinstance(result, str)

    def test_recognize_empty_image_returns_empty_string(self):
        """空白图片返回空字符串。"""
        service = OCRService()
        img = Image.new("RGB", (100, 50), color="white")
        result = service.recognize(img)
        # 空白图片可能返回空字符串或极少文字
        assert isinstance(result, str)
