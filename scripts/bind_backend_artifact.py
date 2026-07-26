"""Bind the exact five-wheel Python release set into a frontend ZIP."""

from __future__ import annotations

import argparse
import email
import hashlib
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

EXPECTED_WHEELS = {
    "vibeocr",
    "vibeocr-backend",
    "vibeocr-client-py",
    "vibeocr-contracts-py",
    "vibeocr-pyside",
}
REQUIRED_WHEEL_MEMBERS = {
    "vibeocr-backend": "vibeocr/supervisor/main.py",
    "vibeocr-contracts-py": "vibeocr/protocol/v2/golden/golden.json",
}
FORBIDDEN_WHEEL_PREFIXES = (
    "vibeocr/worker_host/",
    "vibeocr/protocol/v1/",
)


def _distribution_metadata(wheel: Path) -> tuple[str, str]:
    with zipfile.ZipFile(wheel) as archive:
        metadata_name = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        metadata = email.message_from_bytes(archive.read(metadata_name))
    name = str(metadata["Name"]).lower().replace("_", "-")
    return name, str(metadata["Version"])


def _verify_runtime_layout(wheels: dict[str, Path]) -> None:
    for distribution, required_member in REQUIRED_WHEEL_MEMBERS.items():
        with zipfile.ZipFile(wheels[distribution]) as archive:
            members = set(archive.namelist())
        if required_member not in members:
            raise RuntimeError(
                f"{distribution} wheel is missing {required_member}"
            )
        legacy = sorted(
            member
            for member in members
            if member.startswith(FORBIDDEN_WHEEL_PREFIXES)
        )
        if legacy:
            raise RuntimeError(
                f"{distribution} wheel contains legacy runtime paths: {legacy}"
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frontend", choices=("pyside", "winui"), required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--input", type=Path, required=True)
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--wheel-dir", type=Path)
    source_group.add_argument("--backend-wheel", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source = args.input.resolve()
    output = args.output.resolve()
    wheel_dir = (
        args.wheel_dir.resolve()
        if args.wheel_dir is not None
        else args.backend_wheel.resolve().parent
    )
    if not source.is_file():
        raise FileNotFoundError(f"frontend archive not found: {source}")
    if not wheel_dir.is_dir():
        raise NotADirectoryError(f"wheel directory not found: {wheel_dir}")

    wheels: dict[str, Path] = {}
    for path in sorted(wheel_dir.glob("*.whl")):
        distribution, version = _distribution_metadata(path)
        if distribution not in EXPECTED_WHEELS:
            continue
        if distribution in wheels:
            raise RuntimeError(
                f"duplicate wheel for {distribution}: "
                f"{wheels[distribution].name}, {path.name}"
            )
        if version != args.version:
            raise RuntimeError(
                f"wheel version mismatch for {distribution}: "
                f"expected {args.version}, found {version} in {path.name}"
            )
        wheels[distribution] = path
    missing = EXPECTED_WHEELS - set(wheels)
    if missing:
        raise RuntimeError(f"release wheel set incomplete: {sorted(missing)}")
    _verify_runtime_layout(wheels)
    wheel_records = [
        {
            "distribution": name,
            "file": wheels[name].name,
            "sha256": hashlib.sha256(wheels[name].read_bytes()).hexdigest(),
        }
        for name in sorted(EXPECTED_WHEELS)
    ]
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
        for wheel in wheels.values():
            shutil.copy2(wheel, backend_dir / wheel.name)
        backend_record = next(
            record
            for record in wheel_records
            if record["distribution"] == "vibeocr-backend"
        )
        manifest = {
            "frontend": args.frontend,
            "frontend_version": args.version,
            "backend_wheel": backend_record["file"],
            "backend_sha256": backend_record["sha256"],
            "python_wheels": wheel_records,
            "protocol_major": 2,
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
