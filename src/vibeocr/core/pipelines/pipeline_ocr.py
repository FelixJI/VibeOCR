# src/vibeocr/core/pipelines/pipeline_ocr.py
"""通用 OCR 管道选项与规格

定义通用 OCR 管道的选项类和 PipelineSpec，
适用于纯文本识别场景。
"""

from __future__ import annotations

import gc
import io
import logging
from dataclasses import dataclass
from typing import Any

from vibeocr.core.pipelines.base_options import BasePipelineOptions
from vibeocr.core.pipelines.registry import PipelineSpec

_logger = logging.getLogger(__name__)


@dataclass
class OCROptions(BasePipelineOptions):
    """通用 OCR 管道选项

    适用于纯文本识别场景。
    """

    pipeline: str = "OCR"
    use_doc_orientation_classify: bool = True
    use_doc_unwarping: bool = True
    use_textline_orientation: bool = False


def _create_ocr_pipeline(device: str) -> Any:
    """创建通用 OCR 管道实例"""
    from paddleocr import PaddleOCR

    return PaddleOCR(device=device)


def _consume_generator_safely(output) -> list:
    """安全地消费 generator（禁用 GC 避免 CUDA 内存管理冲突）"""
    gc_was_enabled = gc.isenabled()
    gc.disable()
    try:
        return list(output)
    except Exception as e:
        _logger.error("[安全消费] 消费 generator 时出错: %s", e, exc_info=True)
        return []
    finally:
        if gc_was_enabled:
            gc.enable()


