# src/vibeocr/core/pipelines/pipeline_table.py
"""表格识别管道选项与规格

定义表格识别管道的选项类和 PipelineSpec，
支持有线/无线表格结构识别。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from vibeocr.core.pipelines.base_options import BasePipelineOptions
from vibeocr.core.pipelines.registry import PipelineSpec


@dataclass
class TableRecognitionOptions(BasePipelineOptions):
    """表格识别管道选项

    支持有线和无线表格结构识别，可配置各种 OCR 检测/识别参数。
    """

    pipeline: str = "TABLE_RECOGNITION"
    use_doc_orientation_classify: bool = True
    use_doc_unwarping: bool = True
    use_wireless_table: bool = True
    wireless_table_model_name: str = "SLANeXt_wireless"
    wired_table_model_name: str = "SLANeXt_wired"
    use_table_orientation_classify: bool = True
    use_ocr_results_with_table_cells: bool = True
    text_det_limit_side_len: int | None = None
    text_det_thresh: float | None = None
    text_det_box_thresh: float | None = None
    text_det_unclip_ratio: float | None = None
    text_rec_score_thresh: float | None = None
    use_wired_table_cells_trans_to_html: bool = False
    use_wireless_table_cells_trans_to_html: bool = False
    use_e2e_wired_table_rec_model: bool = False
    use_e2e_wireless_table_rec_model: bool = True
    formula_recognition_batch_size: int = 1


def _create_table_pipeline(device: str) -> Any:
    """创建表格识别管道实例"""
    from paddleocr import TableRecognitionPipelineV2

    return TableRecognitionPipelineV2(device=device)


def _recognize_table(service: Any, image: Any, options: TableRecognitionOptions) -> Any:
    """执行表格识别并返回 OCRResult"""
    from enum import Enum

    from vibeocr.models.ocr_result import OCRResult, TextBlock

    pipeline_name = (
        options.pipeline.value
        if isinstance(options.pipeline, Enum)
        else options.pipeline
    )
    pipeline = service.get_or_create_pipeline(pipeline_name)

    predict_kwargs: dict[str, Any] = {}
    predict_kwargs["use_doc_orientation_classify"] = (
        options.use_doc_orientation_classify
    )
    predict_kwargs["use_doc_unwarping"] = options.use_doc_unwarping
    predict_kwargs["use_table_orientation_classify"] = (
        options.use_table_orientation_classify
    )
    predict_kwargs["use_ocr_results_with_table_cells"] = (
        options.use_ocr_results_with_table_cells
    )
    predict_kwargs["use_wired_table_cells_trans_to_html"] = (
        options.use_wired_table_cells_trans_to_html
    )
    predict_kwargs["use_wireless_table_cells_trans_to_html"] = (
        options.use_wireless_table_cells_trans_to_html
    )
    predict_kwargs["use_e2e_wired_table_rec_model"] = (
        options.use_e2e_wired_table_rec_model
    )
    predict_kwargs["use_e2e_wireless_table_rec_model"] = (
        options.use_e2e_wireless_table_rec_model
    )

    if options.use_wireless_table:
        predict_kwargs["wireless_table_structure_recognition_model_name"] = (
            options.wireless_table_model_name
        )
    else:
        predict_kwargs["wireless_table_structure_recognition_model_name"] = (
            options.wired_table_model_name
        )

    if options.text_det_limit_side_len is not None:
        predict_kwargs["text_det_limit_side_len"] = options.text_det_limit_side_len
    if options.text_det_thresh is not None:
        predict_kwargs["text_det_thresh"] = options.text_det_thresh
    if options.text_det_box_thresh is not None:
        predict_kwargs["text_det_box_thresh"] = options.text_det_box_thresh
    if options.text_det_unclip_ratio is not None:
        predict_kwargs["text_det_unclip_ratio"] = options.text_det_unclip_ratio
    if options.text_rec_score_thresh is not None:
        predict_kwargs["text_rec_score_thresh"] = options.text_rec_score_thresh

    output = pipeline.predict(input=image, **predict_kwargs)
    output_list = list(output)

    text_blocks: list[TextBlock] = []
    text_with_scores: list[tuple[str, float]] = []
    markdown_parts: list[str] = []
    content_list: list[dict[str, Any]] = []

    for res in output_list:
        if hasattr(res, "markdown"):
            md_info = getattr(res, "markdown", None)
            if isinstance(md_info, dict):
                md_text = md_info.get("markdown_texts", "")
                if md_text:
                    markdown_parts.append(md_text)

        parsing_res_list = []
        if hasattr(res, "parsing_res_list"):
            parsing_res_list = res.parsing_res_list

        for block in parsing_res_list:
            label = getattr(block, "label", "table")
            bbox = getattr(block, "bbox", None)
            content = getattr(block, "content", "")
            order_index = getattr(block, "order_index", -1)

            if not content:
                continue

            cl_idx = len(content_list)
            bbox_tuple = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])) if bbox else None

            if label == "table":
                from vibeocr.services.ocr_service import (
                    _extract_table_html,
                    _html_table_to_markdown,
                )

                table_html = _extract_table_html(content)
                table_md = _html_table_to_markdown(table_html)
                if table_md:
                    markdown_parts.append(table_md)
                text_blocks.append(
                    TextBlock(
                        text=content,
                        score=0.9,
                        bbox=bbox_tuple,
                        label=label,
                        order=order_index or -1,
                        content_index=cl_idx,
                    )
                )
                text_with_scores.append((content, 0.9))
                content_list.append(
                    {"type": "table", "table_body": table_html, "bbox": bbox_tuple}
                )
            else:
                text_blocks.append(
                    TextBlock(
                        text=content,
                        score=0.9,
                        bbox=bbox_tuple,
                        label=label,
                        order=order_index or -1,
                        content_index=cl_idx,
                    )
                )
                text_with_scores.append((content, 0.9))
                content_list.append(
                    {"type": label, "text": content, "bbox": bbox_tuple}
                )

    raw_text = "\n".join(b.text for b in text_blocks)
    markdown_text = "\n\n".join(markdown_parts) if markdown_parts else raw_text

    from vibeocr.utils.markdown_converter import markdown_to_html

    return OCRResult(
        raw_text=raw_text,
        markdown_text=markdown_text,
        html_text=markdown_to_html(markdown_text) if markdown_text else "",
        text_with_scores=text_with_scores,
        pipeline_type="TABLE_RECOGNITION",
        text_blocks=text_blocks,
        content_list=content_list,
    )


TABLE_RECOGNITION_SPEC = PipelineSpec(
    name="TABLE_RECOGNITION",
    display_name="表格识别",
    description="独立表格结构识别，支持有线和无线表格",
    options_class=TableRecognitionOptions,
    create_pipeline=_create_table_pipeline,
    recognize=_recognize_table,
)
