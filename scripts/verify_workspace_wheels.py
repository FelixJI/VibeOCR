"""Verify physical ownership and dependency metadata of the five release wheels."""

from __future__ import annotations

import argparse
import email
import re
import zipfile
from pathlib import Path

EXPECTED = {
    "vibeocr",
    "vibeocr-backend",
    "vibeocr-client-py",
    "vibeocr-contracts-py",
    "vibeocr-pyside",
}
CODE_DISTRIBUTIONS = EXPECTED - {"vibeocr"}


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
            if distribution == "vibeocr":
                assert not product_paths, "root compatibility wheel must be code-free"
                continue
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
    assert len(set(versions.values())) == 1, f"wheel versions differ: {versions}"
    assert archive_owners["vibeocr/__init__.py"] == "vibeocr-contracts-py"
    assert archive_owners["vibeocr/env_manager.py"] == "vibeocr-client-py"
    assert (
        archive_owners["vibeocr/dependency_profiles.json"]
        == "vibeocr-client-py"
    )
    assert archive_owners["vibeocr/worker_host/main.py"] == "vibeocr-backend"
    assert archive_owners["vibeocr/main.py"] == "vibeocr-pyside"
    assert archive_owners["vibeocr/ui/main_window.ui"] == "vibeocr-pyside"
    assert (
        archive_owners["vibeocr/protocol/v1/methods.schema.json"]
        == "vibeocr-contracts-py"
    )

    with zipfile.ZipFile(wheels["vibeocr"]) as zf:
        requires = _metadata(zf).get_all("Requires-Dist", [])
    for internal in CODE_DISTRIBUTIONS:
        assert any(_normalize(req.split()[0].split("=")[0]) == internal for req in requires)

    with zipfile.ZipFile(wheels["vibeocr-backend"]) as zf:
        requires = _metadata(zf).get_all("Requires-Dist", [])
    assert any("extra == 'cpu'" in req and req.startswith("paddlepaddle") for req in requires)
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
