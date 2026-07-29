<#
.SYNOPSIS
    Run the complete Supervisor/protocol-v2 contract and lifecycle gate.
#>

param([switch]$ValidateOnly)

$ErrorActionPreference = "Stop"
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Python = Join-Path $ProjectRoot ".venv/Scripts/python.exe"
$Ruff = Join-Path $ProjectRoot ".venv/Scripts/ruff.exe"
$Pyright = Join-Path $ProjectRoot ".venv/Scripts/pyright.exe"
$Dotnet = Join-Path $env:ProgramFiles "dotnet/dotnet.exe"
$ContractProject = Join-Path $ProjectRoot "tests/dotnet/VibeOCR.Contracts.Tests/VibeOCR.Contracts.Tests.csproj"
$RuntimeClientProject = Join-Path $ProjectRoot "tests/dotnet/VibeOCR.Runtime.Client.Tests/VibeOCR.Runtime.Client.Tests.csproj"
$PlatformProject = Join-Path $ProjectRoot "tests/dotnet/VibeOCR.Platform.Tests/VibeOCR.Platform.Tests.csproj"
$NuGetConfig = Join-Path $ProjectRoot "NuGet.Config"
$OpenApiCurrent = Join-Path $ProjectRoot "packages/vibeocr-contracts-py/src/vibeocr/protocol/v2/openapi.yaml"
$OpenApiBaseline = Join-Path $ProjectRoot "packages/vibeocr-contracts-py/src/vibeocr/protocol/v2/baselines/openapi-2.0.0-rc.1.yaml"
$PytestBaseTemp = Join-Path $ProjectRoot ".tmp/phase1-gate-$PID"
$PyrightStage = Join-Path $ProjectRoot ".tmp/phase1-pyright-$PID"

$RequiredFiles = @(
    $Python,
    $Ruff,
    $Pyright,
    $Dotnet,
    $ContractProject,
    $RuntimeClientProject,
    $PlatformProject,
    $NuGetConfig,
    $OpenApiCurrent,
    $OpenApiBaseline
)
foreach ($Path in $RequiredFiles) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Phase 1 gate dependency is missing: $Path"
    }
}

if ($ValidateOnly) {
    Write-Host "Phase 1 gate inputs: OK"
    exit 0
}

Push-Location $ProjectRoot
try {
    & $Python scripts/check_openapi_quality.py --baseline $OpenApiBaseline --current $OpenApiCurrent
    if ($LASTEXITCODE -ne 0) { throw "Formal OpenAPI quality gate failed" }

    & $Python scripts/generate_runtime_protocol.py --check
    if ($LASTEXITCODE -ne 0) { throw "Protocol generated diff gate failed" }

    & $Python scripts/check_runtime_protocol_conformance.py
    if ($LASTEXITCODE -ne 0) { throw "Backend conformance gate failed" }

    & $Python -m pytest tests/contracts/v2 tests/supervisor -q --basetemp $PytestBaseTemp
    if ($LASTEXITCODE -ne 0) { throw "Phase 1 pytest failed" }

    & $Ruff check packages/vibeocr-runtime-client-py/src/vibeocr/protocol/v2 packages/vibeocr-client-py/src/vibeocr/supervisor packages/vibeocr-backend/src/vibeocr/supervisor packages/vibeocr-contracts-py/src/vibeocr/protocol/v2 scripts/check_openapi_quality.py scripts/check_runtime_protocol_conformance.py scripts/generate_runtime_protocol.py tests/supervisor tests/contracts/v2
    if ($LASTEXITCODE -ne 0) { throw "Phase 1 Ruff failed" }

    # The release wheels form one pkgutil namespace. Pyright does not execute
    # extend_path and otherwise resolves only one physical ``vibeocr`` root.
    # Stage the installed namespace shape without ignoring any diagnostics.
    $PreviousPythonPath = $env:PYTHONPATH
    $PyrightExit = 1
    try {
        & $Python scripts/stage_pyright_namespace.py --output $PyrightStage
        if ($LASTEXITCODE -ne 0) { throw "Pyright namespace staging failed" }
        $env:PYTHONPATH = $PyrightStage
        & $Pyright --pythonpath $Python (Join-Path $PyrightStage "vibeocr/supervisor") (Join-Path $PyrightStage "vibeocr/protocol/v2")
        $PyrightExit = $LASTEXITCODE
    } finally {
        $env:PYTHONPATH = $PreviousPythonPath
        $tempRoot = [IO.Path]::GetFullPath((Join-Path $ProjectRoot ".tmp"))
        $stageFull = [IO.Path]::GetFullPath($PyrightStage)
        $expectedPrefix = $tempRoot.TrimEnd('\') + '\'
        if (-not $stageFull.StartsWith($expectedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "unsafe Pyright staging cleanup target: $stageFull"
        }
        if (Test-Path -LiteralPath $stageFull) {
            Remove-Item -LiteralPath $stageFull -Recurse -Force
        }
    }
    if ($PyrightExit -ne 0) { throw "Phase 1 Pyright failed" }

    $PreviousPythonPath = $env:PYTHONPATH
    try {
        $SmokeRoots = @(
            (Join-Path $ProjectRoot "packages\vibeocr-contracts-py\src"),
            (Join-Path $ProjectRoot "packages\vibeocr-runtime-client-py\src"),
            (Join-Path $ProjectRoot "packages\vibeocr-client-py\src"),
            (Join-Path $ProjectRoot "packages\vibeocr-backend\src")
        )
        if ($PreviousPythonPath) {
            $SmokeRoots += $PreviousPythonPath
        }
        $env:PYTHONPATH = $SmokeRoots -join [IO.Path]::PathSeparator
        & $Python -c "from vibeocr.supervisor.main import main; assert callable(main)"
        if ($LASTEXITCODE -ne 0) { throw "Supervisor import smoke failed" }
    } finally {
        $env:PYTHONPATH = $PreviousPythonPath
    }

    foreach ($Project in @($ContractProject, $RuntimeClientProject, $PlatformProject)) {
        & $Dotnet restore $Project --configfile $NuGetConfig --locked-mode
        if ($LASTEXITCODE -ne 0) { throw "C# project restore failed: $Project" }

        & $Dotnet test $Project -c Release --no-restore
        if ($LASTEXITCODE -ne 0) { throw "C# Protocol/Platform test failed: $Project" }
    }
} finally {
    Pop-Location
}

Write-Host "Supervisor/protocol-v2 gate: PASS" -ForegroundColor Green
