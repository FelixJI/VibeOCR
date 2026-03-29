# src/vibeocr/models/ocr_options.py
"""统一 OCR 选项模块

此模块定义统一的 OCR 选项类，用于所有管道的配置。
替代原先分散在多个模块中的选项定义。
"""

from dataclasses import dataclass
from typing import Any

from vibeocr.core.pipelines import DEFAULT_DOC_UNDERSTANDING_MODEL, OCRPipeline


@dataclass
class OCROptions:
    """统一 OCR 选项

    包含所有管道的配置选项。不同管道使用不同的选项子集。
    使用 get_pipeline_supported_options() 查询管道支持的选项。
    """

    # 管道类型
    pipeline: OCRPipeline = OCRPipeline.OCR

    # === 通用预处理选项 ===
    use_doc_orientation_classify: bool = True  # 文档方向分类（0/90/180/270度）
    use_doc_unwarping: bool = True  # 文档扭曲矫正
    use_textline_orientation: bool = False  # 文本行方向分类（0/180度）

    # === PP-StructureV3 子产线选项 ===
    use_table_recognition: bool = True  # 表格识别子产线
    use_formula_recognition: bool = True  # 公式识别子产线
    use_seal_recognition: bool = False  # 印章识别子产线
    use_chart_recognition: bool = False  # 图表识别子产线

    # === PaddleOCR-VL 特有选项 ===
    vl_use_layout_detection: bool = True  # 启用版面区域检测排序
    vl_format_block_content: bool = False  # 将 block_content 格式化为 Markdown
    vl_use_seal_recognition: bool = False  # 启用印章识别
    vl_use_ocr_for_image_block: bool = False  # 对图片中的文字进行识别

    # === VLM 采样参数 ===
    vl_temperature: float = 0.0  # 温度参数（0 表示使用默认）
    vl_top_p: float = 0.0  # top-p 参数（0 表示使用默认）
    vl_max_pixels: int = 0  # 最大像素数（0 表示使用默认）
    vl_min_pixels: int = 0  # 最小像素数（0 表示使用默认）

    # === DOC_UNDERSTANDING 模型选择 ===
    doc_understanding_model: str = DEFAULT_DOC_UNDERSTANDING_MODEL

    def to_dict(self) -> dict[str, Any]:
        """转换为字典

        Returns:
            包含所有选项的字典
        """
        return {
            "pipeline": self.pipeline.value if hasattr(self.pipeline, "value") else self.pipeline,
            "use_doc_orientation_classify": self.use_doc_orientation_classify,
            "use_doc_unwarping": self.use_doc_unwarping,
            "use_textline_orientation": self.use_textline_orientation,
            "use_table_recognition": self.use_table_recognition,
            "use_formula_recognition": self.use_formula_recognition,
            "use_seal_recognition": self.use_seal_recognition,
            "use_chart_recognition": self.use_chart_recognition,
            "vl_use_layout_detection": self.vl_use_layout_detection,
            "vl_format_block_content": self.vl_format_block_content,
            "vl_use_seal_recognition": self.vl_use_seal_recognition,
            "vl_use_ocr_for_image_block": self.vl_use_ocr_for_image_block,
            "vl_temperature": self.vl_temperature,
            "vl_top_p": self.vl_top_p,
            "vl_max_pixels": self.vl_max_pixels,
            "vl_min_pixels": self.vl_min_pixels,
            "doc_understanding_model": self.doc_understanding_model,
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
            vl_format_block_content=data.get("vl_format_block_content", False),
            vl_use_seal_recognition=data.get("vl_use_seal_recognition", False),
            vl_use_ocr_for_image_block=data.get("vl_use_ocr_for_image_block", False),
            vl_temperature=data.get("vl_temperature", 0.0),
            vl_top_p=data.get("vl_top_p", 0.0),
            vl_max_pixels=data.get("vl_max_pixels", 0),
            vl_min_pixels=data.get("vl_min_pixels", 0),
            doc_understanding_model=data.get(
                "doc_understanding_model", DEFAULT_DOC_UNDERSTANDING_MODEL
            ),
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
