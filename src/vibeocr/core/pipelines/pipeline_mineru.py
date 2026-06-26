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

# MinerU backend 名称（3.3.1 canonical；2.x 的 *-auto-engine 已为 legacy 别名）
MINERU_BACKEND_DEFAULT = "hybrid-engine"
MINERU_BACKEND_CHAIN = ("hybrid-engine", "vlm-engine", "pipeline")  # 回退顺序
MINERU_BACKEND_LABELS = {
    "hybrid-engine": "混合引擎（推荐）",
    "vlm-engine": "VLM 智能引擎",
    "pipeline": "传统流水线",
}
# hybrid 解析强度（仅对 hybrid-engine 生效）
MINERU_EFFORT_DEFAULT = "medium"  # 与 3.3.1 默认一致 → 保持当前行为
MINERU_EFFORT_LABELS = {
    "medium": "标准（更快，关闭图片/图表分析）",
    "high": "高精度（启用图片/图表分析，更慢）",
}


@dataclass
class MinerUOptions(BasePipelineOptions):
    """MineRU 文档解析管道选项

    使用 MineRU 解析文档，支持 PDF/图片，提取文本、表格、公式等。
    """

    pipeline: str = "MinerU"
    parse_method: str = "auto"
    backend: str = MINERU_BACKEND_DEFAULT
    effort: str = MINERU_EFFORT_DEFAULT
    enable_formula: bool = True
    enable_table: bool = True
    lang_list: list[str] = field(default_factory=list)
    start_page_id: int = 0
    end_page_id: int | None = None


def _create_mineru_pipeline(device: str, **kwargs: Any) -> Any:
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
