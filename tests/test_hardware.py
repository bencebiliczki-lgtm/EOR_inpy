import pytest

from eor_control.hardware import HardwareConfiguration


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
    assert config.ni_config().inlet_pressure_channel == "Dev1/ai2"
    assert config.ni_terminal_configuration == "DEFAULT"
    assert config.supervised_test_minutes == 60


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
