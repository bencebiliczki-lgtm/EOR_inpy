from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Event

import pytest

from eor_control.calibration import LinearCalibration
from eor_control.domain import MeasurementRecord
from eor_control.measurement import MeasurementService
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
    *, jacket_pressure: float = 120.0, injection_pressure: float = 100.0
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
    daq.inputs.update(line_pressure=2.0, differential_pressure=1.5, inlet_pressure=2.5)
    writer = MemoryWriter()
    calibration = LinearCalibration(1.0, 5.0, 0.0, 400.0)
    measurement_service = MeasurementService(
        jacket_pump=jacket,
        injection_pump=injection,
        daq=daq,
        line_calibration=calibration,
        differential_calibration=LinearCalibration(1.0, 5.0, 0.0, 40.0),
        safety_monitor=SafetyMonitor(SafetyLimits(400.0, 350.0, 50.0)),
        writer=writer,
        clock=FakeClock(),
    )
    return measurement_service, jacket, injection, daq, writer


def test_sample_calibrates_and_tracks_injected_volume() -> None:
    measurement_service, _, injection, _, writer = service()
    first = measurement_service.sample_once(active_stage="water", valve_percent=25.0)
    injection.remaining_volume_ml = 247.5
    second = measurement_service.sample_once(active_stage="water", valve_percent=25.0)

    assert first.snapshot.line_pressure_bar == pytest.approx(100.0)
    assert first.snapshot.differential_pressure_bar == pytest.approx(5.0)
    assert first.snapshot.inlet_pressure_bar == pytest.approx(150.0)
    assert second.injected_volume_ml == pytest.approx(2.5)
    assert writer.records == [first, second]


def test_non_persistent_control_sample_is_not_written() -> None:
    measurement_service, _, _, _, writer = service()

    record = measurement_service.sample_once(
        active_stage="water", valve_percent=25.0, persist=False
    )

    assert record.active_stage == "water"
    assert writer.records == []


def test_safety_interlock_requests_safe_state_after_recording() -> None:
    measurement_service, jacket, injection, daq, writer = service(jacket_pressure=119.0)

    record = measurement_service.sample_once(active_stage="water", valve_percent=25.0)

    assert record.safety_reasons == ("jacket pressure margin is too low",)
    assert writer.records == [record]
    assert jacket.stop_requested
    assert injection.stop_requested
    assert daq.safe_state_requested


def test_connection_error_requests_safe_state() -> None:
    measurement_service, jacket, injection, daq, _ = service()
    del daq.inputs["line_pressure"]

    with pytest.raises(ConnectionError):
        measurement_service.run(
            Event(), interval_seconds=1.0, active_stage="water", valve_percent=25.0
        )

    assert jacket.stop_requested
    assert injection.stop_requested
    assert daq.safe_state_requested


@pytest.mark.parametrize("interval", [0.9, 3600.1])
def test_measurement_interval_is_limited(interval: float) -> None:
    measurement_service, *_ = service()

    with pytest.raises(ValueError, match="between 1 second and 1 hour"):
        measurement_service.run(
            Event(), interval_seconds=interval, active_stage="water", valve_percent=25.0
        )
