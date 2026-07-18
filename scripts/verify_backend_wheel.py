"""Verify the shared backend wheel contains no frontend shell files."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

FORBIDDEN_PARTS = {"views", "widgets", "ui", "pyside", "PySide6", "tests"}

ROOT = Path(__file__).resolve().parents[1]
BACKEND_PYPROJECT = ROOT / "packages" / "vibeocr-backend" / "pyproject.toml"


def _expected_requires_dist() -> list[str]:
    """External runtime deps declared by the backend workspace package.

    The wheel builder emits these verbatim into METADATA (see
    build_backend_wheel._backend_metadata), so the wheel's Requires-Dist must
    always match this list. This check catches manual edits to either file.
    """
    with BACKEND_PYPROJECT.open("rb") as handle:
        deps = tomllib.load(handle)["project"]["dependencies"]
    return [dep for dep in deps if not dep.startswith("vibeocr-")]


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
            # PDF 后端走独立子进程：服务器与 HTTP 客户端都必须进 backend wheel
            "vibeocr/services/pdf_backend_process.py",
            "vibeocr/services/pdf_backend_client.py",
        }
        missing = sorted(required - set(names))
        errors.extend(f"required backend file missing: {name}" for name in missing)

        # Requires-Dist must match the backend package's external deps exactly.
        metadata_name = next(
            (n for n in names if n.endswith(".dist-info/METADATA")), None
        )
        if metadata_name is None:
            errors.append("METADATA file missing from wheel")
        else:
            wheel_deps = sorted(
                m.group(1)
                for m in re.finditer(
                    r"^Requires-Dist:\s*(.+)$",
                    archive.read(metadata_name).decode("utf-8"),
                    re.MULTILINE,
                )
            )
            expected = sorted(_expected_requires_dist())
            if wheel_deps != expected:
                errors.append(
                    f"wheel Requires-Dist drifts from backend pyproject:\n"
                    f"  wheel only:    {sorted(set(wheel_deps) - set(expected))}\n"
                    f"  pyproject only: {sorted(set(expected) - set(wheel_deps))}"
                )

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
