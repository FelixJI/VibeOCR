param(
    [string]$ArtifactsPath = "$env:TEMP\VibeOCR-batch-e2e"
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$dotnet = Join-Path $env:ProgramFiles 'dotnet\dotnet.exe'
if (-not (Test-Path $dotnet)) { throw 'x64 dotnet was not found' }
$python = $env:VIBEOCR_PYTHON
if (-not $python) { $python = Join-Path $repo '.venv\Scripts\python.exe' }
if (-not (Test-Path $python)) { throw 'Set VIBEOCR_PYTHON to the project Python interpreter' }

$rows = [System.Collections.Generic.List[object]]::new()
function Invoke-Gate([string]$Name, [scriptblock]$Action) {
    & $Action
    if ($LASTEXITCODE -ne 0) { throw "$Name failed with exit code $LASTEXITCODE" }
    $rows.Add([pscustomobject]@{ Row = $Name; Status = 'PASS' })
}

$env:VIBEOCR_REPOSITORY_ROOT = $repo

# Gate 1: Python batch queue source-of-truth.
# Covers insertion order preservation, commit boundary, and the cancel contract that
# marks every PENDING request as CANCELLED — the exact behaviour the WinUI view model
# must mirror (cancel-all cancels pending items, not only running ones).
Invoke-Gate 'Python batch queue / order / cancel-marks-pending' {
    & $python -m pytest `
        (Join-Path $repo 'tests\workers\test_batch_queue_manager.py') `
        (Join-Path $repo 'tests\views\test_batch_cancel.py') `
        -q
}

# Gate 2: C# BatchViewModel parity.
# Covers queue dedup/ordering, concurrency budget cap, single/all cancel, continue-on-failure,
# export delegation, restart-no-restore, and the CancelAll-marks-pending invariant.
Invoke-Gate 'C# batch view model / queue / concurrency / cancel / export' {
    & $dotnet restore (Join-Path $repo 'src\dotnet\VibeOCR.slnx') --locked-mode --artifacts-path $ArtifactsPath
    if ($LASTEXITCODE -ne 0) { return }
    & $dotnet test (Join-Path $repo 'tests\dotnet\VibeOCR.App.Tests\VibeOCR.App.Tests.csproj') `
        -c Release --no-restore --artifacts-path $ArtifactsPath --filter Batch
}

# Gate 3: C# BatchCommands output path uniqueness.
# Verifies export-all produces non-colliding absolute output paths, mirroring the
# single-recognition export parity gate but for a multi-item queue.
Invoke-Gate 'C# batch export / unique output paths' {
    & $dotnet test (Join-Path $repo 'tests\dotnet\VibeOCR.App.Tests\VibeOCR.App.Tests.csproj') `
        -c Release --no-restore --artifacts-path $ArtifactsPath `
        --filter "FullyQualifiedName~ExportAllWritesUniqueOutputPerItemAndDelegatesToWorker|FullyQualifiedName~UniqueOutputPathAvoidsCollisionsWithExistingFiles"
}

$rows | Format-Table -AutoSize
if ($rows.Count -ne 3 -or $rows.Where({ $_.Status -ne 'PASS' }).Count -ne 0) { exit 1 }
