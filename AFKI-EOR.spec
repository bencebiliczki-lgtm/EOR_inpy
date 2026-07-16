# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import copy_metadata

root = Path(SPECPATH)
package_metadata = copy_metadata("nidaqmx", recursive=True)

a = Analysis(
    [str(root / "src" / "eor_control" / "__main__.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=[
        (str(root / "img"), "img"),
        (str(root / "docs" / "drivers_readme.txt"), "."),
    ] + package_metadata,
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
    a.binaries,
    a.datas,
    [],
    name="AFKI-EOR",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    icon=str(root / "img" / "icon.png"),
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
