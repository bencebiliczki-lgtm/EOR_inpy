$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "A projekt .venv Python környezete nem található."
}

Push-Location $root
try {
    & $python -c "import sys; assert sys.version_info[:2] == (3, 12), f'A Windows-csomaghoz Python 3.12 szükséges, jelenlegi: {sys.version.split()[0]}'"
    if ($LASTEXITCODE -ne 0) {
        throw "A célgépes Windows-csomagot Python 3.12-es .venv környezetből kell készíteni."
    }

    & $python -m pip install -c constraints-windows-legacy.txt -e ".[ui,hardware,export,package]"
    if ($LASTEXITCODE -ne 0) {
        throw "A régi CPU-val kompatibilis csomagfüggőségek telepítése sikertelen."
    }

    & $python -c "import numpy; assert numpy.__version__ == '1.26.4', numpy.__version__"
    if ($LASTEXITCODE -ne 0) {
        throw "A telepített NumPy verziója nem 1.26.4."
    }

    & $python -m pip check
    if ($LASTEXITCODE -ne 0) {
        throw "A Python-csomagfüggőségek ellenőrzése sikertelen."
    }

    & $python -c "import nidaqmx.system; import serial.tools.list_ports"
    if ($LASTEXITCODE -ne 0) {
        throw "A hardverfelderítéshez szükséges nidaqmx vagy pyserial csomag hiányzik. Telepítsd: python -m pip install -e '.[hardware]'"
    }

    & $python -m PyInstaller --noconfirm --clean eor_control.spec
    Write-Host "Elkészült: $root\dist\EOR_Controller.exe"
}
finally {
    Pop-Location
}
