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
)
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
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
        self._pen_color = pen_color
        self._pen_width = pen_width
        self._fill_enabled = fill_enabled
        self._fill_color = fill_color
        self.setPen(QPen(pen_color, pen_width))
        if fill_enabled and fill_color:
            self.setBrush(QBrush(fill_color))
        else:
            self.setBrush(Qt.BrushStyle.NoBrush)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setZValue(10)

    def set_pen_color(self, color: QColor) -> None:
        self._pen_color = color
        self.setPen(QPen(color, self._pen_width))

    def set_pen_width(self, width: int) -> None:
        self._pen_width = width
        self.setPen(QPen(self._pen_color, width))

    def set_fill_enabled(self, enabled: bool, color: QColor | None = None) -> None:
        self._fill_enabled = enabled
        if enabled and color:
            self._fill_color = color
            self.setBrush(QBrush(color))
        else:
            self.setBrush(Qt.BrushStyle.NoBrush)


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
        self._pen_color = pen_color
        self._pen_width = pen_width
        self._fill_enabled = fill_enabled
        self._fill_color = fill_color
        self.setPen(QPen(pen_color, pen_width))
        if fill_enabled and fill_color:
            self.setBrush(QBrush(fill_color))
        else:
            self.setBrush(Qt.BrushStyle.NoBrush)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setZValue(10)

    def set_pen_color(self, color: QColor) -> None:
        self._pen_color = color
        self.setPen(QPen(color, self._pen_width))

    def set_pen_width(self, width: int) -> None:
        self._pen_width = width
        self.setPen(QPen(self._pen_color, width))

    def set_fill_enabled(self, enabled: bool, color: QColor | None = None) -> None:
        self._fill_enabled = enabled
        if enabled and color:
            self._fill_color = color
            self.setBrush(QBrush(color))
        else:
            self.setBrush(Qt.BrushStyle.NoBrush)


