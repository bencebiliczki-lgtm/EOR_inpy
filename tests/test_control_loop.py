from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from eor_control.calibration import LinearCalibration
from eor_control.control import (
    ControlMode,
    PidController,
    PidParameters,
    PressureSource,
    ValveController,
)
from eor_control.control_loop import ControlLoop
from eor_control.domain import MeasurementRecord
from eor_control.measurement import MeasurementService
from eor_control.safety import SafetyLimits, SafetyMonitor
from eor_control.simulators import (
    SimulatedDataAcquisition,
    SimulatedPump,
    SimulatedValveActuator,
)


@dataclass
class MemoryWriter:
    records: list[MeasurementRecord] = field(default_factory=list)

    def write(self, record: MeasurementRecord) -> None:
        self.records.append(record)

    def close(self) -> None:
        pass


@dataclass
class FakeClock:
    def utc_now(self) -> datetime:
        return datetime(2026, 7, 13, tzinfo=UTC)

    def monotonic(self) -> float:
        return 10.0

    def sleep(self, seconds: float) -> None:
        pass


def loop(
    *, jacket_pressure: float = 120.0
) -> tuple[ControlLoop, SimulatedValveActuator, MemoryWriter]:
    jacket = SimulatedPump(pressure_bar=jacket_pressure)
    injection = SimulatedPump(pressure_bar=100.0, remaining_volume_ml=250.0)
    jacket.connect()
    injection.connect()
    daq = SimulatedDataAcquisition()
    daq.inputs.update(line_pressure=2.0, differential_pressure=1.5)
    writer = MemoryWriter()
    calibration = LinearCalibration(1.0, 5.0, 0.0, 400.0)
    measurement = MeasurementService(
        jacket_pump=jacket,
        injection_pump=injection,
        daq=daq,
        line_calibration=calibration,
        differential_calibration=LinearCalibration(1.0, 5.0, 0.0, 40.0),
        safety_monitor=SafetyMonitor(SafetyLimits(400.0, 350.0, 50.0)),
        writer=writer,
        clock=FakeClock(),
    )
    actuator = SimulatedValveActuator()
    return (
        ControlLoop(
            measurement=measurement,
            controller=ValveController(PidController(PidParameters(1.0, 0.0, 0.0))),
            actuator=actuator,
        ),
        actuator,
        writer,
    )


def test_manual_cycle_records_then_writes_valve_output() -> None:
    control_loop, actuator, writer = loop()

    result = control_loop.execute_once(
        active_stage="water",
        mode=ControlMode.MANUAL,
        dt_seconds=0.1,
        manual_output_percent=35.0,
    )

    assert writer.records == [result.record]
    assert result.record.snapshot.valve_percent == 0.0
    assert actuator.output_percent == 35.0


def test_automatic_cycle_uses_calibrated_line_pressure() -> None:
    control_loop, actuator, _ = loop()

    result = control_loop.execute_once(
        active_stage="water",
        mode=ControlMode.AUTOMATIC,
        dt_seconds=0.1,
        source=PressureSource.LINE_SENSOR,
        setpoint_bar=120.0,
    )

    assert result.record.snapshot.line_pressure_bar == pytest.approx(100.0)
    assert actuator.output_percent == pytest.approx(20.0)


def test_interlock_suppresses_output_and_requests_safe_actuator_state() -> None:
    control_loop, actuator, writer = loop(jacket_pressure=119.0)

    result = control_loop.execute_once(
        active_stage="water",
        mode=ControlMode.MANUAL,
        dt_seconds=0.1,
        manual_output_percent=35.0,
    )

    assert writer.records == [result.record]
    assert not result.command.enabled
    assert actuator.output_percent is None
    assert actuator.safe_state_requested


def test_separate_manual_output_safety_does_not_require_measurement_snapshot() -> None:
    control_loop, actuator, writer = loop(jacket_pressure=119.0)

    result = control_loop.write_manual_output(35.0)

    assert result == 35.0
    assert actuator.output_percent == 35.0
    assert writer.records == []
