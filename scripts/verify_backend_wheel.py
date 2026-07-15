"""Verify the shared backend wheel contains no frontend shell files."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import zipfile
from pathlib import Path, PurePosixPath

FORBIDDEN_PARTS = {"views", "widgets", "ui", "pyside", "PySide6", "tests"}


def verify(wheel: Path) -> dict[str, object]:
    errors: list[str] = []
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        for name in names:
            parts = set(PurePosixPath(name).parts)
            if parts & FORBIDDEN_PARTS:
                errors.append(f"frontend path in backend wheel: {name}")
            if name.endswith(".py"):
                tree = ast.parse(archive.read(name), filename=name)
                for node in ast.walk(tree):
                    module = None
                    if isinstance(node, ast.ImportFrom):
                        module = node.module
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            if alias.name.split(".", 1)[0] in {"PySide6", "qasync"}:
                                errors.append(
                                    f"Qt dependency import in backend wheel: {name}"
                                )
                    if module and module.split(".", 1)[0] in {"PySide6", "qasync"}:
                        errors.append(f"Qt dependency import in backend wheel: {name}")
        required = {
            "vibeocr/worker_host/main.py",
            "vibeocr/worker_host/backend_client.py",
            "vibeocr/contracts/pipelines.py",
            "vibeocr/services/pdf_inprocess_client.py",
        }
        missing = sorted(required - set(names))
        errors.extend(f"required backend file missing: {name}" for name in missing)
    if errors:
        raise RuntimeError("\n".join(errors))
    return {
        "wheel": wheel.name,
        "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "file_count": len(names),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    manifest = verify(args.wheel.resolve())
    if args.manifest:
        args.manifest.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
