"""标注图形项和工具枚举

定义编辑器支持的所有标注图形项类型。
"""

from __future__ import annotations

import math
from enum import Enum

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QTransform,
)
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsTextItem,
    QStyleOptionGraphicsItem,
    QWidget,
)


class EditTool(Enum):
    """编辑工具枚举"""

    SELECT = "select"
    MOSAIC = "mosaic"
    BLUR = "blur"
    CROP = "crop"
    RECT = "rect"
    ELLIPSE = "ellipse"
    ARROW = "arrow"
    TEXT = "text"


class RectAnnotation(QGraphicsRectItem):
    """矩形标注"""

    def __init__(
        self,
        rect: QRectF,
        pen_color: QColor = QColor(255, 0, 0),
        pen_width: int = 2,
        fill_enabled: bool = False,
        fill_color: QColor | None = None,
    ):
        super().__init__(rect)
        pen = QPen(pen_color, pen_width)
        self.setPen(pen)
        if fill_enabled and fill_color:
            self.setBrush(QBrush(fill_color))
        else:
            self.setBrush(Qt.BrushStyle.NoBrush)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        )
        self.setZValue(10)


class EllipseAnnotation(QGraphicsEllipseItem):
    """椭圆标注"""

    def __init__(
        self,
        rect: QRectF,
        pen_color: QColor = QColor(255, 0, 0),
        pen_width: int = 2,
        fill_enabled: bool = False,
        fill_color: QColor | None = None,
    ):
        super().__init__(rect)
        pen = QPen(pen_color, pen_width)
        self.setPen(pen)
        if fill_enabled and fill_color:
            self.setBrush(QBrush(fill_color))
        else:
            self.setBrush(Qt.BrushStyle.NoBrush)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        )
        self.setZValue(10)


class ArrowAnnotation(QGraphicsPathItem):
    """箭头标注"""

    ARROW_HEAD_SIZE = 12

    def __init__(
        self,
        start: QPointF,
        end: QPointF,
        pen_color: QColor = QColor(255, 0, 0),
        pen_width: int = 2,
    ):
        super().__init__()
        self._start = start
        self._end = end
        self._pen_color = pen_color
        self._pen_width = pen_width
        self.setPen(QPen(pen_color, pen_width, Qt.PenStyle.SolidLine,
                         Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        self.setBrush(QBrush(pen_color))
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        )
        self.setZValue(10)
        self._update_path()

    def _update_path(self) -> None:
        """更新箭头路径"""
        path = QPainterPath()
        path.moveTo(self._start)
        path.lineTo(self._end)

        # 计算箭头头部
        dx = self._end.x() - self._start.x()
        dy = self._end.y() - self._start.y()
        length = math.sqrt(dx * dx + dy * dy)
        if length < 1:
            self.setPath(path)
            return

        # 箭头方向单位向量
        ux = dx / length
        uy = dy / length

        # 箭头头部大小
        head_size = self.ARROW_HEAD_SIZE

        # 箭头头部两个点
        # 垂直方向
        px = -uy
        py = ux

        arrow_p1 = QPointF(
            self._end.x() - head_size * ux + head_size * 0.4 * px,
            self._end.y() - head_size * uy + head_size * 0.4 * py,
        )
        arrow_p2 = QPointF(
            self._end.x() - head_size * ux - head_size * 0.4 * px,
            self._end.y() - head_size * uy - head_size * 0.4 * py,
        )

        arrow_head = QPolygonF([self._end, arrow_p1, arrow_p2])
        path.addPolygon(arrow_head)
        path.closeSubpath()
        self.setPath(path)

    def set_end(self, end: QPointF) -> None:
        """更新终点（拖动绘制时使用）"""
        self._end = end
        self._update_path()


class TextAnnotation(QGraphicsTextItem):
    """文字标注"""

    def __init__(
        self,
        text: str = "",
        pos: QPointF = QPointF(0, 0),
        font: QFont | None = None,
        color: QColor = QColor(255, 0, 0),
    ):
        super().__init__(text)
        self.setPos(pos)
        self.setDefaultTextColor(color)
        if font:
            self.setFont(font)
        else:
            default_font = QFont("Microsoft YaHei", 14)
            self.setFont(default_font)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        )
        self.setZValue(10)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

    def enable_editing(self) -> None:
        """启用文字编辑模式"""
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextEditorInteraction
        )
        self.setFocus()

    def disable_editing(self) -> None:
        """禁用文字编辑模式"""
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        cursor = self.textCursor()
        cursor.clearSelection()
        self.setTextCursor(cursor)

    def mouseDoubleClickEvent(self, event) -> None:
        """双击进入编辑模式"""
        self.enable_editing()
        super().mouseDoubleClickEvent(event)

    def focusOutEvent(self, event) -> None:
        """失去焦点退出编辑模式"""
        self.disable_editing()
        # 如果文字为空，可以考虑移除
        super().focusOutEvent(event)


