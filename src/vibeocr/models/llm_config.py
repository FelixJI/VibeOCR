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


@dataclass
class LLMConfigs:
    """MLLM 和 LLM 配置容器"""

    mllm: LLMConfig = field(default_factory=lambda: LLMConfig(is_mllm=True))
    llm: LLMConfig = field(default_factory=lambda: LLMConfig(is_mllm=False))

    def to_dict(self) -> dict[str, Any]:
        return {
            "mllm": self.mllm.to_dict(),
            "llm": self.llm.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LLMConfigs":
        """从字典创建实例，支持旧格式迁移"""
        # 向后兼容：检测旧格式（单一配置）
        if "mllm" not in data and "llm" not in data:
            # 旧格式迁移
            old_config = LLMConfig.from_dict(data)
            return cls(
                mllm=LLMConfig(
                    enabled=old_config.enabled,
                    service_url=old_config.service_url,
                    model_name=old_config.model_name,
                    api_key=old_config.api_key,
                    api_type=old_config.api_type,
                    is_mllm=True,
                ),
                llm=LLMConfig(is_mllm=False),
            )
        # 新格式
        return cls(
            mllm=LLMConfig.from_dict(data.get("mllm", {})),
            llm=LLMConfig.from_dict(data.get("llm", {})),
        )
