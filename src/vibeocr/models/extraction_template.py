# src/vibeocr/models/extraction_template.py
"""抽取模板数据模型"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExtractionTemplate:
    """信息抽取模板

    用于保存常用的抽取字段配置。
    """

    name: str
    """模板名称"""

    keys: list[str] = field(default_factory=list)
    """抽取字段列表"""

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典"""
        return {
            "name": self.name,
            "keys": self.keys,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExtractionTemplate":
        """从字典创建实例"""
        return cls(
            name=data.get("name", ""),
            keys=data.get("keys", []),
        )


# 预设模板
DEFAULT_TEMPLATES: list[ExtractionTemplate] = [
    ExtractionTemplate(
        name="发票信息",
        keys=["发票号码", "开票日期", "购买方", "销售方", "金额", "税额"],
    ),
    ExtractionTemplate(
        name="身份证信息",
        keys=["姓名", "性别", "民族", "出生日期", "住址", "公民身份号码"],
    ),
    ExtractionTemplate(
        name="合同关键信息",
        keys=["合同编号", "签订日期", "甲方", "乙方", "合同金额", "有效期"],
    ),
]
