"""测试 BatchRecognitionWorker.run() 的分小批 recognize_batch 流程。

回归任务2：批量识别 Tab 从逐文件 batch_add/batch_commit（N 次 SHM 往返）
迁移到分小批 recognize_batch（ceil(N/16) 次往返）。验证：
1. 多文件分批正确、结果顺序与文件一致
2. 每批完成逐文件发 file_completed + progress（UI 流式反馈）
3. 单张失败映射为 {"error": ...}
4. 取消在批边界生效
5. 读取文件失败单独标记 failed，不影响整批
6. recognize_batch 整批抛异常时本批标记 failed，继续下一批
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

try:
    from vibeocr.views.batch_recognition_tab import BatchRecognitionWorker

    HAS_MODULE = True
except ImportError:
    BatchRecognitionWorker = None  # type: ignore[assignment,misc]
    HAS_MODULE = False


def _make_worker(service, files, options=None) -> Any:
    """构造 worker（正常 __init__ 以激活 Qt 信号，但不 start 线程）。"""
    assert BatchRecognitionWorker is not None
    if options is None:
        from vibeocr.models.ocr_options import OCROptions

        options = OCROptions()
    # 传 mock service；files 用真实 file_info；parent=None 避免线程归属问题。
    # 不调用 .start()，直接测 run() 同步逻辑。
    return BatchRecognitionWorker(service, files, options, parent=None)


def _make_image_files(tmp_path: Path, n: int) -> list[dict]:
    """造 n 个图片文件，返回 file_info 列表。"""
    files = []
    for i in range(n):
        p = tmp_path / f"img_{i}.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10)  # 假 PNG
        files.append({"path": str(p), "name": p.name})
    return files


@pytest.mark.skipif(not HAS_MODULE, reason="batch_recognition_tab not available")
class TestBatchRecognitionWorkerRecognizeBatch:
    """验证 run() 分小批调用 recognize_batch。"""

    def test_single_batch_all_succeed(self, tmp_path, qtbot):
        """少于 16 个文件时单批完成，逐文件上报 completed。"""
        files = _make_image_files(tmp_path, 3)
        mock_service = MagicMock()
        # recognize_batch 返回 3 个结果（顺序与输入一致）
        mock_service.recognize_batch.return_value = [
            MagicMock(text="result0"),
            MagicMock(text="result1"),
            MagicMock(text="result2"),
        ]
        worker = _make_worker(mock_service, files)
        completed_signals = []
        worker.file_completed.connect(lambda fp, st, res: completed_signals.append((fp, st, res)))
        finished_results = {}
        worker.finished.connect(lambda r: finished_results.update(r))

        worker.run()

        # recognize_batch 调用一次（3 < 16 单批）
        assert mock_service.recognize_batch.call_count == 1
        # 3 个文件全部 completed
        statuses = [st for _, st, _ in completed_signals]
        assert statuses == ["completed", "completed", "completed"]
        assert len(finished_results) == 3

    def test_multi_batch_chunking(self, tmp_path, qtbot):
        """超过 16 个文件时分多批，每批一次 recognize_batch 调用。"""
        files = _make_image_files(tmp_path, 20)
        mock_service = MagicMock()
        # 每次 recognize_batch 返回与输入等长的结果列表
        def fake_recognize(images, options):
            return [MagicMock(text=f"r{i}") for i in range(len(images))]
        mock_service.recognize_batch.side_effect = fake_recognize

        worker = _make_worker(mock_service, files)
        completed_signals = []
        worker.file_completed.connect(lambda fp, st, res: completed_signals.append((fp, st)))
        worker.finished.connect(lambda r: None)

        worker.run()

        # 20 个文件，16+4 分两批
        assert mock_service.recognize_batch.call_count == 2
        # 第一批 16 个，第二批 4 个
        first_batch_args = mock_service.recognize_batch.call_args_list[0][0][0]
        second_batch_args = mock_service.recognize_batch.call_args_list[1][0][0]
        assert len(first_batch_args) == 16
        assert len(second_batch_args) == 4
        # 全部 completed
        statuses = [st for _, st in completed_signals]
        assert all(s == "completed" for s in statuses)
        assert len(statuses) == 20

    def test_single_image_failure_mapped_to_error(self, tmp_path, qtbot):
        """recognize_batch 返回 None 表示该图失败 → file_completed 发 failed。"""
        files = _make_image_files(tmp_path, 3)
        mock_service = MagicMock()
        mock_service.recognize_batch.return_value = [
            MagicMock(text="ok"),
            None,  # 第二张失败
            MagicMock(text="ok3"),
        ]
        worker = _make_worker(mock_service, files)
        completed_signals = []
        worker.file_completed.connect(lambda fp, st, res: completed_signals.append((fp, st, res)))

        worker.run()

        assert completed_signals[1][1] == "failed"
        assert "error" in completed_signals[1][2]
        assert completed_signals[0][1] == "completed"
        assert completed_signals[2][1] == "completed"

    def test_cancel_stops_at_batch_boundary(self, tmp_path, qtbot):
        """取消后不再调用 recognize_batch（批边界检查）。"""
        files = _make_image_files(tmp_path, 40)  # 3 批：16+16+8
        mock_service = MagicMock()
        call_count = [0]

        def fake_recognize(images, options):
            call_count[0] += 1
            if call_count[0] == 1:
                # 第一批完成后取消
                worker._cancelled = True
            return [MagicMock(text="r") for _ in images]
        mock_service.recognize_batch.side_effect = fake_recognize

        worker = _make_worker(mock_service, files)
        worker.file_completed.connect(lambda *a: None)
        worker.finished.connect(lambda r: None)

        worker.run()

        # 只处理了第一批 16 个，取消后不再调
        assert mock_service.recognize_batch.call_count == 1

    def test_read_failure_marks_single_file_failed(self, tmp_path, qtbot):
        """读取文件失败时该文件标记 failed，其余正常识别。"""
        good1 = tmp_path / "g1.png"
        good1.write_bytes(b"png")
        missing = {"path": str(tmp_path / "nonexistent.png"), "name": "nonexistent.png"}
        good2 = tmp_path / "g2.png"
        good2.write_bytes(b"png")
        files = [
            {"path": str(good1), "name": good1.name},
            missing,
            {"path": str(good2), "name": good2.name},
        ]
        mock_service = MagicMock()
        # 只有两个有效图，返回两个结果
        mock_service.recognize_batch.return_value = [
            MagicMock(text="r1"), MagicMock(text="r2")
        ]
        worker = _make_worker(mock_service, files)
        completed_signals = []
        worker.file_completed.connect(lambda fp, st, res: completed_signals.append((Path(fp).name, st)))

        worker.run()

        # 中间文件 failed，其余 completed
        statuses = dict(completed_signals)
        assert statuses["g1.png"] == "completed"
        assert statuses["nonexistent.png"] == "failed"
        assert statuses["g2.png"] == "completed"

    def test_recognize_batch_exception_marks_batch_failed_and_continues(
        self, tmp_path, qtbot
    ):
        """某批 recognize_batch 抛异常时该批标记 failed，下一批继续。"""
        files = _make_image_files(tmp_path, 20)  # 2 批
        mock_service = MagicMock()
        call_count = [0]

        def fake_recognize(images, options):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("GPU error")
            return [MagicMock(text="r") for _ in images]
        mock_service.recognize_batch.side_effect = fake_recognize

        worker = _make_worker(mock_service, files)
        completed_signals = []
        worker.file_completed.connect(lambda fp, st, res: completed_signals.append(st))
        error_signals = []
        worker.error.connect(lambda msg: error_signals.append(msg))

        worker.run()

        # 第一批抛异常 → error 信号 + 16 个 failed
        assert len(error_signals) == 1
        # 第二批正常 → 4 个 completed
        assert completed_signals[:16] == ["failed"] * 16
        assert completed_signals[16:] == ["completed"] * 4
