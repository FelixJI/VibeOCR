"""ChatWidget 单元测试"""


class TestChatWidget:
    """测试 ChatWidget 组件。"""

    def test_chat_widget_creation(self, qapp):
        """测试 ChatWidget 可以被创建"""
        from vibeocr.widgets.chat_widget import ChatWidget

        widget = ChatWidget()
        assert widget is not None

    def test_chat_widget_add_user_message(self, qapp):
        """测试添加用户消息"""
        from vibeocr.widgets.chat_widget import ChatWidget

        widget = ChatWidget()
        widget.add_user_message("你好")

        # 验证消息已添加
        assert widget.message_count() == 1

    def test_chat_widget_add_ai_message(self, qapp):
        """测试添加 AI 消息"""
        from vibeocr.widgets.chat_widget import ChatWidget

        widget = ChatWidget()
        widget.add_ai_message("你好，有什么可以帮助您？")

        assert widget.message_count() == 1

    def test_chat_widget_clear(self, qapp):
        """测试清空对话"""
        from vibeocr.widgets.chat_widget import ChatWidget

        widget = ChatWidget()
        widget.add_user_message("问题1")
        widget.add_ai_message("回答1")
        assert widget.message_count() == 2

        widget.clear_chat()
        assert widget.message_count() == 0

    def test_chat_widget_get_history(self, qapp):
        """测试获取对话历史"""
        from vibeocr.widgets.chat_widget import ChatWidget

        widget = ChatWidget()
        widget.add_user_message("问题1")
        widget.add_ai_message("回答1")

        history = widget.get_history()
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "问题1"
        assert history[1]["role"] == "assistant"
        assert history[1]["content"] == "回答1"

    def test_chat_widget_load_history(self, qapp):
        """测试加载对话历史"""
        from vibeocr.widgets.chat_widget import ChatWidget

        widget = ChatWidget()
        history = [
            {"role": "user", "content": "历史问题"},
            {"role": "assistant", "content": "历史回答"}
        ]

        widget.load_history(history)
        assert widget.message_count() == 2
