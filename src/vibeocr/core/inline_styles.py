# src/vibeocr/core/inline_styles.py
"""内联编辑器毛玻璃浅色样式定义"""

from vibeocr.core.constants import WindowsColors


class InlineStyles:
    """内联编辑器样式常量（毛玻璃浅色主题）"""

    # 面板
    PANEL_BG = "#f5f5f5"
    PANEL_BORDER = "#d0d0d0"
    PANEL_RADIUS = 8

    # 按钮
    BUTTON_HOVER = "#e8e8e8"
    BUTTON_PRESSED = "#dcdcdc"
    BUTTON_CHECKED = WindowsColors.PRIMARY
    BUTTON_CHECKED_HOVER = WindowsColors.PRIMARY_HOVER

    # 确认按钮
    CONFIRM_BG = WindowsColors.PRIMARY
    CONFIRM_HOVER = WindowsColors.PRIMARY_HOVER

    # 取消按钮
    CANCEL_HOVER = "rgba(220, 50, 50, 38)"

    # 选区边框
    SELECTION_BORDER = "#0078d4"

    # 分隔线
    SEPARATOR_COLOR = "rgba(0, 0, 0, 30)"

    # 文字
    TEXT_COLOR = "#333333"
    TEXT_LIGHT = "#ffffff"

    # 面板尺寸
    PANEL_MIN_WIDTH = 180
    TOOLBAR_HEIGHT = 48

    # 阴影
    SHADOW_BLUR = 12
    SHADOW_OFFSET = 2
    SHADOW_COLOR = "rgba(0, 0, 0, 38)"

    @classmethod
    def panel_style(cls) -> str:
        return f"""
            QWidget {{
                background-color: {cls.PANEL_BG};
                border: 1px solid {cls.PANEL_BORDER};
                border-radius: {cls.PANEL_RADIUS}px;
            }}
        """

    @classmethod
    def tool_button_style(cls) -> str:
        return f"""
            QToolButton {{
                background: transparent;
                color: {cls.TEXT_COLOR};
                border: none;
                border-radius: 4px;
                padding: 4px 6px;
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
            }}
            QToolButton:checked:hover {{
                background-color: {cls.BUTTON_CHECKED_HOVER};
            }}
            QToolButton:disabled {{
                color: rgba(150, 150, 150, 180);
            }}
        """

    @classmethod
    def action_button_style(cls) -> str:
        return f"""
            QToolButton {{
                background: transparent;
                color: {cls.TEXT_COLOR};
                border: none;
                border-radius: 4px;
                padding: 4px 6px;
            }}
            QToolButton:hover {{
                background-color: {cls.BUTTON_HOVER};
            }}
            QToolButton:pressed {{
                background-color: {cls.BUTTON_PRESSED};
            }}
            QToolButton:disabled {{
                color: rgba(150, 150, 150, 180);
            }}
        """

    @classmethod
    def confirm_button_style(cls) -> str:
        return f"""
            QToolButton {{
                background-color: {cls.CONFIRM_BG};
                color: {cls.TEXT_LIGHT};
                border: none;
                border-radius: 4px;
                padding: 4px 8px;
            }}
            QToolButton:hover {{
                background-color: {cls.CONFIRM_HOVER};
            }}
        """

    @classmethod
    def cancel_button_style(cls) -> str:
        return f"""
            QToolButton {{
                background: transparent;
                color: {cls.TEXT_COLOR};
                border: none;
                border-radius: 4px;
                padding: 4px 6px;
            }}
            QToolButton:hover {{
                background-color: {cls.CANCEL_HOVER};
                color: #c83232;
            }}
        """

    # 属性条控件颜色
    PROPS_INPUT_BG = "#ffffff"
    PROPS_INPUT_BORDER = "#c0c0c0"
    PROPS_SLIDER_GROOVE = "#c0c0c0"
    PROPS_SLIDER_HANDLE = "#0078d4"

    @classmethod
    def properties_panel_style(cls) -> str:
        """属性面板样式：面板背景 + 内部控件浅色主题"""
        return f"""
            QWidget#propsPanel {{
                background-color: {cls.PANEL_BG};
                border: 1px solid {cls.PANEL_BORDER};
                border-radius: {cls.PANEL_RADIUS}px;
            }}
            #propsPanel QWidget {{
                background-color: transparent;
            }}
            #propsPanel QLabel {{
                color: {cls.TEXT_COLOR};
                font-size: 11px;
            }}
            #propsPanel QSpinBox {{
                background-color: {cls.PROPS_INPUT_BG};
                color: {cls.TEXT_COLOR};
                border: 1px solid {cls.PROPS_INPUT_BORDER};
                border-radius: 3px;
                padding: 1px 4px;
                min-width: 42px;
                max-height: 26px;
            }}
            #propsPanel QSlider::groove:horizontal {{
                background: {cls.PROPS_SLIDER_GROOVE};
                height: 4px;
                border-radius: 2px;
            }}
            #propsPanel QSlider::handle:horizontal {{
                background: {cls.PROPS_SLIDER_HANDLE};
                width: 14px;
                height: 14px;
                margin: -5px 0;
                border-radius: 7px;
            }}
            #propsPanel QCheckBox {{
                color: {cls.TEXT_COLOR};
                font-size: 11px;
                spacing: 4px;
            }}
            #propsPanel QCheckBox::indicator {{
                width: 14px;
                height: 14px;
                border: 1px solid {cls.PROPS_INPUT_BORDER};
                border-radius: 3px;
                background: {cls.PROPS_INPUT_BG};
            }}
            #propsPanel QCheckBox::indicator:checked {{
                background: {cls.PROPS_SLIDER_HANDLE};
                border-color: {cls.PROPS_SLIDER_HANDLE};
            }}
            #propsPanel QFontComboBox {{
                background-color: {cls.PROPS_INPUT_BG};
                color: {cls.TEXT_COLOR};
                border: 1px solid {cls.PROPS_INPUT_BORDER};
                border-radius: 3px;
                padding: 1px 4px;
                max-width: 120px;
                max-height: 26px;
            }}
            #propsPanel QPushButton {{
                background-color: {cls.PROPS_INPUT_BG};
                border: 1px solid {cls.PROPS_INPUT_BORDER};
                border-radius: 3px;
            }}
        """

    @classmethod
    def recognition_button_style(cls) -> str:
        return f"""
            QPushButton {{
                background: transparent;
                color: {cls.TEXT_COLOR};
                border: none;
                border-radius: 4px;
                padding: 6px;
                font-size: 13px;
                text-align: left;
            }}
            QPushButton:hover {{
                background-color: {cls.BUTTON_HOVER};
            }}
            QPushButton:pressed {{
                background-color: {cls.BUTTON_PRESSED};
            }}
            QPushButton:checked {{
                background-color: {cls.BUTTON_CHECKED};
                color: white;
            }}
        """
