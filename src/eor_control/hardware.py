import importlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import StrEnum
from hashlib import sha256
from math import isfinite
from typing import Protocol

from eor_control.diagnostics import DiagnosticCategory, DiagnosticLogger
from eor_control.isco import IscoSerialConfig, open_isco_pump
from eor_control.ni import NidaqBackend, NidaqConfig, NidaqmxBackend
from eor_control.signal_filter import AnalogFilterConfig, AnalogSignalFilter


@dataclass(frozen=True, slots=True)
class SerialPortInfo:
    device: str
    description: str = ""
    manufacturer: str = ""
    product: str = ""
    hardware_id: str = ""

    @property
    def display_name(self) -> str:
        description = self.description.strip()
        if description and description.lower() != "n/a":
            if description.casefold().endswith(f"({self.device})".casefold()):
                return description
            return f"{description} ({self.device})"
        return f"Soros csatlakozó ({self.device})"

    @property
    def tooltip(self) -> str:
        details = [
            value
            for value in (self.manufacturer, self.product, self.hardware_id)
            if value.strip() and value.strip().lower() != "n/a"
        ]
        return "\n".join(details) or self.display_name


@dataclass(frozen=True, slots=True)
class NiPhysicalChannelInfo:
    channel: str
    device_name: str
    product_type: str = ""
    serial_number: str = ""

    @property
    def display_name(self) -> str:
        physical_name = self.channel.rsplit("/", 1)[-1]
        normalized = physical_name.lower()
        if normalized.startswith("ai") and normalized[2:].isdigit():
            return f"{int(normalized[2:]) + 1}. analóg bemenet ({physical_name.upper()})"
        if normalized.startswith("ao") and normalized[2:].isdigit():
            return f"{int(normalized[2:]) + 1}. analóg kimenet ({physical_name.upper()})"
        return f"Fizikai csatorna ({physical_name})"

    @property
    def tooltip(self) -> str:
        details = [f"NI eszköz: {self.device_name}"]
        if self.product_type.strip():
            details.append(f"Típus: {self.product_type}")
        if self.serial_number.strip():
            details.append(f"Sorozatszám: {self.serial_number}")
        return "\n".join(details)

    @property
    def device_display_name(self) -> str:
        if self.product_type.strip():
            return f"{self.product_type} ({self.device_name})"
        return self.device_name


@dataclass(frozen=True, slots=True)
class HardwareDiscovery:
    """Read-only inventory of communication ports and NI physical channels."""

    serial_ports: tuple[SerialPortInfo, ...] = ()
    ni_input_channels: tuple[NiPhysicalChannelInfo, ...] = ()
    ni_output_channels: tuple[NiPhysicalChannelInfo, ...] = ()
    warnings: tuple[str, ...] = ()


def discover_hardware() -> HardwareDiscovery:
    """List locally visible hardware without opening a port or creating an NI task."""

    serial_ports: tuple[SerialPortInfo, ...] = ()
    ni_inputs: tuple[NiPhysicalChannelInfo, ...] = ()
    ni_outputs: tuple[NiPhysicalChannelInfo, ...] = ()
    warnings: list[str] = []

    try:
        list_ports = importlib.import_module("serial.tools.list_ports")
        discovered_ports = {
            str(port.device).strip(): SerialPortInfo(
                device=str(port.device).strip(),
                description=str(getattr(port, "description", "") or "").strip(),
                manufacturer=str(getattr(port, "manufacturer", "") or "").strip(),
                product=str(getattr(port, "product", "") or "").strip(),
                hardware_id=str(getattr(port, "hwid", "") or "").strip(),
            )
            for port in list_ports.comports()
            if getattr(port, "device", None)
        }
        serial_ports = tuple(
            discovered_ports[device]
            for device in sorted(discovered_ports, key=str.casefold)
        )
    except Exception as error:
        warnings.append(f"Soros portok felderítése sikertelen: {error}")

    try:
        system_module = importlib.import_module("nidaqmx.system")
        system = system_module.System.local()
        ni_inputs = tuple(
            sorted(
                (
                    NiPhysicalChannelInfo(
                        channel=str(channel.name).strip(),
                        device_name=str(device.name).strip(),
                        product_type=str(getattr(device, "product_type", "") or "").strip(),
                        serial_number=str(getattr(device, "serial_num", "") or "").strip(),
                    )
                    for device in system.devices
                    for channel in device.ai_physical_chans
                    if channel.name
                ),
                key=lambda item: item.channel.casefold(),
            )
        )
        ni_outputs = tuple(
            sorted(
                (
                    NiPhysicalChannelInfo(
                        channel=str(channel.name).strip(),
                        device_name=str(device.name).strip(),
                        product_type=str(getattr(device, "product_type", "") or "").strip(),
                        serial_number=str(getattr(device, "serial_num", "") or "").strip(),
                    )
                    for device in system.devices
                    for channel in device.ao_physical_chans
                    if channel.name
                ),
                key=lambda item: item.channel.casefold(),
            )
        )
    except Exception as error:
        warnings.append(f"NI eszközök felderítése sikertelen: {error}")

    return HardwareDiscovery(serial_ports, ni_inputs, ni_outputs, tuple(warnings))


