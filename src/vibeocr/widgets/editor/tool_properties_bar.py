"""工具属性条

根据当前工具动态切换显示颜色、线宽、填充、字体等属性控件。
"""

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QFontComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QWidget,
)

from PySide6.QtCore import Qt

from vibeocr.core.editor_styles import EditorStyles
from vibeocr.widgets.editor.annotation_items import EditTool


class ToolPropertiesBar(QWidget):
    """工具属性条"""

    color_changed = Signal(QColor)
    line_width_changed = Signal(int)
    fill_enabled_changed = Signal(bool)
    font_changed = Signal(QFont)
    font_size_changed = Signal(int)
    mosaic_strength_changed = Signal(int)
    blur_radius_changed = Signal(int)

    # 面板索引
    _EMPTY_PAGE = 0
    _SHAPE_PAGE = 1
    _TEXT_PAGE = 2
    _MOSAIC_PAGE = 3
    _BLUR_PAGE = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("propertiesBar")
        self.setStyleSheet(EditorStyles.properties_bar_style())

        self._current_color = QColor(255, 0, 0)
        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._stack = QStackedWidget()
        layout.addWidget(self._stack)

        # 页面 0：空白（SELECT/CROP 等无属性工具）
        self._stack.addWidget(QWidget())

        # 页面 1：图形属性（矩形/圆/箭头）
        self._stack.addWidget(self._create_shape_page())

        # 页面 2：文字属性
        self._stack.addWidget(self._create_text_page())

        # 页面 3：马赛克属性
        self._stack.addWidget(self._create_mosaic_page())

        # 页面 4：模糊属性
        self._stack.addWidget(self._create_blur_page())

    def _create_color_button(self) -> QPushButton:
        """创建颜色选择按钮"""
        btn = QPushButton()
        btn.setFixedSize(24, 24)
        btn.setStyleSheet(
            f"QPushButton {{ background-color: {self._current_color.name()}; "
            f"border: 1px solid #666; border-radius: 3px; }}"
        )
        btn.clicked.connect(self._on_color_pick)
        return btn

    def _create_shape_page(self) -> QWidget:
        """创建图形属性页"""
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(8)

        # 颜色
        layout.addWidget(QLabel("颜色"))
        self._shape_color_btn = self._create_color_button()
        layout.addWidget(self._shape_color_btn)

        # 线宽
        layout.addWidget(QLabel("线宽"))
        self._line_width_spin = QSpinBox()
        self._line_width_spin.setRange(1, 10)
        self._line_width_spin.setValue(2)
        layout.addWidget(self._line_width_spin)

        # 填充
        self._fill_cb = QCheckBox("填充")
        layout.addWidget(self._fill_cb)

        return page

    def _create_text_page(self) -> QWidget:
        """创建文字属性页"""
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(8)

        # 颜色
        layout.addWidget(QLabel("颜色"))
        self._text_color_btn = self._create_color_button()
        layout.addWidget(self._text_color_btn)

        # 字体
        layout.addWidget(QLabel("字体"))
        self._font_combo = QFontComboBox()
        self._font_combo.setCurrentFont(QFont("Microsoft YaHei"))
        layout.addWidget(self._font_combo)

        # 字号
        layout.addWidget(QLabel("字号"))
        self._font_size_spin = QSpinBox()
        self._font_size_spin.setRange(8, 72)
        self._font_size_spin.setValue(14)
        layout.addWidget(self._font_size_spin)

        return page

    def _create_mosaic_page(self) -> QWidget:
        """创建马赛克属性页"""
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(8)

        layout.addWidget(QLabel("强度"))
        self._mosaic_slider = QSlider(Qt.Orientation.Horizontal)
        self._mosaic_slider.setRange(2, 20)
        self._mosaic_slider.setValue(10)
        self._mosaic_slider.setFixedWidth(100)
        layout.addWidget(self._mosaic_slider)

        self._mosaic_label = QLabel("10")
        layout.addWidget(self._mosaic_label)

        return page

    def _create_blur_page(self) -> QWidget:
        """创建模糊属性页"""
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(8)

        layout.addWidget(QLabel("半径"))
        self._blur_slider = QSlider(Qt.Orientation.Horizontal)
        self._blur_slider.setRange(2, 30)
        self._blur_slider.setValue(10)
        self._blur_slider.setFixedWidth(100)
        layout.addWidget(self._blur_slider)

        self._blur_label = QLabel("10")
        layout.addWidget(self._blur_label)

        return page

    def _connect_signals(self) -> None:
        self._line_width_spin.valueChanged.connect(self.line_width_changed.emit)
        self._fill_cb.toggled.connect(self.fill_enabled_changed.emit)
        self._font_combo.currentFontChanged.connect(self.font_changed.emit)
        self._font_size_spin.valueChanged.connect(self.font_size_changed.emit)

        self._mosaic_slider.valueChanged.connect(self._on_mosaic_changed)
        self._blur_slider.valueChanged.connect(self._on_blur_changed)

    def _on_color_pick(self) -> None:
        """打开颜色选择对话框"""
        color = QColorDialog.getColor(
            self._current_color, self, "选择颜色"
        )
        if color.isValid():
            self._current_color = color
            self._update_color_buttons()
            self.color_changed.emit(color)

    def _update_color_buttons(self) -> None:
        """更新所有颜色按钮的背景色"""
        style = (
            f"QPushButton {{ background-color: {self._current_color.name()}; "
            f"border: 1px solid #666; border-radius: 3px; }}"
        )
        if hasattr(self, '_shape_color_btn'):
            self._shape_color_btn.setStyleSheet(style)
        if hasattr(self, '_text_color_btn'):
            self._text_color_btn.setStyleSheet(style)

    def _on_mosaic_changed(self, value: int) -> None:
        self._mosaic_label.setText(str(value))
        self.mosaic_strength_changed.emit(value)

    def _on_blur_changed(self, value: int) -> None:
        self._blur_label.setText(str(value))
        self.blur_radius_changed.emit(value)

    def update_for_tool(self, tool: EditTool) -> None:
        """根据工具切换属性面板"""
        if tool in (EditTool.RECT, EditTool.ELLIPSE, EditTool.ARROW):
            self._stack.setCurrentIndex(self._SHAPE_PAGE)
        elif tool == EditTool.TEXT:
            self._stack.setCurrentIndex(self._TEXT_PAGE)
        elif tool == EditTool.MOSAIC:
            self._stack.setCurrentIndex(self._MOSAIC_PAGE)
        elif tool == EditTool.BLUR:
            self._stack.setCurrentIndex(self._BLUR_PAGE)
        else:
            self._stack.setCurrentIndex(self._EMPTY_PAGE)
