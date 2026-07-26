"""Qt-free shared utility namespace.

v2 重写后移除了共享内存 (shared_memory_v2) 的导出——supervisor 通过 localhost
HTTP 传输结果，不再使用 SHM fast path。本命名空间只保留与传输无关的纯工具
（缩进处理、Markdown 转换）。
"""

from pkgutil import extend_path

from .indent_processor import IndentConfig, IndentProcessor
from .markdown_converter import HTML_STYLE, extract_plain_text, markdown_to_html

__path__ = extend_path(__path__, __name__)

__all__ = [
    "HTML_STYLE",
    "IndentConfig",
    "IndentProcessor",
    "extract_plain_text",
    "markdown_to_html",
]
