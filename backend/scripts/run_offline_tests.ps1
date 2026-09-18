$ErrorActionPreference = 'Stop'

$backendRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $backendRoot '.venv\Scripts\python.exe'

if (-not (Test-Path $python)) {
    throw "Test interpreter was not found: $python"
}

Push-Location $backendRoot
try {
    & $python -m pytest -q -m "not network"
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
