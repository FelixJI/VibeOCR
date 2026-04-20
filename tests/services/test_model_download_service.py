"""ModelDownloadService 单元测试"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from vibeocr.services.model_download_service import (
    DownloadStatus,
    ModelDownloadService,
)


class TestDownloadStatus:
    def test_status_values(self):
        assert DownloadStatus.PENDING == "pending"
        assert DownloadStatus.DOWNLOADING == "downloading"
        assert DownloadStatus.COMPLETED == "completed"
        assert DownloadStatus.FAILED == "failed"
        assert DownloadStatus.SKIPPED == "skipped"


class TestModelDownloadService:
    def test_get_status_all_pending_initially(self, tmp_path):
        service = ModelDownloadService(tmp_path)
        status = service.get_status()
        assert all(s == DownloadStatus.PENDING for s in status.values())

    def test_get_status_includes_paddlex_pipelines(self, tmp_path):
        service = ModelDownloadService(tmp_path)
        status = service.get_status()
        assert "OCR" in status
        assert "table_recognition" in status
        assert "formula_recognition" in status
        assert "MinerU" in status

    @patch("vibeocr.services.model_download_service._download_paddlex_pipeline")
    @patch("vibeocr.services.model_download_service._download_mineru_models")
    def test_download_all_success(self, mock_mineru, mock_paddlex, tmp_path):
        mock_paddlex.return_value = True
        mock_mineru.return_value = True
        service = ModelDownloadService(tmp_path)
        progress = []

        results = service.download_all(
            progress_callback=lambda stage, msg: progress.append((stage, msg)),
        )

        assert all(s == DownloadStatus.COMPLETED for s in results.values())
        assert len(progress) > 0

    @patch("vibeocr.services.model_download_service._download_paddlex_pipeline")
    @patch("vibeocr.services.model_download_service._download_mineru_models")
    def test_download_all_cancel(self, mock_mineru, mock_paddlex, tmp_path):
        import threading
        mock_paddlex.return_value = True
        cancel_event = threading.Event()
        cancel_event.set()  # 立即取消

        service = ModelDownloadService(tmp_path)
        results = service.download_all(cancel_event=cancel_event)

        assert all(s == DownloadStatus.SKIPPED for s in results.values())

    @patch("vibeocr.services.model_download_service._download_paddlex_pipeline")
    @patch("vibeocr.services.model_download_service._download_mineru_models")
    def test_download_all_partial_failure(self, mock_mineru, mock_paddlex, tmp_path):
        mock_paddlex.side_effect = [True, False, True]
        mock_mineru.return_value = True

        service = ModelDownloadService(tmp_path)
        results = service.download_all()

        assert results["OCR"] == DownloadStatus.COMPLETED
        assert results["table_recognition"] == DownloadStatus.FAILED
        assert results["formula_recognition"] == DownloadStatus.COMPLETED
        assert results["MinerU"] == DownloadStatus.COMPLETED

    @patch("vibeocr.services.model_download_service._download_paddlex_pipeline")
    def test_download_single_pipeline(self, mock_paddlex, tmp_path):
        mock_paddlex.return_value = True
        service = ModelDownloadService(tmp_path)
        result = service.download_pipeline("OCR")
        assert result is True

    @patch("vibeocr.services.model_download_service._download_mineru_models")
    def test_download_mineru(self, mock_mineru, tmp_path):
        mock_mineru.return_value = True
        service = ModelDownloadService(tmp_path)
        result = service.download_mineru_models()
        assert result is True
