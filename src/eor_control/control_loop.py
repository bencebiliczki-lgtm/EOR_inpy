from dataclasses import dataclass

from eor_control.calibration import LinearCalibration
from eor_control.control import (
    ControlMode,
    PidParameters,
    PressureSource,
    ValveCommand,
    ValveController,
)
from eor_control.devices import ValveActuator
from eor_control.domain import MeasurementRecord
from eor_control.measurement import MeasurementService
from eor_control.safety import SafetyDecision, SafetyLimits


@dataclass(frozen=True, slots=True)
class ControlCycleResult:
    record: MeasurementRecord
    command: ValveCommand


class ControlLoop:
    def __init__(
        self,
        *,
        measurement: MeasurementService,
        controller: ValveController,
        actuator: ValveActuator,
        initial_output_percent: float = 0.0,
    ) -> None:
        if not 0.0 <= initial_output_percent <= 100.0:
            raise ValueError("initial valve output must be between 0 and 100 percent")
        self._measurement = measurement
        self._controller = controller
        self._actuator = actuator
        self._last_output_percent = initial_output_percent

    def execute_once(
        self,
        *,
        active_stage: str,
        mode: ControlMode,
        dt_seconds: float,
        manual_output_percent: float | None = None,
        source: PressureSource | None = None,
        setpoint_bar: float | None = None,
        persist: bool = True,
        control_deadline_missed: bool = False,
    ) -> ControlCycleResult:
        record = self._measurement.sample_once(
            active_stage=active_stage,
            valve_percent=self._last_output_percent,
            persist=persist,
            control_deadline_missed=control_deadline_missed,
            pressure_target_bar=(
                setpoint_bar if mode is ControlMode.AUTOMATIC else None
            ),
            use_line_pressure_for_control=source is PressureSource.LINE_SENSOR,
        )
        safety = SafetyDecision(
            safe=not record.safety_reasons,
            reasons=record.safety_reasons,
            latched=bool(record.safety_reasons),
        )
        command = self._controller.command(
            snapshot=record.snapshot,
            safety=safety,
            mode=mode,
            manual_output_percent=manual_output_percent,
            source=source,
            setpoint_bar=setpoint_bar,
            dt_seconds=dt_seconds,
        )
        if not command.enabled or command.output_percent is None:
            self._actuator.set_safe_state()
        else:
            self._actuator.write_percent(command.output_percent)
            self._last_output_percent = command.output_percent
        return ControlCycleResult(record=record, command=command)

    def configure_pid(self, parameters: PidParameters) -> None:
        self._controller.configure_pid(
            parameters, current_output_percent=self._last_output_percent
        )

    def observe_once(self, *, active_stage: str) -> MeasurementRecord:
        """Acquire a non-persistent, safety-supervised telemetry snapshot."""
        return self._measurement.sample_once(
            active_stage=active_stage,
            valve_percent=self._last_output_percent,
            persist=False,
        )

    def configure_measurement(
        self,
        *,
        line_calibration: LinearCalibration,
        differential_calibration: LinearCalibration,
        safety_limits: SafetyLimits,
    ) -> None:
        self._measurement.configure_measurement(
            line_calibration=line_calibration,
            differential_calibration=differential_calibration,
            safety_limits=safety_limits,
        )

    def close(self) -> None:
        self._actuator.set_safe_state()
        self._measurement.close()

    def reset_injected_volume_tracking(self) -> None:
        self._measurement.reset_injected_volume_tracking()

    def request_safe_state(self) -> None:
        self._actuator.set_safe_state()
        self._last_output_percent = 0.0
        self._measurement.request_safe_state()
