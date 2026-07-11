"""应用服务边界 import 隔离测试。

验证导入 vibeocr.application 不加载 PySide6——application 层是 UI-free 边界，
供 WorkerHost 和 WinUI 壳共享。
"""

from __future__ import annotations

import importlib
import sys


def _purge_modules(prefix: str) -> None:
    """从 sys.modules 移除指定前缀的模块。"""
    for name in list(sys.modules):
        if name.startswith(prefix):
            del sys.modules[name]


class TestImportBoundary:
    """application 包导入不得触发 PySide6 加载。"""

    def test_import_application_no_pyside6(self):
        """导入 vibeocr.application 不应加载 PySide6。"""
        _purge_modules("vibeocr.application")
        if "PySide6" in sys.modules:
            del sys.modules["PySide6"]
            for mod in list(sys.modules):
                if mod.startswith("PySide6"):
                    del sys.modules[mod]

        importlib.import_module("vibeocr.application")
        assert "PySide6" not in sys.modules, (
            "导入 vibeocr.application 不应加载 PySide6（application 是 UI-free 边界）"
        )

    def test_import_contracts_no_pyside6(self):
        """导入 vibeocr.application.contracts 不应加载 PySide6。"""
        _purge_modules("vibeocr.application")
        if "PySide6" in sys.modules:
            del sys.modules["PySide6"]
            for mod in list(sys.modules):
                if mod.startswith("PySide6"):
                    del sys.modules[mod]

        importlib.import_module("vibeocr.application.contracts")
        assert "PySide6" not in sys.modules

    def test_import_ocr_facade_no_pyside6(self):
        """导入 vibeocr.application.ocr_facade 不应加载 PySide6。"""
        _purge_modules("vibeocr.application")
        if "PySide6" in sys.modules:
            del sys.modules["PySide6"]
            for mod in list(sys.modules):
                if mod.startswith("PySide6"):
                    del sys.modules[mod]

        importlib.import_module("vibeocr.application.ocr_facade")
        assert "PySide6" not in sys.modules

    def test_import_pdf_facade_no_pyside6(self):
        """导入 vibeocr.application.pdf_facade 不应加载 PySide6。"""
        _purge_modules("vibeocr.application")
        if "PySide6" in sys.modules:
            del sys.modules["PySide6"]
            for mod in list(sys.modules):
                if mod.startswith("PySide6"):
                    del sys.modules[mod]

        importlib.import_module("vibeocr.application.pdf_facade")
        assert "PySide6" not in sys.modules

    def test_import_settings_facade_no_pyside6(self):
        """导入 vibeocr.application.settings_facade 不应加载 PySide6。"""
        _purge_modules("vibeocr.application")
        if "PySide6" in sys.modules:
            del sys.modules["PySide6"]
            for mod in list(sys.modules):
                if mod.startswith("PySide6"):
                    del sys.modules[mod]

        importlib.import_module("vibeocr.application.settings_facade")
        assert "PySide6" not in sys.modules

    def test_contracts_source_has_no_qt(self):
        """contracts.py 源码不应 import PySide6。"""
        import inspect

        from vibeocr.application import contracts

        source = inspect.getsource(contracts)
        assert "from PySide6" not in source
        assert "import PySide6" not in source
