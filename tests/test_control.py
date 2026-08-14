from datetime import UTC, datetime
from math import exp

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
from eor_control.domain import (
    AnalogPressureReading,
    DataQuality,
    MeasurementSnapshot,
    PumpPressureReading,
    PumpStatus,
)
from eor_control.safety import SafetyDecision


def snapshot(
    *,
    injection_pressure: float = 100.0,
    line_pressure: float = 80.0,
    line_quality: DataQuality | None = None,
) -> MeasurementSnapshot:
    reading = None
    if line_quality is not None:
        reading = AnalogPressureReading(
            last_raw_voltage=1.8,
            median_voltage=1.8,
            filtered_voltage=1.8,
            raw_pressure_bar=line_pressure,
            filtered_pressure_bar=line_pressure,
            measured_at=datetime.now(UTC),
            monotonic_seconds=1.0,
            sample_age_seconds=0.0,
            quality=line_quality,
            quality_reason="test quality",
            sample_count=20,
        )
    return MeasurementSnapshot(
        recorded_at=datetime.now(UTC),
        monotonic_seconds=1.0,
        jacket_pump=PumpStatus(120.0, 0.0, 200.0),
        injection_pump=PumpStatus(injection_pressure, 10.0, 200.0),
        line_pressure_bar=line_pressure,
        differential_pressure_bar=5.0,
        valve_percent=50.0,
        line_pressure_reading=reading,
    )


def controller(parameters: PidParameters | None = None) -> ValveController:
    return ValveController(
        PidController(
            parameters
            or PidParameters(
                1.0,
                0.0,
                0.0,
                direction=ControlDirection.DIRECT,
            )
        )
    )


def timestamped_snapshot(
    pressure: float,
    *,
    sequence: int,
    timestamp: float,
    quality: DataQuality = DataQuality.GOOD,
) -> MeasurementSnapshot:
    base = snapshot(injection_pressure=pressure)
    return MeasurementSnapshot(
        recorded_at=base.recorded_at,
        monotonic_seconds=timestamp,
        jacket_pump=base.jacket_pump,
        injection_pump=base.injection_pump,
        line_pressure_bar=base.line_pressure_bar,
        differential_pressure_bar=base.differential_pressure_bar,
        valve_percent=base.valve_percent,
        injection_pressure_reading=PumpPressureReading(
            pressure,
            timestamp,
            0.0,
            sequence,
            quality,
        ),
    )


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


@pytest.mark.parametrize(
    "quality",
    [
        DataQuality.STALE,
        DataQuality.INVALID,
        DataQuality.OUT_OF_RANGE,
        DataQuality.DISCONNECTED,
    ],
)
def test_line_sensor_pid_rejects_every_non_good_quality(
    quality: DataQuality,
) -> None:
    command = controller().command(
        snapshot=snapshot(line_quality=quality),
        safety=SafetyDecision(True, ()),
        mode=ControlMode.AUTOMATIC,
        source=PressureSource.LINE_SENSOR,
        setpoint_bar=110.0,
        dt_seconds=1.0,
    )

    assert not command.enabled
    assert command.output_percent is None
    assert quality.value in str(command.reason)


def test_line_sensor_pid_does_not_apply_a_second_ema() -> None:
    valve = controller(
        PidParameters(
            1.0,
            0.0,
            0.0,
            measurement_filter_alpha=0.1,
            direction=ControlDirection.DIRECT,
        )
    )
    valve.command(
        snapshot=snapshot(line_pressure=100.0, line_quality=DataQuality.GOOD),
        safety=SafetyDecision(True, ()),
        mode=ControlMode.AUTOMATIC,
        source=PressureSource.LINE_SENSOR,
        setpoint_bar=100.0,
        dt_seconds=1.0,
    )
    command = valve.command(
        snapshot=snapshot(line_pressure=80.0, line_quality=DataQuality.GOOD),
        safety=SafetyDecision(True, ()),
        mode=ControlMode.AUTOMATIC,
        source=PressureSource.LINE_SENSOR,
        setpoint_bar=100.0,
        dt_seconds=1.0,
    )

    assert command.output_percent == pytest.approx(20.0)


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


