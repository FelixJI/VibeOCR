"""管道元数据测试。"""

from vibeocr.core.pipelines import OCRPipeline, get_heavy_pipelines


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
