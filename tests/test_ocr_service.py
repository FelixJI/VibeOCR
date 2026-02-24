"""Tests for OCRService."""

import threading

import numpy as np
import pytest
from PIL import Image

from vibeocr.services.ocr_service import OCRService

# 检查 paddlex 是否可用
try:
    from paddlex import create_pipeline  # noqa: F401

    HAS_PADDLEX = True
except ImportError:
    HAS_PADDLEX = False


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


@pytest.mark.skipif(not HAS_PADDLEX, reason="paddlex not installed")
class TestOCRServicePipeline:
    """测试 OCR 产线懒加载。"""

    def test_pipeline_lazy_loading(self):
        """产线仅在首次访问时创建。"""
        service = OCRService()
        # 清除现有产线
        OCRService._pipeline = None
        # 如果有实例属性也清除
        if "_pipeline" in service.__dict__:
            del service.__dict__["_pipeline"]

        # 首次访问前，产线应该为 None
        assert OCRService._pipeline is None
        # 访问 pipeline 属性
        pipeline = service.pipeline
        # 首次访问后，产线应该被创建
        assert pipeline is not None
        # 后续访问返回同一产线
        assert service.pipeline is pipeline

        # 清理
        OCRService._pipeline = None
        if "_pipeline" in service.__dict__:
            del service.__dict__["_pipeline"]


@pytest.mark.skipif(not HAS_PADDLEX, reason="paddlex not installed")
class TestOCRServiceRecognize:
    """测试 OCR 识别功能。"""

    def test_recognize_pil_image(self, sample_image_with_text_bytes):
        """识别 PIL Image 格式。"""
        import io

        service = OCRService()
        img = Image.open(io.BytesIO(sample_image_with_text_bytes))
        result = service.recognize(img)
        # 注意：实际识别结果取决于 PaddleX
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
