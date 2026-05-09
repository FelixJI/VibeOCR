# src/vibeocr/core/toolbar_icons.py
"""截图工具栏 SVG 图标资源

使用 Lucide 图标的 SVG 内容，通过 QSvgRenderer 渲染为 QIcon。
"""

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# 每个图标是完整的 SVG 字符串，基于 Lucide (https://lucide.dev)
# stroke="currentColor" 允许通过 QSvgRenderer 的 stylesheet 设置颜色
_SVG_DATA: dict[str, str] = {
    "mosaic": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<rect width="7" height="7" x="3" y="3" rx="1"/>'
        '<rect width="7" height="7" x="14" y="3" rx="1"/>'
        '<rect width="7" height="7" x="14" y="14" rx="1"/>'
        '<rect width="7" height="7" x="3" y="14" rx="1"/>'
        '</svg>'
    ),
    "blur": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M7 16.3c2.2 0 4-1.83 4-4.05 0-1.16-.57-2.26-1.71-3.19S7.29 6.75 '
        '7 5.3c-.29 1.45-1.14 2.84-2.29 3.76S3 11.1 3 12.25c0 2.22 1.8 4.05 4 4.05z"/>'
        '<path d="M12.56 14.69c1.64 0 2.97-1.37 2.97-3.02 0-.86-.42-1.68-1.27-2.37 '
        '-.85-.7-1.37-1.52-1.7-2.3-.33.78-.85 1.6-1.7 2.3-.85.69-1.27 1.51-1.27 2.37 '
        '0 1.65 1.33 3.02 2.97 3.02z"/>'
        '</svg>'
    ),
    "crop": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M6 2v14a2 2 0 0 0 2 2h14"/>'
        '<path d="M18 22V8a2 2 0 0 0-2-2H2"/>'
        '</svg>'
    ),
    "rect": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<rect width="18" height="18" x="3" y="3" rx="2"/>'
        '</svg>'
    ),
    "ellipse": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="12" r="10"/>'
        '</svg>'
    ),
    "arrow": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M5 12h14"/>'
        '<path d="m12 5 7 7-7 7"/>'
        '</svg>'
    ),
    "text": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<polyline points="4 7 4 4 20 4 20 7"/>'
        '<line x1="9" x2="15" y1="20" y2="20"/>'
        '<line x1="12" x2="12" y1="4" y2="20"/>'
        '</svg>'
    ),
    "undo": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M3 7v6h6"/>'
        '<path d="M21 17a9 9 0 0 0-9-9 9 9 0 0 0-6 2.3L3 13"/>'
        '</svg>'
    ),
    "redo": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M21 7v6h-6"/>'
        '<path d="M3 17a9 9 0 0 1 9-9 9 9 0 0 1 6 2.3L21 13"/>'
        '</svg>'
    ),
    "save": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M15.2 3a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/>'
        '<path d="M17 21v-7a1 1 0 0 0-1-1H8a1 1 0 0 0-1 1v7"/>'
        '<path d="M7 3v4a1 1 0 0 0 1 1h7"/>'
        '</svg>'
    ),
    "copy": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/>'
        '<path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>'
        '</svg>'
    ),
    "confirm": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M20 6 9 17l-5-5"/>'
        '</svg>'
    ),
    "cancel": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M18 6 6 18"/>'
        '<path d="m6 6 12 12"/>'
        '</svg>'
    ),
}

ICON_NAMES: tuple[str, ...] = tuple(_SVG_DATA.keys())


def toolbar_icon(name: str, size: int = 20) -> QIcon:
    """渲染指定名称的 SVG 图标为 QIcon。

    Args:
        name: 图标名称，必须是 ICON_NAMES 中的一个
        size: 图标像素尺寸（正方形）

    Returns:
        QIcon 实例

    Raises:
        KeyError: 名称不存在
    """
    svg_bytes = _SVG_DATA[name].encode("utf-8")
    renderer = QSvgRenderer(svg_bytes)
    if not renderer.isValid():
        raise ValueError(f"Invalid SVG data for icon '{name}'")
    pixmap = QPixmap(QSize(size, size))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)
