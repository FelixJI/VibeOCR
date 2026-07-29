from __future__ import annotations

import pytest
from pydantic import ValidationError

from vibeocr.backend.ipc.schemas import (
    BatchAddTextLayerRequest,
    ModelDiff,
    PdfDocumentMirror,
    PdfPageInfoMirror,
    ProgressEvent,
    ProgressPhase,
    SaveRequest,
    TextBlockMirror,
    TextLayerInfoMirror,
)


def test_document_mirror_json_round_trip_preserves_nested_contract() -> None:
    mirror = PdfDocumentMirror(
        file_path="C:/文档/输入.pdf",
        pages=[
            PdfPageInfoMirror(
                page_index=4,
                rotation=270,
                has_text_layer=True,
                text_layers=[
                    TextLayerInfoMirror(
                        index=1,
                        text_preview="预览",
                        char_count=2,
                        bbox=(1.0, 2.0, 3.0, 4.0),
                        color_id=9,
                    )
                ],
                rect=(0.0, 0.0, 595.0, 842.0),
                ocr_text_blocks=[
                    TextBlockMirror(
                        text="正文",
                        score=0.88,
                        bbox=(100.0, 200.0, 300.0, 400.0),
                        polygon=(100.0, 200.0, 300.0, 200.0, 300.0, 400.0, 100.0, 400.0),
                        page_idx=4,
                        is_manually_edited=True,
                        label="paragraph",
                        order=6,
                    )
                ],
            )
        ],
        is_modified=True,
    )

    restored = PdfDocumentMirror.model_validate_json(mirror.model_dump_json())

    assert restored == mirror
    assert restored.pages[0].text_layers[0].bbox == (1.0, 2.0, 3.0, 4.0)
    assert restored.pages[0].ocr_text_blocks[0].polygon is not None


def test_default_factory_lists_are_not_shared_between_messages() -> None:
    first_page = PdfPageInfoMirror(page_index=0)
    second_page = PdfPageInfoMirror(page_index=1)
    first_diff = ModelDiff()
    second_diff = ModelDiff()

    first_page.text_layers.append(
        TextLayerInfoMirror(
            index=0,
            text_preview="x",
            char_count=1,
            bbox=(0.0, 0.0, 1.0, 1.0),
            color_id=0,
        )
    )
    first_diff.invalidated_thumbnails.append(0)

    assert second_page.text_layers == []
    assert second_diff.invalidated_thumbnails == []


@pytest.mark.parametrize("phase", list(ProgressPhase))
def test_progress_event_round_trips_every_phase(phase: ProgressPhase) -> None:
    event = ProgressEvent(
        phase=phase,
        current=2,
        total=5,
        message="处理中",
        page_index=1,
        page_payload={"written": 3},
    )

    restored = ProgressEvent.model_validate_json(event.model_dump_json())

    assert restored == event


def test_progress_event_rejects_unknown_phase() -> None:
    with pytest.raises(ValidationError):
        ProgressEvent(phase="unknown")  # type: ignore[arg-type]


def test_request_defaults_pin_save_and_batch_behavior() -> None:
    save = SaveRequest()
    batch = BatchAddTextLayerRequest(pages=[])

    assert save.path is None
    assert save.rewrite_text_layers is True
    assert (batch.overwrite, batch.save, batch.pdf_settings) == (False, False, None)
