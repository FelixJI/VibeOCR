"""Tab 基类

提供所有 OCR Tab 的基础功能。
"""

import logging
from abc import abstractmethod
from typing import TYPE_CHECKING, Optional

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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ocr_service: OCRServiceSubprocess | None = None
        self._paddlex_service = None
        self._current_ocr_result = None
        self._preview_widget = None
        self._result_widget = None
        self._preprocess_options = None
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
        logger.info(
            f"[{self.__class__.__name__}] OCR 服务已设置: {service is not None}"
        )

        # 子类可以重写此方法来响应服务变化
        self._on_service_changed(service)

    def _on_service_changed(self, service: Optional["OCRServiceSubprocess"]) -> None:
        """OCR 服务变化回调

        子类可以重写此方法来响应服务变化。

        Args:
            service: 新的 OCR 服务实例
        """

    def set_paddlex_service(self, service) -> None:
        """设置 PaddleX 服务"""
        self._paddlex_service = service

    def _get_service_for_pipeline(self, options):
        """根据管道类型路由到对应的服务"""
        from vibeocr.core.pipelines import OCRPipeline

        if options.pipeline == OCRPipeline.DOCUMENT_PARSING:
            return self._ocr_service
        return self._paddlex_service

    def _build_content_list(self, result) -> list[dict]:
        """从 OCRResult 构建 content_list（含 bbox）"""
        content_list = getattr(result, "content_list", [])
        text_blocks = getattr(result, "text_blocks", [])

        if content_list:
            for tb in text_blocks:
                cl_idx = getattr(tb, "content_index", None)
                if cl_idx is not None and cl_idx < len(content_list) and tb.bbox:
                    if "bbox" not in content_list[cl_idx]:
                        content_list[cl_idx]["bbox"] = list(tb.bbox)
            return content_list

        if not text_blocks:
            return []

        built = []
        for b in text_blocks:
            entry: dict = {"type": "text", "text": b.text}
            if b.bbox:
                entry["bbox"] = list(b.bbox)
            if b.page_idx is not None:
                entry["page_idx"] = b.page_idx
            built.append(entry)
        return built

    def _display_result(self, result) -> None:
        """显示 OCR 结果到结果面板和预览面板"""
        self._current_ocr_result = result
        if self._result_widget:
            self._result_widget.display_result(result)
        if self._preview_widget:
            content_list = self._build_content_list(result)
            self._preview_widget.set_content_list(content_list)

    @abstractmethod
    def _setup_ui(self) -> None:
        """设置 UI 布局

        子类必须实现此方法。
        """

    @abstractmethod
    def _connect_signals(self) -> None:
        """连接信号槽

        子类必须实现此方法。
        """

    @abstractmethod
    def _on_start(self) -> None:
        """开始处理

        子类必须实现此方法。
        """

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
