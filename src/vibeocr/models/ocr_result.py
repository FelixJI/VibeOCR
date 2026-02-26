"""OCR 识别结果数据类"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class OCRResult:
    """OCR 识别结果

    Attributes:
        raw_text: 纯文本内容
        markdown_text: Markdown 格式内容（包含表格、公式等）
        html_text: HTML 格式内容（用于富文本显示）
        text_with_scores: 文本块及置信度列表 [(文本, 置信度), ...]
        avg_score: 平均置信度
        low_confidence_items: 低置信度文本块列表 [(文本, 置信度), ...]
        pipeline_type: 管道类型名称
        images: 图像字典（如 markdown_images）
    """

    raw_text: str = ""
    markdown_text: str = ""
    html_text: str = ""
    text_with_scores: list[tuple[str, float]] = field(default_factory=list)
    avg_score: float = 0.0
    low_confidence_items: list[tuple[str, float]] = field(default_factory=list)
    pipeline_type: str = "OCR"
    images: dict[str, Any] = field(default_factory=dict)

    @property
    def has_rich_content(self) -> bool:
        """是否有富文本内容（表格、公式等）"""
        return bool(self.html_text and self.html_text != self.raw_text)

    @property
    def display_text(self) -> str:
        """用于显示的文本（优先使用富文本）"""
        return self.html_text if self.has_rich_content else self.raw_text

    @property
    def copy_text(self) -> str:
        """用于复制的纯文本（Markdown 格式）"""
        return self.markdown_text if self.markdown_text else self.raw_text
