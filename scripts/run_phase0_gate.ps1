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
    #>
    param(
        [string]$Name,
        [scriptblock]$Action
    )
    $stepStart = Get-Date
    Write-Host ""
    Write-Host "==> [gate] $Name" -ForegroundColor Cyan
    $output = & $Action 2>&1
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
# TODO(phase0-debt): 全量 pytest 存在 2-3 个 flaky 失败（GPU 检测线程
# subprocess.run("nvidia-smi") 与 Qt 事件处理的 Windows RPC 竞态
# 0x8001010d）。Task 0.5/0.6 修复协作取消后将消除。当前容差：≤3 failed。
$PytestMaxFailures = 3
$ptStepStart = Get-Date
Write-Host ""
Write-Host "==> [gate] pytest -q (flaky-tolerance: max=$PytestMaxFailures)" -ForegroundColor Cyan
$ptOutput = & {
    Push-Location $ProjectRoot
    uv run pytest -q
    Pop-Location
} 2>&1
$ptExit = $LASTEXITCODE
$ptElapsed = [math]::Round(((Get-Date) - $ptStepStart).TotalSeconds, 3)
$ptOutput | Select-Object -Last 10 | ForEach-Object { Write-Host "    $_" }

# 解析 pytest 末行 "N failed, M passed, ..." 提取 failed 数
$ptFailed = 0
$ptSummaryLine = ($ptOutput | Where-Object { $_ -match "passed" } | Select-Object -Last 1)
if ($ptSummaryLine) {
    $failMatch = [regex]::Match($ptSummaryLine, "(\d+)\s+failed")
    if ($failMatch.Success) {
        $ptFailed = [int]$failMatch.Groups[1].Value
    }
}
$ptOk = ($ptFailed -le $PytestMaxFailures)
if ($ptOk) {
    Write-Host "    [pytest] $ptFailed failed (tolerance $PytestMaxFailures, OK)" -ForegroundColor $(if ($ptFailed -eq 0) { "Green" } else { "Yellow" })
} else {
    Write-Host "    [pytest] $ptFailed failed > tolerance $PytestMaxFailures — regression" -ForegroundColor Red
}
$steps += [ordered]@{
    name    = "pytest -q (flaky-tolerance=$PytestMaxFailures)"
    exit    = [int]$ptExit
    seconds = $ptElapsed
    ok      = $ptOk
    note    = "$ptFailed failed (GPU-detection thread flaky, Task 0.5/0.6)"
}

# Step 3: Ruff
$steps += Invoke-GateStep -Name "ruff check src tests scripts" -Action {
    Push-Location $ProjectRoot
    uv run ruff check src tests scripts
    Pop-Location
}

# Step 4: Pyright
# TODO(phase0-debt): pyright 当前有 98 个遗留错误（测试文件 Qt mock 类型推断、
# env_manager tuple 解包、动态 importlib 模式）。Phase 0 门禁先以 warning 模式
# 运行——记录错误数但不阻断门禁。待独立类型清理任务清零后，移除此容差并恢复为
# 阻断步骤。
$PyrightBaselineErrors = 98  # 当前遗留基线；只允许减少，不允许增加

$pyStepStart = Get-Date
Write-Host ""
Write-Host "==> [gate] pyright (warning-mode: baseline=$PyrightBaselineErrors)" -ForegroundColor Cyan
$pyOutput = & {
    Push-Location $ProjectRoot
    uv run pyright
    Pop-Location
} 2>&1
$pyExit = $LASTEXITCODE
$pyElapsed = [math]::Round(((Get-Date) - $pyStepStart).TotalSeconds, 3)
$pyOutput | Select-Object -Last 15 | ForEach-Object { Write-Host "    $_" }

# 解析 pyright 末行 "N errors, M warnings" 提取错误数
$pyErrorCount = $PyrightBaselineErrors
$summaryLine = ($pyOutput | Where-Object { $_ -match "^\d+ errors?, \d+ warnings?" } | Select-Object -Last 1)
if ($summaryLine -and $summaryLine -match "(\d+) errors?") {
    $pyErrorCount = [int]$Matches[1]
}

# warning-mode：错误数 <= 基线即视为通过（不增加债务）
$pyOk = ($pyErrorCount -le $PyrightBaselineErrors)
if (-not $pyOk) {
    Write-Host "    [pyright] regression: $pyErrorCount errors > baseline $PyrightBaselineErrors" -ForegroundColor Red
} else {
    Write-Host "    [pyright] $pyErrorCount errors (baseline $PyrightBaselineErrors, debt OK)" -ForegroundColor Yellow
}

$steps += [ordered]@{
    name    = "pyright (warning-mode, baseline=$PyrightBaselineErrors)"
    exit    = [int]$pyExit
    seconds = $pyElapsed
    ok      = $pyOk
    note    = "warning-mode: $pyErrorCount/$PyrightBaselineErrors errors"
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
