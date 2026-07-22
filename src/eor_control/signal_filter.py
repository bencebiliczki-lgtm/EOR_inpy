from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite
from statistics import median


@dataclass(frozen=True, slots=True)
class AnalogFilterConfig:
    enabled: bool = True
    samples_per_read: int = 20
    ema_alpha: float = 0.2
    median_enabled: bool = True
    spike_rejection_enabled: bool = True
    line_spike_limit_voltage: float = 0.1
    differential_spike_limit_voltage: float = 0.1
    spike_confirmation_samples: int = 3

    def __post_init__(self) -> None:
        if not 1 <= self.samples_per_read <= 100:
            raise ValueError("analog samples per read must be between 1 and 100")
        if not isfinite(self.ema_alpha) or not 0.0 < self.ema_alpha <= 1.0:
            raise ValueError("analog EMA alpha must be within (0, 1]")
        limits = (
            self.line_spike_limit_voltage,
            self.differential_spike_limit_voltage,
        )
        if not all(isfinite(value) and value > 0.0 for value in limits):
            raise ValueError("analog spike limits must be positive and finite")
        if self.spike_confirmation_samples < 1:
            raise ValueError("analog spike confirmation count must be positive")


@dataclass(frozen=True, slots=True)
class FilteredAnalogValue:
    raw_voltage: float
    filtered_voltage: float


class AnalogSignalFilter:
    """Median and EMA filter that retains the un-smoothed median for safety."""

    def __init__(
        self,
        *,
        alpha: float,
        median_enabled: bool,
        spike_rejection_enabled: bool,
        spike_limit_voltage: float,
        spike_confirmation_samples: int,
    ) -> None:
        self._alpha = alpha
        self._median_enabled = median_enabled
        self._spike_rejection_enabled = spike_rejection_enabled
        self._spike_limit = spike_limit_voltage
        self._spike_confirmation_samples = spike_confirmation_samples
        self._filtered_voltage: float | None = None
        self._pending_spike_count = 0

    def process(self, samples: Sequence[float]) -> FilteredAnalogValue:
        if not samples:
            raise ValueError("no analog samples received")
        if not all(isfinite(value) for value in samples):
            raise ValueError("analog samples must be finite")
        raw_voltage = float(median(samples) if self._median_enabled else samples[-1])
        if self._filtered_voltage is None:
            self._filtered_voltage = raw_voltage
            return FilteredAnalogValue(raw_voltage, raw_voltage)

        candidate = raw_voltage
        if (
            self._spike_rejection_enabled
            and abs(raw_voltage - self._filtered_voltage) > self._spike_limit
        ):
            self._pending_spike_count += 1
            if self._pending_spike_count < self._spike_confirmation_samples:
                candidate = self._filtered_voltage
            else:
                self._pending_spike_count = 0
        else:
            self._pending_spike_count = 0

        self._filtered_voltage = (
            self._alpha * candidate
            + (1.0 - self._alpha) * self._filtered_voltage
        )
        return FilteredAnalogValue(raw_voltage, self._filtered_voltage)

    def reset(self) -> None:
        self._filtered_voltage = None
        self._pending_spike_count = 0
