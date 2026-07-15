from dataclasses import dataclass
from enum import StrEnum
from math import isfinite

from eor_control.domain import MeasurementSnapshot
from eor_control.safety import SafetyDecision


class ControlMode(StrEnum):
    MANUAL = "manual"
    AUTOMATIC = "automatic"


class PressureSource(StrEnum):
    INJECTION_PUMP = "injection_pump"
    LINE_SENSOR = "line_sensor"


class ControlDirection(StrEnum):
    DIRECT = "direct"
    REVERSE = "reverse"


@dataclass(frozen=True, slots=True)
class PidParameters:
    proportional_gain: float
    integral_gain: float
    derivative_gain: float
    output_min_percent: float = 0.0
    output_max_percent: float = 100.0
    direction: ControlDirection = ControlDirection.DIRECT

    def __post_init__(self) -> None:
        values = (
            self.proportional_gain,
            self.integral_gain,
            self.derivative_gain,
            self.output_min_percent,
            self.output_max_percent,
        )
        if not all(isfinite(value) for value in values):
            raise ValueError("PID parameters must be finite")
        if min(self.proportional_gain, self.integral_gain, self.derivative_gain) < 0.0:
            raise ValueError("PID gains must not be negative")
        if not 0.0 <= self.output_min_percent < self.output_max_percent <= 100.0:
            raise ValueError("PID output limits must be ordered within 0 to 100 percent")


@dataclass(frozen=True, slots=True)
class ValveCommand:
    enabled: bool
    output_percent: float | None
    mode: ControlMode
    source: PressureSource | None
    reason: str | None = None


class PidController:
    def __init__(self, parameters: PidParameters) -> None:
        self._parameters = parameters
        self._integral = 0.0
        self._previous_measurement: float | None = None

    def reset(self, *, output_percent: float = 0.0) -> None:
        self._integral = self._clamp(output_percent)
        self._previous_measurement = None

    def configure(self, parameters: PidParameters, *, current_output_percent: float) -> None:
        self._parameters = parameters
        self.reset(output_percent=current_output_percent)

    def calculate(self, *, setpoint: float, measurement: float, dt_seconds: float) -> float:
        if not all(isfinite(value) for value in (setpoint, measurement, dt_seconds)):
            raise ValueError("PID inputs must be finite")
        if dt_seconds <= 0.0:
            raise ValueError("PID time step must be positive")

        direction = 1.0 if self._parameters.direction is ControlDirection.DIRECT else -1.0
        error = direction * (setpoint - measurement)
        proportional = self._parameters.proportional_gain * error
        derivative = 0.0
        if self._previous_measurement is not None:
            measurement_rate = (measurement - self._previous_measurement) / dt_seconds
            derivative = -direction * self._parameters.derivative_gain * measurement_rate

        integral_candidate = (
            self._integral + self._parameters.integral_gain * error * dt_seconds
        )
        unconstrained = proportional + integral_candidate + derivative
        output = self._clamp(unconstrained)

        pushing_above_limit = (
            unconstrained > self._parameters.output_max_percent and error > 0.0
        )
        pushing_below_limit = (
            unconstrained < self._parameters.output_min_percent and error < 0.0
        )
        if not pushing_above_limit and not pushing_below_limit:
            self._integral = integral_candidate

        self._previous_measurement = measurement
        return output

    def _clamp(self, output: float) -> float:
        return min(
            self._parameters.output_max_percent,
            max(self._parameters.output_min_percent, output),
        )


class ValveController:
    def __init__(self, pid: PidController) -> None:
        self._pid = pid

    def configure_pid(
        self, parameters: PidParameters, *, current_output_percent: float
    ) -> None:
        self._pid.configure(parameters, current_output_percent=current_output_percent)

    def command(
        self,
        *,
        snapshot: MeasurementSnapshot,
        safety: SafetyDecision,
        mode: ControlMode,
        manual_output_percent: float | None = None,
        source: PressureSource | None = None,
        setpoint_bar: float | None = None,
        dt_seconds: float | None = None,
    ) -> ValveCommand:
        if not safety.safe:
            return ValveCommand(
                enabled=False,
                output_percent=None,
                mode=mode,
                source=source,
                reason="safety interlock active",
            )

        if mode is ControlMode.MANUAL:
            if manual_output_percent is None or not isfinite(manual_output_percent):
                raise ValueError("manual output must be a finite percentage")
            if not 0.0 <= manual_output_percent <= 100.0:
                raise ValueError("manual output must be between 0 and 100 percent")
            return ValveCommand(True, manual_output_percent, mode, None)

        if source is None or setpoint_bar is None or dt_seconds is None:
            raise ValueError("automatic mode requires source, setpoint and time step")
        measurements = {
            PressureSource.INJECTION_PUMP: snapshot.injection_pump.pressure_bar,
            PressureSource.LINE_SENSOR: snapshot.line_pressure_bar,
        }
        measurement = measurements[source]
        output = self._pid.calculate(
            setpoint=setpoint_bar, measurement=measurement, dt_seconds=dt_seconds
        )
        return ValveCommand(True, output, mode, source)
