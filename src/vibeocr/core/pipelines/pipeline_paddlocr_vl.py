# src/vibeocr/core/pipelines/pipeline_paddlocr_vl.py
"""PaddleOCR-VL 管道选项与规格

定义 PaddleOCR-VL 管道的选项类和 PipelineSpec，
支持图片/PDF 文档解析，提取文本、表格、公式、图表等。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from vibeocr.core.pipelines.base_options import BasePipelineOptions
from vibeocr.core.pipelines.registry import PipelineSpec

_logger = logging.getLogger(__name__)


def _extract_bbox_from_rec_boxes(
    rec_boxes, index: int
) -> tuple[float, float, float, float] | None:
    """从 rec_boxes 提取第 index 个文本框的 bbox"""
    try:
        box = rec_boxes[index]
        if hasattr(box, "tolist"):
            box = box.tolist()
        if len(box) == 4:
            if isinstance(box[0], (int, float)):
                return (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            return (min(xs), min(ys), max(xs), max(ys))
        if len(box) == 2 and len(box[0]) == 2 and len(box[1]) == 2:
            return (
                float(box[0][0]),
                float(box[0][1]),
                float(box[1][0]),
                float(box[1][1]),
            )
    except (IndexError, TypeError, ValueError):
        pass
    return None


def _extract_block_bbox(
    block_bbox: list | tuple | None,
) -> tuple[float, float, float, float] | None:
    """从 parsing_res_list 的 block_bbox 提取坐标"""
    if not block_bbox:
        return None
    try:
        if len(block_bbox) == 4 and all(
            isinstance(v, (int, float)) for v in block_bbox
        ):
            return (
                float(block_bbox[0]),
                float(block_bbox[1]),
                float(block_bbox[2]),
                float(block_bbox[3]),
            )
        if len(block_bbox) >= 2:
            xs = [p[0] for p in block_bbox]
            ys = [p[1] for p in block_bbox]
            return (min(xs), min(ys), max(xs), max(ys))
    except (TypeError, IndexError, ValueError):
        pass
    return None


def _get_block_score(res, block: dict) -> float:
    """从 parsing_res_list 结果中获取 block 的置信度"""
    if hasattr(res, "layout_det_res") and hasattr(res.layout_det_res, "boxes"):
        boxes = res.layout_det_res.boxes
        order = block.get("block_order", -1)
        if 0 <= order < len(boxes):
            return float(boxes[order].get("score", 0.9))
    return 0.9


def _build_ocr_result(
    raw_text: str,
    markdown_text: str = "",
    html_text: str = "",
    text_with_scores: list[tuple[str, float]] | None = None,
    pipeline_type: str = "PaddleOCR-VL",
    images: dict[str, Any] | None = None,
    text_blocks: list | None = None,
    content_list: list[dict[str, Any]] | None = None,
) -> Any:
    """构建 OCRResult 对象"""
    from vibeocr.models.ocr_result import OCRResult

    if text_with_scores is None:
        text_with_scores = []

    avg_score = 0.0
    if text_with_scores:
        avg_score = sum(s for _, s in text_with_scores) / len(text_with_scores)

    low_confidence_items = [
        (text, score) for text, score in text_with_scores if score < 0.80
    ]

    final_html = html_text or raw_text

    return OCRResult(
        raw_text=raw_text,
        markdown_text=markdown_text or raw_text,
        html_text=final_html,
        text_with_scores=text_with_scores,
        avg_score=avg_score,
        low_confidence_items=low_confidence_items,
        pipeline_type=pipeline_type,
        images=images or {},
        text_blocks=text_blocks or [],
        content_list=content_list or [],
    )


@dataclass
class PaddleOCRVLOptions(BasePipelineOptions):
    """PaddleOCR-VL 管道选项

    使用 PaddleOCR-VL 解析文档，支持图片/PDF，提取文本、表格、公式、图表等。
    """

    pipeline: str = "PaddleOCR-VL"
    use_doc_orientation_classify: bool = True
    use_doc_unwarping: bool = True
    vl_use_layout_detection: bool = True
    vl_use_chart_recognition: bool = False
    vl_use_seal_recognition: bool = False
    use_ocr_for_image_block: bool = False


def _create_paddlocr_vl_pipeline(device: str) -> Any:
    """创建 PaddleOCR-VL 管道实例"""
    from paddleocr import PaddleOCRVL

    return PaddleOCRVL(device=device)


def _recognize_paddlocr_vl(
    service: Any, image: Any, options: PaddleOCRVLOptions
) -> Any:
    """PaddleOCR-VL 文档解析

    从 OCRService._recognize_paddlocr_vl 迁移而来。
    """
    from vibeocr.models.ocr_result import TextBlock

    pipeline = service.get_or_create_pipeline("PaddleOCR-VL")

    predict_kwargs: dict[str, Any] = {}
    predict_kwargs["use_doc_orientation_classify"] = options.use_doc_orientation_classify
    predict_kwargs["use_doc_unwarping"] = options.use_doc_unwarping
    predict_kwargs["use_layout_detection"] = options.vl_use_layout_detection
    predict_kwargs["use_chart_recognition"] = options.vl_use_chart_recognition
    predict_kwargs["use_seal_recognition"] = options.vl_use_seal_recognition
    predict_kwargs["use_ocr_for_image_block"] = options.use_ocr_for_image_block

    output = pipeline.predict(input=image, **predict_kwargs)
    output_list = list(output)

    markdown_text = ""
    text_blocks: list[TextBlock] = []
    text_with_scores: list[tuple[str, float]] = []
    content_list: list[dict[str, Any]] = []
    images: dict[str, Any] = {}

    for res in output_list:
        if hasattr(res, "markdown"):
            markdown_text = getattr(res, "markdown", "") or markdown_text

        if hasattr(res, "content_list"):
            cl = getattr(res, "content_list", None)
            if cl:
                content_list = list(cl) if not isinstance(cl, list) else cl

        if hasattr(res, "images"):
            imgs = getattr(res, "images", None)
            if imgs and isinstance(imgs, dict):
                images.update(imgs)

        # PaddleOCR-VL 3.x: parsing_res_list with block-level localization
        if hasattr(res, "parsing_res_list"):
            for block in res.parsing_res_list:
                bbox = _extract_block_bbox(block.get("block_bbox"))
                text = block.get("block_content", "")
                label = block.get("block_label", "text")
                order = block.get("block_order", -1)
                score = _get_block_score(res, block)

                if text:
                    text_blocks.append(
                        TextBlock(
                            text=text,
                            score=score,
                            bbox=bbox,
                            label=label,
                            order=order,
                        )
                    )
                    text_with_scores.append((text, score))
                    content_list.append(
                        {
                            "type": label,
                            "text": text,
                            "bbox": bbox,
                        }
                    )
        elif hasattr(res, "rec_texts") and hasattr(res, "rec_scores"):
            # Fallback: legacy output format
            rec_boxes = getattr(res, "rec_boxes", None)
            for i, (text, score) in enumerate(
                zip(res.rec_texts, res.rec_scores, strict=False)
            ):
                if text:
                    fs = float(score)
                    text_with_scores.append((text, fs))
                    bbox = (
                        _extract_bbox_from_rec_boxes(rec_boxes, i)
                        if rec_boxes is not None
                        else None
                    )
                    text_blocks.append(TextBlock(text=text, score=fs, bbox=bbox))

    raw_text = "\n".join(b.text for b in text_blocks)
    if not raw_text and markdown_text:
        raw_text = markdown_text

    from vibeocr.utils.markdown_converter import markdown_to_html

    return _build_ocr_result(
        raw_text=raw_text,
        markdown_text=markdown_text or raw_text,
        html_text=markdown_to_html(markdown_text) if markdown_text else raw_text,
        text_with_scores=text_with_scores,
        pipeline_type="PaddleOCR-VL",
        images=images if images else None,
        text_blocks=text_blocks,
        content_list=content_list,
    )


PADDLEOCR_VL_SPEC = PipelineSpec(
    name="PaddleOCR-VL",
    display_name="文档P（PaddleOCR-VL）",
    description="使用 PaddleOCR-VL-1.5 解析文档，支持图片/PDF，提取文本、表格、公式、图表等",
    options_class=PaddleOCRVLOptions,
    create_pipeline=_create_paddlocr_vl_pipeline,
    recognize=_recognize_paddlocr_vl,
)
