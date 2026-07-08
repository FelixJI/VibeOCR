"""测试 Constants 常量类"""

import re

from vibeocr import __version__
from vibeocr.core import (
    DEFAULT_SHM_SIZE,
    Constants,
    FileType,
    OCRPipeline,
)
from vibeocr.ui import theme

# Semver 主版本号（major.minor.patch），不带预发布后缀。
# 用于校验 APP_VERSION 是合法版本号，而非断言某个具体值（会随 bump 变化）。
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


class TestConstants:
    """测试常量定义"""

    def test_app_info(self):
        """测试应用信息

        APP_VERSION 不硬编码断言具体值（会随 bump 变化），只验证：
        - 符合 major.minor.patch 格式；
        - 与 vibeocr.__version__ 一致（Constants 从该常量加载版本）。
        """
        assert Constants.APP_NAME == "VibeOCR"
        assert _SEMVER_RE.match(Constants.APP_VERSION), (
            f"APP_VERSION 非法版本号: {Constants.APP_VERSION!r}"
        )
        assert __version__ == Constants.APP_VERSION
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
        # WORKER_TIMEOUT / WORKER_START_TIMEOUT 已移至 Constants.Timeout
        assert Constants.Timeout.WORKER_TIMEOUT > 0
        assert Constants.Timeout.WORKER_START_BASE > 0

    def test_shm_transport_budget_not_smaller_than_page_batch(self):
        """传输批须 ≥ 计算批：SHM 单消息预算能装下完整页批（性能2）。

        预算 = 0.7 × (SHM − 9)。单张 300dpi A4 PNG 上限取 4MB，页批 16 页。
        要求 budget ≥ 16 × 4MB，否则传输批(<16)会卡住计算批(8)，GPU 喂不饱。
        """
        budget = int(0.7 * (Constants.DEFAULT_SHM_SIZE - 9))
        max_page_png = 4 * 1024 * 1024  # 4MB 上限
        page_batch = 16
        assert budget >= page_batch * max_page_png, (
            f"SHM 预算 {budget} 字节不足以装下 {page_batch} 页 × {max_page_png} 字节；"
            "传输批会小于计算批，卡住 GPU"
        )

    def test_batch_config(self):
        """测试批量处理配置"""
        assert Constants.DEFAULT_BATCH_SIZE > 0
        assert Constants.MAX_BATCH_SIZE >= Constants.DEFAULT_BATCH_SIZE
        # BATCH_QUEUE_TIMEOUT 已移至 Constants.Timeout.BATCH_QUEUE
        assert Constants.Timeout.BATCH_QUEUE > 0

    def test_style_constants(self):
        """测试样式常量"""
        assert Constants.Style.BORDER_RADIUS > 0
        assert Constants.Style.PADDING_SMALL > 0
        assert Constants.Style.SPACING_SMALL > 0

    def test_timeout_constants(self):
        """测试超时常量"""
        T = Constants.Timeout
        # 向后兼容别名仍存在
        assert T.OCR_RECOGNIZE > 0
        assert T.PIPELINE_PRELOAD > 0
        assert T.FILE_OPERATION > 0
        # 新增的语义化常量
        assert T.RECOGNIZE_CACHED > 0
        assert T.RECOGNIZE_UNCACHED > 0
        assert T.DOCUMENT_PARSING > 0
        assert T.PRELOAD_CACHED > 0
        assert T.PRELOAD_UNCACHED > 0
        assert T.WORKER_TIMEOUT > 0
        assert T.WORKER_START > 0
        assert T.SHUTDOWN > 0
        assert T.MINERU_HTTP_TOTAL > 0
        assert T.MINERU_MODEL_DOWNLOAD > 0
        # 关键语义关系:未缓存必须 > 已缓存(给下载留时间)
        assert T.RECOGNIZE_UNCACHED > T.RECOGNIZE_CACHED
        assert T.PRELOAD_UNCACHED > T.PRELOAD_CACHED
        # 毫秒子类
        assert T.Ms.PDF_WORKER_CANCEL > 0
        assert T.Ms.SUBPROCESS_SHUTDOWN > 0


class TestOCRPipeline:
    """测试 OCRPipeline 枚举"""

    def test_enum_values(self):
        """测试枚举值"""
        assert OCRPipeline.OCR.value == "OCR"
        assert OCRPipeline.PP_STRUCTURE_V3.value == "PP-StructureV3"
        assert OCRPipeline.DOCUMENT_PARSING.value == "MinerU"


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
        """颜色常量已迁移至 theme 模块"""
        assert theme.Colors.accent.startswith("#")
        assert theme.Colors.bg.startswith("#")

    def test_batch_constants(self):
        """测试批处理常量向后兼容"""
        from vibeocr.core.constants import (
            DEFAULT_BATCH_SIZE,
            MAX_BATCH_SIZE,
            MIN_BATCH_SIZE,
        )

        assert DEFAULT_BATCH_SIZE == Constants.DEFAULT_BATCH_SIZE
        assert MAX_BATCH_SIZE == Constants.MAX_BATCH_SIZE
        assert MIN_BATCH_SIZE == 1
