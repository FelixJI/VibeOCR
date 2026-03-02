"""Pytest configuration and fixtures for VibeOCR tests."""

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
    from PIL import ImageDraw, ImageFont

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
