"""Compatibility adapter for the Protocol-owned Runtime client.

The HTTP transport and operation implementations live in
``vibeocr.protocol.v2.client``. This transitional module only preserves the
legacy import path and product-specific HTTP logging hook until Phase 2 removes
``vibeocr-client-py``.
"""

from __future__ import annotations

import logging
from typing import Any

from vibeocr.protocol.v2.client import SupervisorClient as RuntimeSupervisorClient
from vibeocr.utils.http_log import (
    guess_request_size,
    guess_response_size,
    log_http_response,
)

logger = logging.getLogger(__name__)


class SupervisorClient(RuntimeSupervisorClient):
    """Legacy import-compatible Protocol Runtime client."""

    async def _log_http_response(self, response: Any) -> None:
        request = response.request
        try:
            request_content = request.content
        except Exception:
            request_content = None
        try:
            response_content = response.content
        except Exception:
            response_content = None
        elapsed_ms = None
        try:
            elapsed = getattr(response, "elapsed", None)
            if elapsed is not None:
                elapsed_ms = elapsed.total_seconds() * 1000.0
        except Exception:
            elapsed_ms = None
        log_http_response(
            logger=logger,
            method=request.method,
            url=str(request.url),
            status_code=response.status_code,
            reason=response.reason_phrase,
            elapsed_ms=elapsed_ms,
            request_bytes=guess_request_size(request_content),
            response_bytes=guess_response_size(
                dict(response.headers),
                response_content,
            ),
            stream=not response.is_stream_consumed,
        )


__all__ = ["SupervisorClient"]
