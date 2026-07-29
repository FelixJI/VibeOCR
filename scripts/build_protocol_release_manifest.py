"""Build a deterministic Protocol release manifest and checksum index."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def _expected_artifact_names(protocol_version: str) -> frozenset[str]:
    return frozenset(
        {
            f"vibeocr-runtime-openapi-{protocol_version}.yaml",
            f"vibeocr-runtime-schemas-{protocol_version}.zip",
            f"vibeocr_runtime_contracts-{protocol_version}-py3-none-any.whl",
            f"vibeocr_runtime_client-{protocol_version}-py3-none-any.whl",
            f"VibeOCR.Runtime.Contracts.{protocol_version}.nupkg",
            f"VibeOCR.Runtime.Client.{protocol_version}.nupkg",
            f"vibeocr-runtime-golden-{protocol_version}.zip",
            "SBOM.spdx.json",
        }
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _git_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        text=True,
        encoding="utf-8",
    ).strip()


def build_protocol_release_manifest(
    *,
    protocol_version: str,
    source_commit: str,
    build_workflow: str,
    artifacts: tuple[Path, ...],
    output_dir: Path,
) -> Path:
    if not _SEMVER.fullmatch(protocol_version):
        raise ValueError("protocol_version must be stable SemVer")
    if not _FULL_SHA.fullmatch(source_commit):
        raise ValueError("source_commit must be a full lowercase Git SHA")
    if not build_workflow.strip():
        raise ValueError("build_workflow is required")
    if not artifacts:
        raise ValueError("at least one Protocol artifact is required")

    sources = [path.resolve(strict=True) for path in artifacts]
    names = [path.name for path in sources]
    if len(names) != len(set(names)):
        raise ValueError("Protocol artifact filenames must be unique")
    if "release-manifest.json" in names or "SHA256SUMS" in names:
        raise ValueError("generated metadata cannot be supplied as an artifact")
    expected_names = _expected_artifact_names(protocol_version)
    actual_names = set(names)
    if actual_names != expected_names:
        details: list[str] = []
        missing = sorted(expected_names - actual_names)
        unexpected = sorted(actual_names - expected_names)
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if unexpected:
            details.append(f"unexpected: {', '.join(unexpected)}")
        raise ValueError(
            "Protocol artifact set does not match the required release assets; "
            + "; ".join(details)
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for source in sorted(sources, key=lambda item: item.name):
        destination = output_dir / source.name
        if source != destination:
            shutil.copyfile(source, destination)
        copied.append(destination)

    manifest = {
        "schema_version": 1,
        "protocol_version": protocol_version,
        "source_commit": source_commit,
        "build_workflow": build_workflow,
        "artifacts": {
            path.name: {
                "sha256": _sha256(path),
                "size": path.stat().st_size,
            }
            for path in copied
        },
    }
    manifest_path = output_dir / "release-manifest.json"
    manifest_path.write_bytes(_canonical_json(manifest))
    checksum_targets = [*copied, manifest_path]
    (output_dir / "SHA256SUMS").write_text(
        "".join(
            f"{_sha256(path)}  {path.name}\n"
            for path in sorted(checksum_targets, key=lambda item: item.name)
        ),
        encoding="utf-8",
        newline="\n",
    )
    return manifest_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol-version", required=True)
    parser.add_argument("--source-commit")
    parser.add_argument("--build-workflow", required=True)
    parser.add_argument("--artifact", action="append", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    path = build_protocol_release_manifest(
        protocol_version=args.protocol_version,
        source_commit=args.source_commit or _git_sha(),
        build_workflow=args.build_workflow,
        artifacts=tuple(args.artifact),
        output_dir=args.output_dir,
    )
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
