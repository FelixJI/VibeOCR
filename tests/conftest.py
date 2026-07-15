"""Pytest configuration and fixtures for VibeOCR tests."""

import os

# ---------------------------------------------------------------------------
# Windows + PaddlePaddle + Torch OpenMP 冲突兜底（必须在任何可能 import
# paddle/torch 的测试之前设置）。
#
# 全量 pytest 会依次触发 paddle（含 libiomp5md.dll）与 torch（含另一份
# libiomp5md.dll）的 DLL 加载。Windows 加载器遇到第二份冲突的 OpenMP 时抛
# ENTRYPOINT_NOT_FOUND (0xc0000139) 致命异常（在 torch._load_dll_libraries 处），
# 可能杀死整个 pytest 进程或留下后台线程崩溃的诊断噪音（行为非确定）。
#
# 生产环境 src/vibeocr/main.py 顶部已设置同样的兜底；测试进程不经过 main.py，
# 故在此复制。KMP_DUPLICATE_LIB_OK=TRUE 让 Intel OpenMP 运行时容忍重复加载，
# 减少致命冲突。另见 test_ocr_service.py 的 PADDLE_TORCH_CONFLICT 跳过（治本）。
# ---------------------------------------------------------------------------
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import io
import sys
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

# Add src directory to Python path
src_path = Path(__file__).parent.parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))


@pytest.fixture(scope="session")
def qapp():
    """提供 QApplication 实例（GUI 测试必需）。"""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def pytest_sessionfinish(session, exitstatus):
    """Close the process-wide WorkerHost before pytest joins executor threads."""
    from vibeocr.client.session import shutdown_backend_client

    shutdown_backend_client()


@pytest.fixture
def sample_pixmap():
    """提供测试用 QPixmap（100x50 白色图片）。"""
    pixmap = QPixmap(100, 50)
    pixmap.fill()
    return pixmap


@pytest.fixture
def sample_image_bytes():
    """提供测试图片的字节数据。"""
    img = Image.new("RGB", (100, 50), color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def sample_image_with_text_bytes():
    """提供包含简单文字的测试图片字节数据。"""
    from PIL import ImageDraw

    img = Image.new("RGB", (200, 100), color="white")
    draw = ImageDraw.Draw(img)
    # 使用默认字体绘制文字
    draw.text((10, 30), "Test OCR", fill="black")

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def temp_image_file(tmp_path, sample_image_with_text_bytes):
    """提供临时图片文件路径。"""
    img_path = tmp_path / "test_image.png"
    img_path.write_bytes(sample_image_with_text_bytes)
    return img_path


@pytest.fixture
def wait_worker():
    """返回一个等待 QThread worker 完成的辅助函数。"""
    import time

    from PySide6.QtCore import QCoreApplication

    def _wait(worker, timeout=10000):
        start = time.monotonic()
        while not worker.isFinished():
            QCoreApplication.processEvents()
            worker.wait(50)
            if time.monotonic() - start > timeout / 1000:
                break
        QCoreApplication.processEvents()

    return _wait
