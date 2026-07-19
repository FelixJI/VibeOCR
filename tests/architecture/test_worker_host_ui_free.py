"""WorkerHost must be importable and self-testable without PySide6/Qt.

Per ADR §自动化架构守卫第 2 条: the WorkerHost serves both frontends and must
never pull in the PySide6 UI dependency tree. Two complementary strategies:

1. **Static AST scan** (parametrized): no ``vibeocr/worker_host`` .py file may
   contain a ``PySide6``/``qasync``/``PyQt`` import. This is the load-bearing
   gate — it is process-state-free and catches violations without executing
   the import.

2. **Module-graph scan**: walks ``sys.modules`` after importing worker_host
   and asserts none of the forbidden UI packages are present. Unlike an
   import-hook mutation, this only *observes* the loaded graph and cannot
   corrupt other tests.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKER_HOST_DIRS = (
    _REPO_ROOT / "packages" / "vibeocr-client-py" / "src" / "vibeocr" / "worker_host",
    _REPO_ROOT / "packages" / "vibeocr-backend" / "src" / "vibeocr" / "worker_host",
)
_FORBIDDEN_MODULES = ("PySide6", "qasync", "PyQt5", "PyQt6")


@pytest.mark.parametrize(
    "py_file",
    sorted(path for root in _WORKER_HOST_DIRS for path in root.rglob("*.py")),
)
def test_worker_host_source_has_no_ui_imports(py_file: Path) -> None:
    """No worker_host .py file may import PySide6/qasync/PyQt."""
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names = [node.module]
        for name in names:
            top = name.split(".")[0]
            assert top not in _FORBIDDEN_MODULES, (
                f"{py_file.relative_to(_REPO_ROOT)}:{node.lineno} "
                f"imports {top!r} — WorkerHost must stay UI-free per ADR."
            )


def test_worker_host_import_does_not_load_pyside() -> None:
    """Importing worker_host.main must not pull PySide6 into sys.modules.

    Observation-only: we snapshot the forbidden modules *before* and verify
    none are *newly* added *after* importing the worker_host entry point.
    Using a delta (not absolute absence) keeps this robust regardless of
    whether PySide6 was already loaded by another test in the session.
    No sys.modules mutation, no import-hook patching — so this test cannot
    corrupt sibling tests.
    """
    import importlib

    def _forbidden_loaded() -> set[str]:
        return {
            name
            for name in sys.modules
            if name.split(".")[0] in _FORBIDDEN_MODULES
        }

    before = _forbidden_loaded()
    mod = importlib.import_module("vibeocr.worker_host.main")
    assert mod is not None
    newly_loaded = _forbidden_loaded() - before
    assert not newly_loaded, (
        "导入 worker_host.main 触发了 UI 依赖加载，违反 ADR §守卫第 2 条：\n"
        + "\n".join(sorted(newly_loaded))
    )
