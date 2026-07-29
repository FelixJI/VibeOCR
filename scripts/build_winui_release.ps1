<#
.SYNOPSIS
Publishes the framework-dependent unpackaged WinUI release layout.

.DESCRIPTION
Publishes the App and Bootstrapper into one deterministic staging directory,
then stages the UI-free Python supervisor and protocol-v2 contracts.
The existing portable Python runtime/model cache live outside the archive and
are preserved by the updater.
#>
[CmdletBinding()]
param(
    [string]$Configuration = "Release",
    [string]$OutputDir = "$env:TEMP\VibeOCR-winui-publish",
    [string]$Version = "",
    [string]$BackendVersion = "",
    [string]$WheelDirectory = "",
    [string]$BackendWheel = "",
    [string]$PythonExecutable = ""
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$dotnet = Join-Path $env:ProgramFiles 'dotnet\dotnet.exe'
if (-not (Test-Path $dotnet)) { throw 'dotnet not found' }
if (-not $PythonExecutable) {
    $workspacePython = Join-Path $repo '.venv\Scripts\python.exe'
    if (Test-Path $workspacePython -PathType Leaf) {
        $PythonExecutable = $workspacePython
    } else {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($null -eq $pythonCommand) { throw 'python not found' }
        $PythonExecutable = $pythonCommand.Source
    }
}
$PythonExecutable = (Resolve-Path $PythonExecutable).Path
if (-not $Version) {
    # During the single-repository transition the Classic project carries the
    # shared product version.  The root is intentionally workspace-only and no
    # longer publishes a meta wheel.
    $pyproject = Get-Content (Join-Path $repo 'apps\vibeocr-pyside\pyproject.toml') -Raw
    if ($pyproject -notmatch '(?m)^version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"') {
        throw 'project version not found in pyproject.toml'
    }
    $Version = $Matches[1]
}
if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') { throw "invalid version: $Version" }
if (-not $BackendVersion -and $BackendWheel) {
    $backendWheelName = Split-Path -Leaf $BackendWheel
    if ($backendWheelName -notmatch '^vibeocr_backend-([0-9]+\.[0-9]+\.[0-9]+)-') {
        throw "cannot derive backend version from wheel: $backendWheelName"
    }
    $BackendVersion = $Matches[1]
}
if (-not $BackendVersion) {
    $backendPyproject = Get-Content (Join-Path $repo 'packages\vibeocr-backend\pyproject.toml') -Raw
    if ($backendPyproject -notmatch '(?m)^version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"') {
        throw 'backend version not found in pyproject.toml'
    }
    $BackendVersion = $Matches[1]
}
if ($BackendVersion -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
    throw "invalid backend version: $BackendVersion"
}

$outputFull = [IO.Path]::GetFullPath($OutputDir)
if ($outputFull -eq [IO.Path]::GetPathRoot($outputFull) -or $outputFull -eq $repo) {
    throw "unsafe OutputDir: $outputFull"
}
if (Test-Path $outputFull) {
    Remove-Item -LiteralPath $outputFull -Recurse -Force
}
New-Item -ItemType Directory -Path $outputFull -Force | Out-Null

$app = Join-Path $repo 'src\dotnet\VibeOCR.App\VibeOCR.App.csproj'
$bootstrapper = Join-Path $repo 'src\dotnet\VibeOCR.Bootstrapper\VibeOCR.Bootstrapper.csproj'
$solution = Join-Path $repo 'src\dotnet\VibeOCR.slnx'
& $dotnet restore $solution --locked-mode
if ($LASTEXITCODE -ne 0) { throw "solution restore failed with exit $LASTEXITCODE" }
Write-Host "Publishing $app ($Configuration, framework-dependent, win-x64) -> $OutputDir"
& $dotnet publish $app -c $Configuration -r win-x64 --self-contained false --no-restore -p:Version=$Version -o $outputFull
if ($LASTEXITCODE -ne 0) { throw "publish failed with exit $LASTEXITCODE" }

# Publish the bootstrapper into the same root; building it elsewhere would
# produce a release without its only supported entry/repair surface.
& $dotnet publish $bootstrapper -c $Configuration --self-contained false --no-restore -p:Version=$Version -o $outputFull
if ($LASTEXITCODE -ne 0) { throw "bootstrapper build failed with exit $LASTEXITCODE" }

# Build the release metadata and the independent stdlib updater in this same
# orchestration entry.  CI and local builds must not rely on bump_version.py
# performing an extra, out-of-band completion step.
& $PythonExecutable (Join-Path $repo 'scripts\build_release_metadata.py') --version $Version --output $outputFull
if ($LASTEXITCODE -ne 0) { throw "release metadata/updater build failed with exit $LASTEXITCODE" }

# Stage the exact contracts + Runtime Client + Backend wheel set. WinUI never
# copies workspace source directly; all three wheels retain physical ownership.
if (-not $WheelDirectory -and $BackendWheel) {
    $WheelDirectory = Split-Path -Parent (Resolve-Path $BackendWheel).Path
}
if (-not $WheelDirectory) {
    $WheelDirectory = Join-Path $outputFull '.python-wheels'
    New-Item -ItemType Directory -Path $WheelDirectory -Force | Out-Null
    foreach ($project in @(
        'packages\vibeocr-contracts-py',
        'packages\vibeocr-runtime-client-py',
        'packages\vibeocr-backend'
    )) {
        & $PythonExecutable -m build --wheel (Join-Path $repo $project) --outdir $WheelDirectory
        if ($LASTEXITCODE -ne 0) { throw "wheel build failed for $project" }
    }
}
$wheelDirFull = (Resolve-Path $WheelDirectory).Path
$runtimeWheels = @(
    Get-ChildItem -LiteralPath $wheelDirFull -Filter "vibeocr_runtime_contracts-*.whl" | Select-Object -First 1
    Get-ChildItem -LiteralPath $wheelDirFull -Filter "vibeocr_runtime_client-*.whl" | Select-Object -First 1
    Get-ChildItem -LiteralPath $wheelDirFull -Filter "vibeocr_backend-$BackendVersion-*.whl" | Select-Object -First 1
)
if (@($runtimeWheels | Where-Object { $_ -eq $null }).Count -gt 0) {
    throw 'contracts/runtime-client/backend wheel set is incomplete'
}
$supervisorRoot = Join-Path $outputFull 'supervisor'
# Ensure a clean extraction target: the whole $outputFull is wiped at the top,
# but be defensive in case supervisor/ already exists (e.g. a re-run) so that
# ExtractToDirectory's two-arg overload (which does not overwrite) cannot fail
# on a pre-existing file.
if (Test-Path $supervisorRoot) { Remove-Item -LiteralPath $supervisorRoot -Recurse -Force }
New-Item -ItemType Directory -Path $supervisorRoot -Force | Out-Null
Add-Type -AssemblyName System.IO.Compression.FileSystem
# Use the two-argument overload explicitly: it is the only one present in both
# Windows PowerShell 5.1 (entryNameEncoding-only 3rd arg) and PowerShell 7+
# (bool overwrite 3rd arg). Passing $true binds to Encoding in 5.1 and crashes.
$wheelStore = Join-Path $outputFull 'backend'
New-Item -ItemType Directory -Path $wheelStore -Force | Out-Null
$wheelRecords = @()
foreach ($wheel in $runtimeWheels) {
    [IO.Compression.ZipFile]::ExtractToDirectory($wheel.FullName, $supervisorRoot)
    Copy-Item -LiteralPath $wheel.FullName -Destination $wheelStore -Force
    $hashBytes = [Security.Cryptography.SHA256]::Create().ComputeHash([IO.File]::ReadAllBytes($wheel.FullName))
    $hashSb = New-Object System.Text.StringBuilder($hashBytes.Length * 2)
    foreach ($b in $hashBytes) { [void]$hashSb.Append($b.ToString('x2')) }
    $wheelRecords += [ordered]@{ file = $wheel.Name; sha256 = $hashSb.ToString() }
}
$backendRecord = $wheelRecords | Where-Object { $_.file -like 'vibeocr_backend-*' } | Select-Object -First 1
$protocolWheel = $runtimeWheels | Where-Object { $_.Name -like 'vibeocr_runtime_contracts-*' } | Select-Object -First 1
$protocolVersion = $protocolWheel.BaseName -replace '^vibeocr_runtime_contracts-([^-]+)-.*$', '$1'
$sourceCommit = (git -C $repo rev-parse HEAD).Trim()
$productManifest = [ordered]@{
    frontend = 'winui'
    frontend_version = $Version
    backend_version = $BackendVersion
    backend_wheel = $backendRecord.file
    backend_sha256 = $backendRecord.sha256
    supervisor_module = 'vibeocr.backend.supervisor.main'
    python_wheels = $wheelRecords
    protocol_major = 2
    protocol_version = $protocolVersion
    source_commit = $sourceCommit
}
$productManifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $outputFull 'product-manifest.json') -Encoding utf8

