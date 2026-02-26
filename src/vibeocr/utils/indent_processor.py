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

    def __init__(self, config: IndentConfig = None):
        self.config = config or IndentConfig()
