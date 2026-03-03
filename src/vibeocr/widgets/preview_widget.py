"""Preview widget for image display and screenshot trigger"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class PreviewWidget(QWidget):
    """图片预览组件，无图片时点击可触发截图"""

    screenshot_requested = Signal()  # 请求截图信号
    image_changed = Signal()  # 图片改变信号（可用于启用/禁用复制按钮等）

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        """设置UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 图片显示标签
        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setMinimumSize(300, 200)
        self._image_label.setStyleSheet(
            "QLabel { background-color: #f0f0f0; border: 2px dashed #ccc; }"
        )
        self._image_label.setText("点击此处或按 Ctrl+S 截图\n\n支持打开图片文件")
        self._image_label.setWordWrap(True)
        self._image_label.mousePressEvent = self._on_label_click

        layout.addWidget(self._image_label)

    def _on_label_click(self, event) -> None:
        """点击标签时触发截图"""
        if self._pixmap is None:
            self.screenshot_requested.emit()

    def set_pixmap(self, pixmap: QPixmap) -> None:
        """设置预览图片"""
        self._pixmap = pixmap
        self._update_display()
        self.image_changed.emit()

    def pixmap(self) -> QPixmap | None:
        """获取当前图片"""
        return self._pixmap

    def clear(self) -> None:
        """清除图片"""
        self._pixmap = None
        self._image_label.clear()
        self._image_label.setText("点击此处或按 Ctrl+S 截图\n\n支持打开图片文件")
        self._image_label.setStyleSheet(
            "QLabel { background-color: #f0f0f0; border: 2px dashed #ccc; }"
        )
        self.image_changed.emit()

    def _update_display(self) -> None:
        """更新图片显示"""
        if self._pixmap:
            # 获取 label 的物理像素尺寸（考虑高DPI）
            label_size = self._image_label.size()
            dpr = self._image_label.devicePixelRatio()
            physical_size = label_size * dpr

            scaled = self._pixmap.scaled(
                physical_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            # 设置缩放后图片的设备像素比，确保在高DPI下正确显示
            scaled.setDevicePixelRatio(dpr)
            self._image_label.setPixmap(scaled)
            self._image_label.setStyleSheet(
                "QLabel { background-color: #fff; border: 1px solid #ddd; }"
            )

    def resizeEvent(self, event) -> None:
        """窗口大小改变时重新缩放图片"""
        super().resizeEvent(event)
        if self._pixmap:
            self._update_display()
