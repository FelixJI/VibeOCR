<#
.SYNOPSIS
Publishes the framework-dependent unpackaged WinUI release layout.

.DESCRIPTION
Publishes the App and Bootstrapper into one deterministic staging directory,
then stages the UI-free Python WorkerHost source and versioned contracts.
The existing portable Python runtime/model cache live outside the archive and
are preserved by the updater.
#>
[CmdletBinding()]
param(
    [string]$Configuration = "Release",
    [string]$OutputDir = "$env:TEMP\VibeOCR-winui-publish",
    [string]$Version = "",
    [string]$WheelDirectory = "",
    [string]$BackendWheel = ""
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$dotnet = Join-Path $env:ProgramFiles 'dotnet\dotnet.exe'
if (-not (Test-Path $dotnet)) { throw 'dotnet not found' }
if (-not $Version) {
    $pyproject = Get-Content (Join-Path $repo 'pyproject.toml') -Raw
    if ($pyproject -notmatch '(?m)^version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"') {
        throw 'project version not found in pyproject.toml'
    }
    $Version = $Matches[1]
}
if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') { throw "invalid version: $Version" }

$outputFull = [IO.Path]::GetFullPath($OutputDir)
if ($outputFull -eq [IO.Path]::GetPathRoot($outputFull) -or $outputFull -eq $repo) {
    throw "unsafe OutputDir: $outputFull"
}
if (Test-Path $outputFull) {
    Remove-Item -LiteralPath $outputFull -Recurse -Force
}
New-Item -ItemType Directory -Path $outputFull -Force | Out-Null

$app = Join-Path $repo 'src\dotnet\VibeOCR.App\VibeOCR.App.csproj'
$solution = Join-Path $repo 'src\dotnet\VibeOCR.slnx'
& $dotnet restore $solution
if ($LASTEXITCODE -ne 0) { throw "restore failed with exit $LASTEXITCODE" }
Write-Host "Publishing $app ($Configuration, framework-dependent, win-x64) -> $OutputDir"
& $dotnet publish $app -c $Configuration -r win-x64 --self-contained false --no-restore -p:Version=$Version -o $outputFull
if ($LASTEXITCODE -ne 0) { throw "publish failed with exit $LASTEXITCODE" }

# Publish the bootstrapper into the same root; building it elsewhere would
# produce a release without its only supported entry/repair surface.
$bootstrapper = Join-Path $repo 'src\dotnet\VibeOCR.Bootstrapper\VibeOCR.Bootstrapper.csproj'
& $dotnet publish $bootstrapper -c $Configuration -r win-x64 --self-contained false --no-restore -p:Version=$Version -o $outputFull
if ($LASTEXITCODE -ne 0) { throw "bootstrapper build failed with exit $LASTEXITCODE" }

# Stage the exact contracts + client + backend wheel set. WinUI never copies
# workspace source directly; all three wheels retain their physical ownership.
if (-not $WheelDirectory -and $BackendWheel) {
    $WheelDirectory = Split-Path -Parent (Resolve-Path $BackendWheel).Path
}
if (-not $WheelDirectory) {
    $WheelDirectory = Join-Path $outputFull '.python-wheels'
    New-Item -ItemType Directory -Path $WheelDirectory -Force | Out-Null
    foreach ($project in @(
        'packages\vibeocr-contracts-py',
        'packages\vibeocr-client-py',
        'packages\vibeocr-backend'
    )) {
        python -m build --wheel (Join-Path $repo $project) --outdir $WheelDirectory
        if ($LASTEXITCODE -ne 0) { throw "wheel build failed for $project" }
    }
}
$wheelDirFull = (Resolve-Path $WheelDirectory).Path
$runtimeWheels = @(
    Get-ChildItem -LiteralPath $wheelDirFull -Filter "vibeocr_contracts_py-$Version-*.whl" | Select-Object -First 1
    Get-ChildItem -LiteralPath $wheelDirFull -Filter "vibeocr_client_py-$Version-*.whl" | Select-Object -First 1
    Get-ChildItem -LiteralPath $wheelDirFull -Filter "vibeocr_backend-$Version-*.whl" | Select-Object -First 1
)
if (@($runtimeWheels | Where-Object { $_ -eq $null }).Count -gt 0) {
    throw 'contracts/client/backend wheel set is incomplete'
}
$workerRoot = Join-Path $outputFull 'worker'
# Ensure a clean extraction target: the whole $outputFull is wiped at the top,
# but be defensive in case worker/ already exists (e.g. a re-run) so that
# ExtractToDirectory's two-arg overload (which does not overwrite) cannot fail
# on a pre-existing file.
if (Test-Path $workerRoot) { Remove-Item -LiteralPath $workerRoot -Recurse -Force }
New-Item -ItemType Directory -Path $workerRoot -Force | Out-Null
Add-Type -AssemblyName System.IO.Compression.FileSystem
# Use the two-argument overload explicitly: it is the only one present in both
# Windows PowerShell 5.1 (entryNameEncoding-only 3rd arg) and PowerShell 7+
# (bool overwrite 3rd arg). Passing $true binds to Encoding in 5.1 and crashes.
$wheelStore = Join-Path $outputFull 'backend'
New-Item -ItemType Directory -Path $wheelStore -Force | Out-Null
$wheelRecords = @()
foreach ($wheel in $runtimeWheels) {
    [IO.Compression.ZipFile]::ExtractToDirectory($wheel.FullName, $workerRoot)
    Copy-Item -LiteralPath $wheel.FullName -Destination $wheelStore -Force
    $hashBytes = [Security.Cryptography.SHA256]::Create().ComputeHash([IO.File]::ReadAllBytes($wheel.FullName))
    $hashSb = New-Object System.Text.StringBuilder($hashBytes.Length * 2)
    foreach ($b in $hashBytes) { [void]$hashSb.Append($b.ToString('x2')) }
    $wheelRecords += [ordered]@{ file = $wheel.Name; sha256 = $hashSb.ToString() }
}
$backendRecord = $wheelRecords | Where-Object { $_.file -like 'vibeocr_backend-*' } | Select-Object -First 1
$sourceCommit = (git -C $repo rev-parse HEAD).Trim()
$productManifest = [ordered]@{
    frontend = 'winui'
    frontend_version = $Version
    backend_wheel = $backendRecord.file
    backend_sha256 = $backendRecord.sha256
    python_wheels = $wheelRecords
    protocol_major = 1
    source_commit = $sourceCommit
}
$productManifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $outputFull 'product-manifest.json') -Encoding utf8

$protocolSource = Join-Path $repo 'packages\vibeocr-contracts-py\src\vibeocr\protocol'
Copy-Item -LiteralPath $protocolSource -Destination (Join-Path $outputFull 'contracts') -Recurse -Force
Copy-Item -LiteralPath (Join-Path $repo 'CHANGELOG.md') -Destination $outputFull -Force
Copy-Item -LiteralPath (Join-Path $repo 'LICENSE') -Destination $outputFull -Force

Write-Host "WinUI release layout published to $outputFull"
