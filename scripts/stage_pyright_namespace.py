"""Stage the installed VibeOCR namespace shape for Pyright.

The workspace ships one logical ``vibeocr`` package across the contracts,
client and backend wheels. At runtime ``pkgutil.extend_path`` joins those
physical fragments. Pyright deliberately does not execute that runtime hook
and resolves only one regular-package root, so checking the source roots
directly produces false missing-import errors.

This script copies the non-overlapping fragments into a temporary physical
package. It does not suppress or downgrade any Pyright diagnostic, and it
fails if two distributions try to own the same runtime path.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FRAGMENT_ROOTS = (
    REPO_ROOT / "packages" / "vibeocr-contracts-py" / "src" / "vibeocr",
    REPO_ROOT / "packages" / "vibeocr-runtime-client-py" / "src" / "vibeocr",
    REPO_ROOT / "packages" / "vibeocr-client-py" / "src" / "vibeocr",
    REPO_ROOT / "packages" / "vibeocr-backend" / "src" / "vibeocr",
)


def stage_namespace(output: Path, fragments: Sequence[Path]) -> Path:
    """Copy namespace fragments into an empty ``output/vibeocr`` directory."""
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError(f"Pyright staging directory is not empty: {output}")
    package_root = output / "vibeocr"
    package_root.mkdir(parents=True, exist_ok=True)
    owners: dict[Path, Path] = {}

    for fragment in fragments:
        fragment = fragment.resolve()
        if not fragment.is_dir():
            raise FileNotFoundError(f"namespace fragment not found: {fragment}")
        for source in sorted(fragment.rglob("*")):
            if not source.is_file():
                continue
            relative = source.relative_to(fragment)
            if "__pycache__" in relative.parts or source.suffix in {".pyc", ".pyo"}:
                continue
            previous = owners.get(relative)
            if previous is not None:
                raise RuntimeError(
                    f"namespace path collision: {relative.as_posix()} "
                    f"in {previous} and {fragment}"
                )
            owners[relative] = fragment
            target = package_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    return package_root


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    package_root = stage_namespace(args.output, DEFAULT_FRAGMENT_ROOTS)
    print(f"Pyright namespace staged: {package_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
