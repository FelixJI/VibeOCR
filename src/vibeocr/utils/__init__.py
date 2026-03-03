"""工具函数"""

from .indent_processor import IndentConfig, IndentProcessor
from .markdown_converter import HTML_STYLE, extract_plain_text, markdown_to_html
from .shared_memory_v2 import (
    MSG_ACK,
    MSG_ERROR,
    MSG_INIT,
    MSG_RECOGNIZE,
    MSG_RESULT,
    MSG_SHUTDOWN,
    SharedMemoryProtocolError,
    deserialize_request,
    deserialize_result,
    serialize_request,
    serialize_result,
)
from .shared_memory_v2 import SharedMemoryProtocolV2 as SharedMemoryProtocol

__all__ = [
    "HTML_STYLE",
    "MSG_ACK",
    "MSG_ERROR",
    "MSG_INIT",
    "MSG_RECOGNIZE",
    "MSG_RESULT",
    "MSG_SHUTDOWN",
    "IndentConfig",
    "IndentProcessor",
    "SharedMemoryProtocol",
    "SharedMemoryProtocolError",
    "deserialize_request",
    "deserialize_result",
    "extract_plain_text",
    "markdown_to_html",
    "serialize_request",
    "serialize_result",
]
