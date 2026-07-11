"""Tests for OCRService."""

import threading
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image

from vibeocr.core.constants import DEFAULT_SHM_SIZE
from vibeocr.models.ocr_result import OCRResult
from vibeocr.services.ocr_service import OCROptions, OCRPipeline, OCRService

# 检查 paddleocr 是否安装（运行时是否可用取决于环境配置）
try:
    import importlib.util

    HAS_PADDLEX = importlib.util.find_spec("paddleocr") is not None
except ImportError:
    HAS_PADDLEX = False

# paddle 与 torch 同进程共存时，两者的 OpenMP/CUDA DLL 冲突会触发 Windows
# 致命异常 0xc0000139 (ENTRYPOINT_NOT_FOUND)，直接杀死 pytest 进程（无法被
# try/except 捕获）。生产代码通过子进程 Worker 隔离 OCR（ocr_worker_process.py），
# 但下面两个测试类在 pytest 主进程内直接访问 service.pipeline / service.recognize，
# 会触发 paddle→modelscope→torch 的 import 链导致崩溃。
# 检测 paddle + modelscope 共存（modelscope 会拉入 torch）时跳过这些测试；
# 用 find_spec('torch') 不可靠——torch 可能经 sys.path 注入而在收集期不可见。
# pipeline 的子进程路径另有集成测试覆盖，跳过主进程路径不丢真实覆盖。
try:
    import importlib.util as _ilu

    _HAS_PADDLE = _ilu.find_spec("paddle") is not None
    _HAS_MODELSCOPE = _ilu.find_spec("modelscope") is not None
    PADDLE_TORCH_CONFLICT = _HAS_PADDLE and _HAS_MODELSCOPE
except ImportError:
    PADDLE_TORCH_CONFLICT = False


