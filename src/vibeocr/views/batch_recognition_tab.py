"""批量识别标签页

提供批量文件识别功能，三栏布局：文件列表 | 文件预览 | 识别结果。
"""

import contextlib
import logging
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from vibeocr.models.ocr_options import OCROptions
from vibeocr.ui import theme
from vibeocr.views.tabs.base_tab import BaseOcrTab
from vibeocr.widgets.batch_file_list_widget import BatchFileListWidget
from vibeocr.widgets.export_settings_widget import ExportSettingsWidget
from vibeocr.widgets.preprocess_options_widget import PreprocessOptionsWidget
from vibeocr.widgets.preview_widget import PreviewWidget
from vibeocr.widgets.result_view_widget import ResultViewWidget

# 向后兼容别名
PreprocessOptions = OCROptions

logger = logging.getLogger(__name__)


class BatchRecognitionWorker(QThread):
    """批量识别工作线程"""

    progress = Signal(int, int, str)  # completed, total, current_file
    file_completed = Signal(str, str, object)  # file_path, status, result
    finished = Signal(dict)  # results
    error = Signal(str)

    def __init__(
        self,
        service,
        files: list[dict],
        preprocess_options: PreprocessOptions,
        parent=None,
    ):
        super().__init__(parent)
        self._service = service
        self._files = files
        self._preprocess_options = preprocess_options
        self._cancelled = False

    # 分小批调 recognize_batch 的批大小。每批一次 RCBG SHM 往返（单次 predict），
    # 远少于旧逐文件 batch_add 的 N 次往返。批边界也是取消检查点。
    # 16 与 PDF OCR 页批一致，GPU predict 内部按 text_recognition_batch_size
    # 二次分批；SHM 预算 0.7×(128MB−9)≈90MB 足以装下 16 张图。
    _BATCH_SIZE = 16

    def run(self):
        """执行批量识别（分小批 recognize_batch，每批完成逐文件上报）。

        旧实现用逐文件 batch_add（每文件一次 SHM 往返）+ batch_commit 流式回调，
        N 个文件 = 2N+1 次消息交换。改为 recognize_batch（RCBG 单次往返）后，
        N 个文件 = ceil(N/16) 次往返，IPC 开销降一个数量级。

        recognize_batch 阻塞返回 list（无流式回调），故按 16 个/批切片，
        每批完成即逐文件发 file_completed + progress，保持 UI 流式反馈。
        取消在批边界检查 _cancelled（协作式，单批 predict 进行中不可中断）。
        """
        results = {}
        total = len(self._files)

        if total == 0:
            self.finished.emit(results)
            return

        completed = 0
        for batch_start in range(0, total, self._BATCH_SIZE):
            if self._cancelled:
                break

            batch_files = self._files[batch_start:batch_start + self._BATCH_SIZE]

            # 读文件 bytes（读取失败的单文件标记 failed，不影响整批）
            images: list[bytes | None] = []
            read_errors: dict[int, str] = {}  # batch 内索引 -> 错误
            for bi, file_info in enumerate(batch_files):
                try:
                    with open(file_info["path"], "rb") as f:
                        images.append(f.read())
                except Exception as e:
                    logger.error(f"读取文件失败 {file_info['path']}: {e}")
                    images.append(None)
                    read_errors[bi] = str(e)

            # 识别有效图像
            valid_indices = [bi for bi, img in enumerate(images) if img is not None]
            batch_results: list = [None] * len(valid_indices)
            if valid_indices and not self._cancelled:
                valid_images = [images[bi] for bi in valid_indices]  # type: ignore[list-item]
                try:
                    batch_results = self._service.recognize_batch(
                        valid_images, self._preprocess_options
                    )
                except Exception as e:
                    logger.error(f"批量识别失败(批起始索引 {batch_start}): {e}")
                    self.error.emit(str(e))
                    # 识别整批失败：标记本批所有有效文件 failed
                    for bi in valid_indices:
                        file_path = batch_files[bi]["path"]
                        self.file_completed.emit(
                            file_path, "failed", {"error": str(e)}
                        )
                        results[file_path] = {
                            "file_path": file_path,
                            "error": str(e),
                        }
                        completed += 1
                        self.progress.emit(
                            completed, total,
                            f"失败: {Path(file_path).name}",
                        )
                    # 继续下一批（单批失败不中断整体）
                    continue

            # 逐文件上报结果（保持 UI 流式反馈）
            result_iter = iter(batch_results)
            for bi, file_info in enumerate(batch_files):
                file_path = file_info["path"]
                if file_path in results:
                    # 已在 read_errors 或整批失败中报告
                    completed += 1
                    self.progress.emit(completed, total, Path(file_path).name)
                    continue
                if bi in read_errors:
                    self.file_completed.emit(
                        file_path, "failed", {"error": read_errors[bi]}
                    )
                    results[file_path] = {
                        "file_path": file_path,
                        "error": read_errors[bi],
                    }
                else:
                    # 取对应识别结果（valid_indices 顺序与 batch_results 一致）
                    try:
                        res = next(result_iter)
                    except StopIteration:
                        res = None
                    if res is None:
                        self.file_completed.emit(
                            file_path, "failed", {"error": "识别失败"}
                        )
                        results[file_path] = {
                            "file_path": file_path,
                            "error": "识别失败",
                        }
                    else:
                        self.file_completed.emit(file_path, "completed", res)
                        results[file_path] = {
                            "file_path": file_path,
                            "result": res,
                        }
                completed += 1
                self.progress.emit(completed, total, Path(file_path).name)

        self.progress.emit(total, total, "完成")
        self.finished.emit(results)

    def cancel(self):
        """取消处理（协作式）。

        设置 _cancelled 标志，run() 的批循环在下一个批边界检查并停止。
        batch_cancel 仍调用以兼容 service 层（若底层有 batch_commit 路径仍可中断）；
        对 recognize_batch 路径，当前批 predict 不可抢占，完成后即停止。
        """
        self._cancelled = True
        with contextlib.suppress(Exception):
            self._service.batch_cancel()


