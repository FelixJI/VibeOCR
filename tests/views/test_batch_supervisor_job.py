"""The PySide batch tab submits one logical job and never owns microbatches."""

from __future__ import annotations

import inspect

from vibeocr.models.ocr_options import OCROptions
from vibeocr.views.batch_recognition_tab import BatchRecognitionTab


def test_loaded_batch_is_submitted_once_with_all_inputs(qtbot) -> None:
    tab = BatchRecognitionTab(backend=object())
    qtbot.addWidget(tab)
    tab._supervisor_generation = 1
    tab._run_state = tab.STATE_RUNNING
    calls: list[tuple[list, object]] = []

    class Adapter:
        def submit_recognition(self, uploads, **kwargs):
            calls.append((uploads, kwargs["pipeline"]))
            return 1

    loaded = [
        ({"path": "C:/inputs/a.png"}, b"a"),
        ({"path": "C:/inputs/b.png"}, b"b"),
    ]
    tab._submit_loaded_supervisor_inputs(
        1, Adapter(), OCROptions(), (loaded, [])
    )

    assert len(calls) == 1
    assert [upload[0] for upload in calls[0][0]] == ["a.png", "b.png"]
    assert calls[0][1].pipeline_id == "OCR"


def test_batch_tab_contains_no_private_http_transport() -> None:
    source = inspect.getsource(
        __import__(
            "vibeocr.views.batch_recognition_tab",
            fromlist=["BatchRecognitionTab"],
        )
    )
    assert "httpx" not in source
    assert "_base_url" not in source
    assert "_token" not in source
