# tests/widgets/editor/test_tool_properties_bar_color_dialog.py
"""颜色选择对话框背景修复回归测试。

父窗口是截图覆盖层（WA_TranslucentBackground），原生 QColorDialog 会继承
透明属性导致整窗黑底。强制使用 Qt 自绘对话框可规避此问题。
"""

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog

from vibeocr.widgets.editor.tool_properties_bar import ToolPropertiesBar


class TestColorDialogNonNative:
    def test_dialog_uses_non_native(self, qapp):
        """颜色对话框必须使用 Qt 自绘（非原生），否则在透明父窗口下黑底。"""
        bar = ToolPropertiesBar()
        dialog = bar._make_color_dialog(QColor(255, 0, 0))
        assert dialog.testOption(QColorDialog.ColorDialogOption.DontUseNativeDialog)

    def test_dialog_preserves_initial_color(self, qapp):
        """对话框应携带初始颜色值。"""
        bar = ToolPropertiesBar()
        initial = QColor(10, 20, 30)
        dialog = bar._make_color_dialog(initial)
        selected = dialog.currentColor()
        assert (selected.red(), selected.green(), selected.blue()) == (10, 20, 30)
