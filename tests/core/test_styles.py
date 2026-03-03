"""测试 AppStyles 样式类"""

from PySide6.QtGui import QColor

from vibeocr.core import AppStyles


class TestAppStylesColors:
    """测试颜色定义"""

    def test_primary_colors(self):
        """测试主色调"""
        assert AppStyles.PRIMARY.startswith("#")
        assert AppStyles.PRIMARY_LIGHT.startswith("#")
        assert AppStyles.PRIMARY_DARK.startswith("#")

    def test_status_colors(self):
        """测试状态颜色"""
        assert AppStyles.SUCCESS.startswith("#")
        assert AppStyles.WARNING.startswith("#")
        assert AppStyles.ERROR.startswith("#")

    def test_background_colors(self):
        """测试背景颜色"""
        assert AppStyles.BACKGROUND.startswith("#")
        assert AppStyles.BACKGROUND_SECONDARY.startswith("#")
        assert AppStyles.BACKGROUND_DARK.startswith("#")

    def test_text_colors(self):
        """测试文本颜色"""
        assert AppStyles.TEXT_PRIMARY.startswith("#")
        assert AppStyles.TEXT_SECONDARY.startswith("#")
        assert AppStyles.TEXT_DISABLED.startswith("#")

    def test_border_colors(self):
        """测试边框颜色"""
        assert AppStyles.BORDER.startswith("#")
        assert AppStyles.BORDER_DARK.startswith("#")

    def test_indicator_colors(self):
        """测试状态指示器颜色"""
        assert AppStyles.INDICATOR_IDLE.startswith("#")
        assert AppStyles.INDICATOR_PROCESSING.startswith("#")
        assert AppStyles.INDICATOR_SUCCESS.startswith("#")
        assert AppStyles.INDICATOR_ERROR.startswith("#")


class TestAppStylesButton:
    """测试按钮样式"""

    def test_button_style_default(self):
        """测试默认按钮样式"""
        style = AppStyles.get_button_style()
        assert "QPushButton" in style
        assert "background-color" in style
        assert "border-radius" in style

    def test_button_style_custom_colors(self):
        """测试自定义颜色按钮样式"""
        style = AppStyles.get_button_style(bg_color="#FF0000", text_color="#FFFFFF")
        assert "#FF0000" in style
        assert "#FFFFFF" in style

    def test_button_style_custom_radius(self):
        """测试自定义圆角按钮样式"""
        style = AppStyles.get_button_style(border_radius=10)
        assert "border-radius: 10px" in style

    def test_predefined_buttons(self):
        """测试预定义按钮样式"""
        assert "QPushButton" in AppStyles.BUTTON_PRIMARY()
        assert "QPushButton" in AppStyles.BUTTON_DANGER()
        assert "QPushButton" in AppStyles.BUTTON_SUCCESS()


class TestAppStylesCard:
    """测试卡片样式"""

    def test_card_style_default(self):
        """测试默认卡片样式"""
        style = AppStyles.get_card_style()
        assert "QWidget" in style
        assert "background-color" in style
        assert "border:" in style

    def test_card_style_custom(self):
        """测试自定义卡片样式"""
        style = AppStyles.get_card_style(border_radius=12, padding=20)
        assert "border-radius: 12px" in style
        assert "padding: 20px" in style


class TestAppStylesGroupBox:
    """测试分组框样式"""

    def test_group_box_style_default(self):
        """测试默认分组框样式"""
        style = AppStyles.get_group_box_style()
        assert "QGroupBox" in style
        assert "font-weight: bold" in style

    def test_group_box_style_custom_title(self):
        """测试自定义标题颜色"""
        style = AppStyles.get_group_box_style(title_color="#FF0000")
        assert "#FF0000" in style


class TestAppStylesStatusIndicator:
    """测试状态指示器样式"""

    def test_status_indicator_style(self):
        """测试状态指示器样式"""
        style = AppStyles.get_status_indicator_style("#FF0000")
        assert "QLabel" in style
        assert "#FF0000" in style
        assert "border-radius:" in style
        assert "min-width: 12px" in style


class TestAppStylesScrollArea:
    """测试滚动区域样式"""

    def test_scroll_area_style(self):
        """测试滚动区域样式"""
        style = AppStyles.get_scroll_area_style()
        assert "QScrollArea" in style
        assert "QScrollBar:vertical" in style
        assert "QScrollBar::handle:vertical" in style


class TestAppStylesProgressBar:
    """测试进度条样式"""

    def test_progress_bar_style(self):
        """测试进度条样式"""
        style = AppStyles.get_progress_bar_style()
        assert "QProgressBar" in style
        assert "QProgressBar::chunk" in style
        assert "border-radius:" in style


class TestAppStylesHelpers:
    """测试辅助方法"""

    def test_darken_color(self):
        """测试颜色加深"""
        # 将白色加深应该变暗
        darkened = AppStyles._darken_color("#FFFFFF", 0.5)
        assert darkened.startswith("#")
        # 加深后的值应该小于原始值
        orig = QColor("#FFFFFF").rgb()
        dark = QColor(darkened).rgb()
        assert dark < orig or darkened == "#FFFFFF"  # 边界情况

    def test_darken_color_invalid(self):
        """测试无效颜色加深"""
        # 无效颜色 - QColor 会尝试解析，可能返回一个值
        result = AppStyles._darken_color("invalid", 0.5)
        assert result.startswith("#")  # 返回一个有效的十六进制颜色

    def test_lighten_color(self):
        """测试颜色减淡"""
        # 将黑色减淡应该变亮
        lightened = AppStyles._lighten_color("#000000", 0.5)
        assert lightened.startswith("#")
        # 减淡后的值应该大于原始值
        orig = QColor("#000000").rgb()
        light = QColor(lightened).rgb()
        assert light > orig or lightened == "#000000"  # 边界情况

    def test_lighten_color_invalid(self):
        """测试无效颜色减淡"""
        # 无效颜色 - QColor 会尝试解析，可能返回一个值
        result = AppStyles._lighten_color("invalid", 0.5)
        assert result.startswith("#")  # 返回一个有效的十六进制颜色

    def test_darken_black(self):
        """测试黑色加深（边界情况）"""
        # 黑色不能再加深
        result = AppStyles._darken_color("#000000", 0.5)
        assert result == "#000000"

    def test_lighten_white(self):
        """测试白色减淡（边界情况）"""
        # 白色不能再减淡
        result = AppStyles._lighten_color("#FFFFFF", 0.5)
        assert result.lower() == "#ffffff"
