# src/vibeocr/core/pipelines/pipeline_mineru.py
"""MineRU 文档解析管道选项与规格

定义 MineRU 文档解析管道的选项类和 PipelineSpec。
MinerU 使用独立服务，create_pipeline 和 recognize 暂抛出 NotImplementedError。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from vibeocr.core.pipelines.base_options import BasePipelineOptions
from vibeocr.core.pipelines.registry import PipelineSpec


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


def _create_mineru_pipeline(device: str) -> Any:
    """MinerU 使用独立服务，不通过此工厂创建管道"""
    raise NotImplementedError("MinerU uses its own service")


def _recognize_mineru(service: Any, image: Any, options: MinerUOptions) -> Any:
    """MinerU 使用独立服务执行识别"""
    raise NotImplementedError("MinerU uses its own service")


MINERU_SPEC = PipelineSpec(
    name="MinerU",
    display_name="文档M（MineRU）",
    description="使用 MineRU 解析文档，支持 PDF/图片，提取文本、表格、公式等",
    options_class=MinerUOptions,
    create_pipeline=_create_mineru_pipeline,
    recognize=_recognize_mineru,
)