class BatchRecognitionTab(BaseOcrTab):
    """批量识别标签页

    三栏布局：文件列表 | 文件预览 | 识别结果
    """

    SPLITTER_ID = "batch_tab_v2"

    def __init__(self, ocr_service=None, parent=None, *, backend=None):
        super().__init__(parent)
        self._ocr_service = ocr_service  # MinerUBatchService
        self._paddlex_service = None  # OCRServiceSubprocess
        self._backend = backend
        self._batch_backend = None
        self._worker: BatchRecognitionWorker | None = None
        self._layout_manager = None
        self._current_file_path: str = ""

        self._setup_ui()
        self._connect_signals()
        self._init_options_from_preferences(batch=True)

    def _setup_ui(self):
        """设置三栏 UI"""
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(8, 8, 8, 8)

        # 主分割器（三栏）
        self._splitter = QSplitter()

        # ── 左侧面板：文件列表 + 识别选项+操作 + 导出设置 ──
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setSpacing(0)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self._file_list_widget = BatchFileListWidget()
        left_layout.addWidget(self._file_list_widget, stretch=3)

        self._preprocess_options = PreprocessOptionsWidget()
        left_layout.addWidget(self._preprocess_options)

        # 操作区：开始/取消按钮 + 进度
        action_layout = QHBoxLayout()
        action_layout.setContentsMargins(8, 4, 8, 4)

        self._start_btn = QPushButton("开始识别")
        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.setEnabled(False)

        self._progress_label = QLabel("0/0")
        self._progress_label.setStyleSheet(
            f"color: {theme.Colors.accent}; font-weight: bold;"
        )

        action_layout.addWidget(self._start_btn)
        action_layout.addWidget(self._cancel_btn)
        action_layout.addStretch()
        action_layout.addWidget(self._progress_label)

        left_layout.addLayout(action_layout)

        self._export_widget = ExportSettingsWidget()
        left_layout.addWidget(self._export_widget)

        self._splitter.addWidget(left_panel)

        # ── 中间面板：文件预览 ──
        center_panel = QWidget()
        center_layout = QVBoxLayout(center_panel)
        center_layout.setSpacing(4)
        center_layout.setContentsMargins(0, 0, 0, 0)

        preview_label = QLabel("文件预览")
        preview_label.setStyleSheet(
            f"font-weight: bold; color: {theme.Colors.text_muted};"
        )
        center_layout.addWidget(preview_label)

        self._preview_widget = PreviewWidget(empty_text="选择文件以预览")
        center_layout.addWidget(self._preview_widget, stretch=1)

        self._splitter.addWidget(center_panel)

        # ── 右侧面板：识别结果（独占） ──
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setSpacing(4)
        right_layout.setContentsMargins(0, 0, 0, 0)

        result_label = QLabel("识别结果")
        result_label.setStyleSheet(
            f"font-weight: bold; color: {theme.Colors.text_muted};"
        )
        right_layout.addWidget(result_label)

        self._result_widget = ResultViewWidget()
        right_layout.addWidget(self._result_widget, stretch=1)

        self._splitter.addWidget(right_panel)

        # 设置分割比例 [280, 45%, 45%]
        self._splitter.setSizes([280, 450, 450])

        layout.addWidget(self._splitter, stretch=1)

        self.setLayout(layout)

    def _connect_signals(self):
        """连接信号"""
        self._setup_hover_sync()

        self._start_btn.clicked.connect(self._on_start)
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._file_list_widget.selection_changed.connect(self._on_file_selected)
        self._file_list_widget.files_changed.connect(self._on_files_changed)
        self._export_widget.export_requested.connect(self._on_export_current)
        self._export_widget.export_all_requested.connect(self._on_export_all)

    def _on_files_changed(self, files: list[dict]) -> None:
        """文件列表变化时，根据是否包含文档文件锁定管道"""
        from vibeocr.utils.mime_types import is_document_file

        has_document = any(is_document_file(f["path"]) for f in files)
        if has_document:
            # 文档文件需 MinerU 文档解析（GPU 后端）。CPU 后端下文档解析被禁用，
            # 此时提示用户而非静默锁定到不可用管道。
            from vibeocr.env_manager import (
                get_project_root,
                get_runtime_gpu_capability,
            )

            if not get_runtime_gpu_capability(get_project_root()):
                from PySide6.QtWidgets import QMessageBox

                QMessageBox.warning(
                    self,
                    "文档解析不可用",
                    "当前为 CPU 后端，文档解析(MinerU)需要 GPU 支持。\n"
                    "请移除文档文件，或在设置页切换到 GPU 后端后重启。",
                )
            self._preprocess_options.lock_to_document_parsing(
                "队列含文档文件，仅支持文档解析"
            )
        else:
            self._preprocess_options.unlock_pipeline()

    def _on_start(self):
        """开始识别"""
        files = self._file_list_widget.get_selected_files()
        if not files:
            self._result_widget.clear()
            return

        preprocess_options = self._preprocess_options.get_options()
        service = self._get_batch_backend()

        if not service:
            self._result_widget.clear()
            return

        self._start_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)

        self._progress_label.setText(f"0/{len(files)}")

        self._result_widget.clear()

        # 首次使用提示
        pipeline_val = preprocess_options.pipeline.value
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

        self._worker = BatchRecognitionWorker(service, files, preprocess_options)
        self._worker.progress.connect(self._on_progress)
        self._worker.file_completed.connect(self._on_file_completed)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _get_backend_client(self):
        if self._backend is None:
            from vibeocr.client.session import get_backend_client

            self._backend = get_backend_client()
        return self._backend

    def _get_batch_backend(self):
        if self._batch_backend is None:
            from vibeocr.client.batch import BatchBackendAdapter

            self._batch_backend = BatchBackendAdapter(self._get_backend_client())
        return self._batch_backend

    def _on_cancel(self):
        """取消识别"""
        if self._worker:
            self._worker.cancel()
        self._reset_ui()

    def _on_progress(self, completed: int, total: int, current_file: str):
        """进度更新"""
        self._progress_label.setText(
            f"{completed}/{total} {current_file}"
            if current_file
            else f"{completed}/{total}"
        )

    def _on_file_completed(self, file_path: str, status: str, result):
        """单个文件完成"""
        self._file_list_widget.update_file_status(file_path, status, result)

        # 如果是当前选中的文件，刷新显示
        if file_path == self._current_file_path and status == "completed" and result:
            self._display_result(result)
            self._export_widget.set_current_result(result)

    def _on_finished(self, results: dict):
        """处理完成"""
        completed = len([r for r in results.values() if "result" in r])
        failed = len([r for r in results.values() if "error" in r])

        logger.info(f"批量处理完成: {completed} 成功, {failed} 失败")
        self._reset_ui()

    def _on_error(self, error_msg: str):
        """处理错误"""
        logger.error(f"Batch recognition error: {error_msg}")
        self._reset_ui()

    def _on_file_selected(self, file_path: str):
        """文件选择变更：加载预览和结果"""
        self._current_file_path = file_path

        # 加载文件预览
        self._preview_widget.load_file(file_path)

        # 查找并显示结果
        files = self._file_list_widget._files
        for f in files:
            if f["path"] == file_path:
                result = f.get("result")
                if result:
                    self._display_result(result)
                    self._export_widget.set_current_result(result)
                else:
                    self._result_widget.clear()
                break

    # ── 导出功能 ──

    def _on_export_current(self, fmt: str, result) -> None:
        """导出当前文件"""
        if not result:
            return

        export_dir = self._export_widget.get_export_dir(self._current_file_path)
        from vibeocr.client.export import (
            export_result,
            get_output_filename,
            get_unique_output_path,
        )

        output_name = get_output_filename(
            Path(self._current_file_path).name, fmt
        )
        output_path = get_unique_output_path(
            Path(export_dir) / output_name
        )

        success = export_result(self._get_backend_client(), result, output_path, fmt)
        if success:
            QMessageBox.information(self, "导出成功", f"已导出到:\n{output_path}")
        else:
            QMessageBox.warning(self, "导出失败", f"导出失败:\n{output_path}")

    def _on_export_all(self, fmt: str) -> None:
        """导出全部已完成的文件"""
        from vibeocr.client.export import (
            export_result,
            get_output_filename,
            get_unique_output_path,
        )

        files = self._file_list_widget._files
        completed_files = [
            f for f in files if f["status"] == "completed" and f.get("result")
        ]

        if not completed_files:
            QMessageBox.information(self, "提示", "没有可导出的结果")
            return

        success_count = 0
        fail_count = 0
        renamed: list[str] = []

        for f in completed_files:
            result = f["result"]
            export_dir = self._export_widget.get_export_dir(f["path"])
            output_name = get_output_filename(f["name"], fmt)
            output_path = Path(export_dir) / output_name
            actual_path = get_unique_output_path(output_path)

            if output_path != actual_path:
                renamed.append(f"{output_name} → {actual_path.name}")

            if export_result(
                self._get_backend_client(), result, actual_path, fmt
            ):
                success_count += 1
            else:
                fail_count += 1

        msg = f"导出完成: {success_count} 成功"
        if fail_count:
            msg += f", {fail_count} 失败"
        if renamed:
            msg += "\n\n以下文件因同名已自动重命名:\n" + "\n".join(renamed)
        QMessageBox.information(self, "导出结果", msg)

    def _reset_ui(self):
        """重置 UI 状态"""
        self._start_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._worker = None

    def set_layout_manager(self, layout_manager) -> None:
        """设置布局管理器并恢复分割器状态"""
        self._layout_manager = layout_manager
        if self._layout_manager and hasattr(self, "_splitter"):
            state = self._layout_manager.get_splitter_state(self.SPLITTER_ID)
            if state:
                self._splitter.restoreState(state)

    def save_layout(self) -> None:
        """保存分割器状态"""
        if self._layout_manager and hasattr(self, "_splitter"):
            self._layout_manager.set_splitter_state(
                self.SPLITTER_ID, self._splitter.saveState()
            )
