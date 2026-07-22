from datetime import UTC, datetime

import pytest

from eor_control.domain import DataQuality, MeasurementSnapshot, PumpStatus
from eor_control.safety import ManualSafetyMonitor, SafetyLimits, SafetyMonitor


def snapshot(
    jacket_pressure: float,
    injection_pressure: float,
    delta_p: float = 5.0,
    quality: DataQuality = DataQuality.GOOD,
) -> MeasurementSnapshot:
    return MeasurementSnapshot(
        recorded_at=datetime.now(UTC),
        monotonic_seconds=1.0,
        jacket_pump=PumpStatus(jacket_pressure, 0.0, 200.0),
        injection_pump=PumpStatus(injection_pressure, 10.0, 200.0),
        line_pressure_bar=injection_pressure,
        differential_pressure_bar=delta_p,
        valve_percent=50.0,
        quality=quality,
    )


def monitor() -> SafetyMonitor:
    return SafetyMonitor(
        SafetyLimits(
            max_jacket_pressure_bar=400.0,
            max_injection_pressure_bar=350.0,
            max_differential_pressure_bar=50.0,
        )
    )


def test_exactly_twenty_bar_margin_is_safe() -> None:
    assert monitor().evaluate(snapshot(120.0, 100.0)).safe


def test_margin_below_twenty_bar_is_unsafe() -> None:
    decision = monitor().evaluate(snapshot(119.9, 100.0))

    assert not decision.safe
    assert "jacket pressure margin is too low" in decision.reasons


def test_configured_margin_may_be_below_twenty_bar() -> None:
    safety_monitor = SafetyMonitor(SafetyLimits(400.0, 350.0, 50.0, 10.0))

    assert safety_monitor.evaluate(snapshot(110.0, 100.0)).safe

    decision = SafetyMonitor(SafetyLimits(400.0, 350.0, 50.0, 10.0)).evaluate(
        snapshot(109.9, 100.0)
    )
    assert not decision.safe
    assert "jacket pressure margin is too low" in decision.reasons


def test_differential_limit_is_inclusive() -> None:
    decision = monitor().evaluate(snapshot(120.0, 100.0, delta_p=50.0))

    assert not decision.safe
    assert "differential pressure limit reached" in decision.reasons


def test_line_pressure_limit_is_supervised() -> None:
    unsafe = snapshot(120.0, 100.0)
    unsafe = MeasurementSnapshot(
        recorded_at=unsafe.recorded_at,
        monotonic_seconds=unsafe.monotonic_seconds,
        jacket_pump=unsafe.jacket_pump,
        injection_pump=unsafe.injection_pump,
        line_pressure_bar=401.0,
        differential_pressure_bar=unsafe.differential_pressure_bar,
        valve_percent=unsafe.valve_percent,
    )

    decision = monitor().evaluate(unsafe)

    assert not decision.safe
    assert "line pressure limit exceeded" in decision.reasons


def test_missing_optional_pressure_inputs_do_not_create_safety_fault() -> None:
    optional = snapshot(120.0, 100.0)
    optional = MeasurementSnapshot(
        recorded_at=optional.recorded_at,
        monotonic_seconds=optional.monotonic_seconds,
        jacket_pump=optional.jacket_pump,
        injection_pump=optional.injection_pump,
        line_pressure_bar=None,
        differential_pressure_bar=None,
        valve_percent=optional.valve_percent,
    )

    assert monitor().evaluate(optional).safe


def test_manual_safety_only_checks_selected_pump() -> None:
    safe = ManualSafetyMonitor.evaluate_pump(
        PumpStatus(100.0, 1.0, 200.0), maximum_pressure_bar=150.0
    )
    unsafe = ManualSafetyMonitor.evaluate_pump(
        PumpStatus(151.0, 1.0, 200.0), maximum_pressure_bar=150.0
    )

    assert safe.safe
    assert not unsafe.safe
    assert unsafe.reasons == ("selected pump pressure limit exceeded",)


@pytest.mark.parametrize("invalid_value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_sensor_value_is_unsafe(invalid_value: float) -> None:
    decision = monitor().evaluate(snapshot(120.0, 100.0, delta_p=invalid_value))

    assert not decision.safe
    assert "non-finite measurement value" in decision.reasons


def test_bad_data_quality_is_unsafe() -> None:
    decision = monitor().evaluate(snapshot(120.0, 100.0, quality=DataQuality.STALE))

    assert not decision.safe
    assert "invalid data quality: stale" in decision.reasons


def test_emergency_stop_and_deadline_miss_are_unsafe() -> None:
    decision = monitor().evaluate(
        snapshot(120.0, 100.0), emergency_stop=True, control_deadline_missed=True
    )

    assert not decision.safe
    assert "manual emergency stop" in decision.reasons
    assert "control deadline missed" in decision.reasons


def test_fault_remains_latched_after_cause_disappears() -> None:
    safety_monitor = monitor()
    safety_monitor.evaluate(snapshot(119.0, 100.0))

    decision = safety_monitor.evaluate(snapshot(120.0, 100.0))

    assert not decision.safe
    assert decision.latched
    assert "jacket pressure margin is too low" in decision.reasons


def test_latched_fault_requires_acknowledgement_and_safe_snapshot_to_reset() -> None:
    safety_monitor = monitor()
    unsafe_snapshot = snapshot(119.0, 100.0)
    safe_snapshot = snapshot(120.0, 100.0)
    safety_monitor.evaluate(unsafe_snapshot)

    assert not safety_monitor.reset(safe_snapshot, operator_acknowledged=False).safe
    assert not safety_monitor.reset(unsafe_snapshot, operator_acknowledged=True).safe
    assert safety_monitor.reset(safe_snapshot, operator_acknowledged=True).safe


def test_reset_without_a_latched_fault_returns_current_safety_state() -> None:
    assert monitor().reset(snapshot(120.0, 100.0), operator_acknowledged=False).safe


def test_reconfiguration_does_not_clear_latched_fault() -> None:
    safety_monitor = monitor()
    safety_monitor.evaluate(snapshot(119.0, 100.0))
    safety_monitor.configure(SafetyLimits(500.0, 500.0, 100.0, 10.0))

    decision = safety_monitor.evaluate(snapshot(120.0, 100.0))

    assert not decision.safe
    assert decision.latched


def test_invalid_safety_limits_are_rejected() -> None:
    with pytest.raises(ValueError, match="positive and finite"):
        SafetyLimits(400.0, 350.0, 50.0, 0.0)


def test_controlled_pressure_overshoot_limit_is_inclusive() -> None:
    safety_monitor = monitor()

    decision = safety_monitor.evaluate(
        snapshot(120.0, 100.0),
        controlled_pressure_bar=105.0,
        pressure_target_bar=100.0,
    )

    assert not decision.safe
    assert "controlled pressure overshoot limit reached" in decision.reasons


def test_controlled_pressure_below_overshoot_limit_is_safe() -> None:
    decision = monitor().evaluate(
        snapshot(120.0, 100.0),
        controlled_pressure_bar=104.999,
        pressure_target_bar=100.0,
    )

    assert decision.safe
