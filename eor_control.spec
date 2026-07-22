# PyInstaller onefile build for the offline Windows measurement workstation.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

root = Path(SPECPATH)
package_metadata = copy_metadata("nidaqmx", recursive=True)
timezone_data = collect_data_files("tzdata")

a = Analysis(
    [str(root / "src" / "eor_control" / "__main__.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=[
        (str(root / "img"), "img"),
        (str(root / "docs" / "drivers_readme.txt"), "."),
    ] + package_metadata + timezone_data,
    hiddenimports=[
        "openpyxl",
        "nidaqmx",
        "nidaqmx.system",
        "serial",
        "serial.tools",
        "serial.tools.list_ports",
        "serial.tools.list_ports_windows",
        "pyqtgraph",
        "tzdata",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="EOR_Controller",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    icon=str(root / "img" / "icon.png"),
)
