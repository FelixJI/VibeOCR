"""Verify physical ownership and dependency metadata of the four release wheels."""

from __future__ import annotations

import argparse
import email
import re
import zipfile
from pathlib import Path

EXPECTED = {
    "vibeocr-backend",
    "vibeocr-classic",
    "vibeocr-runtime-contracts",
    "vibeocr-runtime-client",
}


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _metadata(zf: zipfile.ZipFile) -> email.message.Message:
    name = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
    return email.message_from_bytes(zf.read(name))


def verify(directory: Path) -> None:
    wheels: dict[str, Path] = {}
    archive_owners: dict[str, str] = {}

    for wheel in sorted(directory.glob("*.whl")):
        with zipfile.ZipFile(wheel) as zf:
            metadata = _metadata(zf)
            distribution = _normalize(str(metadata["Name"]))
            assert distribution not in wheels, (
                f"duplicate distribution wheel: {distribution}: "
                f"{wheels[distribution].name}, {wheel.name}"
            )
            wheels[distribution] = wheel
            product_paths = [
                name
                for name in zf.namelist()
                if ".dist-info/" not in name and not name.endswith("/")
            ]
            assert not any(
                "__pycache__" in name or name.startswith("vibeocr/output/")
                for name in product_paths
            ), f"forbidden runtime artifact in {wheel.name}"
            for name in product_paths:
                previous = archive_owners.setdefault(name, distribution)
                assert previous == distribution, (
                    f"wheel path collision: {name} in {previous} and {distribution}"
                )

    assert set(wheels) == EXPECTED, f"unexpected wheel set: {sorted(wheels)}"
    versions: dict[str, str] = {}
    for distribution, wheel in wheels.items():
        with zipfile.ZipFile(wheel) as zf:
            versions[distribution] = str(_metadata(zf)["Version"])
    protocol_distributions = {
        "vibeocr-runtime-contracts",
        "vibeocr-runtime-client",
    }
    assert len({versions[name] for name in protocol_distributions}) == 1, (
        f"protocol wheel versions differ: {versions}"
    )
    assert "vibeocr/__init__.py" not in archive_owners
    assert archive_owners["vibeocr/backend/env_manager.py"] == "vibeocr-backend"
    assert (
        archive_owners["vibeocr/backend/dependency_profiles.json"]
        == "vibeocr-backend"
    )
    assert (
        archive_owners["vibeocr/backend/supervisor/main.py"] == "vibeocr-backend"
    )
    assert archive_owners["vibeocr/classic/main.py"] == "vibeocr-classic"
    assert (
        archive_owners["vibeocr/classic/ui/main_window.ui"] == "vibeocr-classic"
    )
    assert (
        archive_owners["vibeocr/runtime_contracts/golden/golden.json"]
        == "vibeocr-runtime-contracts"
    )
    assert (
        archive_owners["vibeocr/runtime_client/client.py"]
        == "vibeocr-runtime-client"
    )
    table_schema_path = (
        "vibeocr/runtime_contracts/contracts/schemas/table-v1.schema.json"
    )
    assert archive_owners.get(table_schema_path) == "vibeocr-runtime-contracts", (
        f"{table_schema_path} must be owned by vibeocr-runtime-contracts, "
        f"got {archive_owners.get(table_schema_path)!r}"
    )
    assert not any(
        path.startswith(("vibeocr/worker_host/", "vibeocr/protocol/v1/"))
        for path in archive_owners
    ), "legacy WorkerHost/protocol-v1 paths must not ship"

    with zipfile.ZipFile(wheels["vibeocr-runtime-client"]) as zf:
        runtime_requires = _metadata(zf).get_all("Requires-Dist", [])
    contracts_pin = (
        "vibeocr-runtime-contracts"
        f"=={versions['vibeocr-runtime-contracts']}"
    )
    assert any(
        requirement.replace(" ", "").lower() == contracts_pin
        for requirement in runtime_requires
    ), f"Runtime Client must pin {contracts_pin}"

    with zipfile.ZipFile(wheels["vibeocr-backend"]) as zf:
        requires = _metadata(zf).get_all("Requires-Dist", [])
    assert any(
        "extra == 'cpu'" in req and req.startswith("paddlepaddle") for req in requires
    )
    assert any(
        "extra == 'gpu-cu126'" in req and req.startswith("paddlepaddle-gpu")
        for req in requires
    )
    assert not any(
        req.startswith(("paddlepaddle;", "paddlepaddle-gpu;", "torch;"))
        for req in requires
    ), "engine dependencies must not be unconditional"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel_dir", type=Path)
    args = parser.parse_args()
    verify(args.wheel_dir)
    print(f"workspace wheel verification OK: {args.wheel_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
