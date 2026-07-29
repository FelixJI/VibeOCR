from __future__ import annotations

import logging

from vibeocr.backend.ipc.model_bridge import apply_diff, mirror_to_doc
from vibeocr.backend.ipc.schemas import (
    ModelDiff,
    PdfDocumentMirror,
    PdfPageInfoMirror,
    TextBlockMirror,
    TextLayerInfoMirror,
)


def _page(page_index: int, *, text: str = "page") -> PdfPageInfoMirror:
    return PdfPageInfoMirror(
        page_index=page_index,
        rotation=90,
        has_text_layer=True,
        text_layers=[
            TextLayerInfoMirror(
                index=2,
                text_preview=text,
                char_count=len(text),
                bbox=(1.0, 2.0, 3.0, 4.0),
                color_id=7,
            )
        ],
        is_scanned=True,
        rect=(0.0, 0.0, 612.0, 792.0),
        ocr_text_blocks=[
            TextBlockMirror(
                text=text,
                score=0.91,
                bbox=(10.0, 20.0, 30.0, 40.0),
                polygon=(10.0, 20.0, 30.0, 20.0, 30.0, 40.0, 10.0, 40.0),
                page_idx=page_index,
                is_manually_edited=True,
                label="title",
                order=3,
            )
        ],
        ocr_preproc_angle=180,
        deskewed=True,
    )


def test_mirror_to_doc_preserves_nested_page_fields() -> None:
    mirror = PdfDocumentMirror(
        file_path="C:/docs/input.pdf",
        pages=[_page(0, text="标题")],
        is_modified=True,
        has_structural_change=True,
        render_dpi=240,
        thumbnail_dpi=72,
    )

    doc = mirror_to_doc(mirror)

    assert doc.file_path == "C:/docs/input.pdf"
    assert (doc.is_modified, doc.has_structural_change) == (True, True)
    assert (doc.render_dpi, doc.thumbnail_dpi) == (240, 72)
    page = doc.pages[0]
    assert (page.rotation, page.has_text_layer, page.is_scanned) == (90, True, True)
    assert page.text_layers[0].text_preview == "标题"
    block = page.ocr_text_blocks[0]
    assert block.polygon == (
        10.0,
        20.0,
        30.0,
        20.0,
        30.0,
        40.0,
        10.0,
        40.0,
    )
    assert (block.page_idx, block.is_manually_edited, block.label, block.order) == (
        0,
        True,
        "title",
        3,
    )


def test_apply_diff_full_model_replaces_pages_and_invalidates_every_page() -> None:
    doc = mirror_to_doc(PdfDocumentMirror(pages=[_page(0, text="old")]))
    full_model = PdfDocumentMirror(
        pages=[_page(0, text="new-0"), _page(1, text="new-1")],
        is_modified=True,
        has_structural_change=True,
    )

    invalidated = apply_diff(doc, ModelDiff(full_model=full_model))

    assert invalidated == [0, 1]
    assert [page.text_layers[0].text_preview for page in doc.pages] == [
        "new-0",
        "new-1",
    ]
    assert (doc.is_modified, doc.has_structural_change) == (True, True)


def test_apply_diff_respects_explicit_invalidations_and_flag_overrides() -> None:
    doc = mirror_to_doc(PdfDocumentMirror(pages=[_page(0)]))
    full_model = PdfDocumentMirror(
        pages=[_page(0, text="replacement")],
        is_modified=True,
        has_structural_change=True,
    )
    diff = ModelDiff(
        full_model=full_model,
        modified_flag=False,
        structural_flag=False,
        invalidated_thumbnails=[0, 7],
    )

    invalidated = apply_diff(doc, diff)

    assert invalidated == [0, 7]
    assert (doc.is_modified, doc.has_structural_change) == (False, False)


def test_apply_diff_replaces_valid_pages_and_warns_for_invalid_index(
    caplog,
) -> None:
    doc = mirror_to_doc(
        PdfDocumentMirror(pages=[_page(0, text="zero"), _page(1, text="one")])
    )
    diff = ModelDiff(
        replaced_pages=[_page(1, text="updated"), _page(5, text="ignored")],
        modified_flag=True,
        structural_flag=False,
        invalidated_thumbnails=[1],
    )

    with caplog.at_level(logging.WARNING):
        invalidated = apply_diff(doc, diff)

    assert invalidated == [1]
    assert [page.text_layers[0].text_preview for page in doc.pages] == [
        "zero",
        "updated",
    ]
    assert (doc.is_modified, doc.has_structural_change) == (True, False)
    assert "索引越界: 5" in caplog.text
