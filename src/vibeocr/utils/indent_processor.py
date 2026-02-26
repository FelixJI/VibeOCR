"""缩进处理器模块"""

import re
from dataclasses import dataclass


@dataclass
class IndentConfig:
    """缩进配置"""
    chinese_indent: str = "2em"
    chinese_threshold: float = 0.05


class IndentProcessor:
    """处理文本缩进的处理器"""

    CHINESE_PATTERN = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf\U00020000-\U0002a6df]')

    def __init__(self, config: IndentConfig | None = None):
        self.config = config or IndentConfig()

    def is_chinese_text(self, text: str) -> bool:
        """检测文本是否主要为中文

        Args:
            text: 待检测文本

        Returns:
            如果中文字符占比 >= chinese_threshold 则返回 True
        """
        if not text.strip():
            return False
        chinese_chars = len(self.CHINESE_PATTERN.findall(text))
        total_chars = len(text.strip())
        return chinese_chars / total_chars >= self.config.chinese_threshold
