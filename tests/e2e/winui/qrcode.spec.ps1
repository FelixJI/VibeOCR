param(
    [string]$ArtifactsPath = "$env:TEMP\VibeOCR-qrcode-e2e"
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

# Gate 1: Python QR service source-of-truth.
# Decode service owns the strict http/https URL check (rejects javascript:/file:);
# generate service owns qrcode + barcode rendering. These are the algorithm truth the
# WinUI tab must mirror without re-implementing.
Invoke-Gate 'Python QR decode + generate service truth' {
    & $python -m pytest `
        (Join-Path $repo 'tests\services\test_qrcode_decode_service.py') `
        (Join-Path $repo 'tests\services\test_qrcode_service.py') `
        -q
}

# Gate 2: Python WorkerHost contract propagates is_url.
# Confirms the decode handler/adapter forward the Python is_url flag so the C# tab can
# decide the safe-open affordance from a single source of truth.
Invoke-Gate 'Python WorkerHost QR handler / is_url propagation' {
    & $python -m pytest `
        (Join-Path $repo 'tests\worker_host\test_handlers.py::test_qr_decode_handler_maps_payload_to_result') `
        (Join-Path $repo 'tests\worker_host\test_handlers.py::test_qr_generate_handler_maps_payload_to_result') `
        -q
}

# Gate 3: C# QrCodeViewModel parity.
# Covers file/clipboard input, multi-code results, no-result, URL-safety filtering,
# qrcode/barcode generate, payload lifecycle, and save (overwrite confirmation, picker cancel).
Invoke-Gate 'C# QR view model / decode / generate / save' {
    & $dotnet restore (Join-Path $repo 'src\dotnet\VibeOCR.slnx') --locked-mode --artifacts-path $ArtifactsPath
    if ($LASTEXITCODE -ne 0) { return }
    & $dotnet test (Join-Path $repo 'tests\dotnet\VibeOCR.App.Tests\VibeOCR.App.Tests.csproj') `
        -c Release --no-restore --artifacts-path $ArtifactsPath --filter QrCode
}

$rows | Format-Table -AutoSize
if ($rows.Count -ne 3 -or $rows.Where({ $_.Status -ne 'PASS' }).Count -ne 0) { exit 1 }
