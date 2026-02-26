"""PaddleX OCR 服务"""

import logging
import os
import sys
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional

# 禁用 OneDNN 并强制使用 CPU 模式以兼容性
os.environ.setdefault("FLAGS_enable_onednn_backend", "0")
os.environ.setdefault("FLAGS_use_mkldnn", "0")

import numpy as np
import paddle
from PIL import Image

# 在导入 paddlex 之前设置模型下载源
# 这会自动检测最快的源（BOS 或 HuggingFace）
from vibeocr.env_manager import setup_paddlex_model_source
setup_paddlex_model_source()

from paddlex import create_pipeline

_logger = logging.getLogger(__name__)


class OCRPipeline(Enum):
    """OCR 管道类型"""

    OCR = "OCR"  # 通用 OCR：文本识别
    TABLE_RECOGNITION = "table_recognition"  # 表格识别
    FORMULA_RECOGNITION = "formula_recognition"  # 公式识别
    PP_STRUCTURE_V3 = "PP-StructureV3"  # 版面解析（包含可选的表格/公式子产线）

    @property
    def display_name(self) -> str:
        """获取显示名称"""
        names = {
            OCRPipeline.OCR: "通用 OCR",
            OCRPipeline.TABLE_RECOGNITION: "表格识别",
            OCRPipeline.FORMULA_RECOGNITION: "公式识别",
            OCRPipeline.PP_STRUCTURE_V3: "版面解析",
        }
        return names.get(self, "通用 OCR")

    @property
    def description(self) -> str:
        """获取描述"""
        descriptions = {
            OCRPipeline.OCR: "识别图片中的文字内容",
            OCRPipeline.TABLE_RECOGNITION: "识别表格结构，输出 HTML/Excel 格式",
            OCRPipeline.FORMULA_RECOGNITION: "识别数学公式，输出 LaTeX 格式",
            OCRPipeline.PP_STRUCTURE_V3: "解析文档版面，支持表格、公式等子产线",
        }
        return descriptions.get(self, "识别图片中的文字内容")


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


@dataclass
class OCROptions:
    """OCR 识别选项"""

    pipeline: OCRPipeline = OCRPipeline.OCR
    # 通用预处理选项（对所有管道适用）
    use_doc_orientation_classify: bool = False  # 文档方向分类
    use_doc_unwarping: bool = False  # 文本图像去弯曲
    # 管道特有选项
    use_textline_orientation: bool = True  # 文本行方向分类（仅 OCR 管道）
    use_layout_detection: bool = False  # 版面检测（表格/公式管道，用于检测目标区域）
    # 版面解析子产线选项（仅当 pipeline == PP_STRUCTURE_V3 时有效）
    use_table_recognition: bool = True  # 表格识别子产线
    use_formula_recognition: bool = True  # 公式识别子产线
    use_seal_recognition: bool = False  # 印章识别子产线
    use_chart_recognition: bool = False  # 图表识别子产线


