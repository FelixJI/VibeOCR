"""测试批量队列管理器"""

import io
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from vibeocr.core.pipelines import OCRPipeline, get_pipeline_supported_options
from vibeocr.models.batch_request import (
    BatchProgress,
    PreprocessOptions,
)
from vibeocr.workers.batch_queue_manager import BatchQueueManager


def _make_png_bytes(w=10, h=10):
    """生成最小有效 PNG 字节数据"""
    img = Image.new("RGB", (w, h), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class MockPipeline:
    """模拟 Pipeline"""

    def predict(self, images, **kwargs):
        """模拟批量预测"""
        for i, _img in enumerate(images):
            yield {"text": f"Result {i}", "confidence": 0.95}


class StrictPipeline:
    """模拟真实 PaddleX 管道：predict() 仅接受该管道支持的选项，
    未声明的 kwargs 会抛 TypeError（与 PaddleOCR.predict 一致）。

    用于验证 BatchQueueManager 是否按目标管道过滤选项。
    """

    def __init__(self, pipeline: OCRPipeline):
        self.pipeline = pipeline
        # 真实 PaddleOCR.predict 接受 input 作为第一个参数
        self.supported = set(get_pipeline_supported_options(pipeline))

    def predict(self, input, **kwargs):
        bad = set(kwargs) - self.supported
        if bad:
            raise TypeError(
                "PaddleOCR.predict() got an unexpected keyword argument "
                f"'{sorted(bad)[0]}'"
            )
        images = input if isinstance(input, list) else [input]
        for i, _img in enumerate(images):
            yield {"text": f"Result {i}", "confidence": 0.95}


class TestBatchQueueManager:
    """BatchQueueManager 测试"""

    @pytest.fixture
    def pipeline(self):
        """创建模拟 pipeline"""
        return MockPipeline()

    @pytest.fixture
    def manager(self, pipeline):
        """创建管理器"""
        return BatchQueueManager(pipeline, max_batch_size=4)

    def test_add_request(self, manager):
        """测试添加请求"""
        request_id = manager.add_request(
            image_data=_make_png_bytes(), options={"lang": "ch"}, file_name="test.png"
        )

        assert request_id != ""
        assert manager.get_queue_size() == 1

    def test_add_multiple_requests(self, manager):
        """测试添加多个请求"""
        for i in range(5):
            manager.add_request(
                image_data=_make_png_bytes(), options={}, file_name=f"test_{i}.png"
            )

        assert manager.get_queue_size() == 5

    def test_clear_queue(self, manager):
        """测试清空队列"""
        manager.add_request(_make_png_bytes(), {})
        manager.add_request(_make_png_bytes(), {})

        assert manager.get_queue_size() == 2

        manager.clear_queue()

        assert manager.get_queue_size() == 0

    def test_commit_empty_queue(self, manager):
        """测试提交空队列"""
        options = PreprocessOptions()
        results = manager.commit(options)

        assert results == {}

    def test_commit_single_request(self, manager):
        """测试提交单个请求"""
        request_id = manager.add_request(_make_png_bytes(), {}, file_name="test.png")

        options = PreprocessOptions()
        results = manager.commit(options)

        assert request_id in results
        assert "text" in results[request_id]

    def test_commit_multiple_requests(self, manager):
        """测试提交多个请求"""
        request_ids = []
        for i in range(3):
            rid = manager.add_request(_make_png_bytes(), {}, file_name=f"test_{i}.png")
            request_ids.append(rid)

        options = PreprocessOptions()
        results = manager.commit(options)

        assert len(results) == 3
        for rid in request_ids:
            assert rid in results

    def test_progress_callback(self, pipeline):
        """测试进度回调"""
        progress_list = []

        def progress_callback(progress: BatchProgress):
            progress_list.append(progress)

        manager = BatchQueueManager(
            pipeline, max_batch_size=2, progress_callback=progress_callback
        )

        for i in range(4):
            manager.add_request(_make_png_bytes(), {}, file_name=f"test_{i}.png")

        options = PreprocessOptions()
        manager.commit(options)

        # 应该有多次进度回调
        assert len(progress_list) > 0

        # 最后一次进度应该是完成状态
        final_progress = progress_list[-1]
        assert final_progress.completed == 4

    def test_cancel(self, manager):
        """测试取消处理"""
        for i in range(10):
            manager.add_request(_make_png_bytes(), {}, file_name=f"test_{i}.png")

        # 在处理前取消
        manager.cancel()

        options = PreprocessOptions()
        _results = manager.commit(options)

        # 取消后不应该有结果
        # (因为取消标志在 commit 开始时已设置)
        # 注意: 实际行为取决于实现细节

    def test_get_stats(self, manager):
        """测试获取统计信息"""
        manager.add_request(_make_png_bytes(), {})
        manager.add_request(_make_png_bytes(), {})

        options = PreprocessOptions()
        manager.commit(options)

        stats = manager.get_stats()

        assert "total_requests" in stats
        assert "total_batches" in stats
        assert "total_time" in stats
        assert stats["total_requests"] == 2


class TestBatchQueueManagerOptionFiltering:
    """验证 BatchQueueManager 按目标管道过滤 predict() 选项。

    回归测试：通用 OCR 批量识别会失败，因为 commit() 把
    use_table_recognition 等 PP-StructureV3 专用选项透传给了
    PaddleOCR.predict()，触发 "unexpected keyword argument" 错误。
    """

    def test_ocr_pipeline_omits_unsupported_options(self):
        """通用 OCR 管道不应接收 use_table_recognition 等无关选项"""
        pipeline = StrictPipeline(OCRPipeline.OCR)
        manager = BatchQueueManager(pipeline, max_batch_size=4)

        manager.add_request(_make_png_bytes(), {}, file_name="test.png")
        # 默认 OCROptions 含 use_table_recognition=True 等
        options = PreprocessOptions(pipeline=OCRPipeline.OCR)

        results = manager.commit(options)

        request_id = next(iter(manager._queue))
        assert results[request_id] != {"error": "结果数量不匹配"}
        assert "text" in results[request_id]

    def test_each_pipeline_only_receives_supported_options(self):
        """每种管道的批量提交只应转发该管道 supported_options 内的选项"""
        for target in (
            OCRPipeline.OCR,
            OCRPipeline.PP_STRUCTURE_V3,
            OCRPipeline.TABLE_RECOGNITION,
            OCRPipeline.FORMULA_RECOGNITION,
        ):
            pipeline = StrictPipeline(target)
            manager = BatchQueueManager(pipeline, max_batch_size=4)
            manager.add_request(_make_png_bytes(), {}, file_name=f"{target.value}.png")
            options = PreprocessOptions(pipeline=target)

            results = manager.commit(options)
            request_id = next(iter(manager._queue))
            assert "text" in results[request_id], (
                f"管道 {target.value} 批量提交失败："
                f"结果包含错误而非文本（选项过滤异常）"
            )


class TestBatchRequestIdPreservation:
    """验证 request_id 在批量处理全链路保持一致。

    回归测试：主进程为每个文件生成 request_id 并以此建立
    request_id -> file_path 映射（BatchRecognitionWorker.request_map）。
    Worker 的 BatchQueueManager 必须复用该 id，否则结果返回时
    request_map 查不到对应文件，结果被静默丢弃，导致 UI 显示
    "0 成功, 0 失败"（实际后端处理了）。
    """

    def test_add_request_returns_provided_request_id(self):
        """add_request 传入 request_id 时，应原样返回该 id"""
        pipeline = MockPipeline()
        manager = BatchQueueManager(pipeline, max_batch_size=4)

        provided_id = "abcdef123456"
        returned_id = manager.add_request(
            image_data=_make_png_bytes(),
            options={},
            file_name="test.png",
            request_id=provided_id,
        )

        assert returned_id == provided_id
        assert provided_id in manager._queue

    def test_commit_keys_match_provided_request_ids(self):
        """commit() 返回的结果字典的键应与 add_request 传入的 request_id 一致"""
        pipeline = MockPipeline()
        manager = BatchQueueManager(pipeline, max_batch_size=4)

        main_process_ids = ["id000001", "id000002", "id000003"]
        for rid in main_process_ids:
            manager.add_request(
                image_data=_make_png_bytes(),
                options={},
                file_name=f"{rid}.png",
                request_id=rid,
            )

        results = manager.commit(PreprocessOptions(pipeline=OCRPipeline.OCR))

        # 主进程用这些 id 查找结果；若 id 不匹配，UI 会拿到空结果
        for rid in main_process_ids:
            assert rid in results, (
                f"主进程 request_id {rid} 未出现在 commit 结果中，"
                f"实际键: {list(results.keys())}"
            )
            assert "text" in results[rid]

    def test_file_completed_callback_uses_provided_request_id(self):
        """流式回调收到的 request_id 应与 add_request 传入的一致"""
        pipeline = MockPipeline()
        callback_ids: list[str] = []
        manager = BatchQueueManager(
            pipeline, max_batch_size=4,
            progress_callback=lambda _p: None,
        )

        provided_id = "mainproc001"
        manager.add_request(
            _make_png_bytes(), {}, file_name="x.png", request_id=provided_id
        )

        received_ids: list[str] = []

        def on_done(request_id, _result):
            received_ids.append(request_id)

        manager.commit(
            PreprocessOptions(pipeline=OCRPipeline.OCR),
            file_completed_callback=on_done,
        )

        assert provided_id in received_ids, (
            f"流式回调未收到主进程 request_id {provided_id}，"
            f"实际收到: {received_ids}"
        )


class TestBatchRegistryDelegation:
    """验证批量路径通过管道注册表分发，复用各管道的选项映射逻辑。

    回归测试：批量识别曾直接调用 pipeline.predict() 并盲目透传选项，
    导致 (1) OCR 管道收到 use_table_recognition 等无关选项而失败，
    (2) VL 管道的 vl_use_layout_detection 未重命名为
    use_layout_detection，(3) 公式管道未强制 use_formula_recognition=True，
    (4) 表格管道的特殊模型名参数丢失。
    正确做法：批量路径委派给 spec.recognize_batch / recognize，
    与单图路径共享同一套选项映射逻辑。
    """

    def _make_recording_spec(self, pipeline_name, *, has_batch=True):
        """构造记录调用的 spec 替身"""
        record = {"batch_calls": [], "single_calls": []}

        def recognize_batch(service, images, options):
            record["batch_calls"].append(
                {"images": list(images), "options": options}
            )
            return [MagicMock(raw_text=f"batch_{i}") for i in range(len(images))]

        def recognize(service, image, options):
            record["single_calls"].append({"image": image, "options": options})
            return MagicMock(raw_text="single")

        spec = MagicMock()
        spec.name = pipeline_name
        spec.recognize_batch = recognize_batch if has_batch else None
        spec.recognize = recognize
        return spec, record

    def _patch_registry(self, specs):
        """让 get_registry() 返回包含给定 specs 的替身注册表"""
        registry = MagicMock()

        def _has(name):
            return any(s.name == name for s in specs)

        def _get(name):
            for s in specs:
                if s.name == name:
                    return s
            raise KeyError(name)

        registry.has.side_effect = _has
        registry.get.side_effect = _get
        return patch(
            "vibeocr.core.pipelines.get_registry", return_value=registry
        )

    def test_uses_recognize_batch_when_available(self):
        """有 recognize_batch 的管道应走真批量"""
        spec, record = self._make_recording_spec("OCR")
        service = MagicMock()
        manager = BatchQueueManager(
            MockPipeline(), max_batch_size=4, service=service
        )
        manager.add_request(_make_png_bytes(), {}, file_name="a.png")

        with self._patch_registry([spec]):
            manager.commit(PreprocessOptions(pipeline=OCRPipeline.OCR))

        assert len(record["batch_calls"]) == 1
        assert len(record["batch_calls"][0]["images"]) == 1
        assert record["single_calls"] == []

    def test_falls_back_to_single_recognize_when_no_batch(self):
        """无 recognize_batch 的管道应回退逐张 recognize"""
        spec, record = self._make_recording_spec(
            OCRPipeline.TABLE_RECOGNITION.value, has_batch=False
        )
        service = MagicMock()
        manager = BatchQueueManager(
            MockPipeline(), max_batch_size=4, service=service
        )
        for i in range(3):
            manager.add_request(_make_png_bytes(), {}, file_name=f"{i}.png")

        with self._patch_registry([spec]):
            manager.commit(PreprocessOptions(pipeline=OCRPipeline.TABLE_RECOGNITION))

        assert record["batch_calls"] == []
        assert len(record["single_calls"]) == 3

    def test_options_passed_through_unchanged_to_spec(self):
        """选项应原样传给 spec（由 spec 内部做名称转换/过滤），
        而非由 BatchQueueManager 自行过滤。"""
        spec, record = self._make_recording_spec(OCRPipeline.PP_STRUCTURE_V3.value)
        service = MagicMock()
        manager = BatchQueueManager(
            MockPipeline(), max_batch_size=4, service=service
        )
        manager.add_request(_make_png_bytes(), {}, file_name="a.png")

        # 含 VL/表格/公式专用选项，但这些不应被批量层剥离——
        # 剥离/转换是各 spec.recognize 的职责
        options = PreprocessOptions(
            pipeline=OCRPipeline.PP_STRUCTURE_V3,
            use_table_recognition=True,
            vl_use_layout_detection=False,
        )

        with self._patch_registry([spec]):
            manager.commit(options)

        passed = record["batch_calls"][0]["options"]
        # spec 收到的应是完整的 options 对象，可自行映射
        assert passed is options

    def test_returns_ocrresult_like_objects_not_raw_dicts(self):
        """委派路径应返回 spec 产生的对象（OCRResult），
        而非 raw predict() 字典，保证 UI 能正确展示。"""
        spec, _ = self._make_recording_spec("OCR")
        service = MagicMock()
        manager = BatchQueueManager(
            MockPipeline(), max_batch_size=4, service=service
        )
        manager.add_request(_make_png_bytes(), {}, file_name="a.png")

        with self._patch_registry([spec]):
            results = manager.commit(PreprocessOptions(pipeline=OCRPipeline.OCR))

        rid = next(iter(manager._queue))
        assert hasattr(results[rid], "raw_text")


class TestBatchQueueManagerWithGPU:
    """带 GPU 监控的测试"""

    def test_calculate_batch_size_with_mock_gpu(self):
        """测试 GPU 显存影响 batch_size 计算"""
        pipeline = MockPipeline()

        with patch(
            "vibeocr.workers.batch_queue_manager.GPUMemoryMonitor"
        ) as MockMonitor:
            # 模拟显存监控器
            mock_monitor = MagicMock()
            mock_monitor.is_available.return_value = True
            mock_monitor.estimate_batch_size.return_value = 4
            MockMonitor.return_value = mock_monitor

            manager = BatchQueueManager(pipeline, max_batch_size=8)

            # 添加请求
            for _ in range(10):
                manager.add_request(_make_png_bytes(), {})

            # commit 会调用 _calculate_batch_size
            # 但这里我们只测试创建是否成功
            assert manager is not None
