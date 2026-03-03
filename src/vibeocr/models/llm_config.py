# src/vibeocr/models/llm_config.py
"""LLM 配置数据模型"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class APIType(StrEnum):
    """API 类型枚举"""

    OPENAI = "openai"
    QIANFAN = "qianfan"


@dataclass
class LLMConfig:
    """LLM 服务配置

    支持 MLLM（多模态大语言模型）和 LLM（文本大语言模型）两种类型。
    """

    enabled: bool = False
    """是否启用"""

    service_url: str = ""
    """服务地址"""

    model_name: str = ""
    """模型名称"""

    api_key: str = ""
    """API Key"""

    api_type: str = field(default_factory=lambda: APIType.OPENAI.value)
    """API 类型"""

    is_mllm: bool = True
    """是否为多模态模型"""

    def is_configured(self) -> bool:
        """检查是否已正确配置"""
        if not self.enabled:
            return False
        return bool(self.service_url and self.model_name)

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典"""
        return {
            "enabled": self.enabled,
            "service_url": self.service_url,
            "model_name": self.model_name,
            "api_key": self.api_key,
            "api_type": self.api_type,
            "is_mllm": self.is_mllm,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LLMConfig":
        """从字典创建实例"""
        return cls(
            enabled=data.get("enabled", False),
            service_url=data.get("service_url", ""),
            model_name=data.get("model_name", ""),
            api_key=data.get("api_key", ""),
            api_type=data.get("api_type", APIType.OPENAI.value),
            is_mllm=data.get("is_mllm", True),
        )
