# src/vibeocr/widgets/inline_toolbar.py
"""内联编辑工具栏

毛玻璃浅色主题的浮动工具栏，包含工具按钮、属性条和操作按钮。
所有按钮使用 Lucide SVG 图标 + tooltip 显示。
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QToolButton,
    QWidget,
)

from vibeocr.core.inline_styles import InlineStyles
from vibeocr.core.toolbar_icons import toolbar_icon
from vibeocr.widgets.editor.annotation_items import EditTool
from vibeocr.widgets.editor.tool_properties_bar import ToolPropertiesBar

# 工具按钮定义：(icon_name, tooltip, EditTool)
_TOOL_DEFS: list[tuple[str, str, EditTool]] = [
    ("mosaic", "马赛克", EditTool.MOSAIC),
    ("blur", "模糊", EditTool.BLUR),
    ("crop", "裁剪", EditTool.CROP),
    ("rect", "矩形", EditTool.RECT),
    ("ellipse", "椭圆", EditTool.ELLIPSE),
    ("arrow", "箭头", EditTool.ARROW),
    ("text", "文字", EditTool.TEXT),
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

    tool_changed = Signal(object)
    undo_requested = Signal()
    redo_requested = Signal()
    save_requested = Signal()
    copy_requested = Signal()
    confirm_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("inlineToolbar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(InlineStyles.TOOLBAR_HEIGHT)
        self.setStyleSheet(InlineStyles.panel_style())

        self._current_tool: EditTool | None = None

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(2)

        # 工具按钮组（exclusive）
        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)

        tool_style = InlineStyles.tool_button_style()

        self._tool_buttons: dict[EditTool, QToolButton] = {}
        for icon_name, tooltip, tool in _TOOL_DEFS:
            btn = QToolButton()
            btn.setIcon(toolbar_icon(icon_name))
            btn.setToolTip(tooltip)
            btn.setCheckable(True)
            btn.setStyleSheet(tool_style)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._tool_group.addButton(btn)
            self._tool_buttons[tool] = btn
            layout.addWidget(btn)

        # 属性区分隔线（初始隐藏）
        self._props_separator = self._create_separator()
        self._props_separator.hide()
        layout.addSpacing(6)
        layout.addWidget(self._props_separator)
        layout.addSpacing(6)

        # 工具属性条（初始隐藏）
        self._properties_bar = ToolPropertiesBar()
        self._properties_bar.hide()
        layout.addWidget(self._properties_bar)

        # 弹性空间
        layout.addStretch()

        # 操作按钮
        action_style = InlineStyles.action_button_style()

        # 撤销
        self._btn_undo = self._make_action_btn("undo", "撤销", action_style)
        self._btn_undo.setEnabled(False)
        layout.addWidget(self._btn_undo)

        # 重做
        self._btn_redo = self._make_action_btn("redo", "重做", action_style)
        self._btn_redo.setEnabled(False)
        layout.addWidget(self._btn_redo)

        # 分隔线
        layout.addSpacing(6)
        layout.addWidget(self._create_separator())
        layout.addSpacing(6)

        # 另存为
        self._btn_save = self._make_action_btn("save", "另存为", action_style)
        layout.addWidget(self._btn_save)

        # 复制
        self._btn_copy = self._make_action_btn("copy", "复制", action_style)
        layout.addWidget(self._btn_copy)

        # 确认识别
        self._btn_confirm = self._make_action_btn(
            "confirm", "确认识别", InlineStyles.confirm_button_style()
        )
        layout.addWidget(self._btn_confirm)

        # 取消
        self._btn_cancel = self._make_action_btn(
            "cancel", "取消", InlineStyles.cancel_button_style()
        )
        layout.addWidget(self._btn_cancel)

    def _make_action_btn(
        self, icon_name: str, tooltip: str, style: str
    ) -> QToolButton:
        btn = QToolButton()
        btn.setIcon(toolbar_icon(icon_name))
        btn.setToolTip(tooltip)
        btn.setStyleSheet(style)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        return btn

    def _create_separator(self) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(f"color: {InlineStyles.SEPARATOR_COLOR};")
        return sep

    def _connect_signals(self) -> None:
        for tool, btn in self._tool_buttons.items():
            btn.clicked.connect(lambda _, t=tool: self._on_tool_clicked(t))

        self._btn_undo.clicked.connect(self.undo_requested.emit)
        self._btn_redo.clicked.connect(self.redo_requested.emit)
        self._btn_save.clicked.connect(self.save_requested.emit)
        self._btn_copy.clicked.connect(self.copy_requested.emit)
        self._btn_confirm.clicked.connect(self.confirm_requested.emit)
        self._btn_cancel.clicked.connect(self.cancel_requested.emit)

    def _on_tool_clicked(self, tool: EditTool) -> None:
        self._current_tool = tool
        has_props = tool not in (EditTool.SELECT, EditTool.CROP)
        self._props_separator.setVisible(has_props)
        self._properties_bar.setVisible(has_props)
        if has_props:
            self._properties_bar.update_for_tool(tool)
        self.tool_changed.emit(tool)

    @property
    def properties_bar(self) -> ToolPropertiesBar:
        return self._properties_bar

    def set_undo_enabled(self, enabled: bool) -> None:
        self._btn_undo.setEnabled(enabled)

    def set_redo_enabled(self, enabled: bool) -> None:
        self._btn_redo.setEnabled(enabled)
