"""Create reviewed, history-free source staging trees for the four repositories."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "eb8b6a6715599332ccf331dabf02468d7b1df40c"


@dataclass(frozen=True)
class RepositorySpec:
    slug: str
    version: str
    description: str
    paths: tuple[str, ...]
    python_paths: tuple[str, ...] = ()


SPECS = {
    "protocol": RepositorySpec(
        "vibeocr-protocol",
        "2.0.0",
        "VibeOCR Local Runtime API, Bootstrap Protocol, schemas and SDKs",
        (
            "packages/vibeocr-contracts-py",
            "packages/vibeocr-runtime-client-py",
            "src/dotnet/VibeOCR.Contracts",
            "src/dotnet/VibeOCR.Runtime.Client",
            "tests/contracts",
            "tests/dotnet/VibeOCR.Contracts.Tests",
            "tests/dotnet/VibeOCR.Runtime.Client.Tests",
            "scripts/generate_runtime_protocol.py",
            "scripts/check_openapi_quality.py",
            "scripts/build_protocol_release_manifest.py",
            "scripts/build_protocol_release_assets.py",
            "scripts/build_spdx_sbom.py",
            "scripts/build_release_checksums.py",
            "tests/runtime/test_build_protocol_release_manifest.py",
            "tests/runtime/test_build_protocol_release_assets.py",
            "tests/runtime/test_build_spdx_sbom.py",
            "tests/runtime/test_build_release_checksums.py",
            "global.json",
            "NuGet.Config",
            "Directory.Build.props",
            "Directory.Packages.props",
        ),
        (
            "packages/vibeocr-contracts-py/src",
            "packages/vibeocr-runtime-client-py/src",
        ),
    ),
    "backend": RepositorySpec(
        "vibeocr-backend",
        "0.7.0",
        "UI-free VibeOCR local OCR/PDF runtime",
        (
            "packages/vibeocr-backend",
            "tests/application",
            "tests/core",
            "tests/models",
            "tests/supervisor",
            "tests/table_contract",
            "tests/integration",
            "tests/services",
            "scripts/check_openapi_quality.py",
            "scripts/check_runtime_protocol_conformance.py",
            "scripts/build_runtime_installer.py",
            "scripts/build_runtime_manifest.py",
            "scripts/runtime_installer_entry.py",
            "scripts/bind_component_releases.py",
            "scripts/verify_table_artifact.py",
            "scripts/build_spdx_sbom.py",
            "scripts/build_release_checksums.py",
            "tests/fixtures",
            "tests/runtime/test_build_runtime_manifest.py",
            "tests/runtime/test_build_runtime_installer.py",
            "tests/runtime/test_runtime_installer.py",
            "tests/runtime/test_bind_component_releases.py",
            "tests/runtime/test_build_spdx_sbom.py",
            "tests/runtime/test_build_release_checksums.py",
        ),
    ),
    "classic": RepositorySpec(
        "vibeocr-classic",
        "0.7.0",
        "VibeOCR Classic desktop app built with PySide",
        (
            "apps/vibeocr-pyside",
            "resources",
            "tests/managers",
            "tests/pyside",
            "tests/ui",
            "tests/views",
            "tests/widgets",
            "tests/workers",
            "tests/utils",
            "tests/release_layout/test_pyside_frozen_startup.py",
            "tests/release_layout/test_pyside_stdio.py",
            "scripts/compile_ui.py",
            "scripts/update_replacer.py",
            "scripts/updater_main.py",
            "scripts/verify_pyside_artifact.py",
            "CHANGELOG.md",
            "scripts/build_spdx_sbom.py",
            "scripts/build_release_checksums.py",
            "scripts/bind_component_releases.py",
        ),
    ),
    "next": RepositorySpec(
        "vibeocr-next",
        "0.1.0-preview.1",
        "VibeOCR Next desktop app built with WinUI",
        (
            "src/dotnet/VibeOCR.App",
            "src/dotnet/VibeOCR.Platform",
            "src/dotnet/VibeOCR.Bootstrapper",
            "tests/dotnet/VibeOCR.App.Tests",
            "tests/dotnet/VibeOCR.Platform.Tests",
            "scripts/build_winui_release.ps1",
            "scripts/verify_winui_artifact.ps1",
            "scripts/benchmark_winui_startup.ps1",
            "global.json",
            "NuGet.Config",
            "Directory.Build.props",
            "Directory.Packages.props",
            "scripts/build_spdx_sbom.py",
            "scripts/build_release_checksums.py",
            "scripts/bind_component_releases.py",
        ),
    ),
}


def _copy(relative: str, destination: Path) -> None:
    source = ROOT / relative
    if not source.exists():
        raise FileNotFoundError(relative)
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(
            source,
            target,
            ignore=shutil.ignore_patterns(
                "__pycache__",
                ".pytest_cache",
                "bin",
                "obj",
                "*.pyc",
            ),
        )
    else:
        shutil.copy2(source, target)


def _git_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
    ).strip()


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.rstrip() + "\n", encoding="utf-8", newline="\n")


def _write_common(
    destination: Path,
    *,
    name: str,
    spec: RepositorySpec,
    cutover_commit: str,
) -> None:
    shutil.copy2(ROOT / "LICENSE", destination / "LICENSE")
    _write_text(
        destination / "README.md",
        f"# {spec.slug}\n\n{spec.description}.\n\n"
        f"Initial component version: `{spec.version}`.\n",
    )
    _write_text(
        destination / "SECURITY.md",
        "# Security Policy\n\n"
        "Please report vulnerabilities privately through GitHub Security "
        "Advisories. Do not open a public issue for undisclosed vulnerabilities.\n",
    )
    _write_text(
        destination / "CONTRIBUTING.md",
        "# Contributing\n\n"
        "Open an issue before large changes. Keep generated files reproducible, "
        "run the repository CI commands, and never commit local path or editable "
        "dependencies.\n",
    )
    _write_text(
        destination / "MIGRATION.md",
        "# Repository migration\n\n"
        "Source repository: https://github.com/FelixJI/VibeOCR\n\n"
        f"Source commit: `{BASELINE}`\n\n"
        f"Cutover commit: `{cutover_commit}`\n\n"
        f"Original paths: `{', '.join(spec.paths)}`\n\n"
        "Migration date: `2026-07-29`\n\n"
        f"Initial component version: `{spec.version}`\n",
    )
    _write_text(
        destination / ".gitignore",
        "__pycache__/\n*.py[cod]\n.venv/\n.pytest_cache/\n.ruff_cache/\n"
        "bin/\nobj/\ndist/\nbuild/\nartifacts/\n"
        "dev-overrides.json\n",
    )
    _write_text(
        destination / "repository.json",
        json.dumps(
            {
                "schema_version": 1,
                "repository": f"FelixJI/{spec.slug}",
                "component": name,
                "version": spec.version,
                "cutover_commit": cutover_commit,
            },
            indent=2,
            sort_keys=True,
        ),
    )
    if spec.python_paths:
        pythonpath = ", ".join(f'"{item}"' for item in spec.python_paths)
        _write_text(
            destination / "pyproject.toml",
            "[tool.pytest.ini_options]\n"
            f"pythonpath = [{pythonpath}]\n"
            'testpaths = ["tests"]\n'
            'addopts = "-m \'not slow\'"\n\n'
            "[tool.ruff]\n"
            'target-version = "py313"\n'
            'line-length = 88\n',
        )
    _write_text(
        destination / ".github/workflows/ci.yml",
        _ci_workflow(name),
    )
    _write_text(
        destination / ".github/workflows/release.yml",
        _release_workflow(name, spec.version),
    )
    _write_text(destination / "scripts/build-release.ps1", _build_release_script(name))
    _write_text(destination / "scripts/dev-link.ps1", _dev_link_script(name))
    if name == "backend":
        _write_text(
            destination / "release/python-runtime.lock.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "version": "3.13.12",
                    "platform": "win_amd64",
                    "source_url": (
                        "https://github.com/astral-sh/"
                        "python-build-standalone/releases/download/20260325/"
                        "cpython-3.13.12+20260325-x86_64-pc-windows-msvc"
                        "_install_only.tar.gz"
                    ),
                    "sha256": (
                        "5b4093f92d9bffcb0d92aea050f3d77d"
                        "5a4fc8e918b31cea000ee4b3ca751f1d"
                    ),
                },
                indent=2,
                sort_keys=True,
            ),
        )


def _ci_workflow(name: str) -> str:
    if name == "protocol":
        commands = (
            "python -m pip install --upgrade pip\n"
            "          python -m pip install pytest jsonschema httpx pyyaml "
            "./packages/vibeocr-contracts-py "
            "./packages/vibeocr-runtime-client-py\n"
            "          python scripts/generate_runtime_protocol.py --check\n"
            "          python scripts/check_openapi_quality.py "
            "--baseline packages/vibeocr-contracts-py/src/vibeocr/"
            "runtime_contracts/baselines/openapi-2.0.0.yaml "
            "--current packages/vibeocr-contracts-py/src/vibeocr/"
            "runtime_contracts/openapi.yaml\n"
            "          python -m pytest tests/contracts\n"
            "          python -m pytest "
            "tests/runtime/test_build_protocol_release_manifest.py "
            "tests/runtime/test_build_protocol_release_assets.py "
            "tests/runtime/test_build_spdx_sbom.py "
            "tests/runtime/test_build_release_checksums.py\n"
            "          dotnet test tests/dotnet/VibeOCR.Contracts.Tests/"
            "VibeOCR.Contracts.Tests.csproj -c Release\n"
            "          dotnet test tests/dotnet/VibeOCR.Runtime.Client.Tests/"
            "VibeOCR.Runtime.Client.Tests.csproj -c Release"
        )
    elif name == "backend":
        commands = (
            "gh release download v2.0.0 --repo FelixJI/vibeocr-protocol "
            "--dir .protocol\n"
            "          Get-ChildItem .protocol -File | "
            "Where-Object Name -ne 'SHA256SUMS' | ForEach-Object { "
            "gh attestation verify $_.FullName "
            "--repo FelixJI/vibeocr-protocol; "
            "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }\n"
            "          python scripts/bind_component_releases.py "
            "protocol-lock --release-dir .protocol "
            "--repository FelixJI/vibeocr-protocol --version 2.0.0 "
            "--output .protocol/protocol.lock.json\n"
            "          if ((Get-FileHash .protocol/protocol.lock.json "
            "-Algorithm SHA256).Hash -ne "
            "(Get-FileHash release/protocol.lock.json "
            "-Algorithm SHA256).Hash) { throw 'Protocol lock mismatch' }\n"
            "          python -m pip install --upgrade pip\n"
            "          python -m pip install pytest pyyaml ruff "
            "./.protocol/vibeocr_runtime_contracts-2.0.0-py3-none-any.whl "
            "./packages/vibeocr-backend\n"
            "          python scripts/check_runtime_protocol_conformance.py\n"
            "          python -m pytest tests/runtime tests/application "
            "tests/core tests/models\n"
            "          python -m ruff check packages/vibeocr-backend "
            "scripts tests/runtime"
        )
    elif name == "classic":
        commands = (
            "gh release download v2.0.0 --repo FelixJI/vibeocr-protocol "
            "--pattern \"*.whl\" --dir .feed\n"
            "          gh release download v0.7.0 --repo FelixJI/vibeocr-backend "
            "--pattern \"vibeocr_backend-*.whl\" --dir .feed\n"
            "          python -m pip install --upgrade pip\n"
            "          python -m pip install pytest pytest-qt "
            "--find-links .feed ./apps/vibeocr-pyside\n"
            "          python -m pytest tests/managers tests/pyside "
            "tests/views tests/widgets"
        )
    else:
        commands = (
            "dotnet test tests/dotnet/VibeOCR.Platform.Tests/"
            "VibeOCR.Platform.Tests.csproj -c Release\n"
            "          dotnet test tests/dotnet/VibeOCR.App.Tests/"
            "VibeOCR.App.Tests.csproj -c Release"
        )
    return (
        "name: CI\n\n"
        "on:\n"
        "  push:\n"
        "    branches: [main]\n"
        "  pull_request:\n\n"
        "permissions:\n"
        "  contents: read\n\n"
        "jobs:\n"
        "  required:\n"
        "    name: required\n"
        "    runs-on: windows-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - uses: actions/setup-python@v5\n"
        "        with:\n"
        "          python-version: '3.13'\n"
        "      - uses: actions/setup-dotnet@v5\n"
        "        with:\n"
        "          global-json-file: global.json\n"
        "        if: hashFiles('global.json') != ''\n"
        "      - name: Verify\n"
        "        env:\n"
        "          GH_TOKEN: ${{ github.token }}\n"
        "        run: |\n"
        f"          {commands}\n"
    )


def _release_workflow(name: str, version: str) -> str:
    tag = f"v{version}"
    return (
        "name: Release\n\n"
        "on:\n"
        "  push:\n"
        f"    tags: ['{tag}']\n\n"
        "permissions:\n"
        "  contents: read\n\n"
        "jobs:\n"
        "  release:\n"
        "    name: release\n"
        "    runs-on: windows-latest\n"
        "    environment: release\n"
        "    permissions:\n"
        "      contents: write\n"
        "      id-token: write\n"
        "      attestations: write\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - uses: actions/setup-python@v5\n"
        "        with:\n"
        "          python-version: '3.13'\n"
        "      - uses: actions/setup-dotnet@v5\n"
        "        with:\n"
        "          global-json-file: global.json\n"
        "        if: hashFiles('global.json') != ''\n"
        "      - name: Verify immutable tag\n"
        "        shell: pwsh\n"
        "        run: |\n"
        f"          if ('${{{{ github.ref_name }}}}' -ne '{tag}') {{ exit 1 }}\n"
        "      - name: Build release assets\n"
        "        shell: pwsh\n"
        "        run: ./scripts/build-release.ps1\n"
        "      - uses: actions/attest@v4\n"
        "        with:\n"
        "          subject-checksums: artifacts/SHA256SUMS\n"
        "      - uses: actions/attest@v4\n"
        "        with:\n"
        "          subject-checksums: artifacts/SHA256SUMS\n"
        "          sbom-path: artifacts/SBOM.spdx.json\n"
        "      - name: Publish immutable release\n"
        "        env:\n"
        "          GH_TOKEN: ${{ github.token }}\n"
        "        run: gh release create ${{ github.ref_name }} artifacts/* "
        "--verify-tag --generate-notes\n"
    )


def _build_release_script(name: str) -> str:
    if name == "protocol":
        return """[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$artifacts = Join-Path $root 'artifacts'
$build = Join-Path $root '.release-build'
if (Test-Path -LiteralPath $artifacts) {
    Remove-Item -LiteralPath $artifacts -Recurse -Force
}
if (Test-Path -LiteralPath $build) {
    Remove-Item -LiteralPath $build -Recurse -Force
}
New-Item -ItemType Directory -Path $artifacts, $build -Force | Out-Null
python -m pip install build==1.5.0 hatchling==1.27.0
python -m build --wheel --no-isolation (Join-Path $root 'packages/vibeocr-contracts-py') --outdir $build
if ($LASTEXITCODE -ne 0) { throw 'contracts wheel build failed' }
python -m build --wheel --no-isolation (Join-Path $root 'packages/vibeocr-runtime-client-py') --outdir $build
if ($LASTEXITCODE -ne 0) { throw 'client wheel build failed' }
dotnet restore (Join-Path $root 'src/dotnet/VibeOCR.Runtime.Client/VibeOCR.Runtime.Client.csproj')
if ($LASTEXITCODE -ne 0) { throw 'NuGet restore failed' }
dotnet pack (Join-Path $root 'src/dotnet/VibeOCR.Contracts/VibeOCR.Contracts.csproj') -c Release --no-restore -o $build
if ($LASTEXITCODE -ne 0) { throw 'contracts NuGet pack failed' }
dotnet pack (Join-Path $root 'src/dotnet/VibeOCR.Runtime.Client/VibeOCR.Runtime.Client.csproj') -c Release --no-restore -o $build
if ($LASTEXITCODE -ne 0) { throw 'client NuGet pack failed' }
python (Join-Path $root 'scripts/build_protocol_release_assets.py') `
  --contracts-root (Join-Path $root 'packages/vibeocr-contracts-py/src/vibeocr/runtime_contracts') `
  --version 2.0.0 --output-dir $artifacts
if ($LASTEXITCODE -ne 0) { throw 'Protocol archive build failed' }
Copy-Item -LiteralPath (Join-Path $build 'vibeocr_runtime_contracts-2.0.0-py3-none-any.whl') -Destination $artifacts
Copy-Item -LiteralPath (Join-Path $build 'vibeocr_runtime_client-2.0.0-py3-none-any.whl') -Destination $artifacts
Copy-Item -LiteralPath (Join-Path $build 'VibeOCR.Runtime.Contracts.2.0.0.nupkg') -Destination $artifacts
Copy-Item -LiteralPath (Join-Path $build 'VibeOCR.Runtime.Client.2.0.0.nupkg') -Destination $artifacts
python (Join-Path $root 'scripts/build_spdx_sbom.py') --artifacts-dir $artifacts `
  --repository-name FelixJI/vibeocr-protocol --version 2.0.0
if ($LASTEXITCODE -ne 0) { throw 'SBOM build failed' }
$inputs = Get-ChildItem -LiteralPath $artifacts -File | ForEach-Object {
    @('--artifact', $_.FullName)
}
$arguments = @(
    (Join-Path $root 'scripts/build_protocol_release_manifest.py'),
    '--protocol-version', '2.0.0',
    '--source-commit', (git -C $root rev-parse HEAD).Trim(),
    '--build-workflow', 'github.com/FelixJI/vibeocr-protocol/.github/workflows/release.yml',
    '--output-dir', $artifacts
) + $inputs
python @arguments
if ($LASTEXITCODE -ne 0) { throw 'Protocol manifest build failed' }
python (Join-Path $root 'scripts/build_release_checksums.py') $artifacts
if ($LASTEXITCODE -ne 0) { throw 'checksum build failed' }
"""
    if name == "backend":
        return """[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$artifacts = Join-Path $root 'artifacts'
$build = Join-Path $root '.release-build'
$inputs = Join-Path $root '.release-input'
foreach ($path in @($artifacts, $build, $inputs)) {
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Recurse -Force
    }
    New-Item -ItemType Directory -Path $path -Force | Out-Null
}
$protocol = Join-Path $inputs 'protocol'
New-Item -ItemType Directory -Path $protocol -Force | Out-Null
gh release download v2.0.0 --repo FelixJI/vibeocr-protocol --dir $protocol
if ($LASTEXITCODE -ne 0) { throw 'Protocol release download failed' }
Get-ChildItem -LiteralPath $protocol -File |
  Where-Object Name -ne 'SHA256SUMS' |
  ForEach-Object {
    gh attestation verify $_.FullName --repo FelixJI/vibeocr-protocol
    if ($LASTEXITCODE -ne 0) { throw "attestation failed: $($_.Name)" }
  }
$generatedLock = Join-Path $build 'protocol.lock.json'
python (Join-Path $root 'scripts/bind_component_releases.py') protocol-lock `
  --release-dir $protocol --repository FelixJI/vibeocr-protocol `
  --version 2.0.0 --output $generatedLock
if ($LASTEXITCODE -ne 0) { throw 'Protocol release verification failed' }
$committedLock = Join-Path $root 'release/protocol.lock.json'
if (-not (Test-Path -LiteralPath $committedLock -PathType Leaf)) {
    throw 'release/protocol.lock.json is required'
}
if ((Get-FileHash $generatedLock -Algorithm SHA256).Hash -ne
    (Get-FileHash $committedLock -Algorithm SHA256).Hash) {
    throw 'committed Protocol lock does not match downloaded release'
}
$runtimeLock = Get-Content (Join-Path $root 'release/python-runtime.lock.json') -Raw |
  ConvertFrom-Json
$pythonArchive = Join-Path $inputs ([IO.Path]::GetFileName($runtimeLock.source_url))
Invoke-WebRequest -Uri $runtimeLock.source_url -OutFile $pythonArchive
if ((Get-FileHash $pythonArchive -Algorithm SHA256).Hash.ToLowerInvariant() -ne
    $runtimeLock.sha256) {
    throw 'standalone Python archive hash mismatch'
}
python -m pip install build==1.5.0 hatchling==1.27.0 pyinstaller==6.21.0
python -m build --wheel --no-isolation (Join-Path $root 'packages/vibeocr-backend') --outdir $build
if ($LASTEXITCODE -ne 0) { throw 'Backend wheel build failed' }
python (Join-Path $root 'scripts/build_runtime_installer.py') `
  --output-dir $build --work-dir (Join-Path $build 'installer-work') `
  --backend-version 0.7.0
if ($LASTEXITCODE -ne 0) { throw 'Runtime installer build failed' }
$backendWheel = Get-ChildItem -LiteralPath $build -Filter 'vibeocr_backend-0.7.0-*.whl' |
  Select-Object -Single
$protocolWheel = Get-ChildItem -LiteralPath $protocol -Filter 'vibeocr_runtime_contracts-2.0.0-*.whl' |
  Select-Object -Single
$installerArchive = Get-ChildItem -LiteralPath $build -Filter 'vibeocr-runtime-installer-0.7.0.zip' |
  Select-Object -Single
python (Join-Path $root 'scripts/build_runtime_manifest.py') `
  --backend-wheel $backendWheel.FullName `
  --protocol-wheel $protocolWheel.FullName `
  --protocol-manifest (Join-Path $protocol 'release-manifest.json') `
  --cpu-lock (Join-Path $root 'packages/vibeocr-backend/runtime-profiles/win-x64-cpu/requirements-win-x64-cpu.lock') `
  --cu126-lock (Join-Path $root 'packages/vibeocr-backend/runtime-profiles/win-x64-cu126/requirements-win-x64-cu126.lock') `
  --python-archive $pythonArchive --python-version $runtimeLock.version `
  --python-source-url $runtimeLock.source_url `
  --installer-archive $installerArchive.FullName --backend-version 0.7.0 `
  --source-commit (git -C $root rev-parse HEAD).Trim() `
  --build-workflow 'github.com/FelixJI/vibeocr-backend/.github/workflows/release.yml' `
  --output-dir $artifacts
if ($LASTEXITCODE -ne 0) { throw 'Runtime manifest build failed' }
Remove-Item -LiteralPath (Join-Path $artifacts 'SHA256SUMS') -Force
python (Join-Path $root 'scripts/build_spdx_sbom.py') --artifacts-dir $artifacts `
  --repository-name FelixJI/vibeocr-backend --version 0.7.0
if ($LASTEXITCODE -ne 0) { throw 'SBOM build failed' }
python (Join-Path $root 'scripts/build_release_checksums.py') $artifacts
if ($LASTEXITCODE -ne 0) { throw 'checksum build failed' }
"""
    return """[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
throw 'Repository-specific release builder has not been generated'
"""


def _dev_link_script(name: str) -> str:
    return f"""[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$ReleaseFeed)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$feed = (Resolve-Path -LiteralPath $ReleaseFeed).Path
$payload = [ordered]@{{
  schema_version = 1
  component = '{name}'
  release_feed = $feed
  local_only = $true
}}
$payload | ConvertTo-Json -Depth 4 | Set-Content `
  -LiteralPath (Join-Path $root 'dev-overrides.json') -Encoding utf8
Write-Host 'Created ignored local development override.'
"""


def stage_repository(
    name: str,
    destination: Path,
    *,
    cutover_commit: str,
) -> Path:
    spec = SPECS[name]
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"staging destination is not empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    for relative in spec.paths:
        _copy(relative, destination)
    _write_common(
        destination,
        name=name,
        spec=spec,
        cutover_commit=cutover_commit,
    )
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--repository", choices=(*SPECS, "all"), default="all")
    parser.add_argument("--cutover-commit")
    args = parser.parse_args(argv)
    cutover = args.cutover_commit or _git_sha()
    names = tuple(SPECS) if args.repository == "all" else (args.repository,)
    for name in names:
        path = stage_repository(
            name,
            args.output_root / SPECS[name].slug,
            cutover_commit=cutover,
        )
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
