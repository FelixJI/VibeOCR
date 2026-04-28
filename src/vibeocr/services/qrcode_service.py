"""二维码/条形码生成服务"""

import logging

from PIL import Image

logger = logging.getLogger(__name__)

QR_ERROR_CORRECTION_MAP = {
    "L": 1,
    "M": 0,
    "Q": 3,
    "H": 2,
}


class QrcodeService:
    """二维码和条形码生成服务"""

    def default_options(self) -> dict:
        return {
            "format": "qr",
            "size": 300,
            "error_correction": "M",
            "fg_color": "#000000",
            "bg_color": "#FFFFFF",
            "invert": False,
            "logo_path": None,
            "logo_ratio": 0.2,
            "label_text": "",
            "label_position": "bottom",
            "label_font_size": 12,
        }

    def generate(self, text: str, options: dict) -> Image.Image:
        fmt = options.get("format", "qr")
        if fmt == "qr":
            return self._generate_qr(text, options)
        return self._generate_barcode(text, options)

    def _generate_qr(self, text: str, options: dict) -> Image.Image:
        import qrcode

        ec_level = QR_ERROR_CORRECTION_MAP.get(
            options.get("error_correction", "M"), 0
        )
        qr = qrcode.QRCode(
            version=None,
            error_correction=ec_level,
            box_size=10,
            border=4,
        )
        qr.add_data(text)
        qr.make(fit=True)

        fg_color = options.get("fg_color", "#000000")
        bg_color = options.get("bg_color", "#FFFFFF")
        img = qr.make_image(fill_color=fg_color, back_color=bg_color)
        if not isinstance(img, Image.Image):
            img = img.get_image()

        target_size = options.get("size", 300)
        img = img.resize((target_size, target_size), Image.Resampling.LANCZOS)
        return img.convert("RGB")

    def _generate_barcode(self, text: str, options: dict) -> Image.Image:
        raise NotImplementedError("barcode generation in Task 3")

    def apply_logo(self, image: Image.Image, logo_path: str, ratio: float = 0.2) -> Image.Image:
        raise NotImplementedError("apply_logo in Task 4")

    def apply_text_label(self, image: Image.Image, text: str, position: str = "bottom", font_size: int = 12) -> Image.Image:
        raise NotImplementedError("apply_text_label in Task 5")

    def invert_colors(self, image: Image.Image) -> Image.Image:
        raise NotImplementedError("invert_colors in Task 5")

    def generate_svg(self, text: str, options: dict) -> str:
        raise NotImplementedError("generate_svg in Task 6")
