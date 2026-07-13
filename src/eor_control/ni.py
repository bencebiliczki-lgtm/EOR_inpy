import importlib
from dataclasses import dataclass
from math import isfinite
from typing import Protocol

from eor_control.diagnostics import DiagnosticCategory, DiagnosticLogger


class NidaqBackend(Protocol):
    def read_voltage(self, physical_channel: str) -> float: ...

    def write_voltage(self, physical_channel: str, voltage: float) -> None: ...


class NidaqmxBackend:
    """Thin NI-DAQmx task wrapper; importing it never performs physical I/O."""

    TERMINAL_CONFIGURATIONS = {
        "DEFAULT",
        "RSE",
        "NRSE",
        "DIFFERENTIAL",
        "PSEUDODIFFERENTIAL",
    }

    def __init__(self, terminal_configuration: str = "DEFAULT") -> None:
        normalized = terminal_configuration.strip().upper()
        if normalized not in self.TERMINAL_CONFIGURATIONS:
            raise ValueError("unsupported NI terminal configuration")
        self._terminal_configuration = normalized

    def read_voltage(self, physical_channel: str) -> float:
        nidaqmx = importlib.import_module("nidaqmx")

        terminal_configuration = getattr(
            importlib.import_module("nidaqmx.constants").TerminalConfiguration,
            self._terminal_configuration,
        )

        with nidaqmx.Task() as task:
            task.ai_channels.add_ai_voltage_chan(
                physical_channel,
                terminal_config=terminal_configuration,
            )
            return float(task.read())

    def write_voltage(self, physical_channel: str, voltage: float) -> None:
        nidaqmx = importlib.import_module("nidaqmx")

        with nidaqmx.Task() as task:
            task.ao_channels.add_ao_voltage_chan(physical_channel)
            task.write(voltage, auto_start=True)


@dataclass(frozen=True, slots=True)
class NidaqConfig:
    line_pressure_channel: str
    differential_pressure_channel: str
    valve_output_channel: str
    safe_output_voltage: float
    output_min_voltage: float = 1.0
    output_max_voltage: float = 5.0
    inlet_pressure_channel: str = "Dev1/ai2"

    def __post_init__(self) -> None:
        channels = (
            self.line_pressure_channel,
            self.differential_pressure_channel,
            self.inlet_pressure_channel,
            self.valve_output_channel,
        )
        if any(not channel.strip() for channel in channels):
            raise ValueError("NI physical channel names must not be empty")
        if len(set(channels)) != len(channels):
            raise ValueError("NI input and output channels must be distinct")
        voltages = (
            self.safe_output_voltage,
            self.output_min_voltage,
            self.output_max_voltage,
        )
        if not all(isfinite(value) for value in voltages):
            raise ValueError("NI output voltages must be finite")
        if not self.output_min_voltage < self.output_max_voltage:
            raise ValueError("NI output voltage range must be ordered")
        if not self.output_min_voltage <= self.safe_output_voltage <= self.output_max_voltage:
            raise ValueError("NI safe voltage must be inside the configured output range")


class NidaqmxDataAcquisition:
    HARDWARE_CONFIRMATION = "ENABLE NI PHYSICAL OUTPUT"

    def __init__(
        self,
        backend: NidaqBackend,
        config: NidaqConfig,
        diagnostics: DiagnosticLogger | None = None,
    ) -> None:
        self._backend = backend
        self._config = config
        self._output_authorized = False
        self._diagnostics = diagnostics
        self._channels = {
            "line_pressure": config.line_pressure_channel,
            "differential_pressure": config.differential_pressure_channel,
            "inlet_pressure": config.inlet_pressure_channel,
        }

    def authorize_output(self, confirmation: str) -> None:
        if confirmation != self.HARDWARE_CONFIRMATION:
            raise PermissionError("NI physical output confirmation did not match")
        self._output_authorized = True

    def read_voltage(self, channel: str) -> float:
        try:
            physical_channel = self._channels[channel]
        except KeyError as error:
            raise KeyError(f"unknown NI logical input channel: {channel}") from error
        voltage = self._backend.read_voltage(physical_channel)
        if not isfinite(voltage):
            raise ValueError(f"NI channel {channel} returned a non-finite voltage")
        categories = {
            "line_pressure": DiagnosticCategory.NI_LINE,
            "differential_pressure": DiagnosticCategory.NI_DIFFERENTIAL,
            "inlet_pressure": DiagnosticCategory.NI_INLET,
        }
        category = categories[channel]
        self._log(category, "RX", f"{physical_channel}={voltage:.6f} V")
        return voltage

    def write_voltage(self, channel: str, voltage: float) -> None:
        if channel != "valve_output":
            raise KeyError(f"unknown NI logical output channel: {channel}")
        if not self._output_authorized:
            raise PermissionError("NI physical output requires explicit operator confirmation")
        self._validate_output(voltage)
        self._backend.write_voltage(self._config.valve_output_channel, voltage)
        self._log(
            DiagnosticCategory.NI_VALVE,
            "TX",
            f"{self._config.valve_output_channel}={voltage:.6f} V",
        )

    def set_safe_state(self) -> None:
        if self._output_authorized:
            self._backend.write_voltage(
                self._config.valve_output_channel, self._config.safe_output_voltage
            )
            self._log(
                DiagnosticCategory.NI_VALVE,
                "SAFE",
                f"{self._config.valve_output_channel}={self._config.safe_output_voltage:.6f} V",
            )
        self._output_authorized = False

    def _validate_output(self, voltage: float) -> None:
        if not isfinite(voltage):
            raise ValueError("NI output voltage must be finite")
        if not self._config.output_min_voltage <= voltage <= self._config.output_max_voltage:
            raise ValueError("NI output voltage is outside the configured range")

    def _log(
        self, category: DiagnosticCategory, direction: str, message: str
    ) -> None:
        if self._diagnostics is not None:
            self._diagnostics.emit(category, direction, message)


class AnalogValveActuator:
    def __init__(
        self,
        daq: NidaqmxDataAcquisition,
        *,
        voltage_at_zero_percent: float,
        voltage_at_hundred_percent: float,
    ) -> None:
        if not all(
            isfinite(value)
            for value in (voltage_at_zero_percent, voltage_at_hundred_percent)
        ):
            raise ValueError("valve calibration voltages must be finite")
        if voltage_at_zero_percent == voltage_at_hundred_percent:
            raise ValueError("valve calibration endpoints must differ")
        self._daq = daq
        self._zero = voltage_at_zero_percent
        self._hundred = voltage_at_hundred_percent

    def write_percent(self, output_percent: float) -> None:
        if not isfinite(output_percent) or not 0.0 <= output_percent <= 100.0:
            raise ValueError("valve output must be a finite percentage from 0 to 100")
        voltage = self._zero + (self._hundred - self._zero) * output_percent / 100.0
        self._daq.write_voltage("valve_output", voltage)

    def set_safe_state(self) -> None:
        self._daq.set_safe_state()
