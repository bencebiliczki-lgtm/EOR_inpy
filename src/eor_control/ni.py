import importlib
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import count
from math import isfinite
from threading import Lock, RLock
from time import monotonic, sleep
from typing import Any, Protocol, cast

from eor_control.diagnostics import DiagnosticCategory, DiagnosticLogger
from eor_control.domain import AnalogPressureReading


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
    _task_ids = count(1)

    def __init__(
        self,
        terminal_configuration: str = "DEFAULT",
        sample_rate_hz: float = 1000.0,
    ) -> None:
        normalized = terminal_configuration.strip().upper()
        if normalized not in self.TERMINAL_CONFIGURATIONS:
            raise ValueError("unsupported NI terminal configuration")
        if not isfinite(sample_rate_hz) or not 1.0 <= sample_rate_hz <= 100_000.0:
            raise ValueError("NI sample rate must be between 1 and 100000 Hz")
        self._terminal_configuration = normalized
        self._sample_rate_hz = sample_rate_hz
        self._output_task: object | None = None
        self._output_channel: str | None = None
        self._task_lock = RLock()

    @classmethod
    def _task_name(cls, purpose: str) -> str:
        return f"eor_{purpose}_{next(cls._task_ids)}"

    def read_voltage(self, physical_channel: str) -> float:
        return self.read_channel_voltages((physical_channel,), 1)[physical_channel][0]

    def read_voltages(
        self, physical_channel: str, number_of_samples: int
    ) -> list[float]:
        return self.read_channel_voltages(
            (physical_channel,), number_of_samples
        )[physical_channel]

    def read_channel_voltages(
        self,
        physical_channels: Sequence[str],
        number_of_samples: int,
        *,
        timeout_seconds: float = 0.1,
    ) -> dict[str, list[float]]:
        if number_of_samples < 1:
            raise ValueError("NI sample count must be positive")
        channels = tuple(physical_channels)
        if not channels or any(not channel.strip() for channel in channels):
            raise ValueError("NI input channel list must not be empty")
        if len(set(channels)) != len(channels):
            raise ValueError("NI input channels must be distinct")
        with self._task_lock:
            nidaqmx = importlib.import_module("nidaqmx")
            constants = importlib.import_module("nidaqmx.constants")
            terminal_configuration = getattr(
                constants.TerminalConfiguration,
                self._terminal_configuration,
            )
            task_name = self._task_name("pressure_inputs_ai")
            with nidaqmx.Task(task_name) as task:
                for physical_channel in channels:
                    task.ai_channels.add_ai_voltage_chan(
                        physical_channel,
                        terminal_config=terminal_configuration,
                    )
                if number_of_samples > 1:
                    task.timing.cfg_samp_clk_timing(
                        self._sample_rate_hz,
                        sample_mode=constants.AcquisitionType.FINITE,
                        samps_per_chan=number_of_samples,
                    )
                    raw_values = task.read(
                        number_of_samples_per_channel=number_of_samples,
                        timeout=timeout_seconds,
                    )
                else:
                    raw_values = task.read(timeout=timeout_seconds)
        return self._split_channel_values(channels, raw_values, number_of_samples)

    @staticmethod
    def _split_channel_values(
        channels: tuple[str, ...], raw_values: object, number_of_samples: int
    ) -> dict[str, list[float]]:
        if len(channels) == 1:
            values = (
                cast(Sequence[object], raw_values)
                if number_of_samples > 1
                else (raw_values,)
            )
            return {channels[0]: [float(cast(Any, value)) for value in values]}
        rows = cast(Sequence[object], raw_values)
        if len(rows) != len(channels):
            raise ValueError(
                f"NI returned {len(rows)} channel rows; expected {len(channels)}"
            )
        result: dict[str, list[float]] = {}
        for channel, row in zip(channels, rows, strict=True):
            samples = cast(Sequence[object], row) if number_of_samples > 1 else (row,)
            if len(samples) != number_of_samples:
                raise ValueError(
                    f"NI channel {channel} returned {len(samples)} samples; "
                    f"expected {number_of_samples}"
                )
            result[channel] = [float(cast(Any, value)) for value in samples]
        return result

    def write_voltage(self, physical_channel: str, voltage: float) -> None:
        with self._task_lock:
            if self._output_task is None:
                nidaqmx = importlib.import_module("nidaqmx")
                task = nidaqmx.Task(self._task_name("valve_ao"))
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
        with self._task_lock:
            task = self._output_task
            self._output_task = None
            self._output_channel = None
            if task is not None:
                cast(Any, task).close()

    @property
    def active_task_names(self) -> tuple[str, ...]:
        with self._task_lock:
            if self._output_task is None:
                return ()
            name = getattr(self._output_task, "name", None)
            return (str(name or "eor_valve_ao"),)


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
    RESOURCE_RESERVED_ERROR_CODES = frozenset({-50103})

    def __init__(
        self,
        backend: NidaqBackend,
        config: NidaqConfig,
        diagnostics: DiagnosticLogger | None = None,
        diagnostic_interval_seconds: float = 5.0,
    ) -> None:
        if not isfinite(diagnostic_interval_seconds) or diagnostic_interval_seconds <= 0.0:
            raise ValueError("NI diagnostic interval must be positive and finite")
        self._backend = backend
        self._config = config
        self._output_authorized = False
        self._diagnostics = diagnostics
        self._diagnostic_interval_seconds = diagnostic_interval_seconds
        self._last_input_log_at: dict[str, float] = {}
        self._last_pressure_quality: dict[str, str] = {}
        self._activation_lock = Lock()
        self._activated = False
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

    @property
    def terminal_configuration(self) -> str:
        return str(getattr(self._backend, "_terminal_configuration", "DEFAULT"))

    def physical_channel(self, channel: str) -> str | None:
        return self._channels.get(channel)

    @property
    def safe_output_voltage(self) -> float:
        return self._config.safe_output_voltage

    def read_voltage(self, channel: str) -> float:
        return self.read_voltages(channel, 1)[0]

    def read_pressure_voltages(
        self, number_of_samples: int
    ) -> dict[str, list[float]]:
        if number_of_samples < 1:
            raise ValueError("NI sample count must be positive")
        logical_channels = tuple(self._channels)
        physical_channels = tuple(self._channels[channel] for channel in logical_channels)
        read_channels = getattr(self._backend, "read_channel_voltages", None)
        if callable(read_channels):
            physical_values = read_channels(physical_channels, number_of_samples)
            values = {
                logical: list(physical_values[physical])
                for logical, physical in zip(
                    logical_channels, physical_channels, strict=True
                )
            }
        else:
            values = {
                channel: self.read_voltages(channel, number_of_samples)
                for channel in logical_channels
            }
        for channel, samples in values.items():
            if len(samples) != number_of_samples:
                raise ValueError(
                    f"NI channel {channel} returned {len(samples)} samples; "
                    f"expected {number_of_samples}"
                )
            self._log_input_samples(channel, samples)
        return values

    def activate(
        self, *, samples_per_read: int = 1, timeout_seconds: float = 0.1
    ) -> None:
        """Validate both AI channels, then create AO and apply SAFE once."""
        if samples_per_read < 1:
            raise ValueError("NI activation sample count must be positive")
        with self._activation_lock:
            if self._activated:
                return
            channels = tuple(self._channels.values())
            terminal = self.terminal_configuration
            sample_rate = getattr(self._backend, "_sample_rate_hz", "UNKNOWN")
            self._log(
                DiagnosticCategory.NI_LINE,
                "NI_ACTIVATION",
                "NI activation started; "
                f"AI channels={','.join(channels) or 'NONE'}; terminal={terminal}; "
                f"sample_rate={sample_rate}; samples_per_read={samples_per_read}",
            )
            for attempt in range(1, 4):
                self._cleanup_backend()
                task_name = "eor_pressure_inputs_ai"
                self._log(
                    DiagnosticCategory.NI_LINE,
                    "NI_ACTIVATION",
                    f"activation attempt={attempt}; AI task name={task_name}; "
                    f"AI channel list={','.join(channels) or 'NONE'}",
                )
                try:
                    if channels:
                        read_channels = getattr(
                            self._backend, "read_channel_voltages", None
                        )
                        if callable(read_channels):
                            read_channels(
                                channels,
                                samples_per_read,
                                timeout_seconds=timeout_seconds,
                            )
                        else:
                            self.read_pressure_voltages(samples_per_read)
                        self._log(
                            DiagnosticCategory.NI_LINE,
                            "NI_ACTIVATION",
                            "AI validation completed",
                        )
                    if self._config.valve_output_channel is not None:
                        if not self._output_authorized:
                            raise PermissionError(
                                "NI physical output requires explicit operator confirmation"
                            )
                        self._backend.write_voltage(
                            self._config.valve_output_channel,
                            self._config.safe_output_voltage,
                        )
                        self._log(
                            DiagnosticCategory.NI_VALVE,
                            "NI_ACTIVATION",
                            "AO task created; safe voltage applied; "
                            f"channel={self._config.valve_output_channel}; "
                            f"voltage={self._config.safe_output_voltage:.6f} V",
                        )
                except Exception as error:
                    cleanup_result = self._cleanup_backend()
                    if not self._is_resource_reserved(error) or attempt == 3:
                        self._log(
                            DiagnosticCategory.NI_LINE,
                            "NI_ACTIVATION",
                            f"activation failed; task={task_name}; attempt={attempt}; "
                            f"active known tasks={self._active_task_names()}; "
                            f"cleanup result={cleanup_result}; error={error}",
                        )
                        raise
                    self._log(
                        DiagnosticCategory.NI_LINE,
                        "NI_RESOURCE_RESERVED",
                        f"NI resource reserved; failing task={task_name}; "
                        f"active known tasks={self._active_task_names()}; "
                        f"cleanup result={cleanup_result}; retry number={attempt + 1}",
                    )
                    sleep(0.25 * attempt)
                    continue
                self._activated = True
                self._log(
                    DiagnosticCategory.NI_LINE,
                    "NI_ACTIVATION",
                    "NI activation completed",
                )
                return

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
        self._log_input_samples(channel, voltages)
        return voltages

    def _log_input_samples(self, channel: str, voltages: list[float]) -> None:
        physical_channel = self._channels[channel]
        categories = {
            "line_pressure": DiagnosticCategory.NI_LINE,
            "differential_pressure": DiagnosticCategory.NI_DIFFERENTIAL,
        }
        category = categories[channel]
        now = monotonic()
        if now - self._last_input_log_at.get(channel, float("-inf")) >= (
            self._diagnostic_interval_seconds
        ):
            self._last_input_log_at[channel] = now
            self._log(
                category,
                "RX",
                f"{physical_channel} samples={len(voltages)} "
                f"min={min(voltages):.6f} V max={max(voltages):.6f} V",
            )

    def log_pressure_reading(
        self, channel: str, reading: AnalogPressureReading
    ) -> None:
        quality = reading.quality.value
        quality_changed = self._last_pressure_quality.get(channel) != quality
        now = monotonic()
        periodic = now - self._last_input_log_at.get(
            f"processed:{channel}", float("-inf")
        ) >= self._diagnostic_interval_seconds
        if not quality_changed and not periodic:
            return
        self._last_pressure_quality[channel] = quality
        self._last_input_log_at[f"processed:{channel}"] = now
        category = (
            DiagnosticCategory.NI_LINE
            if channel == "line_pressure"
            else DiagnosticCategory.NI_DIFFERENTIAL
        )
        self._log(
            category,
            "PRESSURE",
            f"channel={reading.physical_channel}; "
            f"terminal={reading.terminal_configuration}; "
            f"last={reading.last_raw_voltage}; median={reading.median_voltage}; "
            f"filtered={reading.filtered_voltage}; "
            f"raw_pressure={reading.raw_pressure_bar}; "
            f"pressure={reading.filtered_pressure_bar}; "
            f"age={reading.sample_age_seconds:.6f}s; quality={quality}; "
            f"reason={reading.quality_reason}",
        )

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

    def close(self) -> None:
        with self._activation_lock:
            try:
                self.set_safe_state()
            finally:
                self._activated = False
                self._output_authorized = False
                self._cleanup_backend()

    def _cleanup_backend(self) -> str:
        close_output = getattr(self._backend, "close_output", None)
        if not callable(close_output):
            return "no persistent task"
        try:
            close_output()
        except Exception as error:
            return f"failed: {type(error).__name__}: {error}"
        return "completed"

    def _active_task_names(self) -> tuple[str, ...]:
        return tuple(getattr(self._backend, "active_task_names", ()))

    @classmethod
    def _is_resource_reserved(cls, error: Exception) -> bool:
        return getattr(error, "error_code", None) in cls.RESOURCE_RESERVED_ERROR_CODES

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
        self._last_voltage: float | None = None

    def write_percent(self, output_percent: float) -> None:
        if not isfinite(output_percent) or not 0.0 <= output_percent <= 100.0:
            raise ValueError("valve output must be a finite percentage from 0 to 100")
        voltage = self._zero + (self._hundred - self._zero) * output_percent / 100.0
        self._daq.write_voltage("valve_output", voltage)
        self._last_voltage = voltage

    def set_safe_state(self) -> None:
        self._daq.set_safe_state()
        if self._daq.output_authorized:
            self._last_voltage = self._daq.safe_output_voltage

    @property
    def last_voltage(self) -> float | None:
        return self._last_voltage

    @property
    def safe_output_percent(self) -> float:
        return min(
            100.0,
            max(
                0.0,
                (self._daq.safe_output_voltage - self._zero)
                / (self._hundred - self._zero)
                * 100.0,
            ),
        )
