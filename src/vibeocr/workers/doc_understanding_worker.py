"""文档理解工作线程

在后台执行 PaddleX doc_understanding 管道调用，避免阻塞 UI。
"""

import logging
from typing import Optional

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)


class DocUnderstandingWorker(QThread):
    """文档理解工作线程

    在后台执行 doc_understanding 管道调用。
    """

    # 信号定义
    finished = Signal(str)  # AI 回答
    error = Signal(str)  # 错误消息

    # 支持的模型
    AVAILABLE_MODELS = [
        "PP-DocBee-2B",
        "PP-DocBee-7B",
        "PP-DocBee2-3B",
    ]

    def __init__(
        self,
        image_path: str,
        query: str,
        model: str = "PP-DocBee2-3B",
        parent=None
    ):
        super().__init__(parent)
        self._image_path = image_path
        self._query = query
        self._model = model
        self._cancelled = False

    def run(self):
        """执行文档理解任务"""
        if self._cancelled:
            return

        try:
            # 调用 PaddleX 文档理解管道
            result = self._call_pipeline()

            if not self._cancelled:
                self.finished.emit(result)

        except Exception as e:
            logger.error(f"文档理解失败: {e}")
            if not self._cancelled:
                self.error.emit(str(e))

    def _call_pipeline(self) -> str:
        """调用 PaddleX 管道

        Returns:
            AI 回答文本
        """
        try:
            from paddlex import create_pipeline

            # 创建管道
            pipeline = create_pipeline(pipeline="doc_understanding")

            # 执行预测
            output = pipeline.predict({
                "image": self._image_path,
                "query": self._query
            })

            # 提取结果
            for res in output:
                # res.json 包含完整结果
                if hasattr(res, 'json') and res.json:
                    return res.json.get('result', '')

            return "无法获取回答"

        except ImportError:
            logger.warning("PaddleX 未安装，返回模拟结果")
            return f"[模拟] 针对文档 {self._image_path} 的回答：{self._query}"
        except Exception as e:
            raise RuntimeError(f"管道调用失败: {e}")

    def cancel(self):
        """取消任务"""
        self._cancelled = True
