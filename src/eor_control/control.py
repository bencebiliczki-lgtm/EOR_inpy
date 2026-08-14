from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from math import exp, isfinite

from eor_control.domain import DataQuality, MeasurementSnapshot
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


class PidState(StrEnum):
    MANUAL = "MANUAL"
    INITIALIZING = "INITIALIZING"
    ACTIVE = "ACTIVE"
    DEADBAND = "DEADBAND"
    HOLD = "HOLD"
    BLOCKED = "BLOCKED"
    SAFE = "SAFE"
    FAULT = "FAULT"


@dataclass(frozen=True, slots=True)
class PressureMeasurement:
    source: PressureSource
    raw_value_bar: float
    filtered_value_bar: float
    timestamp_monotonic: float
    age_seconds: float
    sequence: int
    quality: DataQuality
    last_error: str = ""


@dataclass(frozen=True, slots=True)
class PidParameters:
    proportional_gain: float
    integral_gain: float
    derivative_gain: float
    output_min_percent: float = 0.0
    output_max_percent: float = 100.0
    # The physical valve uses 0% = closed and 100% = open. Increasing the
    # opening lowers injection pressure, so the safe application default is
    # reverse acting.
    direction: ControlDirection = ControlDirection.REVERSE
    deadband_bar: float = 0.0
    maximum_output_rate_percent_per_second: float = 1000.0
    measurement_filter_alpha: float = 1.0
    minimum_reversal_interval_seconds: float = 1.0
    reversal_deadband_percent: float = 0.5
    maximum_reversals: int = 6
    reversal_window_seconds: float = 10.0
    measurement_filter_enabled: bool = True
    measurement_filter_time_constant_seconds: float = 0.0
    deadband_exit_bar: float = 0.0
    integral_min_percent: float = -100.0
    integral_max_percent: float = 100.0
    maximum_pid_sample_interval_seconds: float = 2.0
    pump_pid_input_max_age_seconds: float = 2.0
    line_pid_input_max_age_seconds: float = 1.0

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
            self.measurement_filter_time_constant_seconds,
            self.deadband_exit_bar,
            self.integral_min_percent,
            self.integral_max_percent,
            self.maximum_pid_sample_interval_seconds,
            self.pump_pid_input_max_age_seconds,
            self.line_pid_input_max_age_seconds,
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
        if self.measurement_filter_time_constant_seconds < 0.0:
            raise ValueError("PID filter time constant must be nonnegative")
        if self.deadband_exit_bar != 0.0 and self.deadband_exit_bar < self.deadband_bar:
            raise ValueError("PID deadband exit must not be below its entry value")
        if self.integral_min_percent >= self.integral_max_percent:
            raise ValueError("PID integral limits must be ordered")
        if min(
            self.maximum_pid_sample_interval_seconds,
            self.pump_pid_input_max_age_seconds,
            self.line_pid_input_max_age_seconds,
        ) <= 0.0:
            raise ValueError("PID sample and age limits must be positive")

    @property
    def effective_deadband_exit_bar(self) -> float:
        return self.deadband_bar if self.deadband_exit_bar == 0.0 else self.deadband_exit_bar


@dataclass(frozen=True, slots=True)
class ValveCommand:
    enabled: bool
    output_percent: float | None
    mode: ControlMode
    source: PressureSource | None
    reason: str | None = None
    pid_state: PidState = PidState.HOLD
    pressure_measurement: PressureMeasurement | None = None
    pid_measurement_bar: float | None = None


