# src/vibeocr/workers/extraction_worker.py
"""信息抽取工作线程"""

import logging
from typing import List, Dict, Any, Optional
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from vibeocr.models.extraction_options import ExtractionOptions

logger = logging.getLogger(__name__)


class ExtractionWorker(QThread):
    """信息抽取工作线程

    在后台执行 PP-ChatOCRv4 产线调用，避免阻塞 UI。
    """

    # 信号定义
    progress = Signal(int, int, str)  # completed, total, current_file
    file_completed = Signal(str, str, dict)  # file_path, status, result
    finished = Signal(dict)  # all results
    error = Signal(str)  # error message

    def __init__(
        self,
        service,
        files: List[dict],
        keys: List[str],
        options: ExtractionOptions,
        llm_config: Optional[dict] = None,
        parent=None
    ):
        super().__init__(parent)
        self._service = service
        self._files = files
        self._keys = keys
        self._options = options
        self._llm_config = llm_config
        self._cancelled = False

    def run(self):
        """执行抽取任务"""
        results = {}
        total = len(self._files)

        if total == 0:
            self.finished.emit(results)
            return

        for i, file_info in enumerate(self._files):
            if self._cancelled:
                break

            file_path = file_info["path"]
            file_name = file_info.get("name", Path(file_path).name)

            self.progress.emit(i, total, file_name)

            try:
                # 读取文件
                with open(file_path, "rb") as f:
                    image_data = f.read()

                # 调用 OCR 服务进行抽取
                result = self._extract(image_data, file_name)

                self.file_completed.emit(file_path, "completed", result)
                results[file_path] = {
                    "file_path": file_path,
                    "file_name": file_name,
                    "result": result,
                }

            except Exception as e:
                logger.error(f"抽取失败 {file_path}: {e}")
                self.file_completed.emit(file_path, "failed", {"error": str(e)})
                results[file_path] = {
                    "file_path": file_path,
                    "file_name": file_name,
                    "error": str(e),
                }

        self.finished.emit(results)

    def _extract(self, image_data: bytes, file_name: str) -> dict:
        """执行单个文件的抽取

        Args:
            image_data: 图像数据
            file_name: 文件名

        Returns:
            抽取结果字典
        """
        # TODO: 实际调用 PP-ChatOCRv4 产线
        # 这里是一个占位实现
        if self._service is None:
            raise RuntimeError("OCR 服务未设置")

        # 调用服务的抽取方法
        # result = self._service.extract(image_data, self._keys, self._options, self._llm_config)

        # 临时返回模拟数据
        return {
            "keys": self._keys,
            "values": {k: f"模拟值_{k}" for k in self._keys},
        }

    def cancel(self):
        """取消任务"""
        self._cancelled = True
