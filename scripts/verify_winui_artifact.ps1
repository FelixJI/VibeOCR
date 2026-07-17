<#
.SYNOPSIS
Verifies a WinUI release artifact enforces the framework-dependent layout rules.

.DESCRIPTION
Rejects: .NET self-contained runtime, duplicate WebView2 SDK, PySide6 UI
modules, dev profile, test/cache/output content. Accepts a directory or a
ZIP archive path.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Artifact
)
$ErrorActionPreference = 'Stop'

if ($Artifact -and (Test-Path $Artifact -PathType Leaf) -and $Artifact.EndsWith('.zip')) {
    # 用 [guid]::NewGuid() 而非 New-Guid cmdlet：本脚本经 powershell.exe（Windows PS
    # 5.1）被 bump_version.py 调起，cmdlet 自动加载在 Win Server 2025 runner 上偶发
    # 失败（release v0.4.33 "New-Guid not recognized"）。直接调 .NET Guid 不依赖
    # 模块发现，PS 5.1 与 7+ 行为一致。同 build_winui_release.ps1 的 Get-FileHash 修复。
    $extract = Join-Path $env:TEMP "VibeOCR-verify-$([guid]::NewGuid().ToString())"
    Expand-Archive -Path $Artifact -DestinationPath $extract -Force
    $rootEntries = @(Get-ChildItem -LiteralPath $extract -Force)
    if ($rootEntries.Count -eq 1 -and $rootEntries[0].PSIsContainer) {
        $root = $rootEntries[0].FullName
    } else {
        $root = $extract
    }
} else {
    $root = (Resolve-Path $Artifact).Path
}

$errors = [System.Collections.Generic.List[string]]::new()

# Required release surface.  A deny-only verifier allowed empty/zero-byte
# fixtures to pass, so validate the stable entry points and worker contract.
$requiredFiles = @(
    'VibeOCR.WinUI.exe',
    'VibeOCR.Bootstrapper.exe',
    'updater.exe',
    'VibeOCR.WinUI.dll',
    'VibeOCR.Contracts.dll',
    'VibeOCR.Platform.dll',
    'worker\vibeocr\worker_host\main.py',
    'product-manifest.json',
    'contracts\v1\golden.json',
    'CHANGELOG.md',
    'LICENSE'
)
foreach ($relative in $requiredFiles) {
    $candidate = Join-Path $root $relative
    if (-not (Test-Path $candidate -PathType Leaf)) {
        $errors.Add("required release file missing: $relative")
    } elseif ((Get-Item $candidate).Length -eq 0) {
        $errors.Add("required release file is empty: $relative")
    }
}

$legacyEntries = @(
    'worker\vibeocr\main.py',
    'worker\vibeocr\views',
    'worker\vibeocr\widgets',
    'worker\vibeocr\ui',
    'worker\vibeocr\pyside'
)
foreach ($relative in $legacyEntries) {
    if (Test-Path (Join-Path $root $relative)) {
        $errors.Add("legacy PySide UI entry present: $relative")
    }
}

# Rule: no self-contained .NET runtime bundles.
$selfContained = Get-ChildItem -Path $root -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '^(libcoreclr|libhostpolicy|System\.Private\.CoreLib)\.dll$' } |
    Select-Object -First 3
if ($selfContained) { $errors.Add('self-contained .NET runtime files present (expected framework-dependent)') }

# Rule: no duplicate WebView2 fixed SDK.
$webview2 = Get-ChildItem -Path $root -Recurse -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match 'WebView2' -or $_.Name -match 'Microsoft.Web.WebView2' }
if (@($webview2).Count -gt 1) { $errors.Add("duplicate WebView2 SDK present ($(@($webview2).Count) copies)") }

# Rule: no PySide6 UI modules in the worker.
$pyside = Get-ChildItem -Path $root -Recurse -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -eq 'PySide6' }
if ($pyside) { $errors.Add('PySide6 UI modules present; worker must exclude the legacy UI') }

# Rule: no dev profile.
$devProfile = Get-ChildItem -Path $root -Recurse -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -eq 'winui-dev' }
if ($devProfile) { $errors.Add('dev profile (winui-dev) present in release artifact') }

# Rule: no output/test content.
$forbidden = Get-ChildItem -Path $root -Recurse -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -in @('output', '__pycache__', '.pytest_cache', 'bin', 'obj') } |
    Select-Object -First 5
if ($forbidden) { $errors.Add("build/test/cache directories present: $($forbidden.Name -join ', ')") }

if ($errors.Count -gt 0) {
    if ($extract -and (Test-Path $extract)) {
        Remove-Item -LiteralPath $extract -Recurse -Force
    }
    Write-Error ($errors -join "`n")
    exit 1
}

if ($extract -and (Test-Path $extract)) {
    Remove-Item -LiteralPath $extract -Recurse -Force
}

Write-Host "Artifact $Artifact verified OK (framework-dependent, no PySide6, no dev profile)"
exit 0
