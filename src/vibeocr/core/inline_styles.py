# src/vibeocr/core/inline_styles.py
"""内联编辑器毛玻璃浅色样式定义"""

from vibeocr.core.constants import WindowsColors


class InlineStyles:
    """内联编辑器样式常量（毛玻璃浅色主题）"""

    # 面板
    PANEL_BG = "rgba(255, 255, 255, 224)"  # 0.88 不透明度
    PANEL_BORDER = "rgba(255, 255, 255, 77)"  # 0.3 不透明度
    PANEL_RADIUS = 8

    # 按钮
    BUTTON_BG = "rgba(255, 255, 255, 153)"  # 0.6 不透明度
    BUTTON_HOVER = "rgba(255, 255, 255, 230)"
    BUTTON_PRESSED = "rgba(240, 240, 240, 230)"
    BUTTON_CHECKED = WindowsColors.PRIMARY
    BUTTON_CHECKED_HOVER = WindowsColors.PRIMARY_HOVER

    # 确认按钮
    CONFIRM_BG = WindowsColors.PRIMARY
    CONFIRM_HOVER = WindowsColors.PRIMARY_HOVER

    # 取消按钮
    CANCEL_BG = "rgba(200, 200, 200, 180)"
    CANCEL_HOVER = "rgba(180, 180, 180, 200)"

    # 选区边框
    SELECTION_BORDER = "#0078d4"

    # 分隔线
    SEPARATOR_COLOR = "rgba(0, 0, 0, 30)"

    # 文字
    TEXT_COLOR = "#333333"
    TEXT_LIGHT = "#ffffff"

    # 面板尺寸
    PANEL_MIN_WIDTH = 180
    TOOLBAR_HEIGHT = 44

    # 阴影
    SHADOW_BLUR = 12
    SHADOW_OFFSET = 2
    SHADOW_COLOR = "rgba(0, 0, 0, 38)"  # 0.15 不透明度

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
                background-color: {cls.BUTTON_BG};
                color: {cls.TEXT_COLOR};
                border: none;
                border-radius: 5px;
                padding: 6px 8px;
                font-size: 16px;
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
        """

    @classmethod
    def recognition_button_style(cls) -> str:
        return f"""
            QPushButton {{
                background-color: {cls.BUTTON_BG};
                color: {cls.TEXT_COLOR};
                border: none;
                border-radius: 6px;
                padding: 10px;
                font-size: 13px;
                text-align: left;
            }}
            QPushButton:hover {{
                background-color: {cls.BUTTON_HOVER};
            }}
            QPushButton:checked {{
                background-color: {cls.BUTTON_CHECKED};
                color: white;
            }}
        """

    @classmethod
    def action_button_style(cls) -> str:
        return f"""
            QPushButton {{
                background-color: {cls.BUTTON_BG};
                color: {cls.TEXT_COLOR};
                border: none;
                border-radius: 5px;
                padding: 6px 10px;
                font-size: 14px;
            }}
            QPushButton:hover {{
                background-color: {cls.BUTTON_HOVER};
            }}
            QPushButton:disabled {{
                background-color: rgba(200, 200, 200, 100);
                color: rgba(100, 100, 100, 150);
            }}
        """

    @classmethod
    def confirm_button_style(cls) -> str:
        return f"""
            QPushButton {{
                background-color: {cls.CONFIRM_BG};
                color: {cls.TEXT_LIGHT};
                border: none;
                border-radius: 5px;
                padding: 6px 14px;
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {cls.CONFIRM_HOVER};
            }}
        """

    @classmethod
    def cancel_button_style(cls) -> str:
        return f"""
            QPushButton {{
                background-color: {cls.CANCEL_BG};
                color: {cls.TEXT_COLOR};
                border: none;
                border-radius: 5px;
                padding: 6px 10px;
                font-size: 14px;
            }}
            QPushButton:hover {{
                background-color: {cls.CANCEL_HOVER};
            }}
        """
