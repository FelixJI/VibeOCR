"""UI→backend import allowlist ratchet (Phase 0 architecture gate).

Asserts that the PySide UI layer (views/widgets/ui) does not grow new direct
imports of backend packages. The frozen baseline lives in
``ui_backend_import_allowlist.txt``. This test is the load-bearing boundary
guard during the PySide→RPC migration (Phases 1–3): each migrated feature
slice must remove entries from the allowlist, and new entries are rejected.

Reversal semantics:
- A NEW import not in the allowlist → FAIL (blocks the leak).
- A REMOVED import still listed → the allowlist is stale and must be shrunk;
  the test prints the removable keys so the developer deletes them, keeping
  the ratchet monotonic.
"""

from __future__ import annotations

from pathlib import Path

from tests.architecture.boundary_scan import (
    UI_PACKAGE_DIRS,
    scan_ui_backend_imports,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALLOWLIST_FILE = Path(__file__).parent / "ui_backend_import_allowlist.txt"


def _load_allowlist() -> set[str]:
    keys: set[str] = set()
    for raw in _ALLOWLIST_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        keys.add(line)
    return keys


def test_no_new_ui_backend_imports() -> None:
    """Every current UI→backend import must already be in the allowlist."""
    allowlist = _load_allowlist()
    current = {h.key for h in scan_ui_backend_imports(UI_PACKAGE_DIRS, _REPO_ROOT)}
    new = current - allowlist
    if new:
        details = "\n".join(sorted(new))
        pytest_fail(
            "PySide UI 引入了新的后端直接 import，未在迁移 allowlist 内。\n"
            "这违反了 ADR 强制边界（UI 只能通过 BackendClient 访问后端）。\n"
            f"新增 {len(new)} 处，必须先迁移该功能、再提交：\n{details}\n"
            "（若确实需要临时新增，请在 DUAL_UI_IMPLEMENTATION_PLAN.md\n"
            " ADR §自动化架构守卫第 6 条明确允许的例外下记录理由。）",
        )


def test_allowlist_does_not_grow() -> None:
    """Allowlisted entries that no longer exist must be removed (ratchet)."""
    allowlist = _load_allowlist()
    current = {h.key for h in scan_ui_backend_imports(UI_PACKAGE_DIRS, _REPO_ROOT)}
    removable = allowlist - current
    if removable:
        details = "\n".join(sorted(removable))
        pytest_fail(
            "迁移已减少 UI→backend import，但 allowlist 未同步收缩。\n"
            "请从 tests/architecture/ui_backend_import_allowlist.txt 删除以下行：\n"
            f"{details}\n"
            "（allowlist 只减不增，保持棘轮单调递减。）",
        )


def test_baseline_count_recorded() -> None:
    """The allowlist header documents the Phase-0 count for traceability."""
    header = _ALLOWLIST_FILE.read_text(encoding="utf-8").splitlines()
    total_line = next((line for line in header if "Total:" in line), "")
    current = {h.key for h in scan_ui_backend_imports(UI_PACKAGE_DIRS, _REPO_ROOT)}
    assert f"Total: {len(current)}" in total_line, (
        "allowlist header 的 Total 行与当前实际数量不符。\n"
        f"header: {total_line!r}\n"
        f"actual: {len(current)}\n"
        "请同步更新 allowlist header 的 Total 行（数量应只减不增）。"
    )


def pytest_fail(msg: str) -> None:
    import pytest

    pytest.fail(msg, pytrace=False)
