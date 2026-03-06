"""PaddleX OCR 服务"""

from __future__ import annotations

import logging
import os
import sys
import threading
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from vibeocr.core.pipelines import OCRPipeline  # 从统一位置导入
from vibeocr.core.singleton_meta import SingletonMeta
from vibeocr.models.ocr_options import OCROptions  # 从统一位置导入
from vibeocr.models.ocr_result import OCRResult
from vibeocr.utils.markdown_converter import markdown_to_html

# 重新导出以保持向后兼容性
__all__ = [
    "OCRService",
    "OCROptions",
    "OCRPipeline",
    "OCRResult",
    "OCRPreset",
]

# 禁用 OneDNN 并强制使用 CPU 模式以兼容性
os.environ.setdefault("FLAGS_enable_onednn_backend", "0")
os.environ.setdefault("FLAGS_use_mkldnn", "0")

# 注意：GPU 模式需要确保所有 Paddle 操作在同一线程中执行
# 工作线程设计已确保这一点

# 延迟导入: numpy, paddle, PIL 这些模块在首次使用时才导入
# import numpy as np
# import paddle
# from PIL import Image

# 导入模型缓存管理器（轻量级，可以保留）
from vibeocr.model_cache_manager import (
    is_pipeline_cached,
    quick_check_all_models,
)

# 延迟导入: PaddleX 和模型源设置（这些是启动慢的主要原因）
# setup_paddlex_model_source() 和 create_pipeline 会在首次使用时才调用

_logger = logging.getLogger(__name__)

