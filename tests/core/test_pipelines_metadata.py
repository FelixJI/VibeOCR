"""管道元数据测试。"""

from vibeocr.contracts.pipelines import (
    _PIPELINE_METADATA,
    OCRPipeline,
    get_all_pipelines,
    get_heavy_pipelines,
    get_mineru_pipelines,
    get_paddle_pipelines,
)


def test_heavy_pipelines_includes_pp_v3_vl_mineru():
    """重管道 = PP-StructureV3 + PaddleOCR-VL + MinerU。"""
    heavy = set(get_heavy_pipelines())
    assert OCRPipeline.PP_STRUCTURE_V3 in heavy
    assert OCRPipeline.PADDLEOCR_VL in heavy
    assert OCRPipeline.DOCUMENT_PARSING in heavy


def test_ocr_is_not_heavy():
    """通用 OCR 是轻管道，不纳入 TTL/FIFO。"""
    heavy = set(get_heavy_pipelines())
    assert OCRPipeline.OCR not in heavy


def test_heavy_pipelines_count_is_three():
    """恰好 3 个重管道。"""
    assert len(get_heavy_pipelines()) == 3


def test_table_formula_not_heavy():
    """表格/公式识别是轻量级独立管道。"""
    heavy = set(get_heavy_pipelines())
    assert OCRPipeline.TABLE_RECOGNITION not in heavy
    assert OCRPipeline.FORMULA_RECOGNITION not in heavy


def test_every_pipeline_has_cache_kind() -> None:
    """每个管道元数据必须含 cache_kind 字段，值为 paddle 或 mineru。"""
    for pipeline in OCRPipeline:
        kind = _PIPELINE_METADATA[pipeline].get("cache_kind")
        assert kind in {"paddle", "mineru"}, (
            f"{pipeline.name} cache_kind 缺失或非法: {kind!r}"
        )


def test_paddle_pipelines_are_five() -> None:
    """paddle 系管道 = OCR + 表格 + 公式 + PP-StructureV3 + PaddleOCR-VL。"""
    paddle = {p.value for p in get_paddle_pipelines()}
    assert paddle == {
        "OCR",
        "TABLE_RECOGNITION",
        "FORMULA_RECOGNITION",
        "PP-StructureV3",
        "PaddleOCR-VL",
    }


def test_mineru_pipelines_are_one() -> None:
    """mineru 系管道 = 仅 DOCUMENT_PARSING (MinerU)。"""
    mineru = {p.value for p in get_mineru_pipelines()}
    assert mineru == {"MinerU"}


def test_paddle_and_mineru_partition_all_pipelines() -> None:
    """paddle ∪ mineru = 全部 6 管道，且不相交。"""
    paddle = set(get_paddle_pipelines())
    mineru = set(get_mineru_pipelines())
    all_pipelines = set(get_all_pipelines())
    assert paddle | mineru == all_pipelines
    assert paddle & mineru == set()
