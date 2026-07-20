from dataclasses import dataclass


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
        if not self.voltage_min <= voltage <= self.voltage_max:
            raise ValueError(
                f"voltage {voltage:.6g} V is outside the calibrated range "
                f"{self.voltage_min:.6g}–{self.voltage_max:.6g} V"
            )
        ratio = (voltage - self.voltage_min) / (self.voltage_max - self.voltage_min)
        return self.value_min + ratio * (self.value_max - self.value_min)
