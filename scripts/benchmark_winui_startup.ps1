<#
.SYNOPSIS
Collects WinUI cold-start samples and emits a metrics JSON for compare_release_metrics.py.

.DESCRIPTION
Delegates to collect_startup_metrics.py, which launches the published app in
T6 smoke mode and parses the real JSONL T0/T3/T6 milestones. Missing,
non-monotonic, timed-out, or failed samples are rejected.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$AppPath,
    [int]$Runs = 30,
    [string]$ZipPath = "",
    [string]$Output = "$env:TEMP\VibeOCR-winui-startup.json"
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $repo '.venv\Scripts\python.exe'
$collector = Join-Path $repo 'scripts\collect_startup_metrics.py'
if (-not (Test-Path $python)) { throw "project Python not found: $python" }
if (-not (Test-Path $AppPath -PathType Leaf)) { throw "WinUI app not found: $AppPath" }

$zipBytes = 0
if ($ZipPath) {
    if (-not (Test-Path $ZipPath -PathType Leaf)) { throw "ZIP not found: $ZipPath" }
    $zipBytes = (Get-Item $ZipPath).Length
}

& $python $collector `
    --target $AppPath `
    --runs $Runs `
    --name winui `
    --zip-bytes $zipBytes `
    --output $Output
exit $LASTEXITCODE