# 类型检查时导入（不影响运行时）
if TYPE_CHECKING:
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
    _device: str | None = None
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
            from vibeocr.env_manager import setup_paddlex_model_source

            setup_paddlex_model_source()
            cls._source_configured = True

    def __init__(self):
        """初始化 OCR 服务

        使用 SingletonMeta 确保单例，_initialized 标志防止重复初始化。
        """
        if not self._initialized:
            with self._lock:
                if not self._initialized:
                    self._init_gpu()
                    self._initialized = True

    @classmethod
    def _reset(cls) -> None:
        """重置服务状态

        供 SingletonMeta.reset_instance() 调用，用于测试清理。
        """
        with cls._lock:
            cls._pipelines = {}
            cls._device = None
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
            _logger.info(f"[预加载] 开始加载管道: {pipeline.display_name}")
            instance = cls()
            instance.get_pipeline(pipeline)

            # 更新预加载状态（使用预加载锁）
            with cls._preload_lock:
                cls._preloaded_pipelines.add(pipeline_name)

            _logger.info(f"[预加载] 管道加载完成: {pipeline.display_name}")
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

                _logger.info(f"[预加载] ({i}/{total}) 加载 {display_name}...")
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
    def preload_pipelines_parallel(
        cls,
        pipelines: list[OCRPipeline],
        max_workers: int = 2,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> dict[str, bool]:
        """并行预加载多个管道

        Args:
            pipelines: 要预加载的管道列表
            max_workers: 最大并行工作线程数
            progress_callback: 进度回调 (pipeline_name, current, total)

        Returns:
            各管道加载结果 {pipeline_name: success}
        """
        import concurrent.futures

        # 检查是否有预加载任务在进行中
        with cls._preload_lock:
            if cls._is_preloading:
                _logger.warning("[预加载] 已有预加载任务在进行中")
                return {}
            cls._is_preloading = True

        results: dict[str, bool] = {}
        total = len(pipelines)
        completed = 0

        try:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=max_workers
            ) as executor:
                future_to_pipeline = {
                    executor.submit(cls.preload_pipeline, pipeline): pipeline
                    for pipeline in pipelines
                }

                for future in concurrent.futures.as_completed(future_to_pipeline):
                    pipeline = future_to_pipeline[future]
                    pipeline_name = pipeline.value
                    display_name = pipeline.display_name

                    try:
                        success = future.result()
                        results[pipeline_name] = success
                        completed += 1

                        if progress_callback:
                            progress_callback(pipeline_name, completed, total)
                        if cls._preload_progress_callback:
                            cls._preload_progress_callback(
                                pipeline_name, completed, total
                            )

                        status = "成功" if success else "失败"
                        _logger.info(
                            f"[预加载] ({completed}/{total}) {display_name}: {status}"
                        )
                    except Exception as e:
                        results[pipeline_name] = False
                        completed += 1
                        _logger.error(f"[预加载] {display_name} 加载异常: {e}")

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

        通过执行一次虚拟识别来触发模型加载和 CUDA 初始化。
        这是真正让模型进入就绪状态的关键步骤。

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
            _logger.info(f"[预热] 开始使用测试图片预热管道: {pipeline_name}")
            if progress_callback:
                progress_callback("准备测试图片", 10)

            # 获取测试图片
            test_image = get_warmup_image()
            _logger.debug(f"[预热] 测试图片大小: {len(test_image)} 字节")

            if progress_callback:
                progress_callback("执行虚拟识别", 50)

            # 创建选项并执行识别
            options = OCROptions(pipeline=pipeline)
            instance = cls()
            instance.recognize(test_image, options)

            if progress_callback:
                progress_callback("预热完成", 100)

            _logger.info(f"[预热] 管道 {pipeline_name} 预热成功")
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

        _logger.info(f"[预热] 开始批量预热 {total} 个管道")

        for i, pipeline in enumerate(pipelines, 1):
            pipeline_name = pipeline.value

            def make_progress(
                stage: str, percent: int, idx=i, name=pipeline_name, total_count=total
            ):
                if progress_callback:
                    overall_percent = int(((idx - 1) * 100 + percent) / total_count)
                    progress_callback(name, idx, overall_percent)

            _logger.info(f"[预热] ({i}/{total}) 预热 {pipeline.display_name}...")
            results[pipeline_name] = cls.warmup_with_test_image(pipeline, make_progress)

        success_count = sum(1 for v in results.values() if v)
        _logger.info(f"[预热] 完成: {success_count}/{total} 个管道预热成功")

        return results

    @classmethod
    def preload_in_background(
        cls,
        pipelines: list[OCRPipeline],
        parallel: bool = True,
        max_workers: int = 2,
        on_complete: Callable[[dict[str, bool]], None] | None = None,
    ) -> threading.Thread:
        """在后台线程中预加载管道（非阻塞）

        Args:
            pipelines: 要预加载的管道列表
            parallel: 是否并行加载（默认 True）
            max_workers: 并行加载的最大工作线程数
            on_complete: 完成回调，接收加载结果字典

        Returns:
            后台线程对象
        """

        def _preload_task():
            try:
                if parallel:
                    results = cls.preload_pipelines_parallel(pipelines, max_workers)
                else:
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

    def _init_gpu(self) -> None:
        """初始化 GPU 环境并检查可用性"""
        # 延迟导入: paddle（首次使用时才导入）
        import paddle

        try:
            is_compiled_with_cuda = paddle.is_compiled_with_cuda()
            current_device = paddle.device.get_device()

            _logger.info(f"Paddle 编译时包含 CUDA: {is_compiled_with_cuda}")
            _logger.info(f"当前 Paddle 设备: {current_device}")

            if is_compiled_with_cuda:
                # 如果在 Windows 上，尝试设置 CUDA 路径
                if os.name == "nt":
                    self._setup_cuda_paths_windows()

                # 通过 run_check 检查 GPU 是否实际可用
                try:
                    # 仅在尚未确认 GPU 工作的情况下运行检查
                    if "gpu" not in current_device:
                        paddle.utils.run_check()
                        _logger.info("Paddle run_check 通过")
                except Exception as e:
                    _logger.warning(f"Paddle run_check 失败: {e}")

        except Exception as e:
            _logger.error(f"初始化 GPU 环境时出错: {e}")

    def _setup_cuda_paths_windows(self) -> None:
        """尝试在 Windows 上将 CUDA DLL 路径添加到当前进程环境（不影响系统环境）"""
        # Python 环境中 CUDA DLL 的常见位置
        possible_paths = []

        # 1. 当前环境的 Library/bin (conda 风格)
        if sys.prefix:
            possible_paths.append(Path(sys.prefix) / "Library" / "bin")
            possible_paths.append(Path(sys.prefix) / "bin")

        # 2. Site-packages/paddle/libs (如果存在)
        for path in sys.path:
            p = Path(path)
            if p.name == "site-packages":
                possible_paths.append(p / "paddle" / "libs")
                # 同时也检查通过 pip 安装的 nvidia 包
                possible_paths.append(p / "nvidia" / "cudnn" / "bin")
                possible_paths.append(p / "nvidia" / "cublas" / "bin")

        # 3. 如果存在且尚未在 PATH 中，则添加到当前进程的 PATH
        current_path = os.environ.get("PATH", "")
        paths_to_add = []

        for p in possible_paths:
            if p.exists():
                path_str = str(p)
                # 使用 os.add_dll_directory (Python 3.8+ Windows)
                if hasattr(os, "add_dll_directory"):
                    try:
                        os.add_dll_directory(path_str)
                        _logger.info(f"已添加 DLL 目录: {path_str}")
                    except Exception as e:
                        _logger.warning(f"添加 DLL 目录失败: {e}")

                # 同时也更新 PATH，以兼容旧版或依赖 PATH 查找的库
                if path_str not in current_path:
                    paths_to_add.append(path_str)

        if paths_to_add:
            _logger.info(f"临时添加 CUDA 路径到当前进程环境: {paths_to_add}")
            os.environ["PATH"] = os.pathsep.join([*paths_to_add, current_path])

            # 更新路径后重新检查设备（可能需要重启或重新加载库，但值得一试）
            try:
                import paddle

                if "gpu" not in paddle.device.get_device():
                    # 如果可能，强制重新检查？ paddle 通常会缓存设备信息。
                    pass
            except Exception:
                pass

    def _create_pipeline(self, pipeline_name: str, device: str) -> Any:
        """创建指定管道"""
        # 延迟导入: 确保模型源已配置（首次使用时才执行网络检测）
        self._ensure_source_configured()

        # 延迟导入: PaddleX（这是启动慢的主要原因，~30s）
        from paddlex import create_pipeline

        # 获取管道显示名称
        display_name = pipeline_name
        for p in OCRPipeline:
            if p.value == pipeline_name:
                display_name = p.display_name
                break

        # 检查模型是否已缓存（快速检查，避免重复提示）
        models_cached = is_pipeline_cached(pipeline_name)

        if not models_cached:
            # 模型未缓存，需要下载，显示初始化提示
            self._notify_status(
                "模型初始化",
                f"正在初始化 {display_name} 管道（首次使用需要下载模型）...",
            )
        else:
            # 模型已缓存，只显示简洁的加载信息
            _logger.info(f"管道 {display_name} 模型已存在，直接加载...")
            # 可以选择性地通知，或者完全跳过以减少干扰
            # self._notify_status("模型加载", f"正在加载 {display_name}...")

        pipeline = create_pipeline(
            pipeline=pipeline_name,
            device=device,
        )
        _logger.info("管道 %s 初始化于设备: %s", pipeline_name, device)

        # 通知初始化完成
        if not models_cached:
            self._notify_status("模型初始化", f"{display_name} 管道初始化完成")

        return pipeline

    def _is_gpu_error(self, error: Exception) -> bool:
        """检查错误是否与 GPU 相关"""
        err_str = str(error).lower()
        return any(keyword in err_str for keyword in ["cudnn", "cuda", "gpu", "cudart"])

    def get_pipeline(self, pipeline: OCRPipeline) -> Any:
        """延迟加载指定管道 (线程安全，如果 GPU 不可用自动回退到 CPU)"""
        pipeline_name = pipeline.value

        if pipeline_name not in self._pipelines:
            with self._lock:
                if pipeline_name not in self._pipelines:  # 双重检查
                    # 尝试优先使用 GPU，失败则回退到 CPU
                    for device in ["gpu:0", "cpu"]:
                        try:
                            self._pipelines[pipeline_name] = self._create_pipeline(
                                pipeline_name, device
                            )
                            if self._device is None:
                                self._device = device
                            break
                        except RuntimeError as e:
                            if self._is_gpu_error(e) and "gpu" in device.lower():
                                _logger.warning("GPU 不可用，回退到 CPU: %s", e)
                                continue
                            raise
                    else:
                        raise RuntimeError(
                            f"无法在任何设备上初始化管道 {pipeline_name}"
                        )
        return self._pipelines[pipeline_name]

    @property
    def pipeline(self) -> Any:
        """延迟加载默认 OCR 流水线 (向后兼容)"""
        return self.get_pipeline(OCRPipeline.OCR)

    def _reset_pipeline_to_cpu(self, pipeline_name: str = "OCR") -> None:
        """重置指定管道到 CPU 模式"""
        with self._lock:
            _logger.warning("由于 GPU 错误，正在重置管道 %s 到 CPU 模式", pipeline_name)
            self._pipelines[pipeline_name] = self._create_pipeline(pipeline_name, "cpu")
            self._device = "cpu"

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
        _logger.info(f"[recognize] 开始识别，管道: {actual_options.pipeline.value}")

        # 如果输入是 bytes，转换为 numpy.ndarray（PaddleX 只支持 ndarray 和 str）
        if isinstance(image, bytes):
            import io

            import numpy as np
            from PIL import Image as PILImage

            _logger.info(
                f"[recognize] 输入是 bytes ({len(image)} 字节)，转换为 numpy.ndarray"
            )
            pil_image = PILImage.open(io.BytesIO(image))
            # 转换为 RGB 模式（确保格式一致）再转为 numpy 数组
            if pil_image.mode != "RGB":
                pil_image = pil_image.convert("RGB")
            image = np.array(pil_image)
            _logger.info(f"[recognize] 转换完成，数组形状: {image.shape}")

        # 根据管道类型分发
        try:
            if actual_options.pipeline == OCRPipeline.OCR:
                result = self._recognize_ocr(image, actual_options)
            elif actual_options.pipeline == OCRPipeline.TABLE_RECOGNITION:
                result = self._recognize_table(image, actual_options)
            elif actual_options.pipeline == OCRPipeline.FORMULA_RECOGNITION:
                result = self._recognize_formula(image, actual_options)
            elif actual_options.pipeline == OCRPipeline.PP_STRUCTURE_V3:
                result = self._recognize_structure(image, actual_options)
            elif actual_options.pipeline == OCRPipeline.PADDLEOCR_VL:
                result = self._recognize_paddleocr_vl(image, actual_options)
            elif actual_options.pipeline == OCRPipeline.CHATOCRV4:
                result = self._recognize_chatocrv4(image, actual_options)
            elif actual_options.pipeline == OCRPipeline.DOC_UNDERSTANDING:
                result = self._recognize_doc_understanding(image, actual_options)
            else:
                result = self._recognize_ocr(image, actual_options)
            _logger.info(f"[recognize] 识别完成，返回 {len(result.raw_text)} 字符")
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

        def _do_recognize(img: Image.Image | np.ndarray | str) -> OCRResult:
            _logger.info("[_recognize_ocr] 获取 OCR 管道...")
            pipeline = self.get_pipeline(OCRPipeline.OCR)
            _logger.info("[_recognize_ocr] 执行 predict...")
            try:
                # 调用 predict
                output = pipeline.predict(
                    input=img,
                    use_doc_orientation_classify=options.use_doc_orientation_classify,
                    use_doc_unwarping=options.use_doc_unwarping,
                    use_textline_orientation=options.use_textline_orientation,
                )
                _logger.info(f"[_recognize_ocr] predict 返回，类型: {type(output)}")
            except Exception as e:
                _logger.error(f"[_recognize_ocr] predict 调用失败: {e}", exc_info=True)
                raise

            # 安全地处理输出 - 使用安全消费方法（内部会禁用 GC）
            _logger.info("[_recognize_ocr] 开始处理输出...")
            result = self._process_ocr_output_safe(output)
            _logger.info(f"[_recognize_ocr] 结果处理完成: {len(result.raw_text)} 字符")
            return result

        try:
            return _do_recognize(image)
        except RuntimeError as e:
            if self._is_gpu_error(e) and self._device != "cpu":
                _logger.warning("预测过程中发生 GPU 错误，回退到 CPU: %s", e)
                self._reset_pipeline_to_cpu("OCR")
                return _do_recognize(image)
            raise

    def _recognize_table(
        self,
        image: Image.Image | np.ndarray | str,
        options: OCROptions,
    ) -> OCRResult:
        """表格识别"""

        def _do_recognize(img: Image.Image | np.ndarray | str) -> OCRResult:
            pipeline = self.get_pipeline(OCRPipeline.TABLE_RECOGNITION)
            output = pipeline.predict(
                input=img,
                use_doc_orientation_classify=options.use_doc_orientation_classify,
                use_doc_unwarping=options.use_doc_unwarping,
                use_layout_detection=options.vl_use_layout_detection,
            )

            # 确保 GPU 操作完成后再处理结果 - 使用安全消费方法
            output_list = self._consume_generator_safely(output)

            text_with_scores: list[tuple[str, float]] = []
            html_tables: list[str] = []

            for res in output_list:
                # 提取表格 HTML
                if hasattr(res, "table_res_list"):
                    for table_res in res.table_res_list:
                        if hasattr(table_res, "pred_html"):
                            html_tables.append(table_res.pred_html)
                        # 提取表格中的文本
                        if hasattr(table_res, "table_ocr_pred"):
                            ocr_pred = table_res.table_ocr_pred
                            if hasattr(ocr_pred, "rec_texts") and hasattr(
                                ocr_pred, "rec_scores"
                            ):
                                for text, score in zip(
                                    ocr_pred.rec_texts, ocr_pred.rec_scores
                                ):
                                    if text:
                                        text_with_scores.append((text, float(score)))
                elif isinstance(res, dict):
                    table_res_list = res.get("table_res_list", [])
                    for table_res in table_res_list:
                        html_tables.append(table_res.get("pred_html", ""))
                        ocr_pred = table_res.get("table_ocr_pred", {})
                        rec_texts = ocr_pred.get("rec_texts", [])
                        rec_scores = ocr_pred.get("rec_scores", [])
                        for text, score in zip(rec_texts, rec_scores):
                            if text:
                                text_with_scores.append((text, float(score)))

            # 组合结果：HTML 表格 + 文本
            html_text = "\n\n".join(html_tables) if html_tables else ""
            raw_text = (
                "\n".join(t for t, _ in text_with_scores) if text_with_scores else ""
            )

            return self._build_ocr_result(
                raw_text=raw_text,
                html_text=html_text,
                text_with_scores=text_with_scores,
                pipeline_type="table_recognition",
            )

        try:
            return _do_recognize(image)
        except RuntimeError as e:
            if self._is_gpu_error(e) and self._device != "cpu":
                _logger.warning("预测过程中发生 GPU 错误，回退到 CPU: %s", e)
                self._reset_pipeline_to_cpu("table_recognition")
                return _do_recognize(image)
            raise

    def _recognize_formula(
        self,
        image: Image.Image | np.ndarray | str,
        options: OCROptions,
    ) -> OCRResult:
        """公式识别"""

        def _do_recognize(img: Image.Image | np.ndarray | str) -> OCRResult:
            pipeline = self.get_pipeline(OCRPipeline.FORMULA_RECOGNITION)
            output = pipeline.predict(
                input=img,
                use_doc_orientation_classify=options.use_doc_orientation_classify,
                use_doc_unwarping=options.use_doc_unwarping,
                use_layout_detection=options.vl_use_layout_detection,
            )

            # 确保 GPU 操作完成后再处理结果 - 使用安全消费方法
            output_list = self._consume_generator_safely(output)

            text_with_scores: list[tuple[str, float]] = []
            markdown_parts: list[str] = []

            for res in output_list:
                # 提取公式 LaTeX 代码
                if hasattr(res, "rec_formula"):
                    formula = res.rec_formula
                    if formula:
                        markdown_parts.append(f"$$\n{formula}\n$$")
                        text_with_scores.append((formula, 1.0))
                elif isinstance(res, dict):
                    formula = res.get("rec_formula", "")
                    if formula:
                        markdown_parts.append(f"$$\n{formula}\n$$")
                        text_with_scores.append((formula, 1.0))

            markdown_text = "\n\n".join(markdown_parts)
            raw_text = "\n".join(t for t, _ in text_with_scores)
            html_text = markdown_to_html(markdown_text)

            return self._build_ocr_result(
                raw_text=raw_text,
                markdown_text=markdown_text,
                html_text=html_text,
                text_with_scores=text_with_scores,
                pipeline_type="formula_recognition",
            )

        try:
            return _do_recognize(image)
        except RuntimeError as e:
            if self._is_gpu_error(e) and self._device != "cpu":
                _logger.warning("预测过程中发生 GPU 错误，回退到 CPU: %s", e)
                self._reset_pipeline_to_cpu("formula_recognition")
                return _do_recognize(image)
            raise

    def _recognize_structure(
        self,
        image: Image.Image | np.ndarray | str,
        options: OCROptions,
    ) -> OCRResult:
        """版面解析（PP-StructureV3）"""

        def _do_recognize(img: Image.Image | np.ndarray | str) -> OCRResult:
            pipeline = self.get_pipeline(OCRPipeline.PP_STRUCTURE_V3)
            output = pipeline.predict(
                input=img,
                use_doc_orientation_classify=options.use_doc_orientation_classify,
                use_doc_unwarping=options.use_doc_unwarping,
                use_table_recognition=options.use_table_recognition,
                use_formula_recognition=options.use_formula_recognition,
                use_seal_recognition=options.use_seal_recognition,
                use_chart_recognition=options.use_chart_recognition,
            )

            # 确保 GPU 操作完成后再处理结果 - 使用安全消费方法
            output_list = self._consume_generator_safely(output)

            text_with_scores: list[tuple[str, float]] = []
            markdown_parts: list[str] = []
            images: dict[str, Any] = {}

            for res in output_list:
                # 提取 Markdown 结果（如果有）
                # 注意：res.markdown 返回的是字典，包含 markdown_texts 等键
                if hasattr(res, "markdown"):
                    markdown_data = res.markdown
                    if isinstance(markdown_data, dict):
                        # 提取 markdown_texts 字符串
                        markdown_text = markdown_data.get("markdown_texts", "")
                        if markdown_text:
                            markdown_parts.append(markdown_text)
                        # 提取图像字典
                        if "markdown_images" in markdown_data:
                            images.update(markdown_data["markdown_images"])
                    elif isinstance(markdown_data, str):
                        markdown_parts.append(markdown_data)

                # 提取 OCR 文本和置信度
                if hasattr(res, "rec_texts") and hasattr(res, "rec_scores"):
                    for text, score in zip(res.rec_texts, res.rec_scores):
                        if text:
                            text_with_scores.append((text, float(score)))

                # 提取表格 HTML（转换为 Markdown 表格）
                if hasattr(res, "table_res_list"):
                    for table_res in res.table_res_list:
                        if hasattr(table_res, "pred_html"):
                            html_table = table_res.pred_html
                            markdown_parts.append(html_table)
                        # 提取表格 OCR 的置信度
                        if hasattr(table_res, "table_ocr_pred"):
                            ocr_pred = table_res.table_ocr_pred
                            if hasattr(ocr_pred, "rec_texts") and hasattr(
                                ocr_pred, "rec_scores"
                            ):
                                for text, score in zip(
                                    ocr_pred.rec_texts, ocr_pred.rec_scores
                                ):
                                    if text:
                                        text_with_scores.append((text, float(score)))

                # 提取公式 LaTeX
                if hasattr(res, "formula_res_list"):
                    for formula_res in res.formula_res_list:
                        if hasattr(formula_res, "rec_formula"):
                            latex = formula_res.rec_formula
                            if latex:
                                markdown_parts.append(f"$$\n{latex}\n$$")
                                text_with_scores.append((latex, 1.0))

                # 字典格式处理
                if isinstance(res, dict):
                    if "markdown" in res:
                        markdown_data = res["markdown"]
                        if isinstance(markdown_data, dict):
                            markdown_text = markdown_data.get("markdown_texts", "")
                            if markdown_text:
                                markdown_parts.append(markdown_text)
                            if "markdown_images" in markdown_data:
                                images.update(markdown_data["markdown_images"])
                        elif isinstance(markdown_data, str):
                            markdown_parts.append(markdown_data)

                    # 提取 OCR 文本
                    rec_texts = res.get("rec_texts", [])
                    rec_scores = res.get("rec_scores", [])
                    for text, score in zip(rec_texts, rec_scores):
                        if text:
                            text_with_scores.append((text, float(score)))

                    # 提取表格结果
                    for table_res in res.get("table_res_list", []):
                        if "pred_html" in table_res:
                            markdown_parts.append(table_res["pred_html"])
                        ocr_pred = table_res.get("table_ocr_pred", {})
                        for text, score in zip(
                            ocr_pred.get("rec_texts", []),
                            ocr_pred.get("rec_scores", []),
                        ):
                            if text:
                                text_with_scores.append((text, float(score)))

                    # 提取公式结果
                    for formula_res in res.get("formula_res_list", []):
                        latex = formula_res.get("rec_formula", "")
                        if latex:
                            markdown_parts.append(f"$$\n{latex}\n$$")
                            text_with_scores.append((latex, 1.0))

            # 组合 Markdown 文本
            markdown_text = "\n\n".join(markdown_parts) if markdown_parts else ""

            # 转换 Markdown 为 HTML
            html_text = markdown_to_html(markdown_text) if markdown_text else ""

            # 生成纯文本
            raw_text = (
                "\n".join(t for t, _ in text_with_scores) if text_with_scores else ""
            )

            return self._build_ocr_result(
                raw_text=raw_text,
                markdown_text=markdown_text,
                html_text=html_text,
                text_with_scores=text_with_scores,
                pipeline_type="PP-StructureV3",
                images=images,
            )

        try:
            return _do_recognize(image)
        except RuntimeError as e:
            if self._is_gpu_error(e) and self._device != "cpu":
                _logger.warning("预测过程中发生 GPU 错误，回退到 CPU: %s", e)
                self._reset_pipeline_to_cpu("PP-StructureV3")
                return _do_recognize(image)
            raise

    def _recognize_paddleocr_vl(
        self,
        image: Image.Image | np.ndarray | str,
        options: OCROptions,
    ) -> OCRResult:
        """PaddleOCR-VL 多模态文档解析

        PaddleOCR-VL 是一款先进、高效的文档解析模型，专为文档中的元素识别设计。
        支持 109 种语言，能识别复杂元素（文本、表格、公式、图表、印章等）。
        """

        def _do_recognize(img) -> OCRResult:
            pipeline = self.get_pipeline(OCRPipeline.PADDLEOCR_VL)

            # 构建 predict 参数
            predict_kwargs = {
                "input": img,
                "use_doc_orientation_classify": options.use_doc_orientation_classify,
                "use_doc_unwarping": options.use_doc_unwarping,
                "use_layout_detection": options.vl_use_layout_detection,
                "use_chart_recognition": options.use_chart_recognition,
                "use_seal_recognition": options.vl_use_seal_recognition,
                "use_ocr_for_image_block": options.vl_use_ocr_for_image_block,
                "format_block_content": options.vl_format_block_content,
            }

            # 添加 VLM 采样参数（如果设置了非零值）
            if options.vl_temperature > 0:
                predict_kwargs["temperature"] = options.vl_temperature
            if options.vl_top_p > 0:
                predict_kwargs["top_p"] = options.vl_top_p
            if options.vl_max_pixels > 0:
                predict_kwargs["max_pixels"] = options.vl_max_pixels
            if options.vl_min_pixels > 0:
                predict_kwargs["min_pixels"] = options.vl_min_pixels

            output = pipeline.predict(**predict_kwargs)

            # 确保 GPU 操作完成后再处理结果
            output_list = self._consume_generator_safely(output)

            text_with_scores: list[tuple[str, float]] = []
            markdown_parts: list[str] = []
            images: dict[str, Any] = {}

            for res in output_list:
                # 提取 Markdown 结果
                if hasattr(res, "markdown"):
                    markdown_data = res.markdown
                    if isinstance(markdown_data, dict):
                        markdown_text = markdown_data.get("markdown_texts", "")
                        if markdown_text:
                            markdown_parts.append(markdown_text)
                        if "markdown_images" in markdown_data:
                            images.update(markdown_data["markdown_images"])
                    elif isinstance(markdown_data, str):
                        markdown_parts.append(markdown_data)

                # 提取解析结果列表中的内容
                if hasattr(res, "parsing_res_list"):
                    for block in res.parsing_res_list:
                        if hasattr(block, "block_content"):
                            content = block.block_content
                            if content:
                                text_with_scores.append((content, 1.0))
                        # 提取 block_label 用于调试
                        if hasattr(block, "block_label"):
                            _logger.debug(f"PaddleOCR-VL block: {block.block_label}")

                # 字典格式处理
                if isinstance(res, dict):
                    if "markdown" in res:
                        markdown_data = res["markdown"]
                        if isinstance(markdown_data, dict):
                            markdown_text = markdown_data.get("markdown_texts", "")
                            if markdown_text:
                                markdown_parts.append(markdown_text)
                            if "markdown_images" in markdown_data:
                                images.update(markdown_data["markdown_images"])
                        elif isinstance(markdown_data, str):
                            markdown_parts.append(markdown_data)

                    # 提取 parsing_res_list
                    for block in res.get("parsing_res_list", []):
                        content = block.get("block_content", "")
                        if content:
                            text_with_scores.append((content, 1.0))

            # 组合 Markdown 文本
            markdown_text = "\n\n".join(markdown_parts) if markdown_parts else ""

            # 转换 Markdown 为 HTML
            html_text = markdown_to_html(markdown_text) if markdown_text else ""

            # 生成纯文本
            raw_text = (
                "\n".join(t for t, _ in text_with_scores) if text_with_scores else ""
            )

            return self._build_ocr_result(
                raw_text=raw_text,
                markdown_text=markdown_text,
                html_text=html_text,
                text_with_scores=text_with_scores,
                pipeline_type="PaddleOCR-VL",
                images=images,
            )

        try:
            return _do_recognize(image)
        except RuntimeError as e:
            if self._is_gpu_error(e) and self._device != "cpu":
                _logger.warning("预测过程中发生 GPU 错误，回退到 CPU: %s", e)
                self._reset_pipeline_to_cpu("PaddleOCR-VL")
                return _do_recognize(image)
            raise

    def _sync_cuda_if_available(self) -> None:
        """如果使用 GPU，同步 CUDA 操作以确保完成"""
        if self._device and "gpu" in self._device:
            try:
                import paddle

                # 使用新的 API，避免 deprecation warning
                paddle.device.synchronize()
                _logger.debug("[CUDA] 同步完成")
            except Exception as e:
                _logger.warning(f"[CUDA] 同步失败（可能在 CPU 模式）: {e}")

    def _consume_generator_safely(self, output) -> list:
        """安全地消费 generator

        关键：在消费 generator 时禁用 Python GC，
        避免 GC 与 CUDA 内存管理冲突导致堆损坏。
        """
        import gc

        # 同步确保 GPU 操作完成
        self._sync_cuda_if_available()

        # 禁用 GC 以避免堆损坏
        gc_was_enabled = gc.isenabled()
        gc.disable()

        try:
            _logger.debug("[安全消费] 开始消费 generator...(GC 已禁用)")
            output_list = list(output)
            _logger.debug(f"[安全消费] 获取到 {len(output_list)} 个结果项")

            # 再次同步确保数据完全就绪
            self._sync_cuda_if_available()

            return output_list
        except Exception as e:
            _logger.error(f"[安全消费] 消费 generator 时出错: {e}", exc_info=True)
            return []
        finally:
            # 恢复 GC 状态
            if gc_was_enabled:
                gc.enable()
            _logger.debug("[安全消费] GC 已恢复")

    def _process_ocr_output_safe(self, output) -> OCRResult:
        """从 OCR 输出中提取结果（安全版本）

        关键：先将 generator 完全消费为 list，确保所有 GPU 计算在当前线程完成，
        然后再提取数据。这避免了 generator 跨线程访问 GPU 资源导致的崩溃。
        """
        _logger.info("[_process_ocr_output_safe] 开始提取结果...")
        text_with_scores: list[tuple[str, float]] = []

        # 关键修复：使用安全的 generator 消费方法（禁用 GC）
        output_list = self._consume_generator_safely(output)

        # 处理结果列表（此时数据已在 CPU 内存中，安全访问）
        result_count = 0
        for res in output_list:
            result_count += 1
            if result_count > 100:  # 防止异常情况
                _logger.warning("[_process_ocr_output_safe] 结果项过多，可能有问题")
                break
            try:
                if hasattr(res, "rec_texts") and hasattr(res, "rec_scores"):
                    for text, score in zip(res.rec_texts, res.rec_scores):
                        if text:  # 跳过空文本
                            text_with_scores.append((text, float(score)))
                elif hasattr(res, "rec_texts"):
                    # 没有置信度信息时，使用默认值
                    for text in res.rec_texts:
                        if text:
                            text_with_scores.append((text, 1.0))
                elif hasattr(res, "ocr_text"):
                    text_with_scores.append((res.ocr_text, 1.0))
                elif isinstance(res, dict):
                    rec_texts = res.get("rec_texts", [])
                    rec_scores = res.get("rec_scores", [])
                    if rec_scores:
                        for text, score in zip(rec_texts, rec_scores):
                            if text:
                                text_with_scores.append((text, float(score)))
                    else:
                        for text in rec_texts:
                            if text:
                                text_with_scores.append((text, 1.0))
            except Exception as e:
                _logger.error(
                    f"[_process_ocr_output_safe] 处理结果项 #{result_count} 时出错: {e}"
                )
                continue

        raw_text = "\n".join(t for t, _ in text_with_scores)
        _logger.info(
            f"[_process_ocr_output_safe] 处理完成: 共 {result_count} 个结果项, {len(text_with_scores)} 个文本块"
        )

        return self._build_ocr_result(
            raw_text=raw_text,
            text_with_scores=text_with_scores,
            pipeline_type="OCR",
        )

    def _build_ocr_result(
        self,
        raw_text: str,
        markdown_text: str = "",
        html_text: str = "",
        text_with_scores: list[tuple[str, float]] | None = None,
        pipeline_type: str = "OCR",
        images: dict[str, Any] | None = None,
    ) -> OCRResult:
        """构建 OCRResult 对象

        Args:
            raw_text: 纯文本
            markdown_text: Markdown 格式文本
            html_text: HTML 格式文本
            text_with_scores: 文本块及置信度列表
            pipeline_type: 管道类型
            images: 图像字典

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

        return OCRResult(
            raw_text=raw_text,
            markdown_text=markdown_text or raw_text,
            html_text=html_text or raw_text,
            text_with_scores=text_with_scores,
            avg_score=avg_score,
            low_confidence_items=low_confidence_items,
            pipeline_type=pipeline_type,
            images=images or {},
        )

    def _recognize_chatocrv4(
        self,
        image: Image.Image | np.ndarray | str,
        options: OCROptions,
    ) -> OCRResult:
        """PP-ChatOCRv4 文档场景信息抽取

        PP-ChatOCRv4 是结合 LLM 和 OCR 技术的文档场景信息抽取模型。
        """

        def _do_recognize(img) -> OCRResult:
            pipeline = self.get_pipeline(OCRPipeline.CHATOCRV4)

            # 构建 predict 参数
            predict_kwargs = {
                "input": img,
                "use_doc_orientation_classify": options.use_doc_orientation_classify,
                "use_doc_unwarping": options.use_doc_unwarping,
            }

            output = pipeline.predict(**predict_kwargs)

            # 确保 GPU 操作完成后再处理结果
            output_list = self._consume_generator_safely(output)

            text_with_scores: list[tuple[str, float]] = []

            for res in output_list:
                # 提取识别结果
                if hasattr(res, "rec_texts") and hasattr(res, "rec_scores"):
                    for text, score in zip(res.rec_texts, res.rec_scores):
                        if text:
                            text_with_scores.append((text, float(score)))
                elif hasattr(res, "ocr_text"):
                    text_with_scores.append((res.ocr_text, 1.0))
                # 字典格式处理
                elif isinstance(res, dict):
                    rec_texts = res.get("rec_texts", [])
                    rec_scores = res.get("rec_scores", [])
                    if rec_scores:
                        for text, score in zip(rec_texts, rec_scores):
                            if text:
                                text_with_scores.append((text, float(score)))
                    else:
                        for text in rec_texts:
                            if text:
                                text_with_scores.append((text, 1.0))

            # 生成文本
            raw_text = (
                "\n".join(t for t, _ in text_with_scores) if text_with_scores else ""
            )

            return self._build_ocr_result(
                raw_text=raw_text,
                text_with_scores=text_with_scores,
                pipeline_type="PP-ChatOCRv4",
            )

        try:
            return _do_recognize(image)
        except RuntimeError as e:
            if self._is_gpu_error(e) and self._device != "cpu":
                _logger.warning("预测过程中发生 GPU 错误，回退到 CPU: %s", e)
                self._reset_pipeline_to_cpu("PP-ChatOCRv4")
                return _do_recognize(image)
            raise

    def _recognize_doc_understanding(
        self,
        image: Image.Image | np.ndarray | str,
        options: OCROptions,
    ) -> OCRResult:
        """文档理解管道（VLM）

        基于视觉-语言模型（VLM）的文档问答。
        支持 PP-DocBee 系列模型。
        """
        from vibeocr.core.pipelines import (
            DEFAULT_DOC_UNDERSTANDING_MODEL,
            DOC_UNDERSTANDING_MODELS,
        )

        def _do_recognize(img) -> OCRResult:
            pipeline = self.get_pipeline(OCRPipeline.DOC_UNDERSTANDING)

            # 验证模型名称
            model_name = options.doc_understanding_model
            if model_name not in DOC_UNDERSTANDING_MODELS:
                _logger.warning(
                    f"模型 {model_name} 不在支持列表中，使用默认模型: {DEFAULT_DOC_UNDERSTANDING_MODEL}"
                )
                model_name = DEFAULT_DOC_UNDERSTANDING_MODEL

            # 构建 predict 参数
            predict_kwargs = {
                "input": img,
                "model": model_name,
            }

            # 添加 VLM 采样参数（如果设置了非零值）
            if options.vl_temperature > 0:
                predict_kwargs["temperature"] = options.vl_temperature
            if options.vl_top_p > 0:
                predict_kwargs["top_p"] = options.vl_top_p

            output = pipeline.predict(**predict_kwargs)

            # 确保 GPU 操作完成后再处理结果
            output_list = self._consume_generator_safely(output)

            text_with_scores: list[tuple[str, float]] = []

            for res in output_list:
                # 提取结果
                if hasattr(res, "result"):
                    result_text = res.result
                    if result_text:
                        text_with_scores.append((result_text, 1.0))
                elif hasattr(res, "answer"):
                    answer_text = res.answer
                    if answer_text:
                        text_with_scores.append((answer_text, 1.0))
                elif hasattr(res, "text"):
                    text_with_scores.append((res.text, 1.0))
                # 字典格式处理
                elif isinstance(res, dict):
                    result_text = res.get(
                        "result", res.get("answer", res.get("text", ""))
                    )
                    if result_text:
                        text_with_scores.append((result_text, 1.0))

            # 生成文本
            raw_text = (
                "\n".join(t for t, _ in text_with_scores) if text_with_scores else ""
            )

            return self._build_ocr_result(
                raw_text=raw_text,
                text_with_scores=text_with_scores,
                pipeline_type="DocUnderstanding",
            )

        try:
            return _do_recognize(image)
        except RuntimeError as e:
            if self._is_gpu_error(e) and self._device != "cpu":
                _logger.warning("预测过程中发生 GPU 错误，回退到 CPU: %s", e)
                self._reset_pipeline_to_cpu("doc_understanding")
                return _do_recognize(image)
            raise
