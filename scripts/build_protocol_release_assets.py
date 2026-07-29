"""Build deterministic standalone Protocol schema and golden archives."""

from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path

FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def _write_zip(
    members: list[tuple[str, Path]],
    output: Path,
) -> None:
    members = sorted(members, key=lambda item: item[0])
    if not members:
        raise ValueError("archive source is empty")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member_name, path in members:
            info = zipfile.ZipInfo(
                member_name,
                date_time=FIXED_ZIP_TIME,
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes(), compresslevel=9)


def build_protocol_release_assets(
    *,
    contracts_root: Path,
    version: str,
    output_dir: Path,
) -> tuple[Path, Path, Path]:
    contracts_root = contracts_root.resolve(strict=True)
    openapi = contracts_root / "openapi.yaml"
    schemas = contracts_root / "schemas"
    golden = contracts_root / "golden"
    if not openapi.is_file():
        raise FileNotFoundError(openapi)
    if not schemas.is_dir():
        raise FileNotFoundError(schemas)
    if not golden.is_dir():
        raise FileNotFoundError(golden)

    output_dir.mkdir(parents=True, exist_ok=True)
    openapi_output = output_dir / f"vibeocr-runtime-openapi-{version}.yaml"
    schemas_output = output_dir / f"vibeocr-runtime-schemas-{version}.zip"
    golden_output = output_dir / f"vibeocr-runtime-golden-{version}.zip"
    shutil.copyfile(openapi, openapi_output)
    schema_members = [
        (path.relative_to(contracts_root).as_posix(), path)
        for root in (schemas, contracts_root / "contracts" / "schemas")
        if root.is_dir()
        for path in root.rglob("*")
        if path.is_file()
    ]
    schema_members.extend(
        (name, path)
        for name in (
            "bootstrap.schema.json",
            "capabilities.json",
            "errors.json",
        )
        if (path := contracts_root / name).is_file()
    )
    golden_members = [
        (path.relative_to(golden).as_posix(), path)
        for path in golden.rglob("*")
        if path.is_file()
    ]
    _write_zip(schema_members, schemas_output)
    _write_zip(golden_members, golden_output)
    return openapi_output, schemas_output, golden_output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contracts-root", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    for path in build_protocol_release_assets(
        contracts_root=args.contracts_root,
        version=args.version,
        output_dir=args.output_dir,
    ):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
