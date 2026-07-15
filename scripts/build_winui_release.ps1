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

# Stage the exact prebuilt backend wheel. This is include-only: WinUI never
# copies the source tree and therefore cannot accidentally inherit new UI dirs.
if (-not $BackendWheel) {
    $backendOut = Join-Path $outputFull '.backend-wheel'
    python (Join-Path $repo 'scripts\build_backend_wheel.py') --output-dir $backendOut
    if ($LASTEXITCODE -ne 0) { throw "backend wheel build failed with exit $LASTEXITCODE" }
    $BackendWheel = (Get-ChildItem -LiteralPath $backendOut -Filter '*.whl' | Select-Object -First 1).FullName
}
$backendFull = (Resolve-Path $BackendWheel).Path
python (Join-Path $repo 'scripts\verify_backend_wheel.py') $backendFull
if ($LASTEXITCODE -ne 0) { throw "backend wheel verification failed with exit $LASTEXITCODE" }
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
[IO.Compression.ZipFile]::ExtractToDirectory($backendFull, $workerRoot)
$backendHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $backendFull).Hash.ToLowerInvariant()
$sourceCommit = (git -C $repo rev-parse HEAD).Trim()
$productManifest = [ordered]@{
    frontend = 'winui'
    frontend_version = $Version
    backend_wheel = [IO.Path]::GetFileName($backendFull)
    backend_sha256 = $backendHash
    protocol_major = 1
    source_commit = $sourceCommit
}
$productManifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $outputFull 'product-manifest.json') -Encoding utf8

Copy-Item -LiteralPath (Join-Path $repo 'contracts') -Destination (Join-Path $outputFull 'contracts') -Recurse -Force
Copy-Item -LiteralPath (Join-Path $repo 'CHANGELOG.md') -Destination $outputFull -Force
Copy-Item -LiteralPath (Join-Path $repo 'LICENSE') -Destination $outputFull -Force

Write-Host "WinUI release layout published to $outputFull"
