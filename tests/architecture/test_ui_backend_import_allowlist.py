"""Final UI→backend zero-dependency architecture gate."""

from __future__ import annotations

from pathlib import Path

from tests.architecture.boundary_scan import (
    UI_PACKAGE_DIRS,
    scan_ui_backend_imports,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
def test_ui_has_zero_backend_imports() -> None:
    hits = scan_ui_backend_imports(UI_PACKAGE_DIRS, _REPO_ROOT)
    assert not hits, (
        "PySide UI 只能通过 contracts/client/pyside 壳层访问能力，"
        "不得直接 import backend：\n" + "\n".join(hit.render() for hit in hits)
    )
