from __future__ import annotations

import io
import json
import logging

from vibeocr.logging_context import (
    JsonLogFormatter,
    configure_worker_stderr_logging,
    forward_worker_output_line,
)


def _isolated_logger(name: str, stream: io.StringIO) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter(frontend="pyside", profile="production"))
    logger.addHandler(handler)
    return logger


def test_json_formatter_has_stable_fields_context_and_exception() -> None:
    stream = io.StringIO()
    logger = _isolated_logger("test.structured", stream)
    try:
        raise ValueError("broken")
    except ValueError:
        logger.exception(
            "recognition failed",
            extra={
                "event": "ocr.failed",
                "request_id": "req-1",
                "pipeline": "OCR",
            },
        )

    document = json.loads(stream.getvalue())
    assert {
        "timestamp",
        "level",
        "logger",
        "process",
        "thread",
        "event",
        "frontend",
        "profile",
        "message",
        "exception",
    }.issubset(document)
    assert document["level"] == "ERROR"
    assert document["event"] == "ocr.failed"
    assert document["request_id"] == "req-1"
    assert document["pipeline"] == "OCR"
    assert "ValueError: broken" in document["exception"]


def test_worker_logging_defaults_to_info_and_suppresses_http_debug(
    monkeypatch,
) -> None:
    monkeypatch.delenv("VIBEOCR_LOG_LEVEL", raising=False)
    stream = io.StringIO()
    root = logging.getLogger()
    old_handlers = root.handlers[:]
    old_level = root.level
    noisy = logging.getLogger("httpcore.http11")
    old_noisy_level = noisy.level
    try:
        handler = configure_worker_stderr_logging(
            frontend="pyside", profile="production", stream=stream
        )
        assert handler.level == logging.INFO
        assert root.level == logging.INFO
        assert noisy.getEffectiveLevel() == logging.WARNING
        noisy.debug("receive_response_headers.started")
        assert stream.getvalue() == ""
    finally:
        root.handlers.clear()
        root.handlers.extend(old_handlers)
        root.setLevel(old_level)
        noisy.setLevel(old_noisy_level)


def test_forward_worker_json_preserves_severity_traceback_and_context() -> None:
    stream = io.StringIO()
    logger = _isolated_logger("main.worker_bridge", stream)
    line = json.dumps(
        {
            "timestamp": "2026-07-19T01:02:03.004Z",
            "level": "ERROR",
            "logger": "vibeocr.worker_host.handler",
            "process": 42,
            "thread": "worker-thread",
            "event": "ocr.failed",
            "frontend": "pyside",
            "profile": "production",
            "message": "backend failed",
            "exception": "Traceback...\nRuntimeError: boom",
            "request_id": "req-7",
            "pipeline": "OCR",
            "model": "detector-v1",
        }
    )

    assert forward_worker_output_line(
        logger, line, fallback_level=logging.WARNING, stream_name="stderr"
    )
    document = json.loads(stream.getvalue())
    assert document["level"] == "ERROR"
    assert document["logger"] == "vibeocr.worker_host.handler"
    assert document["process"] == 42
    assert document["thread"] == "worker-thread"
    assert document["exception"].endswith("RuntimeError: boom")
    assert document["request_id"] == "req-7"
    assert document["pipeline"] == "OCR"
    assert document["context"] == {"model": "detector-v1"}


def test_forward_non_json_line_uses_safe_fallback_level() -> None:
    stream = io.StringIO()
    logger = _isolated_logger("main.worker_fallback", stream)
    assert not forward_worker_output_line(
        logger,
        "native library warning",
        fallback_level=logging.WARNING,
        stream_name="stderr",
    )
    document = json.loads(stream.getvalue())
    assert document["level"] == "WARNING"
    assert document["event"] == "worker.output"
    assert document["message"] == "WorkerHost stderr: native library warning"


def test_coerce_level_int_zero_or_negative_returns_fallback() -> None:
    """int<=0 时返回 fallback（line 118）。"""
    import logging

    from vibeocr.logging_context import _coerce_level

    assert _coerce_level(0, logging.WARNING) == logging.WARNING
    assert _coerce_level(-5, logging.WARNING) == logging.WARNING
    assert _coerce_level(20, logging.WARNING) == 20  # 正常 int 透传


def test_coerce_level_unknown_type_returns_fallback() -> None:
    """非 int/str 类型返回 fallback（line 123）。"""
    import logging

    from vibeocr.logging_context import _coerce_level

    assert _coerce_level(None, logging.WARNING) == logging.WARNING
    assert _coerce_level([1, 2], logging.WARNING) == logging.WARNING


def test_forward_worker_empty_line_returns_false() -> None:
    """空行/纯空白返回 False（line 140）。"""
    import logging

    from vibeocr.logging_context import forward_worker_output_line

    logger = logging.getLogger("test_empty")
    assert (
        forward_worker_output_line(
            logger, "", fallback_level=logging.INFO, stream_name="stderr"
        )
        is False
    )
    assert (
        forward_worker_output_line(
            logger, "   \n", fallback_level=logging.INFO, stream_name="stderr"
        )
        is False
    )


def test_ui_status_extra_builds_dict() -> None:
    """ui_status_extra 构造 ui_status 标记 dict（line 184）。"""
    from vibeocr.logging_context import ui_status_extra

    result = ui_status_extra(tab="ocr", action="start")
    assert result["ui_status"] is True
    assert result["event"] == "ui.status"
    assert result["tab"] == "ocr"
    assert result["action"] == "start"