@dataclass(frozen=True, slots=True)
class HardwareConfiguration:
    jacket_port: str
    jacket_unit_id: int
    jacket_channel: str
    injection_port: str
    injection_unit_id: int
    injection_channel: str
    baud_rate: int
    line_pressure_channel: str
    differential_pressure_channel: str
    valve_output_channel: str
    safe_output_voltage: float
    valve_zero_percent_voltage: float
    valve_hundred_percent_voltage: float
    ni_terminal_configuration: str = "DEFAULT"
    pump_cabling_notes: str = ""
    ni_wiring_notes: str = ""
    supervised_test_minutes: int = 60
    cable_disconnect_test_completed: bool = False
    emergency_stop_test_completed: bool = False
    supervised_test_completed: bool = False
    jacket_pump_enabled: bool = True
    injection_pump_enabled: bool = True
    line_pressure_enabled: bool = True
    differential_pressure_enabled: bool = True
    valve_output_enabled: bool = True
    serial_command_timeout_seconds: float = 2.0
    serial_command_retries: int = 2
    analog_filter_enabled: bool = True
    analog_samples_per_read: int = 20
    analog_sample_rate_hz: float = 1000.0
    line_median_enabled: bool = True
    line_ema_alpha: float = 0.2
    line_spike_rejection_enabled: bool = True
    line_spike_limit_voltage: float = 0.1
    line_spike_confirmation_samples: int = 3
    line_electrical_min_voltage: float = 0.5
    line_electrical_max_voltage: float = 5.5
    line_physical_min_pressure_bar: float = -15.0
    line_physical_max_pressure_bar: float = 420.0
    line_stale_timeout_seconds: float = 1.0
    differential_median_enabled: bool = True
    differential_ema_alpha: float = 0.2
    differential_spike_rejection_enabled: bool = True
    differential_spike_limit_voltage: float = 0.1
    differential_spike_confirmation_samples: int = 3
    differential_electrical_min_voltage: float = 0.5
    differential_electrical_max_voltage: float = 5.5
    differential_physical_min_pressure_bar: float = -5.0
    differential_physical_max_pressure_bar: float = 55.0
    differential_stale_timeout_seconds: float = 1.0
    analog_diagnostic_interval_seconds: float = 5.0

    def __post_init__(self) -> None:
        if (
            self.jacket_pump_enabled
            and self.injection_pump_enabled
            and self.jacket_port.strip().upper() == self.injection_port.strip().upper()
        ):
            raise ValueError("the two ISCO pumps must use different COM ports")
        if self.jacket_pump_enabled:
            IscoSerialConfig(
                self.jacket_port,
                self.jacket_unit_id,
                self.jacket_channel,
                self.baud_rate,
                self.serial_command_timeout_seconds,
                self.serial_command_retries,
            )
        if self.injection_pump_enabled:
            IscoSerialConfig(
                self.injection_port,
                self.injection_unit_id,
                self.injection_channel,
                self.baud_rate,
                self.serial_command_timeout_seconds,
                self.serial_command_retries,
            )
        NidaqConfig(
            self.line_pressure_channel if self.line_pressure_enabled else None,
            (
                self.differential_pressure_channel
                if self.differential_pressure_enabled
                else None
            ),
            self.valve_output_channel if self.valve_output_enabled else None,
            self.safe_output_voltage,
        )
        if self.ni_terminal_configuration not in NidaqmxBackend.TERMINAL_CONFIGURATIONS:
            raise ValueError("unsupported NI terminal configuration")
        self.analog_filter_config()
        if (
            not isfinite(self.analog_diagnostic_interval_seconds)
            or self.analog_diagnostic_interval_seconds <= 0.0
        ):
            raise ValueError("analog diagnostic interval must be positive and finite")
        if not 1 <= self.supervised_test_minutes <= 1440:
            raise ValueError("supervised test duration must be between 1 and 1440 minutes")
        valve_voltages = (
            self.valve_zero_percent_voltage,
            self.valve_hundred_percent_voltage,
        )
        if not all(isfinite(value) and 1.0 <= value <= 5.0 for value in valve_voltages):
            raise ValueError("valve endpoint voltages must be finite and between 1 and 5 V")
        if self.valve_zero_percent_voltage == self.valve_hundred_percent_voltage:
            raise ValueError("valve endpoint voltages must differ")

    def jacket_config(self) -> IscoSerialConfig:
        if not self.jacket_pump_enabled:
            raise ValueError("jacket pump is not enabled")
        return IscoSerialConfig(
            self.jacket_port,
            self.jacket_unit_id,
            self.jacket_channel,
            self.baud_rate,
            self.serial_command_timeout_seconds,
            self.serial_command_retries,
        )

    def injection_config(self) -> IscoSerialConfig:
        if not self.injection_pump_enabled:
            raise ValueError("injection pump is not enabled")
        return IscoSerialConfig(
            self.injection_port,
            self.injection_unit_id,
            self.injection_channel,
            self.baud_rate,
            self.serial_command_timeout_seconds,
            self.serial_command_retries,
        )

    def ni_config(self) -> NidaqConfig:
        return NidaqConfig(
            self.line_pressure_channel if self.line_pressure_enabled else None,
            (
                self.differential_pressure_channel
                if self.differential_pressure_enabled
                else None
            ),
            self.valve_output_channel if self.valve_output_enabled else None,
            self.safe_output_voltage,
        )

    def analog_filter_config(self) -> AnalogFilterConfig:
        return AnalogFilterConfig(
            enabled=self.analog_filter_enabled,
            samples_per_read=self.analog_samples_per_read,
            sample_rate_hz=self.analog_sample_rate_hz,
            ema_alpha=self.line_ema_alpha,
            median_enabled=self.line_median_enabled,
            spike_rejection_enabled=self.line_spike_rejection_enabled,
            line_spike_limit_voltage=self.line_spike_limit_voltage,
            spike_confirmation_samples=self.line_spike_confirmation_samples,
            line_electrical_min_voltage=self.line_electrical_min_voltage,
            line_electrical_max_voltage=self.line_electrical_max_voltage,
            line_physical_min_pressure_bar=self.line_physical_min_pressure_bar,
            line_physical_max_pressure_bar=self.line_physical_max_pressure_bar,
            line_stale_timeout_seconds=self.line_stale_timeout_seconds,
            differential_median_enabled=self.differential_median_enabled,
            differential_ema_alpha=self.differential_ema_alpha,
            differential_spike_rejection_enabled=(
                self.differential_spike_rejection_enabled
            ),
            differential_spike_limit_voltage=self.differential_spike_limit_voltage,
            differential_spike_confirmation_samples=(
                self.differential_spike_confirmation_samples
            ),
            differential_electrical_min_voltage=(
                self.differential_electrical_min_voltage
            ),
            differential_electrical_max_voltage=(
                self.differential_electrical_max_voltage
            ),
            differential_physical_min_pressure_bar=(
                self.differential_physical_min_pressure_bar
            ),
            differential_physical_max_pressure_bar=(
                self.differential_physical_max_pressure_bar
            ),
            differential_stale_timeout_seconds=(
                self.differential_stale_timeout_seconds
            ),
        )

    def enabled_test_devices(self) -> tuple["HardwareTestDevice", ...]:
        enabled = {
            HardwareTestDevice.JACKET_PUMP: self.jacket_pump_enabled,
            HardwareTestDevice.INJECTION_PUMP: self.injection_pump_enabled,
            HardwareTestDevice.LINE_PRESSURE: self.line_pressure_enabled,
            HardwareTestDevice.DIFFERENTIAL_PRESSURE: self.differential_pressure_enabled,
        }
        return tuple(device for device in HardwareTestDevice if enabled[device])

    @property
    def measurement_ready(self) -> bool:
        """Core devices needed for a normal EOR measurement; line pressure is optional."""
        return (
            self.jacket_pump_enabled
            and self.injection_pump_enabled
            and self.differential_pressure_enabled
            and self.valve_output_enabled
        )

    def to_settings(self) -> dict[str, object]:
        return asdict(self)


