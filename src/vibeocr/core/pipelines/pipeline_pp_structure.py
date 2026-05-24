# src/vibeocr/core/pipelines/pipeline_pp_structure.py
"""PP-StructureV3 管道选项"""

from dataclasses import dataclass

from vibeocr.core.pipelines.base_options import BasePipelineOptions


@dataclass
class PPStructureV3Options(BasePipelineOptions):
    """PP-StructureV3 管道选项

    文档结构分析，支持表格、公式、印章、图表识别。
    """

    pipeline: str = "PP-StructureV3"
    use_doc_orientation_classify: bool = True
    use_doc_unwarping: bool = True
    use_textline_orientation: bool = False
    use_table_recognition: bool = True
    use_formula_recognition: bool = True
    use_seal_recognition: bool = False
    use_chart_recognition: bool = False
