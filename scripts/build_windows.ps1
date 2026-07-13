$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "A projekt .venv Python környezete nem található."
}

Push-Location $root
try {
    & $python -m PyInstaller --noconfirm --clean eor_control.spec
    Write-Host "Elkészült: $root\dist\EOR_Controller\EOR_Controller.exe"
}
finally {
    Pop-Location
}
