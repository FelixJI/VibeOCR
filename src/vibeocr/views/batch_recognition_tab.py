"""批量识别标签页

提供批量文件识别功能。
"""

import contextlib
import logging
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from vibeocr.models.batch_request import PreprocessOptions
from vibeocr.views.tabs.base_tab import BaseOcrTab
from vibeocr.widgets.batch_file_list_widget import BatchFileListWidget
from vibeocr.widgets.preprocess_options_widget import PreprocessOptionsWidget

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
            self.progress.emit(i, total, Path(file_path).name)

            try:
                # 读取文件
                with open(file_path, "rb") as f:
                    image_data = f.read()

                # 添加到队列
                request_id = self._service.batch_add(
                    image_data, file_name=file_info["name"]
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
                self.progress.emit(len(request_map), total, "正在处理批量任务...")
                batch_results = self._service.batch_commit(self._preprocess_options)

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

    继承自 BaseOcrTab，提供批量文件 OCR 识别功能。
    """

    def __init__(self, ocr_service=None, parent=None):
        super().__init__(parent)
        self._ocr_service = ocr_service
        self._worker: BatchRecognitionWorker | None = None

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self):
        """设置 UI"""
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(8, 8, 8, 8)

        # 使用 Splitter 分割左右面板
        splitter = QSplitter()

        # 左侧面板
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setSpacing(8)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # 文件列表
        self._file_list_widget = BatchFileListWidget()
        left_layout.addWidget(self._file_list_widget)

        # 预处理选项
        self._preprocess_options = PreprocessOptionsWidget()
        left_layout.addWidget(self._preprocess_options)

        splitter.addWidget(left_panel)

        # 右侧面板 - 结果显示
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # 结果显示标签
        result_label = QLabel("识别结果")
        right_layout.addWidget(result_label)

        # 使用 ConsoleWidget 显示结果
        from vibeocr.widgets.console_widget import ConsoleWidget

        self._result_widget = ConsoleWidget()
        right_layout.addWidget(self._result_widget)

        splitter.addWidget(right_panel)

        # 设置分割比例
        splitter.setSizes([300, 500])

        layout.addWidget(splitter, stretch=1)

        # 底部进度区域
        progress_layout = QHBoxLayout()

        self._start_btn = QPushButton("开始识别")
        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.setEnabled(False)

        self._progress_bar = QProgressBar()
        self._progress_bar.setMinimum(0)
        self._progress_bar.setMaximum(100)
        self._progress_bar.setValue(0)

        self._progress_label = QLabel("0/0")

        progress_layout.addWidget(self._start_btn)
        progress_layout.addWidget(self._progress_bar, stretch=1)
        progress_layout.addWidget(self._progress_label)
        progress_layout.addWidget(self._cancel_btn)

        layout.addLayout(progress_layout)

        self.setLayout(layout)

    def _connect_signals(self):
        """连接信号"""
        self._start_btn.clicked.connect(self._on_start)
        self._cancel_btn.clicked.connect(self._on_cancel)

        self._file_list_widget.selection_changed.connect(self._on_file_selected)

    def _on_start(self):
        """开始识别"""
        files = self._file_list_widget.get_selected_files()
        if not files:
            self._result_widget.set_text("请选择要处理的文件。")
            return

        if not self._ocr_service:
            self._result_widget.set_text("OCR 服务未就绪。")
            return

        # 获取预处理选项
        preprocess_options = self._preprocess_options.get_options()

        # 禁用按钮
        self._start_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)

        # 更新进度
        self._progress_bar.setValue(0)
        self._progress_label.setText(f"0/{len(files)}")

        # 清空之前的结果
        self._result_widget.clear()

        # 创建工作线程
        self._worker = BatchRecognitionWorker(
            self._ocr_service, files, preprocess_options
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
        if total > 0:
            self._progress_bar.setMaximum(total)
            self._progress_bar.setValue(completed)
        self._progress_label.setText(f"{completed}/{total}")

    def _on_file_completed(self, file_path: str, status: str, result):
        """单个文件完成"""
        # 更新文件列表状态
        self._file_list_widget.update_file_status(file_path, status, result)

        # 显示结果
        file_name = Path(file_path).name
        if status == "completed" and result:
            text = self._extract_text(result)
            self._result_widget.append_text(f"=== {file_name} ===\n{text}\n\n")
        elif status == "failed":
            error = (
                result.get("error", "未知错误")
                if isinstance(result, dict)
                else "未知错误"
            )
            self._result_widget.append_text(f"=== {file_name} ===\n[失败] {error}\n\n")

    def _on_finished(self, results: dict):
        """处理完成"""
        completed = len([r for r in results.values() if "result" in r])
        failed = len([r for r in results.values() if "error" in r])

        self._result_widget.append_text(
            f"\n--- 批量处理完成: {completed} 个成功, {failed} 个失败 ---"
        )

        self._reset_ui()

    def _on_error(self, error_msg: str):
        """处理错误"""
        logger.error(f"Batch recognition error: {error_msg}")
        self._result_widget.append_text(f"[错误] {error_msg}")
        self._reset_ui()

    def _on_file_selected(self, file_path: str):
        """文件选择变更"""
        # 查找对应的结果并显示
        files = self._file_list_widget._files
        for f in files:
            if f["path"] == file_path and f.get("result"):
                result = f["result"]
                text = self._extract_text(result)
                self._result_widget.set_text(text)
                break

    def _extract_text(self, result) -> str:
        """从结果中提取文本"""
        if result is None:
            return ""

        if hasattr(result, "raw_text"):
            return result.raw_text
        elif hasattr(result, "text"):
            return result.text
        elif isinstance(result, dict):
            if "text" in result:
                return result["text"]
            if "raw_text" in result:
                return result["raw_text"]
            return str(result)
        else:
            return str(result)

    def _reset_ui(self):
        """重置 UI 状态"""
        self._start_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._worker = None

    def set_ocr_service(self, service):
        """设置 OCR 服务"""
        self._ocr_service = service