@dataclass(frozen=True, slots=True)
class PidDiagnostics:
    state: PidState = PidState.HOLD
    measurement_dt_seconds: float | None = None
    error_bar: float | None = None
    p_term_percent: float = 0.0
    i_term_percent: float = 0.0
    d_term_percent: float = 0.0
    unconstrained_output_percent: float = 0.0
    constrained_output_percent: float = 0.0
    applied_output_percent: float = 0.0
    filtered_measurement_bar: float | None = None
    reason: str = ""


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
        self._last_sequence: int | None = None
        self._last_sample_timestamp: float | None = None
        self._suppress_integral_derivative_once = False
        self._in_deadband = False
        self._diagnostics = PidDiagnostics(applied_output_percent=0.0)

    @property
    def parameters(self) -> PidParameters:
        return self._parameters

    @property
    def last_output_percent(self) -> float:
        return self._last_output

    @property
    def diagnostics(self) -> PidDiagnostics:
        return self._diagnostics

    def reset(self, *, output_percent: float = 0.0) -> None:
        self._integral = self._clamp(output_percent)
        self._previous_measurement = None
        self._filtered_measurement = None
        self._last_output = self._clamp(output_percent)
        self._elapsed_seconds = 0.0
        self._last_direction = 0
        self._last_reversal_seconds = float("-inf")
        self._reversals.clear()
        self._last_sequence = None
        self._last_sample_timestamp = None
        self._suppress_integral_derivative_once = False
        self._in_deadband = False
        self._diagnostics = PidDiagnostics(
            state=PidState.HOLD,
            i_term_percent=self._integral,
            applied_output_percent=self._last_output,
            reason="PID reset",
        )

    def configure(
        self,
        parameters: PidParameters,
        *,
        current_output_percent: float,
        setpoint: float | None = None,
        measurement: float | None = None,
    ) -> None:
        self._parameters = parameters
        if setpoint is None or measurement is None:
            self.reset(output_percent=current_output_percent)
        else:
            self.prepare_bumpless(
                setpoint=setpoint,
                measurement=measurement,
                output_percent=current_output_percent,
            )

    def calculate(
        self,
        *,
        setpoint: float,
        measurement: float,
        dt_seconds: float,
        measurement_filter_alpha: float | None = None,
        sequence: int | None = None,
        timestamp_monotonic: float | None = None,
        filter_enabled: bool | None = None,
    ) -> float:
        if not all(isfinite(value) for value in (setpoint, measurement, dt_seconds)):
            raise ValueError("PID inputs must be finite")
        if dt_seconds <= 0.0:
            raise ValueError("PID time step must be positive")

        if sequence is not None and sequence == self._last_sequence:
            self._diagnostics = PidDiagnostics(
                state=PidState.HOLD,
                i_term_percent=self._integral,
                applied_output_percent=self._last_output,
                filtered_measurement_bar=self._filtered_measurement,
                reason="no new source sample",
            )
            return self._last_output
        measurement_dt = dt_seconds
        if timestamp_monotonic is not None and self._last_sample_timestamp is not None:
            measurement_dt = timestamp_monotonic - self._last_sample_timestamp
            if not isfinite(measurement_dt) or measurement_dt <= 0.0:
                raise ValueError("PID measurement timestamps must increase")
        self._elapsed_seconds += measurement_dt
        enabled = (
            self._parameters.measurement_filter_enabled
            if filter_enabled is None
            else filter_enabled
        )
        if not enabled:
            alpha = 1.0
        elif self._parameters.measurement_filter_time_constant_seconds > 0.0:
            alpha = 1.0 - exp(
                -measurement_dt / self._parameters.measurement_filter_time_constant_seconds
            )
        else:
            alpha = (
                self._parameters.measurement_filter_alpha
                if measurement_filter_alpha is None
                else measurement_filter_alpha
            )
        filtered = (
            measurement
            if self._filtered_measurement is None
            else alpha * measurement + (1.0 - alpha) * self._filtered_measurement
        )
        self._filtered_measurement = filtered
        direction = 1.0 if self._parameters.direction is ControlDirection.DIRECT else -1.0
        error = direction * (setpoint - filtered)
        deadband_limit = (
            self._parameters.effective_deadband_exit_bar
            if self._in_deadband
            else self._parameters.deadband_bar
        )
        self._in_deadband = abs(error) <= deadband_limit
        if self._in_deadband:
            self._previous_measurement = filtered
            self._remember_sample(sequence, timestamp_monotonic)
            self._diagnostics = PidDiagnostics(
                state=PidState.DEADBAND,
                measurement_dt_seconds=measurement_dt,
                error_bar=error,
                i_term_percent=self._integral,
                applied_output_percent=self._last_output,
                filtered_measurement_bar=filtered,
                reason="inside hysteretic deadband",
            )
            return self._last_output
        proportional = self._parameters.proportional_gain * error
        derivative = 0.0
        interval_valid = measurement_dt <= self._parameters.maximum_pid_sample_interval_seconds
        suppress_dynamic_terms = self._suppress_integral_derivative_once or not interval_valid
        if self._previous_measurement is not None and not suppress_dynamic_terms:
            measurement_rate = (filtered - self._previous_measurement) / measurement_dt
            derivative = -direction * self._parameters.derivative_gain * measurement_rate

        integral_candidate = self._integral
        if not suppress_dynamic_terms:
            integral_candidate += self._parameters.integral_gain * error * measurement_dt
            integral_candidate = min(
                self._parameters.integral_max_percent,
                max(self._parameters.integral_min_percent, integral_candidate),
            )
        unconstrained = proportional + integral_candidate + derivative
        constrained = self._clamp(unconstrained)
        max_delta = (
            self._parameters.maximum_output_rate_percent_per_second * measurement_dt
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
        self._remember_sample(sequence, timestamp_monotonic)
        initializing = self._suppress_integral_derivative_once
        self._suppress_integral_derivative_once = False
        self._diagnostics = PidDiagnostics(
            state=PidState.INITIALIZING if initializing else PidState.ACTIVE,
            measurement_dt_seconds=measurement_dt,
            error_bar=error,
            p_term_percent=proportional,
            i_term_percent=self._integral,
            d_term_percent=derivative,
            unconstrained_output_percent=unconstrained,
            constrained_output_percent=constrained,
            applied_output_percent=output,
            filtered_measurement_bar=filtered,
            reason=(
                "bumpless first sample"
                if initializing
                else ("sample interval too long; I/D held" if not interval_valid else "")
            ),
        )
        return output

    def track_output(self, output_percent: float) -> None:
        self._last_output = self._clamp(output_percent)
        self._diagnostics = PidDiagnostics(
            state=PidState.MANUAL,
            i_term_percent=self._integral,
            applied_output_percent=self._last_output,
            reason="manual output tracking",
        )

    def enter_safe(self, output_percent: float) -> None:
        self.reset(output_percent=output_percent)
        self._diagnostics = PidDiagnostics(
            state=PidState.SAFE,
            i_term_percent=self._integral,
            applied_output_percent=self._last_output,
            reason="safe state applied",
        )

    def enter_fault(self, reason: str) -> None:
        self._suppress_integral_derivative_once = True
        self._previous_measurement = None
        self._diagnostics = PidDiagnostics(
            state=PidState.FAULT,
            i_term_percent=self._integral,
            applied_output_percent=self._last_output,
            filtered_measurement_bar=self._filtered_measurement,
            reason=reason,
        )

    def prepare_bumpless(
        self, *, setpoint: float, measurement: float, output_percent: float
    ) -> None:
        direction = 1.0 if self._parameters.direction is ControlDirection.DIRECT else -1.0
        error = direction * (setpoint - measurement)
        proportional = self._parameters.proportional_gain * error
        self._integral = min(
            self._parameters.integral_max_percent,
            max(
                self._parameters.integral_min_percent,
                self._clamp(output_percent) - proportional,
            ),
        )
        self._previous_measurement = measurement
        self._filtered_measurement = measurement
        self._last_output = self._clamp(output_percent)
        self._last_sequence = None
        self._last_sample_timestamp = None
        self._suppress_integral_derivative_once = True
        self._in_deadband = False

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

    def _remember_sample(
        self, sequence: int | None, timestamp_monotonic: float | None
    ) -> None:
        self._last_sequence = sequence
        if timestamp_monotonic is not None:
            self._last_sample_timestamp = timestamp_monotonic


class ValveController:
    def __init__(self, pid: PidController) -> None:
        self._pid = pid
        self._last_mode: ControlMode | None = None
        self._last_manual_output = 0.0
        self._last_source: PressureSource | None = None
        self._last_setpoint: float | None = None
        self._last_pressure: PressureMeasurement | None = None
        self._requires_bumpless_restart = False

    @property
    def diagnostics(self) -> PidDiagnostics:
        return self._pid.diagnostics

    @property
    def last_output_percent(self) -> float:
        return self._pid.last_output_percent

    def configure_pid(
        self, parameters: PidParameters, *, current_output_percent: float
    ) -> None:
        self._pid.configure(
            parameters,
            current_output_percent=current_output_percent,
            setpoint=self._last_setpoint,
            measurement=(
                None
                if self._last_pressure is None
                else self._last_pressure.filtered_value_bar
            ),
        )

    def enter_safe(self, output_percent: float) -> None:
        self._pid.enter_safe(output_percent)
        self._last_mode = None
        self._requires_bumpless_restart = True

    @staticmethod
    def pressure_measurement(
        snapshot: MeasurementSnapshot, source: PressureSource
    ) -> PressureMeasurement | None:
        if source is PressureSource.INJECTION_PUMP:
            reading = snapshot.injection_pressure_reading
            if reading is None:
                return PressureMeasurement(
                    source,
                    snapshot.injection_pump.pressure_bar,
                    snapshot.injection_pump.pressure_bar,
                    snapshot.monotonic_seconds,
                    0.0,
                    int(snapshot.monotonic_seconds * 1_000_000),
                    snapshot.injection_pressure_quality,
                )
            return PressureMeasurement(
                source,
                reading.pressure_bar,
                reading.pressure_bar,
                reading.monotonic_seconds,
                max(0.0, snapshot.monotonic_seconds - reading.monotonic_seconds),
                reading.sequence,
                reading.quality,
                reading.last_error,
            )
        line_reading = snapshot.line_pressure_reading
        if line_reading is None:
            if snapshot.line_pressure_bar is None:
                return None
            legacy_timestamp = snapshot.recorded_at.timestamp()
            return PressureMeasurement(
                source,
                snapshot.line_pressure_bar,
                snapshot.line_pressure_bar,
                legacy_timestamp,
                0.0,
                int(legacy_timestamp * 1_000_000),
                snapshot.line_pressure_quality,
                snapshot.line_pressure_quality_reason,
            )
        if line_reading.filtered_pressure_bar is None:
            return None
        raw = (
            line_reading.filtered_pressure_bar
            if line_reading.raw_pressure_bar is None
            else line_reading.raw_pressure_bar
        )
        return PressureMeasurement(
            source,
            raw,
            line_reading.filtered_pressure_bar,
            (
                line_reading.monotonic_seconds
                if line_reading.sequence > 0
                else snapshot.recorded_at.timestamp()
            ),
            max(0.0, snapshot.monotonic_seconds - line_reading.monotonic_seconds),
            (
                line_reading.sequence
                if line_reading.sequence > 0
                else int(snapshot.recorded_at.timestamp() * 1_000_000)
            ),
            line_reading.quality,
            line_reading.quality_reason,
        )

    def begin_measurement(self, mode: ControlMode) -> None:
        """Make the operator-selected mode the active mode at a new run boundary."""
        self._last_mode = mode

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
                pid_state=PidState.SAFE,
            )

        if mode is ControlMode.MANUAL:
            if manual_output_percent is None or not isfinite(manual_output_percent):
                raise ValueError("manual output must be a finite percentage")
            if not 0.0 <= manual_output_percent <= 100.0:
                raise ValueError("manual output must be between 0 and 100 percent")
            self._pid.track_output(manual_output_percent)
            self._last_manual_output = manual_output_percent
            self._last_mode = mode
            return ValveCommand(
                True,
                manual_output_percent,
                mode,
                None,
                pid_state=PidState.MANUAL,
            )

        if source is None or setpoint_bar is None or dt_seconds is None:
            raise ValueError("automatic mode requires source, setpoint and time step")
        pressure = self.pressure_measurement(snapshot, source)
        if pressure is None:
            raise ValueError("the selected pressure source is not configured")
        if pressure.quality is not DataQuality.GOOD:
            return ValveCommand(
                False,
                None,
                mode,
                source,
                f"selected pressure source quality is {pressure.quality.value}",
                PidState.BLOCKED,
                pressure,
            )
        max_age = (
            self._pid.parameters.pump_pid_input_max_age_seconds
            if source is PressureSource.INJECTION_PUMP
            else self._pid.parameters.line_pid_input_max_age_seconds
        )
        if pressure.age_seconds > max_age:
            return ValveCommand(
                True,
                self._pid.last_output_percent,
                mode,
                source,
                f"PID input age {pressure.age_seconds:.3f}s exceeds {max_age:.3f}s",
                PidState.BLOCKED,
                pressure,
                self._pid.diagnostics.filtered_measurement_bar,
            )
        transition_reason = None
        source_changed = self._last_source is not None and source is not self._last_source
        if (
            self._last_mode is ControlMode.MANUAL
            or source_changed
            or self._requires_bumpless_restart
        ):
            self._pid.prepare_bumpless(
                setpoint=setpoint_bar,
                measurement=pressure.filtered_value_bar,
                output_percent=(
                    self._last_manual_output
                    if self._last_mode is ControlMode.MANUAL
                    else self._pid.last_output_percent
                ),
            )
            if source_changed:
                transition_reason = "bumpless pressure-source transfer"
            elif self._requires_bumpless_restart:
                transition_reason = "bumpless safe-to-automatic restart"
            else:
                transition_reason = "bumpless manual-to-automatic transfer"
            self._requires_bumpless_restart = False
        try:
            output = self._pid.calculate(
                setpoint=setpoint_bar,
                measurement=pressure.filtered_value_bar,
                dt_seconds=dt_seconds,
                sequence=(
                    pressure.sequence
                    if pressure.sequence < 1_000_000_000_000
                    else None
                ),
                timestamp_monotonic=(
                    pressure.timestamp_monotonic
                    if pressure.sequence < 1_000_000_000_000
                    else None
                ),
                filter_enabled=(
                    False if source is PressureSource.LINE_SENSOR else None
                ),
            )
        except ValveOscillationError:
            self._pid.enter_fault("VALVE_OSCILLATION")
            raise
        self._last_mode = mode
        self._last_source = source
        self._last_setpoint = setpoint_bar
        self._last_pressure = pressure
        diagnostics = self._pid.diagnostics
        return ValveCommand(
            True,
            output,
            mode,
            source,
            transition_reason or diagnostics.reason or None,
            diagnostics.state,
            pressure,
            diagnostics.filtered_measurement_bar,
        )
