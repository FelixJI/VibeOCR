"""内联编辑工具栏

毛玻璃浅色主题的浮动工具栏，包含工具按钮、属性条和操作按钮。
所有按钮使用 Unicode 图标 + tooltip 显示。
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QToolButton,
    QWidget,
)

from vibeocr.core.inline_styles import InlineStyles
from vibeocr.widgets.editor.annotation_items import EditTool
from vibeocr.widgets.editor.tool_properties_bar import ToolPropertiesBar

# 工具按钮定义：(icon, tooltip, EditTool)
_TOOL_DEFS: list[tuple[str, str, EditTool]] = [
    ("✦", "马赛克", EditTool.MOSAIC),     # ✦
    ("◎", "模糊", EditTool.BLUR),         # ◎
    ("⮒", "框选裁剪", EditTool.CROP),     # ⬒
    ("□", "矩形", EditTool.RECT),         # □
    ("○", "圆形", EditTool.ELLIPSE),       # ○
    ("→", "箭头", EditTool.ARROW),         # →
    ("T", "文字", EditTool.TEXT),               # T
]


class InlineToolbar(QWidget):
    """内联编辑工具栏（毛玻璃浅色主题）

    Signals:
        tool_changed(EditTool): 当前工具切换
        undo_requested(): 撤销请求
        redo_requested(): 重做请求
        save_requested(): 另存为请求
        copy_requested(): 复制请求
        confirm_requested(): 确认识别请求
        cancel_requested(): 取消请求
    """

    tool_changed = Signal(object)   # EditTool
    undo_requested = Signal()
    redo_requested = Signal()
    save_requested = Signal()
    copy_requested = Signal()
    confirm_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("inlineToolbar")
        self.setFixedHeight(InlineStyles.TOOLBAR_HEIGHT)
        self.setStyleSheet(InlineStyles.panel_style())

        self._current_tool: EditTool | None = None

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)

        # 工具按钮组（exclusive）
        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)

        tool_style = InlineStyles.tool_button_style()

        self._tool_buttons: dict[EditTool, QToolButton] = {}
        for icon, tooltip, tool in _TOOL_DEFS:
            btn = QToolButton()
            btn.setText(icon)
            btn.setToolTip(tooltip)
            btn.setCheckable(True)
            btn.setStyleSheet(tool_style)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._tool_group.addButton(btn)
            self._tool_buttons[tool] = btn
            layout.addWidget(btn)

        # 分隔线
        layout.addWidget(self._create_separator())

        # 工具属性条
        self._properties_bar = ToolPropertiesBar()
        layout.addWidget(self._properties_bar)

        # 弹性空间
        layout.addStretch()

        # 操作按钮样式
        action_style = InlineStyles.action_button_style()

        # 撤销
        self._btn_undo = QPushButton("↩")
        self._btn_undo.setToolTip("撤销")
        self._btn_undo.setStyleSheet(action_style)
        self._btn_undo.setEnabled(False)
        self._btn_undo.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self._btn_undo)

        # 重做
        self._btn_redo = QPushButton("↪")
        self._btn_redo.setToolTip("重做")
        self._btn_redo.setStyleSheet(action_style)
        self._btn_redo.setEnabled(False)
        self._btn_redo.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self._btn_redo)

        # 分隔线
        layout.addWidget(self._create_separator())

        # 另存为
        self._btn_save = QPushButton("\U0001F4BE")
        self._btn_save.setToolTip("另存为")
        self._btn_save.setStyleSheet(action_style)
        self._btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self._btn_save)

        # 复制
        self._btn_copy = QPushButton("\U0001F4CB")
        self._btn_copy.setToolTip("复制")
        self._btn_copy.setStyleSheet(action_style)
        self._btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self._btn_copy)

        # 确认识别
        self._btn_confirm = QPushButton("✓")
        self._btn_confirm.setToolTip("确认识别")
        self._btn_confirm.setStyleSheet(InlineStyles.confirm_button_style())
        self._btn_confirm.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self._btn_confirm)

        # 取消
        self._btn_cancel = QPushButton("✕")
        self._btn_cancel.setToolTip("取消")
        self._btn_cancel.setStyleSheet(InlineStyles.cancel_button_style())
        self._btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self._btn_cancel)

    def _create_separator(self) -> QFrame:
        """创建垂直分隔线"""
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(f"color: {InlineStyles.SEPARATOR_COLOR};")
        return sep

    def _connect_signals(self) -> None:
        # 工具按钮
        for tool, btn in self._tool_buttons.items():
            btn.clicked.connect(lambda checked, t=tool: self._on_tool_clicked(t))

        # 操作按钮
        self._btn_undo.clicked.connect(self.undo_requested.emit)
        self._btn_redo.clicked.connect(self.redo_requested.emit)
        self._btn_save.clicked.connect(self.save_requested.emit)
        self._btn_copy.clicked.connect(self.copy_requested.emit)
        self._btn_confirm.clicked.connect(self.confirm_requested.emit)
        self._btn_cancel.clicked.connect(self.cancel_requested.emit)

    def _on_tool_clicked(self, tool: EditTool) -> None:
        """工具按钮点击处理"""
        self._current_tool = tool
        self._properties_bar.update_for_tool(tool)
        self.tool_changed.emit(tool)

    @property
    def properties_bar(self) -> ToolPropertiesBar:
        """返回工具属性条"""
        return self._properties_bar

    def set_undo_enabled(self, enabled: bool) -> None:
        """设置撤销按钮是否可用"""
        self._btn_undo.setEnabled(enabled)

    def set_redo_enabled(self, enabled: bool) -> None:
        """设置重做按钮是否可用"""
        self._btn_redo.setEnabled(enabled)
