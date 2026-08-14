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
class PumpPressureReading:
    """Timestamped PRESS telemetry used independently from slower pump fields."""

    pressure_bar: float
    monotonic_seconds: float
    sample_age_seconds: float
    sequence: int
    quality: DataQuality
    last_error: str = ""


@dataclass(frozen=True, slots=True)
class AnalogPressureReading:
    """Traceable result of one analog pressure sample burst."""

    last_raw_voltage: float | None
    median_voltage: float | None
    filtered_voltage: float | None
    raw_pressure_bar: float | None
    filtered_pressure_bar: float | None
    measured_at: datetime
    monotonic_seconds: float
    sample_age_seconds: float
    quality: DataQuality
    quality_reason: str = ""
    sample_count: int = 0
    sample_min_voltage: float | None = None
    sample_max_voltage: float | None = None
    physical_channel: str | None = None
    terminal_configuration: str | None = None
    sequence: int = 0


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
    raw_line_voltage: float | None = None
    raw_differential_voltage: float | None = None
    line_pressure_reading: AnalogPressureReading | None = None
    differential_pressure_reading: AnalogPressureReading | None = None
    jacket_pressure_reading: PumpPressureReading | None = None
    injection_pressure_reading: PumpPressureReading | None = None

    @property
    def injection_pressure_quality(self) -> DataQuality:
        if self.injection_pressure_reading is not None:
            return self.injection_pressure_reading.quality
        return DataQuality.GOOD if self.injection_pump.connected else DataQuality.DISCONNECTED

    @property
    def injection_pressure_sample_age_seconds(self) -> float | None:
        return (
            None
            if self.injection_pressure_reading is None
            else self.injection_pressure_reading.sample_age_seconds
        )

    @property
    def line_pressure_quality(self) -> DataQuality:
        if self.line_pressure_reading is not None:
            return self.line_pressure_reading.quality
        return DataQuality.GOOD if self.line_pressure_bar is not None else DataQuality.DISCONNECTED

    @property
    def line_pressure_quality_reason(self) -> str:
        return (
            ""
            if self.line_pressure_reading is None
            else self.line_pressure_reading.quality_reason
        )

    @property
    def line_pressure_sample_age_seconds(self) -> float | None:
        return (
            None
            if self.line_pressure_reading is None
            else self.line_pressure_reading.sample_age_seconds
        )

    @property
    def differential_pressure_quality(self) -> DataQuality:
        if self.differential_pressure_reading is not None:
            return self.differential_pressure_reading.quality
        return (
            DataQuality.GOOD
            if self.differential_pressure_bar is not None
            else DataQuality.DISCONNECTED
        )

    @property
    def differential_pressure_quality_reason(self) -> str:
        return (
            ""
            if self.differential_pressure_reading is None
            else self.differential_pressure_reading.quality_reason
        )

    @property
    def differential_pressure_sample_age_seconds(self) -> float | None:
        return (
            None
            if self.differential_pressure_reading is None
            else self.differential_pressure_reading.sample_age_seconds
        )


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