def test_default_pid_direction_matches_opening_valve_pressure_response() -> None:
    pid = PidController(PidParameters(1.0, 0.0, 0.0))
    pid.reset(output_percent=50.0)

    below_setpoint = pid.calculate(
        setpoint=100.0, measurement=90.0, dt_seconds=1.0
    )
    pid.reset(output_percent=50.0)
    above_setpoint = pid.calculate(
        setpoint=100.0, measurement=110.0, dt_seconds=1.0
    )

    assert below_setpoint < 50.0  # close the valve to raise pressure
    assert above_setpoint > 50.0  # open the valve to lower pressure


def test_output_is_limited_and_integral_does_not_wind_up() -> None:
    pid = PidController(
        PidParameters(
            10.0,
            5.0,
            0.0,
            output_min_percent=0.0,
            output_max_percent=50.0,
            direction=ControlDirection.DIRECT,
        )
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
        PidParameters(
            10.0,
            0.0,
            0.0,
            maximum_output_rate_percent_per_second=5.0,
            direction=ControlDirection.DIRECT,
        )
    )

    assert pid.calculate(setpoint=100.0, measurement=0.0, dt_seconds=0.2) == 1.0
    assert pid.calculate(setpoint=100.0, measurement=0.0, dt_seconds=0.2) == 2.0


def test_pid_filters_control_measurement() -> None:
    pid = PidController(
        PidParameters(
            1.0,
            0.0,
            0.0,
            measurement_filter_alpha=0.5,
            direction=ControlDirection.DIRECT,
        )
    )

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
            direction=ControlDirection.DIRECT,
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


def test_new_measurement_activates_preselected_automatic_mode_without_stale_transition() -> None:
    valve = controller(PidParameters(1.0, 0.0, 0.0))
    valve.command(
        snapshot=snapshot(),
        safety=SafetyDecision(True, ()),
        mode=ControlMode.MANUAL,
        manual_output_percent=42.0,
    )

    valve.begin_measurement(ControlMode.AUTOMATIC)
    command = valve.command(
        snapshot=snapshot(injection_pressure=100.0),
        safety=SafetyDecision(True, ()),
        mode=ControlMode.AUTOMATIC,
        source=PressureSource.INJECTION_PUMP,
        setpoint_bar=110.0,
        dt_seconds=0.1,
    )

    assert command.mode is ControlMode.AUTOMATIC
    assert command.source is PressureSource.INJECTION_PUMP
    assert command.reason is None


def test_same_pressure_sequence_updates_pid_only_once() -> None:
    pid = PidController(
        PidParameters(0.0, 1.0, 0.0, direction=ControlDirection.DIRECT)
    )

    first = pid.calculate(
        setpoint=10.0,
        measurement=0.0,
        dt_seconds=0.2,
        sequence=7,
        timestamp_monotonic=1.0,
    )
    held = pid.calculate(
        setpoint=10.0,
        measurement=0.0,
        dt_seconds=0.2,
        sequence=7,
        timestamp_monotonic=1.0,
    )

    assert held == pytest.approx(first)
    assert pid.diagnostics.state.value == "HOLD"


def test_time_based_ema_uses_real_measurement_interval() -> None:
    pid = PidController(
        PidParameters(
            0.0,
            0.0,
            0.0,
            measurement_filter_time_constant_seconds=1.0,
            direction=ControlDirection.DIRECT,
        )
    )
    pid.calculate(
        setpoint=0.0,
        measurement=0.0,
        dt_seconds=0.2,
        sequence=1,
        timestamp_monotonic=0.0,
    )
    pid.calculate(
        setpoint=0.0,
        measurement=10.0,
        dt_seconds=0.5,
        sequence=2,
        timestamp_monotonic=1.0,
    )

    assert pid.diagnostics.filtered_measurement_bar == pytest.approx(
        10.0 * (1.0 - exp(-1.0))
    )


