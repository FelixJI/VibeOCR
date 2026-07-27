"""services 命名空间 __init__ 兼容层测试。"""

import pytest


def test_get_ocr_service_returns_instance():
    """get_ocr_service 返回 OCRService 实例（line 22-23）。"""
    from vibeocr.services import get_ocr_service

    # OCRService 是单例，构造不触发模型加载（lazy）
    svc = get_ocr_service()
    assert svc is not None
    assert svc.__class__.__name__ == "OCRService"


def test_getattr_ocr_service_returns_class():
    """services.OCRService 转发到直接实现（line 29-32）。"""
    import vibeocr.services as services

    cls = services.OCRService
    assert cls.__name__ == "OCRService"
    # 第二次访问命中缓存（globals 已设置）
    assert services.OCRService is cls


def test_getattr_unknown_attribute_raises():
    """请求已删除的历史导出时 raise AttributeError（line 33）。"""
    import vibeocr.services as services

    with pytest.raises(AttributeError, match="has no attribute"):
        _ = services.OCRServiceSubprocess  # noqa: F841


def test_getattr_other_unknown_attribute_raises():
    with pytest.raises(AttributeError, match="MinerUBatchService"):
        import vibeocr.services as services

        _ = services.MinerUBatchService  # noqa: F841
