"""Bind an exact backend wheel and product manifest into a frontend ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frontend", choices=("pyside", "winui"), required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--backend-wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source = args.input.resolve()
    wheel = args.backend_wheel.resolve()
    output = args.output.resolve()
    wheel_hash = hashlib.sha256(wheel.read_bytes()).hexdigest()
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True, encoding="utf-8"
    ).strip()

    with tempfile.TemporaryDirectory(prefix="vibeocr-bind-") as temp:
        stage = Path(temp)
        with zipfile.ZipFile(source) as archive:
            archive.extractall(stage)
        roots = list(stage.iterdir())
        root = roots[0] if len(roots) == 1 and roots[0].is_dir() else stage
        backend_dir = root / "backend"
        backend_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(wheel, backend_dir / wheel.name)
        manifest = {
            "frontend": args.frontend,
            "frontend_version": args.version,
            "backend_wheel": wheel.name,
            "backend_sha256": wheel_hash,
            "protocol_major": 1,
            "source_commit": commit,
        }
        (root / "product-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    archive.write(path, f"{root.name}/{path.relative_to(root).as_posix()}")
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    Path(f"{output}.sha256").write_text(
        f"{digest}  {output.name}\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
