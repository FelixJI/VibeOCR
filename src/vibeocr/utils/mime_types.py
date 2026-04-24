"""共享 MIME 类型映射和文件过滤器

单一来源（Single Source of Truth），所有 MIME 类型映射和文件对话框过滤器统一定义。
"""

from __future__ import annotations

from pathlib import Path

# 扩展名（小写，带点） → MIME 类型
EXT_TO_MIME: dict[str, str] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".jp2": "image/jp2",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

# MIME → 扩展名（反向映射，取第一个匹配的）
_MIME_TO_EXT: dict[str, str] = {v: k for k, v in EXT_TO_MIME.items()}


def extension_to_mime(ext: str) -> str | None:
    """扩展名 → MIME 类型。ext 需带前导点，如 '.png'。"""
    return EXT_TO_MIME.get(ext.lower())


def mime_to_extension(mime: str) -> str | None:
    """MIME 类型 → 扩展名（带前导点）。"""
    return _MIME_TO_EXT.get(mime)


# 文件对话框过滤器
FILE_FILTER_IMAGES = "图片 (*.png *.jpg *.jpeg *.bmp *.gif *.tiff *.tif *.webp *.jp2)"
FILE_FILTER_DOCUMENTS = "文档 (*.pdf *.docx *.pptx *.xlsx)"
FILE_FILTER_ALL = (
    "所有支持的格式 (*.png *.jpg *.jpeg *.bmp *.gif *.tiff *.tif *.webp *.jp2"
    " *.pdf *.docx *.pptx *.xlsx)"
)


def guess_mime_from_filename(filename: str) -> str:
    """从文件名猜测 MIME 类型，未知时默认 application/pdf。"""
    suffix = Path(filename).suffix.lower()
    return EXT_TO_MIME.get(suffix, "application/pdf")


def is_office_file(path_or_name: str) -> bool:
    """判断文件是否为 Office 文档（docx/pptx/xlsx）。"""
    suffix = Path(path_or_name).suffix.lower()
    return suffix in {".docx", ".pptx", ".xlsx"}
