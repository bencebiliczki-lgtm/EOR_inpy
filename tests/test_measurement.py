from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from threading import Event

import pytest

from eor_control.calibration import LinearCalibration
from eor_control.devices import DisabledPump
from eor_control.domain import DataQuality, MeasurementRecord
from eor_control.measurement import MeasurementChannels, MeasurementService
from eor_control.safety import SafetyLimits, SafetyMonitor
from eor_control.simulators import SimulatedDataAcquisition, SimulatedPump


@dataclass
class MemoryWriter:
    records: list[MeasurementRecord] = field(default_factory=list)

    def write(self, record: MeasurementRecord) -> None:
        self.records.append(record)

    def close(self) -> None:
        pass


@dataclass
class FakeClock:
    current: float = 10.0
    sleeps: list[float] = field(default_factory=list)

    def utc_now(self) -> datetime:
        return datetime(2026, 7, 13, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def service(
    *,
    jacket_pressure: float = 120.0,
    injection_pressure: float = 100.0,
    persistence_enabled: bool = True,
) -> tuple[
    MeasurementService,
    SimulatedPump,
    SimulatedPump,
    SimulatedDataAcquisition,
    MemoryWriter,
]:
    jacket = SimulatedPump(pressure_bar=jacket_pressure)
    injection = SimulatedPump(
        pressure_bar=injection_pressure, flow_ml_per_hour=10.0, remaining_volume_ml=250.0
    )
    jacket.connect()
    injection.connect()
    daq = SimulatedDataAcquisition()
    daq.inputs.update(line_pressure=2.0, differential_pressure=1.5)
    writer = MemoryWriter()
    calibration = LinearCalibration(1.0, 5.0, 0.0, 400.0)
    measurement_service = MeasurementService(
        jacket_pump=jacket,
        injection_pump=injection,
        daq=daq,
        line_calibration=calibration,
        differential_calibration=LinearCalibration(1.0, 5.0, 0.0, 40.0),
        safety_monitor=SafetyMonitor(SafetyLimits(350.0, 350.0, 50.0)),
        writer=writer,
        clock=FakeClock(),
        persistence_enabled=persistence_enabled,
    )
    return measurement_service, jacket, injection, daq, writer


def test_sample_calibrates_and_tracks_injected_volume() -> None:
    measurement_service, jacket, injection, _, writer = service()
    first = measurement_service.sample_once(active_stage="water", valve_percent=25.0)
    injection.remaining_volume_ml = 247.5
    jacket.remaining_volume_ml = 258.0
    second = measurement_service.sample_once(active_stage="water", valve_percent=25.0)

    assert first.snapshot.line_pressure_bar == pytest.approx(100.0)
    assert first.snapshot.differential_pressure_bar == pytest.approx(5.0)
    assert second.injected_volume_ml == pytest.approx(2.5)
    assert second.injection_net_volume_ml == pytest.approx(2.5)
    assert second.jacket_net_volume_ml == pytest.approx(2.0)
    assert writer.records == [first, second]

    injection.remaining_volume_ml = 252.0
    jacket.remaining_volume_ml = 263.0
    reversed_flow = measurement_service.sample_once(
        active_stage="water", valve_percent=25.0
    )
    assert reversed_flow.injection_net_volume_ml == pytest.approx(-2.0)
    assert reversed_flow.jacket_net_volume_ml == pytest.approx(-3.0)

    measurement_service.reset_injected_volume_tracking()
    restarted = measurement_service.sample_once(active_stage="water", valve_percent=25.0)
    assert restarted.injected_volume_ml == pytest.approx(0.0)
    assert restarted.jacket_net_volume_ml == pytest.approx(0.0)


def test_disabled_pump_is_not_read_and_does_not_create_a_safety_fault() -> None:
    injection = SimulatedPump(pressure_bar=100.0, remaining_volume_ml=250.0)
    injection.connect()
    daq = SimulatedDataAcquisition()
    daq.inputs["differential_pressure"] = 1.5
    measurement = MeasurementService(
        jacket_pump=DisabledPump("jacket"),
        injection_pump=injection,
        daq=daq,
        line_calibration=LinearCalibration(1.0, 5.0, 0.0, 400.0),
        differential_calibration=LinearCalibration(1.0, 5.0, 0.0, 40.0),
        safety_monitor=SafetyMonitor(SafetyLimits(350.0, 350.0, 50.0)),
        writer=MemoryWriter(),
        clock=FakeClock(),
        channels=MeasurementChannels(line_pressure=None),
        enabled_pumps=frozenset({"injection"}),
    )

    record = measurement.sample_once(active_stage="water", valve_percent=25.0)

    assert not record.snapshot.jacket_pump.connected
    assert record.snapshot.quality is DataQuality.GOOD
    assert record.safety_reasons == ()


def test_non_persistent_control_sample_is_not_written() -> None:
    measurement_service, _, _, _, writer = service()

    record = measurement_service.sample_once(
        active_stage="water", valve_percent=25.0, persist=False
    )

    assert record.active_stage == "water"
    assert writer.records == []


def test_explicitly_disabled_persistence_never_writes_records() -> None:
    measurement_service, _, _, _, writer = service(persistence_enabled=False)

    record = measurement_service.sample_once(
        active_stage="simulation", valve_percent=25.0, persist=True
    )

    assert record.active_stage == "simulation"
    assert writer.records == []


def test_normal_measurement_does_not_enforce_startup_pressure_margin() -> None:
    measurement_service, jacket, injection, daq, writer = service(jacket_pressure=119.0)

    record = measurement_service.sample_once(active_stage="water", valve_percent=25.0)

    assert record.safety_reasons == ()
    assert writer.records == [record]
    assert not jacket.stop_requested
    assert not injection.stop_requested
    assert not daq.safe_state_requested


def test_explicit_startup_margin_interlock_requests_safe_state() -> None:
    measurement_service, jacket, injection, daq, writer = service(jacket_pressure=119.0)

    record = measurement_service.sample_once(
        active_stage="startup",
        valve_percent=25.0,
        enforce_minimum_margin=True,
    )

    assert record.safety_reasons == ("jacket pressure margin is too low",)
    assert writer.records == [record]
    assert not jacket.stop_requested
    assert injection.stop_requested
    assert daq.safe_state_requested


def test_disconnected_optional_line_sensor_does_not_stop_pump_pressure_control() -> None:
    measurement_service, jacket, injection, daq, _ = service()
    del daq.inputs["line_pressure"]

    record = measurement_service.sample_once(active_stage="water", valve_percent=25.0)

    assert record.snapshot.line_pressure_quality is DataQuality.DISCONNECTED
    assert record.safety_reasons == ()
    assert not jacket.stop_requested
    assert not injection.stop_requested
    assert not daq.safe_state_requested


def test_disconnected_selected_line_source_requests_latched_safe_state() -> None:
    measurement_service, jacket, injection, daq, _ = service()
    del daq.inputs["line_pressure"]

    record = measurement_service.sample_once(
        active_stage="water",
        valve_percent=25.0,
        use_line_pressure_for_control=True,
    )

    assert any(
        "line pressure source quality is disconnected" in reason
        for reason in record.safety_reasons
    )
    assert not jacket.stop_requested
    assert injection.stop_requested
    assert daq.safe_state_requested


def test_out_of_range_finite_sensor_voltage_is_preserved_without_safe_state() -> None:
    measurement_service, _, _, daq, _ = service()
    daq.inputs["line_pressure"] = 0.9

    record = measurement_service.sample_once(active_stage="water", valve_percent=0.0)

    assert record.snapshot.raw_line_voltage == pytest.approx(0.9)
    assert record.snapshot.line_pressure_bar == pytest.approx(-10.0)
    assert record.snapshot.line_pressure_quality is DataQuality.GOOD
    assert record.safety_reasons == ()
    assert not daq.safe_state_requested


def test_electrically_impossible_line_voltage_is_out_of_range() -> None:
    measurement_service, _, _, daq, _ = service()
    daq.inputs["line_pressure"] = 0.4

    record = measurement_service.sample_once(active_stage="water", valve_percent=0.0)

    assert record.snapshot.line_pressure_quality is DataQuality.OUT_OF_RANGE
    assert "electrical limits" in record.snapshot.line_pressure_quality_reason


def test_out_of_range_line_signal_is_not_a_pump_source_overpressure() -> None:
    measurement_service, jacket, injection, daq, _ = service()
    daq.inputs["line_pressure"] = 6.0

    record = measurement_service.sample_once(active_stage="water", valve_percent=0.0)

    assert record.snapshot.raw_line_pressure_bar == pytest.approx(500.0)
    assert record.snapshot.line_pressure_quality is DataQuality.OUT_OF_RANGE
    assert record.safety_reasons == ()
    assert not jacket.stop_requested
    assert not injection.stop_requested
    assert not daq.safe_state_requested


def test_non_finite_line_voltage_is_invalid() -> None:
    measurement_service, _, _, daq, _ = service()
    daq.inputs["line_pressure"] = float("nan")

    record = measurement_service.sample_once(active_stage="water", valve_percent=0.0)

    assert record.snapshot.line_pressure_quality is DataQuality.INVALID


def test_differential_voltage_uses_the_same_quality_pipeline() -> None:
    measurement_service, _, _, daq, _ = service()
    daq.inputs["differential_pressure"] = 0.9

    valid = measurement_service.sample_once(active_stage="water", valve_percent=0.0)

    assert valid.snapshot.raw_differential_voltage == pytest.approx(0.9)
    assert valid.snapshot.raw_differential_pressure_bar == pytest.approx(-1.0)
    assert valid.snapshot.differential_pressure_quality is DataQuality.GOOD


def test_electrically_impossible_differential_voltage_requests_safe_state() -> None:
    measurement_service, jacket, injection, daq, _ = service()
    daq.inputs["differential_pressure"] = 0.4

    record = measurement_service.sample_once(active_stage="water", valve_percent=0.0)

    assert record.snapshot.differential_pressure_quality is DataQuality.OUT_OF_RANGE
    assert "electrical limits" in (
        record.snapshot.differential_pressure_quality_reason
    )
    assert not jacket.stop_requested
    assert injection.stop_requested
    assert daq.safe_state_requested


def test_differential_physical_range_is_independent_from_electrical_range() -> None:
    measurement_service, _, _, daq, _ = service()
    measurement_service._analog_filter_config = replace(
        measurement_service._analog_filter_config,
        differential_electrical_max_voltage=10.0,
        differential_physical_max_pressure_bar=45.0,
    )
    daq.inputs["differential_pressure"] = 6.0

    record = measurement_service.sample_once(active_stage="water", valve_percent=0.0)

    assert record.snapshot.raw_differential_pressure_bar == pytest.approx(50.0)
    assert record.snapshot.differential_pressure_quality is DataQuality.OUT_OF_RANGE
    assert "physical limits" in (
        record.snapshot.differential_pressure_quality_reason
    )


def test_missing_differential_sensor_is_disconnected_and_latched() -> None:
    measurement_service, jacket, injection, daq, _ = service()
    del daq.inputs["differential_pressure"]

    record = measurement_service.sample_once(active_stage="water", valve_percent=0.0)

    assert record.snapshot.differential_pressure_quality is DataQuality.DISCONNECTED
    assert any(
        "differential pressure quality is disconnected" in reason
        for reason in record.safety_reasons
    )
    assert not jacket.stop_requested
    assert injection.stop_requested
    assert daq.safe_state_requested


def test_service_telemetry_keeps_valid_sensor_when_other_sensor_is_missing() -> None:
    measurement_service, _, _, daq, _ = service()
    del daq.inputs["differential_pressure"]

    values, errors = measurement_service.read_pressure_inputs_individually()

    assert values["line_pressure"] == pytest.approx(100.0)
    assert "differential_pressure" not in values
    assert "differential_pressure" in errors


def test_measurement_runs_without_optional_line_pressure_sensor() -> None:
    measurement_service, _, _, daq, writer = service()
    del daq.inputs["line_pressure"]
    measurement_service._channels = MeasurementChannels(line_pressure=None)

    record = measurement_service.sample_once(active_stage="water", valve_percent=0.0)
    values, errors = measurement_service.read_pressure_inputs_individually()

    assert record.snapshot.line_pressure_bar is None
    assert record.snapshot.differential_pressure_bar == pytest.approx(5.0)
    assert record.safety_reasons == ()
    assert writer.records == [record]
    assert "line_pressure" not in values
    assert "line_pressure" not in errors


def test_snapshot_keeps_raw_pressure_while_pid_value_is_filtered() -> None:
    measurement_service, _, _, _, _ = service()

    class BurstDaq(SimulatedDataAcquisition):
        line_bursts = iter(([2.0] * 20, [3.0] * 20))

        def read_voltages(self, channel: str, number_of_samples: int) -> list[float]:
            assert number_of_samples == 20
            if channel == "line_pressure":
                return list(next(self.line_bursts))
            return [1.5] * number_of_samples

    measurement_service._daq = BurstDaq()
    first = measurement_service.sample_once(active_stage="water", valve_percent=0.0)
    second = measurement_service.sample_once(active_stage="water", valve_percent=0.0)

    assert first.snapshot.raw_line_pressure_bar == pytest.approx(100.0)
    assert first.snapshot.line_pressure_bar == pytest.approx(100.0)
    assert second.snapshot.raw_line_pressure_bar == pytest.approx(200.0)
    assert second.snapshot.line_pressure_bar == pytest.approx(100.0)


def test_snapshot_distinguishes_last_raw_median_and_filtered_voltage() -> None:
    measurement_service, _, _, _, _ = service()

    class BurstDaq(SimulatedDataAcquisition):
        def read_voltages(self, channel: str, number_of_samples: int) -> list[float]:
            if channel == "line_pressure":
                return [2.0] * (number_of_samples - 1) + [4.0]
            return [1.5] * number_of_samples

    measurement_service._daq = BurstDaq()
    snapshot = measurement_service.sample_once(
        active_stage="water", valve_percent=0.0
    ).snapshot
    reading = snapshot.line_pressure_reading

    assert reading is not None
    assert reading.last_raw_voltage == pytest.approx(4.0)
    assert reading.median_voltage == pytest.approx(2.0)
    assert reading.filtered_voltage == pytest.approx(2.0)
    assert reading.raw_pressure_bar == pytest.approx(100.0)


def test_slow_selected_line_sample_is_stale_and_requests_safe_state() -> None:
    measurement_service, jacket, injection, daq, _ = service()
    clock = measurement_service._clock

    class SlowDaq(SimulatedDataAcquisition):
        def read_voltages(self, channel: str, number_of_samples: int) -> list[float]:
            if channel == "line_pressure":
                clock.current += 1.1
            return [self.inputs[channel]] * number_of_samples

    slow_daq = SlowDaq()
    slow_daq.inputs.update(daq.inputs)
    measurement_service._daq = slow_daq
    record = measurement_service.sample_once(
        active_stage="water",
        valve_percent=0.0,
        use_line_pressure_for_control=True,
    )

    assert record.snapshot.line_pressure_quality is DataQuality.STALE
    assert not jacket.stop_requested
    assert injection.stop_requested
    assert slow_daq.safe_state_requested


def test_slow_differential_sample_is_stale_and_requests_safe_state() -> None:
    measurement_service, jacket, injection, daq, _ = service()
    clock = measurement_service._clock

    class SlowDaq(SimulatedDataAcquisition):
        def read_voltages(self, channel: str, number_of_samples: int) -> list[float]:
            if channel == "differential_pressure":
                clock.current += 1.1
            return [self.inputs[channel]] * number_of_samples

    slow_daq = SlowDaq()
    slow_daq.inputs.update(daq.inputs)
    measurement_service._daq = slow_daq
    record = measurement_service.sample_once(
        active_stage="water", valve_percent=0.0
    )

    assert record.snapshot.differential_pressure_quality is DataQuality.STALE
    assert not jacket.stop_requested
    assert injection.stop_requested
    assert slow_daq.safe_state_requested


@pytest.mark.parametrize("interval", [0.9, 3600.1])
def test_measurement_interval_is_limited(interval: float) -> None:
    measurement_service, *_ = service()

    with pytest.raises(ValueError, match="between 1 second and 1 hour"):
        measurement_service.run(
            Event(), interval_seconds=interval, active_stage="water", valve_percent=25.0
        )
