from collections import deque
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
    deadband_bar: float = 0.0
    maximum_output_rate_percent_per_second: float = 1000.0
    measurement_filter_alpha: float = 1.0
    minimum_reversal_interval_seconds: float = 1.0
    reversal_deadband_percent: float = 0.5
    maximum_reversals: int = 6
    reversal_window_seconds: float = 10.0

    def __post_init__(self) -> None:
        values = (
            self.proportional_gain,
            self.integral_gain,
            self.derivative_gain,
            self.output_min_percent,
            self.output_max_percent,
            self.deadband_bar,
            self.maximum_output_rate_percent_per_second,
            self.measurement_filter_alpha,
            self.minimum_reversal_interval_seconds,
            self.reversal_deadband_percent,
            self.reversal_window_seconds,
        )
        if not all(isfinite(value) for value in values):
            raise ValueError("PID parameters must be finite")
        if min(self.proportional_gain, self.integral_gain, self.derivative_gain) < 0.0:
            raise ValueError("PID gains must not be negative")
        if not 0.0 <= self.output_min_percent < self.output_max_percent <= 100.0:
            raise ValueError("PID output limits must be ordered within 0 to 100 percent")
        if self.deadband_bar < 0.0:
            raise ValueError("PID deadband must be nonnegative")
        if self.maximum_output_rate_percent_per_second <= 0.0:
            raise ValueError("PID output rate limit must be positive")
        if not 0.0 < self.measurement_filter_alpha <= 1.0:
            raise ValueError("PID filter alpha must be within (0, 1]")
        if self.minimum_reversal_interval_seconds < 0.0:
            raise ValueError("PID reversal interval must be nonnegative")
        if self.reversal_deadband_percent < 0.0:
            raise ValueError("PID reversal deadband must be nonnegative")
        if self.maximum_reversals < 1 or self.reversal_window_seconds <= 0.0:
            raise ValueError("PID reversal supervision limits are invalid")


@dataclass(frozen=True, slots=True)
class ValveCommand:
    enabled: bool
    output_percent: float | None
    mode: ControlMode
    source: PressureSource | None
    reason: str | None = None


class ValveOscillationError(RuntimeError):
    pass


