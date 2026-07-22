from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from eor_control import ni
from eor_control.diagnostics import DiagnosticCategory, DiagnosticLogger
from eor_control.ni import (
    AnalogValveActuator,
    NidaqConfig,
    NidaqmxBackend,
    NidaqmxDataAcquisition,
)


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


def test_input_burst_is_read_in_one_backend_operation() -> None:
    class BurstBackend(FakeBackend):
        reads: list[tuple[str, int]] = []

        def read_voltages(
            self, physical_channel: str, number_of_samples: int
        ) -> list[float]:
            self.reads.append((physical_channel, number_of_samples))
            return [2.0, 2.01, 4.8, 1.99, 2.02]

    backend = BurstBackend()
    daq = NidaqmxDataAcquisition(backend, config())

    values = daq.read_voltages("line_pressure", 5)

    assert values == [2.0, 2.01, 4.8, 1.99, 2.02]
    assert backend.reads == [("Dev1/ai0", 5)]


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


def test_physical_backend_reuses_one_persistent_ao_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks: list[object] = []

    class FakeTask:
        def __init__(self) -> None:
            self.ao_channels = SimpleNamespace(
                add_ao_voltage_chan=lambda channel: setattr(self, "channel", channel)
            )
            self.writes: list[float] = []
            self.closed = False
            tasks.append(self)

        def write(self, voltage: float, *, auto_start: bool) -> None:
            assert auto_start
            self.writes.append(voltage)

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(
        ni.importlib,
        "import_module",
        lambda name: SimpleNamespace(Task=FakeTask),
    )
    backend = NidaqmxBackend()

    backend.write_voltage("Dev1/ao0", 1.0)
    backend.write_voltage("Dev1/ao0", 2.0)
    backend.close_output()

    assert len(tasks) == 1
    task = tasks[0]
    assert isinstance(task, FakeTask)
    assert task.writes == [1.0, 2.0]
    assert task.closed


def test_physical_backend_uses_finite_timed_burst_for_multiple_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timing_calls: list[tuple[float, object, int]] = []

    class FakeTask:
        def __init__(self) -> None:
            self.ai_channels = SimpleNamespace(
                add_ai_voltage_chan=lambda channel, terminal_config: None
            )
            self.timing = SimpleNamespace(
                cfg_samp_clk_timing=lambda rate, sample_mode, samps_per_chan: (
                    timing_calls.append((rate, sample_mode, samps_per_chan))
                )
            )

        def __enter__(self) -> "FakeTask":
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def read(self, *, number_of_samples_per_channel: int, timeout: float) -> list[float]:
            assert number_of_samples_per_channel == 20
            assert timeout == pytest.approx(0.1)
            return [2.0] * number_of_samples_per_channel

    def import_module(name: str) -> object:
        if name == "nidaqmx.constants":
            return SimpleNamespace(
                TerminalConfiguration=SimpleNamespace(DEFAULT="default"),
                AcquisitionType=SimpleNamespace(FINITE="finite"),
            )
        return SimpleNamespace(Task=FakeTask)

    monkeypatch.setattr(ni.importlib, "import_module", import_module)

    values = NidaqmxBackend().read_voltages("Dev1/ai0", 20)

    assert values == [2.0] * 20
    assert timing_calls == [(1000.0, "finite", 20)]