class OCRService:
    """OCR 识别服务 (线程安全的单例模式)"""

    _instance: Optional["OCRService"] = None
    _pipelines: dict[str, Any] = {}  # 管道缓存：{pipeline_name: pipeline_instance}
    _device: Optional[str] = None
    _lock = threading.Lock()
    _initialized = False
    _status_callback: Optional[callable] = None  # 状态回调函数

    @classmethod
    def set_status_callback(cls, callback: Optional[callable]) -> None:
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

    def __new__(cls) -> "OCRService":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:  # 双重检查
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            with self._lock:
                if not self._initialized:
                    self._init_gpu()
                    self._initialized = True

    def _init_gpu(self) -> None:
        """初始化 GPU 环境并检查可用性"""
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
            os.environ["PATH"] = os.pathsep.join(paths_to_add + [current_path])
            
            # 更新路径后重新检查设备（可能需要重启或重新加载库，但值得一试）
            try:
                if "gpu" not in paddle.device.get_device():
                    # 如果可能，强制重新检查？ paddle 通常会缓存设备信息。
                    pass
            except:
                pass

    def _create_pipeline(self, pipeline_name: str, device: str) -> Any:
        """创建指定管道"""
        # 获取管道显示名称
        display_name = pipeline_name
        for p in OCRPipeline:
            if p.value == pipeline_name:
                display_name = p.display_name
                break

        # 通知正在初始化/下载模型
        self._notify_status("模型初始化", f"正在初始化 {display_name} 管道（首次使用需要下载模型）...")

        pipeline = create_pipeline(
            pipeline=pipeline_name,
            device=device,
        )
        _logger.info("管道 %s 初始化于设备: %s", pipeline_name, device)

        # 通知初始化完成
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
                            self._pipelines[pipeline_name] = self._create_pipeline(pipeline_name, device)
                            if self._device is None:
                                self._device = device
                            break
                        except RuntimeError as e:
                            if self._is_gpu_error(e) and "gpu" in device.lower():
                                _logger.warning(
                                    "GPU 不可用，回退到 CPU: %s", e
                                )
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
        image: Image.Image | np.ndarray | str,
        options: OCROptions | None = None,
    ) -> tuple[str, list[tuple[str, float]]]:
        """
        对图像执行 OCR 识别

        Args:
            image: PIL Image, numpy 数组, 或图像路径
            options: OCR 识别选项

        Returns:
            (识别出的文本内容, [(文本块, 置信度), ...])
        """

        actual_options = options if options is not None else OCROptions()

        # 根据管道类型分发
        if actual_options.pipeline == OCRPipeline.OCR:
            return self._recognize_ocr(image, actual_options)
        elif actual_options.pipeline == OCRPipeline.TABLE_RECOGNITION:
            return self._recognize_table(image, actual_options)
        elif actual_options.pipeline == OCRPipeline.FORMULA_RECOGNITION:
            return self._recognize_formula(image, actual_options)
        elif actual_options.pipeline == OCRPipeline.PP_STRUCTURE_V3:
            return self._recognize_structure(image, actual_options)
        else:
            return self._recognize_ocr(image, actual_options)

    def _recognize_ocr(
        self,
        image: Image.Image | np.ndarray | str,
        options: OCROptions,
    ) -> tuple[str, list[tuple[str, float]]]:
        """通用 OCR 识别"""

        def _do_recognize(img: Image.Image | np.ndarray | str) -> tuple[str, list[tuple[str, float]]]:
            pipeline = self.get_pipeline(OCRPipeline.OCR)
            output = pipeline.predict(
                input=img,
                use_doc_orientation_classify=options.use_doc_orientation_classify,
                use_doc_unwarping=options.use_doc_unwarping,
                use_textline_orientation=options.use_textline_orientation,
            )

            return self._extract_ocr_result(output)

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
    ) -> tuple[str, list[tuple[str, float]]]:
        """表格识别"""

        def _do_recognize(img: Image.Image | np.ndarray | str) -> tuple[str, list[tuple[str, float]]]:
            pipeline = self.get_pipeline(OCRPipeline.TABLE_RECOGNITION)
            output = pipeline.predict(
                input=img,
                use_doc_orientation_classify=options.use_doc_orientation_classify,
                use_doc_unwarping=options.use_doc_unwarping,
                use_layout_detection=options.use_layout_detection,
            )

            text_with_scores: list[tuple[str, float]] = []
            html_tables: list[str] = []

            for res in output:
                # 提取表格 HTML
                if hasattr(res, "table_res_list"):
                    for table_res in res.table_res_list:
                        if hasattr(table_res, "pred_html"):
                            html_tables.append(table_res.pred_html)
                        # 提取表格中的文本
                        if hasattr(table_res, "table_ocr_pred"):
                            ocr_pred = table_res.table_ocr_pred
                            if hasattr(ocr_pred, "rec_texts") and hasattr(ocr_pred, "rec_scores"):
                                for text, score in zip(ocr_pred.rec_texts, ocr_pred.rec_scores):
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
            full_text = "\n\n".join(html_tables) if html_tables else ""
            if text_with_scores:
                if full_text:
                    full_text += "\n\n"
                full_text += "\n".join(t for t, _ in text_with_scores)

            return full_text, text_with_scores

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
    ) -> tuple[str, list[tuple[str, float]]]:
        """公式识别"""

        def _do_recognize(img: Image.Image | np.ndarray | str) -> tuple[str, list[tuple[str, float]]]:
            pipeline = self.get_pipeline(OCRPipeline.FORMULA_RECOGNITION)
            output = pipeline.predict(
                input=img,
                use_doc_orientation_classify=options.use_doc_orientation_classify,
                use_doc_unwarping=options.use_doc_unwarping,
                use_layout_detection=options.use_layout_detection,
            )

            text_with_scores: list[tuple[str, float]] = []

            for res in output:
                # 提取公式 LaTeX 代码
                if hasattr(res, "rec_formula"):
                    formula = res.rec_formula
                    if formula:
                        text_with_scores.append((formula, 1.0))
                elif isinstance(res, dict):
                    formula = res.get("rec_formula", "")
                    if formula:
                        text_with_scores.append((formula, 1.0))

            full_text = "\n".join(t for t, _ in text_with_scores)
            return full_text, text_with_scores

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
    ) -> tuple[str, list[tuple[str, float]]]:
        """版面解析（PP-StructureV3）"""

        def _do_recognize(img: Image.Image | np.ndarray | str) -> tuple[str, list[tuple[str, float]]]:
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

            text_with_scores: list[tuple[str, float]] = []
            markdown_parts: list[str] = []

            for res in output:
                # 提取 Markdown 结果（如果有）
                # 注意：res.markdown 返回的是字典，包含 markdown_texts 等键
                if hasattr(res, "markdown"):
                    markdown_data = res.markdown
                    if isinstance(markdown_data, dict):
                        # 提取 markdown_texts 字符串
                        markdown_text = markdown_data.get("markdown_texts", "")
                        if markdown_text:
                            markdown_parts.append(markdown_text)
                    elif isinstance(markdown_data, str):
                        markdown_parts.append(markdown_data)

                # 提取 OCR 文本
                if hasattr(res, "rec_texts") and hasattr(res, "rec_scores"):
                    for text, score in zip(res.rec_texts, res.rec_scores):
                        if text:
                            text_with_scores.append((text, float(score)))

                # 提取表格 HTML
                if hasattr(res, "table_res_list"):
                    for table_res in res.table_res_list:
                        if hasattr(table_res, "pred_html"):
                            markdown_parts.append(table_res.pred_html)

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
                        elif isinstance(markdown_data, str):
                            markdown_parts.append(markdown_data)
                    rec_texts = res.get("rec_texts", [])
                    rec_scores = res.get("rec_scores", [])
                    for text, score in zip(rec_texts, rec_scores):
                        if text:
                            text_with_scores.append((text, float(score)))

            # 优先使用 Markdown，否则使用文本
            full_text = "\n\n".join(markdown_parts) if markdown_parts else "\n".join(t for t, _ in text_with_scores)
            return full_text, text_with_scores

        try:
            return _do_recognize(image)
        except RuntimeError as e:
            if self._is_gpu_error(e) and self._device != "cpu":
                _logger.warning("预测过程中发生 GPU 错误，回退到 CPU: %s", e)
                self._reset_pipeline_to_cpu("PP-StructureV3")
                return _do_recognize(image)
            raise

    def _extract_ocr_result(self, output) -> tuple[str, list[tuple[str, float]]]:
        """从 OCR 输出中提取结果"""
        text_with_scores: list[tuple[str, float]] = []
        for res in output:
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

        full_text = "\n".join(t for t, _ in text_with_scores)
        return full_text, text_with_scores
