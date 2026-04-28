"""二维码/条形码生成服务"""

import io
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
        import barcode
        from barcode.writer import ImageWriter

        fmt = options.get("format", "code128").upper()
        fg_color = options.get("fg_color", "#000000")
        bg_color = options.get("bg_color", "#FFFFFF")

        writer = ImageWriter()
        writer.set_options({
            "foreground": fg_color,
            "background": bg_color,
        })

        barcode_class = barcode.get_barcode_class(fmt)
        bc = barcode_class(text, writer=writer)

        buffer = io.BytesIO()
        bc.write(buffer)
        buffer.seek(0)
        img = Image.open(buffer)
        img = img.convert("RGB")

        target_size = options.get("size", 300)
        w, h = img.size
        new_h = target_size
        new_w = int(w * new_h / h) if h > 0 else target_size
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        return img

    def apply_logo(self, image: Image.Image, logo_path: str, ratio: float = 0.2) -> Image.Image:
        logo = Image.open(logo_path).convert("RGBA")
        qr_w, qr_h = image.size
        logo_size = int(min(qr_w, qr_h) * ratio)
        logo = logo.resize((logo_size, logo_size), Image.Resampling.LANCZOS)

        image = image.convert("RGBA")
        pos_x = (qr_w - logo_size) // 2
        pos_y = (qr_h - logo_size) // 2

        image.paste(logo, (pos_x, pos_y), logo)
        return image.convert("RGB")

    def apply_text_label(self, image: Image.Image, text: str, position: str = "bottom", font_size: int = 12) -> Image.Image:
        if position == "none" or not text:
            return image

        from PIL import ImageDraw, ImageFont

        font = ImageFont.load_default(size=font_size)
        dummy = Image.new("RGB", (1, 1))
        draw = ImageDraw.Draw(dummy)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        padding = 8

        img_w, img_h = image.size
        new_w = int(max(img_w, text_w + padding * 2))
        label_h = int(text_h + padding * 2)

        if position == "top":
            canvas = Image.new("RGB", (new_w, img_h + label_h), image.getpixel((0, 0)))
            draw = ImageDraw.Draw(canvas)
            text_x = (new_w - text_w) // 2
            draw.text((text_x, padding), text, fill=(0, 0, 0), font=font)
            canvas.paste(image, ((new_w - img_w) // 2, label_h))
        else:
            canvas = Image.new("RGB", (new_w, img_h + label_h), image.getpixel((0, 0)))
            canvas.paste(image, ((new_w - img_w) // 2, 0))
            draw = ImageDraw.Draw(canvas)
            text_x = (new_w - text_w) // 2
            draw.text((text_x, img_h + padding), text, fill=(0, 0, 0), font=font)

        return canvas

    def invert_colors(self, image: Image.Image) -> Image.Image:
        from PIL import ImageOps

        return ImageOps.invert(image)

    def generate_svg(self, text: str, options: dict) -> str:
        raise NotImplementedError("generate_svg in Task 6")
