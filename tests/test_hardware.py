from types import SimpleNamespace

import pytest

from eor_control import hardware
from eor_control.hardware import (
    ConnectionTestRegistry,
    ConnectionTestResult,
    DeviceConnectionResult,
    HardwareConfiguration,
    HardwareTestDevice,
    PhysicalHardwareConnectionTester,
)
from eor_control.isco import IscoSerialConfig


def configuration() -> HardwareConfiguration:
    return HardwareConfiguration(
        jacket_port="COM3",
        jacket_unit_id=1,
        jacket_channel="A",
        injection_port="COM4",
        injection_unit_id=2,
        injection_channel="A",
        baud_rate=9600,
        line_pressure_channel="Dev1/ai0",
        differential_pressure_channel="Dev1/ai1",
        valve_output_channel="Dev1/ao0",
        safe_output_voltage=1.0,
        valve_zero_percent_voltage=1.0,
        valve_hundred_percent_voltage=5.0,
    )


def test_hardware_configuration_builds_adapter_configs() -> None:
    config = configuration()

    assert config.jacket_config().port == "COM3"
    assert config.injection_config().unit_id == 2
    assert config.ni_config().valve_output_channel == "Dev1/ao0"
    assert config.ni_config().line_pressure_channel == "Dev1/ai0"
    assert config.ni_terminal_configuration == "DEFAULT"
    assert config.supervised_test_minutes == 60


def test_line_pressure_device_can_be_omitted_from_hardware_profile() -> None:
    values = configuration().to_settings()
    values["line_pressure_enabled"] = False
    values["line_pressure_channel"] = ""

    config = HardwareConfiguration(**values)  # type: ignore[arg-type]

    assert config.measurement_ready
    assert config.ni_config().line_pressure_channel is None
    assert HardwareTestDevice.LINE_PRESSURE not in config.enabled_test_devices()
    successful = ConnectionTestResult(
        tuple(
            DeviceConnectionResult(device, True, device.value)
            for device in config.enabled_test_devices()
        )
    )
    assert successful.successful_for(config.enabled_test_devices())


def test_serial_port_display_name_does_not_repeat_embedded_com_port() -> None:
    port = hardware.SerialPortInfo(
        device="COM4",
        description="Standard Serial over Bluetooth link (COM4)",
    )

    assert port.display_name == "Standard Serial over Bluetooth link (COM4)"


def test_hardware_configuration_rejects_shared_port_and_invalid_valve_range() -> None:
    values = configuration().to_settings()
    values["injection_port"] = "com3"
    with pytest.raises(ValueError, match="different COM ports"):
        HardwareConfiguration(**values)  # type: ignore[arg-type]

    values = configuration().to_settings()
    values["valve_hundred_percent_voltage"] = 6.0
    with pytest.raises(ValueError, match="between 1 and 5"):
        HardwareConfiguration(**values)  # type: ignore[arg-type]

    values = configuration().to_settings()
    values["ni_terminal_configuration"] = "INVALID"
    with pytest.raises(ValueError, match="terminal configuration"):
        HardwareConfiguration(**values)  # type: ignore[arg-type]

    values = configuration().to_settings()
    values["supervised_test_minutes"] = 0
    with pytest.raises(ValueError, match="test duration"):
        HardwareConfiguration(**values)  # type: ignore[arg-type]


