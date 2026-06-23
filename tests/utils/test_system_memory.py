"""system_memory 工具单元测试。"""

from __future__ import annotations

from vibeocr.utils.system_memory import FALLBACK_RAM_MB, get_available_ram_mb


def test_get_available_ram_mb_returns_positive_int():
    """在真实环境上应返回正值（单位 MB）。"""
    result = get_available_ram_mb()
    assert isinstance(result, int)
    assert result > 0


def test_get_available_ram_mb_at_least_some_memory():
    """任何能跑测试的机器可用内存至少应有 64MB。"""
    assert get_available_ram_mb() >= 64


def test_fallback_constant_is_conservative():
    """回退值应为 2048（2GB），保证 batch 至少为 1-2。"""
    assert FALLBACK_RAM_MB == 2048
