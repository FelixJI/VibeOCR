"""Pure MineRU option values shared by frontend forms and backend adapters."""

MINERU_BACKEND_DEFAULT = "hybrid-engine"
MINERU_BACKEND_CHAIN = ("hybrid-engine", "vlm-engine", "pipeline")
MINERU_BACKEND_LABELS = {
    "hybrid-engine": "混合引擎（推荐）",
    "vlm-engine": "VLM 智能引擎",
    "pipeline": "传统流水线",
}
MINERU_EFFORT_DEFAULT = "medium"
MINERU_EFFORT_LABELS = {
    "medium": "标准（更快，关闭图片/图表分析）",
    "high": "高精度（启用图片/图表分析，更慢）",
}

__all__ = [
    "MINERU_BACKEND_CHAIN",
    "MINERU_BACKEND_DEFAULT",
    "MINERU_BACKEND_LABELS",
    "MINERU_EFFORT_DEFAULT",
    "MINERU_EFFORT_LABELS",
]
