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
from vibeocr.services.export_service import ExportService
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

    def run(self):
        """执行批量识别"""
        results = {}
        total = len(self._files)

        if total == 0:
            self.finished.emit(results)
            return

        # 添加所有文件到批量队列
        request_map = {}  # request_id -> file_info
        for i, file_info in enumerate(self._files):
            if self._cancelled:
                break

            file_path = file_info["path"]
            self.progress.emit(i, total, f"准备: {Path(file_path).name}")

            try:
                with open(file_path, "rb") as f:
                    image_data = f.read()

                request_id = self._service.batch_add(
                    image_data,
                    options=self._preprocess_options,
                    file_name=file_info["name"],
                )

                request_map[request_id] = file_info
                logger.debug(
                    f"添加文件到批量队列: {file_path}, request_id={request_id}"
                )

            except Exception as e:
                logger.error(f"处理文件失败 {file_path}: {e}")
                self.file_completed.emit(file_path, "failed", {"error": str(e)})
                results[file_path] = {"file_path": file_path, "error": str(e)}

        # 提交批量处理
        if not self._cancelled and request_map:
            try:
                def on_process_progress(completed: int, total_count: int, name: str):
                    self.progress.emit(completed, total_count, f"处理: {name}")

                batch_results = self._service.batch_commit(
                    self._preprocess_options,
                    progress_callback=on_process_progress,
                )

                # 分发结果
                for request_id, result in batch_results.items():
                    if request_id in request_map:
                        file_info = request_map[request_id]
                        file_path = file_info["path"]

                        if isinstance(result, dict) and "error" in result:
                            self.file_completed.emit(file_path, "failed", result)
                            results[file_path] = {
                                "file_path": file_path,
                                "error": result["error"],
                            }
                        else:
                            self.file_completed.emit(file_path, "completed", result)
                            results[file_path] = {
                                "file_path": file_path,
                                "result": result,
                            }

            except Exception as e:
                self.error.emit(str(e))
                return

        self.finished.emit(results)

    def cancel(self):
        """取消处理"""
        self._cancelled = True
        with contextlib.suppress(Exception):
            self._service.batch_cancel()


class BatchRecognitionTab(BaseOcrTab):
    """批量识别标签页

    三栏布局：文件列表 | 文件预览 | 识别结果
    """

    SPLITTER_ID = "batch_tab_v2"

    def __init__(self, ocr_service=None, parent=None):
        super().__init__(parent)
        self._ocr_service = ocr_service  # MinerUBatchService
        self._paddlex_service = None  # OCRServiceSubprocess
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
        self._progress_label.setStyleSheet("color: #3b82f6; font-weight: bold;")

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
        preview_label.setStyleSheet("font-weight: bold; color: #555;")
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
        result_label.setStyleSheet("font-weight: bold; color: #555;")
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
        self._export_widget.export_requested.connect(self._on_export_current)
        self._export_widget.export_all_requested.connect(self._on_export_all)

    def _on_start(self):
        """开始识别"""
        files = self._file_list_widget.get_selected_files()
        if not files:
            self._result_widget.clear()
            return

        preprocess_options = self._preprocess_options.get_options()
        service = self._get_service_for_pipeline(preprocess_options)

        if not service:
            self._result_widget.clear()
            return

        self._start_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)

        self._progress_label.setText(f"0/{len(files)}")

        self._result_widget.clear()

        self._worker = BatchRecognitionWorker(
            service, files, preprocess_options
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.file_completed.connect(self._on_file_completed)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_cancel(self):
        """取消识别"""
        if self._worker:
            self._worker.cancel()
        self._reset_ui()

    def _on_progress(self, completed: int, total: int, current_file: str):
        """进度更新"""
        self._progress_label.setText(
            f"{completed}/{total} {current_file}" if current_file else f"{completed}/{total}"
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
        output_name = ExportService.get_output_filename(
            Path(self._current_file_path).name, fmt
        )
        output_path = Path(export_dir) / output_name

        success = ExportService.export(result, output_path, fmt)
        if success:
            QMessageBox.information(
                self, "导出成功", f"已导出到:\n{output_path}"
            )
        else:
            QMessageBox.warning(self, "导出失败", f"导出失败:\n{output_path}")

    def _on_export_all(self, fmt: str) -> None:
        """导出全部已完成的文件"""
        files = self._file_list_widget._files
        completed_files = [f for f in files if f["status"] == "completed" and f.get("result")]

        if not completed_files:
            QMessageBox.information(self, "提示", "没有可导出的结果")
            return

        success_count = 0
        fail_count = 0

        for f in completed_files:
            result = f["result"]
            export_dir = self._export_widget.get_export_dir(f["path"])
            output_name = ExportService.get_output_filename(f["name"], fmt)
            output_path = Path(export_dir) / output_name

            if ExportService.export(result, output_path, fmt):
                success_count += 1
            else:
                fail_count += 1

        msg = f"导出完成: {success_count} 成功"
        if fail_count:
            msg += f", {fail_count} 失败"
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
