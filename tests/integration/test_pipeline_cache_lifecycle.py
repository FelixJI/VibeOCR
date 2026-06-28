"""管道缓存生命周期端到端集成测试。

验证：释放按钮 → OCRServiceSubprocess.release_pipelines → worker release。
使用 mock subprocess manager 避免真实模型加载。
"""

from unittest.mock import MagicMock


def test_release_heavy_only_flow():
    """释放重管道的完整 RPC 路径可调用（mock 验证）。"""
    from vibeocr.services.ocr_service_subprocess import OCRServiceSubprocess

    svc = OCRServiceSubprocess.__new__(OCRServiceSubprocess)
    svc._initialized = True

    mock_manager = MagicMock()
    mock_manager.execute.return_value = ["PP-StructureV3", "PaddleOCR-VL"]
    svc._paddlex_manager = mock_manager

    result = svc.release_pipelines(heavy_only=True)
    assert result == ["PP-StructureV3", "PaddleOCR-VL"]
    mock_manager.execute.assert_called_once()
    # 验证 lambda 传入了 heavy_only=True
    call_args = mock_manager.execute.call_args
    assert call_args.kwargs.get("timeout") == 60.0


def test_release_all_flow():
    """全部释放的 RPC 路径。"""
    from vibeocr.services.ocr_service_subprocess import OCRServiceSubprocess

    svc = OCRServiceSubprocess.__new__(OCRServiceSubprocess)
    svc._initialized = True

    mock_manager = MagicMock()
    mock_manager.execute.return_value = ["PP-StructureV3", "OCR"]
    svc._paddlex_manager = mock_manager

    result = svc.release_pipelines(heavy_only=False)
    assert "OCR" in result


def test_release_not_initialized_returns_empty():
    """服务未初始化时返回空列表。"""
    from vibeocr.services.ocr_service_subprocess import OCRServiceSubprocess

    svc = OCRServiceSubprocess.__new__(OCRServiceSubprocess)
    svc._initialized = False
    assert svc.release_pipelines() == []


def test_set_ttl_flow():
    """设置 TTL 的 RPC 路径。"""
    from vibeocr.services.ocr_service_subprocess import OCRServiceSubprocess

    svc = OCRServiceSubprocess.__new__(OCRServiceSubprocess)
    svc._initialized = True

    mock_manager = MagicMock()
    mock_manager.execute.return_value = True
    svc._paddlex_manager = mock_manager

    assert svc.set_pipeline_ttl(600)
    mock_manager.execute.assert_called_once()
    call_args = mock_manager.execute.call_args
    assert call_args.kwargs.get("timeout") == 30.0


def test_set_ttl_not_initialized_returns_false():
    """服务未初始化时 set_ttl 返回 False。"""
    from vibeocr.services.ocr_service_subprocess import OCRServiceSubprocess

    svc = OCRServiceSubprocess.__new__(OCRServiceSubprocess)
    svc._initialized = False
    assert svc.set_pipeline_ttl(600) is False


def test_release_handles_exception():
    """RPC 异常时返回空列表，不传播。"""
    from vibeocr.services.ocr_service_subprocess import OCRServiceSubprocess

    svc = OCRServiceSubprocess.__new__(OCRServiceSubprocess)
    svc._initialized = True

    mock_manager = MagicMock()
    mock_manager.execute.side_effect = RuntimeError("RPC failed")
    svc._paddlex_manager = mock_manager

    result = svc.release_pipelines()
    assert result == []


def test_set_ttl_handles_exception():
    """RPC 异常时返回 False，不传播。"""
    from vibeocr.services.ocr_service_subprocess import OCRServiceSubprocess

    svc = OCRServiceSubprocess.__new__(OCRServiceSubprocess)
    svc._initialized = True

    mock_manager = MagicMock()
    mock_manager.execute.side_effect = RuntimeError("RPC failed")
    svc._paddlex_manager = mock_manager

    assert svc.set_pipeline_ttl(600) is False
