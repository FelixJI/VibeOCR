"""工具函数"""

from .markdown_converter import markdown_to_html, extract_plain_text, HTML_STYLE
from .indent_processor import IndentProcessor, IndentConfig
from .shared_memory import (
    SharedMemoryProtocol,
    SharedMemoryProtocolError,
    MSG_INIT,
    MSG_RECOGNIZE,
    MSG_RESULT,
    MSG_ERROR,
    MSG_SHUTDOWN,
    MSG_ACK,
    serialize_request,
    deserialize_request,
    serialize_result,
    deserialize_result,
)

__all__ = [
    "markdown_to_html",
    "extract_plain_text",
    "HTML_STYLE",
    "IndentProcessor",
    "IndentConfig",
    "SharedMemoryProtocol",
    "SharedMemoryProtocolError",
    "MSG_INIT",
    "MSG_RECOGNIZE",
    "MSG_RESULT",
    "MSG_ERROR",
    "MSG_SHUTDOWN",
    "MSG_ACK",
    "serialize_request",
    "deserialize_request",
    "serialize_result",
    "deserialize_result",
]
