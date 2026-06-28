"""cpu_info 工具单元测试。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from vibeocr.utils import cpu_info
from vibeocr.utils.cpu_info import (
    CPU_THREADS_CAP,
    detect_cpu_features,
    get_cpu_thread_count,
    _version_in_range,
)


# ---------------------------------------------------------------------------
# get_cpu_thread_count
# ---------------------------------------------------------------------------

def test_get_cpu_thread_count_returns_positive_int():
    """在真实环境上应返回正值。"""
    result = get_cpu_thread_count()
    assert isinstance(result, int)
    assert result >= 1


def test_get_cpu_thread_count_capped(monkeypatch):
    """逻辑核数超过上限时夹到 CPU_THREADS_CAP。"""
    monkeypatch.delenv("VIBEOCR_CPU_THREADS", raising=False)
    with patch("vibeocr.utils.cpu_info.os.cpu_count", return_value=128):
        assert get_cpu_thread_count() == CPU_THREADS_CAP


def test_get_cpu_thread_count_respects_user_override(monkeypatch):
    """VIBEOCR_CPU_THREADS 显式覆盖优先，且不受上限限制。"""
    monkeypatch.setenv("VIBEOCR_CPU_THREADS", "24")
    with patch("vibeocr.utils.cpu_info.os.cpu_count", return_value=4):
        assert get_cpu_thread_count() == 24


def test_get_cpu_thread_count_invalid_override_ignored(monkeypatch):
    """非整数的覆盖值被忽略，回退到探测。"""
    monkeypatch.setenv("VIBEOCR_CPU_THREADS", "abc")
    with patch("vibeocr.utils.cpu_info.os.cpu_count", return_value=8):
        assert get_cpu_thread_count() == 8


def test_get_cpu_thread_count_fallback_on_probe_failure(monkeypatch):
    """cpu_count 返回 None 时回退到 FALLBACK_CPU_THREADS。"""
    monkeypatch.delenv("VIBEOCR_CPU_THREADS", raising=False)
    with patch("vibeocr.utils.cpu_info.os.cpu_count", return_value=None):
        assert get_cpu_thread_count() == cpu_info.FALLBACK_CPU_THREADS


# ---------------------------------------------------------------------------
# detect_cpu_features
# ---------------------------------------------------------------------------

def test_detect_cpu_features_returns_dict_with_expected_keys():
    """返回的字典必须含约定键。"""
    feats = detect_cpu_features()
    assert set(feats.keys()) == {"avx", "avx2", "avx512", "fma", "amx"}


def test_detect_cpu_features_when_flags_empty():
    """flags 探测为空时所有特性为 False。"""
    with patch("vibeocr.utils.cpu_info._read_cpu_flags_text", return_value=""):
        feats = detect_cpu_features()
    assert feats == {
        "avx": False,
        "avx2": False,
        "avx512": False,
        "fma": False,
        "amx": False,
    }


def test_detect_cpu_features_parses_linux_flags():
    """Linux flags 行正确解析各指令集（含 AVX-512 子集）。"""
    flags = "fpu vme de pe avx avx2 fma avx512f avx512cd amx_bf16"
    with patch("vibeocr.utils.cpu_info._read_cpu_flags_text", return_value=flags):
        feats = detect_cpu_features()
    assert feats["avx"] is True
    assert feats["avx2"] is True
    assert feats["fma"] is True
    assert feats["avx512"] is True
    assert feats["amx"] is True


# ---------------------------------------------------------------------------
# can_safely_enable_onednn
# ---------------------------------------------------------------------------

def test_onednn_force_enable(monkeypatch):
    """VIBEOCR_FORCE_ONEDNN=1 强制启用。"""
    monkeypatch.setenv("VIBEOCR_FORCE_ONEDNN", "1")
    safe, reason = cpu_info.can_safely_enable_onednn()
    assert safe is True
    assert "强制启用" in reason


def test_onednn_force_disable(monkeypatch):
    """VIBEOCR_FORCE_ONEDNN=0 强制禁用。"""
    monkeypatch.setenv("VIBEOCR_FORCE_ONEDNN", "0")
    safe, reason = cpu_info.can_safely_enable_onednn()
    assert safe is False
    assert "强制禁用" in reason


def test_onednn_rejected_without_avx2(monkeypatch):
    """无 AVX2 的 CPU 一律拒绝。"""
    monkeypatch.delenv("VIBEOCR_FORCE_ONEDNN", raising=False)
    monkeypatch.setattr(cpu_info, "_get_paddle_version", lambda: "3.4.0")
    with patch(
        "vibeocr.utils.cpu_info.detect_cpu_features",
        return_value={"avx": True, "avx2": False, "avx512": False, "fma": False, "amx": False},
    ):
        safe, reason = cpu_info.can_safely_enable_onednn()
    assert safe is False
    assert "AVX2" in reason


def test_onednn_rejected_for_blacklisted_paddle(monkeypatch):
    """paddle 3.3.x 落在黑名单内则拒绝。"""
    monkeypatch.delenv("VIBEOCR_FORCE_ONEDNN", raising=False)
    monkeypatch.setattr(cpu_info, "_get_paddle_version", lambda: "3.3.1")
    with patch(
        "vibeocr.utils.cpu_info.detect_cpu_features",
        return_value={"avx": True, "avx2": True, "avx512": False, "fma": False, "amx": False},
    ):
        safe, reason = cpu_info.can_safely_enable_onednn()
    assert safe is False
    assert "77340" in reason


def test_onednn_allowed_for_new_paddle_with_avx2(monkeypatch):
    """新 paddle 版本 + AVX2 CPU 允许启用。"""
    monkeypatch.delenv("VIBEOCR_FORCE_ONEDNN", raising=False)
    monkeypatch.setattr(cpu_info, "_get_paddle_version", lambda: "3.4.0")
    with patch(
        "vibeocr.utils.cpu_info.detect_cpu_features",
        return_value={"avx": True, "avx2": True, "avx512": True, "fma": True, "amx": False},
    ):
        safe, reason = cpu_info.can_safely_enable_onednn()
    assert safe is True
    assert "未在黑名单" in reason


def test_onednn_paddle_version_with_build_suffix(monkeypatch):
    """带构建后缀的版本号（如 3.3.1+cu126）也正确判定为黑名单。"""
    monkeypatch.delenv("VIBEOCR_FORCE_ONEDNN", raising=False)
    monkeypatch.setattr(cpu_info, "_get_paddle_version", lambda: "3.3.1+cu126")
    with patch(
        "vibeocr.utils.cpu_info.detect_cpu_features",
        return_value={"avx": True, "avx2": True, "avx512": False, "fma": False, "amx": False},
    ):
        safe, _ = cpu_info.can_safely_enable_onednn()
    assert safe is False


# ---------------------------------------------------------------------------
# _version_in_range
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "ver,lo,hi,expected",
    [
        ("3.3.1", "3.3.0", "3.3.99", True),
        ("3.3.0", "3.3.0", "3.3.99", True),
        ("3.3.99", "3.3.0", "3.3.99", True),
        ("3.4.0", "3.3.0", "3.3.99", False),
        ("3.2.9", "3.3.0", "3.3.99", False),
        ("3.3.1+cu126", "3.3.0", "3.3.99", True),
    ],
)
def test_version_in_range(ver, lo, hi, expected):
    assert _version_in_range(ver, lo, hi) is expected
