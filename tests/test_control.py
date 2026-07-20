from datetime import UTC, datetime

import pytest

from eor_control.control import (
    ControlDirection,
    ControlMode,
    PidController,
    PidParameters,
    PressureSource,
    ValveController,
    ValveOscillationError,
)
from eor_control.domain import MeasurementSnapshot, PumpStatus
from eor_control.safety import SafetyDecision


def snapshot(
    *, injection_pressure: float = 100.0, line_pressure: float = 80.0
) -> MeasurementSnapshot:
    return MeasurementSnapshot(
        recorded_at=datetime.now(UTC),
        monotonic_seconds=1.0,
        jacket_pump=PumpStatus(120.0, 0.0, 200.0),
        injection_pump=PumpStatus(injection_pressure, 10.0, 200.0),
        line_pressure_bar=line_pressure,
        differential_pressure_bar=5.0,
        valve_percent=50.0,
    )


def controller(parameters: PidParameters | None = None) -> ValveController:
    return ValveController(PidController(parameters or PidParameters(1.0, 0.0, 0.0)))


def test_manual_mode_passes_validated_percentage() -> None:
    command = controller().command(
        snapshot=snapshot(),
        safety=SafetyDecision(True, ()),
        mode=ControlMode.MANUAL,
        manual_output_percent=42.0,
    )

    assert command.enabled
    assert command.output_percent == 42.0
    assert command.source is None


@pytest.mark.parametrize("output", [-0.1, 100.1, float("nan")])
def test_manual_mode_rejects_invalid_percentage(output: float) -> None:
    with pytest.raises(ValueError, match="manual output"):
        controller().command(
            snapshot=snapshot(),
            safety=SafetyDecision(True, ()),
            mode=ControlMode.MANUAL,
            manual_output_percent=output,
        )


def test_automatic_mode_uses_selected_pressure_source() -> None:
    injection_command = controller().command(
        snapshot=snapshot(),
        safety=SafetyDecision(True, ()),
        mode=ControlMode.AUTOMATIC,
        source=PressureSource.INJECTION_PUMP,
        setpoint_bar=110.0,
        dt_seconds=1.0,
    )
    line_command = controller().command(
        snapshot=snapshot(),
        safety=SafetyDecision(True, ()),
        mode=ControlMode.AUTOMATIC,
        source=PressureSource.LINE_SENSOR,
        setpoint_bar=110.0,
        dt_seconds=1.0,
    )

    assert injection_command.output_percent == pytest.approx(10.0)
    assert line_command.output_percent == pytest.approx(30.0)


def test_reverse_direction_inverts_control_action() -> None:
    reverse = controller(PidParameters(1.0, 0.0, 0.0, direction=ControlDirection.REVERSE))
    command = reverse.command(
        snapshot=snapshot(injection_pressure=120.0),
        safety=SafetyDecision(True, ()),
        mode=ControlMode.AUTOMATIC,
        source=PressureSource.INJECTION_PUMP,
        setpoint_bar=100.0,
        dt_seconds=1.0,
    )

    assert command.output_percent == pytest.approx(20.0)


def test_output_is_limited_and_integral_does_not_wind_up() -> None:
    pid = PidController(
        PidParameters(10.0, 5.0, 0.0, output_min_percent=0.0, output_max_percent=50.0)
    )

    for _ in range(100):
        assert pid.calculate(setpoint=100.0, measurement=0.0, dt_seconds=1.0) == 50.0

    assert pid.calculate(setpoint=0.0, measurement=10.0, dt_seconds=1.0) == 0.0


def test_derivative_is_on_measurement_without_setpoint_kick() -> None:
    pid = PidController(
        PidParameters(0.0, 0.0, 1.0, output_min_percent=0.0, output_max_percent=100.0)
    )

    first = pid.calculate(setpoint=10.0, measurement=5.0, dt_seconds=1.0)
    after_setpoint_change = pid.calculate(setpoint=20.0, measurement=5.0, dt_seconds=1.0)

    assert first == 0.0
    assert after_setpoint_change == 0.0