class ArrowAnnotation(QGraphicsPathItem):
    """箭头标注

    改进特性：
    - 箭头头部大小与线宽成比例
    - 更清晰的箭头样式（带凹陷的三角形）
    - 选中时显示端点手柄
    """

    # 箭头头部比例（头部长度 = 线宽 * 比例）
    ARROW_HEAD_RATIO = 4.0
    # 箭头头部最小尺寸
    ARROW_HEAD_MIN = 12
    # 箭头头部宽度比例（相对于长度）
    ARROW_HEAD_WIDTH_RATIO = 0.5

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
        self.setPen(
            QPen(
                pen_color,
                pen_width,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        self.setBrush(QBrush(pen_color))
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setZValue(10)
        self._update_path()

    def _update_path(self) -> None:
        """更新箭头路径（改进样式）"""
        path = QPainterPath()

        # 计算箭头方向
        dx = self._end.x() - self._start.x()
        dy = self._end.y() - self._start.y()
        length = math.sqrt(dx * dx + dy * dy)
        if length < 1:
            path.moveTo(self._start)
            path.lineTo(self._end)
            self.setPath(path)
            return

        # 箭头方向单位向量
        ux = dx / length
        uy = dy / length

        # 垂直方向单位向量
        px = -uy
        py = ux

        # 箭头头部大小（与线宽成比例，但有最小值）
        head_length = max(self.ARROW_HEAD_MIN, self._pen_width * self.ARROW_HEAD_RATIO)
        head_width = head_length * self.ARROW_HEAD_WIDTH_RATIO

        # 箭头头部的基点（从终点向回退 head_length）
        base_x = self._end.x() - head_length * ux
        base_y = self._end.y() - head_length * uy

        # 绘制线条（从起点到箭头头部基点）
        path.moveTo(self._start)
        path.lineTo(QPointF(base_x, base_y))

        # 箭头头部两侧点
        arrow_p1 = QPointF(
            base_x + head_width * px,
            base_y + head_width * py,
        )
        arrow_p2 = QPointF(
            base_x - head_width * px,
            base_y - head_width * py,
        )

        # 箭头凹陷点（使箭头更锐利）
        indent_depth = head_length * 0.25
        indent_point = QPointF(
            base_x + indent_depth * ux,
            base_y + indent_depth * uy,
        )

        # 绘制箭头头部（带凹陷的三角形）
        arrow_head = QPolygonF(
            [
                self._end,  # 箭头尖端
                arrow_p1,  # 左侧点
                indent_point,  # 凹陷点
                arrow_p2,  # 右侧点
            ]
        )
        path.addPolygon(arrow_head)
        path.closeSubpath()
        self.setPath(path)

    def set_end(self, end: QPointF) -> None:
        """更新终点（拖动绘制时使用）"""
        self._end = end
        self._update_path()

    def set_pen_color(self, color: QColor) -> None:
        """设置颜色"""
        self._pen_color = color
        self.setPen(
            QPen(
                color,
                self._pen_width,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        self.setBrush(QBrush(color))
        self.update()

    def set_pen_width(self, width: int) -> None:
        """设置线宽"""
        self._pen_width = width
        self.setPen(
            QPen(
                self._pen_color,
                width,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        self._update_path()
        self.update()


class TextAnnotation(QGraphicsTextItem):
    """文字标注

    增强功能：
    - 选中时显示边框和调整手柄
    - 支持字体、字号、颜色的动态修改
    - 单击选中，双击编辑
    """

    # 选中边框颜色
    SELECTION_COLOR = QColor(0, 120, 215)
    # 手柄大小
    HANDLE_SIZE = 6

    def __init__(
        self,
        text: str = "",
        pos: QPointF = QPointF(0, 0),
        font: QFont | None = None,
        color: QColor = QColor(255, 0, 0),
    ):
        super().__init__(text)
        self.setPos(pos)
        self._text_color = color
        self.setDefaultTextColor(color)
        if font:
            self.setFont(font)
        else:
            default_font = QFont("Microsoft YaHei", 14)
            self.setFont(default_font)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setZValue(10)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,  # type: ignore[override]
    ) -> None:
        """绘制文字和选中边框"""
        # 绘制文字内容
        super().paint(painter, option, widget)  # type: ignore[arg-type]

        # 选中时绘制边框
        if self.isSelected():
            rect = self.boundingRect()
            painter.setPen(QPen(self.SELECTION_COLOR, 2, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)

            # 绘制四个角的调整手柄
            handle_size = self.HANDLE_SIZE
            half_handle = handle_size / 2
            painter.setPen(QPen(self.SELECTION_COLOR, 1))
            painter.setBrush(QBrush(QColor(255, 255, 255)))

            # 四个角
            corners = [
                QPointF(rect.left() - half_handle, rect.top() - half_handle),
                QPointF(rect.right() - half_handle, rect.top() - half_handle),
                QPointF(rect.left() - half_handle, rect.bottom() - half_handle),
                QPointF(rect.right() - half_handle, rect.bottom() - half_handle),
            ]
            for corner in corners:
                painter.drawRect(
                    QRectF(corner.x(), corner.y(), handle_size, handle_size)
                )

    def enable_editing(self) -> None:
        """启用文字编辑模式"""
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.setFocus()

    def disable_editing(self) -> None:
        """禁用文字编辑模式"""
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        cursor = self.textCursor()
        cursor.clearSelection()
        self.setTextCursor(cursor)

    def set_font_family(self, family: str) -> None:
        """设置字体族"""
        font = self.font()
        font.setFamily(family)
        self.setFont(font)

    def set_font_size(self, size: int) -> None:
        """设置字号"""
        font = self.font()
        font.setPointSize(size)
        self.setFont(font)

    def set_text_color(self, color: QColor) -> None:
        """设置文字颜色"""
        self._text_color = color
        self.setDefaultTextColor(color)

    def set_bold(self, bold: bool) -> None:
        """设置粗体"""
        font = self.font()
        font.setBold(bold)
        self.setFont(font)

    def set_italic(self, italic: bool) -> None:
        """设置斜体"""
        font = self.font()
        font.setItalic(italic)
        self.setFont(font)

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

    对背景截图的指定区域进行高质量马赛克处理。
    使用块平均色算法，直接操作像素以获得更好的效果。
    """

    def __init__(
        self,
        rect: QRectF,
        background_pixmap: QPixmap,
        strength: int = 10,
    ):
        super().__init__(rect)
        self._background_pixmap = background_pixmap
        self._strength = max(4, strength)  # 最小块大小为4像素
        self._cached_mosaic: QPixmap | None = None
        self._resizing = False
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setBrush(Qt.BrushStyle.NoBrush)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setZValue(5)  # 在普通标注下面
        self._generate_mosaic()

    def set_resizing(self, resizing: bool) -> None:
        """设置正在调整大小状态。

        调整大小时显示占位矩形，结束后重新生成马赛克效果。
        """
        self._resizing = resizing
        if not resizing:
            self._generate_mosaic()
        self.update()

    def regenerate(self) -> None:
        """重新生成马赛克效果（调整大小结束后调用）。"""
        self._generate_mosaic()
        self.update()

    def set_strength(self, strength: int) -> None:
        """动态设置马赛克强度"""
        self._strength = max(4, strength)
        self._generate_mosaic()
        self.update()

    def _generate_mosaic(self) -> None:
        """使用块平均色算法生成高质量马赛克效果"""
        rect = self.rect()
        if rect.isEmpty():
            return

        # 从背景截图中复制对应区域
        src_rect = rect.toRect()
        valid = QRectF(
            0,
            0,
            self._background_pixmap.width(),
            self._background_pixmap.height(),
        )
        src_rect = rect.intersected(valid).toRect()

        if src_rect.isEmpty():
            return

        region = self._background_pixmap.copy(src_rect)

        # 转换为 QImage 进行像素级操作
        img = region.toImage()
        if img.isNull():
            return

        block_size = self._strength
        width = img.width()
        height = img.height()

        # 遍历每个块，计算平均色并填充
        for by in range(0, height, block_size):
            for bx in range(0, width, block_size):
                # 计算当前块的实际范围
                block_w = min(block_size, width - bx)
                block_h = min(block_size, height - by)

                # 计算块内平均颜色
                avg_color = self._calc_block_average(img, bx, by, block_w, block_h)

                # 用平均色填充整个块
                self._fill_block(img, bx, by, block_w, block_h, avg_color)

        self._cached_mosaic = QPixmap.fromImage(img)

    def _calc_block_average(self, img, x: int, y: int, w: int, h: int) -> QColor:
        """计算图像块内的平均颜色"""
        total_r, total_g, total_b = 0, 0, 0
        pixel_count = 0

        for py in range(y, y + h):
            for px in range(x, x + w):
                if px < img.width() and py < img.height():
                    color = img.pixelColor(px, py)
                    total_r += color.red()
                    total_g += color.green()
                    total_b += color.blue()
                    pixel_count += 1

        if pixel_count == 0:
            return QColor(0, 0, 0)

        return QColor(
            total_r // pixel_count,
            total_g // pixel_count,
            total_b // pixel_count,
        )

    def _fill_block(self, img, x: int, y: int, w: int, h: int, color: QColor) -> None:
        """用指定颜色填充图像块"""
        for py in range(y, y + h):
            for px in range(x, x + w):
                if px < img.width() and py < img.height():
                    img.setPixelColor(px, py, color)

    def update_background(self, pixmap: QPixmap) -> None:
        self._background_pixmap = pixmap
        self._generate_mosaic()
        self.update()

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        if self._resizing:
            painter.fillRect(self.rect(), QColor(100, 100, 100, 80))
        elif self._cached_mosaic:
            painter.drawPixmap(self.rect().toRect(), self._cached_mosaic)
        if self.isSelected():
            pen = QPen(QColor(0, 120, 215), 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.rect())


class BlurItem(QGraphicsRectItem):
    """模糊标注

    对背景截图的指定区域进行高质量模糊处理。
    使用多级缩放算法实现更平滑的模糊效果。
    """

    def __init__(
        self,
        rect: QRectF,
        background_pixmap: QPixmap,
        radius: int = 10,
    ):
        super().__init__(rect)
        self._background_pixmap = background_pixmap
        self._radius = max(4, radius)  # 最小模糊半径为4
        self._cached_blur: QPixmap | None = None
        self._resizing = False
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setBrush(Qt.BrushStyle.NoBrush)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setZValue(5)
        self._generate_blur()

    def set_resizing(self, resizing: bool) -> None:
        """设置正在调整大小状态。

        调整大小时显示占位矩形，结束后重新生成模糊效果。
        """
        self._resizing = resizing
        if not resizing:
            self._generate_blur()
        self.update()

    def regenerate(self) -> None:
        """重新生成模糊效果（调整大小结束后调用）。"""
        self._generate_blur()
        self.update()

    def set_radius(self, radius: int) -> None:
        """动态设置模糊半径"""
        self._radius = max(4, radius)
        self._generate_blur()
        self.update()

    def _generate_blur(self) -> None:
        """使用多级缩放实现高质量模糊效果

        通过多次缩小-放大迭代，产生更自然的模糊效果，
        避免单次大幅缩放导致的像素化问题。
        """
        rect = self.rect()
        if rect.isEmpty():
            return

        src_rect = rect.toRect()
        valid = QRectF(
            0,
            0,
            self._background_pixmap.width(),
            self._background_pixmap.height(),
        )
        src_rect = rect.intersected(valid).toRect()

        if src_rect.isEmpty():
            return

        region = self._background_pixmap.copy(src_rect)
        original_w = region.width()
        original_h = region.height()

        if original_w < 2 or original_h < 2:
            self._cached_blur = region
            return

        # 多级缩放实现更平滑的模糊
        # 迭代次数基于模糊半径，每次缩放到50%
        iterations = max(1, self._radius // 8)
        scale_factor = 0.5

        temp = region
        current_w = original_w
        current_h = original_h

        for _ in range(iterations):
            # 缩小
            new_w = max(4, int(current_w * scale_factor))
            new_h = max(4, int(current_h * scale_factor))

            small = temp.scaled(
                new_w,
                new_h,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

            # 放大回当前尺寸
            temp = small.scaled(
                current_w,
                current_h,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

        # 最后确保尺寸正确
        if temp.width() != original_w or temp.height() != original_h:
            temp = temp.scaled(
                original_w,
                original_h,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

        self._cached_blur = temp

    def update_background(self, pixmap: QPixmap) -> None:
        self._background_pixmap = pixmap
        self._generate_blur()
        self.update()

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        if self._resizing:
            painter.fillRect(self.rect(), QColor(100, 100, 100, 80))
        elif self._cached_blur:
            painter.drawPixmap(self.rect().toRect(), self._cached_blur)
        if self.isSelected():
            pen = QPen(QColor(0, 120, 215), 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.rect())
