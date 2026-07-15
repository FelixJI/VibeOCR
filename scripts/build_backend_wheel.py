"""Build the shared WorkerHost wheel from an explicit source allow-list."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "vibeocr"
ALLOWLIST = ROOT / "config" / "backend_artifact_include.txt"
# The backend workspace package is the single source of truth for the UI-free
# runtime dependency list. Reading it here (instead of duplicating the list as a
# hardcoded METADATA string) keeps the wheel in lockstep with uv lock / uv sync
# and qa/upgrade_deps.py, which both update this file.
BACKEND_PYPROJECT = ROOT / "packages" / "vibeocr-backend" / "pyproject.toml"


def _backend_metadata() -> tuple[str, str, list[str]]:
    """Return ``(version, requires_python, external_deps)`` from the backend package.

    ``external_deps`` excludes ``vibeocr-*`` workspace-internal references: the
    wheel is a standalone artifact and must not declare a dependency on a
    package that only resolves inside the uv workspace.
    """
    with BACKEND_PYPROJECT.open("rb") as handle:
        data = tomllib.load(handle)
    project = data["project"]
    deps = project["dependencies"]
    external = [dep for dep in deps if not dep.startswith("vibeocr-")]
    return project["version"], project["requires-python"], external


def _allowed_files() -> list[Path]:
    files: set[Path] = set()
    for raw in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        entry = raw.strip()
        if not entry or entry.startswith("#"):
            continue
        candidate = (SOURCE / entry).resolve()
        if SOURCE.resolve() not in candidate.parents and candidate != SOURCE.resolve():
            raise RuntimeError(f"allow-list entry escapes source root: {entry}")
        if not candidate.exists():
            raise RuntimeError(f"allow-list entry missing: {entry}")
        if candidate.is_dir():
            files.update(
                path
                for path in candidate.rglob("*")
                if path.is_file()
                and "__pycache__" not in path.parts
                and path.suffix != ".pyc"
            )
        else:
            files.add(candidate)
    return sorted(files, key=lambda path: path.as_posix())


def _record_line(name: str, data: bytes) -> tuple[str, str, str]:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
    return name, f"sha256={digest.decode('ascii')}", str(len(data))


def build(output_dir: Path) -> Path:
    version, requires_python, external_deps = _backend_metadata()
    output_dir.mkdir(parents=True, exist_ok=True)
    wheel = output_dir / f"vibeocr_backend-{version}-py3-none-any.whl"
    dist_info = f"vibeocr_backend-{version}.dist-info"
    entries: dict[str, bytes] = {}
    for path in _allowed_files():
        relative = path.relative_to(SOURCE).as_posix()
        entries[f"vibeocr/{relative}"] = path.read_bytes()
    requires_dist = "".join(f"Requires-Dist: {dep}\n" for dep in external_deps)
    entries[f"{dist_info}/METADATA"] = (
        "Metadata-Version: 2.4\n"
        f"Name: vibeocr-backend\nVersion: {version}\n"
        f"Requires-Python: {requires_python}\n"
        f"{requires_dist}"
    ).encode()
    entries[f"{dist_info}/WHEEL"] = (
        b"Wheel-Version: 1.0\nGenerator: vibeocr-build\n"
        b"Root-Is-Purelib: true\nTag: py3-none-any\n"
    )

    rows = [_record_line(name, data) for name, data in sorted(entries.items())]
    record_name = f"{dist_info}/RECORD"
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerows([*rows, (record_name, "", "")])
    entries[record_name] = stream.getvalue().encode()

    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(entries.items()):
            archive.writestr(name, data)
    print(wheel)
    return wheel


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist" / "backend")
    args = parser.parse_args()
    build(args.output_dir.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
