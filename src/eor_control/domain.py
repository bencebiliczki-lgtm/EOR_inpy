from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class DataQuality(StrEnum):
    GOOD = "good"
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
    line_pressure_bar: float
    differential_pressure_bar: float
    valve_percent: float
    quality: DataQuality = DataQuality.GOOD


@dataclass(frozen=True, slots=True)
class MeasurementRecord:
    snapshot: MeasurementSnapshot
    injected_volume_ml: float
    active_stage: str
    safety_reasons: tuple[str, ...] = ()
