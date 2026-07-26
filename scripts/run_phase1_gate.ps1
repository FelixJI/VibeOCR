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
$NuGetConfig = Join-Path $ProjectRoot "NuGet.Config"
$PytestBaseTemp = Join-Path $ProjectRoot ".tmp/phase1-gate-$PID"
$PyrightStage = Join-Path $ProjectRoot ".tmp/phase1-pyright-$PID"

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
    & $Python -m pytest tests/contracts/v2 tests/supervisor -q --basetemp $PytestBaseTemp
    if ($LASTEXITCODE -ne 0) { throw "Phase 1 pytest failed" }

    & $Ruff check packages/vibeocr-client-py/src/vibeocr/supervisor packages/vibeocr-backend/src/vibeocr/supervisor tests/supervisor tests/contracts/v2
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
        & $Pyright --pythonpath $Python (Join-Path $PyrightStage "vibeocr/supervisor") (Join-Path $PyrightStage "vibeocr/protocol/v2") tests/supervisor tests/contracts/v2
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

    & $Python -c "from vibeocr.supervisor.main import main; assert callable(main)"
    if ($LASTEXITCODE -ne 0) { throw "Supervisor import smoke failed" }

    & $Dotnet restore $ContractProject --configfile $NuGetConfig --locked-mode
    if ($LASTEXITCODE -ne 0) { throw "C# contract restore failed" }

    & $Dotnet test $ContractProject -c Release --no-restore
    if ($LASTEXITCODE -ne 0) { throw "C# golden contract failed" }
} finally {
    Pop-Location
}

Write-Host "Supervisor/protocol-v2 gate: PASS" -ForegroundColor Green
