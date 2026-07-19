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
from vibeocr.pyside.batch_budget import (
    BatchBudget,
    BatchEntry,
    image_pixel_count,
    partition_batches,
)
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
    # 业务终态与 QThread.finished 分离。后者只表示线程已经真正退出，UI 只能在
    # 收到 QThread.finished 后释放 worker 引用。
    terminal = Signal(str, dict)  # status, results
    error = Signal(str)

    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"
    STATUS_PARTIAL_FAILED = "partial_failed"

    def __init__(
        self,
        service,
        files: list[dict],
        preprocess_options: PreprocessOptions,
        parent=None,
        *,
        batch_budget: BatchBudget | None = None,
    ):
        super().__init__(parent)
        self._service = service
        self._files = files
        self._preprocess_options = preprocess_options
        self._cancelled = False
        self._batch_budget = batch_budget or BatchBudget.ocr_default()
        self._terminal_status: str | None = None
        self._results: dict = {}

    @property
    def terminal_status(self) -> str | None:
        """返回本次运行的业务终态。"""
        return self._terminal_status

    @property
    def results(self) -> dict:
        """返回已产生的结果快照。"""
        return dict(self._results)

    def run(self):
        """执行批量识别，并保证每次运行都产生一个明确业务终态。"""
        try:
            results, completed, total, failed = self._run_batches()
        except Exception as exc:  # 防止意外异常绕过终态和 UI 清理
            logger.exception("批量识别线程异常终止")
            self.error.emit(str(exc))
            results = dict(self._results)
            completed = len(results)
            total = len(self._files)
            failed = max(1, sum("error" in item for item in results.values()))

        if self._cancelled:
            status = self.STATUS_CANCELLED
            self.progress.emit(completed, total, "已取消")
        elif failed:
            status = self.STATUS_PARTIAL_FAILED
            self.progress.emit(completed, total, f"完成（{failed} 个失败）")
        else:
            status = self.STATUS_COMPLETED
            self.progress.emit(total, total, "完成")

        self._results = results
        self._terminal_status = status
        self.terminal.emit(status, dict(results))

    def _run_batches(self) -> tuple[dict, int, int, int]:
        """执行分批识别，返回 results/completed/total/failed。

        旧实现用逐文件 batch_add（每文件一次 SHM 往返）+ batch_commit 流式回调，
        N 个文件 = 2N+1 次消息交换。改为 recognize_batch（RCBG 单次往返）后，
        N 个文件只需按预算后的批次数次往返，IPC 开销降一个数量级。

        recognize_batch 阻塞返回 list（无流式回调），故按 16 个/批切片，
        现同时按文件数、编码字节数、解码像素数切片；每批完成即逐文件发
        file_completed + progress，保持 UI 流式反馈。
        取消在批边界检查 _cancelled（协作式，单批 predict 进行中不可中断）。
        """
        results: dict = {}
        total = len(self._files)
        completed = 0
        failed = 0
        entries: list[BatchEntry[dict]] = []
        for file_info in self._files:
            path = Path(file_info["path"])
            try:
                encoded_bytes = path.stat().st_size
            except OSError:
                encoded_bytes = 0
            entries.append(
                BatchEntry(
                    value=file_info,
                    encoded_bytes=encoded_bytes,
                    pixels=image_pixel_count(path),
                )
            )

        batches = partition_batches(entries, self._batch_budget)
        for batch_index, chunk in enumerate(batches):
            if self._cancelled:
                break

            batch_files = chunk.values
            logger.info(
                "提交图片 OCR 批次",
                extra={
                    "batch": {
                        "index": batch_index,
                        "items": len(batch_files),
                        "encoded_bytes": chunk.encoded_bytes,
                        "pixels": chunk.pixels,
                        "oversized_single": chunk.oversized_single,
                    }
                },
            )

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
            if self._cancelled:
                break
            if valid_indices:
                valid_images = [images[bi] for bi in valid_indices]  # type: ignore[list-item]
                try:
                    batch_results = self._service.recognize_batch(
                        valid_images, self._preprocess_options
                    )
                except Exception as e:
                    logger.error("批量识别失败(batch=%d): %s", batch_index, e)
                    self.error.emit(str(e))
                    # 识别整批失败：有效文件使用 RPC 错误；本批读取失败文件仍
                    # 保留各自 I/O 错误，确保终态计数覆盖整批。
                    for bi, file_info in enumerate(batch_files):
                        file_path = file_info["path"]
                        error = read_errors.get(bi, str(e))
                        self.file_completed.emit(
                            file_path, "failed", {"error": error}
                        )
                        results[file_path] = {
                            "file_path": file_path,
                            "error": error,
                        }
                        failed += 1
                        completed += 1
                        self.progress.emit(
                            completed, total,
                            f"失败: {Path(file_path).name}",
                        )
                    # 继续下一批（单批失败不中断整体）
                    continue

            # cancel() 可能在 recognize_batch 阻塞期间到达。此时不得把返回结果
            # 再包装成“全部完成”，也不得继续下一批。
            if self._cancelled:
                break

            # 逐文件上报结果（保持 UI 流式反馈）
            result_iter = iter(batch_results)
            for bi, file_info in enumerate(batch_files):
                if self._cancelled:
                    break
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
                    failed += 1
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
                        failed += 1
                    else:
                        self.file_completed.emit(file_path, "completed", res)
                        results[file_path] = {
                            "file_path": file_path,
                            "result": res,
                        }
                completed += 1
                self.progress.emit(completed, total, Path(file_path).name)

        self._results = results
        return results, completed, total, failed

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

    STATE_IDLE = "idle"
    STATE_RUNNING = "running"
    STATE_CANCELLING = "cancelling"
    STATE_SHUTDOWN = "shutdown"

    def __init__(self, ocr_service=None, parent=None, *, backend=None):
        super().__init__(parent)
        self._ocr_service = ocr_service  # MinerUBatchService
        self._paddlex_service = None  # OCRServiceSubprocess
        self._backend = backend
        self._batch_backend = None
        self._worker: BatchRecognitionWorker | None = None
        self._run_state = self.STATE_IDLE
        self._run_total = 0
        self._last_terminal_status: str | None = None
        self._shutting_down = False
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
        # QThread 对象必须保留到原生 finished 信号到达。在 cancelling 或线程
        # 刚退出但 finished 尚未派发的窗口内，一律禁止重入。
        if self._shutting_down or self._worker is not None:
            return

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
        self._run_state = self.STATE_RUNNING
        self._run_total = len(files)
        self._last_terminal_status = None

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

        worker = BatchRecognitionWorker(service, files, preprocess_options)
        self._worker = worker
        worker.progress.connect(self._on_progress)
        worker.file_completed.connect(self._on_file_completed)
        worker.terminal.connect(self._on_terminal)
        worker.error.connect(self._on_error)
        worker.finished.connect(lambda: self._on_worker_stopped(worker))
        worker.start()

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
        """请求取消；线程真正结束前不释放引用、也不允许重新开始。"""
        worker = self._worker
        if worker is None or self._run_state != self.STATE_RUNNING:
            return

        self._run_state = self.STATE_CANCELLING
        self._start_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        self._progress_label.setText("正在取消…")
        worker.cancel()

    def _is_current_worker_signal(self) -> bool:
        """过滤已经释放/替换的 worker 迟到信号。"""
        if self._shutting_down:
            return False
        sender = self.sender()
        return sender is None or sender is self._worker

    def _on_progress(self, completed: int, total: int, current_file: str):
        """进度更新"""
        if not self._is_current_worker_signal():
            return
        if self._run_state == self.STATE_CANCELLING and current_file != "已取消":
            return
        self._progress_label.setText(
            f"{completed}/{total} {current_file}"
            if current_file
            else f"{completed}/{total}"
        )

    def _on_file_completed(self, file_path: str, status: str, result):
        """单个文件完成"""
        if not self._is_current_worker_signal():
            return
        self._file_list_widget.update_file_status(file_path, status, result)

        # 如果是当前选中的文件，刷新显示
        if file_path == self._current_file_path and status == "completed" and result:
            self._display_result(result)
            self._export_widget.set_current_result(result)

    def _on_terminal(self, status: str, results: dict):
        """记录业务终态；引用释放仍等待 QThread.finished。"""
        if not self._is_current_worker_signal():
            return
        self._apply_terminal(status, results)

    def _apply_terminal(self, status: str, results: dict) -> None:
        """将明确的 completed/cancelled/partial_failed 终态呈现到 UI。"""
        self._last_terminal_status = status
        completed = len([r for r in results.values() if "result" in r])
        failed = len([r for r in results.values() if "error" in r])

        if status == BatchRecognitionWorker.STATUS_CANCELLED:
            processed = completed + failed
            self._progress_label.setText(f"{processed}/{self._run_total} 已取消")
            logger.info("批量处理已取消: %d 成功, %d 失败", completed, failed)
        elif status == BatchRecognitionWorker.STATUS_PARTIAL_FAILED:
            self._progress_label.setText(
                f"{completed + failed}/{self._run_total} 完成，{failed} 个失败"
            )
            logger.warning("批量处理部分失败: %d 成功, %d 失败", completed, failed)
        else:
            self._progress_label.setText(f"{self._run_total}/{self._run_total} 完成")
            logger.info("批量处理完成: %d 成功", completed)

        self._cancel_btn.setEnabled(False)

    def _on_worker_stopped(self, worker: BatchRecognitionWorker) -> None:
        """QThread 已真实退出后，才释放引用并恢复可启动状态。"""
        if worker is not self._worker:
            return

        # drain() 阻塞等待期间 Qt 事件不会派发，直接从 worker 快照补齐终态。
        if self._last_terminal_status is None and not self._shutting_down:
            status = worker.terminal_status or BatchRecognitionWorker.STATUS_PARTIAL_FAILED
            self._apply_terminal(status, worker.results)

        self._release_worker(worker)

    def _release_worker(self, worker: BatchRecognitionWorker) -> None:
        if worker is not self._worker or worker.isRunning():
            return
        self._worker = None
        self._run_state = (
            self.STATE_SHUTDOWN if self._shutting_down else self.STATE_IDLE
        )
        self._start_btn.setEnabled(not self._shutting_down)
        self._cancel_btn.setEnabled(False)
        worker.deleteLater()

    def _on_error(self, error_msg: str):
        """记录单批错误；worker 会继续处理，故不得重置 UI。"""
        logger.error("Batch recognition error: %s", error_msg)

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
        """兼容性 UI 复位；运行中的 worker 永远不能由此释放。"""
        if self._worker is not None:
            return
        self._start_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._run_state = self.STATE_IDLE

    def drain(self, timeout_ms: int = 0) -> bool:
        """有界等待当前 worker 退出；成功时安全释放引用。

        该方法供 MainWindow 的统一退出协调器调用。timeout_ms=0 仅探测，
        不进入事件循环，也不会无限等待。
        """
        worker = self._worker
        if worker is None:
            return True
        if QThread.currentThread() is worker:
            return False

        if worker.isRunning() and timeout_ms > 0:
            worker.wait(max(0, int(timeout_ms)))
        if worker.isRunning():
            return False

        self._on_worker_stopped(worker)
        return self._worker is None

    def shutdown(self, timeout_ms: int = 1000) -> bool:
        """请求取消并在给定预算内排空线程，绝不丢失运行中引用。"""
        self._shutting_down = True
        worker = self._worker
        if worker is None:
            self._run_state = self.STATE_SHUTDOWN
            self._start_btn.setEnabled(False)
            self._cancel_btn.setEnabled(False)
            return True

        self._run_state = self.STATE_CANCELLING
        self._start_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        worker.cancel()
        return self.drain(timeout_ms)

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
