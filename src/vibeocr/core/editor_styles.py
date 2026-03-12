"""编辑器专用样式定义

集中管理截图编辑窗口的样式常量。
"""

from vibeocr.core.constants import WindowsColors


class EditorStyles:
    """编辑器样式常量"""

    # 编辑窗口背景
    EDITOR_BG = "#1a1a1a"
    EDITOR_BG_ALPHA = "rgba(26, 26, 26, 242)"  # 95% 不透明

    # 工具栏
    TOOLBAR_BG = "#2d2d2d"
    TOOLBAR_BORDER = "#404040"
    TOOLBAR_HEIGHT = 48

    # 右侧面板
    PANEL_BG = "#2d2d2d"
    PANEL_BORDER = "#404040"
    PANEL_WIDTH = 280
    PANEL_TITLE_COLOR = "#ffffff"

    # 按钮通用样式
    BUTTON_TEXT = "#e0e0e0"
    BUTTON_BG = "#3d3d3d"
    BUTTON_HOVER = "#4d4d4d"
    BUTTON_PRESSED = "#555555"
    BUTTON_CHECKED = WindowsColors.PRIMARY
    BUTTON_CHECKED_HOVER = WindowsColors.PRIMARY_HOVER

    # 确认按钮
    CONFIRM_BG = WindowsColors.SUCCESS
    CONFIRM_HOVER = WindowsColors.SUCCESS_HOVER

    # 取消按钮
    CANCEL_BG = "#555555"
    CANCEL_HOVER = "#666666"

    # 分隔线
    SEPARATOR_COLOR = "#505050"

    @classmethod
    def toolbar_style(cls) -> str:
        """工具栏整体样式"""
        return f"""
            QWidget#editorToolbar {{
                background-color: {cls.TOOLBAR_BG};
                border-top: 1px solid {cls.TOOLBAR_BORDER};
            }}
        """

    @classmethod
    def tool_button_style(cls) -> str:
        """工具按钮样式"""
        return f"""
            QToolButton {{
                background-color: {cls.BUTTON_BG};
                color: {cls.BUTTON_TEXT};
                border: 1px solid {cls.TOOLBAR_BORDER};
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 12px;
            }}
            QToolButton:hover {{
                background-color: {cls.BUTTON_HOVER};
            }}
            QToolButton:pressed {{
                background-color: {cls.BUTTON_PRESSED};
            }}
            QToolButton:checked {{
                background-color: {cls.BUTTON_CHECKED};
                color: white;
                border-color: {cls.BUTTON_CHECKED};
            }}
            QToolButton:checked:hover {{
                background-color: {cls.BUTTON_CHECKED_HOVER};
            }}
        """

    @classmethod
    def action_button_style(cls) -> str:
        """操作按钮样式（撤销/重做/保存/复制）"""
        return f"""
            QPushButton {{
                background-color: {cls.BUTTON_BG};
                color: {cls.BUTTON_TEXT};
                border: 1px solid {cls.TOOLBAR_BORDER};
                border-radius: 4px;
                padding: 6px 12px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {cls.BUTTON_HOVER};
            }}
            QPushButton:pressed {{
                background-color: {cls.BUTTON_PRESSED};
            }}
            QPushButton:disabled {{
                background-color: #2a2a2a;
                color: #666666;
            }}
        """

    @classmethod
    def confirm_button_style(cls) -> str:
        """确认识别按钮样式"""
        return f"""
            QPushButton {{
                background-color: {cls.CONFIRM_BG};
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 16px;
                font-size: 12px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {cls.CONFIRM_HOVER};
            }}
        """

    @classmethod
    def cancel_button_style(cls) -> str:
        """取消按钮样式"""
        return f"""
            QPushButton {{
                background-color: {cls.CANCEL_BG};
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 12px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {cls.CANCEL_HOVER};
            }}
        """

    @classmethod
    def panel_style(cls) -> str:
        """右侧面板样式"""
        return f"""
            QWidget#recognitionPanel {{
                background-color: {cls.PANEL_BG};
                border-left: 1px solid {cls.PANEL_BORDER};
            }}
            QLabel#panelTitle {{
                color: {cls.PANEL_TITLE_COLOR};
                font-size: 14px;
                font-weight: bold;
                padding: 8px;
            }}
            QGroupBox {{
                color: #cccccc;
                border: 1px solid {cls.TOOLBAR_BORDER};
                border-radius: 4px;
                margin-top: 8px;
                padding-top: 8px;
            }}
            QGroupBox::title {{
                color: #cccccc;
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px;
            }}
            QCheckBox {{
                color: {cls.BUTTON_TEXT};
            }}
            QComboBox {{
                background-color: {cls.BUTTON_BG};
                color: {cls.BUTTON_TEXT};
                border: 1px solid {cls.TOOLBAR_BORDER};
                border-radius: 3px;
                padding: 4px 8px;
            }}
            QComboBox:hover {{
                border-color: {cls.BUTTON_CHECKED};
            }}
            QComboBox QAbstractItemView {{
                background-color: {cls.BUTTON_BG};
                color: {cls.BUTTON_TEXT};
                selection-background-color: {cls.BUTTON_CHECKED};
            }}
            QLabel {{
                color: {cls.BUTTON_TEXT};
            }}
            QTabWidget::pane {{
                border: 1px solid {cls.TOOLBAR_BORDER};
                background-color: {cls.PANEL_BG};
            }}
            QTabBar::tab {{
                background-color: {cls.BUTTON_BG};
                color: {cls.BUTTON_TEXT};
                padding: 6px 12px;
                border: 1px solid {cls.TOOLBAR_BORDER};
                border-bottom: none;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }}
            QTabBar::tab:selected {{
                background-color: {cls.PANEL_BG};
            }}
        """

    @classmethod
    def properties_bar_style(cls) -> str:
        """工具属性条样式"""
        return f"""
            QWidget#propertiesBar {{
                background-color: transparent;
            }}
            QLabel {{
                color: {cls.BUTTON_TEXT};
                font-size: 11px;
            }}
            QSpinBox {{
                background-color: {cls.BUTTON_BG};
                color: {cls.BUTTON_TEXT};
                border: 1px solid {cls.TOOLBAR_BORDER};
                border-radius: 3px;
                padding: 2px 4px;
                min-width: 50px;
            }}
            QSlider::groove:horizontal {{
                background: {cls.TOOLBAR_BORDER};
                height: 4px;
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {cls.BUTTON_CHECKED};
                width: 12px;
                height: 12px;
                margin: -4px 0;
                border-radius: 6px;
            }}
            QCheckBox {{
                color: {cls.BUTTON_TEXT};
                font-size: 11px;
            }}
            QFontComboBox {{
                background-color: {cls.BUTTON_BG};
                color: {cls.BUTTON_TEXT};
                border: 1px solid {cls.TOOLBAR_BORDER};
                border-radius: 3px;
                padding: 2px 4px;
                max-width: 120px;
            }}
        """
