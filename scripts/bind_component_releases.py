"""Verify immutable upstream release metadata and write committed lock files."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _write_json(path: Path, value: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def verify_protocol_release(
    release_dir: Path,
    *,
    version: str,
) -> tuple[Path, dict[str, object]]:
    root = release_dir.resolve(strict=True)
    manifest_path = root / "release-manifest.json"
    manifest = _load_json(manifest_path)
    if manifest.get("protocol_version") != version:
        raise ValueError("Protocol manifest version mismatch")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("Protocol manifest artifacts must be an object")
    for name, record in artifacts.items():
        if not isinstance(name, str) or not isinstance(record, dict):
            raise ValueError("invalid Protocol artifact record")
        path = root / name
        if not path.is_file():
            raise FileNotFoundError(path)
        if record.get("sha256") != _sha256(path):
            raise ValueError(f"Protocol artifact hash mismatch: {name}")
        if record.get("size") != path.stat().st_size:
            raise ValueError(f"Protocol artifact size mismatch: {name}")
    return manifest_path, manifest


def bind_protocol_release(
    *,
    release_dir: Path,
    repository: str,
    version: str,
    output: Path,
) -> Path:
    manifest_path, manifest = verify_protocol_release(release_dir, version=version)
    return _write_json(
        output,
        {
            "schema_version": 1,
            "repository": repository,
            "version": version,
            "manifest_sha256": _sha256(manifest_path),
            "artifacts": manifest["artifacts"],
        },
    )


def bind_product_releases(
    *,
    protocol_release_dir: Path,
    backend_release_dir: Path,
    protocol_repository: str,
    protocol_version: str,
    backend_repository: str,
    backend_version: str,
    profile: str,
    required_capabilities: tuple[str, ...],
    output: Path,
) -> Path:
    protocol_manifest_path, _ = verify_protocol_release(
        protocol_release_dir,
        version=protocol_version,
    )
    backend_root = backend_release_dir.resolve(strict=True)
    runtime_manifest_path = backend_root / "runtime-manifest.json"
    runtime_manifest = _load_json(runtime_manifest_path)
    if runtime_manifest.get("backend_version") != backend_version:
        raise ValueError("Backend manifest version mismatch")
    backend_wheel_name = runtime_manifest.get("backend_wheel")
    if not isinstance(backend_wheel_name, str):
        raise ValueError("Backend manifest wheel name is missing")
    backend_wheel = backend_root / backend_wheel_name
    if runtime_manifest.get("backend_sha256") != _sha256(backend_wheel):
        raise ValueError("Backend wheel hash mismatch")
    capabilities = runtime_manifest.get("capabilities")
    if not isinstance(capabilities, list) or not all(
        isinstance(item, str) for item in capabilities
    ):
        raise ValueError("Backend capabilities must be a string array")
    missing = sorted(set(required_capabilities) - set(capabilities))
    if missing:
        raise ValueError(f"Backend is missing required capabilities: {missing}")
    profiles = runtime_manifest.get("profiles")
    if not isinstance(profiles, dict) or profile not in profiles:
        raise ValueError(f"Backend profile is missing: {profile}")
    return _write_json(
        output,
        {
            "schema_version": 1,
            "protocol": {
                "repository": protocol_repository,
                "version": protocol_version,
                "manifest_sha256": _sha256(protocol_manifest_path),
            },
            "backend": {
                "repository": backend_repository,
                "version": backend_version,
                "artifact_sha256": _sha256(backend_wheel),
                "runtime_manifest_sha256": _sha256(runtime_manifest_path),
                "profile": profile,
            },
            "required_capabilities": sorted(set(required_capabilities)),
        },
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    protocol = subparsers.add_parser("protocol-lock")
    protocol.add_argument("--release-dir", type=Path, required=True)
    protocol.add_argument("--repository", required=True)
    protocol.add_argument("--version", required=True)
    protocol.add_argument("--output", type=Path, required=True)
    product = subparsers.add_parser("product-lock")
    product.add_argument("--protocol-release-dir", type=Path, required=True)
    product.add_argument("--backend-release-dir", type=Path, required=True)
    product.add_argument("--protocol-repository", required=True)
    product.add_argument("--protocol-version", required=True)
    product.add_argument("--backend-repository", required=True)
    product.add_argument("--backend-version", required=True)
    product.add_argument("--profile", required=True)
    product.add_argument("--required-capability", action="append", required=True)
    product.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "protocol-lock":
        path = bind_protocol_release(
            release_dir=args.release_dir,
            repository=args.repository,
            version=args.version,
            output=args.output,
        )
    else:
        path = bind_product_releases(
            protocol_release_dir=args.protocol_release_dir,
            backend_release_dir=args.backend_release_dir,
            protocol_repository=args.protocol_repository,
            protocol_version=args.protocol_version,
            backend_repository=args.backend_repository,
            backend_version=args.backend_version,
            profile=args.profile,
            required_capabilities=tuple(args.required_capability),
            output=args.output,
        )
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
