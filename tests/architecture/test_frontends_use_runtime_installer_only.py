"""Frontends must not understand Backend dependency resolution details."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOTS = (
    ROOT / "apps" / "vibeocr-pyside" / "src" / "vibeocr" / "classic",
    ROOT / "src" / "dotnet" / "VibeOCR.App",
    ROOT / "src" / "dotnet" / "VibeOCR.Platform",
)

FORBIDDEN = (
    "dependency_profiles",
    "ocr_check_modules",
    "install_single_dependency",
    "install_dependencies_batch",
    "paddle-whl.bj.bcebos.com",
    "download.pytorch.org/whl",
    "--require-hashes",
)


def test_frontends_do_not_parse_or_install_python_dependencies() -> None:
    violations: list[str] = []
    scanned = 0
    for root in FRONTEND_ROOTS:
        for path in root.rglob("*"):
            if path.suffix.lower() not in {".py", ".cs"}:
                continue
            scanned += 1
            source = path.read_text(encoding="utf-8")
            text = (
                ast.unparse(ast.parse(source, filename=str(path))).lower()
                if path.suffix.lower() == ".py"
                else source.lower()
            )
            for token in FORBIDDEN:
                if token in text:
                    violations.append(
                        f"{path.relative_to(ROOT).as_posix()}: {token}"
                    )
    assert scanned > 0
    assert not violations, (
        "前端只能调用 Runtime Installer，不得解析/安装 Python 依赖：\n"
        + "\n".join(violations)
    )
