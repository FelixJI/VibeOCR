"""Tab 基类

提供所有 OCR Tab 的基础功能。
"""

import logging
from abc import abstractmethod
from typing import Any

from PySide6.QtWidgets import QWidget

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
        self._ocr_service: Any = None
        self._paddlex_service: Any = None
        self._current_ocr_result: Any = None
        self._preview_widget: Any = None
        self._result_widget: Any = None
        self._preprocess_options: Any = None
        self._is_processing = False

        # 子类在 __init__ 中调用以下方法
        # self._setup_ui()
        # self._connect_signals()

    @property
    def ocr_service(self) -> Any:
        """获取 OCR 服务"""
        return self._ocr_service

    @property
    def is_processing(self) -> bool:
        """检查是否正在处理"""
        return self._is_processing

    def set_ocr_service(self, service: Any) -> None:
        """设置 OCR 服务

        Args:
            service: OCR 服务实例
        """
        self._ocr_service = service
        logger.debug(
            f"[{self.__class__.__name__}] OCR 服务已设置: {service is not None}"
        )

        # 子类可以重写此方法来响应服务变化
        self._on_service_changed(service)

    def _on_service_changed(self, service: Any) -> None:
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
        from vibeocr.contracts.pipelines import OCRPipeline

        if options.pipeline == OCRPipeline.DOCUMENT_PARSING:
            return self._ocr_service
        return self._paddlex_service

    def _build_content_list(self, result) -> list[dict]:
        """从 OCRResult 构建 content_list（含归一化 bbox）"""
        from vibeocr.models.ocr_result import normalize_bbox

        content_list = getattr(result, "content_list", [])
        text_blocks = getattr(result, "text_blocks", [])
        img_w = getattr(result, "image_width", 0)
        img_h = getattr(result, "image_height", 0)

        if content_list:
            for cl_block in content_list:
                bbox = cl_block.get("bbox")
                if bbox and len(bbox) >= 4:
                    cl_block["bbox"] = list(normalize_bbox(bbox[:4], img_w, img_h))
            for tb in text_blocks:
                cl_idx = getattr(tb, "content_index", None)
                if cl_idx is not None and cl_idx < len(content_list):
                    if tb.bbox and "bbox" not in content_list[cl_idx]:
                        content_list[cl_idx]["bbox"] = list(
                            normalize_bbox(tb.bbox, img_w, img_h)
                        )
                    # 表格/图片/印章等结构识别块没有文本置信度（pipeline 里 score
                    # 是占位值），不写入 confidence，避免在 hover title 里显示
                    # 误导性的"置信度: 90%"。文本/标题块保留真实置信度。
                    cl_type = content_list[cl_idx].get("type", "")
                    if cl_type not in ("table", "image", "figure", "chart", "seal"):
                        content_list[cl_idx]["confidence"] = tb.score
            return content_list

        if not text_blocks:
            return []

        built = []
        for b in text_blocks:
            entry: dict = {"type": "text", "text": b.text, "confidence": b.score}
            if b.bbox:
                entry["bbox"] = list(normalize_bbox(b.bbox, img_w, img_h))
            if b.page_idx is not None:
                entry["page_idx"] = b.page_idx
            built.append(entry)
        return built

    def _display_result(self, result) -> None:
        """显示 OCR 结果到结果面板和预览面板"""
        self._current_ocr_result = result
        # 统一构建 content_list 并回填到 result，保证右侧结果区与左侧预览、
        # 编辑回调用同一套索引。通用 OCR 管道的 content_list 为空（只有 text_blocks），
        # 若不回填，display_result 会走 raw_text 的 <pre> 分支，无法按块编辑；
        # _on_result_block_edited 按 content_index 反查 text_block 也会失败。
        content_list = self._build_content_list(result)
        if content_list:
            result.content_list = content_list
            # 为通用 OCR（text_blocks 无 content_index）补建索引，使编辑回调能反查。
            # 结构化管道（table/formula/mineru）的 content_index 在 pipeline 已设好，
            # 这里只在缺失时补，不覆盖。
            for i, tb in enumerate(result.text_blocks):
                if getattr(tb, "content_index", None) is None and i < len(content_list):
                    tb.content_index = i
        if self._result_widget:
            self._result_widget.display_result(result)
        if self._preview_widget:
            self._preview_widget.set_content_list(content_list)

    def _setup_hover_sync(self) -> None:
        """设置预览 ↔ 结果的双向悬停联动"""
        if not self._result_widget or not self._preview_widget:
            return
        self._result_widget.block_hovered.connect(self._preview_widget.highlight_block)
        self._result_widget.block_unhovered.connect(
            lambda: self._preview_widget.highlight_block(-1)
        )
        self._preview_widget.block_hovered.connect(self._result_widget.highlight_block)
        self._preview_widget.block_unhovered.connect(
            self._result_widget.clear_highlight
        )

    def _on_block_text_edited(self, index: int, new_text: str) -> None:
        """文本块被编辑后同步更新结果和展示"""
        if not self._current_ocr_result or index < 0:
            return
        result = self._current_ocr_result
        if index >= len(result.text_blocks):
            return

        old_text = result.text_blocks[index].text
        if old_text == new_text:
            return

        result.text_blocks[index].text = new_text
        result.text_blocks[index].is_manually_edited = True

        if index < len(result.text_with_scores):
            score = result.text_with_scores[index][1]
            result.text_with_scores[index] = (new_text, score)

        cl_idx = None
        if result.content_list:
            cl_idx = getattr(result.text_blocks[index], "content_index", None)
            if cl_idx is not None and cl_idx < len(result.content_list):
                cl_block = result.content_list[cl_idx]
                block_type = cl_block.get("type", "text")
                if block_type == "table":
                    import html as html_lib

                    table_body = cl_block.get("table_body", "")
                    cl_block["table_body"] = table_body.replace(
                        html_lib.escape(old_text), html_lib.escape(new_text), 1
                    )
                else:
                    cl_block["text"] = new_text

        result.raw_text = "\n".join(b.text for b in result.text_blocks if b.text)

        if result.markdown_text and result.markdown_text != old_text:
            result.markdown_text = result.markdown_text.replace(old_text, new_text, 1)
        else:
            result.markdown_text = result.raw_text

        if result.html_text and result.html_text != old_text:
            result.html_text = result.html_text.replace(old_text, new_text, 1)
        else:
            result.html_text = result.raw_text

        if self._preview_widget:
            self._preview_widget.set_text_blocks(result.text_blocks)
        if self._result_widget:
            if cl_idx is not None:
                self._result_widget.update_block_text(cl_idx, new_text)
            else:
                self._result_widget.display_result(result)

    def _on_table_block_edited(self, content_index: int, new_html: str) -> None:
        """表格块被网格编辑后同步更新结果和展示。

        与 ``_on_block_text_edited``（普通文本内联编辑）并列，专处理左侧画布
        双击表格弹出的网格编辑器结果：整体替换 ``content_list`` 中的
        ``table_body`` 及对应 text_block 的文本，重算纯文本/markdown/html，
        并反向同步右侧 HTML 视图。
        """
        if not self._current_ocr_result:
            return
        result = self._current_ocr_result
        if not result.content_list or not (
            0 <= content_index < len(result.content_list)
        ):
            return

        cl_block = result.content_list[content_index]
        old_html = cl_block.get("table_body", "")
        if old_html == new_html:
            return

        # 更新 content_list 的 table_body，并从新 HTML 提取纯文本回填 text 字段
        cl_block["table_body"] = new_html
        cl_block["text"] = self._table_html_to_plain_text(new_html)

        # 同步匹配的 text_block（按 content_index 反查）
        for tb in result.text_blocks:
            if getattr(tb, "content_index", None) == content_index:
                tb.text = new_html
                tb.is_manually_edited = True
                if content_index < len(result.text_with_scores):
                    score = result.text_with_scores[content_index][1]
                    result.text_with_scores[content_index] = (new_html, score)
                break

        result.raw_text = "\n".join(b.text for b in result.text_blocks if b.text)

        # markdown/html 难以精确替换单个表格，全量重建最稳妥
        if result.has_content_list:
            from vibeocr.utils.html_tables import (
                _extract_table_html,
                _html_table_to_markdown,
            )

            md_parts = []
            for blk in result.content_list:
                if blk.get("type") == "table":
                    md = _html_table_to_markdown(
                        _extract_table_html(blk.get("table_body", ""))
                    )
                    if md:
                        md_parts.append(md)
                else:
                    t = blk.get("text", "")
                    if t:
                        md_parts.append(t)
            result.markdown_text = "\n\n".join(md_parts)
            from vibeocr.utils.markdown_converter import markdown_to_html

            result.html_text = (
                markdown_to_html(result.markdown_text) if result.markdown_text else ""
            )

        if self._preview_widget:
            self._preview_widget.set_content_list(result.content_list)
        if self._result_widget:
            # update_block_text 现已支持 table 块的 DOM 重建
            self._result_widget.update_block_text(content_index, new_html)

    @staticmethod
    def _table_html_to_plain_text(html: str) -> str:
        """从表格 HTML 提取纯文本（供 content_list 的 text 字段）。"""
        import re

        text = re.sub(r"<[^>]+>", " ", html)
        return re.sub(r"\s+", " ", text).strip()

    def _init_options_from_preferences(self, *, batch: bool = False) -> None:
        """从 OCRPreferences 恢复选项，建立管道切换同步"""
        if not self._preprocess_options:
            return
        try:
            from vibeocr.utils.ocr_preferences import OCRPreferences

            prefs = OCRPreferences.instance()
        except RuntimeError:
            return
        if batch:
            self._preprocess_options.set_options(prefs.get_batch_options())
            self._preprocess_options.options_changed.connect(
                lambda opts: OCRPreferences.instance().set_batch_options(opts)
            )
            prefs.batch_options_changed.connect(self._preprocess_options.set_options)
        else:
            source = "main"
            default_pipeline = self._preprocess_options.get_current_pipeline()
            self._preprocess_options.set_options(
                prefs.get_pipeline_options(source, default_pipeline)
            )
            self._preprocess_options.pipeline_switching.connect(
                lambda old_pipeline, opts: (
                    OCRPreferences.instance().set_pipeline_options(
                        source, old_pipeline, opts
                    )
                )
            )
            self._preprocess_options.pipeline_switched.connect(
                lambda new_pipeline: self._preprocess_options.set_options(
                    OCRPreferences.instance().get_pipeline_options(source, new_pipeline)
                )
            )
            self._preprocess_options.options_changed.connect(
                lambda opts: OCRPreferences.instance().set_pipeline_options(
                    source, opts.pipeline, opts
                )
            )

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
