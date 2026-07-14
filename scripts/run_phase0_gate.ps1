<#
.SYNOPSIS
    VibeOCR Phase 0 quality gate.

.DESCRIPTION
    Runs lockfile sync, full pytest, Ruff, and Pyright in sequence,
    then writes a baseline JSON report to reports/local/.

    The report must NOT contain machine-local absolute paths.

    -ValidateOnly mode performs only self-checks (script structure,
    baseline schema validity, report directory writability) and does
    NOT run the time-consuming uv sync / pytest / ruff / pyright.

.PARAMETER ValidateOnly
    Self-check only: validate script structure, schema loadability,
    and report directory writability.

.PARAMETER ReportPath
    Baseline JSON output path. Default: reports/local/phase0-baseline.json.

.EXAMPLE
    ./scripts/run_phase0_gate.ps1 -ValidateOnly
    ./scripts/run_phase0_gate.ps1
#>

param(
    [switch]$ValidateOnly,
    [switch]$BuildRelease,
    [string]$ReportPath = "",
    [string]$NodePath = ""
)

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Resolve project root (script lives in <root>/scripts/)
# ---------------------------------------------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..")
$Uv = Join-Path $ProjectRoot ".venv\Scripts\uv.exe"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$DotNet = Join-Path $env:ProgramFiles "dotnet\dotnet.exe"
$Node = $NodePath
if (-not $Node) { $Node = $env:VIBEOCR_NODE }
if (-not $Node) { $Node = (Get-Command node.exe -ErrorAction SilentlyContinue).Source }
if (-not (Test-Path $Uv)) { throw "project uv not found: $Uv" }
if (-not (Test-Path $Python)) { throw "project Python not found: $Python" }
if (-not (Test-Path $DotNet)) { throw "x64 dotnet not found: $DotNet" }
if (-not $Node) { $Node = (Get-Command node -ErrorAction SilentlyContinue).Source }

# ---------------------------------------------------------------------------
# Baseline report output path (gitignored via reports/)
# ---------------------------------------------------------------------------
if ([string]::IsNullOrEmpty($ReportPath)) {
    $ReportPath = Join-Path $ProjectRoot "reports/local/phase0-baseline.json"
}
$ReportDir = Split-Path -Parent $ReportPath
New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null

# baseline schema path
$SchemaPath = Join-Path $ProjectRoot "tests/fixtures/startup/baseline.schema.json"

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

function Invoke-GateStep {
    <#
        Run a gate step, capturing exit code and elapsed time.
        CLI tools (uv, pytest, ruff, pyright) write progress to stderr;
        we capture 2>&1 but only treat non-zero $LASTEXITCODE as failure.
    #>
    param(
        [string]$Name,
        [scriptblock]$Action
    )
    $stepStart = Get-Date
    Write-Host ""
    Write-Host "==> [gate] $Name" -ForegroundColor Cyan
    # Temporarily relax ErrorActionPreference: stderr output from CLI tools
    # (e.g. uv "Audited N packages") must not trigger Stop.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $Action 2>&1
    } finally {
        $ErrorActionPreference = $prevEAP
    }
    $code = $LASTEXITCODE
    $elapsed = ((Get-Date) - $stepStart).TotalSeconds
    if ($output) {
        $output | Select-Object -Last 25 | ForEach-Object { Write-Host "    $_" }
    }
    return [ordered]@{
        name    = $Name
        exit    = [int]$code
        seconds = [math]::Round($elapsed, 3)
        ok      = ($code -eq 0)
    }
}

function Test-SchemaFile {
    <#
        Validate the schema file is valid JSON and has the minimal
        structure for a phase0 baseline report.
    #>
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        throw "baseline schema not found: $Path"
    }
    $raw = Get-Content -Raw -Encoding UTF8 $Path
    $schema = $null
    try {
        $schema = $raw | ConvertFrom-Json
    } catch {
        throw "baseline schema is not valid JSON: $_"
    }
    if ($schema.type -ne "object") {
        throw "baseline schema.type must be 'object', got '$($schema.type)'"
    }
    if (-not $schema.properties) {
        throw "baseline schema missing properties"
    }
    if (-not $schema.required) {
        throw "baseline schema missing required list"
    }
    return $true
}

function Test-BaselineReport {
    <#
        Validate the generated report meets the minimal schema
        constraints and is scrubbed of machine-local absolute paths.
    #>
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        throw "baseline report not found: $Path"
    }
    $raw = Get-Content -Raw -Encoding UTF8 $Path
    $report = $raw | ConvertFrom-Json
    if (-not $report.generated_at) {
        throw "baseline report missing generated_at"
    }
    if (-not $report.gate) {
        throw "baseline report missing gate"
    }
    if (-not $report.steps) {
        throw "baseline report missing steps"
    }
    # Scrub check: report must not contain the user home absolute path
    $homePath = [Environment]::GetFolderPath("UserProfile")
    if ($homePath -and $raw.Contains($homePath)) {
        throw "baseline report contains machine-local absolute path (user home); must be scrubbed"
    }
    return $true
}

# ---------------------------------------------------------------------------
# -ValidateOnly: self-check only, no time-consuming gates
# ---------------------------------------------------------------------------
if ($ValidateOnly) {
    Write-Host "[phase0-gate] ValidateOnly mode: self-check script and schema" -ForegroundColor Yellow

    Test-SchemaFile -Path $SchemaPath | Out-Null
    Write-Host "[phase0-gate] baseline schema OK: $SchemaPath" -ForegroundColor Green

    # verify report directory is writable
    $probe = Join-Path $ReportDir ".gate-probe"
    "probe" | Out-File -FilePath $probe -Encoding UTF8
    Remove-Item -Force $probe

    Write-Host "[phase0-gate] report dir writable: $ReportDir" -ForegroundColor Green
    Write-Host "[phase0-gate] ValidateOnly OK" -ForegroundColor Green
    exit 0
}

