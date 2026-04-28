"""QrcodeService 单元测试"""

import pytest
from PIL import Image


@pytest.fixture
def service():
    from vibeocr.services.qrcode_service import QrcodeService

    return QrcodeService()


class TestQrCodeGeneration:
    def test_generate_qr_returns_pil_image(self, service):
        options = service.default_options()
        options["format"] = "qr"
        options["text"] = "Hello"
        img = service.generate("Hello", options)
        assert isinstance(img, Image.Image)

    def test_generate_qr_non_empty(self, service):
        options = service.default_options()
        options["format"] = "qr"
        img = service.generate("Hello", options)
        assert img.width > 0
        assert img.height > 0

    def test_generate_qr_respects_size(self, service):
        options = service.default_options()
        options["format"] = "qr"
        options["size"] = 200
        img = service.generate("Test", options)
        assert img.width == 200
        assert img.height == 200

    def test_generate_qr_respects_error_correction(self, service):
        options = service.default_options()
        options["format"] = "qr"
        options["error_correction"] = "H"
        img = service.generate("Test", options)
        assert isinstance(img, Image.Image)

    def test_generate_qr_respects_fg_bg_colors(self, service):
        options = service.default_options()
        options["format"] = "qr"
        options["fg_color"] = "#FF0000"
        options["bg_color"] = "#0000FF"
        img = service.generate("Test", options)
        assert isinstance(img, Image.Image)

    def test_default_options_returns_dict(self, service):
        opts = service.default_options()
        assert isinstance(opts, dict)
        assert opts["format"] == "qr"
        assert opts["size"] == 300
        assert opts["error_correction"] == "M"
        assert opts["fg_color"] == "#000000"
        assert opts["bg_color"] == "#FFFFFF"
        assert opts["invert"] is False
