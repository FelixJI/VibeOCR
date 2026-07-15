"""Build the shared WorkerHost wheel from an explicit source allow-list."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "vibeocr"
ALLOWLIST = ROOT / "config" / "backend_artifact_include.txt"


def _version() -> str:
    match = re.search(
        r'(?m)^version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"',
        (ROOT / "pyproject.toml").read_text(encoding="utf-8"),
    )
    if not match:
        raise RuntimeError("project version not found")
    return match.group(1)


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
    version = _version()
    output_dir.mkdir(parents=True, exist_ok=True)
    wheel = output_dir / f"vibeocr_backend-{version}-py3-none-any.whl"
    dist_info = f"vibeocr_backend-{version}.dist-info"
    entries: dict[str, bytes] = {}
    for path in _allowed_files():
        relative = path.relative_to(SOURCE).as_posix()
        entries[f"vibeocr/{relative}"] = path.read_bytes()
    entries[f"{dist_info}/METADATA"] = (
        "Metadata-Version: 2.4\n"
        f"Name: vibeocr-backend\nVersion: {version}\n"
        "Requires-Python: >=3.13,<3.14\n"
        "Requires-Dist: pillow>=12.3.0\nRequires-Dist: numpy>=2.3.5\n"
        "Requires-Dist: httpx>=0.28.1\nRequires-Dist: pymupdf>=1.28.0\n"
        "Requires-Dist: pydantic>=2.13.4\nRequires-Dist: fastapi>=0.139.0\n"
        "Requires-Dist: uvicorn>=0.51.0\nRequires-Dist: fonttools>=4.63.0\n"
        "Requires-Dist: markdown>=3.10.2\nRequires-Dist: python-docx>=1.2.0\n"
        "Requires-Dist: openpyxl>=3.1.5\nRequires-Dist: qrcode[pil]>=8.2\n"
        "Requires-Dist: python-barcode>=0.16.1\n"
        "Requires-Dist: opencv-contrib-python>=4.10.0.84\n"
        "Requires-Dist: pyzbar>=0.1.9\n"
        "Requires-Dist: paddlepaddle-gpu>=3.3.1\nRequires-Dist: torch>=2.6.0\n"
        "Requires-Dist: paddleocr[doc-parser]>=3.7.0\n"
        "Requires-Dist: mineru[core]>=3.4.3\n"
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
