"""PaddleX OCR 服务"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
from enum import Enum
from typing import TYPE_CHECKING, Any

from vibeocr.core.pipelines import OCRPipeline
from vibeocr.core.singleton_meta import SingletonMeta
from vibeocr.models.ocr_options import OCROptions
from vibeocr.models.ocr_result import OCRResult, TextBlock

# 重新导出以保持向后兼容性
__all__ = [
    "OCROptions",
    "OCRPipeline",
    "OCRPreset",
    "OCRResult",
    "OCRService",
]

# 跳过模型源网络检测，避免推理时的网络超时开销
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

import re as _re


def _extract_table_html(html_str: str) -> str:
    """从 pred_html 中提取 <table> 标签内容，去除外层 html/body 包装"""
    match = _re.search(
        r"(<table\b.*</table>)", html_str, _re.DOTALL | _re.IGNORECASE
    )
    return match.group(1) if match else html_str


def _html_table_to_markdown(html: str) -> str:
    """将 HTML <table> 转换为 Markdown 表格格式"""
    rows: list[list[str]] = []
    tr_pat = _re.compile(r"<tr[^>]*>(.*?)</tr>", _re.DOTALL | _re.IGNORECASE)
    td_pat = _re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", _re.DOTALL | _re.IGNORECASE)

    for tr_match in tr_pat.finditer(html):
        cells = []
        for td_match in td_pat.finditer(tr_match.group(1)):
            cell_text = _re.sub(r"<[^>]+>", "", td_match.group(1)).strip()
            cell_text = cell_text.replace("|", "\\|")
            cells.append(cell_text)
        if cells:
            rows.append(cells)

    if not rows:
        return ""

    # 对齐列数
    max_cols = max(len(r) for r in rows)
    for r in rows:
        while len(r) < max_cols:
            r.append("")

    # 构建 Markdown 表格
    lines = []
    header = "| " + " | ".join(rows[0]) + " |"
    sep = "| " + " | ".join("---" for _ in range(max_cols)) + " |"
    lines.append(header)
    lines.append(sep)
    for row in rows[1:]:
        lines.append("| " + " | ".join(row) + " |")

    return "\n".join(lines)


def _looks_like_markdown(text: str) -> bool:
    """检测文本是否包含 Markdown 格式标记"""
    if not text:
        return False
    patterns = [
        r"^#{1,6}\s",
        r"\*\*[^*]+\*\*",
        r"__[^_]+__",
        r"^- ",
        r"^\* ",
        r"\|.+\|",
        r"\$\$",
        r"```",
    ]
    return any(_re.search(p, text, _re.MULTILINE) for p in patterns)

# 注意：所有操作在同一线程中执行（CPU 模式）
# 工作线程设计已确保这一点

# 导入模型缓存管理器（轻量级，可以保留）
from vibeocr.model_cache_manager import (
    is_pipeline_cached,
    quick_check_all_models,
)

_logger = logging.getLogger(__name__)

# 类型检查时导入（不影响运行时）
if TYPE_CHECKING:
    from collections.abc import Callable

    import numpy as np
    from PIL import Image


class OCRPreset(Enum):
    """OCR 预设模式（用于通用 OCR 管道的预处理配置）"""

    GENERAL = "general"  # 通用模式：适用于屏幕截图
    SCANNED = "scanned"  # 扫描件模式：适用于扫描文档/拍照

    @property
    def display_name(self) -> str:
        """获取显示名称"""
        names = {
            OCRPreset.GENERAL: "通用",
            OCRPreset.SCANNED: "扫描件",
        }
        return names.get(self, "通用")


class OCRService(metaclass=SingletonMeta):
    """OCR 识别服务 (使用 SingletonMeta 实现线程安全单例)"""

    _pipelines: dict[str, Any] = {}  # 管道缓存：{pipeline_name: pipeline_instance}
    _lock = threading.Lock()
    _initialized = False
    _status_callback: Callable | None = None  # 状态回调函数
    _source_configured = False  # 模型源是否已配置

    # 预加载相关状态
    _preload_progress_callback: Callable[[str, int, int], None] | None = (
        None  # (pipeline_name, current, total)
    )
    _preloaded_pipelines: set[str] = set()  # 已预加载的管道名称
    _is_preloading = False  # 是否正在预加载
    _preload_lock = (
        threading.Lock()
    )  # 预加载专用锁，保护 _preloaded_pipelines 和 _is_preloading

    @classmethod
    def set_status_callback(cls, callback: Callable | None) -> None:
        """设置状态回调函数

        Args:
            callback: 回调函数，接收 (stage, message) 参数
                     例如: ("模型下载", "正在下载 OCR 模型...")
        """
        cls._status_callback = callback

    @classmethod
    def _notify_status(cls, stage: str, message: str) -> None:
        """通知状态变化"""
        if cls._status_callback:
            try:
                cls._status_callback(stage, message)
            except Exception:
                pass  # 忽略回调错误

    @classmethod
    def _ensure_source_configured(cls) -> None:
        """确保模型下载源已配置（延迟调用）"""
        if not cls._source_configured:
            from vibeocr.env_manager import get_project_root
            from vibeocr.network_detector import NetworkDetector

            NetworkDetector(get_project_root()).paddlex_source_env
            cls._source_configured = True

    def __init__(self):
        """初始化 OCR 服务

        使用 SingletonMeta 确保单例，_initialized 标志防止重复初始化。
        """
        if not self._initialized:
            with self._lock:
                if not self._initialized:
                    self._initialized = True

    @classmethod
    def _reset(cls) -> None:
        """重置服务状态

        供 SingletonMeta.reset_instance() 调用，用于测试清理。
        """
        with cls._lock:
            cls._pipelines = {}
            cls._initialized = False
            cls._status_callback = None
            cls._source_configured = False
            cls._preload_progress_callback = None
            cls._preloaded_pipelines = set()
            cls._is_preloading = False

    @classmethod
    def preload_model_cache(cls) -> dict[str, bool]:
        """预加载模型缓存信息，加速后续初始化

        Returns:
            各管道模型就绪状态
        """
        try:
            return quick_check_all_models()
        except Exception as e:
            _logger.warning(f"预加载模型缓存失败: {e}")
            return {}

    @classmethod
    def set_preload_progress_callback(
        cls, callback: Callable[[str, int, int], None] | None
    ) -> None:
        """设置预加载进度回调函数

        Args:
            callback: 回调函数，接收 (pipeline_name, current, total) 参数
                     例如: ("OCR", 1, 3) 表示正在加载第 1 个管道，共 3 个
        """
        cls._preload_progress_callback = callback

    @classmethod
    def is_pipeline_preloaded(cls, pipeline: OCRPipeline) -> bool:
        """检查指定管道是否已预加载

        Args:
            pipeline: 管道类型

        Returns:
            是否已预加载
        """
        with cls._preload_lock:
            return pipeline.value in cls._preloaded_pipelines

    @classmethod
    def get_preloaded_pipelines(cls) -> list[str]:
        """获取已预加载的管道名称列表"""
        with cls._preload_lock:
            return list(cls._preloaded_pipelines)

    @classmethod
    def preload_pipeline(cls, pipeline: OCRPipeline) -> bool:
        """预加载单个管道（同步）

        Args:
            pipeline: 要预加载的管道

        Returns:
            是否成功加载
        """
        pipeline_name = pipeline.value

        # 检查是否已缓存（使用主锁保护 _pipelines）
        with cls._lock:
            if pipeline_name in cls._pipelines:
                with cls._preload_lock:
                    cls._preloaded_pipelines.add(pipeline_name)
                return True

        try:
            _logger.debug(f"[预加载] 开始加载管道: {pipeline.display_name}")
            instance = cls()
            instance.get_pipeline(pipeline)

            # 更新预加载状态（使用预加载锁）
            with cls._preload_lock:
                cls._preloaded_pipelines.add(pipeline_name)

            _logger.debug(f"[预加载] 管道加载完成: {pipeline.display_name}")
            return True
        except Exception as e:
            _logger.error(f"[预加载] 管道加载失败 {pipeline.display_name}: {e}")
            return False

    @classmethod
    def preload_pipelines_sequential(
        cls,
        pipelines: list[OCRPipeline],
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> dict[str, bool]:
        """顺序预加载多个管道

        Args:
            pipelines: 要预加载的管道列表
            progress_callback: 进度回调 (pipeline_name, current, total)

        Returns:
            各管道加载结果 {pipeline_name: success}
        """
        # 检查是否有预加载任务在进行中
        with cls._preload_lock:
            if cls._is_preloading:
                _logger.warning("[预加载] 已有预加载任务在进行中")
                return {}
            cls._is_preloading = True

        results = {}
        total = len(pipelines)

        try:
            for i, pipeline in enumerate(pipelines, 1):
                pipeline_name = pipeline.value
                display_name = pipeline.display_name

                # 通知进度
                if progress_callback:
                    progress_callback(pipeline_name, i, total)
                if cls._preload_progress_callback:
                    cls._preload_progress_callback(pipeline_name, i, total)

                _logger.debug(f"[预加载] ({i}/{total}) 加载 {display_name}...")
                results[pipeline_name] = cls.preload_pipeline(pipeline)

            # 汇总结果
            success_count = sum(1 for v in results.values() if v)
            _logger.info(f"[预加载] 完成: {success_count}/{total} 个管道加载成功")

        finally:
            # 确保重置状态
            with cls._preload_lock:
                cls._is_preloading = False

        return results


    @classmethod
    def warmup_with_test_image(
        cls,
        pipeline: OCRPipeline | None = None,
        progress_callback: Callable[[str, int], None] | None = None,
    ) -> bool:
        """使用测试图片预热 OCR 服务

        通过执行一次虚拟识别来触发模型加载。

        Args:
            pipeline: 要预热的管道，None 表示使用默认 OCR 管道
            progress_callback: 进度回调 (stage, percent)

        Returns:
            预热是否成功
        """
        from vibeocr.utils.warmup_utils import get_warmup_image

        pipeline = pipeline or OCRPipeline.OCR
        pipeline_name = pipeline.value

        try:
            _logger.debug(f"[预热] 开始使用测试图片预热管道: {pipeline_name}")
            if progress_callback:
                progress_callback("准备测试图片", 10)

            # 获取测试图片
            test_image = get_warmup_image()
            if test_image is None:
                _logger.error("[预热] 无法创建预热测试图片，预热中止")
                return False
            _logger.debug(f"[预热] 测试图片大小: {len(test_image)} 字节")

            if progress_callback:
                progress_callback("执行虚拟识别", 50)

            # 创建选项并执行识别
            options = OCROptions(pipeline=pipeline)
            instance = cls()
            instance.recognize(test_image, options)

            if progress_callback:
                progress_callback("预热完成", 100)

            _logger.debug(f"[预热] 管道 {pipeline_name} 预热成功")
            return True

        except Exception as e:
            _logger.error(f"[预热] 管道 {pipeline_name} 预热失败: {e}")
            return False

    @classmethod
    def warmup_pipelines(
        cls,
        pipelines: list[OCRPipeline],
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> dict[str, bool]:
        """使用测试图片预热多个管道

        Args:
            pipelines: 要预热的管道列表
            progress_callback: 进度回调 (pipeline_name, current, total)

        Returns:
            各管道预热结果 {pipeline_name: success}
        """
        results = {}
        total = len(pipelines)

        _logger.debug(f"[预热] 开始批量预热 {total} 个管道")

        for i, pipeline in enumerate(pipelines, 1):
            pipeline_name = pipeline.value

            def make_progress(
                stage: str, percent: int, idx=i, name=pipeline_name, total_count=total
            ):
                if progress_callback:
                    overall_percent = int(((idx - 1) * 100 + percent) / total_count)
                    progress_callback(name, idx, overall_percent)

            _logger.debug(f"[预热] ({i}/{total}) 预热 {pipeline.display_name}...")
            results[pipeline_name] = cls.warmup_with_test_image(pipeline, make_progress)

        success_count = sum(1 for v in results.values() if v)
        _logger.info(f"[预热] 完成: {success_count}/{total} 个管道预热成功")

        return results

    @classmethod
    def preload_in_background(
        cls,
        pipelines: list[OCRPipeline],
        on_complete: Callable[[dict[str, bool]], None] | None = None,
    ) -> threading.Thread:
        """在后台线程中预加载管道（非阻塞）

        Args:
            pipelines: 要预加载的管道列表
            on_complete: 完成回调，接收加载结果字典

        Returns:
            后台线程对象
        """

        def _preload_task():
            try:
                results = cls.preload_pipelines_sequential(pipelines)

                if on_complete:
                    on_complete(results)
            except Exception as e:
                _logger.error(f"[预加载] 后台预加载任务失败: {e}")
                if on_complete:
                    on_complete({})

        thread = threading.Thread(
            target=_preload_task, daemon=True, name="PipelinePreload"
        )
        thread.start()
        return thread

    @staticmethod
    def _get_optimal_cpu_threads() -> int:
        """动态检测 CPU 核心数并返回最优线程数

        物理核心数优先；若无法检测则取逻辑核心数的 1/4（下限 4，上限 16）。
        """
        try:
            import multiprocessing

            logical = multiprocessing.cpu_count() or 4
        except Exception:
            logical = 4
        try:
            import psutil

            physical = psutil.cpu_count(logical=False)
            if physical and physical >= 2:
                return min(max(physical, 4), 16)
        except ImportError:
            pass
        # 无 psutil，取逻辑核心的 1/4
        return min(max(logical // 4, 4), 16)

    @staticmethod
    def _try_create_with_hpip(
        create_pipeline, pipeline_name: str, device: str, pp_option
    ):
        """尝试使用 HPIP（高性能推理插件）创建管道

        HPIP 需要 ultra-infer 包，仅 Linux + Python <= 3.12 可用。
        失败时静默回退到普通推理。
        """
        try:
            from paddlex.utils.deps import is_hpip_available

            if not is_hpip_available():
                _logger.debug("[HPIP] ultra-infer 不可用，跳过")
                return None

            pipeline = create_pipeline(
                pipeline=pipeline_name,
                device=device,
                pp_option=pp_option,
                use_hpip=True,
                hpi_config={"backend": "onnxruntime"},
            )
            _logger.debug("[HPIP] 高性能推理管道创建成功")
            return pipeline
        except Exception as e:
            _logger.debug(f"[HPIP] 创建失败，回退到普通推理: {e}")
            return None

    _cuda_dll_registered = False

    @classmethod
    def _setup_cuda_dll_path(cls) -> None:
        """将所有 nvidia/* 包的 DLL 目录加入 PATH，使 PaddlePaddle 能找到 CUDA 运行时库"""
        if cls._cuda_dll_registered:
            return
        import sys
        if sys.platform != "win32":
            cls._cuda_dll_registered = True
            return
        site_packages = next(p for p in sys.path if "site-packages" in p)
        nvidia_base = os.path.join(site_packages, "nvidia")
        existing = os.environ.get("PATH", "")
        if os.path.isdir(nvidia_base):
            for entry in os.scandir(nvidia_base):
                if not entry.is_dir():
                    continue
                # 扫描 bin/ 和 lib/ 目录（不同 nvidia 包的 DLL 位置不同）
                for subfolder in ("bin", "lib"):
                    sub_dir = os.path.join(entry.path, subfolder)
                    if os.path.isdir(sub_dir) and sub_dir not in existing:
                        existing = sub_dir + ";" + existing
                    # 新版 nvidia 包 (cu13 等) 把 DLL 放在 <subfolder>/<arch>/ 子目录
                    if os.path.isdir(sub_dir):
                        for sub in os.scandir(sub_dir):
                            arch_dir = os.path.join(sub_dir, sub.name)
                            if sub.is_dir() and arch_dir not in existing:
                                existing = arch_dir + ";" + existing
        os.environ["PATH"] = existing
        cls._cuda_dll_registered = True

    @staticmethod
    def _get_device() -> str:
        """根据环境变量和 GPU 可用性检测推理设备"""
        if os.environ.get("VIBEOCR_USE_GPU", "").lower() != "true":
            return "cpu"
        try:
            import paddle
            import paddle.device as paddle_device
            if paddle_device.cuda.device_count() > 0:
                # 驱动可检测到 GPU，但 CUDA 运行时库（cuBLAS 等）可能未安装，
                # 执行一次矩阵乘法验证 cuBLAS 可用性。
                paddle.device.set_device("gpu")
                a = paddle.randn([4, 4])
                _ = paddle.matmul(a, a).numpy()
                _logger.debug("[GPU] CUDA 运行时验证通过，使用 GPU 推理")
                # paddle 导入完成后注册 DLL 目录，供 predict() 内部的 ctypes 使用
                # （必须在 paddle 导入后调用，否则会触发 paddle/libs/nvidia 路径错误）
                OCRService._register_dll_directories()
                return "gpu"
        except Exception as e:
            _logger.warning("[GPU] GPU 可用性验证失败: %s，回退到 CPU", e)
            return "cpu"
        _logger.warning("[GPU] VIBEOCR_USE_GPU=true 但未检测到可用 GPU，回退到 CPU")
        return "cpu"

    @classmethod
    def _register_dll_directories(cls) -> None:
        """通过 os.add_dll_directory() 注册 CUDA DLL 目录

        Python 3.8+ Windows 上 ctypes.CDLL 不再搜索 os.environ["PATH"]，
        必须通过 os.add_dll_directory() 注册。此方法必须在 PaddlePaddle
        导入完成后调用，否则会触发 PaddlePaddle 内部的路径错误。
        """
        if not hasattr(os, "add_dll_directory"):
            return
        import sys
        site_packages = next((p for p in sys.path if "site-packages" in p), None)
        if not site_packages:
            return
        nvidia_base = os.path.join(site_packages, "nvidia")
        if not os.path.isdir(nvidia_base):
            return
        for entry in os.scandir(nvidia_base):
            if not entry.is_dir():
                continue
            for subfolder in ("bin", "lib"):
                sub_dir = os.path.join(entry.path, subfolder)
                if os.path.isdir(sub_dir):
                    with contextlib.suppress(OSError):
                        os.add_dll_directory(sub_dir)
                    for sub in os.scandir(sub_dir):
                        arch_dir = os.path.join(sub_dir, sub.name)
                        if sub.is_dir():
                            with contextlib.suppress(OSError):
                                os.add_dll_directory(arch_dir)

    def _create_pipeline(self, pipeline_name: str, device: str = "cpu") -> Any:
        """创建指定管道"""
        # 延迟导入: 确保模型源已配置（首次使用时才执行网络检测）
        _logger.debug("[_create_pipeline] %s: 开始，配置模型源...", pipeline_name)
        self._ensure_source_configured()

        # 延迟导入: PaddleX（这是启动慢的主要原因，~30s）
        _logger.debug("[_create_pipeline] %s: 导入 PaddleX...", pipeline_name)
        from paddlex import create_pipeline
        from paddlex.inference.utils.pp_option import PaddlePredictorOption

        # 获取管道显示名称
        display_name = pipeline_name
        for p in OCRPipeline:
            if p.value == pipeline_name:
                display_name = p.display_name
                break

        # 检查模型是否已缓存（快速检查，避免重复提示）
        _logger.debug("[_create_pipeline] %s: 检查模型缓存...", pipeline_name)
        models_cached = is_pipeline_cached(pipeline_name)

        if not models_cached:
            _logger.debug(
                "[_create_pipeline] %s: 模型未缓存，需要下载，设备=%s",
                pipeline_name, device,
            )
            self._notify_status(
                "模型初始化",
                f"正在初始化 {display_name} 管道（首次使用需要下载模型）...",
            )
        else:
            _logger.debug(f"管道 {display_name} 模型已存在，直接加载...")

        # 构建 pp_option：动态 CPU 线程数 + 禁用 new IR
        cpu_threads = self._get_optimal_cpu_threads()
        _logger.debug(f"[推理优化] CPU 线程数: {cpu_threads}")

        pp_option = PaddlePredictorOption()
        pp_option.enable_new_ir = False
        pp_option.run_mode = "paddle"
        pp_option.cpu_threads = cpu_threads

        # 尝试 HPIP 加速（需要 ultra-infer）
        _logger.debug(
            "[_create_pipeline] %s: 尝试 HPIP 加速，设备=%s...",
            pipeline_name, device,
        )
        pipeline = self._try_create_with_hpip(
            create_pipeline, pipeline_name, device, pp_option
        )
        if pipeline is not None:
            _logger.debug("管道 %s (HPIP) 初始化于设备: %s", pipeline_name, device)
            if not models_cached:
                self._notify_status("模型初始化", f"{display_name} 管道初始化完成")
            return pipeline

        # 普通推理路径
        _logger.debug(
            "[_create_pipeline] %s: 调用 create_pipeline()，设备=%s ...",
            pipeline_name, device,
        )
        pipeline = create_pipeline(
            pipeline=pipeline_name,
            device=device,
            pp_option=pp_option,
        )

        _logger.debug("管道 %s 初始化于设备: %s (线程: %d)", pipeline_name, device, cpu_threads)

        if not models_cached:
            self._notify_status("模型初始化", f"{display_name} 管道初始化完成")

        return pipeline

    def get_pipeline(self, pipeline: OCRPipeline) -> Any:
        """延迟加载指定管道 (线程安全)"""
        pipeline_name = pipeline.value

        if pipeline_name not in self._pipelines:
            with self._lock:
                if pipeline_name not in self._pipelines:  # 双重检查
                    self._setup_cuda_dll_path()
                    device = self._get_device()
                    _logger.debug(
                        "[get_pipeline] 创建管道 %s，设备=%s，"
                        "已加载管道: %s",
                        pipeline_name, device,
                        list(self._pipelines.keys()),
                    )
                    self._pipelines[pipeline_name] = self._create_pipeline(
                        pipeline_name, device
                    )
                    _logger.debug("[get_pipeline] 管道 %s 创建完成", pipeline_name)
        return self._pipelines[pipeline_name]

    @property
    def pipeline(self) -> Any:
        """延迟加载默认 OCR 流水线 (向后兼容)"""
        return self.get_pipeline(OCRPipeline.OCR)

    def recognize(
        self,
        image: Image.Image | np.ndarray | str | bytes,
        options: OCROptions | None = None,
    ) -> OCRResult:
        """
        对图像执行 OCR 识别

        Args:
            image: PIL Image, numpy 数组, 图像路径, 或图像字节数据
            options: OCR 识别选项

        Returns:
            OCRResult 对象，包含识别结果和置信度信息
        """

        actual_options = options if options is not None else OCROptions()
        _logger.debug(f"[recognize] 开始识别，管道: {actual_options.pipeline.value}")

        # 如果输入是 bytes，转换为 numpy.ndarray（PaddleX 只支持 ndarray 和 str）
        if isinstance(image, bytes):
            import io

            import numpy as np
            from PIL import Image as PILImage

            _logger.debug(
                f"[recognize] 输入是 bytes ({len(image)} 字节)，转换为 numpy.ndarray"
            )
            pil_image = PILImage.open(io.BytesIO(image))
            # 转换为 RGB 模式（确保格式一致）再转为 numpy 数组
            if pil_image.mode != "RGB":
                pil_image = pil_image.convert("RGB")  # type: ignore[assignment]
            image = np.array(pil_image)
            _logger.debug(f"[recognize] 转换完成，数组形状: {image.shape}")

        # 根据管道类型分发
        try:
            if actual_options.pipeline == OCRPipeline.OCR:
                result = self._recognize_ocr(image, actual_options)
            elif actual_options.pipeline == OCRPipeline.TABLE_RECOGNITION:
                result = self._recognize_table(image, actual_options)
            elif actual_options.pipeline == OCRPipeline.FORMULA_RECOGNITION:
                result = self._recognize_formula(image, actual_options)
            else:
                result = self._recognize_ocr(image, actual_options)
            # Normalize bbox from pixel coords to [0-1000]
            if hasattr(image, 'shape') and len(image.shape) >= 2:
                img_h, img_w = image.shape[:2]
            elif hasattr(image, 'size'):
                img_w, img_h = image.size
            else:
                img_w = img_h = 0
            if img_w > 0 and img_h > 0:
                for block in result.text_blocks:
                    if block.bbox:
                        x0, y0, x1, y1 = block.bbox
                        block.bbox = (
                            x0 / img_w * 1000, y0 / img_h * 1000,
                            x1 / img_w * 1000, y1 / img_h * 1000,
                        )

            _logger.debug(f"[recognize] 识别完成，返回 {len(result.raw_text)} 字符")
            return result
        except Exception as e:
            _logger.error(f"[recognize] 识别过程中发生异常: {e}", exc_info=True)
            raise

    def _recognize_ocr(
        self,
        image: Image.Image | np.ndarray | str,
        options: OCROptions,
    ) -> OCRResult:
        """通用 OCR 识别"""
        _logger.debug("[_recognize_ocr] 获取 OCR 管道...")
        pipeline = self.get_pipeline(OCRPipeline.OCR)
        _logger.debug("[_recognize_ocr] 执行 predict...")
        try:
            output = pipeline.predict(
                input=image,
                use_doc_orientation_classify=options.use_doc_orientation_classify,
                use_doc_unwarping=options.use_doc_unwarping,
                use_textline_orientation=options.use_textline_orientation,
            )
            _logger.debug(f"[_recognize_ocr] predict 返回，类型: {type(output)}")
        except Exception as e:
            _logger.error(f"[_recognize_ocr] predict 调用失败: {e}", exc_info=True)
            raise

        _logger.debug("[_recognize_ocr] 开始处理输出...")
        result = self._process_ocr_output_safe(output)
        _logger.debug(f"[_recognize_ocr] 结果处理完成: {len(result.raw_text)} 字符")
        return result

    def _recognize_table(
        self,
        image: Image.Image | np.ndarray | str,
        options: OCROptions,
    ) -> OCRResult:
        """表格识别"""
        pipeline = self.get_pipeline(OCRPipeline.TABLE_RECOGNITION)
        output = pipeline.predict(
            input=image,
            use_doc_orientation_classify=options.use_doc_orientation_classify,
            use_doc_unwarping=options.use_doc_unwarping,
        )

        output_list = list(output)

        text_with_scores: list[tuple[str, float]] = []
        text_blocks: list[TextBlock] = []
        content_list: list[dict[str, Any]] = []

        for res in output_list:
            if hasattr(res, "table_res_list"):
                for table_res in res.table_res_list:
                    cl_idx = len(content_list)
                    pred_html = getattr(table_res, "pred_html", "")
                    content_list.append({
                        "type": "table",
                        "table_body": _extract_table_html(pred_html) if pred_html else "",
                    })
                    if hasattr(table_res, "table_ocr_pred"):
                        ocr_pred = table_res.table_ocr_pred
                        if hasattr(ocr_pred, "rec_texts") and hasattr(
                            ocr_pred, "rec_scores"
                        ):
                            rec_boxes = getattr(ocr_pred, "rec_boxes", None)
                            for i, (text, score) in enumerate(
                                zip(ocr_pred.rec_texts, ocr_pred.rec_scores, strict=False),
                            ):
                                if text:
                                    fs = float(score)
                                    text_with_scores.append((text, fs))
                                    bbox = self._extract_bbox(rec_boxes, i) if rec_boxes is not None else None
                                    text_blocks.append(TextBlock(text=text, score=fs, bbox=bbox, content_index=cl_idx))
            elif isinstance(res, dict):
                table_res_list = res.get("table_res_list", [])
                for table_res in table_res_list:
                    cl_idx = len(content_list)
                    pred_html = table_res.get("pred_html", "")
                    content_list.append({
                        "type": "table",
                        "table_body": _extract_table_html(pred_html) if pred_html else "",
                    })
                    ocr_pred = table_res.get("table_ocr_pred", {})
                    rec_texts = ocr_pred.get("rec_texts", [])
                    rec_scores = ocr_pred.get("rec_scores", [])
                    rec_boxes = ocr_pred.get("rec_boxes")
                    for i, (text, score) in enumerate(zip(rec_texts, rec_scores, strict=False)):
                        if text:
                            fs = float(score)
                            text_with_scores.append((text, fs))
                            bbox = self._extract_bbox(rec_boxes, i) if rec_boxes is not None else None
                            text_blocks.append(TextBlock(text=text, score=fs, bbox=bbox, content_index=cl_idx))

        raw_text = (
            "\n".join(t for t, _ in text_with_scores) if text_with_scores else ""
        )

        # 从 content_list 中收集 HTML 表格并转为 Markdown
        md_parts: list[str] = []
        for cl in content_list:
            table_html = cl.get("table_body", "")
            if table_html:
                md_parts.append(_html_table_to_markdown(table_html))
        markdown_text = "\n\n".join(md_parts)

        return self._build_ocr_result(
            raw_text=raw_text,
            markdown_text=markdown_text,
            html_text="",
            text_with_scores=text_with_scores,
            pipeline_type="table_recognition",
            text_blocks=text_blocks,
            content_list=content_list,
        )

    def _recognize_formula(
        self,
        image: Image.Image | np.ndarray | str,
        options: OCROptions,
    ) -> OCRResult:
        """公式识别"""
        pipeline = self.get_pipeline(OCRPipeline.FORMULA_RECOGNITION)
        output = pipeline.predict(
            input=image,
            use_doc_orientation_classify=options.use_doc_orientation_classify,
            use_doc_unwarping=options.use_doc_unwarping,
        )

        output_list = list(output)

        text_with_scores: list[tuple[str, float]] = []
        markdown_parts: list[str] = []
        text_blocks: list[TextBlock] = []
        content_list: list[dict[str, Any]] = []

        for res in output_list:
            rec_boxes = None
            if hasattr(res, "rec_formula"):
                formula = res.rec_formula
                rec_boxes = getattr(res, "rec_boxes", None)
                if formula:
                    cl_idx = len(content_list)
                    markdown_parts.append(f"$$\n{formula}\n$$")
                    text_with_scores.append((formula, 1.0))
                    bbox = self._extract_bbox(rec_boxes, 0) if rec_boxes is not None else None
                    text_blocks.append(TextBlock(text=formula, score=1.0, bbox=bbox, content_index=cl_idx))
                    content_list.append({"type": "equation", "text": formula})
            elif isinstance(res, dict):
                formula = res.get("rec_formula", "")
                rec_boxes = res.get("rec_boxes")
                if formula:
                    cl_idx = len(content_list)
                    markdown_parts.append(f"$$\n{formula}\n$$")
                    text_with_scores.append((formula, 1.0))
                    bbox = self._extract_bbox(rec_boxes, 0) if rec_boxes is not None else None
                    text_blocks.append(TextBlock(text=formula, score=1.0, bbox=bbox, content_index=cl_idx))
                    content_list.append({"type": "equation", "text": formula})

        markdown_text = "\n\n".join(markdown_parts)
        raw_text = "\n".join(t for t, _ in text_with_scores)

        return self._build_ocr_result(
            raw_text=raw_text,
            markdown_text=markdown_text,
            html_text="",
            text_with_scores=text_with_scores,
            pipeline_type="formula_recognition",
            text_blocks=text_blocks,
            content_list=content_list,
        )

    @staticmethod
    def _extract_bbox(rec_boxes, index: int) -> tuple[float, float, float, float] | None:
        """从 rec_boxes 提取第 index 个文本框的 bbox [x0, y0, x1, y1]

        支持格式:
        - (N, 4): [x0, y0, x1, y1] 轴对齐矩形
        - (N, 4, 2): [[x0,y0], [x1,y1], [x2,y2], [x3,y3]] 四点多边形
        - (N, 2, 2): [[x0,y0], [x1,y1]] 两点矩形
        """
        try:
            box = rec_boxes[index]
            if hasattr(box, "tolist"):
                box = box.tolist()
            if len(box) == 4:
                # (N, 4) 或 (N, 4, 2)
                if isinstance(box[0], (int, float)):
                    return (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
                # (N, 4, 2) 多边形格式: 取外接矩形
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                return (min(xs), min(ys), max(xs), max(ys))
            if len(box) == 2 and len(box[0]) == 2 and len(box[1]) == 2:
                return (float(box[0][0]), float(box[0][1]), float(box[1][0]), float(box[1][1]))
        except (IndexError, TypeError, ValueError):
            pass
        return None

    @staticmethod
    def _consume_generator_safely(output) -> list:
        """安全地消费 generator（禁用 GC 避免 CUDA 内存管理冲突）"""
        import gc

        gc_was_enabled = gc.isenabled()
        gc.disable()
        try:
            output_list = list(output)
            return output_list
        except Exception as e:
            _logger.error(f"[安全消费] 消费 generator 时出错: {e}", exc_info=True)
            return []
        finally:
            if gc_was_enabled:
                gc.enable()

    def _process_ocr_output_safe(self, output) -> OCRResult:
        """从 OCR 输出中提取结果

        先将 generator 完全消费为 list（禁用 GC），然后提取数据。
        """
        _logger.debug("[_process_ocr_output_safe] 开始提取结果...")
        text_with_scores: list[tuple[str, float]] = []
        text_blocks: list[TextBlock] = []

        output_list = self._consume_generator_safely(output)

        result_count = 0
        for res in output_list:
            result_count += 1
            if result_count > 100:  # 防止异常情况
                _logger.warning("[_process_ocr_output_safe] 结果项过多，可能有问题")
                break
            try:
                if hasattr(res, "rec_texts") and hasattr(res, "rec_scores"):
                    rec_boxes = getattr(res, "rec_boxes", None)
                    for i, (text, score) in enumerate(
                        zip(res.rec_texts, res.rec_scores, strict=False)
                    ):
                        if text:
                            fs = float(score)
                            text_with_scores.append((text, fs))
                            bbox = self._extract_bbox(rec_boxes, i) if rec_boxes is not None else None
                            text_blocks.append(TextBlock(text=text, score=fs, bbox=bbox))
                elif hasattr(res, "rec_texts"):
                    rec_boxes = getattr(res, "rec_boxes", None)
                    for i, text in enumerate(res.rec_texts):
                        if text:
                            text_with_scores.append((text, 1.0))
                            bbox = self._extract_bbox(rec_boxes, i) if rec_boxes is not None else None
                            text_blocks.append(TextBlock(text=text, score=1.0, bbox=bbox))
                elif hasattr(res, "ocr_text"):
                    text_with_scores.append((res.ocr_text, 1.0))
                    text_blocks.append(TextBlock(text=res.ocr_text, score=1.0, bbox=None))
                elif isinstance(res, dict):
                    rec_texts = res.get("rec_texts", [])
                    rec_scores = res.get("rec_scores", [])
                    rec_boxes = res.get("rec_boxes")
                    if rec_scores:
                        for i, (text, score) in enumerate(
                            zip(rec_texts, rec_scores, strict=False)
                        ):
                            if text:
                                fs = float(score)
                                text_with_scores.append((text, fs))
                                bbox = self._extract_bbox(rec_boxes, i) if rec_boxes is not None else None
                                text_blocks.append(TextBlock(text=text, score=fs, bbox=bbox))
                    else:
                        for i, text in enumerate(rec_texts):
                            if text:
                                text_with_scores.append((text, 1.0))
                                bbox = self._extract_bbox(rec_boxes, i) if rec_boxes is not None else None
                                text_blocks.append(TextBlock(text=text, score=1.0, bbox=bbox))
            except Exception as e:
                _logger.error(
                    f"[_process_ocr_output_safe] 处理结果项 #{result_count} 时出错: {e}"
                )
                continue

        raw_text = "\n".join(t for t, _ in text_with_scores)
        _logger.debug(
            f"[_process_ocr_output_safe] 处理完成: 共 {result_count} 个结果项, {len(text_with_scores)} 个文本块"
        )

        return self._build_ocr_result(
            raw_text=raw_text,
            text_with_scores=text_with_scores,
            pipeline_type="OCR",
            text_blocks=text_blocks,
        )

    def _build_ocr_result(
        self,
        raw_text: str,
        markdown_text: str = "",
        html_text: str = "",
        text_with_scores: list[tuple[str, float]] | None = None,
        pipeline_type: str = "OCR",
        images: dict[str, Any] | None = None,
        text_blocks: list[TextBlock] | None = None,
        content_list: list[dict[str, Any]] | None = None,
    ) -> OCRResult:
        """构建 OCRResult 对象

        Args:
            raw_text: 纯文本
            markdown_text: Markdown 格式文本
            html_text: HTML 格式文本
            text_with_scores: 文本块及置信度列表
            pipeline_type: 管道类型
            images: 图像字典
            text_blocks: 含坐标的文本块列表
            content_list: 结构化内容列表（含布局信息）

        Returns:
            OCRResult 对象
        """
        if text_with_scores is None:
            text_with_scores = []

        # 计算平均置信度
        avg_score = 0.0
        if text_with_scores:
            avg_score = sum(s for _, s in text_with_scores) / len(text_with_scores)

        # 收集低置信度项（低于 80%）
        low_confidence_items = [
            (text, score) for text, score in text_with_scores if score < 0.80
        ]

        final_html = html_text or raw_text

        return OCRResult(
            raw_text=raw_text,
            markdown_text=markdown_text or raw_text,
            html_text=final_html,
            text_with_scores=text_with_scores,
            avg_score=avg_score,
            low_confidence_items=low_confidence_items,
            pipeline_type=pipeline_type,
            images=images or {},
            text_blocks=text_blocks or [],
            content_list=content_list or [],
        )
