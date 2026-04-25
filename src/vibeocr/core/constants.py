"""全局常量定义

集中管理应用程序中使用的所有常量。

注意: OCRPipeline 枚举已移至 pipelines.py，此文件不再定义管道类型。
"""

from enum import Enum, auto

try:
    from vibeocr import __version__ as _APP_VERSION
except ImportError:
    _APP_VERSION = "0.1.0"


class FileType(Enum):
    """支持的文件类型"""

    PDF = auto()
    IMAGE = auto()
    DOC = auto()
    DOCX = auto()
    UNKNOWN = auto()


class Constants:
    """应用程序常量"""

    # 应用程序信息
    APP_NAME = "VibeOCR"
    APP_VERSION = _APP_VERSION
    APP_DESCRIPTION = "基于 PaddleOCR 的文档识别工具"

    # 窗口尺寸
    WINDOW_MIN_WIDTH = 1200
    WINDOW_MIN_HEIGHT = 800
    WINDOW_DEFAULT_WIDTH = 1400
    WINDOW_DEFAULT_HEIGHT = 900

    # 支持文件格式
    SUPPORTED_IMAGE_FORMATS = [
        "*.png",
        "*.jpg",
        "*.jpeg",
        "*.bmp",
        "*.tiff",
        "*.tif",
        "*.webp",
    ]
    SUPPORTED_PDF_FORMATS = ["*.pdf"]
    SUPPORTED_DOC_FORMATS = ["*.doc", "*.docx"]

    # 共享内存配置
    DEFAULT_SHM_SIZE = 10 * 1024 * 1024  # 10MB
    DEFAULT_SHM_LOG_SIZE = 1 * 1024 * 1024  # 1MB
    WORKER_TIMEOUT = 300.0  # 5分钟
    WORKER_START_TIMEOUT = 30.0  # 30秒

    # 批量处理配置
    DEFAULT_BATCH_SIZE = 8
    MAX_BATCH_SIZE = 32
    BATCH_QUEUE_TIMEOUT = 5.0

    # 日志配置
    LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

    # 样式常量
    class Style:
        """UI 样式常量"""

        BORDER_RADIUS = 6
        BORDER_RADIUS_LARGE = 8
        PADDING_SMALL = 8
        PADDING_MEDIUM = 12
        PADDING_LARGE = 16
        SPACING_SMALL = 8
        SPACING_MEDIUM = 12
        SPACING_LARGE = 16

    # 超时配置（秒）
    class Timeout:
        """超时配置"""

        OCR_RECOGNIZE = 60.0
        PIPELINE_PRELOAD = 120.0
        FILE_OPERATION = 10.0
        SHUTDOWN = 5.0


# 向后兼容的常量导出
DEFAULT_SHM_SIZE = Constants.DEFAULT_SHM_SIZE
SHM_TIMEOUT = Constants.WORKER_TIMEOUT
SHORT_DELAY_MS = 100
MEDIUM_DELAY_MS = 500
LONG_DELAY_MS = 1000
TOAST_DELAY_MS = 3000
DEFAULT_BATCH_SIZE = Constants.DEFAULT_BATCH_SIZE
MAX_BATCH_SIZE = Constants.MAX_BATCH_SIZE
MIN_BATCH_SIZE = 1
DEFAULT_SPACING = Constants.Style.SPACING_MEDIUM
DEFAULT_MARGIN = Constants.Style.PADDING_MEDIUM

# 颜色常量（向后兼容 - Material Design）
COLOR_PRIMARY = "#2196F3"
COLOR_SUCCESS = "#4CAF50"
COLOR_WARNING = "#FF9800"
COLOR_ERROR = "#F44336"
COLOR_TEXT = "#212121"
COLOR_BORDER = "#E0E0E0"
COLOR_BACKGROUND = "#FFFFFF"
COLOR_HOVER = "#F5F5F5"


del _APP_VERSION


class WindowsColors:
    """Windows/Office 风格配色方案

    用于 main_window.py 中的按钮样式。
    """

    # 主色调（Windows 蓝）
    PRIMARY = "#0078d4"
    PRIMARY_HOVER = "#106ebe"

    # 成功色（绿色）
    SUCCESS = "#107c10"
    SUCCESS_HOVER = "#0b6a0b"

    # 预处理色（橙色）
    ACCENT = "#f7630c"
    ACCENT_HOVER = "#d6550a"

    # 背景色
    BACKGROUND = "#f0f0f0"
    BACKGROUND_HOVER = "#e0e0e0"

    # 边框色
    BORDER = "#c0c0c0"

    # 文本色
    TEXT = "#333333"
    TEXT_LIGHT = "#ffffff"
