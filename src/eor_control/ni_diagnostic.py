import importlib
import importlib.metadata
import platform
import sys
from collections.abc import Sequence
from typing import TextIO


def _package_version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "nincs becsomagolva"


def _format_error(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


def run_ni_diagnostic(output: TextIO) -> int:
    """Inspect the local NI installation without creating a task or writing output."""

    print("AFKI-EOR NI diagnosztika (csak olvasás)", file=output)
    print(f"Python: {sys.version.split()[0]} ({platform.architecture()[0]})", file=output)
    print(f"Futtatható állomány: {sys.executable}", file=output)
    print(f"NumPy csomag: {_package_version('numpy')}", file=output)
    print(f"nidaqmx csomag: {_package_version('nidaqmx')}", file=output)

    try:
        numpy = importlib.import_module("numpy")
    except Exception as error:
        print(f"HIBA – NumPy import: {_format_error(error)}", file=output)
        return 1
    print(f"NumPy import: OK ({numpy.__version__})", file=output)

    try:
        system_module = importlib.import_module("nidaqmx.system")
        system = system_module.System.local()
        driver = system.driver_version
        driver_version = (
            f"{driver.major_version}.{driver.minor_version}.{driver.update_version}"
        )
        devices: Sequence[object] = tuple(system.devices)
    except Exception as error:
        print(f"HIBA – NI-DAQmx elérés: {_format_error(error)}", file=output)
        return 2

    print(f"NI-DAQmx driver: {driver_version}", file=output)
    if not devices:
        print("NI eszközök: 0", file=output)
        print("HIBA – A DAQmx driver nem adott vissza helyi eszközt.", file=output)
        return 3

    print(f"NI eszközök: {len(devices)}", file=output)
    for device in devices:
        name = str(getattr(device, "name", "?") or "?")
        product = str(getattr(device, "product_type", "?") or "?")
        serial = str(getattr(device, "serial_num", "?") or "?")
        print(f"- {name}: {product}; sorozatszám={serial}", file=output)
    return 0
