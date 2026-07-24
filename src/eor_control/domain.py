from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class DataQuality(StrEnum):
    GOOD = "good"
    INVALID = "invalid"
    OUT_OF_RANGE = "out_of_range"
    STALE = "stale"
    DISCONNECTED = "disconnected"


@dataclass(frozen=True, slots=True)
class PumpStatus:
    pressure_bar: float
    flow_ml_per_hour: float
    remaining_volume_ml: float
    connected: bool = True


@dataclass(frozen=True, slots=True)
class MeasurementSnapshot:
    recorded_at: datetime
    monotonic_seconds: float
    jacket_pump: PumpStatus
    injection_pump: PumpStatus
    line_pressure_bar: float | None
    differential_pressure_bar: float | None
    valve_percent: float
    quality: DataQuality = DataQuality.GOOD
    raw_line_pressure_bar: float | None = None
    raw_differential_pressure_bar: float | None = None


@dataclass(frozen=True, slots=True)
class MeasurementRecord:
    snapshot: MeasurementSnapshot
    injected_volume_ml: float
    active_stage: str
    jacket_net_volume_ml: float = 0.0
    safety_reasons: tuple[str, ...] = ()

    @property
    def injection_net_volume_ml(self) -> float:
        """Signed net injection-pump volume change since measurement start."""

        return self.injected_volume_ml
