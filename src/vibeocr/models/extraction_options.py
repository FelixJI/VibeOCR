# src/vibeocr/models/extraction_options.py
"""PP-ChatOCRv4 抽取选项数据模型"""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional


@dataclass
class ExtractionOptions:
    """PP-ChatOCRv4 产线选项配置

    对应 visual_predict() 方法的参数。
    """

    use_doc_orientation: bool = True
    """是否使用文档方向分类模块"""

    use_doc_unwarping: bool = True
    """是否使用文档扭曲矫正模块"""

    use_general_ocr: bool = True
    """是否使用通用 OCR 子产线"""

    use_table_recognition: bool = True
    """是否使用表格识别子产线"""

    use_seal_recognition: bool = False
    """是否使用印章识别子产线"""

    def to_dict(self) -> Dict[str, Any]:
        """转换为 PP-ChatOCRv4 visual_predict 参数格式"""
        return {
            "use_doc_orientation_classify": self.use_doc_orientation,
            "use_doc_unwarping": self.use_doc_unwarping,
            "use_general_ocr": self.use_general_ocr,
            "use_table_recognition": self.use_table_recognition,
            "use_seal_recognition": self.use_seal_recognition,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExtractionOptions":
        """从字典创建实例"""
        return cls(
            use_doc_orientation=data.get("use_doc_orientation_classify", data.get("use_doc_orientation", True)),
            use_doc_unwarping=data.get("use_doc_unwarping", True),
            use_general_ocr=data.get("use_general_ocr", True),
            use_table_recognition=data.get("use_table_recognition", True),
            use_seal_recognition=data.get("use_seal_recognition", False),
        )
