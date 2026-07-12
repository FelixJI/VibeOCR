"""应用服务边界 import 隔离测试。

验证导入 vibeocr.application 不加载 PySide6——application 层是 UI-free 边界，
供 WorkerHost 和 WinUI 壳共享。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_IMPORT_PROBE = """
import importlib
import sys

importlib.import_module(sys.argv[1])
loaded = sorted(
    name
    for name in sys.modules
    if name == "PySide6" or name.startswith("PySide6.")
)
if loaded:
    raise SystemExit(f"unexpected PySide6 modules: {loaded}")
"""


def _assert_import_does_not_load_pyside6(module_name: str) -> None:
    """在干净子进程中验证导入边界，不破坏 pytest-qt 的 Qt 模块状态。"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_PROJECT_ROOT / "src")
    result = subprocess.run(
        [sys.executable, "-c", _IMPORT_PROBE, module_name],
        cwd=_PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


class TestImportBoundary:
    """application 包导入不得触发 PySide6 加载。"""

    def test_import_application_no_pyside6(self):
        """导入 vibeocr.application 不应加载 PySide6。"""
        _assert_import_does_not_load_pyside6("vibeocr.application")

    def test_import_contracts_no_pyside6(self):
        """导入 vibeocr.application.contracts 不应加载 PySide6。"""
        _assert_import_does_not_load_pyside6("vibeocr.application.contracts")

    def test_import_ocr_facade_no_pyside6(self):
        """导入 vibeocr.application.ocr_facade 不应加载 PySide6。"""
        _assert_import_does_not_load_pyside6("vibeocr.application.ocr_facade")

    def test_import_pdf_facade_no_pyside6(self):
        """导入 vibeocr.application.pdf_facade 不应加载 PySide6。"""
        _assert_import_does_not_load_pyside6("vibeocr.application.pdf_facade")

    def test_import_settings_facade_no_pyside6(self):
        """导入 vibeocr.application.settings_facade 不应加载 PySide6。"""
        _assert_import_does_not_load_pyside6("vibeocr.application.settings_facade")

    def test_contracts_source_has_no_qt(self):
        """contracts.py 源码不应 import PySide6。"""
        import inspect

        from vibeocr.application import contracts

        source = inspect.getsource(contracts)
        assert "from PySide6" not in source
        assert "import PySide6" not in source
