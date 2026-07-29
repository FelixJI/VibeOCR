"""前端生命周期常量契约测试。

这些常量独立于 Qt 类，被 PySide 生产代码引用；此处固定其值与导出表面，
避免后续误改导致 PDF worker 等待行为回归。
"""

from vibeocr.runtime_contracts.contracts import frontend


def test_pdf_worker_terminate_wait_ms_value() -> None:
    """PDF worker 终止等待时长固定为 500ms。"""
    assert frontend.PDF_WORKER_TERMINATE_WAIT_MS == 500


def test_pdf_thumbnail_drain_wait_ms_value() -> None:
    """PDF 缩略图排空等待时长固定为 6000ms。"""
    assert frontend.PDF_THUMBNAIL_DRAIN_WAIT_MS == 6000


def test_all_exports_both_constants() -> None:
    """__all__ 仅导出两个常量，且按字母序排列。"""
    assert frontend.__all__ == [
        "PDF_THUMBNAIL_DRAIN_WAIT_MS",
        "PDF_WORKER_TERMINATE_WAIT_MS",
    ]
