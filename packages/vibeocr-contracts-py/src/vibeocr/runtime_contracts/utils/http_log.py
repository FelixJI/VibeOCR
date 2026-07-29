"""HTTP 日志增强工具：把状态码转换为中文说明，统一输出交易摘要。"""

from __future__ import annotations

# Shared by the client and backend distributions.
from http import HTTPStatus
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, quote_plus, urlsplit

if TYPE_CHECKING:
    import logging

if not hasattr(HTTPStatus, "__members__"):  # pragma: no cover - HTTPStatus always has __members__
    HTTPStatus = HTTPStatus  # type: ignore[assignment]


_RANGE_HINTS = {
    "2": "请求已成功执行",
    "3": "重定向，需要客户端按新地址继续",
    "4": "客户端请求有问题，请检查参数/鉴权/版本",
    "5": "服务端处理失败，请检查后台日志与资源状态",
}


_CODE_HINTS = {
    100: "继续（信息）",
    101: "协议切换（继续）",
    200: "成功处理",
    201: "已创建新资源",
    202: "已接收，异步处理中",
    204: "处理完成且未返回正文",
    301: "永久重定向",
    302: "临时重定向",
    304: "资源未变更（可复用缓存）",
    307: "临时重定向（保持方法）",
    308: "永久重定向（保持方法）",
    400: "参数有误或请求非法",
    401: "未授权或登录过期",
    403: "权限不足",
    404: "接口/资源不存在",
    408: "请求超时",
    409: "状态冲突（并发/资源版本冲突）",
    410: "资源已不可用",
    413: "请求体过大",
    415: "媒体类型不支持",
    422: "参数校验失败",
    423: "资源锁定或暂不可写",
    425: "请先升级 TLS/协议版本",
    426: "需要升级协议",
    429: "请求过于频繁（限流）",
    500: "服务端内部错误",
    501: "服务端不支持该功能",
    502: "上游服务异常或网关错误",
    503: "服务暂不可用",
    504: "网关/上游超时",
    507: "磁盘空间不足导致处理失败",
}


def status_zh_meaning(status_code: int) -> str:
    """返回状态码中文说明（支持常见状态码，不支持时按百位段落兜底）。"""
    if status_code in _CODE_HINTS:
        return _CODE_HINTS[status_code]
    bucket = _RANGE_HINTS.get(str(status_code // 100))
    return bucket or "未知结果"


def status_summary(status_code: int) -> str:
    """返回“状态码 + 英文短语 + 中文说明”。"""
    try:
        phrase = HTTPStatus(status_code).phrase
    except Exception:
        phrase = "Unknown"
    return f"{status_code} {phrase}（{status_zh_meaning(status_code)}）"


def _shorten_path(url: str) -> str:
    parsed = urlsplit(url)
    path = parsed.path or "/"
    if parsed.query:
        query = "&".join(
            f"{quote_plus(key)}=<redacted>"
            for key, _value in parse_qsl(parsed.query, keep_blank_values=True)
        )
        if query:
            path = f"{path}?{query}"
    if not path.startswith("/"):
        path = f"/{path}"
    return path


def _human_bytes(size: int | None) -> str | None:
    if size is None:
        return None
    units = ["B", "KB", "MB", "GB"]
    s = float(size)
    for unit in units:
        if s < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(s)} {unit}"
            return f"{s:.1f} {unit}"
        s /= 1024
    return f"{s:.1f} {units[-1]}"  # pragma: no cover - loop always exits via unit==units[-1]


def format_http_transaction(
    method: str,
    url: str,
    status_code: int,
    *,
    reason: str | None = None,
    elapsed_ms: float | None = None,
    request_bytes: int | None = None,
    response_bytes: int | None = None,
    stream: bool = False,
) -> str:
    """构造一行可读 HTTP 日志。"""
    items = [
        f"{method.upper()} {_shorten_path(url)}",
        status_summary(status_code),
    ]
    if reason:
        items.append(str(reason))
    if stream:
        items.append("stream=True")
    if elapsed_ms is not None:
        items.append(f"耗时={elapsed_ms:.1f}ms")
    req = _human_bytes(request_bytes)
    if req is not None:
        items.append(f"请求体={req}")
    resp = _human_bytes(response_bytes)
    if resp is not None:
        items.append(f"返回体={resp}")
    return " | ".join(items)


def _safe_int(value: object) -> int | None:
    try:
        if value is None:
            return None
        return int(str(value))
    except Exception:
        return None


def log_http_response(
    logger: logging.Logger,
    method: str,
    url: str,
    status_code: int,
    reason: str | None = None,
    elapsed_ms: float | None = None,
    request_bytes: int | None = None,
    response_bytes: int | None = None,
    stream: bool = False,
) -> None:
    """按等级输出 HTTP 结果日志。"""
    message = format_http_transaction(
        method=method,
        url=url,
        status_code=status_code,
        reason=reason,
        elapsed_ms=elapsed_ms,
        request_bytes=request_bytes,
        response_bytes=response_bytes,
        stream=stream,
    )
    if status_code >= 500:
        logger.error("%s", message)
    elif status_code >= 400:
        logger.warning("%s", message)
    else:
        logger.debug("%s", message)


def guess_response_size(headers: dict[str, str] | None, content: bytes | str | None) -> int | None:
    """从 headers/content 里取返回体字节数。"""
    if content is not None:
        try:
            if isinstance(content, str):
                return len(content.encode("utf-8"))
            return len(content)
        except Exception:  # pragma: no cover - len() on valid bytes/str never raises
            pass
    if headers is None:
        return None
    value = headers.get("content-length") or headers.get("Content-Length")
    return _safe_int(value)


def guess_request_size(content: bytes | bytearray | str | None) -> int | None:
    if content is None:
        return None
    try:
        if isinstance(content, str):
            return len(content.encode("utf-8"))
        return len(content)
    except Exception:  # pragma: no cover - len() on valid bytes/str never raises
        return None
