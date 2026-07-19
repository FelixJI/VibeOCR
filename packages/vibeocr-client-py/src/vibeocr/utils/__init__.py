"""Qt-free shared utility namespace."""

from pkgutil import extend_path

from .indent_processor import IndentConfig, IndentProcessor
from .markdown_converter import HTML_STYLE, extract_plain_text, markdown_to_html

__path__ = extend_path(__path__, __name__)

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

_SHARED_MEMORY_EXPORTS = {
    "MSG_ACK",
    "MSG_ERROR",
    "MSG_INIT",
    "MSG_RECOGNIZE",
    "MSG_RESULT",
    "MSG_SHUTDOWN",
    "SharedMemoryProtocolError",
    "deserialize_request",
    "deserialize_result",
    "serialize_request",
    "serialize_result",
}


def __getattr__(name: str):
    """兼容旧导出，但只在调用时要求 backend wheel。"""
    if name != "SharedMemoryProtocol" and name not in _SHARED_MEMORY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    import importlib

    module = importlib.import_module("vibeocr.utils.shared_memory_v2")
    target = "SharedMemoryProtocolV2" if name == "SharedMemoryProtocol" else name
    value = getattr(module, target)
    globals()[name] = value
    return value