class TestOCRServiceSingleton:
    """测试单例模式。"""

    def test_singleton_returns_same_instance(self):
        """多次实例化返回同一对象。"""
        instance1 = OCRService()
        instance2 = OCRService()
        assert instance1 is instance2

    def test_singleton_thread_safety(self):
        """多线程环境下单例仍然唯一。"""
        instances = []

        def create_instance():
            instances.append(OCRService())

        threads = [threading.Thread(target=create_instance) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(inst is instances[0] for inst in instances)


class TestOCRPipeline:
    """测试 OCR 管道枚举。"""

    def test_pipeline_values(self):
        """管道枚举值正确。"""
        assert OCRPipeline.OCR.value == "OCR"
        assert OCRPipeline.PP_STRUCTURE_V3.value == "PP-StructureV3"
        assert OCRPipeline.DOCUMENT_PARSING.value == "MinerU"

    def test_pipeline_display_names(self):
        """管道显示名称正确。"""
        assert OCRPipeline.OCR.display_name == "通用 OCR"
        assert OCRPipeline.PP_STRUCTURE_V3.display_name == "PP-StructureV3"
        assert OCRPipeline.DOCUMENT_PARSING.display_name == "文档M（MineRU）"

    def test_pipeline_descriptions(self):
        """管道描述正确。"""
        assert "文字" in OCRPipeline.OCR.description
        assert "文档结构" in OCRPipeline.PP_STRUCTURE_V3.description
        assert "MineRU" in OCRPipeline.DOCUMENT_PARSING.description


class TestOCROptions:
    """测试 OCR 选项数据类。"""

    def test_default_options(self):
        """默认选项值正确。"""
        options = OCROptions()
        assert options.pipeline == OCRPipeline.OCR
        assert options.use_doc_orientation_classify is True
        assert options.use_doc_unwarping is False
        assert options.use_textline_orientation is False
        assert options.parse_method == "auto"
        assert options.enable_formula is True
        assert options.enable_table is True

    def test_custom_options(self):
        """自定义选项值正确。"""
        options = OCROptions(
            pipeline=OCRPipeline.DOCUMENT_PARSING,
            parse_method="ocr",
            enable_formula=False,
            enable_table=False,
        )
        assert options.pipeline == OCRPipeline.DOCUMENT_PARSING
        assert options.parse_method == "ocr"
        assert options.enable_formula is False
        assert options.enable_table is False


@pytest.mark.skipif(
    not HAS_PADDLEX or PADDLE_TORCH_CONFLICT,
    reason="paddleocr not installed, or paddle+torch 同进程 DLL 冲突",
)
class TestOCRServicePipeline:
    """测试 OCR 产线懒加载。"""

    def test_pipeline_lazy_loading(self):
        """产线仅在首次访问时创建。"""
        service = OCRService()
        OCRService._pipelines = {}
        try:
            pipeline = service.pipeline
        except (OSError, RuntimeError, AttributeError) as e:
            pytest.skip(f"paddleocr runtime unavailable: {e}")

        assert pipeline is not None
        assert service.pipeline is pipeline
        OCRService._pipelines = {}


@pytest.mark.skipif(
    not HAS_PADDLEX or PADDLE_TORCH_CONFLICT,
    reason="paddleocr not installed, or paddle+torch 同进程 DLL 冲突",
)
class TestOCRServiceRecognize:
    """测试 OCR 识别功能。"""

    def test_recognize_pil_image(self, sample_image_with_text_bytes):
        """识别 PIL Image 格式。"""
        import io

        service = OCRService()
        img = Image.open(io.BytesIO(sample_image_with_text_bytes))
        try:
            result = service.recognize(img)
        except (OSError, RuntimeError, AttributeError) as e:
            pytest.skip(f"paddleocr runtime unavailable: {e}")
        assert isinstance(result, OCRResult)
        assert isinstance(result.raw_text, str)
        assert isinstance(result.text_with_scores, list)

    def test_recognize_numpy_array(self, sample_image_with_text_bytes):
        """识别 numpy 数组格式。"""
        import io

        service = OCRService()
        img = Image.open(io.BytesIO(sample_image_with_text_bytes))
        arr = np.array(img)
        try:
            result = service.recognize(arr)
        except (OSError, RuntimeError, AttributeError) as e:
            pytest.skip(f"paddleocr runtime unavailable: {e}")
        assert isinstance(result, OCRResult)
        assert isinstance(result.raw_text, str)
        assert isinstance(result.text_with_scores, list)

    def test_recognize_empty_image_returns_empty_string(self):
        """空白图片返回空字符串。"""
        service = OCRService()
        img = Image.new("RGB", (100, 50), color="white")
        try:
            result = service.recognize(img)
        except (OSError, RuntimeError, AttributeError) as e:
            pytest.skip(f"paddleocr runtime unavailable: {e}")
        assert isinstance(result, OCRResult)
        assert isinstance(result.raw_text, str)
        assert isinstance(result.text_with_scores, list)

    def test_recognize_with_options(self, sample_image_with_text_bytes):
        """测试使用 OCROptions 进行识别。"""
        import io

        service = OCRService()
        img = Image.open(io.BytesIO(sample_image_with_text_bytes))
        options = OCROptions(
            pipeline=OCRPipeline.OCR,
            use_doc_orientation_classify=True,
            use_doc_unwarping=False,
        )
        try:
            result = service.recognize(img, options)
        except (OSError, RuntimeError, AttributeError) as e:
            pytest.skip(f"paddleocr runtime unavailable: {e}")
        assert isinstance(result, OCRResult)
        assert isinstance(result.raw_text, str)
        assert isinstance(result.text_with_scores, list)


class TestMinerURouting:
    """MineRU 管道应直接调用 MinerUService，不走共享内存"""

    def setup_method(self):
        from vibeocr.services.ocr_service_subprocess import OCRServiceSubprocess

        OCRServiceSubprocess._instance = None

    def teardown_method(self):
        from vibeocr.services.ocr_service_subprocess import OCRServiceSubprocess

        if OCRServiceSubprocess._instance is not None:
            OCRServiceSubprocess._instance.shutdown()
            OCRServiceSubprocess._instance = None

    def test_recognize_mineru_calls_service_directly(self):
        # MinerU 依赖 httpx；若当前环境未安装则跳过（生产环境为必需依赖）。
        pytest.importorskip("httpx")
        with patch("vibeocr.services.ocr_service_subprocess.WorkerManager"):
            from vibeocr.services.ocr_service_subprocess import OCRServiceSubprocess

            svc = OCRServiceSubprocess.__new__(OCRServiceSubprocess)
            svc._initialized = True
            svc.max_workers = 1
            svc.use_gpu = False
            svc.shm_size = DEFAULT_SHM_SIZE
            svc.start_timeout = 120.0
            svc._start_progress_callback = None
            svc._paddlex_manager = MagicMock()
            from vibeocr.services.mineru_batch_service import MinerUBatchService

            svc._mineru_batch = MinerUBatchService()

            mock_mineru_result = MagicMock()
            mock_mineru_result.raw_text = "parsed"

            # recognize() 内部为局部导入 MinerUService，需先把子模块注册为
            # vibeocr.services 的属性，否则 mock.patch 在解析目标时会因
            # getattr(vibeocr.services, "mineru_service") 失败而报错。
            import vibeocr.services.mineru_service  # noqa: F401  # pyright: ignore[reportUnusedImport]

            with patch("vibeocr.services.mineru_service.MinerUService") as MockMinerU:
                MockMinerU.return_value.parse.return_value = mock_mineru_result
                from vibeocr.core.pipelines import OCRPipeline
                from vibeocr.models.ocr_options import OCROptions

                options = OCROptions(pipeline=OCRPipeline.DOCUMENT_PARSING)
                result = svc.recognize(b"pdf_data", options)

            MockMinerU.return_value.parse.assert_called_once()
            assert result.raw_text == "parsed"

    def test_recognize_paddlex_uses_worker_manager(self):
        with patch("vibeocr.services.ocr_service_subprocess.WorkerManager"):
            from vibeocr.services.ocr_service_subprocess import OCRServiceSubprocess

            svc = OCRServiceSubprocess.__new__(OCRServiceSubprocess)
            svc._initialized = True
            svc.max_workers = 1
            svc.use_gpu = False
            svc.shm_size = DEFAULT_SHM_SIZE
            svc.start_timeout = 120.0
            svc._start_progress_callback = None
            svc._paddlex_manager = MagicMock()
            from vibeocr.services.mineru_batch_service import MinerUBatchService

            svc._mineru_batch = MinerUBatchService()

            mock_result = MagicMock()
            svc._paddlex_manager.execute.return_value = mock_result

            from vibeocr.core.pipelines import OCRPipeline
            from vibeocr.models.ocr_options import OCROptions

            options = OCROptions(pipeline=OCRPipeline.OCR)
            svc.recognize(b"img_data", options)

            svc._paddlex_manager.execute.assert_called_once()


class TestHtmlTableToMarkdown:
    """测试 _html_table_to_markdown 辅助函数"""

    def test_simple_table(self):
        from vibeocr.services.ocr_service import _html_table_to_markdown

        html = "<table><tr><td>Name</td><td>Age</td></tr><tr><td>Alice</td><td>30</td></tr></table>"
        md = _html_table_to_markdown(html)
        assert "| Name | Age |" in md
        assert "| --- | --- |" in md
        assert "| Alice | 30 |" in md

    def test_th_header(self):
        from vibeocr.services.ocr_service import _html_table_to_markdown

        html = "<table><tr><th>Col1</th><th>Col2</th></tr><tr><td>A</td><td>B</td></tr></table>"
        md = _html_table_to_markdown(html)
        assert "| Col1 | Col2 |" in md
        assert "| A | B |" in md

    def test_pipe_escaping(self):
        from vibeocr.services.ocr_service import _html_table_to_markdown

        html = "<table><tr><td>A|B</td></tr></table>"
        md = _html_table_to_markdown(html)
        assert "| A\\|B |" in md

    def test_empty_html(self):
        from vibeocr.services.ocr_service import _html_table_to_markdown

        assert _html_table_to_markdown("<table></table>") == ""
        assert _html_table_to_markdown("") == ""

    def test_uneven_columns_padded(self):
        from vibeocr.services.ocr_service import _html_table_to_markdown

        html = (
            "<table><tr><td>A</td><td>B</td><td>C</td></tr><tr><td>D</td></tr></table>"
        )
        md = _html_table_to_markdown(html)
        assert "| D |  |  |" in md

    def test_br_in_cell_preserved_as_br(self):
        """单元格内的 <br> 应保留为 markdown 的 <br>，而非被剥成无分隔。

        回归：旧实现用 ``re.sub(r"<[^>]+>", "")`` 剥所有标签，把 ``行1<br>行2``
        压成 ``行1行2``，丢失多行结构。修复后复用 ``_cell_text``（``<br>→\\n``），
        再把 ``\\n`` 转回 ``<br>`` 以符合 GFM 表格单元格语义。
        """
        from vibeocr.services.ocr_service import _html_table_to_markdown

        html = "<table><tr><td>行1<br>行2</td><td>正常</td></tr></table>"
        md = _html_table_to_markdown(html)
        assert "行1<br>行2" in md, f"<br> 多行结构应保留: {md!r}"
        assert "行1行2" not in md, "不应被压成一行"

    def test_entity_decoded_in_cell(self):
        """单元格内的 HTML 实体（&amp; 等）应解码，不残留实体名。

        复用 ``_cell_text`` 会 unescape；旧实现只剥标签不解码实体。
        """
        from vibeocr.services.ocr_service import _html_table_to_markdown

        html = "<table><tr><td>A&amp;B</td></tr></table>"
        md = _html_table_to_markdown(html)
        assert "| A&B |" in md, f"实体应解码: {md!r}"


class TestCacheManagerIntegration:
    """OCRService 与 PipelineCacheManager 集成测试。"""

    def test_ocr_service_has_cache_manager(self):
        """OCRService 实例持有 PipelineCacheManager。"""
        from vibeocr.services.pipeline_cache_manager import PipelineCacheManager

        OCRService._reset()
        svc = OCRService()
        assert isinstance(svc.cache_manager, PipelineCacheManager)

    def test_reset_clears_cache_manager(self):
        """_reset 后 cache_manager 重新创建（last_used 清空）。"""
        OCRService._reset()
        svc = OCRService()
        svc.cache_manager.touch("PP-StructureV3", now=100.0)
        assert svc.cache_manager.get_last_used("PP-StructureV3") == 100.0
        OCRService._reset()
        svc2 = OCRService()
        assert svc2.cache_manager.get_last_used("PP-StructureV3") is None

    def test_release_pipelines_classmethod(self):
        """release_pipelines 类方法可调用（直连模式）。"""
        OCRService._reset()
        svc = OCRService()
        svc._pipelines = {"OCR": object()}
        svc.cache_manager.touch("OCR")
        released = OCRService.release_pipelines(heavy_only=False)
        assert "OCR" in released
        assert len(svc._pipelines) == 0

    def test_set_pipeline_ttl_classmethod(self):
        """set_pipeline_ttl 类方法可调用（直连模式）。"""
        OCRService._reset()
        svc = OCRService()
        assert OCRService.set_pipeline_ttl(600) is True
        assert svc.cache_manager.ttl_seconds == 600
