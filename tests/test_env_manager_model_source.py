"""Tests for PaddleX model source detection."""

import os
import pytest
from unittest.mock import patch, MagicMock

from vibeocr.env_manager import (
    detect_paddlex_model_source,
    setup_paddlex_model_source,
    PADDLEX_MODEL_SOURCES,
    PADDLEX_SOURCE_TEST_URLS,
)


class TestDetectPaddleXModelSource:
    """测试 PaddleX 模型源检测功能。"""

    def test_model_sources_defined(self):
        """模型源定义正确。"""
        assert "bos" in PADDLEX_MODEL_SOURCES
        assert "huggingface" in PADDLEX_MODEL_SOURCES
        assert PADDLEX_MODEL_SOURCES["bos"] == "BOS"
        assert PADDLEX_MODEL_SOURCES["huggingface"] == "HuggingFace"

    def test_model_source_test_urls_defined(self):
        """模型源测试 URL 定义正确。"""
        assert "bos" in PADDLEX_SOURCE_TEST_URLS
        assert "huggingface" in PADDLEX_SOURCE_TEST_URLS
        assert "bcebos.com" in PADDLEX_SOURCE_TEST_URLS["bos"]
        assert "huggingface.co" in PADDLEX_SOURCE_TEST_URLS["huggingface"]

    @patch("vibeocr.env_manager.urlopen")
    def test_detect_bos_faster(self, mock_urlopen):
        """BOS 更快时选择 BOS。"""
        import time

        def mock_response(url, *args, **kwargs):
            response = MagicMock()
            response.status = 200
            response.__enter__ = MagicMock(return_value=response)
            response.__exit__ = MagicMock(return_value=False)
            return response

        mock_urlopen.side_effect = mock_response

        # 模拟 BOS 更快
        with patch("time.time") as mock_time:
            # BOS: 0.5秒, HuggingFace: 2.0秒
            mock_time.side_effect = [0, 0.5, 0, 2.0]
            env_value, source_name = detect_paddlex_model_source(timeout=5)

        assert source_name == "bos"
        assert env_value == "BOS"

    @patch("vibeocr.env_manager.urlopen")
    def test_detect_huggingface_faster(self, mock_urlopen):
        """HuggingFace 更快时选择 HuggingFace。"""
        import time

        def mock_response(url, *args, **kwargs):
            response = MagicMock()
            response.status = 200
            response.__enter__ = MagicMock(return_value=response)
            response.__exit__ = MagicMock(return_value=False)
            return response

        mock_urlopen.side_effect = mock_response

        # 模拟 HuggingFace 更快
        with patch("time.time") as mock_time:
            # BOS: 2.0秒, HuggingFace: 0.5秒
            mock_time.side_effect = [0, 2.0, 0, 0.5]
            env_value, source_name = detect_paddlex_model_source(timeout=5)

        assert source_name == "huggingface"
        assert env_value == "HuggingFace"

    @patch("vibeocr.env_manager.urlopen")
    def test_detect_all_sources_unavailable(self, mock_urlopen):
        """所有源不可访问时返回默认 BOS。"""
        mock_urlopen.side_effect = Exception("Connection failed")

        env_value, source_name = detect_paddlex_model_source(timeout=1)

        assert source_name == "bos"
        assert env_value == "BOS"


class TestSetupPaddleXModelSource:
    """测试 PaddleX 模型源设置功能。"""

    def test_setup_sets_environment_variable(self):
        """设置函数正确设置环境变量。"""
        # 清除可能存在的环境变量
        if "PADDLE_PDX_MODEL_SOURCE" in os.environ:
            del os.environ["PADDLE_PDX_MODEL_SOURCE"]

        with patch("vibeocr.env_manager.detect_paddlex_model_source") as mock_detect:
            mock_detect.return_value = ("BOS", "bos")
            source = setup_paddlex_model_source()

        assert os.environ.get("PADDLE_PDX_MODEL_SOURCE") == "BOS"
        assert source == "bos"

        # 清理
        if "PADDLE_PDX_MODEL_SOURCE" in os.environ:
            del os.environ["PADDLE_PDX_MODEL_SOURCE"]

    def test_setup_returns_existing_value(self):
        """如果已设置环境变量，直接返回现有值。"""
        os.environ["PADDLE_PDX_MODEL_SOURCE"] = "HuggingFace"

        with patch("vibeocr.env_manager.detect_paddlex_model_source") as mock_detect:
            # 这个 mock 不应该被调用
            source = setup_paddlex_model_source()
            mock_detect.assert_not_called()

        assert source == "huggingface"

        # 清理
        del os.environ["PADDLE_PDX_MODEL_SOURCE"]
