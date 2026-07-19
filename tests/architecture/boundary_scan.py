"""AST-based import boundary scanner for the UI → backend dependency direction.

Public API:
    scan_ui_backend_imports(ui_dirs, root) -> list[BackendImport]
    BACKEND_PACKAGES, UI_PACKAGE_DIRS

A ``BackendImport`` records one ``from vibeocr.<backend_pkg> import ...`` or
``import vibeocr.<backend_pkg>`` statement found inside a UI-layer file.
``ast`` is used (not regex) so multi-line parenthesized imports, aliased
imports and nested imports are all resolved correctly.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

# Packages that constitute the "backend" — the UI must not import these
# directly. The source now lives in the physical pyside workspace package.
#
# NOTE: ``models`` is excluded — per ADR §5.3, pure data models (OCROptions,
# OCRResult, TextBlockOptions, ExportSettings, PdfGlobalSettings, etc.) are
# shared DTOs allowed on both sides. They are Qt-free dataclasses/enums.
# (pdf_document.py has a QPixmap field — a known Phase-4 debt to split out,
# tracked separately; it is not imported by the UI layer.)
BACKEND_PACKAGES: frozenset[str] = frozenset(
    {
        "services",
        "managers",
        "workers",
        "core",
        "application",
        "migration",
    }
)

# UI-layer sub-packages under apps/vibeocr-pyside/src/vibeocr/.
UI_PACKAGE_DIRS: tuple[str, ...] = ("views", "widgets", "ui")


@dataclass(frozen=True)
class BackendImport:
    """One direct UI→backend import statement."""

    file: Path
    lineno: int
    backend_package: str
    statement: str

    @property
    def key(self) -> str:
        """Stable identity for allowlist matching (file:line)."""
        return f"{self.file.as_posix()}:{self.lineno}"

    def render(self) -> str:
        rel = self.file.as_posix()
        return f"  {rel}:{self.lineno}  [{self.backend_package}]  {self.statement}"


def _backend_pkg_from_module(module: str) -> str | None:
    """Return the backend sub-package if ``module`` is vibeocr.<backend>.<...>."""
    if not module.startswith("vibeocr."):
        return None
    parts = module.split(".")
    if len(parts) < 2:
        return None
    sub = parts[1]
    return sub if sub in BACKEND_PACKAGES else None


def _ast_statement(node: ast.AST) -> str:
    """Reconstruct a short human-readable import statement from an AST node."""
    try:
        return ast.unparse(node)  # py3.9+
    except Exception:  # pragma: no cover - fallback only
        return f"<ast {type(node).__name__}>"


def scan_ui_backend_imports(
    ui_dirs: tuple[str, ...],
    root: Path,
) -> list[BackendImport]:
    """Scan all UI-layer .py files for direct backend-package imports.

    Args:
        ui_dirs: sub-package names under the pyside workspace source root.
            (e.g. ``("views", "widgets", "ui")``).
        root: repository root containing ``apps/vibeocr-pyside``.

    Returns:
        Sorted list of :class:`BackendImport` (by file then line).
    """
    base = root / "apps" / "vibeocr-pyside" / "src" / "vibeocr"
    hits: list[BackendImport] = []

    for sub in ui_dirs:
        pkg = base / sub
        if not pkg.is_dir():
            continue
        for py_file in sorted(pkg.rglob("*.py")):
            rel = py_file.relative_to(root)
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            except SyntaxError:
                # Skip unparseable files; they'd fail compilation elsewhere.
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.module is None:
                        continue
                    bpkg = _backend_pkg_from_module(node.module)
                    if bpkg is None:
                        continue
                    hits.append(
                        BackendImport(
                            file=rel,
                            lineno=node.lineno,
                            backend_package=bpkg,
                            statement=_ast_statement(node),
                        )
                    )
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        bpkg = _backend_pkg_from_module(alias.name)
                        if bpkg is None:
                            continue
                        hits.append(
                            BackendImport(
                                file=rel,
                                lineno=node.lineno,
                                backend_package=bpkg,
                                statement=_ast_statement(node),
                            )
                        )

    hits.sort(key=lambda h: (str(h.file), h.lineno))
    return hits


__all__ = [
    "BACKEND_PACKAGES",
    "UI_PACKAGE_DIRS",
    "BackendImport",
    "scan_ui_backend_imports",
]