def _extract_bbox(rec_boxes, index: int) -> tuple[float, float, float, float] | None:
    """从 rec_boxes 提取第 index 个文本框的 bbox [x0, y0, x1, y1]

    支持格式:
    - (N, 4): [x0, y0, x1, y1] 轴对齐矩形
    - (N, 4, 2): [[x0,y0], [x1,y1], [x2,y2], [x3,y3]] 四点多边形
    - (N, 2, 2): [[x0,y0], [x1,y1]] 两点矩形
    """
    try:
        box = rec_boxes[index]
        if hasattr(box, "tolist"):
            box = box.tolist()
        if len(box) == 4:
            # (N, 4) 或 (N, 4, 2)
            if isinstance(box[0], (int, float)):
                return (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
            # (N, 4, 2) 多边形格式: 取外接矩形
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


def _build_ocr_result(
    raw_text: str,
    markdown_text: str = "",
    html_text: str = "",
    text_with_scores: list[tuple[str, float]] | None = None,
    pipeline_type: str = "OCR",
    images: dict[str, Any] | None = None,
    text_blocks: list | None = None,
    content_list: list[dict[str, Any]] | None = None,
) -> Any:
    """构建 OCRResult 对象"""
    from vibeocr.models.ocr_result import OCRResult

    if text_with_scores is None:
        text_with_scores = []

    # 计算平均置信度
    avg_score = 0.0
    if text_with_scores:
        avg_score = sum(s for _, s in text_with_scores) / len(text_with_scores)

    # 收集低置信度项（低于 80%）
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


def _recognize_ocr(service: Any, image: Any, options: OCROptions) -> Any:
    """通用 OCR 识别

    从 OCRService._recognize_ocr 和 _process_ocr_output_safe 迁移而来。
    使用 service.get_or_create_pipeline("OCR") 获取管道实例。
    """
    from vibeocr.models.ocr_result import TextBlock

    _logger.debug("[_recognize_ocr] 获取 OCR 管道...")
    pipeline = service.get_or_create_pipeline("OCR")
    _logger.debug("[_recognize_ocr] 执行 predict...")
    try:
        output = pipeline.predict(
            input=image,
            use_doc_orientation_classify=options.use_doc_orientation_classify,
            use_doc_unwarping=options.use_doc_unwarping,
            use_textline_orientation=options.use_textline_orientation,
        )
        _logger.debug("[_recognize_ocr] predict 返回，类型: %s", type(output))
    except Exception as e:
        _logger.error("[_recognize_ocr] predict 调用失败: %s", e, exc_info=True)
        raise

    _logger.debug("[_recognize_ocr] 开始处理输出...")
    text_with_scores: list[tuple[str, float]] = []
    text_blocks: list[TextBlock] = []

    output_list = _consume_generator_safely(output)

    # 提取预处理信息：旋转角度和实际预处理后图像
    # 注意：res.img['preprocessed_img'] 是拼接可视化，不用；
    #       实际预处理图在 doc_preprocessor_res['output_img']（numpy BGR）
    preproc_angle = 0
    preprocessed_png: bytes | None = None
    preproc_w = preproc_h = 0
    if output_list:
        res = output_list[0]
        dp_res = res.get("doc_preprocessor_res")
        if dp_res is not None:
            preproc_angle = dp_res.get("angle", 0)
            out_arr = dp_res.get("output_img")
            if out_arr is not None:
                from PIL import Image as _PILImage

                # output_img 是 BGR numpy 数组，转 RGB 再存 PNG
                rgb = out_arr[:, :, ::-1] if out_arr.ndim == 3 else out_arr
                pil_img = _PILImage.fromarray(rgb)
                preproc_w, preproc_h = pil_img.size
                buf = io.BytesIO()
                pil_img.save(buf, format="PNG")
                preprocessed_png = buf.getvalue()

    result_count = 0
    for res in output_list:
        result_count += 1
        if result_count > 100:
            _logger.warning("[_recognize_ocr] 结果项过多，可能有问题")
            break
        try:
            if hasattr(res, "rec_texts") and hasattr(res, "rec_scores"):
                rec_boxes = getattr(res, "rec_boxes", None)
                for i, (text, score) in enumerate(
                    zip(res.rec_texts, res.rec_scores, strict=False)
                ):
                    if text:
                        fs = float(score)
                        text_with_scores.append((text, fs))
                        bbox = (
                            _extract_bbox(rec_boxes, i)
                            if rec_boxes is not None
                            else None
                        )
                        text_blocks.append(TextBlock(text=text, score=fs, bbox=bbox))
            elif hasattr(res, "rec_texts"):
                rec_boxes = getattr(res, "rec_boxes", None)
                for i, text in enumerate(res.rec_texts):
                    if text:
                        text_with_scores.append((text, 1.0))
                        bbox = (
                            _extract_bbox(rec_boxes, i)
                            if rec_boxes is not None
                            else None
                        )
                        text_blocks.append(TextBlock(text=text, score=1.0, bbox=bbox))
            elif hasattr(res, "ocr_text"):
                text_with_scores.append((res.ocr_text, 1.0))
                text_blocks.append(TextBlock(text=res.ocr_text, score=1.0, bbox=None))
            elif isinstance(res, dict):
                rec_texts = res.get("rec_texts", [])
                rec_scores = res.get("rec_scores", [])
                rec_boxes = res.get("rec_boxes")
                if rec_scores:
                    for i, (text, score) in enumerate(
                        zip(rec_texts, rec_scores, strict=False)
                    ):
                        if text:
                            fs = float(score)
                            text_with_scores.append((text, fs))
                            bbox = (
                                _extract_bbox(rec_boxes, i)
                                if rec_boxes is not None
                                else None
                            )
                            text_blocks.append(
                                TextBlock(text=text, score=fs, bbox=bbox)
                            )
                else:
                    for i, text in enumerate(rec_texts):
                        if text:
                            text_with_scores.append((text, 1.0))
                            bbox = (
                                _extract_bbox(rec_boxes, i)
                                if rec_boxes is not None
                                else None
                            )
                            text_blocks.append(
                                TextBlock(text=text, score=1.0, bbox=bbox)
                            )
        except Exception as e:
            _logger.error("[_recognize_ocr] 处理结果项 #%d 时出错: %s", result_count, e)
            continue

    raw_text = "\n".join(t for t, _ in text_with_scores)
    _logger.debug(
        "[_recognize_ocr] 处理完成: 共 %d 个结果项, %d 个文本块",
        result_count,
        len(text_with_scores),
    )

    result = _build_ocr_result(
        raw_text=raw_text,
        text_with_scores=text_with_scores,
        pipeline_type="OCR",
        text_blocks=text_blocks,
    )
    result.preproc_angle = preproc_angle
    result.preprocessed_image = preprocessed_png
    result.preproc_img_w = preproc_w
    result.preproc_img_h = preproc_h
    _logger.debug("[_recognize_ocr] 结果处理完成: %d 字符", len(result.raw_text))
    return result


OCR_SPEC = PipelineSpec(
    name="OCR",
    display_name="通用 OCR",
    description="识别图片中的文字内容，适用于纯文本场景",
    options_class=OCROptions,
    create_pipeline=_create_ocr_pipeline,
    recognize=_recognize_ocr,
)
