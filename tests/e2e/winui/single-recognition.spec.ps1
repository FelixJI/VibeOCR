param(
    [string]$ArtifactsPath = "$env:TEMP\VibeOCR-single-recognition-e2e"
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$dotnet = Join-Path $env:ProgramFiles 'dotnet\dotnet.exe'
if (-not (Test-Path $dotnet)) { throw 'x64 dotnet was not found' }
$python = $env:VIBEOCR_PYTHON
if (-not $python) { $python = Join-Path $repo '.venv\Scripts\python.exe' }
if (-not (Test-Path $python)) { throw 'Set VIBEOCR_PYTHON to the project Python interpreter' }
$node = $env:VIBEOCR_NODE
if (-not $node) { $node = (Get-Command node -ErrorAction Stop).Source }

$rows = [System.Collections.Generic.List[object]]::new()
function Invoke-Gate([string]$Name, [scriptblock]$Action) {
    & $Action
    if ($LASTEXITCODE -ne 0) { throw "$Name failed with exit code $LASTEXITCODE" }
    $rows.Add([pscustomobject]@{ Row = $Name; Status = 'PASS' })
}

$env:VIBEOCR_REPOSITORY_ROOT = $repo
Invoke-Gate 'Python export facade / Unicode path / hashes' {
    & $python -m pytest (Join-Path $repo 'tests\e2e\winui\test_single_recognition_parity.py') -q
}
Invoke-Gate 'C# clipboard retry / overwrite / typed export' {
    & $dotnet restore (Join-Path $repo 'src\dotnet\VibeOCR.slnx') --locked-mode --artifacts-path $ArtifactsPath
    if ($LASTEXITCODE -ne 0) { return }
    & $dotnet test (Join-Path $repo 'tests\dotnet\VibeOCR.App.Tests\VibeOCR.App.Tests.csproj') -c Release --no-restore --artifacts-path $ArtifactsPath --filter ResultActions
}
Invoke-Gate 'Web semantic rendering / XSS / Unicode' {
    & $node --test (Join-Path $repo 'tests\web\result-renderer.test.ts')
}

$rows | Format-Table -AutoSize
if ($rows.Count -ne 3 -or $rows.Where({ $_.Status -ne 'PASS' }).Count -ne 0) { exit 1 }
