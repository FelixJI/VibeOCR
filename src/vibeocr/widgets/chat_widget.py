"""聊天气泡组件

提供聊天气泡式的对话显示界面。
"""

import logging

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from vibeocr.ui import theme

logger = logging.getLogger(__name__)


class MessageBubble(QFrame):
    """单条消息气泡"""

    def __init__(self, text: str, is_user: bool = False, parent=None):
        super().__init__(parent)
        self._setup_ui(text, is_user)

    def _setup_ui(self, text: str, is_user: bool):
        """设置 UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)

        # 角色标签
        role_label = QLabel("用户" if is_user else "AI")
        role_label.setStyleSheet(
            f"font-size: {theme.Typography.caption}px; color: {theme.Colors.text_muted};"
        )
        layout.addWidget(role_label)

        # 消息内容
        self._content_label = QLabel(text)
        self._content_label.setWordWrap(True)
        self._content_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._content_label.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )
        layout.addWidget(self._content_label)

        # 设置气泡样式
        if is_user:
            self.setStyleSheet(f"""
                MessageBubble {{
                    background-color: {theme.Colors.accent_soft};
                    border-radius: {theme.Radius.lg}px;
                    margin-left: 40px;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                MessageBubble {{
                    background-color: {theme.Colors.surface_alt};
                    border-radius: {theme.Radius.lg}px;
                    margin-right: 40px;
                }}
            """)


class ChatWidget(QWidget):
    """聊天气泡组件

    提供聊天气泡式的对话显示界面，支持：
    - 用户消息和 AI 消息的区分显示
    - 滚动查看历史
    - 底部输入框和发送按钮
    """

    # 信号：用户发送消息
    message_sent = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._messages: list[dict] = []
        self._setup_ui()

    def _setup_ui(self):
        """设置 UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # 消息滚动区域
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll_area.setStyleSheet(
            f"QScrollArea {{ border: none; background: {theme.Colors.surface}; }}"
        )

        # 消息容器
        self._messages_container = QWidget()
        self._messages_layout = QVBoxLayout(self._messages_container)
        self._messages_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._messages_layout.setSpacing(8)
        self._messages_layout.setContentsMargins(8, 8, 8, 8)

        # 添加弹性空间，让消息从顶部开始
        self._messages_layout.addStretch()

        self._scroll_area.setWidget(self._messages_container)
        layout.addWidget(self._scroll_area)

        # 输入区域
        input_widget = self._create_input_widget()
        layout.addWidget(input_widget)

    def _create_input_widget(self) -> QWidget:
        """创建输入区域"""
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # 输入框
        self._input_field = QLineEdit()
        self._input_field.setPlaceholderText("输入您的问题...")
        self._input_field.returnPressed.connect(self._on_send)
        layout.addWidget(self._input_field, 1)

        # 发送按钮
        self._send_button = QPushButton("发送")
        self._send_button.setFixedWidth(80)
        self._send_button.clicked.connect(self._on_send)
        layout.addWidget(self._send_button)

        return widget

    @Slot()
    def _on_send(self):
        """发送消息"""
        text = self._input_field.text().strip()
        if not text:
            return

        self._input_field.clear()
        self.add_user_message(text)
        self.message_sent.emit(text)

    def add_user_message(self, text: str):
        """添加用户消息"""
        self._add_message(text, is_user=True)

    def add_ai_message(self, text: str):
        """添加 AI 消息"""
        self._add_message(text, is_user=False)

    def _add_message(self, text: str, is_user: bool):
        """添加消息到界面"""
        # 保存到历史
        role = "user" if is_user else "assistant"
        self._messages.append({"role": role, "content": text})

        # 创建气泡（在 stretch 之前插入）
        bubble = MessageBubble(text, is_user)
        self._messages_layout.insertWidget(self._messages_layout.count() - 1, bubble)

        # 滚动到底部
        self._scroll_to_bottom()

    def clear_chat(self):
        """清空对话"""
        self._messages.clear()

        # 移除所有气泡（保留 stretch）
        while self._messages_layout.count() > 1:
            item = self._messages_layout.takeAt(0)
            if item is not None:
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()

    def get_history(self) -> list[dict]:
        """获取对话历史"""
        return self._messages.copy()

    def load_history(self, history: list[dict]):
        """加载对话历史"""
        self.clear_chat()

        for msg in history:
            is_user = msg["role"] == "user"
            self._add_message(msg["content"], is_user)

    def message_count(self) -> int:
        """获取消息数量"""
        return len(self._messages)

    def set_loading(self, loading: bool):
        """设置加载状态"""
        self._send_button.setEnabled(not loading)
        self._input_field.setEnabled(not loading)
        if loading:
            self._input_field.setPlaceholderText("AI 正在思考...")
        else:
            self._input_field.setPlaceholderText("输入您的问题...")

    def _scroll_to_bottom(self):
        """滚动到底部"""
        from PySide6.QtCore import QTimer

        # 延迟滚动，确保布局已更新
        QTimer.singleShot(
            100,
            lambda: self._scroll_area.verticalScrollBar().setValue(
                self._scroll_area.verticalScrollBar().maximum()
            ),
        )
