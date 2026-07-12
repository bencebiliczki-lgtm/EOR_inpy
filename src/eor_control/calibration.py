from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LinearCalibration:
    voltage_min: float
    voltage_max: float
    value_min: float
    value_max: float

    def convert(self, voltage: float) -> float:
        if self.voltage_max <= self.voltage_min:
            raise ValueError("voltage_max must be greater than voltage_min")
        if not self.voltage_min <= voltage <= self.voltage_max:
            raise ValueError("voltage is outside the calibrated range")
        ratio = (voltage - self.voltage_min) / (self.voltage_max - self.voltage_min)
        return self.value_min + ratio * (self.value_max - self.value_min)

