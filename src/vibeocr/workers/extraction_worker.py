# src/vibeocr/workers/extraction_worker.py
"""信息抽取工作线程"""

import logging
from typing import Any

from PySide6.QtCore import Signal

from vibeocr.core import BatchWorker
from vibeocr.models.extraction_options import ExtractionOptions

logger = logging.getLogger(__name__)


class ExtractionWorker(BatchWorker[dict[str, Any]]):
    """信息抽取工作线程

    在后台执行 PP-ChatOCRv4 产线调用，避免阻塞 UI。
    """

    # 信号定义（继承并可能扩展基类信号）
    progress = Signal(int, int, str)  # completed, total, current_file
    file_completed = Signal(str, str, dict)  # file_path, status, result
    finished = Signal(dict)  # all results
    error = Signal(str)  # error message

    def __init__(
        self,
        service,
        files: list[dict],
        keys: list[str],
        options: ExtractionOptions,
        llm_config: dict | None = None,
        parent=None,
    ):
        """初始化抽取 Worker

        Args:
            service: OCR 服务实例
            files: 文件信息列表，每项为 dict 包含 path, name 等
            keys: 要抽取的字段列表
            options: 抽取选项
            llm_config: LLM 配置
            parent: 父对象
        """
        super().__init__(files, parent)
        self._service = service
        self._keys = keys
        self._options = options
        self._llm_config = llm_config

    def _process_item(self, item: dict, index: int) -> dict[str, Any]:
        """处理单个文件

        Args:
            item: 文件信息字典
            index: 文件索引

        Returns:
            抽取结果字典
        """
        file_path = item.get("path", "")

        # 读取文件
        with open(file_path, "rb") as f:
            image_data = f.read()

        # 执行抽取
        return self._extract(image_data, self._get_file_name(item))

    def _extract(self, image_data: bytes, file_name: str) -> dict[str, Any]:
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
