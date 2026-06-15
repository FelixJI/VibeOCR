"""QrcodeDecodeService 单元测试"""

import pytest

pytest.importorskip("pyzbar")  # pyzbar 缺失时整个文件跳过

from PIL import Image  # noqa: E402

from vibeocr.services.qrcode_decode_service import (  # noqa: E402
    DecodedItem,
    QrcodeDecodeService,
)
from vibeocr.services.qrcode_service import QrcodeService  # noqa: E402


@pytest.fixture
def decode_service():
    return QrcodeDecodeService()


@pytest.fixture
def gen_service():
    return QrcodeService()


def _make_qr_image(text: str, gen_service) -> Image.Image:
    opts = gen_service.default_options()
    opts["format"] = "qr"
    return gen_service.generate(text, opts)


class TestDecodeServiceStructure:
    def test_default_options_returns_dict(self, decode_service):
        opts = decode_service.default_options()
        assert isinstance(opts, dict)

    def test_decode_returns_list(self, decode_service, gen_service):
        img = _make_qr_image("Hello", gen_service)
        results = decode_service.decode(img)
        assert isinstance(results, list)

    def test_decoded_item_fields(self):
        item = DecodedItem(data="x", type="QRCODE", is_url=False)
        assert item.data == "x"
        assert item.type == "QRCODE"
        assert item.is_url is False


class TestDecodeRoundtrip:
    def test_decode_url_qr(self, decode_service, gen_service):
        url = "https://example.com"
        img = _make_qr_image(url, gen_service)
        results = decode_service.decode(img)
        assert len(results) == 1
        assert results[0].data == url
        assert results[0].is_url is True

    def test_decode_non_url_text(self, decode_service, gen_service):
        # 注：用 ASCII 文本，避免 qrcode 库对 CJK 的已知编码缺陷
        # （qrcode 把非 ASCII 字节按 kanji 模式错误转换，是生成侧问题，与本解码服务无关）
        text = "Plain text 12345"
        img = _make_qr_image(text, gen_service)
        results = decode_service.decode(img)
        assert len(results) == 1
        assert results[0].data == text
        assert results[0].is_url is False

    def test_decode_type_is_qrcode(self, decode_service, gen_service):
        img = _make_qr_image("test", gen_service)
        results = decode_service.decode(img)
        assert results[0].type.upper() == "qrcode".upper() or "QR" in results[
            0
        ].type.upper()
