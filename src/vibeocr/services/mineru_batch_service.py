"""MinerU 直接批量处理服务

绕过子进程层，直接在调用线程中调用 MinerUService 进行批量文档解析。
保留 batch_add / batch_commit / batch_cancel 接口。
"""

import logging
import uuid
from typing import TYPE_CHECKING, Callable

from vibeocr.utils.mime_types import guess_mime_from_filename

if TYPE_CHECKING:
    from vibeocr.models.ocr_options import OCROptions

logger = logging.getLogger(__name__)


class MinerUBatchService:
    """MinerU 直接批量处理服务

    在调用线程（通常是 QThread）中直接调用 MinerUService，
    无需经过共享内存子进程通信。

    接口兼容 OCRServiceSubprocess 的 batch_add/batch_commit/batch_cancel。
    """

    def __init__(self):
        self._queue: list[dict] = []
        self._cancelled = False
        self._request_map: dict[str, dict] = {}

    def batch_add(
        self,
        image: bytes,
        options: "OCROptions | None" = None,  # noqa: ARG002
        file_name: str = "",
    ) -> str:
        """添加文件到批量队列

        Args:
            image: 文件数据 (bytes)
            options: OCR 选项（当前未使用）
            file_name: 文件名

        Returns:
            request_id
        """
        request_id = uuid.uuid4().hex[:12]
        item = {
            "request_id": request_id,
            "data": image,
            "file_name": file_name,
            "mime_type": guess_mime_from_filename(file_name),
        }
        self._queue.append(item)
        self._request_map[request_id] = item
        logger.debug(f"[MinerUBatch] 添加文件: {file_name}, request_id={request_id}")
        return request_id

    def batch_commit(
        self,
        preprocess_options=None,
        timeout: float = 300.0,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> dict:
        """执行批量处理

        Args:
            preprocess_options: OCROptions 预处理选项，传递给 MinerUService.parse()
            timeout: 超时时间（秒）
            progress_callback: 进度回调 (completed, total, current_file)

        Returns:
            {request_id: OCRResult} 结果字典
        """
        from vibeocr.services.mineru_service import MinerUService

        self._cancelled = False
        results = {}

        if progress_callback:
            progress_callback(0, len(self._queue), "正在启动 MineRU 服务...")

        mineru = MinerUService()
        queue = list(self._queue)
        total = len(queue)

        logger.info(f"[MinerUBatch] 开始处理 {total} 个文件")

        for i, item in enumerate(queue):
            if self._cancelled:
                break

            file_name = item["file_name"]
            logger.info(f"[MinerUBatch] 处理 {i + 1}/{total}: {file_name}")

            if progress_callback:
                progress_callback(i, total, file_name)

            try:
                result = mineru.parse(
                    data=item["data"],
                    mime_type=item["mime_type"],
                    options=preprocess_options,
                )
                results[item["request_id"]] = result
            except Exception as e:
                logger.error(f"[MinerUBatch] 处理失败 {file_name}: {e}")
                results[item["request_id"]] = {"error": str(e)}

        if progress_callback and not self._cancelled:
            progress_callback(total, total, "完成")

        self._queue.clear()
        self._request_map.clear()

        logger.info(f"[MinerUBatch] 处理完成: {len(results)} 个结果")
        return results

    def batch_cancel(self):
        """取消批量处理"""
        self._cancelled = True
        logger.info("[MinerUBatch] 取消批量处理")
