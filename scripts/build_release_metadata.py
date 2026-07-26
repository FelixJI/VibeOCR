"""Build the version metadata and independent updater for a release layout.

This is the single Python entry used by the WinUI release script.  Keeping the
updater build here prevents CI from producing a layout that the artifact
verifier (and the runtime update hand-off) cannot actually consume.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import bump_version


def build_release_metadata(*, version: str, output: Path) -> None:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    work = output / ".updater-build"
    work.mkdir(parents=True, exist_ok=True)
    original_dist_base = bump_version.DIST_BASE_DIR
    try:
        bump_version.DIST_BASE_DIR = work
        bump_version._generate_version_json(version, output)
        version_file = bump_version._generate_version_file(
            version,
            work,
            target="updater",
        )
        if not bump_version._build_updater(output, version_file=version_file):
            raise RuntimeError("updater build failed")
        updater = output / "updater.exe"
        if not updater.is_file() or updater.stat().st_size == 0:
            raise RuntimeError("updater build produced no non-empty updater.exe")
    finally:
        bump_version.DIST_BASE_DIR = original_dist_base
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build_release_metadata(version=args.version, output=args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
