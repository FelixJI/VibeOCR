<#
.SYNOPSIS
Publishes the framework-dependent unpackaged WinUI release layout.

.DESCRIPTION
dotnet publish --self-contained false -r win-x64 for the App; the Python
worker is assembled by bump_version.py and only includes worker modules
(no PySide6 UI). The bootstrapper, App, WorkerHost, runtime and model cache
reuse the existing portable layout.
#>
[CmdletBinding()]
param(
    [string]$Configuration = "Release",
    [string]$OutputDir = "$env:TEMP\VibeOCR-winui-publish"
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$dotnet = Join-Path $env:ProgramFiles 'dotnet\dotnet.exe'
if (-not (Test-Path $dotnet)) { throw 'dotnet not found' }

$app = Join-Path $repo 'src\dotnet\VibeOCR.App\VibeOCR.App.csproj'
Write-Host "Publishing $app ($Configuration, framework-dependent, win-x64) -> $OutputDir"
& $dotnet publish $app -c $Configuration -r win-x64 --self-contained false -o $OutputDir
if ($LASTEXITCODE -ne 0) { throw "publish failed with exit $LASTEXITCODE" }

# The bootstrapper is published alongside so the repair-mode entry exists.
$bootstrapper = Join-Path $repo 'src\dotnet\VibeOCR.Bootstrapper\VibeOCR.Bootstrapper.csproj'
& $dotnet build $bootstrapper -c $Configuration -r win-x64 --self-contained false
if ($LASTEXITCODE -ne 0) { throw "bootstrapper build failed with exit $LASTEXITCODE" }

Write-Host "WinUI release layout published to $OutputDir"
