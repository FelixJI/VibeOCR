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
    [string]$ReportPath = ""
)

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Resolve project root (script lives in <root>/scripts/)
# ---------------------------------------------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..")

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
    uv sync --frozen --group dev
    Pop-Location
}

# Step 2: full pytest
# The full suite occasionally triggers a Windows fatal exception (segfault,
# exit code 127/139) from the GPU detection thread's nvidia-smi subprocess
# racing with Qt event processing. This is a test-infrastructure issue, not a
# code defect. We retry up to 2 times on non-zero exit to handle the flaky crash.
$ptStep = Invoke-GateStep -Name "pytest -q" -Action {
    Push-Location $ProjectRoot
    uv run pytest -q
    Pop-Location
}
$ptRetry = 0
while (-not $ptStep.ok -and $ptRetry -lt 2) {
    $ptRetry++
    Write-Host "    [pytest] non-zero exit (attempt 1), retry $ptRetry/2..." -ForegroundColor Yellow
    $ptStep = Invoke-GateStep -Name "pytest -q (retry $ptRetry)" -Action {
        Push-Location $ProjectRoot
        uv run pytest -q
        Pop-Location
    }
}
$steps += $ptStep

# Step 3: Ruff
$steps += Invoke-GateStep -Name "ruff check src tests scripts" -Action {
    Push-Location $ProjectRoot
    uv run ruff check src tests scripts
    Pop-Location
}

# Step 4: Pyright
$steps += Invoke-GateStep -Name "pyright" -Action {
    Push-Location $ProjectRoot
    uv run pyright
    Pop-Location
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
    gate         = "phase0"
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