class MosaicItem(QGraphicsRectItem):
    """马赛克标注

    对背景截图的指定区域进行马赛克处理。
    """

    def __init__(
        self,
        rect: QRectF,
        background_pixmap: QPixmap,
        strength: int = 10,
    ):
        super().__init__(rect)
        self._background_pixmap = background_pixmap
        self._strength = max(2, strength)
        self._cached_mosaic: QPixmap | None = None
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setBrush(Qt.BrushStyle.NoBrush)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        )
        self.setZValue(5)  # 在普通标注下面
        self._generate_mosaic()

    def _generate_mosaic(self) -> None:
        """生成马赛克效果的缓存图像"""
        rect = self.rect()
        if rect.isEmpty():
            return

        # 从背景截图中复制对应区域
        src_rect = rect.toRect()
        if (
            src_rect.x() < 0
            or src_rect.y() < 0
            or src_rect.right() > self._background_pixmap.width()
            or src_rect.bottom() > self._background_pixmap.height()
        ):
            # 裁剪到有效范围
            valid = QRectF(
                0, 0,
                self._background_pixmap.width(),
                self._background_pixmap.height(),
            )
            src_rect = rect.intersected(valid).toRect()

        if src_rect.isEmpty():
            return

        region = self._background_pixmap.copy(src_rect)

        # 马赛克效果：缩小到 1/strength 再放大回来（最近邻插值）
        small_w = max(1, region.width() // self._strength)
        small_h = max(1, region.height() // self._strength)

        small = region.scaled(
            small_w, small_h,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self._cached_mosaic = small.scaled(
            region.width(), region.height(),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        if self._cached_mosaic:
            painter.drawPixmap(self.rect().toRect(), self._cached_mosaic)
        # 选中时绘制边框
        if self.isSelected():
            pen = QPen(QColor(0, 120, 215), 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.rect())


class BlurItem(QGraphicsRectItem):
    """模糊标注

    对背景截图的指定区域进行高斯模糊处理。
    """

    def __init__(
        self,
        rect: QRectF,
        background_pixmap: QPixmap,
        radius: int = 10,
    ):
        super().__init__(rect)
        self._background_pixmap = background_pixmap
        self._radius = max(2, radius)
        self._cached_blur: QPixmap | None = None
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setBrush(Qt.BrushStyle.NoBrush)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        )
        self.setZValue(5)
        self._generate_blur()

    def _generate_blur(self) -> None:
        """生成模糊效果的缓存图像"""
        rect = self.rect()
        if rect.isEmpty():
            return

        src_rect = rect.toRect()
        valid = QRectF(
            0, 0,
            self._background_pixmap.width(),
            self._background_pixmap.height(),
        )
        src_rect = rect.intersected(valid).toRect()

        if src_rect.isEmpty():
            return

        region = self._background_pixmap.copy(src_rect)

        # 模糊效果：缩小再用平滑插值放大
        small_w = max(1, region.width() // self._radius)
        small_h = max(1, region.height() // self._radius)

        small = region.scaled(
            small_w, small_h,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._cached_blur = small.scaled(
            region.width(), region.height(),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        if self._cached_blur:
            painter.drawPixmap(self.rect().toRect(), self._cached_blur)
        if self.isSelected():
            pen = QPen(QColor(0, 120, 215), 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.rect())
