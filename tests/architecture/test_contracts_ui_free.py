"""The shared Python contracts layer must stay stdlib-only and UI-free."""

from __future__ import annotations

import ast
from pathlib import Path

from vibeocr.contracts.pipelines import OCRPipeline as ContractPipeline
from vibeocr.core.pipelines import OCRPipeline as CompatibilityPipeline

_CONTRACTS = (
    Path(__file__).parents[2]
    / "packages"
    / "vibeocr-contracts-py"
    / "src"
    / "vibeocr"
    / "contracts"
)


def test_contracts_do_not_import_vibeocr_or_ui_packages() -> None:
    forbidden = {"vibeocr", "PySide6", "qasync", "PyQt5", "PyQt6"}
    for source_file in _CONTRACTS.glob("*.py"):
        tree = ast.parse(source_file.read_text(encoding="utf-8"), source_file.as_posix())
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert name.split(".", 1)[0] not in forbidden, (
                    f"{source_file.name} imports forbidden dependency {name}"
                )


def test_backend_compatibility_export_uses_exact_contract_enum() -> None:
    assert CompatibilityPipeline is ContractPipeline
