$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "A projekt .venv Python környezete nem található."
}

Push-Location $root
try {
    & $python -m pip install -c constraints-windows-legacy.txt "numpy==1.26.4"
    if ($LASTEXITCODE -ne 0) {
        throw "A régi CPU-val kompatibilis NumPy telepítése sikertelen."
    }

    & $python -c "import numpy; assert numpy.__version__ == '1.26.4', numpy.__version__"
    if ($LASTEXITCODE -ne 0) {
        throw "A telepített NumPy verziója nem 1.26.4."
    }

    & $python -m PyInstaller --noconfirm --clean eor_control.spec
    Write-Host "Elkészült: $root\dist\EOR_Controller\EOR_Controller.exe"
}
finally {
    Pop-Location
}
