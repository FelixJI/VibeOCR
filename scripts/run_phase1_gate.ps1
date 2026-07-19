<#
.SYNOPSIS
    Run the complete WorkerHost Phase 1 contract and lifecycle gate.
#>

param([switch]$ValidateOnly)

$ErrorActionPreference = "Stop"
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Python = Join-Path $ProjectRoot ".venv/Scripts/python.exe"
$Ruff = Join-Path $ProjectRoot ".venv/Scripts/ruff.exe"
$Pyright = Join-Path $ProjectRoot ".venv/Scripts/pyright.exe"
$Dotnet = Join-Path $env:ProgramFiles "dotnet/dotnet.exe"
$ContractProject = Join-Path $ProjectRoot "tests/dotnet/VibeOCR.Contracts.Tests/VibeOCR.Contracts.Tests.csproj"
$NuGetConfig = Join-Path $ProjectRoot "NuGet.Config"

$RequiredFiles = @(
    $Python,
    $Ruff,
    $Pyright,
    $Dotnet,
    $ContractProject,
    $NuGetConfig
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
    & $Python -m pytest tests/contracts tests/worker_host -q
    if ($LASTEXITCODE -ne 0) { throw "Phase 1 pytest failed" }

    & $Ruff check packages/vibeocr-client-py/src/vibeocr/worker_host packages/vibeocr-backend/src/vibeocr/worker_host tests/worker_host tests/contracts
    if ($LASTEXITCODE -ne 0) { throw "Phase 1 Ruff failed" }

    & $Pyright --pythonpath $Python packages/vibeocr-client-py/src/vibeocr/worker_host packages/vibeocr-backend/src/vibeocr/worker_host tests/worker_host tests/contracts
    if ($LASTEXITCODE -ne 0) { throw "Phase 1 Pyright failed" }

    & $Python -m vibeocr.worker_host.main --self-test
    if ($LASTEXITCODE -ne 0) { throw "WorkerHost self-test failed" }

    & $Dotnet restore $ContractProject --configfile $NuGetConfig --locked-mode
    if ($LASTEXITCODE -ne 0) { throw "C# contract restore failed" }

    & $Dotnet test $ContractProject -c Release --no-restore
    if ($LASTEXITCODE -ne 0) { throw "C# golden contract failed" }
} finally {
    Pop-Location
}

Write-Host "Phase 1 gate: PASS" -ForegroundColor Green
