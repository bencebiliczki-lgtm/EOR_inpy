from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class LinearCalibration:
    voltage_min: float
    voltage_max: float
    value_min: float
    value_max: float

    def __post_init__(self) -> None:
        if self.voltage_max <= self.voltage_min:
            raise ValueError("voltage_max must be greater than voltage_min")

    def convert(self, voltage: float) -> float:
        """Convert a finite voltage, extrapolating outside the nominal span.

        ``voltage_min`` and ``voltage_max`` describe calibration points.  They
        are deliberately not electrical-fault or safety thresholds; those
        limits, when verified for a particular installation, belong in the
        hardware profile and safety monitor.
        """
        if not isfinite(voltage):
            raise ValueError("voltage must be finite")
        ratio = (voltage - self.voltage_min) / (self.voltage_max - self.voltage_min)
        return self.value_min + ratio * (self.value_max - self.value_min)

    def is_inside_nominal_range(self, voltage: float) -> bool:
        """Return diagnostic calibration-span membership without rejecting data."""
        return isfinite(voltage) and self.voltage_min <= voltage <= self.voltage_max
