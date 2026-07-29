"""表格 schema 必须由 contracts wheel 唯一、稳定地携带。"""

from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[2]
VERIFIER_PATH = REPO_ROOT / "scripts" / "verify_workspace_wheels.py"


def _load_verifier():
    spec = importlib.util.spec_from_file_location(
        "verify_workspace_wheels", VERIFIER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_wheel(
    root: Path,
    distribution: str,
    product_files: list[str],
    *,
    requirements: tuple[str, ...] = (),
) -> None:
    normalized = distribution.replace("-", "_")
    wheel = root / f"{normalized}-1.0-py3-none-any.whl"
    metadata = [
        "Metadata-Version: 2.1",
        f"Name: {distribution}",
        "Version: 1.0",
        *(f"Requires-Dist: {requirement}" for requirement in requirements),
        "",
    ]
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(f"{normalized}-1.0.dist-info/METADATA", "\n".join(metadata))
        for product_file in product_files:
            archive.writestr(product_file, "{}")


def _write_workspace_wheels(
    root: Path, *, schema_owner: str = "vibeocr-runtime-contracts"
) -> None:
    code_files = {
        "vibeocr-runtime-contracts": [
            "vibeocr/runtime_contracts/golden/golden.json",
        ],
        "vibeocr-runtime-client": [
            "vibeocr/runtime_client/client.py",
            "vibeocr/runtime_client/mock_server.py",
        ],
        "vibeocr-backend": [
            "vibeocr/backend/env_manager.py",
            "vibeocr/backend/dependency_profiles.json",
            "vibeocr/backend/supervisor/main.py",
        ],
        "vibeocr-classic": [
            "vibeocr/classic/main.py",
            "vibeocr/classic/ui/main_window.ui",
        ],
    }
    code_files[schema_owner].append(
        "vibeocr/runtime_contracts/contracts/schemas/table-v1.schema.json"
    )
    for distribution in (
        "vibeocr-runtime-contracts",
        "vibeocr-runtime-client",
        "vibeocr-backend",
        "vibeocr-classic",
    ):
        requirements: tuple[str, ...] = ()
        if distribution == "vibeocr-runtime-client":
            requirements = ("vibeocr-runtime-contracts==1.0",)
        if distribution == "vibeocr-backend":
            requirements = (
                "paddlepaddle>=3; extra == 'cpu'",
                "paddlepaddle-gpu>=3; extra == 'gpu-cu126'",
            )
        _write_wheel(
            root,
            distribution,
            code_files.get(distribution, []),
            requirements=requirements,
        )


def test_workspace_wheel_verifier_rejects_table_schema_outside_contracts(
    tmp_path: Path,
) -> None:
    verifier = _load_verifier()
    _write_workspace_wheels(tmp_path, schema_owner="vibeocr-backend")

    with pytest.raises(AssertionError, match=r"table-v1\.schema\.json"):
        verifier.verify(tmp_path)
