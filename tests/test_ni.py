from dataclasses import dataclass, field
from pathlib import Path

import pytest

from eor_control.diagnostics import DiagnosticCategory, DiagnosticLogger
from eor_control.ni import AnalogValveActuator, NidaqConfig, NidaqmxDataAcquisition


@dataclass
class FakeBackend:
    inputs: dict[str, float] = field(default_factory=dict)
    writes: list[tuple[str, float]] = field(default_factory=list)

    def read_voltage(self, physical_channel: str) -> float:
        return self.inputs[physical_channel]

    def write_voltage(self, physical_channel: str, voltage: float) -> None:
        self.writes.append((physical_channel, voltage))


def config() -> NidaqConfig:
    return NidaqConfig("Dev1/ai0", "Dev1/ai1", "Dev1/ao0", safe_output_voltage=1.0)


def test_inputs_use_configured_physical_channels() -> None:
    backend = FakeBackend({"Dev1/ai0": 2.0, "Dev1/ai1": 1.5})
    daq = NidaqmxDataAcquisition(backend, config())

    assert daq.read_voltage("line_pressure") == 2.0
    assert daq.read_voltage("differential_pressure") == 1.5


def test_output_requires_exact_confirmation_and_range() -> None:
    backend = FakeBackend()
    daq = NidaqmxDataAcquisition(backend, config())

    with pytest.raises(PermissionError, match="explicit operator"):
        daq.write_voltage("valve_output", 2.0)
    with pytest.raises(PermissionError, match="did not match"):
        daq.authorize_output("yes")
    daq.authorize_output(NidaqmxDataAcquisition.HARDWARE_CONFIRMATION)
    with pytest.raises(ValueError, match="outside"):
        daq.write_voltage("valve_output", 5.1)

    daq.write_voltage("valve_output", 2.0)
    assert backend.writes == [("Dev1/ao0", 2.0)]


def test_safe_state_writes_configured_voltage_then_revokes_authorization() -> None:
    backend = FakeBackend()
    daq = NidaqmxDataAcquisition(backend, config())
    daq.authorize_output(NidaqmxDataAcquisition.HARDWARE_CONFIRMATION)

    daq.set_safe_state()

    assert backend.writes == [("Dev1/ao0", 1.0)]
    with pytest.raises(PermissionError):
        daq.write_voltage("valve_output", 2.0)


def test_safe_state_does_not_write_before_hardware_authorization() -> None:
    backend = FakeBackend()
    NidaqmxDataAcquisition(backend, config()).set_safe_state()

    assert backend.writes == []


def test_valve_calibration_maps_percent_to_voltage_in_both_directions() -> None:
    backend = FakeBackend()
    daq = NidaqmxDataAcquisition(backend, config())
    daq.authorize_output(NidaqmxDataAcquisition.HARDWARE_CONFIRMATION)
    direct = AnalogValveActuator(
        daq, voltage_at_zero_percent=1.0, voltage_at_hundred_percent=5.0
    )
    direct.write_percent(25.0)

    reverse = AnalogValveActuator(
        daq, voltage_at_zero_percent=5.0, voltage_at_hundred_percent=1.0
    )
    reverse.write_percent(25.0)

    assert backend.writes == [("Dev1/ao0", 2.0), ("Dev1/ao0", 4.0)]


def test_invalid_channel_and_voltage_configuration_is_rejected() -> None:
    with pytest.raises(ValueError, match="distinct"):
        NidaqConfig("Dev1/ai0", "Dev1/ai0", "Dev1/ao0", 1.0)
    with pytest.raises(ValueError, match="safe voltage"):
        NidaqConfig("Dev1/ai0", "Dev1/ai1", "Dev1/ao0", 0.0)


def test_ni_operations_are_logged_by_physical_function(tmp_path: Path) -> None:
    logger = DiagnosticLogger(tmp_path / "communication.log")
    logger.configure(
        enabled=True,
        categories=[DiagnosticCategory.NI_LINE, DiagnosticCategory.NI_VALVE],
    )
    backend = FakeBackend({"Dev1/ai0": 2.0})
    daq = NidaqmxDataAcquisition(backend, config(), logger)
    daq.authorize_output(NidaqmxDataAcquisition.HARDWARE_CONFIRMATION)

    daq.read_voltage("line_pressure")
    daq.write_voltage("valve_output", 2.5)

    categories = [event.category for event in logger.events_after(0)]
    assert categories == [DiagnosticCategory.NI_LINE, DiagnosticCategory.NI_VALVE]
