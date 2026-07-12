from dataclasses import dataclass
from math import isfinite

from eor_control.domain import DataQuality, MeasurementSnapshot


@dataclass(frozen=True, slots=True)
class SafetyLimits:
    max_jacket_pressure_bar: float
    max_injection_pressure_bar: float
    max_differential_pressure_bar: float
    minimum_jacket_margin_bar: float = 20.0


@dataclass(frozen=True, slots=True)
class SafetyDecision:
    safe: bool
    reasons: tuple[str, ...]
    latched: bool = False


class SafetyMonitor:
    def __init__(self, limits: SafetyLimits) -> None:
        self._limits = limits
        self._latched_reasons: tuple[str, ...] = ()

    def evaluate(
        self,
        snapshot: MeasurementSnapshot,
        *,
        emergency_stop: bool = False,
        control_deadline_missed: bool = False,
    ) -> SafetyDecision:
        reasons: list[str] = []
        if snapshot.quality is not DataQuality.GOOD:
            reasons.append(f"invalid data quality: {snapshot.quality}")
        if not snapshot.jacket_pump.connected or not snapshot.injection_pump.connected:
            reasons.append("pump disconnected")
        measured_values = (
            snapshot.jacket_pump.pressure_bar,
            snapshot.jacket_pump.flow_ml_per_hour,
            snapshot.jacket_pump.remaining_volume_ml,
            snapshot.injection_pump.pressure_bar,
            snapshot.injection_pump.flow_ml_per_hour,
            snapshot.injection_pump.remaining_volume_ml,
            snapshot.line_pressure_bar,
            snapshot.differential_pressure_bar,
            snapshot.valve_percent,
            snapshot.monotonic_seconds,
        )
        if not all(isfinite(value) for value in measured_values):
            reasons.append("non-finite measurement value")
        if emergency_stop:
            reasons.append("manual emergency stop")
        if control_deadline_missed:
            reasons.append("control deadline missed")
        if snapshot.jacket_pump.pressure_bar > self._limits.max_jacket_pressure_bar:
            reasons.append("jacket pressure limit exceeded")
        if snapshot.injection_pump.pressure_bar > self._limits.max_injection_pressure_bar:
            reasons.append("injection pressure limit exceeded")
        if snapshot.differential_pressure_bar >= self._limits.max_differential_pressure_bar:
            reasons.append("differential pressure limit reached")
        margin = snapshot.jacket_pump.pressure_bar - snapshot.injection_pump.pressure_bar
        if margin < self._limits.minimum_jacket_margin_bar:
            reasons.append("jacket pressure margin is too low")
        if reasons:
            self._latched_reasons = tuple(dict.fromkeys((*self._latched_reasons, *reasons)))
        return SafetyDecision(
            safe=not self._latched_reasons,
            reasons=self._latched_reasons,
            latched=bool(self._latched_reasons),
        )

    def reset(
        self, snapshot: MeasurementSnapshot, *, operator_acknowledged: bool
    ) -> SafetyDecision:
        """Clear a latched fault only after acknowledgement and safe precondition checks."""
        if not self._latched_reasons:
            return self.evaluate(snapshot)
        if not operator_acknowledged:
            return SafetyDecision(False, self._latched_reasons, bool(self._latched_reasons))

        previous_reasons = self._latched_reasons
        self._latched_reasons = ()
        decision = self.evaluate(snapshot)
        if decision.safe:
            return decision

        self._latched_reasons = tuple(dict.fromkeys((*previous_reasons, *self._latched_reasons)))
        return SafetyDecision(False, self._latched_reasons, True)
