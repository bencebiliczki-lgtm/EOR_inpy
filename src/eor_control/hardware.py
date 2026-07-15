import importlib
from dataclasses import asdict, dataclass
from math import isfinite
from typing import Protocol

from eor_control.diagnostics import DiagnosticCategory, DiagnosticLogger
from eor_control.isco import IscoSerialConfig, open_isco_pump
from eor_control.ni import NidaqBackend, NidaqConfig, NidaqmxBackend


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

    def __post_init__(self) -> None:
        if self.jacket_port.strip().upper() == self.injection_port.strip().upper():
            raise ValueError("the two ISCO pumps must use different COM ports")
        IscoSerialConfig(
            self.jacket_port,
            self.jacket_unit_id,
            self.jacket_channel,
            self.baud_rate,
        )
        IscoSerialConfig(
            self.injection_port,
            self.injection_unit_id,
            self.injection_channel,
            self.baud_rate,
        )
        NidaqConfig(
            self.line_pressure_channel,
            self.differential_pressure_channel,
            self.valve_output_channel,
            self.safe_output_voltage,
        )
        if self.ni_terminal_configuration not in NidaqmxBackend.TERMINAL_CONFIGURATIONS:
            raise ValueError("unsupported NI terminal configuration")
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
        return IscoSerialConfig(
            self.jacket_port,
            self.jacket_unit_id,
            self.jacket_channel,
            self.baud_rate,
        )

    def injection_config(self) -> IscoSerialConfig:
        return IscoSerialConfig(
            self.injection_port,
            self.injection_unit_id,
            self.injection_channel,
            self.baud_rate,
        )

    def ni_config(self) -> NidaqConfig:
        return NidaqConfig(
            self.line_pressure_channel,
            self.differential_pressure_channel,
            self.valve_output_channel,
            self.safe_output_voltage,
        )

    def to_settings(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ConnectionTestResult:
    jacket_pump: str
    injection_pump: str
    line_voltage: float
    differential_voltage: float


class HardwareConnectionTester(Protocol):
    def test(self, configuration: HardwareConfiguration) -> ConnectionTestResult: ...


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
        ni_backend = self._ni_backend or NidaqmxBackend(
            configuration.ni_terminal_configuration
        )
        jacket = open_isco_pump(
            configuration.jacket_config(),
            diagnostics=self._diagnostics,
            diagnostic_category=DiagnosticCategory.JACKET_PUMP,
        )
        injection = open_isco_pump(
            configuration.injection_config(),
            diagnostics=self._diagnostics,
            diagnostic_category=DiagnosticCategory.INJECTION_PUMP,
        )
        try:
            jacket.connect()
            injection.connect()
            jacket_status = jacket.read_status()
            injection_status = injection.read_status()
            line_voltage = ni_backend.read_voltage(
                configuration.line_pressure_channel
            )
            differential_voltage = ni_backend.read_voltage(
                configuration.differential_pressure_channel
            )
            if self._diagnostics is not None:
                self._diagnostics.emit(
                    DiagnosticCategory.NI_LINE,
                    "TEST-RX",
                    f"{configuration.line_pressure_channel}={line_voltage:.6f} V",
                )
                self._diagnostics.emit(
                    DiagnosticCategory.NI_DIFFERENTIAL,
                    "TEST-RX",
                    f"{configuration.differential_pressure_channel}="
                    f"{differential_voltage:.6f} V",
                )
            if not all(isfinite(value) for value in (line_voltage, differential_voltage)):
                raise ConnectionError("NI connection test returned a non-finite voltage")
            return ConnectionTestResult(
                jacket_pump=(
                    f"{jacket.identified_model}; {jacket_status.pressure_bar:.2f} bar"
                ),
                injection_pump=(
                    f"{injection.identified_model}; {injection_status.pressure_bar:.2f} bar"
                ),
                line_voltage=line_voltage,
                differential_voltage=differential_voltage,
            )
        finally:
            jacket.disconnect()
            injection.disconnect()