# ---------------------------------------------------------------------------
# Full gate mode
# ---------------------------------------------------------------------------

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " VibeOCR Phase 0 Quality Gate" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " project: $ProjectRoot"
Write-Host " report:  $ReportPath"

# Validate schema first
Test-SchemaFile -Path $SchemaPath | Out-Null
Write-Host "[phase0-gate] baseline schema OK" -ForegroundColor Green

$steps = @()

# Step 1: lockfile sync (deterministic install)
$steps += Invoke-GateStep -Name "uv sync --frozen --group dev" -Action {
    Push-Location $ProjectRoot
    & $Uv sync --frozen --group dev --python $Python --no-managed-python `
        --cache-dir (Join-Path $ProjectRoot ".vibeocr\uv-cache")
    Pop-Location
}

# Step 2: full pytest
$steps += Invoke-GateStep -Name "pytest -q" -Action {
    Push-Location $ProjectRoot
    & $Python -m pytest -q
    Pop-Location
}

# Step 3: Ruff
$steps += Invoke-GateStep -Name "ruff check src tests scripts" -Action {
    Push-Location $ProjectRoot
    & $Python -m ruff check src tests scripts
    Pop-Location
}

# Step 4: Pyright
$steps += Invoke-GateStep -Name "pyright" -Action {
    Push-Location $ProjectRoot
    & $Python -m pyright
    Pop-Location
}

# Step 5: complete .NET solution (x64 SDK; PATH can resolve the x86 host).
$steps += Invoke-GateStep -Name "dotnet restore" -Action {
    Push-Location $ProjectRoot
    & $DotNet restore "src\dotnet\VibeOCR.slnx"
    Pop-Location
}
$steps += Invoke-GateStep -Name "dotnet build -c Release" -Action {
    Push-Location $ProjectRoot
    & $DotNet build "src\dotnet\VibeOCR.slnx" -c Release --no-restore
    Pop-Location
}
$steps += Invoke-GateStep -Name "dotnet test -c Release" -Action {
    Push-Location $ProjectRoot
    & $DotNet test "src\dotnet\VibeOCR.slnx" -c Release --no-restore
    Pop-Location
}

# Step 6: Web semantic/security tests.
$steps += Invoke-GateStep -Name "node --test tests/web/*.test.ts" -Action {
    Push-Location $ProjectRoot
    if (-not $Node) { throw "node not found" }
    & $Node --test tests\web\*.test.ts
    Pop-Location
}

# Step 7: feature parity must be complete, not merely schema-valid.
$steps += Invoke-GateStep -Name "feature parity --require-pass" -Action {
    Push-Location $ProjectRoot
    & $Python tests\parity\validate_matrix.py docs\quality\feature-parity.md --require-pass
    Pop-Location
}

if ($BuildRelease) {
    $versionMatch = Select-String -Path (Join-Path $ProjectRoot "pyproject.toml") -Pattern '^version = "([0-9]+\.[0-9]+\.[0-9]+)"$'
    if (-not $versionMatch) { throw "could not read project version" }
    $version = $versionMatch.Matches[0].Groups[1].Value
    $archive = Join-Path $ProjectRoot "dist\VibeOCR-v$version-win64.zip"
    $steps += Invoke-GateStep -Name "build WinUI release" -Action {
        Push-Location $ProjectRoot
        & $Python scripts\bump_version.py --rebuild $version --force
        Pop-Location
    }
    $steps += Invoke-GateStep -Name "verify WinUI artifact" -Action {
        Push-Location $ProjectRoot
        powershell -NoProfile -ExecutionPolicy Bypass -File `
            (Join-Path $ProjectRoot "scripts\verify_winui_artifact.ps1") `
            -Artifact $archive
        Pop-Location
    }
}

# ---------------------------------------------------------------------------
# Generate baseline report (scrubbed of absolute paths)
# ---------------------------------------------------------------------------
$failed = $false
foreach ($s in $steps) {
    if (-not $s.ok) { $failed = $true }
}

$report = [ordered]@{
    generated_at = (Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ")
    gate         = "full-migration"
    schema       = "tests/fixtures/startup/baseline.schema.json"
    result       = if ($failed) { "FAIL" } else { "PASS" }
    steps        = $steps
}

$reportJson = $report | ConvertTo-Json -Depth 5
$reportJson | Out-File -FilePath $ReportPath -Encoding UTF8

# Self-validate the report is scrubbed
Test-BaselineReport -Path $ReportPath | Out-Null

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
foreach ($s in $steps) {
    $color = if ($s.ok) { "Green" } else { "Red" }
    $mark = if ($s.ok) { "OK" } else { "FAIL" }
    $line = " [{0}] {1} ({2}s)" -f $mark, $s.name, $s.seconds
    Write-Host $line -ForegroundColor $color
}
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " report: $ReportPath"
$resultColor = if ($failed) { "Red" } else { "Green" }
Write-Host " result: $($report.result)" -ForegroundColor $resultColor

if ($failed) {
    Write-Error "Phase 0 gate FAILED"
    exit 1
}

Write-Host "[phase0-gate] all steps passed" -ForegroundColor Green
exit 0
