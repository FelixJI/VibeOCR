<#
.SYNOPSIS
Regenerates the repository's committed NuGet lock files intentionally.

.DESCRIPTION
Normal restores run in locked mode and must not rewrite packages.lock.json.
Use this script only when package references, central package versions, target
frameworks, or supported runtime identifiers change. Commit the resulting lock
file changes together with the dependency declaration changes.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$solution = Join-Path $repo 'src\dotnet\VibeOCR.slnx'
$dotnet = Join-Path $env:ProgramFiles 'dotnet\dotnet.exe'

if (-not (Test-Path -LiteralPath $dotnet)) {
    $dotnetCommand = Get-Command dotnet -ErrorAction SilentlyContinue
    if (-not $dotnetCommand) {
        throw 'dotnet not found'
    }
    $dotnet = $dotnetCommand.Source
}

Push-Location $repo
try {
    Write-Host 'Regenerating NuGet lock files with the SDK pinned by global.json...'
    & $dotnet restore $solution `
        --force-evaluate `
        -p:UpdatePackageLocks=true `
        -p:RestoreLockedMode=false
    if ($LASTEXITCODE -ne 0) {
        throw "lock file update failed with exit $LASTEXITCODE"
    }

    Write-Host 'NuGet lock files regenerated. Review and commit dependency declarations and lock files together.'
    git diff --name-only -- ':(glob)**/packages.lock.json'
}
finally {
    Pop-Location
}
