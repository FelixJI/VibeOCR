"""
OCR Service 使用便携式 Python

这个模块通过在主进程中修改 sys.path 来导入 PaddleX，
而不是使用子进程。支持：
- 开发环境：使用 .venv 虚拟环境
- 生产环境：使用便携式 python/ 目录

与子进程方案的对比：
- 优点：无进程间通信开销，调试方便，代码更简单
- 缺点：失去进程隔离，PaddleX 崩溃会影响主程序
"""

import logging
import threading
from typing import Any, Optional

import numpy as np
from PIL import Image

import os

# 跳过模型源网络检测
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

# 导入路径管理器
from vibeocr.python_path_manager import (
    PythonPathMode,
    get_python_path_manager,
)

_logger = logging.getLogger(__name__)


class OCRServicePortable:
    """
    OCR Service 使用便携式 Python

    通过路径管理器在主进程中导入 PaddleX，支持开发和生产环境。
    """

    _instance: Optional["OCRServicePortable"] = None
    _pipeline: Any = None
    _lock = threading.Lock()

    def __new__(cls) -> "OCRServicePortable":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """初始化 OCR 服务"""
        # 确保路径已设置
        self.path_manager = get_python_path_manager()
        self.path_manager.setup_sys_path()

        # 记录环境信息
        _logger.info(f"OCR 服务初始化，Python 模式: {self.path_manager.mode}")
        if self.path_manager.ocr_lib_path:
            _logger.info(f"OCR 库路径: {self.path_manager.ocr_lib_path}")

    def _import_paddlex(self):
        """导入 PaddleX（延迟导入）"""
        try:
            from paddlex import create_pipeline

            return create_pipeline
        except ImportError as e:
            # 提供详细的错误信息
            error_msg = f"无法导入 PaddleX: {e}\n"

            if self.path_manager.mode == PythonPathMode.DEVELOPMENT:
                error_msg += "\n开发环境解决方案：\n"
                error_msg += "1. 激活虚拟环境: source .venv/bin/activate (Linux/Mac) 或 .venv\\Scripts\\activate (Windows)\n"
                error_msg += "2. 安装 PaddleX: pip install paddlex paddlepaddle\n"

            elif self.path_manager.mode == PythonPathMode.PORTABLE:
                error_msg += "\n便携式环境解决方案：\n"
                error_msg += "1. 检查 python/ 目录是否存在且完整\n"
                error_msg += "2. 运行环境设置: python -m vibeocr.env_manager\n"

            else:
                error_msg += "\n系统环境解决方案：\n"
                error_msg += "1. 安装 PaddleX: pip install paddlex paddlepaddle\n"

            _logger.error(error_msg)
            raise ImportError(error_msg) from e

    @staticmethod
    def _get_optimal_cpu_threads() -> int:
        """动态检测 CPU 核心数并返回最优线程数"""
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
        return min(max(logical // 4, 4), 16)

    def _create_pipeline(self) -> Any:
        """创建 OCR 流水线（CPU 模式）"""
        create_pipeline = self._import_paddlex()

        from paddlex.inference.utils.pp_option import PaddlePredictorOption

        cpu_threads = self._get_optimal_cpu_threads()
        _logger.info(f"[推理优化] CPU 线程数: {cpu_threads}")

        pp_option = PaddlePredictorOption()
        pp_option.enable_new_ir = False
        pp_option.run_mode = "paddle"
        pp_option.cpu_threads = cpu_threads

        # 尝试 HPIP 加速
        pipeline = None
        try:
            from paddlex.utils.deps import is_hpip_available
            if is_hpip_available():
                pipeline = create_pipeline(
                    pipeline="OCR",
                    device="cpu",
                    pp_option=pp_option,
                    use_hpip=True,
                    hpi_config={"backend": "onnxruntime"},
                )
                _logger.info("[HPIP] 高性能推理管道创建成功")
        except Exception as e:
            _logger.info(f"[HPIP] 创建失败，回退到普通推理: {e}")

        if pipeline is None:
            pipeline = create_pipeline(
                pipeline="OCR",
                device="cpu",
                pp_option=pp_option,
            )

        _logger.info(f"OCR 流水线创建成功，设备: cpu, 线程: {cpu_threads}")
        return pipeline

    @property
    def pipeline(self) -> Any:
        """获取 OCR 流水线（懒加载，线程安全，CPU 模式）"""
        if self._pipeline is None:
            with self._lock:
                if self._pipeline is None:
                    self._pipeline = self._create_pipeline()

        return self._pipeline

    def recognize(
        self,
        image: Image.Image | np.ndarray | str,
    ) -> str:
        """
        对图像执行 OCR 识别

        Args:
            image: PIL Image、numpy 数组或图像路径

        Returns:
            识别的文本内容
        """
        output = self.pipeline.predict(
            input=image,
            use_doc_orientation_classify=True,
            use_doc_unwarping=True,
            use_textline_orientation=True,
        )

        texts = []
        for res in output:
            if hasattr(res, "rec_texts"):
                texts.extend(res.rec_texts)
            elif hasattr(res, "ocr_text"):
                texts.append(res.ocr_text)
            elif isinstance(res, dict):
                rec_texts = res.get("rec_texts", [])
                texts.extend(rec_texts)

        return "\\n".join(texts) if texts else ""

    def get_environment_info(self) -> dict:
        """获取环境信息"""
        return self.path_manager.get_environment_info()

    def verify_environment(self) -> tuple[bool, str]:
        """验证环境是否正确配置"""
        return self.path_manager.verify_environment()


# 便捷函数
def get_ocr_service_portable() -> OCRServicePortable:
    """获取便携式 OCR 服务实例"""
    return OCRServicePortable()


# 为了向后兼容，提供一个别名
OCRService = OCRServicePortable


# 测试函数
def test_portable_ocr():
    """测试便携式 OCR 服务"""
    print("\n" + "=" * 60)
    print("便携式 OCR 服务测试")
    print("=" * 60)

    # 获取服务
    service = get_ocr_service_portable()

    # 打印环境信息
    info = service.get_environment_info()
    print("\n环境信息:")
    print(f"  模式: {info['mode']}")
    print(f"  是否打包: {info['is_frozen']}")
    print(f"  Python: {info['python_executable']}")
    print(f"  OCR 库路径: {info['ocr_lib_path']}")
    print(f"  可导入 PaddleX: {info['can_import_paddlex']}")

    # 验证环境
    success, message = service.verify_environment()
    print(f"\n环境验证: {message}")

    if success:
        print("\n✓ 环境配置正确，可以正常使用 OCR 服务")
        return 0
    print(f"\n✗ 环境配置有问题: {message}")
    return 1


if __name__ == "__main__":
    import sys

    sys.exit(test_portable_ocr())
