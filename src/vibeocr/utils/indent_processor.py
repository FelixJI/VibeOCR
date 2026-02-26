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

    def process_markdown(self, markdown_text: str) -> str:
        """处理 Markdown 文本，为中文段落添加标记

        Args:
            markdown_text: 原始 Markdown 文本

        Returns:
            处理后的 Markdown 文本，中文段落被包装在 zh-paragraph div 中
        """
        if not markdown_text:
            return ""

        # 检测代码块和表格的正则
        code_block_pattern = re.compile(r'^```.*?^```', re.MULTILINE | re.DOTALL)
        table_pattern = re.compile(r'^\|.*\|$', re.MULTILINE)
        list_pattern = re.compile(r'^[\*\-\+]\s|^\d+\.\s', re.MULTILINE)

        # 跳过代码块、表格、列表
        if code_block_pattern.search(markdown_text):
            return markdown_text
        if table_pattern.search(markdown_text):
            return markdown_text
        if list_pattern.search(markdown_text):
            return markdown_text

        # 按双换行分割段落
        paragraphs = markdown_text.split('\n\n')
        processed = []

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            # 跳过单行代码块标记
            if para.startswith('```'):
                processed.append(para)
                continue

            if self.is_chinese_text(para):
                processed.append(f'<div class="zh-paragraph">{para}</div>')
            else:
                processed.append(para)

        return '\n\n'.join(processed)
