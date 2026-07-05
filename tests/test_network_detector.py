"""Tests for NetworkDetector."""

import os
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from vibeocr.machine_cache import CACHE_VERSION, generate_machine_id, save_cache


class TestNetworkDetectorConstants:
    def test_endpoints_defined(self):
        from vibeocr.network_detector import CHINA_ENDPOINT, INTERNATIONAL_ENDPOINT

        assert "bcebos.com" in CHINA_ENDPOINT
        assert "huggingface.co" in INTERNATIONAL_ENDPOINT


class TestNetworkDetectorDetection:
    """测试网络探测逻辑。"""

    @patch("vibeocr.network_detector.urlopen")
    def test_china_endpoint_faster_returns_domestic(self, mock_urlopen, tmp_path):
        """中国端点更快 → domestic 源。"""
        from vibeocr.network_detector import NetworkDetector

        def mock_response(req, *args, **kwargs):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            response = MagicMock()
            response.status = 200
            response.__enter__ = MagicMock(return_value=response)
            response.__exit__ = MagicMock(return_value=False)
            if "huggingface.co" in url:
                time.sleep(0.2)
                raise URLError(TimeoutError("timeout"))
            return response

        mock_urlopen.side_effect = mock_response
        detector = NetworkDetector(tmp_path, force_detect=True)
        assert detector.paddlex_source == "bos"
        assert detector.paddlex_source_env == "BOS"
        assert detector.mineru_source == "modelscope"
        assert detector.network_type == "domestic"
        assert "tsinghua" in detector.pip_mirror_url

    @patch("vibeocr.network_detector.urlopen")
    def test_international_endpoint_faster_returns_international(
        self, mock_urlopen, tmp_path
    ):
        """HuggingFace 更快 → international 源。"""
        from vibeocr.network_detector import NetworkDetector

        def mock_response(req, *args, **kwargs):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            response = MagicMock()
            response.status = 200
            response.__enter__ = MagicMock(return_value=response)
            response.__exit__ = MagicMock(return_value=False)
            if "bcebos.com" in url:
                time.sleep(0.2)
                raise URLError(TimeoutError("timeout"))
            return response

        mock_urlopen.side_effect = mock_response
        detector = NetworkDetector(tmp_path, force_detect=True)
        assert detector.paddlex_source == "huggingface"
        assert detector.paddlex_source_env == "HuggingFace"
        assert detector.mineru_source == "huggingface"
        assert detector.network_type == "international"
        assert "pypi.org" in detector.pip_mirror_url

    @patch("vibeocr.network_detector.urlopen")
    def test_both_endpoints_fail_returns_domestic_default(self, mock_urlopen, tmp_path):
        """两个端点都不可达 → 默认 domestic。"""
        from vibeocr.network_detector import NetworkDetector

        mock_urlopen.side_effect = URLError(OSError("Connection failed"))
        detector = NetworkDetector(tmp_path, force_detect=True)
        assert detector.paddlex_source == "bos"
        assert detector.mineru_source == "modelscope"
        assert detector.network_type == "domestic"


class TestNetworkDetectorCache:
    """测试缓存逻辑。"""

    def test_cache_hit_skips_detection(self, tmp_path):
        """缓存有效期内跳过探测。"""
        from vibeocr.network_detector import NetworkDetector

        network = {
            "last_detected": datetime.now().isoformat(),
            "paddlex_source": "huggingface",
            "mineru_source": "huggingface",
        }
        save_cache(
            tmp_path,
            {
                "version": CACHE_VERSION,
                "machine_id": generate_machine_id(),
                "network": network,
            },
        )
        detector = NetworkDetector(tmp_path)
        assert detector.paddlex_source == "huggingface"
        assert detector.mineru_source == "huggingface"

    def test_cache_expired_triggers_detection(self, tmp_path):
        """缓存超过 7 天触发重新探测。"""
        from vibeocr.network_detector import NetworkDetector

        old_time = (datetime.now() - timedelta(days=8)).isoformat()
        network = {
            "last_detected": old_time,
            "paddlex_source": "huggingface",
            "mineru_source": "huggingface",
        }
        save_cache(
            tmp_path,
            {
                "version": CACHE_VERSION,
                "machine_id": generate_machine_id(),
                "network": network,
            },
        )
        with patch("vibeocr.network_detector.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = URLError(OSError("Connection failed"))
            detector = NetworkDetector(tmp_path)
        assert detector.paddlex_source == "bos"

    def test_machine_id_changed_triggers_detection(self, tmp_path):
        """机器码变化触发重新探测。"""
        from vibeocr.network_detector import NetworkDetector

        network = {
            "last_detected": datetime.now().isoformat(),
            "paddlex_source": "huggingface",
            "mineru_source": "huggingface",
        }
        save_cache(
            tmp_path,
            {
                "version": CACHE_VERSION,
                "machine_id": "wrong_machine_id",
                "network": network,
            },
        )
        with patch("vibeocr.network_detector.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = URLError(OSError("Connection failed"))
            detector = NetworkDetector(tmp_path)
        assert detector.paddlex_source == "bos"


class TestNetworkDetectorEnvVar:
    """测试环境变量设置。"""

    def test_paddlex_source_env_sets_environ(self, tmp_path):
        """paddlex_source_env 自动设置 os.environ。"""
        from vibeocr.network_detector import NetworkDetector

        if "PADDLE_PDX_MODEL_SOURCE" in os.environ:
            del os.environ["PADDLE_PDX_MODEL_SOURCE"]
        network = {
            "last_detected": datetime.now().isoformat(),
            "paddlex_source": "bos",
            "mineru_source": "modelscope",
        }
        save_cache(
            tmp_path,
            {
                "version": CACHE_VERSION,
                "machine_id": generate_machine_id(),
                "network": network,
            },
        )
        detector = NetworkDetector(tmp_path)
        assert detector.paddlex_source_env == "BOS"
        assert os.environ.get("PADDLE_PDX_MODEL_SOURCE") == "BOS"
        if "PADDLE_PDX_MODEL_SOURCE" in os.environ:
            del os.environ["PADDLE_PDX_MODEL_SOURCE"]
