# src/vibeocr/core/pipelines/pipeline_mineru.py
"""MineRU 文档解析管道选项"""

from dataclasses import dataclass, field

from vibeocr.core.pipelines.base_options import BasePipelineOptions


@dataclass
class MinerUOptions(BasePipelineOptions):
    """MineRU 文档解析管道选项

    使用 MineRU 解析文档，支持 PDF/图片，提取文本、表格、公式等。
    """

    pipeline: str = "MinerU"
    parse_method: str = "auto"
    backend: str = "hybrid-auto-engine"
    enable_formula: bool = True
    enable_table: bool = True
    lang_list: list[str] = field(default_factory=list)
    start_page_id: int = 0
    end_page_id: int | None = None