def test_safety_interlock_suppresses_manual_and_automatic_output() -> None:
    command = controller().command(
        snapshot=snapshot(),
        safety=SafetyDecision(False, ("fault",), latched=True),
        mode=ControlMode.MANUAL,
        manual_output_percent=50.0,
    )

    assert not command.enabled
    assert command.output_percent is None
    assert command.reason == "safety interlock active"


def test_invalid_pid_configuration_is_rejected() -> None:
    with pytest.raises(ValueError, match="gains"):
        PidParameters(-1.0, 0.0, 0.0)
    with pytest.raises(ValueError, match="limits"):
        PidParameters(1.0, 0.0, 0.0, output_min_percent=50.0, output_max_percent=50.0)
    with pytest.raises(ValueError, match="finite"):
        PidParameters(float("nan"), 0.0, 0.0)


def test_pid_can_be_reconfigured_with_bumpless_current_output() -> None:
    pid = PidController(PidParameters(1.0, 0.0, 0.0))
    pid.configure(PidParameters(0.0, 0.0, 0.0), current_output_percent=42.0)

    assert pid.calculate(setpoint=100.0, measurement=100.0, dt_seconds=1.0) == 42.0


def test_pid_deadband_holds_output_and_integrator() -> None:
    pid = PidController(PidParameters(1.0, 2.0, 0.0, deadband_bar=1.0))
    pid.reset(output_percent=35.0)

    assert pid.calculate(setpoint=100.0, measurement=99.5, dt_seconds=1.0) == 35.0
    assert pid.calculate(setpoint=100.0, measurement=99.5, dt_seconds=1.0) == 35.0


def test_pid_output_rate_limit_is_time_based() -> None:
    pid = PidController(
        PidParameters(10.0, 0.0, 0.0, maximum_output_rate_percent_per_second=5.0)
    )

    assert pid.calculate(setpoint=100.0, measurement=0.0, dt_seconds=0.2) == 1.0
    assert pid.calculate(setpoint=100.0, measurement=0.0, dt_seconds=0.2) == 2.0


def test_pid_filters_control_measurement() -> None:
    pid = PidController(PidParameters(1.0, 0.0, 0.0, measurement_filter_alpha=0.5))

    assert pid.calculate(setpoint=100.0, measurement=100.0, dt_seconds=1.0) == 0.0
    assert pid.calculate(setpoint=100.0, measurement=80.0, dt_seconds=1.0) == 10.0


def test_pid_raises_latched_style_oscillation_fault_after_repeated_reversals() -> None:
    pid = PidController(
        PidParameters(
            1.0,
            0.0,
            0.0,
            minimum_reversal_interval_seconds=0.0,
            reversal_deadband_percent=0.0,
            maximum_reversals=2,
            reversal_window_seconds=10.0,
        )
    )
    pid.calculate(setpoint=50.0, measurement=40.0, dt_seconds=1.0)
    pid.calculate(setpoint=50.0, measurement=60.0, dt_seconds=1.0)
    pid.calculate(setpoint=50.0, measurement=40.0, dt_seconds=1.0)

    with pytest.raises(ValveOscillationError, match="VALVE_OSCILLATION"):
        pid.calculate(setpoint=50.0, measurement=60.0, dt_seconds=1.0)


def test_manual_to_automatic_transfer_starts_from_manual_output() -> None:
    valve = controller(PidParameters(1.0, 0.0, 0.0))
    valve.command(
        snapshot=snapshot(),
        safety=SafetyDecision(True, ()),
        mode=ControlMode.MANUAL,
        manual_output_percent=42.0,
    )

    command = valve.command(
        snapshot=snapshot(injection_pressure=100.0),
        safety=SafetyDecision(True, ()),
        mode=ControlMode.AUTOMATIC,
        source=PressureSource.INJECTION_PUMP,
        setpoint_bar=110.0,
        dt_seconds=0.1,
    )

    assert command.output_percent == pytest.approx(42.0)
    assert command.reason == "bumpless manual-to-automatic transfer"
