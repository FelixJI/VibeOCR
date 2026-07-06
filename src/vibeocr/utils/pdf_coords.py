"""PDF 坐标变换纯函数(无 fitz 依赖)。

主进程 PDF 预览窗口渲染文字层高亮时,需要把 bbox(PDF points 或归一化坐标)
转换为渲染图像的像素坐标。此模块从 ``pdf_service.py`` 抽出,使主进程不再触发
``pdf_service`` 的顶层 ``import fitz``,从而 fitz 可从主 exe 排除(子进程仍用)。

设计要点:
- 纯数学,不 import fitz/numpy/Qt,任意进程均可安全加载。
- ``page_rect`` 同时支持 4-tuple ``(x0,y0,x1,y1)`` 与带 ``.width/.height`` 的对象
  (兼容遗留调用方可能传入的 ``fitz.Rect``,虽然主进程统一用 tuple)。
"""

from __future__ import annotations


def bbox_to_pixel(
    bbox: tuple[float, float, float, float],
    page_rect: tuple[float, float, float, float],
    render_dpi: int,
    source: str = "pdf",
) -> tuple[float, float, float, float]:
    """将 bbox 转换为渲染图像的像素坐标。

    Args:
        bbox: 输入 bbox。
        page_rect: PDF 页面矩形 (points)。接受 4-tuple ``(x0,y0,x1,y1)`` 或
            带 ``.width/.height`` 属性的对象(兼容 ``fitz.Rect``,主进程统一用 tuple)。
        render_dpi: 渲染 DPI。
        source: ``"pdf"`` 表示 bbox 是 PDF points 坐标,
                ``"normalized"`` 表示 ``[0, 1000]`` 归一化坐标。

    Returns:
        像素坐标 ``(x0, y0, x1, y1)``。
    """
    # 兼容 fitz.Rect(有 .width/.height)与 4-tuple (x0,y0,x1,y1)
    pw = getattr(page_rect, "width", None)
    if pw is None:
        pw = page_rect[2] - page_rect[0]
        ph = page_rect[3] - page_rect[1]
    else:
        ph = page_rect.height  # type: ignore[attr-defined]
    if source == "normalized":
        # 先转为 PDF points
        x0 = bbox[0] / 1000 * pw
        y0 = bbox[1] / 1000 * ph
        x1 = bbox[2] / 1000 * pw
        y1 = bbox[3] / 1000 * ph
    else:
        x0, y0, x1, y1 = bbox

    # PDF points → pixels: coord / 72 * dpi
    scale = render_dpi / 72.0
    return (x0 * scale, y0 * scale, x1 * scale, y1 * scale)
