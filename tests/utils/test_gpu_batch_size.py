"""estimate_gpu_batch_size 纯函数单元测试。

独立于 test_gpu_memory_monitor.py（后者有 pynvml importorskip，
会跳过整个文件）。本文件只测纯函数，不需要 GPU/pynvml。
"""

from vibeocr.utils.gpu_memory_monitor import estimate_gpu_batch_size


def test_estimate_gpu_batch_size_large_vram_caps_at_10():
    """8G 显存（free 6G）、A4@300（8.7M 像素）→ 5× 放大、0.5 安全 → 夹到 10。"""
    batch = estimate_gpu_batch_size(free_mb=6144, avg_pixels=8_700_000)
    assert batch == 10


def test_estimate_gpu_batch_size_small_vram_scales_down():
    """2G 显存（free 1.5G）、A4@300 → 1536*0.5/124.45=6.17 → 6。"""
    batch = estimate_gpu_batch_size(free_mb=1536, avg_pixels=8_700_000)
    assert batch == 6


def test_estimate_gpu_batch_size_minimum_is_1():
    """极小显存也要至少 1。"""
    batch = estimate_gpu_batch_size(free_mb=100, avg_pixels=8_700_000)
    assert batch == 1


def test_estimate_gpu_batch_size_tiny_image():
    """小图（100K 像素）即便显存小也返回较大值，夹到 10。"""
    batch = estimate_gpu_batch_size(free_mb=2000, avg_pixels=100_000)
    assert batch == 10


def test_estimate_gpu_batch_size_zero_inputs_returns_1():
    """零或负输入兜底返回 1。"""
    assert estimate_gpu_batch_size(free_mb=0, avg_pixels=8_700_000) == 1
    assert estimate_gpu_batch_size(free_mb=6144, avg_pixels=0) == 1
