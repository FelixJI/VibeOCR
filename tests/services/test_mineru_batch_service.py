"""mineru_batch_service MinerU 直接批量处理服务测试"""

from unittest.mock import MagicMock, patch

from vibeocr.services.mineru_batch_service import MinerUBatchService


class TestBatchAdd:
    def test_add_returns_request_id(self):
        svc = MinerUBatchService()
        rid = svc.batch_add(b"data", file_name="test.pdf")
        assert isinstance(rid, str)
        assert len(rid) == 12

    def test_add_queues_item(self):
        svc = MinerUBatchService()
        rid = svc.batch_add(b"img", file_name="a.png")
        assert len(svc._queue) == 1
        assert svc._queue[0]["request_id"] == rid
        assert svc._queue[0]["data"] == b"img"
        assert svc._queue[0]["file_name"] == "a.png"

    def test_add_detects_mime_type(self):
        svc = MinerUBatchService()
        svc.batch_add(b"", file_name="doc.pdf")
        assert svc._queue[0]["mime_type"] == "application/pdf"

    def test_add_maps_request(self):
        svc = MinerUBatchService()
        rid = svc.batch_add(b"data", file_name="x.txt")
        assert rid in svc._request_map
        assert svc._request_map[rid]["data"] == b"data"

    def test_multiple_adds(self):
        svc = MinerUBatchService()
        svc.batch_add(b"a", file_name="a.pdf")
        svc.batch_add(b"b", file_name="b.pdf")
        assert len(svc._queue) == 2
        assert len(svc._request_map) == 2


class TestBatchCommit:
    @patch("vibeocr.services.mineru_service.MinerUService")
    def test_commit_processes_queue(self, MockMinerU):
        mock_instance = MagicMock()
        mock_result = MagicMock()
        mock_instance.parse.return_value = mock_result
        MockMinerU.return_value = mock_instance

        svc = MinerUBatchService()
        rid = svc.batch_add(b"data", file_name="test.pdf")
        results = svc.batch_commit()

        assert rid in results
        assert results[rid] == mock_result
        mock_instance.parse.assert_called_once()

    @patch("vibeocr.services.mineru_service.MinerUService")
    def test_commit_clears_queue(self, MockMinerU):
        MockMinerU.return_value = MagicMock()

        svc = MinerUBatchService()
        svc.batch_add(b"d", file_name="f.pdf")
        svc.batch_commit()

        assert len(svc._queue) == 0
        assert len(svc._request_map) == 0

    @patch("vibeocr.services.mineru_service.MinerUService")
    def test_commit_calls_progress(self, MockMinerU):
        MockMinerU.return_value = MagicMock()

        svc = MinerUBatchService()
        svc.batch_add(b"d", file_name="f.pdf")

        progress = MagicMock()
        svc.batch_commit(progress_callback=progress)

        assert progress.call_count >= 2

    @patch("vibeocr.services.mineru_service.MinerUService")
    def test_commit_calls_file_completed(self, MockMinerU):
        mock_result = MagicMock()
        MockMinerU.return_value = MagicMock(parse=MagicMock(return_value=mock_result))

        svc = MinerUBatchService()
        rid = svc.batch_add(b"d", file_name="f.pdf")

        callback = MagicMock()
        svc.batch_commit(file_completed_callback=callback)

        callback.assert_called_once_with(rid, mock_result)

    @patch("vibeocr.services.mineru_service.MinerUService")
    def test_commit_handles_error(self, MockMinerU):
        MockMinerU.return_value = MagicMock(
            parse=MagicMock(side_effect=RuntimeError("boom"))
        )

        svc = MinerUBatchService()
        rid = svc.batch_add(b"d", file_name="f.pdf")

        callback = MagicMock()
        results = svc.batch_commit(file_completed_callback=callback)

        assert "error" in results[rid]
        callback.assert_called_once()

    @patch("vibeocr.services.mineru_service.MinerUService")
    def test_commit_cancels_midway(self, MockMinerU):
        MockMinerU.return_value = MagicMock()

        svc = MinerUBatchService()
        svc.batch_add(b"a", file_name="a.pdf")
        svc.batch_add(b"b", file_name="b.pdf")

        def cancel_on_first(completed, total, name):
            if name == "a.pdf":
                svc._cancelled = True

        progress = MagicMock(side_effect=cancel_on_first)
        results = svc.batch_commit(progress_callback=progress)

        assert len(results) == 1


class TestBatchCancel:
    def test_cancel_sets_flag(self):
        svc = MinerUBatchService()
        assert not svc._cancelled
        svc.batch_cancel()
        assert svc._cancelled
