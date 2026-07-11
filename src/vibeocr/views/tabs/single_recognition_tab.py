"""单次识别标签页"""

import io
import logging
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QBuffer, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from vibeocr.services.text_block_processor import TextBlockProcessor
from vibeocr.ui import theme
from vibeocr.views.tabs.base_tab import BaseOcrTab
from vibeocr.widgets.preprocess_options_widget import PreprocessOptionsWidget
from vibeocr.widgets.preview_widget import PreviewWidget
from vibeocr.widgets.result_view_widget import ResultViewWidget
from vibeocr.widgets.text_block_options_widget import TextBlockOptionsWidget

logger = logging.getLogger(__name__)


class SingleRecognitionTab(BaseOcrTab):
    """单次识别标签页

    左侧：统一预览（图片/PDF/截图）
    右侧：管道选项 + 结果展示
    """

    SPLITTER_ID = "ocr_tab"

    screenshot_requested = Signal()
    file_open_requested = Signal()
    # 截图来源的识别完成时发出，由 MainWindow 重新把主窗口提到前台。
    # 根因：主窗口激活此前只在 OCR 开始前发生一次；异步识别期间用户/系统切走
    # 窗口后，识别完成时窗口就静悄悄留在后台（表现为「识别后主界面不弹出」）。
    # 仅截图来源识别需要抢焦点（用户离开过应用）；文件/粘贴来源用户本就在应用内，
    # 不发信号以免无谓抢焦点。
    bring_to_front_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._closing = False
        self._pending_pixmap: QPixmap | None = None
        self._pending_file_path: str | None = None
        # 本次识别是否来自截图（由 run_ocr 的 from_screenshot 参数设置）。
        # _on_ocr_finished 据此决定是否发 bring_to_front_requested。
        self._ocr_from_screenshot: bool = False
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
        self._copy_image_btn = QPushButton("复制图片")
        self._copy_image_btn.setFixedHeight(28)
        self._copy_image_btn.setEnabled(False)  # 默认禁用，有图后启用

        # 复制图片成功浮层提示（锚点为复制图片按钮）
        self._copy_toast = QLabel("原图已复制到剪贴板", self._copy_image_btn)
        self._copy_toast.setStyleSheet(
            f"QLabel {{ background-color: {theme.Colors.text};"
            f" color: {theme.Colors.surface}; padding: 6px 12px;"
            f" border-radius: {theme.Radius.sm}px;"
            f" font-size: {theme.Typography.small}px; }}"
        )
        self._copy_toast.hide()
        self._start_btn = QPushButton("开始识别")
        self._start_btn.setFixedHeight(28)
        self._start_btn.setEnabled(False)

        action_layout.addWidget(self._screenshot_btn)
        action_layout.addWidget(self._file_btn)
        action_layout.addWidget(self._paste_btn)
        action_layout.addWidget(self._copy_image_btn)
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

        self._text_options_widget = TextBlockOptionsWidget()
        right_layout.addWidget(self._text_options_widget)

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
        # 文本块处理选项变化 → 实时重排当前结果（仅纯文本结果生效）。
        self._text_options_widget.options_changed.connect(
            self._on_text_options_changed
        )

        # 转发预览组件的截图/文件请求信号
        self._preview_widget.screenshot_requested.connect(
            self.screenshot_requested.emit
        )
        self._preview_widget.file_open_requested.connect(self.file_open_requested.emit)

        # 操作按钮
        self._screenshot_btn.clicked.connect(self.screenshot_requested.emit)
        self._file_btn.clicked.connect(self._on_file_btn_clicked)
        self._paste_btn.clicked.connect(self._on_paste)
        self._copy_image_btn.clicked.connect(self._on_copy_image)
        self._start_btn.clicked.connect(self._start_recognition)

    def _on_file_btn_clicked(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        from vibeocr.utils.mime_types import FILE_FILTER_ALL, is_document_file

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择文件",
            "",
            f"{FILE_FILTER_ALL};;所有文件 (*)",
        )
        if not file_path:
            return

        self._pending_file_path = file_path
        self._pending_pixmap = None

        if is_document_file(file_path):
            self._preprocess_options.lock_to_document_parsing("当前文件仅支持文档解析")
            self._preview_widget.load_file(file_path)
            self._copy_image_btn.setEnabled(False)  # PDF 文档非位图原图，禁用
        else:
            self._preprocess_options.unlock_pipeline()
            pixmap = QPixmap(file_path)
            if not pixmap.isNull():
                self.set_pixmap(pixmap)
                self._pending_pixmap = pixmap

        self._start_btn.setEnabled(True)
        self._start_btn.setText("开始识别")

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
        self.set_image_for_recognition(pixmap)
        self._start_btn.setText("开始识别")

    def _on_copy_image(self) -> None:
        """复制原始图片到剪贴板（取 original_pixmap，非预处理后图像）。"""
        pixmap = self._preview_widget.original_pixmap()
        if pixmap is None or pixmap.isNull():
            return
        QGuiApplication.clipboard().setPixmap(pixmap)
        self._show_copy_toast()

    def _show_copy_toast(self) -> None:
        """显示「原图已复制到剪贴板」浮层提示（按钮上方居中，1.5s 自动隐藏）。"""
        toast = self._copy_toast
        toast.setText("原图已复制到剪贴板")
        toast.adjustSize()
        x = (self._copy_image_btn.width() - toast.width()) // 2
        y = -toast.height() - 8
        toast.move(x, y)
        toast.show()
        QTimer.singleShot(1500, toast.hide)

    def _on_start(self):
        self._start_recognition()

    def _start_recognition(self) -> None:
        """开始识别：保存当前管道选项后执行 OCR"""
        self._save_current_pipeline_options()
        if self._pending_pixmap:
            self.run_ocr(self._pending_pixmap)
        elif self._pending_file_path:
            self.process_file(self._pending_file_path)

    def _save_current_pipeline_options(self) -> None:
        """保存当前管道选项到持久化"""
        if not self._preprocess_options:
            return
        try:
            from vibeocr.utils.ocr_preferences import OCRPreferences

            prefs = OCRPreferences.instance()
            pipeline = self._preprocess_options.get_current_pipeline()
            options = self._preprocess_options.get_options()
            prefs.set_pipeline_options("main", pipeline, options)
        except RuntimeError:
            pass

    def set_closing(self, closing: bool) -> None:
        self._closing = closing

    # ── 公共接口（由 MainWindow 调用）──

    def set_pixmap(self, pixmap) -> None:
        self._preview_widget.set_pixmap(pixmap)
        self._update_copy_image_enabled()

    def _update_copy_image_enabled(self) -> None:
        """根据是否有原始图片启用/禁用「复制图片」按钮。"""
        pix = self._preview_widget.original_pixmap()
        self._copy_image_btn.setEnabled(pix is not None and not pix.isNull())

    def set_image_for_recognition(self, pixmap: QPixmap) -> None:
        """记录待识别图（用于粘贴 / 截图后启用「重新识别」）。

        - 存入 _pending_pixmap，清空 _pending_file_path
        - 启用 _start_btn

        截图入口与粘贴入口都应经过此方法，确保识别完成后按钮可用、
        能用界面面板选项（main 源）反复重识别。
        注意：截图首次识别的 options 仍由调用方按截图源传入，本方法
        只负责让「重新识别」可用，不改变首次识别的选项来源。
        """
        self._pending_pixmap = pixmap
        self._pending_file_path = None
        self._start_btn.setEnabled(True)
        self._update_copy_image_enabled()

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

    def run_ocr(
        self, pixmap: QPixmap, options=None, *, from_screenshot: bool = False
    ) -> None:
        """执行 OCR 识别（入口方法，由 MainWindow 调用）

        Args:
            pixmap: 待识别图片。
            options: OCR 选项；为 None 时从界面面板读取。
            from_screenshot: 本次识别是否来自截图确认路径。为 True 时，识别完成
                (_on_ocr_finished) 会发出 bring_to_front_requested，让 MainWindow
                重新把主窗口提到前台。文件/粘贴来源传 False，避免无谓抢焦点。
        """
        from vibeocr.services import USE_SUBPROCESS

        # 记录识别来源，_on_ocr_finished / _on_ocr_error 据此决定是否发前置信号。
        self._ocr_from_screenshot = from_screenshot

        self._preprocess_options.unlock_pipeline()

        if pixmap.devicePixelRatio() != 1.0:
            pixmap = QPixmap(pixmap)
            pixmap.setDevicePixelRatio(1.0)

        self._result_widget.clear()

        if options is None:
            options = self._build_options_from_ui()

        # 首次使用提示
        pipeline_val = options.pipeline.value
        if pipeline_val in (
            "OCR",
            "PP-StructureV3",
            "TABLE_RECOGNITION",
            "FORMULA_RECOGNITION",
        ):
            from vibeocr.env_manager import get_project_root
            from vibeocr.pipeline_status import is_pipeline_ever_succeeded

            if not is_pipeline_ever_succeeded(pipeline_val, get_project_root()):
                self._result_widget._ensure_web_view().setHtml(
                    '<div style="display:flex;align-items:center;justify-content:center;'
                    'height:100%;color:#666;font-size:14px;">'
                    "<p>正在识别，首次使用可能需要下载模型，请耐心等待…</p></div>"
                )

        QApplication.processEvents()

        buffer = QBuffer()
        buffer.open(QBuffer.OpenModeFlag.ReadWrite)
        pixmap.save(buffer, "PNG")
        image_data = bytes(buffer.data().data())
        buffer.close()

        if USE_SUBPROCESS:
            from vibeocr.core.constants import Constants
            from vibeocr.utils.qt_async import run_coroutine

            # 兜底超时:底层 IPC 超时(60-600s)会先触发,此值仅防止协程
            # 在 worker 卡死且 IPC 未响应的边缘场景下永久挂起。
            run_coroutine(
                self._perform_ocr_async(image_data, options),
                timeout=Constants.Timeout.MINERU_HTTP_TOTAL,
            )
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
                self._on_ocr_error(
                    str(e) + self._first_use_suffix(options.pipeline.value, str(e))
                )

    def process_file(self, file_path: str) -> None:
        """处理文件（由 MainWindow 调用，支持 PDF/Office/图片）"""
        from vibeocr.utils.mime_types import guess_mime_from_filename, is_document_file

        path = Path(file_path)
        if not path.exists():
            return

        if is_document_file(file_path):
            # 文档文件(PDF/Office)强制走 MinerU 文档解析，CPU 后端下不可用。
            # 在此拦截，避免进入 _run_ocr_with_data 后因管道被 GPU 门控禁用而崩溃。
            from vibeocr.env_manager import get_project_root, get_runtime_gpu_capability

            if not get_runtime_gpu_capability(get_project_root()):
                from PySide6.QtWidgets import QMessageBox

                QMessageBox.warning(
                    self,
                    "文档解析不可用",
                    "当前为 CPU 后端，文档解析(MinerU)需要 GPU 支持。\n"
                    "请将文件转为图片后识别，或在设置页切换到 GPU 后端后重启。",
                )
                return
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
            from vibeocr.core.constants import Constants
            from vibeocr.utils.qt_async import run_coroutine

            # 兜底超时:文档解析(MinerU)可能很慢,用 MINERU_HTTP_TOTAL(30 分钟)
            run_coroutine(
                self._perform_ocr_with_data_async(data, mime_type, filename, options),
                timeout=Constants.Timeout.MINERU_HTTP_TOTAL,
            )
        else:
            try:
                from vibeocr.services import get_ocr_service

                ocr_service = get_ocr_service()
                result = ocr_service.recognize(data, options)
                self._on_ocr_finished(result)
            except Exception as e:
                logger.error(f"OCR 识别失败: {e}", exc_info=True)
                self._on_ocr_error(
                    str(e) + self._first_use_suffix(options.pipeline.value, str(e))
                )

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
            self._on_ocr_error(str(e) + self._first_use_suffix(options.pipeline.value, str(e)))

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
            self._on_ocr_error(str(e) + self._first_use_suffix(options.pipeline.value, str(e)))

    def _on_result_block_edited(self, index: int, new_text: str) -> None:
        """右侧结果块被编辑后同步更新数据模型。

        表格块的 new_text 是新的 ``<table>`` HTML（见 JS _finishTableEdit），
        其数据源是 ``content_list`` 的 ``table_body``，处理逻辑与左侧网格编辑
        一致，故直接委托给 ``_on_table_block_edited``，复用其 table_body 更新、
        set_content_list（保持块类型模式）、update_block_text(HTML) 等正确流程。
        """
        if not self._current_ocr_result or index < 0:
            return
        result = self._current_ocr_result

        if not result.content_list or index >= len(result.content_list):
            return

        cl_block = result.content_list[index]
        # 表格块委托给表格专用同步逻辑（举一反三：与左侧网格编辑同一数据源）
        if cl_block.get("type", "") == "table":
            self._on_table_block_edited(index, new_text)
            return

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
                if tb.content_index is not None and tb.content_index < len(
                    result.text_with_scores
                ):
                    score = result.text_with_scores[tb.content_index][1]
                    result.text_with_scores[tb.content_index] = (new_text, score)
                break

        # 全量重建 raw_text，避免 str.replace 子串误匹配。
        # 结构化结果（has_content_list）保持原 "\n".join 行为；纯文本结果走后处理器，
        # 保证手动改某块后重建的 raw_text 与识别时排版规则一致。
        if result.has_content_list:
            result.raw_text = "\n".join(b.text for b in result.text_blocks if b.text)
        else:
            text_opts = self._text_options_widget.get_text_options()
            result.raw_text = TextBlockProcessor.process(
                result.text_blocks, text_opts, result.image_height
            )

        # 同步更新 markdown_text / html_text
        if old_text:
            if result.markdown_text and old_text in result.markdown_text:
                result.markdown_text = result.markdown_text.replace(
                    old_text, new_text, 1
                )
            if result.html_text and old_text in result.html_text:
                result.html_text = result.html_text.replace(old_text, new_text, 1)

        # 刷新左侧 overlay（显示手动修改标记）。
        # 结构化结果（表格/公式/MinerU）左侧在块类型模式渲染，必须用
        # set_content_list 保持该模式；否则切到置信度模式会让块类型着色与
        # 编辑状态错位（右侧变黄但左侧无变化）。
        if self._preview_widget:
            if result.has_content_list:
                self._preview_widget.set_content_list(result.content_list)
            else:
                self._preview_widget.set_text_blocks(result.text_blocks)

    def _on_text_options_changed(self, _options) -> None:
        """「文本块处理」选项变化 → 实时重排当前结果。

        仅对识别时即为纯文本的结果生效（_plain_text_at_recognition=True）：
        结构化结果（表格/公式/MinerU）走块类型渲染，不读 raw_text，重排无意义
        且会破坏复制/导出链路（误改其 raw_text）。

        重排后重算 raw_text / markdown_text，并刷新结果区（块间排版会随
        换行模式/空格/缩进/去空白块选项变化）。结果区用 display_text_layout
        整体渲染（而非逐块），使排版变化在屏幕上可见。
        """
        result = self._current_ocr_result
        if result is None or not getattr(
            self, "_plain_text_at_recognition", False
        ):
            return

        text_opts = self._text_options_widget.get_text_options()
        result.raw_text = TextBlockProcessor.process(
            result.text_blocks, text_opts, result.image_height
        )
        # markdown_text 对纯文本结果即 raw_text（见各 pipeline 的 `or raw_text` 兜底），
        # 同步以保持复制 MD / 导出的一致性。
        result.markdown_text = result.raw_text
        # 用按选项排版的整体渲染刷新右侧（而非逐块的 _display_result），
        # 使换行模式/空格/缩进可见。左侧预览保持置信度模式（块级编辑入口仍在）。
        if self._result_widget is not None:
            self._result_widget.display_text_layout(result, text_opts)

    def _on_ocr_finished(self, result) -> None:
        """OCR 完成回调"""
        self._current_ocr_result = result
        self._start_btn.setText("重新识别")

        # 文本块后处理：仅对纯文本结果应用（结构化结果走块类型渲染，不读 raw_text）。
        # 改写 raw_text 后，下游的 _display_result / 复制 / 手动编辑重建均读 raw_text，自动一致。
        # 同时记录识别时的纯文本标志，供 _on_text_options_changed 实时重排判断：
        # 注意 has_content_list 必须在 _display_result 之前读，否则通用 OCR 会被
        # _build_content_list 回填成 content_list 而误判为结构化结果。
        self._plain_text_at_recognition = not result.has_content_list
        if self._plain_text_at_recognition:
            text_opts = self._text_options_widget.get_text_options()
            result.raw_text = TextBlockProcessor.process(
                result.text_blocks, text_opts, result.image_height
            )

        char_count = len(result.raw_text) if result.raw_text else 0
        block_count = len(result.text_with_scores)
        logger.info(f"OCR 完成: {block_count} 个文本块, {char_count} 个字符")

        # 预处理改变了图像时，用预处理后的图像更新预览
        if result.preprocessed_image:
            pixmap = QPixmap()
            pixmap.loadFromData(result.preprocessed_image)
            if not pixmap.isNull():
                self._preview_widget.set_pixmap(pixmap)

        # 设置文本块到预览（置信度模式）
        self._preview_widget.set_text_blocks(result.text_blocks)

        # 显示结果。纯文本结果用按选项排版的整体渲染（display_text_layout），
        # 使换行模式/空格/缩进在识别完成即可见；结构化结果走 _display_result
        # 的块类型渲染。二者都会为左侧预览回填 content_list（块级编辑入口）。
        if self._plain_text_at_recognition and self._result_widget is not None:
            text_opts = self._text_options_widget.get_text_options()
            # 仍走 _display_result 以回填 content_list / 同步预览，随后用
            # display_text_layout 覆盖右侧渲染，体现排版选项。
            self._display_result(result)
            self._result_widget.display_text_layout(result, text_opts)
        else:
            self._display_result(result)

        # 识别成功后折叠选项面板，让结果区获得最大空间（失败路径不折叠，
        # 保留选项可见方便调整重试；用户可随时点标题重新展开）。
        if self._preprocess_options is not None:
            self._preprocess_options.set_collapsed(True)
        if self._text_options_widget is not None:
            self._text_options_widget.set_collapsed(True)

        # 截图来源识别完成 → 通知 MainWindow 重新把主窗口提到前台。
        # 异步识别可能耗时数秒（首次还需下载模型），期间用户/系统切走窗口后，
        # OCR 开始前那次激活已失效，故在此再次前置。发出后复位标记，避免
        # 后续手动「重新识别」（文件来源语义）误触发抢焦点。
        if self._ocr_from_screenshot:
            self._ocr_from_screenshot = False
            self.bring_to_front_requested.emit()

    def _first_use_suffix(self, pipeline_val: str, error_text: str = "") -> str:
        """首次使用失败时返回追加提示。

        依赖类错误（dependency/缺少依赖/DependencyError）优先给依赖修复提示，
        而非误导性的"下载模型"——模型下载解决不了依赖缺失，反而让用户白等。
        """
        # 依赖缺失特征词（覆盖 PaddleX DependencyError / 本项目 TableDependencyError）
        lowered = error_text.lower()
        if any(
            k in lowered
            for k in (
                "dependency",
                "依赖",
                "缺少依赖",
                "paddlex[ocr]",
                "additional dependencies",
                "tabledependencyerror",
            )
        ):
            return "\n\n提示：检测到依赖缺失，请在「设置 → 重装 OCR 依赖」修复后重试。"

        if pipeline_val in (
            "OCR",
            "PP-StructureV3",
            "TABLE_RECOGNITION",
            "FORMULA_RECOGNITION",
        ):
            from vibeocr.env_manager import get_project_root
            from vibeocr.pipeline_status import is_pipeline_ever_succeeded

            if not is_pipeline_ever_succeeded(pipeline_val, get_project_root()):
                return "\n\n提示：首次使用需要下载模型，请保持网络畅通后重试。"
        return ""

    def _on_ocr_error(self, error_msg: str) -> None:
        """OCR 失败回调"""
        # 失败不复位标记会让下次识别误判来源，显式复位。
        self._ocr_from_screenshot = False
        self._current_ocr_result = None
        self._start_btn.setText("开始识别")
        self._result_widget.clear()
        self._result_widget._ensure_web_view().setHtml(
            f"<p style='color:#f44336;'>识别失败：{error_msg}</p>"
        )

    def show_waiting_message(self, message: str) -> None:
        """在结果面板显示等待提示（预加载排队时调用）"""
        self._result_widget._ensure_web_view().setHtml(
            f'<div style="display:flex;align-items:center;justify-content:center;'
            f'height:100%;color:#666;font-size:14px;">'
            f"<p>{message}</p></div>"
        )
