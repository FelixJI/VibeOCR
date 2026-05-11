# src/vibeocr/widgets/inline_toolbar.py
"""内联编辑工具栏

毛玻璃浅色主题的浮动工具栏，包含工具按钮、属性条和操作按钮。
所有按钮使用纯文字标签 + tooltip 显示。
"""

from PySide6.QtCore import QEvent, QObject, QPoint, QTimer, Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QWidget,
)

from vibeocr.core.inline_styles import InlineStyles
from vibeocr.widgets.editor.annotation_items import EditTool
from vibeocr.widgets.editor.tool_properties_bar import ToolPropertiesBar


class _TooltipManager(QObject):
    """自定义 tooltip 显示，绕过 WA_TranslucentBackground 继承问题。

    父窗口设置了 WA_TranslucentBackground 后，Qt 默认创建的 tooltip
    窗口会继承该属性，在 Windows 上渲染为黑色背景。此管理器拦截按钮的
    tooltip 事件，创建独立的 QLabel 窗口并显式关闭 WA_TranslucentBackground。
    """

    _TOOLTIP_STYLE = (
        "QLabel { background: #ffffe0; color: #000; border: 1px solid #aaa; "
        "padding: 4px 6px; font-size: 12px; border-radius: 2px; }"
    )

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._label: QLabel | None = None
        self._pending_text = ""
        self._pending_btn: QWidget | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._show_pending)

    def register(self, btn: QWidget) -> None:
        btn.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            return True
        if event.type() == QEvent.Type.Enter:
            text = obj.toolTip()
            if text:
                self._pending_text = text
                self._pending_btn = obj
                self._timer.start()
        elif event.type() == QEvent.Type.Leave:
            self._timer.stop()
            self._hide()
        elif event.type() == QEvent.Type.MouseButtonPress:
            self._hide()
        return False

    def _show_pending(self) -> None:
        if not self._pending_text:
            return
        self._hide()
        label = QLabel(self._pending_text)
        label.setWindowFlags(
            Qt.WindowType.ToolTip
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        label.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        label.setStyleSheet(self._TOOLTIP_STYLE)
        label.adjustSize()

        # 定位在按钮下方
        pos = QCursor.pos()
        if self._pending_btn:
            btn = self._pending_btn
            pos = btn.mapToGlobal(QPoint(0, btn.height() + 4))
        screen = QApplication.screenAt(pos)
        if screen:
            sg = screen.availableGeometry()
            if pos.x() + label.width() > sg.right():
                pos.setX(sg.right() - label.width())
            if pos.y() + label.height() > sg.bottom():
                pos.setY(pos.y() - label.height())
        label.move(pos)
        label.show()
        self._label = label

    def _hide(self) -> None:
        if self._label:
            self._label.deleteLater()
            self._label = None

# 工具按钮定义：(label, tooltip, EditTool)
_TOOL_DEFS: list[tuple[str, str, EditTool]] = [
    ("打码", "马赛克", EditTool.MOSAIC),
    ("模糊", "模糊", EditTool.BLUR),
    ("裁剪", "裁剪", EditTool.CROP),
    ("矩形", "矩形", EditTool.RECT),
    ("椭圆", "椭圆", EditTool.ELLIPSE),
    ("箭头", "箭头", EditTool.ARROW),
    ("文字", "文字", EditTool.TEXT),
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
        self._tooltip_mgr = _TooltipManager(self)

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
        for label, tooltip, tool in _TOOL_DEFS:
            btn = QToolButton()
            btn.setText(label)
            btn.setToolTip(tooltip)
            btn.setCheckable(True)
            btn.setStyleSheet(tool_style)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._tool_group.addButton(btn)
            self._tool_buttons[tool] = btn
            self._tooltip_mgr.register(btn)
            layout.addWidget(btn)

        # 属性区分隔线（初始隐藏）
        self._props_separator = self._create_separator()
        self._props_separator.hide()
        layout.addWidget(self._props_separator)

        # 工具属性条（初始隐藏）
        self._properties_bar = ToolPropertiesBar()
        self._properties_bar.hide()
        layout.addWidget(self._properties_bar)

        # 弹性空间
        layout.addStretch()

        # 操作按钮
        action_style = InlineStyles.action_button_style()

        self._btn_undo = self._make_action_btn("撤销", "撤销", action_style)
        self._btn_undo.setEnabled(False)
        layout.addWidget(self._btn_undo)

        self._btn_redo = self._make_action_btn("重做", "重做", action_style)
        self._btn_redo.setEnabled(False)
        layout.addWidget(self._btn_redo)

        layout.addWidget(self._create_separator())

        self._btn_save = self._make_action_btn("保存", "另存为", action_style)
        layout.addWidget(self._btn_save)

        self._btn_copy = self._make_action_btn("复制", "复制", action_style)
        layout.addWidget(self._btn_copy)

        self._btn_confirm = self._make_action_btn(
            "识别", "确认识别", InlineStyles.confirm_button_style()
        )
        layout.addWidget(self._btn_confirm)

        self._btn_cancel = self._make_action_btn(
            "取消", "取消", InlineStyles.cancel_button_style()
        )
        layout.addWidget(self._btn_cancel)

    def _make_action_btn(
        self, text: str, tooltip: str, style: str
    ) -> QToolButton:
        btn = QToolButton()
        btn.setText(text)
        btn.setToolTip(tooltip)
        btn.setStyleSheet(style)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tooltip_mgr.register(btn)
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