def test_integral_and_derivative_use_measurement_timestamp_delta() -> None:
    pid = PidController(
        PidParameters(
            0.0,
            1.0,
            1.0,
            direction=ControlDirection.REVERSE,
            measurement_filter_enabled=False,
        )
    )
    pid.calculate(
        setpoint=20.0,
        measurement=10.0,
        dt_seconds=0.2,
        sequence=1,
        timestamp_monotonic=10.0,
    )
    pid.calculate(
        setpoint=20.0,
        measurement=14.0,
        dt_seconds=0.2,
        sequence=2,
        timestamp_monotonic=12.0,
    )

    assert pid.diagnostics.measurement_dt_seconds == pytest.approx(2.0)
    assert pid.diagnostics.d_term_percent == pytest.approx(2.0)


def test_long_sample_gap_holds_integral_and_derivative_terms() -> None:
    pid = PidController(
        PidParameters(
            0.0,
            1.0,
            1.0,
            maximum_pid_sample_interval_seconds=1.0,
            direction=ControlDirection.REVERSE,
            measurement_filter_enabled=False,
        )
    )
    pid.calculate(
        setpoint=20.0,
        measurement=10.0,
        dt_seconds=0.2,
        sequence=1,
        timestamp_monotonic=1.0,
    )
    first_integral = pid.diagnostics.i_term_percent
    pid.calculate(
        setpoint=20.0,
        measurement=15.0,
        dt_seconds=0.2,
        sequence=2,
        timestamp_monotonic=5.0,
    )

    assert pid.diagnostics.i_term_percent == pytest.approx(first_integral)
    assert pid.diagnostics.d_term_percent == pytest.approx(0.0)


def test_stale_selected_source_blocks_automatic_pid() -> None:
    valve = controller()
    command = valve.command(
        snapshot=timestamped_snapshot(
            100.0,
            sequence=1,
            timestamp=1.0,
            quality=DataQuality.STALE,
        ),
        safety=SafetyDecision(True, ()),
        mode=ControlMode.AUTOMATIC,
        source=PressureSource.INJECTION_PUMP,
        setpoint_bar=110.0,
        dt_seconds=0.2,
    )

    assert not command.enabled
    assert command.pid_state.value == "BLOCKED"


def test_pressure_source_switch_is_bumpless() -> None:
    valve = controller(PidParameters(2.0, 0.2, 0.5))
    before = valve.command(
        snapshot=snapshot(injection_pressure=100.0, line_pressure=70.0),
        safety=SafetyDecision(True, ()),
        mode=ControlMode.AUTOMATIC,
        source=PressureSource.INJECTION_PUMP,
        setpoint_bar=110.0,
        dt_seconds=0.2,
    )
    after = valve.command(
        snapshot=snapshot(injection_pressure=100.0, line_pressure=70.0),
        safety=SafetyDecision(True, ()),
        mode=ControlMode.AUTOMATIC,
        source=PressureSource.LINE_SENSOR,
        setpoint_bar=110.0,
        dt_seconds=0.2,
    )

    assert after.output_percent == pytest.approx(before.output_percent)
    assert after.reason == "bumpless pressure-source transfer"
    assert after.pid_state.value == "INITIALIZING"


def test_safe_state_restart_is_bumpless() -> None:
    valve = controller(PidParameters(2.0, 0.2, 0.5))
    valve.enter_safe(17.0)

    command = valve.command(
        snapshot=snapshot(injection_pressure=90.0),
        safety=SafetyDecision(True, ()),
        mode=ControlMode.AUTOMATIC,
        source=PressureSource.INJECTION_PUMP,
        setpoint_bar=110.0,
        dt_seconds=0.2,
    )

    assert command.output_percent == pytest.approx(17.0)
    assert command.reason == "bumpless safe-to-automatic restart"
    assert command.pid_state.value == "INITIALIZING"


def test_deadband_uses_separate_exit_threshold() -> None:
    pid = PidController(
        PidParameters(
            1.0,
            0.0,
            0.0,
            deadband_bar=1.0,
            deadband_exit_bar=2.0,
            direction=ControlDirection.DIRECT,
            measurement_filter_enabled=False,
        )
    )
    pid.reset(output_percent=30.0)

    assert pid.calculate(setpoint=10.0, measurement=9.5, dt_seconds=0.2) == 30.0
    assert pid.calculate(setpoint=10.0, measurement=8.5, dt_seconds=0.2) == 30.0
    assert pid.calculate(setpoint=10.0, measurement=7.5, dt_seconds=0.2) != 30.0
