import importlib
from dataclasses import dataclass
from math import isfinite
from threading import Lock
from typing import Any, Protocol, cast

from eor_control.diagnostics import DiagnosticCategory, DiagnosticLogger


class NidaqBackend(Protocol):
    def read_voltage(self, physical_channel: str) -> float: ...

    def read_voltages(
        self, physical_channel: str, number_of_samples: int
    ) -> list[float]: ...

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
        self._output_task: object | None = None
        self._output_channel: str | None = None
        self._output_lock = Lock()

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

    def read_voltages(
        self, physical_channel: str, number_of_samples: int
    ) -> list[float]:
        if number_of_samples < 1:
            raise ValueError("NI sample count must be positive")
        nidaqmx = importlib.import_module("nidaqmx")
        constants = importlib.import_module("nidaqmx.constants")
        terminal_configuration = getattr(
            constants.TerminalConfiguration,
            self._terminal_configuration,
        )
        with nidaqmx.Task() as task:
            task.ai_channels.add_ai_voltage_chan(
                physical_channel,
                terminal_config=terminal_configuration,
            )
            task.timing.cfg_samp_clk_timing(
                1000.0,
                sample_mode=constants.AcquisitionType.FINITE,
                samps_per_chan=number_of_samples,
            )
            values = task.read(
                number_of_samples_per_channel=number_of_samples,
                timeout=0.1,
            )
        return [float(value) for value in cast(list[float], values)]

    def write_voltage(self, physical_channel: str, voltage: float) -> None:
        with self._output_lock:
            if self._output_task is None:
                nidaqmx = importlib.import_module("nidaqmx")
                task = nidaqmx.Task()
                try:
                    task.ao_channels.add_ao_voltage_chan(physical_channel)
                except Exception:
                    task.close()
                    raise
                self._output_task = task
                self._output_channel = physical_channel
            elif self._output_channel != physical_channel:
                raise RuntimeError("persistent NI AO task is bound to another channel")
            cast(Any, self._output_task).write(voltage, auto_start=True)

    def close_output(self) -> None:
        with self._output_lock:
            task = self._output_task
            self._output_task = None
            self._output_channel = None
            if task is not None:
                cast(Any, task).close()


@dataclass(frozen=True, slots=True)
class NidaqConfig:
    line_pressure_channel: str | None
    differential_pressure_channel: str | None
    valve_output_channel: str | None
    safe_output_voltage: float
    output_min_voltage: float = 1.0
    output_max_voltage: float = 5.0

    def __post_init__(self) -> None:
        channels = (
            self.line_pressure_channel,
            self.differential_pressure_channel,
            self.valve_output_channel,
        )
        configured_channels = tuple(
            channel.strip() for channel in channels if channel is not None
        )
        if any(not channel for channel in configured_channels):
            raise ValueError("configured NI physical channel names must not be empty")
        if len(set(configured_channels)) != len(configured_channels):
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
            key: channel
            for key, channel in (
                ("line_pressure", config.line_pressure_channel),
                ("differential_pressure", config.differential_pressure_channel),
            )
            if channel is not None
        }

    def authorize_output(self, confirmation: str) -> None:
        if confirmation != self.HARDWARE_CONFIRMATION:
            raise PermissionError("NI physical output confirmation did not match")
        self._output_authorized = True

    @property
    def output_authorized(self) -> bool:
        return self._output_authorized

    @property
    def physical_output_required(self) -> bool:
        return self._config.valve_output_channel is not None

    def read_voltage(self, channel: str) -> float:
        return self.read_voltages(channel, 1)[0]

    def read_voltages(self, channel: str, number_of_samples: int) -> list[float]:
        if number_of_samples < 1:
            raise ValueError("NI sample count must be positive")
        try:
            physical_channel = self._channels[channel]
        except KeyError as error:
            raise KeyError(f"unknown NI logical input channel: {channel}") from error
        read_many = getattr(self._backend, "read_voltages", None)
        voltages = (
            [self._backend.read_voltage(physical_channel)]
            if number_of_samples == 1
            else read_many(physical_channel, number_of_samples)
            if callable(read_many)
            else [
                self._backend.read_voltage(physical_channel)
                for _ in range(number_of_samples)
            ]
        )
        if len(voltages) != number_of_samples:
            raise ValueError(
                f"NI channel {channel} returned {len(voltages)} samples; "
                f"expected {number_of_samples}"
            )
        if not all(isfinite(voltage) for voltage in voltages):
            raise ValueError(f"NI channel {channel} returned a non-finite voltage")
        categories = {
            "line_pressure": DiagnosticCategory.NI_LINE,
            "differential_pressure": DiagnosticCategory.NI_DIFFERENTIAL,
        }
        category = categories[channel]
        self._log(
            category,
            "RX",
            f"{physical_channel} samples={len(voltages)} "
            f"min={min(voltages):.6f} V max={max(voltages):.6f} V",
        )
        return voltages

    def write_voltage(self, channel: str, voltage: float) -> None:
        if channel != "valve_output":
            raise KeyError(f"unknown NI logical output channel: {channel}")
        if not self._output_authorized:
            raise PermissionError("NI physical output requires explicit operator confirmation")
        if self._config.valve_output_channel is None:
            raise ConnectionError("NI valve output is not configured")
        self._validate_output(voltage)
        self._backend.write_voltage(self._config.valve_output_channel, voltage)
        self._log(
            DiagnosticCategory.NI_VALVE,
            "TX",
            f"{self._config.valve_output_channel}={voltage:.6f} V",
        )

    def set_safe_state(self) -> None:
        try:
            if self._output_authorized:
                if self._config.valve_output_channel is None:
                    return
                self._backend.write_voltage(
                    self._config.valve_output_channel, self._config.safe_output_voltage
                )
                self._log(
                    DiagnosticCategory.NI_VALVE,
                    "SAFE",
                    f"{self._config.valve_output_channel}={self._config.safe_output_voltage:.6f} V",
                )
        finally:
            self._output_authorized = False
            close_output = getattr(self._backend, "close_output", None)
            if callable(close_output):
                close_output()

    def close(self) -> None:
        self.set_safe_state()

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
