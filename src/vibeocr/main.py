"""VibeOCR 应用程序入口点"""

import os
import sys
from pathlib import Path

# ============================================================
# 重要：必须在导入任何其他模块之前设置以下环境变量
# 这些设置解决 Windows + PaddlePaddle + NumPy 环境下的常见崩溃问题
# ============================================================

# 解决 OpenMP 库冲突 (libiomp5md.dll 重复加载导致 0xC0000005 崩溃)
# 当多个库（PaddlePaddle、NumPy、Intel MKL）各自捆绑不同版本的 OpenMP 时会发生冲突
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

# 禁用 OneDNN 以提高兼容性（某些 CPU 指令集不兼容会导致崩溃）
os.environ.setdefault("FLAGS_enable_onednn_backend", "0")
os.environ.setdefault("FLAGS_use_mkldnn", "0")

# 设置环境变量以抑制不必要的警告
# 禁用 PaddleX 的模型源连接检查
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

# 导入环境管理模块
from vibeocr import env_manager

# 确保src目录在路径中
src_path = Path(__file__).parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))


def check_production_dependencies() -> bool:
    """检查生产环境依赖

    Returns:
        是否所有生产依赖都已安装
    """
    ready, missing = env_manager.is_production_environment_ready()
    if not ready:
        print(f"[VibeOCR] 缺少生产依赖: {', '.join(missing)}")
        print("[VibeOCR] 请使用以下命令安装:")
        print("  pip install pyside6 pillow")
        print("  或")
        print("  uv sync")
    return ready


def launch_application() -> int:
    """启动应用程序"""
    from PySide6.QtWidgets import QApplication
    from vibeocr.views.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("VibeOCR")
    app.setApplicationVersion("0.1.0")

    window = MainWindow()
    window.show()

    return app.exec()


def main() -> int:
    """应用程序入口点

    启动流程：
    1. 检测生产环境依赖（PySide6, Pillow）
    2. 失败 → 控制台错误提示，退出
    3. 通过 → 启动GUI
    4. GUI启动后 → 异步检测嵌入式OCR依赖
    """

    # 1. 检查生产环境依赖
    if not check_production_dependencies():
        input("\n按回车键退出...")
        return 1

    # 2. 启动应用
    print("[VibeOCR] 启动应用...")
    return launch_application()


if __name__ == "__main__":
    sys.exit(main())
