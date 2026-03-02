"""应用程序样式定义

集中管理 UI 样式，确保一致性。
"""

from PySide6.QtGui import QColor


class AppStyles:
    """应用程序样式

    提供统一的配色方案和样式字符串。
    """

    # ========== 配色方案 ==========
    # 主色调
    PRIMARY = "#2196F3"
    PRIMARY_LIGHT = "#64B5F6"
    PRIMARY_DARK = "#1976D2"

    # 成功/完成
    SUCCESS = "#4CAF50"
    SUCCESS_LIGHT = "#81C784"

    # 警告
    WARNING = "#FF9800"
    WARNING_LIGHT = "#FFB74D"

    # 错误/失败
    ERROR = "#F44336"
    ERROR_LIGHT = "#E57373"

    # 背景色
    BACKGROUND = "#FFFFFF"
    BACKGROUND_SECONDARY = "#F5F5F5"
    BACKGROUND_DARK = "#E0E0E0"

    # 文本色
    TEXT_PRIMARY = "#212121"
    TEXT_SECONDARY = "#757575"
    TEXT_DISABLED = "#BDBDBD"

    # 边框色
    BORDER = "#E0E0E0"
    BORDER_DARK = "#BDBDBD"

    # 状态指示器颜色
    INDICATOR_IDLE = "#9E9E9E"
    INDICATOR_PROCESSING = PRIMARY
    INDICATOR_SUCCESS = SUCCESS
    INDICATOR_ERROR = ERROR

    # ========== 样式字符串生成器 ==========
    @classmethod
    def get_button_style(
        cls,
        bg_color: str = None,
        text_color: str = None,
        border_radius: int = 6,
        padding: str = "8px 16px"
    ) -> str:
        """生成按钮样式

        Args:
            bg_color: 背景色，默认使用主色
            text_color: 文本色，默认白色
            border_radius: 圆角半径
            padding: 内边距

        Returns:
            样式字符串
        """
        bg = bg_color or cls.PRIMARY
        text = text_color or "#FFFFFF"
        return f"""
            QPushButton {{
                background-color: {bg};
                color: {text};
                border: none;
                border-radius: {border_radius}px;
                padding: {padding};
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {cls._darken_color(bg, 0.1)};
            }}
            QPushButton:pressed {{
                background-color: {cls._darken_color(bg, 0.2)};
            }}
            QPushButton:disabled {{
                background-color: #BDBDBD;
                color: #757575;
            }}
        """

    @classmethod
    def get_card_style(cls, border_radius: int = 8, padding: int = 16) -> str:
        """生成卡片样式

        Args:
            border_radius: 圆角半径
            padding: 内边距

        Returns:
            样式字符串
        """
        return f"""
            QWidget {{
                background-color: {cls.BACKGROUND};
                border: 1px solid {cls.BORDER};
                border-radius: {border_radius}px;
                padding: {padding}px;
            }}
        """

    @classmethod
    def get_group_box_style(cls, title_color: str = None) -> str:
        """生成分组框样式

        Args:
            title_color: 标题颜色

        Returns:
            样式字符串
        """
        title = title_color or cls.TEXT_PRIMARY
        return f"""
            QGroupBox {{
                font-weight: bold;
                border: 1px solid {cls.BORDER};
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 12px;
                padding: 16px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 8px;
                color: {title};
            }}
        """

    @classmethod
    def get_status_indicator_style(cls, color: str) -> str:
        """生成状态指示器样式

        Args:
            color: 指示器颜色

        Returns:
            样式字符串
        """
        return f"""
            QLabel {{
                background-color: {color};
                border-radius: 6px;
                min-width: 12px;
                max-width: 12px;
                min-height: 12px;
                max-height: 12px;
            }}
        """

    @classmethod
    def get_scroll_area_style(cls) -> str:
        """生成滚动区域样式

        Returns:
            样式字符串
        """
        return f"""
            QScrollArea {{
                border: none;
                background-color: transparent;
            }}
            QScrollBar:vertical {{
                background: {cls.BACKGROUND_SECONDARY};
                width: 12px;
                border-radius: 6px;
            }}
            QScrollBar::handle:vertical {{
                background: {cls.BORDER_DARK};
                border-radius: 6px;
                min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {cls.TEXT_SECONDARY};
            }}
        """

    @classmethod
    def get_progress_bar_style(cls) -> str:
        """生成进度条样式

        Returns:
            样式字符串
        """
        return f"""
            QProgressBar {{
                border: none;
                border-radius: 4px;
                background-color: {cls.BACKGROUND_DARK};
                text-align: center;
                color: {cls.TEXT_PRIMARY};
            }}
            QProgressBar::chunk {{
                background-color: {cls.PRIMARY};
                border-radius: 4px;
            }}
        """

    # ========== 预定义样式（作为类方法使用）==========
    @classmethod
    def BUTTON_PRIMARY(cls) -> str:
        """主要按钮样式"""
        return cls.get_button_style()

    @classmethod
    def BUTTON_DANGER(cls) -> str:
        """危险/删除按钮样式"""
        return cls.get_button_style(bg_color=cls.ERROR)

    @classmethod
    def BUTTON_SUCCESS(cls) -> str:
        """成功按钮样式"""
        return cls.get_button_style(bg_color=cls.SUCCESS)

    # ========== 辅助方法 ==========
    @staticmethod
    def _darken_color(color: str, factor: float) -> str:
        """加深颜色

        Args:
            color: 十六进制颜色字符串 (#RRGGBB)
            factor: 加深因子 (0.0 - 1.0)

        Returns:
            加深后的颜色字符串
        """
        try:
            qcolor = QColor(color)
            r = max(0, int(qcolor.red() * (1 - factor)))
            g = max(0, int(qcolor.green() * (1 - factor)))
            b = max(0, int(qcolor.blue() * (1 - factor)))
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return color

    @staticmethod
    def _lighten_color(color: str, factor: float) -> str:
        """减淡颜色

        Args:
            color: 十六进制颜色字符串 (#RRGGBB)
            factor: 减淡因子 (0.0 - 1.0)

        Returns:
            减淡后的颜色字符串
        """
        try:
            qcolor = QColor(color)
            r = min(255, int(qcolor.red() + (255 - qcolor.red()) * factor))
            g = min(255, int(qcolor.green() + (255 - qcolor.green()) * factor))
            b = min(255, int(qcolor.blue() + (255 - qcolor.blue()) * factor))
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return color
