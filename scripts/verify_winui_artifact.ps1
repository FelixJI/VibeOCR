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

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)

    $stream = [System.IO.File]::OpenRead($LiteralPath)
    try {
        $sha256 = [System.Security.Cryptography.SHA256]::Create()
        try {
            return ([System.BitConverter]::ToString(
                $sha256.ComputeHash($stream)
            )).Replace('-', '').ToLowerInvariant()
        } finally {
            $sha256.Dispose()
        }
    } finally {
        $stream.Dispose()
    }
}

if ($Artifact -and (Test-Path $Artifact -PathType Leaf) -and $Artifact.EndsWith('.zip')) {
    # 用 [guid]::NewGuid() 而非 New-Guid cmdlet：本脚本经 powershell.exe（Windows PS
    # 5.1）被 bump_version.py 调起，cmdlet 自动加载在 Win Server 2025 runner 上偶发
    # 失败（release v0.4.33 "New-Guid not recognized"）。直接调 .NET Guid 不依赖
    # 模块发现，PS 5.1 与 7+ 行为一致。同 build_winui_release.ps1 的 Get-FileHash 修复。
    $tempRoot = [System.IO.Path]::GetTempPath()
    if ([string]::IsNullOrWhiteSpace($tempRoot)) {
        $tempRoot = (Get-Location).Path
    }
    $extract = Join-Path $tempRoot "VibeOCR-verify-$([guid]::NewGuid().ToString())"
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
# fixtures to pass, so validate the stable entry points and supervisor contract.
$requiredFiles = @(
    'VibeOCR.WinUI.exe',
    'VibeOCR.Bootstrapper.exe',
    'updater.exe',
    'VibeOCR.WinUI.dll',
    'VibeOCR.WinUI.pri',
    'VibeOCR.Contracts.dll',
    'VibeOCR.Platform.dll',
    'App.xbf',
    'MainWindow.xbf',
    'Views\AboutPage.xbf',
    'Views\BatchPage.xbf',
    'Views\DiagnosticsPage.xbf',
    'Views\PdfPage.xbf',
    'Views\QrCodePage.xbf',
    'Views\RecognitionPage.xbf',
    'Views\SettingsPage.xbf',
    'component-lock.json',
    'product-release-manifest.json',
    'backend\runtime-manifest.json',
    'runtime-installer\vibeocr-runtime-installer.exe',
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

$manifestPath = Join-Path $root 'product-release-manifest.json'
if (Test-Path $manifestPath -PathType Leaf) {
    $manifest = $null
    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    } catch {
        $errors.Add("product manifest is invalid JSON: $($_.Exception.Message)")
    }
    if ($null -ne $manifest) {
        if ($manifest.frontend -ne 'next') {
            $errors.Add("product release manifest frontend must be next")
        }
        $records = @($manifest.files.psobject.Properties)
        if ($records.Count -eq 0) {
            $errors.Add("product release manifest has no file closure")
        }
        foreach ($record in $records) {
            $boundPath = Join-Path $root $record.Name
            if (-not (Test-Path $boundPath -PathType Leaf)) {
                $errors.Add("bound product file missing: $($record.Name)")
                continue
            }
            $actualHash = Get-Sha256 -LiteralPath $boundPath
            if ($actualHash -ne $record.Value.sha256) {
                $errors.Add("bound product file hash mismatch: $($record.Name)")
            }
            if ((Get-Item -LiteralPath $boundPath).Length -ne $record.Value.size) {
                $errors.Add("bound product file size mismatch: $($record.Name)")
            }
        }

        $lockPath = Join-Path $root 'component-lock.json'
        $lock = Get-Content -LiteralPath $lockPath -Raw | ConvertFrom-Json
        $lockHash = Get-Sha256 -LiteralPath $lockPath
        if ($lockHash -ne $manifest.component_lock_sha256) {
            $errors.Add("embedded component lock hash mismatch")
        }
        $requiredCapabilities = @($lock.required_capabilities | Sort-Object)
        $expectedCapabilities = @(
            'export.document.v1',
            'ocr.recognition.v2',
            'pdf.edit.v2',
            'qrcode.v2',
            'runtime.settings.v2'
        )
        if (($requiredCapabilities -join "`n") -ne ($expectedCapabilities -join "`n")) {
            $errors.Add("Next component lock capability set is incomplete")
        }

        $runtimeManifestPath = Join-Path $root 'backend\runtime-manifest.json'
        $runtimeManifest = Get-Content -LiteralPath $runtimeManifestPath -Raw | ConvertFrom-Json
        $runtimeManifestHash = Get-Sha256 -LiteralPath $runtimeManifestPath
        if ($runtimeManifestHash -ne $lock.backend.runtime_manifest_sha256) {
            $errors.Add("bound runtime manifest hash mismatch")
        }
        $backendWheelPath = Join-Path (Join-Path $root 'backend') $runtimeManifest.backend_wheel
        if (-not (Test-Path $backendWheelPath -PathType Leaf)) {
            $errors.Add("bound backend wheel is missing")
        } else {
            $backendHash = Get-Sha256 -LiteralPath $backendWheelPath
            if ($backendHash -ne $lock.backend.artifact_sha256 -or
                $backendHash -ne $runtimeManifest.backend_sha256) {
                $errors.Add("bound backend wheel hash mismatch")
            }
        }
        $installerPath = Join-Path $root 'runtime-installer\vibeocr-runtime-installer.exe'
        if (Test-Path $installerPath -PathType Leaf) {
            $installerHash = Get-Sha256 -LiteralPath $installerPath
            if ($installerHash -ne $runtimeManifest.installer.executable_sha256) {
                $errors.Add("extracted Runtime Installer hash mismatch")
            }
        }
    }
}

$legacyBackendEntries = @(
    'worker',
    'worker\vibeocr\worker_host',
    'contracts\v1'
)
foreach ($relative in $legacyBackendEntries) {
    if (Test-Path (Join-Path $root $relative)) {
        $errors.Add("legacy backend entry present: $relative")
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

# Rule: no PySide6 UI modules in the Supervisor runtime.
$pyside = Get-ChildItem -Path $root -Recurse -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -eq 'PySide6' }
if ($pyside) { $errors.Add('PySide6 UI modules present; Supervisor must exclude the legacy UI') }

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

Write-Host "Artifact $Artifact verified OK (Supervisor/protocol v2, framework-dependent, no PySide6, no dev profile)"
exit 0
