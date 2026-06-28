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


from vibeocr.utils.system_memory import estimate_cpu_batch_size  # noqa: E402


def test_estimate_cpu_batch_size_8g_ram():
    """8G RAM（free 4G）、A4@300 → 4096*0.3/199.13=6.17 → 6。"""
    assert estimate_cpu_batch_size(free_mb=4096, avg_pixels=8_700_000) == 6


def test_estimate_cpu_batch_size_4g_ram():
    """4G RAM（free 2G）→ 2048*0.3/199.13=3.08 → 3。"""
    assert estimate_cpu_batch_size(free_mb=2048, avg_pixels=8_700_000) == 3


def test_estimate_cpu_batch_size_16g_ram_caps_at_6():
    """16G RAM（free 8G）→ 8192*0.3/199.13=12.3 → 夹到 6。"""
    assert estimate_cpu_batch_size(free_mb=8192, avg_pixels=8_700_000) == 6


def test_estimate_cpu_batch_size_minimum_is_1():
    """2G RAM（free 1G）→ 1024*0.3/199.13=1.54 → int 1。"""
    assert estimate_cpu_batch_size(free_mb=1024, avg_pixels=8_700_000) == 1


def test_estimate_cpu_batch_size_zero_inputs_returns_1():
    """零或负输入兜底返回 1。"""
    assert estimate_cpu_batch_size(free_mb=0, avg_pixels=8_700_000) == 1
    assert estimate_cpu_batch_size(free_mb=4096, avg_pixels=0) == 1