class HardwareTestDevice(StrEnum):
    JACKET_PUMP = "jacket_pump"
    INJECTION_PUMP = "injection_pump"
    LINE_PRESSURE = "line_pressure"
    DIFFERENTIAL_PRESSURE = "differential_pressure"


@dataclass(frozen=True, slots=True)
class DeviceConnectionResult:
    device: HardwareTestDevice
    successful: bool
    detail: str
    value: float | None = None


@dataclass(frozen=True, slots=True)
class ConnectionTestResult:
    devices: tuple[DeviceConnectionResult, ...]
    required_devices: tuple[HardwareTestDevice, ...] = tuple(HardwareTestDevice)

    @property
    def all_successful(self) -> bool:
        results = {result.device: result for result in self.devices}
        return all(
            device in results and results[device].successful
            for device in self.required_devices
        )

    def for_device(self, device: HardwareTestDevice) -> DeviceConnectionResult | None:
        return next((result for result in self.devices if result.device is device), None)

    def successful_for(self, devices: tuple[HardwareTestDevice, ...]) -> bool:
        results = {result.device: result for result in self.devices}
        return all(
            device in results and results[device].successful for device in devices
        )


def connection_configuration_fingerprint(
    configuration: HardwareConfiguration, device: HardwareTestDevice
) -> str:
    """Hash only settings that can affect one read-only connection test."""
    fields: dict[HardwareTestDevice, tuple[object, ...]] = {
        HardwareTestDevice.JACKET_PUMP: (
            configuration.jacket_pump_enabled,
            configuration.jacket_port,
            configuration.jacket_unit_id,
            configuration.jacket_channel,
            configuration.baud_rate,
            configuration.serial_command_timeout_seconds,
            configuration.serial_command_retries,
        ),
        HardwareTestDevice.INJECTION_PUMP: (
            configuration.injection_pump_enabled,
            configuration.injection_port,
            configuration.injection_unit_id,
            configuration.injection_channel,
            configuration.baud_rate,
            configuration.serial_command_timeout_seconds,
            configuration.serial_command_retries,
        ),
        HardwareTestDevice.LINE_PRESSURE: (
            configuration.line_pressure_enabled,
            configuration.line_pressure_channel,
            configuration.ni_terminal_configuration,
            asdict(configuration.analog_filter_config()),
        ),
        HardwareTestDevice.DIFFERENTIAL_PRESSURE: (
            configuration.differential_pressure_enabled,
            configuration.differential_pressure_channel,
            configuration.ni_terminal_configuration,
        ),
    }
    payload = json.dumps(fields[device], ensure_ascii=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


class ConnectionTestRegistry:
    """Accumulate independent results without reusing stale configuration data."""

    def __init__(self) -> None:
        self._results: dict[
            HardwareTestDevice, tuple[str, DeviceConnectionResult]
        ] = {}

    def record(
        self,
        configuration: HardwareConfiguration,
        result: DeviceConnectionResult,
    ) -> None:
        fingerprint = connection_configuration_fingerprint(
            configuration, result.device
        )
        self._results[result.device] = (fingerprint, result)

    def record_all(
        self,
        configuration: HardwareConfiguration,
        result: ConnectionTestResult,
    ) -> None:
        for device_result in result.devices:
            self.record(configuration, device_result)

    def result_for(
        self,
        configuration: HardwareConfiguration,
        device: HardwareTestDevice,
    ) -> DeviceConnectionResult | None:
        stored = self._results.get(device)
        if stored is None:
            return None
        fingerprint, result = stored
        if fingerprint != connection_configuration_fingerprint(configuration, device):
            self._results.pop(device, None)
            return None
        return result

    def aggregate(self, configuration: HardwareConfiguration) -> ConnectionTestResult:
        return ConnectionTestResult(
            tuple(
                result
                for device in HardwareTestDevice
                if (result := self.result_for(configuration, device)) is not None
            ),
            configuration.enabled_test_devices(),
        )

    def invalidate_changed(
        self,
        previous: HardwareConfiguration,
        current: HardwareConfiguration,
    ) -> tuple[HardwareTestDevice, ...]:
        invalidated: list[HardwareTestDevice] = []
        for device in HardwareTestDevice:
            if connection_configuration_fingerprint(
                previous, device
            ) != connection_configuration_fingerprint(current, device):
                self._results.pop(device, None)
                invalidated.append(device)
        return tuple(invalidated)


class HardwareConnectionTester(Protocol):
    def test(self, configuration: HardwareConfiguration) -> ConnectionTestResult: ...

    def test_pump(
        self,
        configuration: IscoSerialConfig,
        device: HardwareTestDevice,
    ) -> DeviceConnectionResult: ...

    def test_ni_input(
        self,
        channel: str,
        terminal_configuration: str,
        device: HardwareTestDevice,
        filter_config: AnalogFilterConfig | None = None,
    ) -> DeviceConnectionResult: ...


class PhysicalHardwareConnectionTester:
    """Read-only connection test. It never enters REMOTE or creates an AO task."""

    def __init__(
        self,
        ni_backend: NidaqBackend | None = None,
        diagnostics: DiagnosticLogger | None = None,
    ) -> None:
        self._ni_backend = ni_backend
        self._diagnostics = diagnostics

    def test(self, configuration: HardwareConfiguration) -> ConnectionTestResult:
        operations: dict[HardwareTestDevice, Callable[[], DeviceConnectionResult]] = {
            HardwareTestDevice.JACKET_PUMP: lambda: self.test_pump(
                configuration.jacket_config(), HardwareTestDevice.JACKET_PUMP
            ),
            HardwareTestDevice.INJECTION_PUMP: lambda: self.test_pump(
                configuration.injection_config(), HardwareTestDevice.INJECTION_PUMP
            ),
            HardwareTestDevice.LINE_PRESSURE: lambda: self.test_ni_input(
                configuration.line_pressure_channel,
                configuration.ni_terminal_configuration,
                HardwareTestDevice.LINE_PRESSURE,
                configuration.analog_filter_config(),
            ),
            HardwareTestDevice.DIFFERENTIAL_PRESSURE: lambda: self.test_ni_input(
                configuration.differential_pressure_channel,
                configuration.ni_terminal_configuration,
                HardwareTestDevice.DIFFERENTIAL_PRESSURE,
                configuration.analog_filter_config(),
            ),
        }
        return ConnectionTestResult(
            tuple(operations[device]() for device in configuration.enabled_test_devices()),
            configuration.enabled_test_devices(),
        )

    def test_pump(
        self,
        configuration: IscoSerialConfig,
        device: HardwareTestDevice,
    ) -> DeviceConnectionResult:
        if device not in (
            HardwareTestDevice.JACKET_PUMP,
            HardwareTestDevice.INJECTION_PUMP,
        ):
            raise ValueError("pump test requires a pump device")
        category = (
            DiagnosticCategory.JACKET_PUMP
            if device is HardwareTestDevice.JACKET_PUMP
            else DiagnosticCategory.INJECTION_PUMP
        )
        pump = None
        result: DeviceConnectionResult
        try:
            pump = open_isco_pump(
                configuration,
                diagnostics=self._diagnostics,
                diagnostic_category=category,
            )
            pump.connect()
            status = pump.read_status()
            result = DeviceConnectionResult(
                device,
                True,
                f"{pump.identified_model}; {status.pressure_bar:.3f} bar; "
                f"{status.flow_ml_per_hour:.3f} ml/h",
            )
        except Exception as error:
            result = DeviceConnectionResult(
                device, False, f"{type(error).__name__}: {error}"
            )
        finally:
            if pump is not None:
                try:
                    pump.disconnect()
                except Exception as error:
                    result = DeviceConnectionResult(
                        device,
                        False,
                        f"leválasztási hiba: {type(error).__name__}: {error}",
                    )
        return result

    def test_ni_input(
        self,
        channel: str,
        terminal_configuration: str,
        device: HardwareTestDevice,
        filter_config: AnalogFilterConfig | None = None,
    ) -> DeviceConnectionResult:
        if device not in (
            HardwareTestDevice.LINE_PRESSURE,
            HardwareTestDevice.DIFFERENTIAL_PRESSURE,
        ):
            raise ValueError("NI input test requires a pressure input device")
        category = (
            DiagnosticCategory.NI_LINE
            if device is HardwareTestDevice.LINE_PRESSURE
            else DiagnosticCategory.NI_DIFFERENTIAL
        )
        try:
            config = filter_config or AnalogFilterConfig()
            backend = self._ni_backend or NidaqmxBackend(
                terminal_configuration, config.sample_rate_hz
            )
            read_many = getattr(backend, "read_voltages", None)
            samples = (
                read_many(channel, config.samples_per_read)
                if callable(read_many)
                else [backend.read_voltage(channel) for _ in range(config.samples_per_read)]
            )
            filtered = AnalogSignalFilter(
                alpha=config.ema_alpha,
                median_enabled=config.median_enabled,
                spike_rejection_enabled=config.spike_rejection_enabled,
                spike_limit_voltage=(
                    config.line_spike_limit_voltage
                    if device is HardwareTestDevice.LINE_PRESSURE
                    else config.differential_spike_limit_voltage
                ),
                spike_confirmation_samples=config.spike_confirmation_samples,
            ).process(samples)
            voltage = filtered.last_raw_voltage
            if device is HardwareTestDevice.LINE_PRESSURE and not (
                config.line_electrical_min_voltage
                <= filtered.median_voltage
                <= config.line_electrical_max_voltage
            ):
                raise ConnectionError(
                    "NI line voltage is outside configured electrical limits"
                )
            if device is HardwareTestDevice.DIFFERENTIAL_PRESSURE and not (
                config.differential_electrical_min_voltage
                <= filtered.median_voltage
                <= config.differential_electrical_max_voltage
            ):
                raise ConnectionError(
                    "NI differential voltage is outside configured electrical limits"
                )
            if self._diagnostics is not None:
                self._diagnostics.emit(
                    category,
                    "TEST-RX",
                    f"{channel}; terminal={terminal_configuration}; "
                    f"last={voltage:.6f} V; median={filtered.median_voltage:.6f} V; "
                    f"filtered={filtered.filtered_voltage:.6f} V; quality=good",
                )
            return DeviceConnectionResult(
                device,
                True,
                f"{channel} [{terminal_configuration}]: raw={voltage:.4f} V; "
                f"median={filtered.median_voltage:.4f} V; "
                f"filtered={filtered.filtered_voltage:.4f} V; GOOD",
                voltage,
            )
        except Exception as error:
            return DeviceConnectionResult(
                device, False, f"{type(error).__name__}: {error}"
            )
