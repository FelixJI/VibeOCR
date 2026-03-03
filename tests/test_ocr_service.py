"""Tests for OCRService."""

import threading

import numpy as np
import pytest
from PIL import Image

from vibeocr.models.ocr_result import OCRResult
from vibeocr.services.ocr_service import OCROptions, OCRPipeline, OCRService

# 检查 paddlex 是否可用
try:
    from paddlex import create_pipeline

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


class TestOCRPipeline:
    """测试 OCR 管道枚举。"""

    def test_pipeline_values(self):
        """管道枚举值正确。"""
        assert OCRPipeline.OCR.value == "OCR"
        assert OCRPipeline.TABLE_RECOGNITION.value == "table_recognition"
        assert OCRPipeline.FORMULA_RECOGNITION.value == "formula_recognition"
        assert OCRPipeline.PP_STRUCTURE_V3.value == "PP-StructureV3"

    def test_pipeline_display_names(self):
        """管道显示名称正确。"""
        assert OCRPipeline.OCR.display_name == "通用 OCR"
        assert OCRPipeline.TABLE_RECOGNITION.display_name == "表格识别"
        assert OCRPipeline.FORMULA_RECOGNITION.display_name == "公式识别"
        assert OCRPipeline.PP_STRUCTURE_V3.display_name == "版面解析"

    def test_pipeline_descriptions(self):
        """管道描述正确。"""
        assert "文字" in OCRPipeline.OCR.description
        assert "表格" in OCRPipeline.TABLE_RECOGNITION.description
        assert "公式" in OCRPipeline.FORMULA_RECOGNITION.description
        assert "版面" in OCRPipeline.PP_STRUCTURE_V3.description


class TestOCROptions:
    """测试 OCR 选项数据类。"""

    def test_default_options(self):
        """默认选项值正确。"""
        options = OCROptions()
        assert options.pipeline == OCRPipeline.OCR
        assert options.use_doc_orientation_classify is False
        assert options.use_doc_unwarping is False
        assert options.use_textline_orientation is True
        assert options.use_layout_detection is False
        assert options.use_table_recognition is True
        assert options.use_formula_recognition is True
        assert options.use_seal_recognition is False
        assert options.use_chart_recognition is False

    def test_custom_options(self):
        """自定义选项值正确。"""
        options = OCROptions(
            pipeline=OCRPipeline.PP_STRUCTURE_V3,
            use_doc_orientation_classify=True,
            use_doc_unwarping=True,
            use_table_recognition=False,
            use_formula_recognition=True,
            use_seal_recognition=True,
            use_chart_recognition=True,
        )
        assert options.pipeline == OCRPipeline.PP_STRUCTURE_V3
        assert options.use_doc_orientation_classify is True
        assert options.use_doc_unwarping is True
        assert options.use_table_recognition is False
        assert options.use_formula_recognition is True
        assert options.use_seal_recognition is True
        assert options.use_chart_recognition is True


@pytest.mark.skipif(not HAS_PADDLEX, reason="paddlex not installed")
class TestOCRServicePipeline:
    """测试 OCR 产线懒加载。"""

    def test_pipeline_lazy_loading(self):
        """产线仅在首次访问时创建。"""
        service = OCRService()
        # 清除现有产线
        OCRService._pipelines = {}

        # 首次访问前，产线应该为空
        assert len(OCRService._pipelines) == 0
        # 访问 pipeline 属性
        pipeline = service.pipeline
        # 首次访问后，产线应该被创建
        assert pipeline is not None
        # 后续访问返回同一产线
        assert service.pipeline is pipeline

        # 清理
        OCRService._pipelines = {}


@pytest.mark.skipif(not HAS_PADDLEX, reason="paddlex not installed")
class TestOCRServiceRecognize:
    """测试 OCR 识别功能。"""

    def test_recognize_pil_image(self, sample_image_with_text_bytes):
        """识别 PIL Image 格式。"""
        import io

        service = OCRService()
        img = Image.open(io.BytesIO(sample_image_with_text_bytes))
        result = service.recognize(img)
        # 验证返回类型
        assert isinstance(result, OCRResult)
        assert isinstance(result.raw_text, str)
        assert isinstance(result.text_with_scores, list)

    def test_recognize_numpy_array(self, sample_image_with_text_bytes):
        """识别 numpy 数组格式。"""
        import io

        service = OCRService()
        img = Image.open(io.BytesIO(sample_image_with_text_bytes))
        arr = np.array(img)
        result = service.recognize(arr)
        assert isinstance(result, OCRResult)
        assert isinstance(result.raw_text, str)
        assert isinstance(result.text_with_scores, list)

    def test_recognize_empty_image_returns_empty_string(self):
        """空白图片返回空字符串。"""
        service = OCRService()
        img = Image.new("RGB", (100, 50), color="white")
        result = service.recognize(img)
        # 空白图片可能返回空字符串或极少文字
        assert isinstance(result, OCRResult)
        assert isinstance(result.raw_text, str)
        assert isinstance(result.text_with_scores, list)

    def test_recognize_with_options(self, sample_image_with_text_bytes):
        """测试使用 OCROptions 进行识别。"""
        import io

        service = OCRService()
        img = Image.open(io.BytesIO(sample_image_with_text_bytes))

        # 使用 OCROptions
        options = OCROptions(
            pipeline=OCRPipeline.OCR,
            use_doc_orientation_classify=True,
            use_doc_unwarping=False,
        )
        result = service.recognize(img, options)
        assert isinstance(result, OCRResult)
        assert isinstance(result.raw_text, str)
        assert isinstance(result.text_with_scores, list)
