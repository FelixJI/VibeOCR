# src/vibeocr/models/ocr_options.py
"""统一 OCR 选项模块

此模块定义统一的 OCR 选项类，用于所有管道的配置。
替代原先分散在多个模块中的选项定义。
"""

from dataclasses import dataclass, field
from typing import Any

from vibeocr.core.pipelines import OCRPipeline


@dataclass
class OCROptions:
    """统一 OCR 选项

    包含所有管道的配置选项。不同管道使用不同的选项子集。
    使用 get_pipeline_supported_options() 查询管道支持的选项。
    """

    # 管道类型
    pipeline: OCRPipeline = OCRPipeline.OCR

    # === 通用预处理选项（OCR + PP-StructureV3 共享）===
    use_doc_orientation_classify: bool = True  # 文档方向分类（0/90/180/270度）
    use_doc_unwarping: bool = True  # 文档扭曲矫正
    use_textline_orientation: bool = False  # 文本行方向分类（0/180度）

    # === PP-StructureV3 专用 ===
    use_table_recognition: bool = True  # 表格识别
    use_formula_recognition: bool = True  # 公式识别
    use_seal_recognition: bool = False  # 印章识别
    use_chart_recognition: bool = False  # 图表识别

    # === PaddleOCR-VL 专用 ===
    vl_use_layout_detection: bool = True  # 版面检测
    vl_use_chart_recognition: bool = False  # 图表识别
    vl_use_seal_recognition: bool = False  # 印章识别
    use_ocr_for_image_block: bool = False  # 图片文字识别

    # === MineRU 文档解析选项 ===
    parse_method: str = "auto"  # 解析方法: auto, txt, ocr
    backend: str = (
        "hybrid-auto-engine"  # 解析后端: vlm-auto-engine, hybrid-auto-engine, pipeline
    )
    enable_formula: bool = True  # 启用公式识别
    enable_table: bool = True  # 启用表格识别

    # === MineRU 语言和页面范围 ===
    lang_list: list[str] = field(default_factory=lambda: [])  # 空列表=自动检测
    start_page_id: int = 0
    end_page_id: int | None = None  # None 表示不限制

    # === 表格识别专用（TABLE_RECOGNITION）===
    use_wireless_table: bool = True  # 无线表格模式
    use_table_orientation_classify: bool = True  # 表格方向分类
    use_ocr_results_with_table_cells: bool = True  # 单元格文字识别

    # === 公式识别专用（FORMULA_RECOGNITION）===
    formula_recognition_batch_size: int = 1  # 公式批量大小

    def to_dict(self) -> dict[str, Any]:
        """转换为字典

        Returns:
            包含所有选项的字典
        """
        return {
            "pipeline": self.pipeline.value
            if hasattr(self.pipeline, "value")
            else self.pipeline,
            "use_doc_orientation_classify": self.use_doc_orientation_classify,
            "use_doc_unwarping": self.use_doc_unwarping,
            "use_textline_orientation": self.use_textline_orientation,
            "use_table_recognition": self.use_table_recognition,
            "use_formula_recognition": self.use_formula_recognition,
            "use_seal_recognition": self.use_seal_recognition,
            "use_chart_recognition": self.use_chart_recognition,
            "vl_use_layout_detection": self.vl_use_layout_detection,
            "vl_use_chart_recognition": self.vl_use_chart_recognition,
            "vl_use_seal_recognition": self.vl_use_seal_recognition,
            "use_ocr_for_image_block": self.use_ocr_for_image_block,
            "parse_method": self.parse_method,
            "backend": self.backend,
            "enable_formula": self.enable_formula,
            "enable_table": self.enable_table,
            "lang_list": self.lang_list,
            "start_page_id": self.start_page_id,
            "end_page_id": self.end_page_id,
            "use_wireless_table": self.use_wireless_table,
            "use_table_orientation_classify": self.use_table_orientation_classify,
            "use_ocr_results_with_table_cells": self.use_ocr_results_with_table_cells,
            "formula_recognition_batch_size": self.formula_recognition_batch_size,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OCROptions":
        """从字典创建

        Args:
            data: 包含选项的字典

        Returns:
            OCROptions 实例
        """
        # 处理 pipeline 字段
        pipeline_value = data.get("pipeline", "OCR")
        if isinstance(pipeline_value, str):
            pipeline = OCRPipeline(pipeline_value)
        else:
            pipeline = pipeline_value

        return cls(
            pipeline=pipeline,
            use_doc_orientation_classify=data.get("use_doc_orientation_classify", True),
            use_doc_unwarping=data.get("use_doc_unwarping", True),
            use_textline_orientation=data.get("use_textline_orientation", False),
            use_table_recognition=data.get("use_table_recognition", True),
            use_formula_recognition=data.get("use_formula_recognition", True),
            use_seal_recognition=data.get("use_seal_recognition", False),
            use_chart_recognition=data.get("use_chart_recognition", False),
            vl_use_layout_detection=data.get("vl_use_layout_detection", True),
            vl_use_chart_recognition=data.get("vl_use_chart_recognition", False),
            vl_use_seal_recognition=data.get("vl_use_seal_recognition", False),
            use_ocr_for_image_block=data.get("use_ocr_for_image_block", False),
            parse_method=data.get("parse_method", "auto"),
            backend=data.get("backend", "hybrid-auto-engine"),
            enable_formula=data.get("enable_formula", True),
            enable_table=data.get("enable_table", True),
            lang_list=data.get("lang_list", []),
            start_page_id=data.get("start_page_id", 0),
            end_page_id=data.get("end_page_id", None),
            use_wireless_table=data.get("use_wireless_table", True),
            use_table_orientation_classify=data.get("use_table_orientation_classify", True),
            use_ocr_results_with_table_cells=data.get("use_ocr_results_with_table_cells", True),
            formula_recognition_batch_size=data.get("formula_recognition_batch_size", 1),
        )

    def copy(self, **updates) -> "OCROptions":
        """创建副本，可选地更新部分字段

        Args:
            **updates: 要更新的字段

        Returns:
            新的 OCROptions 实例
        """
        data = self.to_dict()
        data.update(updates)
        # 处理 pipeline 枚举
        if "pipeline" in updates and isinstance(updates["pipeline"], OCRPipeline):
            data["pipeline"] = updates["pipeline"].value
        return OCROptions.from_dict(data)
