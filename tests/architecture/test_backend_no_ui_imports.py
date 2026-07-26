"""Backend packages must not import the UI layer (reverse-dependency guard).

Per ADR §强制边界: the dependency direction is strictly UI → SupervisorClient →
Supervisor → application → domain. No backend package may import
``vibeocr.views`` / ``vibeocr.widgets`` / ``vibeocr.ui``.

This direction must be zero. Phase 4「去 Qt 化」已完成 ``update_service.py`` 的
物理拆分——Qt 对话框与更新流程编排（``UpdateDialog`` / ``await_dialog`` /
``UpdateService`` 编排器）移至 ``vibeocr.pyside.update``（Qt 平台壳层），backend
纯逻辑留在 ``vibeocr.services.update_service``。历史唯一已知债务随之清零，
``BACKEND_UI_KNOWN_DEBT`` 现为空集，本守卫对任何 backend→UI 泄漏零容忍。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

BACKEND_PACKAGE_DIRS: tuple[str, ...] = (
    "services",
    "managers",
    "workers",
    "core",
    "models",
    "application",
    "migration",
    "supervisor",
)

UI_MODULES: frozenset[str] = frozenset({"views", "widgets", "ui"})

# Known legacy Qt-into-backend leak. Phase 4 (去 Qt 化) 已完成 update_service.py
# 拆分：Qt 对话框与编排移至 vibeocr.pyside.update，backend 纯逻辑留在
# services.update_service。债务清零，集合为空；新增条目需 ADR 修正案。
BACKEND_UI_KNOWN_DEBT: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Violation:
    file: Path
    lineno: int
    statement: str

    @property
    def key(self) -> str:
        return f"{self.file.as_posix()}:{self.lineno}"


def _scan() -> list[Violation]:
    base = _REPO_ROOT / "packages" / "vibeocr-backend" / "src" / "vibeocr"
    hits: list[Violation] = []
    for sub in BACKEND_PACKAGE_DIRS:
        pkg = base / sub
        if not pkg.is_dir():
            continue
        for py_file in sorted(pkg.rglob("*.py")):
            rel = py_file.relative_to(_REPO_ROOT)
            try:
                tree = ast.parse(
                    py_file.read_text(encoding="utf-8"), filename=str(py_file)
                )
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.module and node.module.startswith("vibeocr."):
                        parts = node.module.split(".")
                        if len(parts) >= 2 and parts[1] in UI_MODULES:
                            hits.append(
                                Violation(rel, node.lineno, ast.unparse(node))
                            )
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("vibeocr."):
                            parts = alias.name.split(".")
                            if len(parts) >= 2 and parts[1] in UI_MODULES:
                                hits.append(
                                    Violation(rel, node.lineno, ast.unparse(node))
                                )
    return hits


def test_backend_does_not_import_ui() -> None:
    """No backend package may import the UI layer (except known debt)."""
    violations = _scan()
    keys = {v.key for v in violations}
    new = keys - BACKEND_UI_KNOWN_DEBT
    if new:
        offenders = [v for v in violations if v.key in new]
        details = "\n".join(
            f"  {v.file.as_posix()}:{v.lineno}  {v.statement}" for v in offenders
        )
        raise AssertionError(
            "后端包 import 了 UI 层，违反 ADR 强制依赖方向（UI → 后端，禁止反向）。\n"
            f"发现 {len(new)} 处新违规：\n{details}"
        )


def test_backend_ui_known_debt_is_real() -> None:
    """Every documented debt entry must still exist (else shrink the debt)."""
    current = {v.key for v in _scan()}
    stale = BACKEND_UI_KNOWN_DEBT - current
    assert not stale, (
        "BACKEND_UI_KNOWN_DEBT 含已修复的条目，请删除以保持清单准确：\n"
        + "\n".join(sorted(stale))
    )
