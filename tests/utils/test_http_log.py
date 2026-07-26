import logging

import pytest

from vibeocr.utils import http_log


def test_status_summary_explains_known_and_unknown_codes() -> None:
    assert "200 OK" in http_log.status_summary(200)
    assert "成功处理" in http_log.status_summary(200)
    assert "599 Unknown" in http_log.status_summary(599)
    assert "服务端处理失败" in http_log.status_summary(599)


def test_transaction_redacts_query_values_and_lists_metrics(
) -> None:
    message = http_log.format_http_transaction(
        "post",
        "http://127.0.0.1:61335/session/model?token=secret&page=3",
        422,
        reason="Unprocessable Entity",
        elapsed_ms=12.34,
        request_bytes=1024,
        response_bytes=2048,
        stream=True,
    )

    assert "POST /session/model?token=<redacted>&page=<redacted>" in message
    assert "secret" not in message
    assert "422 " in message
    assert "Unprocessable Entity" in message
    assert "参数校验失败" in message
    assert "耗时=12.3ms" in message
    assert "请求体=1.0 KB" in message
    assert "返回体=2.0 KB" in message
    assert "stream=True" in message


def test_size_helpers_count_utf8_bytes_and_ignore_invalid_header(
) -> None:
    assert http_log.guess_request_size("中文") == 6
    assert http_log.guess_response_size({}, "中文") == 6
    assert http_log.guess_response_size({"Content-Length": "12"}, None) == 12
    assert http_log.guess_response_size({"Content-Length": "bad"}, None) is None


def test_log_level_tracks_status_class(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger(f"test.http.{id(http_log)}")
    logger.propagate = True

    with caplog.at_level(logging.DEBUG, logger=logger.name):
        http_log.log_http_response(logger, "GET", "/ok", 200)
        http_log.log_http_response(logger, "GET", "/bad", 404)
        http_log.log_http_response(logger, "GET", "/error", 503)

    levels = [record.levelno for record in caplog.records[-3:]]
    assert levels == [logging.DEBUG, logging.WARNING, logging.ERROR]
