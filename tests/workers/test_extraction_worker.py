# tests/workers/test_extraction_worker.py
"""测试 ExtractionWorker"""

from unittest.mock import Mock, mock_open, patch

import pytest

from vibeocr.models.extraction_options import ExtractionOptions
from vibeocr.workers.extraction_worker import ExtractionWorker


class TestExtractionWorker:
    """测试信息抽取 Worker"""

    def test_create_worker(self, qtbot):
        """测试创建 Worker"""
        options = ExtractionOptions()
        worker = ExtractionWorker(
            service=Mock(),
            files=[{"path": "/tmp/test.png", "name": "test.png"}],
            keys=["姓名", "日期"],
            options=options,
        )
        assert worker is not None
        assert not worker.is_cancelled

    def test_cancel_worker(self, qtbot):
        """测试取消 Worker"""
        worker = ExtractionWorker(
            service=Mock(), files=[], keys=[], options=ExtractionOptions()
        )
        worker.cancel()
        assert worker.is_cancelled is True

    def test_worker_signals(self, qtbot):
        """测试 Worker 信号定义"""
        worker = ExtractionWorker(
            service=Mock(), files=[], keys=[], options=ExtractionOptions()
        )
        # 验证信号存在
        assert hasattr(worker, "progress")
        assert hasattr(worker, "file_completed")
        assert hasattr(worker, "finished")
        assert hasattr(worker, "error")

    def test_worker_initialization_with_llm_config(self, qtbot):
        """测试带 LLM 配置的 Worker 初始化"""
        llm_config = {"model": "gpt-4", "api_key": "test-key"}
        worker = ExtractionWorker(
            service=Mock(),
            files=[{"path": "/test/file.png", "name": "file.png"}],
            keys=["name"],
            options=ExtractionOptions(),
            llm_config=llm_config,
        )

        assert worker._llm_config == llm_config

    def test_get_items(self, qtbot):
        """测试获取项目列表"""
        files = [
            {"path": "/test/file1.png", "name": "file1.png"},
            {"path": "/test/file2.jpg", "name": "file2.jpg"},
        ]
        worker = ExtractionWorker(
            service=Mock(), files=files, keys=["name"], options=ExtractionOptions()
        )

        items = worker._get_items()
        assert items == files
        assert len(items) == 2

    def test_get_item_id(self, qtbot):
        """测试获取项目 ID"""
        files = [{"path": "/test/file1.png", "name": "file1.png"}]
        worker = ExtractionWorker(
            service=Mock(), files=files, keys=["name"], options=ExtractionOptions()
        )

        item_id = worker._get_item_id(files[0], 0)
        assert item_id == "/test/file1.png"

    def test_get_item_id_fallback(self, qtbot):
        """测试获取项目 ID 回退"""
        worker = ExtractionWorker(
            service=Mock(),
            files=[{"name": "test.png"}],
            keys=["name"],
            options=ExtractionOptions(),
        )

        item_id = worker._get_item_id({"name": "test.png"}, 5)
        assert item_id == "file_5"

    def test_get_file_name(self, qtbot):
        """测试获取文件名"""
        files = [{"path": "/test/file1.png", "name": "file1.png"}]
        worker = ExtractionWorker(
            service=Mock(), files=files, keys=["name"], options=ExtractionOptions()
        )

        name = worker._get_file_name(files[0])
        assert name == "file1.png"

    def test_get_file_name_from_path(self, qtbot):
        """测试从路径获取文件名"""
        worker = ExtractionWorker(
            service=Mock(),
            files=[{"path": "/path/to/file.png"}],
            keys=["name"],
            options=ExtractionOptions(),
        )

        name = worker._get_file_name({"path": "/path/to/file.png"})
        assert name == "file.png"

    @patch("builtins.open", mock_open(read_data=b"fake_image_data"))
    def test_process_item(self, qtbot):
        """测试处理单个项目"""
        files = [{"path": "/test/file.png", "name": "file.png"}]
        worker = ExtractionWorker(
            service=Mock(),
            files=files,
            keys=["name", "date"],
            options=ExtractionOptions(),
        )

        result = worker._process_item(files[0], 0)

        assert "keys" in result
        assert "values" in result
        assert result["keys"] == ["name", "date"]
        assert "name" in result["values"]
        assert "date" in result["values"]

    @patch("builtins.open", mock_open(read_data=b"fake_image_data"))
    def test_extract_raises_without_service(self, qtbot):
        """测试无服务时抛出异常"""
        worker = ExtractionWorker(
            service=None,
            files=[{"path": "/test/file.png"}],
            keys=["name"],
            options=ExtractionOptions(),
        )

        with pytest.raises(RuntimeError, match="OCR 服务未设置"):
            worker._process_item({"path": "/test/file.png"}, 0)

    def test_inheritance(self, qtbot):
        """测试继承 BatchWorker"""
        from vibeocr.core import BatchWorker

        worker = ExtractionWorker(
            service=Mock(), files=[], keys=[], options=ExtractionOptions()
        )

        assert isinstance(worker, BatchWorker)
