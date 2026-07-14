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
    [string]$Version = ""
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

# Stage WorkerHost source.  The portable runtime is reused, but it needs an
# importable application package that is independent of the removed PySide UI.
$workerPackage = Join-Path $outputFull 'worker\vibeocr'
New-Item -ItemType Directory -Path (Split-Path -Parent $workerPackage) -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $repo 'src\vibeocr') -Destination $workerPackage -Recurse -Force
foreach ($relative in @('main.py', 'views', 'widgets', 'ui', 'output')) {
    $candidate = Join-Path $workerPackage $relative
    if (Test-Path $candidate) { Remove-Item -LiteralPath $candidate -Recurse -Force }
}
Get-ChildItem -Path $workerPackage -Directory -Recurse -Filter '__pycache__' |
    Remove-Item -Recurse -Force
Get-ChildItem -Path $workerPackage -File -Recurse -Filter '*.pyc' |
    Remove-Item -Force

Copy-Item -LiteralPath (Join-Path $repo 'contracts') -Destination (Join-Path $outputFull 'contracts') -Recurse -Force
Copy-Item -LiteralPath (Join-Path $repo 'CHANGELOG.md') -Destination $outputFull -Force
Copy-Item -LiteralPath (Join-Path $repo 'LICENSE') -Destination $outputFull -Force

Write-Host "WinUI release layout published to $outputFull"
