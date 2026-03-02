"""Tab 基类

提供所有 OCR Tab 的基础功能。
"""

import logging
from abc import abstractmethod
from typing import Optional, TYPE_CHECKING

from PySide6.QtWidgets import QWidget

if TYPE_CHECKING:
    from vibeocr.services.ocr_service_subprocess import OCRServiceSubprocess

logger = logging.getLogger(__name__)


class BaseOcrTab(QWidget):
    """OCR Tab 基类

    提供所有 OCR Tab 的公共接口和基础功能。

    子类需要实现：
    - _setup_ui(): 设置 UI 布局
    - _connect_signals(): 连接信号槽
    - _on_start(): 开始处理
    - _on_cancel(): 取消处理（可选）
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._ocr_service: Optional["OCRServiceSubprocess"] = None
        self._is_processing = False

        # 子类在 __init__ 中调用以下方法
        # self._setup_ui()
        # self._connect_signals()

    @property
    def ocr_service(self) -> Optional["OCRServiceSubprocess"]:
        """获取 OCR 服务"""
        return self._ocr_service

    @property
    def is_processing(self) -> bool:
        """检查是否正在处理"""
        return self._is_processing

    def set_ocr_service(self, service: Optional["OCRServiceSubprocess"]) -> None:
        """设置 OCR 服务

        Args:
            service: OCR 服务实例
        """
        self._ocr_service = service
        logger.info(f"[{self.__class__.__name__}] OCR 服务已设置: {service is not None}")

        # 子类可以重写此方法来响应服务变化
        self._on_service_changed(service)

    def _on_service_changed(self, service: Optional["OCRServiceSubprocess"]) -> None:
        """OCR 服务变化回调

        子类可以重写此方法来响应服务变化。

        Args:
            service: 新的 OCR 服务实例
        """
        pass

    @abstractmethod
    def _setup_ui(self) -> None:
        """设置 UI 布局

        子类必须实现此方法。
        """
        pass

    @abstractmethod
    def _connect_signals(self) -> None:
        """连接信号槽

        子类必须实现此方法。
        """
        pass

    @abstractmethod
    def _on_start(self) -> None:
        """开始处理

        子类必须实现此方法。
        """
        pass

    def _on_cancel(self) -> None:
        """取消处理

        子类可以重写此方法来实现取消功能。
        """
        logger.warning(f"[{self.__class__.__name__}] 取消功能未实现")

    def _set_processing(self, processing: bool) -> None:
        """设置处理状态

        Args:
            processing: 是否正在处理
        """
        self._is_processing = processing