$protocolSource = Join-Path $repo 'packages\vibeocr-contracts-py\src\vibeocr\runtime_contracts'
$contractsRoot = Join-Path $outputFull 'contracts'
New-Item -ItemType Directory -Path $contractsRoot -Force | Out-Null
Copy-Item -LiteralPath $protocolSource -Destination (Join-Path $contractsRoot 'v2') -Recurse -Force
$contractCaches = @(
    Get-ChildItem -LiteralPath (Join-Path $contractsRoot 'v2') -Recurse -Directory -Filter '__pycache__'
)
foreach ($cache in $contractCaches) {
    Remove-Item -LiteralPath $cache.FullName -Recurse -Force
}

$stagedSupervisor = Join-Path $supervisorRoot 'vibeocr\backend\supervisor\main.py'
$stagedGolden = Join-Path $contractsRoot 'v2\golden\golden.json'
if (-not (Test-Path $stagedSupervisor -PathType Leaf)) {
    throw 'backend wheel did not stage vibeocr/backend/supervisor/main.py'
}
if (-not (Test-Path $stagedGolden -PathType Leaf)) {
    throw 'contracts v2 staging is incomplete'
}
Copy-Item -LiteralPath (Join-Path $repo 'CHANGELOG.md') -Destination $outputFull -Force
Copy-Item -LiteralPath (Join-Path $repo 'LICENSE') -Destination $outputFull -Force

Write-Host "WinUI release layout published to $outputFull"
