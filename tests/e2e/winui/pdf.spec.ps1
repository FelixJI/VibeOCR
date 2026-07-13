param(
    [string]$ArtifactsPath = "$env:TEMP\VibeOCR-pdf-e2e"
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

# Gate 1: Python PDF OCR orchestrator + sidecar truth.
# Covers durable batch+save, sidecar checkpoint, resume filtering, cancel at
# page boundary, compress failure keeping pages, the page-state/layer-source
# projection, write-error aggregation, and the winui-dev sidecar isolation.
Invoke-Gate 'Python PDF orchestrator / sidecar / resume truth' {
    & $python -m pytest `
        (Join-Path $repo 'tests\application\test_pdf_ocr_orchestrator.py') `
        (Join-Path $repo 'tests\utils\test_ocr_sidecar.py') `
        (Join-Path $repo 'tests\services\test_pdf_service_save_incremental.py') `
        -q
}

# Gate 2: Python WorkerHost PDF contract + handlers.
# Confirms the 8 new pdf.* methods validate cross-language and the handlers map
# payloads to the backend/orchestrator correctly.
Invoke-Gate 'Python WorkerHost PDF contract + handlers' {
    & $python -m pytest `
        (Join-Path $repo 'tests\contracts') `
        (Join-Path $repo 'tests\worker_host\test_handlers.py') `
        (Join-Path $repo 'tests\worker_host\test_composition.py') `
        -q
}

# Gate 3: C# PdfViewModel parity.
# Covers open/cancel, rotate (selected/all/empty), start_ocr with aggregated
# write_errors, save in-place, delete text layers, localized worker errors,
# close session, and thumbnail byte passthrough.
Invoke-Gate 'C# PDF view model / open / rotate / ocr / save' {
    & $dotnet restore (Join-Path $repo 'src\dotnet\VibeOCR.slnx') --locked-mode --artifacts-path $ArtifactsPath
    if ($LASTEXITCODE -ne 0) { return }
    & $dotnet test (Join-Path $repo 'tests\dotnet\VibeOCR.App.Tests\VibeOCR.App.Tests.csproj') `
        -c Release --no-restore --artifacts-path $ArtifactsPath --filter Pdf
}

$rows | Format-Table -AutoSize
if ($rows.Count -ne 3 -or $rows.Where({ $_.Status -ne 'PASS' }).Count -ne 0) { exit 1 }
