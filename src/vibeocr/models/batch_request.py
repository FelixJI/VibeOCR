"""批量请求数据模型"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import uuid
import time


class BatchRequestStatus(Enum):
    """批量请求状态"""
    PENDING = "pending"       # 等待处理
    PROCESSING = "processing" # 处理中
    COMPLETED = "completed"   # 已完成
    FAILED = "failed"         # 失败
    CANCELLED = "cancelled"   # 已取消


@dataclass
class BatchRequest:
    """单个批量请求

    代表一个待处理的图片识别请求。
    """
    # 请求标识
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    # 文件信息
    file_path: str = ""
    file_name: str = ""

    # 图像数据
    image_data: bytes = b""

    # OCR 选项
    options: dict = field(default_factory=dict)

    # 状态
    status: BatchRequestStatus = BatchRequestStatus.PENDING

    # 结果
    result: Optional[object] = None
    error_message: str = ""

    # 时间戳
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    def mark_processing(self):
        """标记为处理中"""
        self.status = BatchRequestStatus.PROCESSING
        self.started_at = time.time()

    def mark_completed(self, result):
        """标记为已完成"""
        self.status = BatchRequestStatus.COMPLETED
        self.result = result
        self.completed_at = time.time()

    def mark_failed(self, error: str):
        """标记为失败"""
        self.status = BatchRequestStatus.FAILED
        self.error_message = error
        self.completed_at = time.time()

    def mark_cancelled(self):
        """标记为已取消"""
        self.status = BatchRequestStatus.CANCELLED
        self.completed_at = time.time()

    @property
    def elapsed_time(self) -> Optional[float]:
        """已用时间（秒）"""
        if self.started_at is None:
            return None
        end_time = self.completed_at or time.time()
        return end_time - self.started_at

    @property
    def is_finished(self) -> bool:
        """是否已结束（完成/失败/取消）"""
        return self.status in (
            BatchRequestStatus.COMPLETED,
            BatchRequestStatus.FAILED,
            BatchRequestStatus.CANCELLED,
        )


@dataclass
class PreprocessOptions:
    """预处理选项

    对应 PP-StructureV3 的预处理参数。
    """
    use_doc_orientation_classify: bool = True   # 文档方向分类
    use_doc_unwarping: bool = True              # 文档扭曲矫正
    use_textline_orientation: bool = False      # 文本行方向分类

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            'use_doc_orientation_classify': self.use_doc_orientation_classify,
            'use_doc_unwarping': self.use_doc_unwarping,
            'use_textline_orientation': self.use_textline_orientation,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PreprocessOptions":
        """从字典创建"""
        return cls(
            use_doc_orientation_classify=data.get('use_doc_orientation_classify', True),
            use_doc_unwarping=data.get('use_doc_unwarping', True),
            use_textline_orientation=data.get('use_textline_orientation', False),
        )


@dataclass
class BatchProgress:
    """批量处理进度"""
    total: int = 0
    completed: int = 0
    failed: int = 0
    current_file: str = ""
    current_batch_size: int = 0

    @property
    def progress_percent(self) -> float:
        """进度百分比"""
        if self.total == 0:
            return 0.0
        return (self.completed / self.total) * 100

    @property
    def remaining(self) -> int:
        """剩余数量"""
        return self.total - self.completed - self.failed
