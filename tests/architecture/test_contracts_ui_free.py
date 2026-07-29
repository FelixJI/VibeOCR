"""The shared Python contracts layer must stay stdlib-only and UI-free."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from vibeocr.backend.core.pipelines import OCRPipeline as CompatibilityPipeline
from vibeocr.runtime_contracts.contracts.pipelines import (
    OCRPipeline as ContractPipeline,
)

_CONTRACTS = (
    Path(__file__).parents[2]
    / "packages"
    / "vibeocr-contracts-py"
    / "src"
    / "vibeocr"
    / "runtime_contracts"
)


def test_contracts_do_not_import_vibeocr_or_ui_packages() -> None:
    assert _CONTRACTS.is_dir(), f"contracts source root missing: {_CONTRACTS}"
    sources = sorted(_CONTRACTS.rglob("*.py"))
    assert sources, f"contracts source scan is empty: {_CONTRACTS}"
    forbidden = {"vibeocr", "PySide6", "qasync", "PyQt5", "PyQt6"}
    for source_file in sources:
        tree = ast.parse(source_file.read_text(encoding="utf-8"), source_file.as_posix())
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.level == 0
            ):
                names = [node.module]
            for name in names:
                top_level = name.split(".", 1)[0]
                assert top_level not in forbidden, (
                    f"{source_file.name} imports forbidden dependency {name}"
                )
                assert top_level in sys.stdlib_module_names, (
                    f"{source_file.name} imports non-stdlib dependency {name}"
                )


def test_backend_compatibility_export_uses_exact_contract_enum() -> None:
    assert CompatibilityPipeline is ContractPipeline
