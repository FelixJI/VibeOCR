"""单次识别标签页"""

import io
import logging
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QBuffer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QHBoxLayout, QPushButton, QSplitter, QVBoxLayout, QWidget

from vibeocr.views.tabs.base_tab import BaseOcrTab
from vibeocr.widgets.preprocess_options_widget import PreprocessOptionsWidget
from vibeocr.widgets.preview_widget import PreviewWidget
from vibeocr.widgets.result_view_widget import ResultViewWidget

logger = logging.getLogger(__name__)


class SingleRecognitionTab(BaseOcrTab):
    """单次识别标签页

    左侧：统一预览（图片/PDF/截图）
    右侧：管道选项 + 结果展示
    """

    SPLITTER_ID = "ocr_tab"

    screenshot_requested = Signal()
    file_open_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._closing = False
        self._pending_pixmap: QPixmap | None = None
        self._pending_file_path: str | None = None
        self._setup_ui()
        self._connect_signals()
        self._init_options_from_preferences(batch=False)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(8, 8, 8, 8)

        self._splitter = QSplitter()

        # 左侧：按钮栏 + 统一预览（包裹在容器中）
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setSpacing(4)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # 操作按钮栏
        action_bar = QWidget()
        action_layout = QHBoxLayout(action_bar)
        action_layout.setContentsMargins(4, 2, 4, 2)
        action_layout.setSpacing(4)

        self._screenshot_btn = QPushButton("截图")
        self._screenshot_btn.setFixedHeight(28)
        self._file_btn = QPushButton("选择文件")
        self._file_btn.setFixedHeight(28)
        self._paste_btn = QPushButton("粘贴")
        self._paste_btn.setFixedHeight(28)
        self._start_btn = QPushButton("开始识别")
        self._start_btn.setFixedHeight(28)
        self._start_btn.setEnabled(False)

        action_layout.addWidget(self._screenshot_btn)
        action_layout.addWidget(self._file_btn)
        action_layout.addWidget(self._paste_btn)
        action_layout.addStretch()
        action_layout.addWidget(self._start_btn)

        left_layout.addWidget(action_bar)

        self._preview_widget = PreviewWidget(
            empty_text="左键点击截图 · 右键点击选择文件\n\n支持图片、PDF 格式"
        )
        left_layout.addWidget(self._preview_widget, stretch=1)

        self._splitter.addWidget(left_panel)

        # 右侧：管道选项 + 结果展示
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setSpacing(6)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self._preprocess_options = PreprocessOptionsWidget()
        right_layout.addWidget(self._preprocess_options)

        self._result_widget = ResultViewWidget()
        right_layout.addWidget(self._result_widget, stretch=1)

        right_panel.setMinimumWidth(300)
        self._splitter.addWidget(right_panel)

        self._splitter.setSizes([400, 500])
        layout.addWidget(self._splitter, stretch=1)
        self.setLayout(layout)

    def _connect_signals(self):
        self._setup_hover_sync()
        self._preview_widget.block_text_edited.connect(self._on_block_text_edited)
        self._preview_widget.block_clicked.connect(self._result_widget.highlight_block)
        self._result_widget.block_edited.connect(self._on_result_block_edited)

        # 转发预览组件的截图/文件请求信号
        self._preview_widget.screenshot_requested.connect(self.screenshot_requested.emit)
        self._preview_widget.file_open_requested.connect(self.file_open_requested.emit)

        # 操作按钮
        self._screenshot_btn.clicked.connect(self.screenshot_requested.emit)
        self._file_btn.clicked.connect(self._on_file_btn_clicked)
        self._paste_btn.clicked.connect(self._on_paste)
        self._start_btn.clicked.connect(self._start_recognition)

    def _on_file_btn_clicked(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        from vibeocr.utils.mime_types import FILE_FILTER_ALL, is_document_file

        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择文件", "",
            f"{FILE_FILTER_ALL};;所有文件 (*)",
        )
        if not file_path:
            return

        self._pending_file_path = file_path
        self._pending_pixmap = None

        if is_document_file(file_path):
            self._preprocess_options.lock_to_document_parsing("当前文件仅支持文档解析")
            self._preview_widget.load_file(file_path)
        else:
            self._preprocess_options.unlock_pipeline()
            pixmap = QPixmap(file_path)
            if not pixmap.isNull():
                self._preview_widget.set_pixmap(pixmap)
                self._pending_pixmap = pixmap

        self._start_btn.setEnabled(True)

    def _on_paste(self) -> None:
        from PySide6.QtGui import QGuiApplication

        clipboard = QGuiApplication.clipboard()
        pixmap = clipboard.pixmap()
        if pixmap.isNull():
            return

        if pixmap.devicePixelRatio() != 1.0:
            pixmap = QPixmap(pixmap)
            pixmap.setDevicePixelRatio(1.0)

        self._preprocess_options.unlock_pipeline()
        self._preview_widget.set_pixmap(pixmap)
        self._pending_pixmap = pixmap
        self._pending_file_path = None
        self._start_btn.setEnabled(True)

    def _on_start(self):
        self._start_recognition()

    def _start_recognition(self) -> None:
        """开始识别：根据待处理的来源执行 OCR"""
        if self._pending_pixmap:
            self.run_ocr(self._pending_pixmap)
        elif self._pending_file_path:
            self.process_file(self._pending_file_path)

    def set_closing(self, closing: bool) -> None:
        self._closing = closing

    # ── 公共接口（由 MainWindow 调用）──

    def set_pixmap(self, pixmap) -> None:
        self._preview_widget.set_pixmap(pixmap)

    def pixmap(self):
        return self._preview_widget.pixmap()

    def _build_options_from_ui(self):
        return self._preprocess_options.get_options()

    def _check_ocr_ready(self) -> bool:
        if self._ocr_service is None and self._paddlex_service is None:
            logger.debug("OCR 服务未就绪")
            return False
        return True

    # ── OCR 执行 ──

    def run_ocr(self, pixmap: QPixmap, options=None) -> None:
        """执行 OCR 识别（入口方法，由 MainWindow 调用）"""
        from vibeocr.services import USE_SUBPROCESS

        self._preprocess_options.unlock_pipeline()

        if pixmap.devicePixelRatio() != 1.0:
            pixmap = QPixmap(pixmap)
            pixmap.setDevicePixelRatio(1.0)

        self._result_widget.clear()

        if options is None:
            options = self._build_options_from_ui()

        # 首次使用提示
        pipeline_val = options.pipeline.value
        if pipeline_val in ("OCR", "table_recognition", "formula_recognition"):
            from vibeocr.pipeline_status import is_pipeline_ever_succeeded
            from vibeocr.env_manager import get_project_root
            if not is_pipeline_ever_succeeded(pipeline_val, get_project_root()):
                self._result_widget._ensure_web_view().setHtml(
                    '<div style="display:flex;align-items:center;justify-content:center;'
                    'height:100%;color:#666;font-size:14px;">'
                    '<p>正在识别，首次使用可能需要下载模型，请耐心等待…</p></div>'
                )

        QApplication.processEvents()

        buffer = QBuffer()
        buffer.open(QBuffer.OpenModeFlag.ReadWrite)
        pixmap.save(buffer, "PNG")
        image_data = bytes(buffer.data().data())
        buffer.close()

        if USE_SUBPROCESS:
            from vibeocr.utils.qt_async import run_coroutine

            run_coroutine(self._perform_ocr_async(image_data, options))
        else:
            try:
                pil_image = Image.open(io.BytesIO(image_data))
                import numpy as np

                image_array = np.array(pil_image)
                from vibeocr.services import get_ocr_service

                ocr_service = get_ocr_service()
                result = ocr_service.recognize(image_array, options)
                self._on_ocr_finished(result)
            except Exception as e:
                logger.error(f"OCR 识别失败: {e}", exc_info=True)
                self._on_ocr_error(str(e))

    def process_file(self, file_path: str) -> None:
        """处理文件（由 MainWindow 调用，支持 PDF/Office/图片）"""
        from vibeocr.utils.mime_types import guess_mime_from_filename, is_document_file

        path = Path(file_path)
        if not path.exists():
            return

        if is_document_file(file_path):
            data = path.read_bytes()
            mime_type = guess_mime_from_filename(file_path)
            self._run_ocr_with_data(data, mime_type, path.name)
        else:
            pixmap = QPixmap(file_path)
            if not pixmap.isNull():
                self._preview_widget.set_pixmap(pixmap)
                self.run_ocr(pixmap)

    def _run_ocr_with_data(self, data: bytes, mime_type: str, filename: str) -> None:
        """使用原始文件数据进行 OCR"""
        from vibeocr.services import USE_SUBPROCESS
        from vibeocr.services.ocr_service import OCRPipeline

        self._result_widget.clear()
        QApplication.processEvents()

        options = self._build_options_from_ui()
        options.pipeline = OCRPipeline.DOCUMENT_PARSING

        if USE_SUBPROCESS:
            from vibeocr.utils.qt_async import run_coroutine

            run_coroutine(self._perform_ocr_with_data_async(data, mime_type, filename, options))
        else:
            try:
                from vibeocr.services import get_ocr_service

                ocr_service = get_ocr_service()
                result = ocr_service.recognize(data, options)
                self._on_ocr_finished(result)
            except Exception as e:
                logger.error(f"OCR 识别失败: {e}", exc_info=True)
                self._on_ocr_error(str(e))

    async def _perform_ocr_async(self, image_data: bytes, options) -> None:
        try:
            if self._closing:
                return

            from vibeocr.services import get_ocr_service

            ocr_service = get_ocr_service()

            if self._closing:
                return

            if hasattr(ocr_service, "is_ready"):
                ready = ocr_service.is_ready()
                if not ready:
                    raise RuntimeError("OCR 服务未就绪，请稍后再试")

            import asyncio

            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: ocr_service.recognize(image_data, options)
            )

            if self._closing:
                return

            self._on_ocr_finished(result)
        except Exception as e:
            if self._closing:
                return
            logger.error(f"[异步OCR] 识别失败: {e}", exc_info=True)
            self._on_ocr_error(str(e))

    async def _perform_ocr_with_data_async(
        self, data: bytes, mime_type: str, filename: str, options
    ) -> None:
        try:
            if self._closing:
                return

            from vibeocr.services import get_ocr_service

            ocr_service = get_ocr_service()

            if self._closing:
                return

            if hasattr(ocr_service, "is_ready"):
                ready = ocr_service.is_ready()
                if not ready:
                    raise RuntimeError("OCR 服务未就绪，请稍后再试")

            original_to_dict = options.to_dict
            options.to_dict = lambda: {  # type: ignore[assignment]
                **original_to_dict(),
                "mime_type": mime_type,
                "file_path": filename,
            }

            try:
                import asyncio

                result = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: ocr_service.recognize(data, options)
                )
            finally:
                options.to_dict = original_to_dict  # type: ignore[assignment]

            if self._closing:
                return

            self._on_ocr_finished(result)
        except Exception as e:
            if self._closing:
                return
            logger.error(f"[异步OCR] 识别失败: {e}", exc_info=True)
            self._on_ocr_error(str(e))

    def _on_result_block_edited(self, index: int, new_text: str) -> None:
        """右侧结果块被编辑后同步更新数据模型"""
        if not self._current_ocr_result or index < 0:
            return
        result = self._current_ocr_result

        if not result.content_list or index >= len(result.content_list):
            return

        cl_block = result.content_list[index]
        old_text = cl_block.get("text", "")
        if old_text == new_text:
            return

        # 更新 content_list
        cl_block["text"] = new_text
        block_type = cl_block.get("type", "text")
        if block_type == "list" and "list_items" in cl_block:
            cl_block["list_items"] = new_text.split("\n")
        elif block_type == "code":
            cl_block["code_body"] = new_text

        # 查找并更新对应的 text_block
        for tb in result.text_blocks:
            if getattr(tb, "content_index", None) == index:
                tb.text = new_text
                tb.is_manually_edited = True
                if tb.content_index is not None and tb.content_index < len(result.text_with_scores):
                    score = result.text_with_scores[tb.content_index][1]
                    result.text_with_scores[tb.content_index] = (new_text, score)
                break

        # 全量重建 raw_text，避免 str.replace 子串误匹配
        result.raw_text = "\n".join(b.text for b in result.text_blocks if b.text)

        # 同步更新 markdown_text / html_text
        if old_text:
            if result.markdown_text and old_text in result.markdown_text:
                result.markdown_text = result.markdown_text.replace(old_text, new_text, 1)
            if result.html_text and old_text in result.html_text:
                result.html_text = result.html_text.replace(old_text, new_text, 1)

        # 刷新左侧 overlay（显示手动修改标记）
        if self._preview_widget:
            self._preview_widget.set_text_blocks(result.text_blocks)

    def _on_ocr_finished(self, result) -> None:
        """OCR 完成回调"""
        self._current_ocr_result = result

        char_count = len(result.raw_text) if result.raw_text else 0
        block_count = len(result.text_with_scores)
        logger.info(f"OCR 完成: {block_count} 个文本块, {char_count} 个字符")

        # 设置文本块到预览（置信度模式）
        self._preview_widget.set_text_blocks(result.text_blocks)

        # 显示结果（包括 content_list 块类型模式）
        self._display_result(result)

    def _on_ocr_error(self, error_msg: str) -> None:
        """OCR 失败回调"""
        self._current_ocr_result = None
        self._result_widget.clear()
        self._result_widget._ensure_web_view().setHtml(
            f"<p style='color:#f44336;'>识别失败：{error_msg}</p>"
        )

    def show_waiting_message(self, message: str) -> None:
        """在结果面板显示等待提示（预加载排队时调用）"""
        self._result_widget._ensure_web_view().setHtml(
            f'<div style="display:flex;align-items:center;justify-content:center;'
            f'height:100%;color:#666;font-size:14px;">'
            f'<p>{message}</p></div>'
        )
