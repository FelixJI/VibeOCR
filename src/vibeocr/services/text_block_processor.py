"""向后兼容重导出。

TextBlockProcessor 已迁移到 ``vibeocr.utils.text_layout``（ADR §5.2：输出排版
属于 UI 层）。此处仅为兼容现有后端引用保留重导出，UI 层应直接从
``vibeocr.utils.text_layout`` 导入。
"""

from vibeocr.utils.text_layout import TextBlockProcessor

__all__ = ["TextBlockProcessor"]
