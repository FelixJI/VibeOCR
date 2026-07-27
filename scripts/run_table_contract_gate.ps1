<#
.SYNOPSIS
    Run the offline table semantic contract gate.

.DESCRIPTION
    This gate intentionally uses only checked-in synthetic fixtures.  It must
    not download models, start MinerU, or require a GPU.
#>

param(
    [string]$PythonPath = "",
    [string]$ReportDir = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $PythonPath) {
    $venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    $PythonPath = if (Test-Path -LiteralPath $venvPython) {
        $venvPython
    } else {
        (Get-Command python -ErrorAction Stop).Source
    }
}
if (-not $ReportDir) {
    $ReportDir = Join-Path $ProjectRoot "reports\table-contract"
}
$ReportDir = [IO.Path]::GetFullPath($ReportDir)
New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null

Push-Location $ProjectRoot
try {
    $previousPythonPath = $env:PYTHONPATH
    $sourceRoots = @(
        (Join-Path $ProjectRoot "packages\vibeocr-contracts-py\src"),
        (Join-Path $ProjectRoot "packages\vibeocr-client-py\src"),
        (Join-Path $ProjectRoot "packages\vibeocr-backend\src"),
        (Join-Path $ProjectRoot "apps\vibeocr-pyside\src")
    ) -join [IO.Path]::PathSeparator
    $env:PYTHONPATH = if ($previousPythonPath) {
        "$sourceRoots$([IO.Path]::PathSeparator)$previousPythonPath"
    } else {
        $sourceRoots
    }
    $env:TABLE_CONTRACT_REPORT_DIR = Join-Path $ReportDir "artifacts"
    & $PythonPath scripts/verify_table_artifact.py --fixture-root tests/fixtures/table_contract/v1 --report-dir (Join-Path $ReportDir "artifact-verifier")
    if ($LASTEXITCODE -ne 0) { throw "table fixture verification failed" }

    & $PythonPath -m pytest tests/table_contract -q --junitxml (Join-Path $ReportDir "junit.xml")
    if ($LASTEXITCODE -ne 0) { throw "table contract pytest failed" }
} finally {
    Remove-Item Env:TABLE_CONTRACT_REPORT_DIR -ErrorAction SilentlyContinue
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}

Write-Host "Table contract gate: PASS" -ForegroundColor Green
