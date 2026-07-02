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


def _create_table_pipeline(device: str, **kwargs: Any) -> Any:
    """创建表格识别管道实例

    额外 kwargs 透传给 TableRecognitionPipelineV2（例如 enable_mkldnn）。
    """
    from paddleocr import TableRecognitionPipelineV2

    return TableRecognitionPipelineV2(device=device, **kwargs)


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

    # 同时传入有线和无线模型名，管道内部的表格分类器
    # (PP-LCNet_x1_0_table_cls) 会自动判断表格类型并选用对应模型。
    predict_kwargs["wireless_table_structure_recognition_model_name"] = (
        options.wireless_table_model_name
    )
    predict_kwargs["wired_table_structure_recognition_model_name"] = (
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
        # PaddleX 表格结果（TableRecognitionResult）是 dict 子类，键需下标访问。
        # 表格内容在 "table_res_list"（每项含 pred_html），而非 parsing_res_list
        # （后者属于版面解析管道）。早期代码误读 parsing_res_list 导致永远取空，
        # 表现为"未识别到文字"。
        table_res_list: list[Any] = []
        if hasattr(res, "__getitem__"):
            table_res_list = (
                res["table_res_list"]
                if "table_res_list" in (res.keys() if hasattr(res, "keys") else [])
                else []
            )

        table_bboxes: list[tuple[float, float, float, float]] = []
        for idx, table_res in enumerate(table_res_list):
            # pred_html: <html><body><table>...</table></body></html>
            pred_html = (
                table_res.get("pred_html") if hasattr(table_res, "get") else None
            )
            if not pred_html:
                continue

            # 记录表格区域 bbox，用于后续过滤 overall_ocr_res 中的重复文本。
            # 注意：PaddleX 的 SingleTableRecognitionResult 不含 ``table_bbox``
            # 字段，只有 ``cell_box_list``（各单元格 [x1,y1,x2,y2]，原图坐标系，
            # 已在 post-processing 中 clip 到原图范围）。表格整体外接框需从
            # cell_box_list 的并集推导，否则过滤条件永远为空，overall_ocr_res
            # 里整图文字（含表格内文字）会被原样再展示一遍。
            cell_box_list = (
                table_res.get("cell_box_list") if hasattr(table_res, "get") else None
            )
            if cell_box_list:
                try:
                    xs_min: list[float] = []
                    ys_min: list[float] = []
                    xs_max: list[float] = []
                    ys_max: list[float] = []
                    for cell in cell_box_list:
                        if hasattr(cell, "tolist"):
                            cell = cell.tolist()
                        if (
                            isinstance(cell, (list, tuple))
                            and len(cell) >= 4
                            and all(
                                isinstance(v, (int, float)) for v in cell[:4]
                            )
                        ):
                            xs_min.append(float(cell[0]))
                            ys_min.append(float(cell[1]))
                            xs_max.append(float(cell[2]))
                            ys_max.append(float(cell[3]))
                    if xs_min:
                        table_bboxes.append(
                            (
                                min(xs_min),
                                min(ys_min),
                                max(xs_max),
                                max(ys_max),
                            )
                        )
                except (TypeError, ValueError, IndexError):
                    pass

            from vibeocr.services.ocr_service import (
                _extract_table_html,
                _html_table_to_markdown,
            )

            table_html = _extract_table_html(pred_html)
            table_md = _html_table_to_markdown(table_html)
            if table_md:
                markdown_parts.append(table_md)

            cl_idx = len(content_list)
            text_blocks.append(
                TextBlock(
                    text=table_html,
                    score=0.9,
                    bbox=None,
                    label="table",
                    order=idx,
                    content_index=cl_idx,
                )
            )
            text_with_scores.append((table_html, 0.9))
            content_list.append(
                {"type": "table", "table_body": table_html, "bbox": None}
            )

        # 表格外的普通文字（overall_ocr_res）：截图场景多为整图表格，此处通常为空，
        # 但保留以兼容"表格 + 周边文字"的图片。
        # 注意：overall_ocr_res 包含整图所有文本（含表格内文字），需要过滤掉
        # 落在表格区域内的文本块，避免与已提取的表格内容重复。
        overall_ocr_res = (
            res.get("overall_ocr_res") if hasattr(res, "get") else None
        )
        if overall_ocr_res is not None:
            rec_texts = (
                overall_ocr_res.get("rec_texts")
                if hasattr(overall_ocr_res, "get")
                else None
            )
            rec_scores = (
                overall_ocr_res.get("rec_scores")
                if hasattr(overall_ocr_res, "get")
                else None
            )
            rec_polys = (
                overall_ocr_res.get("rec_polys")
                if hasattr(overall_ocr_res, "get")
                else None
            )
            if rec_texts:
                for i, text in enumerate(rec_texts):
                    if not text:
                        continue
                    score = (
                        float(rec_scores[i])
                        if rec_scores and i < len(rec_scores)
                        else 0.9
                    )
                    poly = rec_polys[i] if rec_polys and i < len(rec_polys) else None
                    bbox_tuple = None
                    if poly is not None and hasattr(poly, "shape") and poly.size >= 4:
                        xs = poly[:, 0].tolist() if poly.ndim == 2 else None
                        ys = poly[:, 1].tolist() if poly.ndim == 2 else None
                        if xs and ys:
                            bbox_tuple = (
                                float(min(xs)),
                                float(min(ys)),
                                float(max(xs)),
                                float(max(ys)),
                            )
                    # 跳过落在表格区域内的文本块（已在 table 块中展示）
                    if bbox_tuple and table_bboxes:
                        cx = (bbox_tuple[0] + bbox_tuple[2]) / 2
                        cy = (bbox_tuple[1] + bbox_tuple[3]) / 2
                        if any(
                            tb[0] <= cx <= tb[2] and tb[1] <= cy <= tb[3]
                            for tb in table_bboxes
                        ):
                            continue
                    cl_idx = len(content_list)
                    text_blocks.append(
                        TextBlock(
                            text=text,
                            score=score,
                            bbox=bbox_tuple,
                            label="text",
                            order=-1,
                            content_index=cl_idx,
                        )
                    )
                    text_with_scores.append((text, score))
                    content_list.append(
                        {"type": "text", "text": text, "bbox": bbox_tuple}
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
