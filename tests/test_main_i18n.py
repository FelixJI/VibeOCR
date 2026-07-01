# tests/test_main_i18n.py
"""Qt 标准对话框中文翻译加载测试。

颜色选择对话框（QColorDialog）等 Qt 自带对话框默认为英文。_install_qt_translations
在应用启动时加载 PySide6 附带的 qtbase_zh_CN.qm，使这些对话框文案中文化。
"""

from vibeocr.main import _install_qt_translations


class TestInstallQtTranslations:
    def test_install_is_safe_for_non_zh_locale(self, qapp):
        """非中文 locale 下应静默跳过，不安装任何翻译器，不抛错。"""
        if hasattr(qapp, "_qt_translators"):
            del qapp._qt_translators
        _install_qt_translations(qapp, locale="en_US")
        # 非中文不应创建翻译器列表
        assert not hasattr(qapp, "_qt_translators") or not qapp._qt_translators

    def test_install_zh_loads_translators(self, qapp):
        """中文 locale 下应成功加载 qtbase 翻译并保留引用。"""
        if hasattr(qapp, "_qt_translators"):
            del qapp._qt_translators
        _install_qt_translations(qapp, locale="zh_CN")
        # 应至少加载到一个翻译器（qtbase_zh_CN.qm 随 PySide6 附带）
        assert hasattr(qapp, "_qt_translators")
        assert len(qapp._qt_translators) >= 1
        # 清理：从 app 移除翻译器，避免污染其它测试
        for t in qapp._qt_translators:
            qapp.removeTranslator(t)
        del qapp._qt_translators
