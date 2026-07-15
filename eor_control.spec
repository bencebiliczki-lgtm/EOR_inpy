# PyInstaller onedir build for the offline Windows measurement workstation.
from pathlib import Path

root = Path(SPECPATH)

a = Analysis(
    [str(root / "src" / "eor_control" / "__main__.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=[
        (str(root / "img" / "icon.png"), "img"),
        (str(root / "docs" / "drivers_readme.txt"), "."),
    ],
    hiddenimports=[
        "openpyxl",
        "nidaqmx",
        "nidaqmx.system",
        "serial",
        "serial.tools",
        "serial.tools.list_ports",
        "serial.tools.list_ports_windows",
        "pyqtgraph",
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
    [],
    exclude_binaries=True,
    name="EOR_Controller",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(root / "img" / "icon.png"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="EOR_Controller",
)