class PidController:
    def __init__(self, parameters: PidParameters) -> None:
        self._parameters = parameters
        self._integral = 0.0
        self._previous_measurement: float | None = None
        self._filtered_measurement: float | None = None
        self._last_output = 0.0
        self._elapsed_seconds = 0.0
        self._last_direction = 0
        self._last_reversal_seconds = float("-inf")
        self._reversals: deque[float] = deque()

    def reset(self, *, output_percent: float = 0.0) -> None:
        self._integral = self._clamp(output_percent)
        self._previous_measurement = None
        self._filtered_measurement = None
        self._last_output = self._clamp(output_percent)
        self._elapsed_seconds = 0.0
        self._last_direction = 0
        self._last_reversal_seconds = float("-inf")
        self._reversals.clear()

    def configure(self, parameters: PidParameters, *, current_output_percent: float) -> None:
        self._parameters = parameters
        self.reset(output_percent=current_output_percent)

    def calculate(self, *, setpoint: float, measurement: float, dt_seconds: float) -> float:
        if not all(isfinite(value) for value in (setpoint, measurement, dt_seconds)):
            raise ValueError("PID inputs must be finite")
        if dt_seconds <= 0.0:
            raise ValueError("PID time step must be positive")

        self._elapsed_seconds += dt_seconds
        alpha = self._parameters.measurement_filter_alpha
        filtered = (
            measurement
            if self._filtered_measurement is None
            else alpha * measurement + (1.0 - alpha) * self._filtered_measurement
        )
        self._filtered_measurement = filtered
        direction = 1.0 if self._parameters.direction is ControlDirection.DIRECT else -1.0
        error = direction * (setpoint - filtered)
        if abs(error) <= self._parameters.deadband_bar:
            self._previous_measurement = filtered
            return self._last_output
        proportional = self._parameters.proportional_gain * error
        derivative = 0.0
        if self._previous_measurement is not None:
            measurement_rate = (filtered - self._previous_measurement) / dt_seconds
            derivative = -direction * self._parameters.derivative_gain * measurement_rate

        integral_candidate = (
            self._integral + self._parameters.integral_gain * error * dt_seconds
        )
        unconstrained = proportional + integral_candidate + derivative
        constrained = self._clamp(unconstrained)
        max_delta = (
            self._parameters.maximum_output_rate_percent_per_second * dt_seconds
        )
        output = min(
            self._last_output + max_delta,
            max(self._last_output - max_delta, constrained),
        )
        output = self._apply_reversal_protection(output)

        pushing_above_limit = (
            unconstrained > self._parameters.output_max_percent and error > 0.0
        )
        pushing_below_limit = (
            unconstrained < self._parameters.output_min_percent and error < 0.0
        )
        rate_limited = output != constrained
        if not pushing_above_limit and not pushing_below_limit and not rate_limited:
            self._integral = integral_candidate

        self._previous_measurement = filtered
        self._last_output = output
        return output

    def track_output(self, output_percent: float) -> None:
        self._last_output = self._clamp(output_percent)
        self._integral = self._last_output

    def prepare_bumpless(
        self, *, setpoint: float, measurement: float, output_percent: float
    ) -> None:
        direction = 1.0 if self._parameters.direction is ControlDirection.DIRECT else -1.0
        error = direction * (setpoint - measurement)
        proportional = self._parameters.proportional_gain * error
        self._integral = self._clamp(output_percent) - proportional
        self._previous_measurement = measurement
        self._filtered_measurement = measurement
        self._last_output = self._clamp(output_percent)

    def _apply_reversal_protection(self, candidate: float) -> float:
        delta = candidate - self._last_output
        if abs(delta) <= 1e-12:
            return self._last_output
        direction = 1 if delta > 0.0 else -1
        reversing = self._last_direction != 0 and direction != self._last_direction
        if reversing:
            if abs(delta) <= self._parameters.reversal_deadband_percent:
                return self._last_output
            since_reversal = self._elapsed_seconds - self._last_reversal_seconds
            if since_reversal < self._parameters.minimum_reversal_interval_seconds:
                return self._last_output
            self._last_reversal_seconds = self._elapsed_seconds
            self._reversals.append(self._elapsed_seconds)
            cutoff = self._elapsed_seconds - self._parameters.reversal_window_seconds
            while self._reversals and self._reversals[0] < cutoff:
                self._reversals.popleft()
            if len(self._reversals) > self._parameters.maximum_reversals:
                raise ValveOscillationError("VALVE_OSCILLATION")
        self._last_direction = direction
        return candidate

    def _clamp(self, output: float) -> float:
        return min(
            self._parameters.output_max_percent,
            max(self._parameters.output_min_percent, output),
        )


class ValveController:
    def __init__(self, pid: PidController) -> None:
        self._pid = pid
        self._last_mode: ControlMode | None = None
        self._last_manual_output = 0.0

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
            self._pid.track_output(manual_output_percent)
            self._last_manual_output = manual_output_percent
            self._last_mode = mode
            return ValveCommand(True, manual_output_percent, mode, None)

        if source is None or setpoint_bar is None or dt_seconds is None:
            raise ValueError("automatic mode requires source, setpoint and time step")
        measurements = {
            PressureSource.INJECTION_PUMP: snapshot.injection_pump.pressure_bar,
            PressureSource.LINE_SENSOR: snapshot.line_pressure_bar,
        }
        measurement = measurements[source]
        if measurement is None:
            raise ValueError("the selected pressure source is not configured")
        transition_reason = None
        if self._last_mode is ControlMode.MANUAL:
            self._pid.prepare_bumpless(
                setpoint=setpoint_bar,
                measurement=measurement,
                output_percent=self._last_manual_output,
            )
            transition_reason = "bumpless manual-to-automatic transfer"
        output = self._pid.calculate(
            setpoint=setpoint_bar, measurement=measurement, dt_seconds=dt_seconds
        )
        self._last_mode = mode
        return ValveCommand(True, output, mode, source, transition_reason)