def test_hardware_discovery_lists_serial_and_ni_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    serial_module = SimpleNamespace(
        comports=lambda: [
            SimpleNamespace(
                device="COM7",
                description="USB Serial Port",
                manufacturer="FTDI",
                product="FT232R",
                hwid="USB VID:PID=0403:6001",
            ),
            SimpleNamespace(device="COM2", description="PCI Serial Port"),
        ]
    )
    device = SimpleNamespace(
        name="Dev1",
        product_type="NI USB-6001",
        serial_num=12345678,
        ai_physical_chans=[SimpleNamespace(name="Dev1/ai1"), SimpleNamespace(name="Dev1/ai0")],
        ao_physical_chans=[SimpleNamespace(name="Dev1/ao0")],
    )
    ni_module = SimpleNamespace(
        System=SimpleNamespace(local=lambda: SimpleNamespace(devices=[device]))
    )

    def import_module(name: str) -> object:
        return serial_module if name == "serial.tools.list_ports" else ni_module

    monkeypatch.setattr(hardware.importlib, "import_module", import_module)

    discovery = hardware.discover_hardware()

    assert [port.device for port in discovery.serial_ports] == ["COM2", "COM7"]
    assert discovery.serial_ports[0].display_name == "PCI Serial Port (COM2)"
    assert discovery.serial_ports[1].manufacturer == "FTDI"
    assert "USB VID:PID=0403:6001" in discovery.serial_ports[1].tooltip
    assert [channel.channel for channel in discovery.ni_input_channels] == [
        "Dev1/ai0",
        "Dev1/ai1",
    ]
    assert discovery.ni_input_channels[0].display_name == "1. analóg bemenet (AI0)"
    assert discovery.ni_input_channels[0].serial_number == "12345678"
    assert [channel.channel for channel in discovery.ni_output_channels] == ["Dev1/ao0"]
    assert discovery.warnings == ()


def test_connection_test_reports_each_device_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePump:
        identified_model = "260D"

        def __init__(self, *, fails: bool) -> None:
            self._fails = fails
            self.disconnected = False

        def connect(self) -> None:
            if self._fails:
                raise ConnectionError("pump unavailable")

        def read_status(self) -> object:
            return SimpleNamespace(
                pressure_bar=123.0,
                flow_ml_per_hour=4.5,
            )

        def disconnect(self) -> None:
            self.disconnected = True

    pumps: list[FakePump] = []

    def open_pump(config: IscoSerialConfig, **_kwargs: object) -> FakePump:
        pump = FakePump(fails=config.port == "COM3")
        pumps.append(pump)
        return pump

    class FakeNiBackend:
        def read_voltage(self, channel: str) -> float:
            if channel.endswith("ai1"):
                raise ConnectionError("NI channel unavailable")
            return 2.25

    monkeypatch.setattr(hardware, "open_isco_pump", open_pump)
    tester = PhysicalHardwareConnectionTester(ni_backend=FakeNiBackend())  # type: ignore[arg-type]

    result = tester.test(configuration())

    assert not result.all_successful
    jacket = result.for_device(HardwareTestDevice.JACKET_PUMP)
    injection = result.for_device(HardwareTestDevice.INJECTION_PUMP)
    line = result.for_device(HardwareTestDevice.LINE_PRESSURE)
    differential = result.for_device(HardwareTestDevice.DIFFERENTIAL_PRESSURE)
    assert jacket is not None and not jacket.successful
    assert injection is not None and injection.successful
    assert line is not None and line.successful and line.value == 2.25
    assert differential is not None and not differential.successful
    assert all(pump.disconnected for pump in pumps)


def test_individual_connection_results_accumulate_and_only_changed_device_expires() -> None:
    original = configuration()
    registry = ConnectionTestRegistry()
    for device in HardwareTestDevice:
        registry.record(original, DeviceConnectionResult(device, True, device.value))

    assert registry.aggregate(original).all_successful
    changed_values = original.to_settings()
    changed_values["jacket_port"] = "COM2"
    changed = HardwareConfiguration(**changed_values)  # type: ignore[arg-type]

    invalidated = registry.invalidate_changed(original, changed)

    assert invalidated == (HardwareTestDevice.JACKET_PUMP,)
    remaining = registry.aggregate(changed)
    assert remaining.for_device(HardwareTestDevice.JACKET_PUMP) is None
    assert remaining.for_device(HardwareTestDevice.LINE_PRESSURE) is not None
