"""Regression coverage for the supervisor-only PySide startup handshake."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from vibeocr.views.main_window import MainWindow


class _ReadyWindow:
    """Smallest MainWindow-shaped object needed by the ready callback."""

    def __init__(self) -> None:
        self._closing = False
        self._statusbar = MagicMock()
        self._ensure_ocr_status_callback = MagicMock()
        self._record_supervisor_ready = MagicMock()

    _on_supervisor_ready = MainWindow._on_supervisor_ready


def test_supervisor_ready_is_not_reported_as_startup_failure() -> None:
    """A started v2 adapter makes the Supervisor handshake ready."""
    window = _ReadyWindow()
    adapter = SimpleNamespace(is_started=True)

    with (
        patch(
            "vibeocr.pyside.supervisor_adapter.get_supervisor_adapter",
            return_value=adapter,
        ),
        patch("vibeocr.views.main_window.QMessageBox.warning") as warning,
        patch("vibeocr.startup_metrics.record_startup"),
    ):
        window._on_supervisor_ready(True)

    warning.assert_not_called()
    window._statusbar.showMessage.assert_any_call(
        "OCR 服务已就绪（模型按需加载）"
    )
    window._record_supervisor_ready.assert_called_once_with()
