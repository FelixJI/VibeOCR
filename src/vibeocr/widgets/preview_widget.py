"""Preview widget for image display and screenshot trigger"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QPixmap
from PySide6.QtWidgets import QLabel, QMenu, QVBoxLayout, QWidget


class PreviewWidget(QWidget):
    """图片预览组件，无图片时点击可触发截图或选择文件"""

    screenshot_requested = Signal()  # 请求截图信号
    file_open_requested = Signal()  # 请求打开文件信号
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
        self._image_label.setText("左键点击截图 · 右键点击选择文件\n\n支持图片、PDF 格式")
        self._image_label.setWordWrap(True)
        self._image_label.mousePressEvent = self._on_label_click  # type: ignore[method-assign]
        self._image_label.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self._image_label.customContextMenuRequested.connect(
            self._on_context_menu
        )

        layout.addWidget(self._image_label)

    def _on_label_click(self, event) -> None:
        """点击标签时：左键截图，右键选择文件"""
        if self._pixmap is None:
            if event.button() == Qt.MouseButton.LeftButton:
                self.screenshot_requested.emit()
            elif event.button() == Qt.MouseButton.RightButton:
                self.file_open_requested.emit()

    def _on_context_menu(self, pos) -> None:
        """右键上下文菜单"""
        if self._pixmap is not None:
            return
        menu = QMenu(self._image_label)
        action_screenshot = QAction("截图识别", menu)
        action_open_file = QAction("选择文件（图片/PDF）", menu)
        action_screenshot.triggered.connect(self.screenshot_requested.emit)
        action_open_file.triggered.connect(self.file_open_requested.emit)
        menu.addAction(action_screenshot)
        menu.addAction(action_open_file)
        menu.exec(self._image_label.mapToGlobal(pos))

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
        self._image_label.setText("左键点击截图 · 右键点击选择文件\n\n支持图片、PDF 格式")
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
