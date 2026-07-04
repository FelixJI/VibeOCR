"""浮层 Toast 通知组件

提供轻量级的"保存成功"类提示，自动淡入淡出，不阻塞交互。
"""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    QTimer,
)
from PySide6.QtWidgets import QLabel, QWidget

from vibeocr.ui.theme import Radius, Typography

_TOAST_BG = "#333333"
_TOAST_DURATION = 2000  # ms


class ToastWidget(QLabel):
    """浮层 Toast 通知。

    显示在父控件顶部居中位置，带淡入/淡出动画，自动消失。
    鼠标可穿透，不响应用户交互。

    Usage::

        toast = ToastWidget(self, "保存成功")
        toast.show_at_top()
    """

    def __init__(
        self,
        parent: QWidget,
        text: str,
        duration: int = _TOAST_DURATION,
    ) -> None:
        super().__init__(parent)
        self.setText(text)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(f"""
            QLabel {{
                background-color: {_TOAST_BG};
                color: #ffffff;
                padding: 8px 20px;
                border-radius: {Radius.md}px;
                font-size: {Typography.body}px;
                font-weight: {Typography.weight_medium};
            }}
        """)
        # 鼠标可穿透，不抢焦点
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self._duration = duration
        self._fade_in: QPropertyAnimation | None = None
        self._fade_out: QPropertyAnimation | None = None

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._start_fade_out)

        self.hide()

    # ----------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------

    def show_at_top(self, offset_y: int = 50) -> None:
        """在父控件顶部居中显示，并启动自动消失计时器。"""
        parent = self.parent()
        if parent is None:
            return

        self.adjustSize()
        pw = parent.width()
        x = (pw - self.width()) // 2
        self.move(x, offset_y)
        self.raise_()

        self.setWindowOpacity(0.0)
        self.show()

        # 淡入
        self._fade_in = QPropertyAnimation(self, b"windowOpacity")
        self._fade_in.setDuration(180)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_in.start()

        self._timer.start(self._duration)

    # ----------------------------------------------------------------
    # Internal
    # ----------------------------------------------------------------

    def _start_fade_out(self) -> None:
        self._fade_out = QPropertyAnimation(self, b"windowOpacity")
        self._fade_out.setDuration(300)
        self._fade_out.setStartValue(self.windowOpacity())
        self._fade_out.setEndValue(0.0)
        self._fade_out.setEasingCurve(QEasingCurve.Type.InCubic)
        self._fade_out.finished.connect(self.hide)
        self._fade_out.start()


# ----------------------------------------------------------------
# Convenience singleton-style helper for one-shot toasts
# ----------------------------------------------------------------

# 持有 toast 引用防止 GC 过早回收（动画未完成即消失）
_active_toasts: list[ToastWidget] = []


def _on_toast_destroyed(t: ToastWidget) -> None:
    try:
        _active_toasts.remove(t)
    except ValueError:
        pass


def show_toast(parent: QWidget, text: str, duration: int = _TOAST_DURATION) -> None:
    """便捷函数：在 *parent* 的顶部居中弹出 Toast 后自动消失。

    *parent* 通常传 ``self.window()`` 或 ``self``（主窗口），避免被 Tab 裁剪。
    """
    toast = ToastWidget(parent, text, duration)
    toast.destroyed.connect(lambda obj=t: _on_toast_destroyed(obj))  # type: ignore[misc]
    _active_toasts.append(toast)
    toast.show_at_top()
