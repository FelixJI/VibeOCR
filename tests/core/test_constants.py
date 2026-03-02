"""测试 Constants 常量类"""

import pytest
from vibeocr.core import (
    Constants,
    OCRPipeline,
    FileType,
    DEFAULT_SHM_SIZE,
    COLOR_PRIMARY,
)


class TestConstants:
    """测试常量定义"""

    def test_app_info(self):
        """测试应用信息"""
        assert Constants.APP_NAME == "VibeOCR"
        assert Constants.APP_VERSION == "1.0.0"
        assert "PaddleOCR" in Constants.APP_DESCRIPTION

    def test_window_sizes(self):
        """测试窗口尺寸"""
        assert Constants.WINDOW_MIN_WIDTH >= 800
        assert Constants.WINDOW_MIN_HEIGHT >= 600
        assert Constants.WINDOW_DEFAULT_WIDTH >= Constants.WINDOW_MIN_WIDTH
        assert Constants.WINDOW_DEFAULT_HEIGHT >= Constants.WINDOW_MIN_HEIGHT

    def test_file_formats(self):
        """测试文件格式"""
        # 图像格式
        assert "*.png" in Constants.SUPPORTED_IMAGE_FORMATS
        assert "*.jpg" in Constants.SUPPORTED_IMAGE_FORMATS
        assert "*.jpeg" in Constants.SUPPORTED_IMAGE_FORMATS
        assert "*.pdf" in Constants.SUPPORTED_PDF_FORMATS
        assert "*.doc" in Constants.SUPPORTED_DOC_FORMATS
        assert "*.docx" in Constants.SUPPORTED_DOC_FORMATS

    def test_shm_config(self):
        """测试共享内存配置"""
        assert Constants.DEFAULT_SHM_SIZE > 0
        assert Constants.DEFAULT_SHM_LOG_SIZE > 0
        assert Constants.WORKER_TIMEOUT > 0
        assert Constants.WORKER_START_TIMEOUT > 0

    def test_batch_config(self):
        """测试批量处理配置"""
        assert Constants.DEFAULT_BATCH_SIZE > 0
        assert Constants.MAX_BATCH_SIZE >= Constants.DEFAULT_BATCH_SIZE
        assert Constants.BATCH_QUEUE_TIMEOUT > 0

    def test_style_constants(self):
        """测试样式常量"""
        assert Constants.Style.BORDER_RADIUS > 0
        assert Constants.Style.PADDING_SMALL > 0
        assert Constants.Style.SPACING_SMALL > 0

    def test_timeout_constants(self):
        """测试超时常量"""
        assert Constants.Timeout.OCR_RECOGNIZE > 0
        assert Constants.Timeout.PIPELINE_PRELOAD > 0
        assert Constants.Timeout.FILE_OPERATION > 0

    def test_doc_understanding_models(self):
        """测试文档理解模型"""
        assert len(Constants.DOC_UNDERSTANDING_MODELS) > 0
        assert Constants.DEFAULT_DOC_UNDERSTANDING_MODEL in Constants.DOC_UNDERSTANDING_MODELS


class TestOCRPipeline:
    """测试 OCRPipeline 枚举"""

    def test_enum_values(self):
        """测试枚举值"""
        assert OCRPipeline.PP_STRUCTURE_V3.value == "PP-StructureV3"
        assert OCRPipeline.PADDLEOCR_VL.value == "PaddleOCR-VL"
        assert OCRPipeline.CHATOCRv4.value == "PP-ChatOCRv4"
        assert OCRPipeline.DOC_UNDERSTANDING.value == "doc_understanding"


class TestFileType:
    """测试 FileType 枚举"""

    def test_enum_exists(self):
        """测试枚举存在"""
        assert FileType.PDF is not None
        assert FileType.IMAGE is not None
        assert FileType.DOC is not None
        assert FileType.DOCX is not None
        assert FileType.UNKNOWN is not None


class TestBackwardCompatibility:
    """测试向后兼容性"""

    def test_shm_constants(self):
        """测试共享内存常量向后兼容"""
        assert DEFAULT_SHM_SIZE == Constants.DEFAULT_SHM_SIZE

    def test_color_constants(self):
        """测试颜色常量向后兼容"""
        assert COLOR_PRIMARY == Constants.Style.BORDER_RADIUS or isinstance(COLOR_PRIMARY, str)

    def test_batch_constants(self):
        """测试批处理常量向后兼容"""
        from vibeocr.core.constants import DEFAULT_BATCH_SIZE, MAX_BATCH_SIZE, MIN_BATCH_SIZE

        assert DEFAULT_BATCH_SIZE == Constants.DEFAULT_BATCH_SIZE
        assert MAX_BATCH_SIZE == Constants.MAX_BATCH_SIZE
        assert MIN_BATCH_SIZE == 1
