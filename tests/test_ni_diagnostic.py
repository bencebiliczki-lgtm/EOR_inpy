import importlib
import io
from types import SimpleNamespace

import pytest

from eor_control import ni_diagnostic


def test_ni_diagnostic_lists_driver_and_devices(monkeypatch: pytest.MonkeyPatch) -> None:
    numpy = SimpleNamespace(__version__="1.26.4")
    device = SimpleNamespace(name="Dev2", product_type="NI USB-6001", serial_num=1234)
    system = SimpleNamespace(
        driver_version=SimpleNamespace(
            major_version=24,
            minor_version=5,
            update_version=0,
        ),
        devices=[device],
    )

    def import_module(name: str) -> object:
        if name == "numpy":
            return numpy
        if name == "nidaqmx.system":
            return SimpleNamespace(System=SimpleNamespace(local=lambda: system))
        raise AssertionError(name)

    monkeypatch.setattr(importlib, "import_module", import_module)
    monkeypatch.setattr(ni_diagnostic, "_package_version", lambda name: "1.26.4")
    output = io.StringIO()

    assert ni_diagnostic.run_ni_diagnostic(output) == 0
    result = output.getvalue()
    assert "NumPy import: OK (1.26.4)" in result
    assert "NI-DAQmx driver: 24.5.0" in result
    assert "Dev2: NI USB-6001; sorozatszám=1234" in result


def test_ni_diagnostic_reports_import_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def import_module(name: str) -> object:
        raise RuntimeError("X86_V2 nem támogatott")

    monkeypatch.setattr(importlib, "import_module", import_module)
    output = io.StringIO()

    assert ni_diagnostic.run_ni_diagnostic(output) == 1
    assert "HIBA – NumPy import: RuntimeError: X86_V2 nem támogatott" in output.getvalue()
