"""
OCR Service 使用便携式 Python

这个模块通过在主进程中修改 sys.path 来导入 PaddleX，
而不是使用子进程。支持：
- 开发环境：使用 .venv 虚拟环境
- 生产环境：使用便携式 python/ 目录

与子进程方案的对比：
- 优点：无进程间通信开销，调试方便，代码更简单
- 缺点：失去进程隔离，PaddleX 崩溃会影响主程序

便携式 Python 环境说明：
在生产环境中，python/ 目录包含：
- python.exe: Python 解释器
- Lib/site-packages/: PaddleX、PaddlePaddle 等依赖
- DLLs/: CUDA、cuDNN 等 DLL 文件（如需要）
"""

import logging
import os
import sys
import threading
from typing import Any, Optional, Union
from pathlib import Path

import numpy as np
from PIL import Image

# 禁用 OneDNN 以提高兼容性
os.environ.setdefault("FLAGS_enable_onednn_backend", "0")
os.environ.setdefault("FLAGS_use_mkldnn", "0")

# 导入路径管理器
from vibeocr.python_path_manager import (
    PythonPathManager,
    get_python_path_manager,
    PythonPathMode,
)

_logger = logging.getLogger(__name__)


def _setup_cuda_dll_paths() -> None:
    """
    设置 CUDA DLL 路径到 PATH 环境变量

    在便携式 Python 环境中，CUDA DLL 文件可能位于 python/ 目录下的特定位置。
    此函数确保这些路径被添加到 PATH 中，以便 PaddlePaddle 可以正确加载它们。
    """
    if hasattr(sys, 'frozen'):
        # 打包环境：python/ 目录在 exe 同级
        app_dir = Path(sys.executable).parent
        portable_python_dir = app_dir / "python"
    else:
        # 开发环境
        portable_python_dir = Path(__file__).parent.parent.parent.parent / "python"

    if not portable_python_dir.exists():
        return

    # 常见的 CUDA DLL 位置
    cuda_dll_paths = [
        portable_python_dir / "Library" / "bin",  # conda 风格
        portable_python_dir / "DLLs",              # Windows 嵌入式 Python
        portable_python_dir,                       # 根目录
    ]

    # 获取当前 PATH
    current_path = os.environ.get("PATH", "")

    # 添加不存在的路径
    paths_to_add = []
    for dll_path in cuda_dll_paths:
        if dll_path.exists() and str(dll_path) not in current_path:
            paths_to_add.append(str(dll_path))

    if paths_to_add:
        # 将新路径添加到 PATH 前面
        os.environ["PATH"] = os.pathsep.join(paths_to_add + [current_path])
        _logger.info(f"已添加 CUDA DLL 路径到 PATH: {paths_to_add}")


class OCRServicePortable:
    """
    OCR Service 使用便携式 Python

    通过路径管理器在主进程中导入 PaddleX，支持开发和生产环境。
    """

    _instance: Optional["OCRServicePortable"] = None
    _pipeline: Any = None
    _device: Optional[str] = None
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

        # 在便携式 Python 环境中设置 CUDA DLL 路径
        if self.path_manager.mode == PythonPathMode.PORTABLE:
            _setup_cuda_dll_paths()

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

    def _create_pipeline(self, device: str) -> Any:
        """创建 OCR 流水线"""
        create_pipeline = self._import_paddlex()

        pipeline = create_pipeline(
            pipeline="OCR",
            device=device,
        )
        _logger.info(f"OCR 流水线创建成功，设备: {device}")
        return pipeline

    def _is_gpu_error(self, error: Exception) -> bool:
        """检查错误是否与 GPU 相关"""
        err_str = str(error).lower()
        gpu_keywords = ["cudnn", "cuda", "gpu", "cudart", "cublas"]
        return any(keyword in err_str for keyword in gpu_keywords)

    @property
    def pipeline(self) -> Any:
        """
        获取 OCR 流水线（懒加载，线程安全）

        自动尝试 GPU，失败时降级到 CPU
        """
        if self._pipeline is None:
            with self._lock:
                if self._pipeline is None:
                    # 尝试 GPU，失败时降级到 CPU
                    for device in ["gpu:0", "cpu"]:
                        try:
                            self._pipeline = self._create_pipeline(device)
                            self._device = device
                            break
                        except RuntimeError as e:
                            if self._is_gpu_error(e) and "gpu" in device.lower():
                                _logger.warning(f"GPU 不可用，降级到 CPU: {e}")
                                continue
                            raise
                    else:
                        raise RuntimeError("无法在任何设备上初始化 OCR 流水线")

        return self._pipeline

    def _reset_pipeline_to_cpu(self) -> None:
        """重置流水线到 CPU 模式"""
        with self._lock:
            _logger.warning("由于 GPU 错误，重置流水线到 CPU 模式")
            self._pipeline = self._create_pipeline("cpu")
            self._device = "cpu"

    def recognize(
        self,
        image: Union[Image.Image, np.ndarray, str],
    ) -> str:
        """
        对图像执行 OCR 识别

        Args:
            image: PIL Image、numpy 数组或图像路径

        Returns:
            识别的文本内容
        """
        def _do_recognize(img: Union[Image.Image, np.ndarray, str]) -> str:
            """执行 OCR 识别"""
            output = self.pipeline.predict(
                input=img,
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

        try:
            return _do_recognize(image)
        except RuntimeError as e:
            # GPU 错误且未在 CPU 模式，降级到 CPU
            if self._is_gpu_error(e) and self._device != "cpu":
                _logger.warning(f"GPU 预测错误，降级到 CPU: {e}")
                self._reset_pipeline_to_cpu()
                return _do_recognize(image)
            raise

    def get_environment_info(self) -> dict:
        """
        获取环境信息

        Returns:
            包含环境详细信息的字典
        """
        return self.path_manager.get_environment_info()

    def verify_environment(self) -> tuple[bool, str]:
        """
        验证环境是否正确配置

        Returns:
            (是否成功, 消息)
        """
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
    print(f"\n环境信息:")
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
    else:
        print(f"\n✗ 环境配置有问题: {message}")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(test_portable_ocr())
