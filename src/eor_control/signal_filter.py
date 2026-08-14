from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite
from statistics import median


@dataclass(frozen=True, slots=True)
class AnalogFilterConfig:
    enabled: bool = True
    samples_per_read: int = 20
    sample_rate_hz: float = 1000.0
    ema_alpha: float = 0.2
    median_enabled: bool = True
    spike_rejection_enabled: bool = True
    line_spike_limit_voltage: float = 0.1
    differential_spike_limit_voltage: float = 0.1
    differential_ema_alpha: float = 0.2
    differential_median_enabled: bool = True
    differential_spike_rejection_enabled: bool = True
    differential_spike_confirmation_samples: int = 3
    spike_confirmation_samples: int = 3
    line_electrical_min_voltage: float = 0.5
    line_electrical_max_voltage: float = 5.5
    line_physical_min_pressure_bar: float = -15.0
    line_physical_max_pressure_bar: float = 420.0
    line_stale_timeout_seconds: float = 1.0
    differential_electrical_min_voltage: float = 0.5
    differential_electrical_max_voltage: float = 5.5
    differential_physical_min_pressure_bar: float = -5.0
    differential_physical_max_pressure_bar: float = 55.0
    differential_stale_timeout_seconds: float = 1.0

    def __post_init__(self) -> None:
        if not 1 <= self.samples_per_read <= 100:
            raise ValueError("analog samples per read must be between 1 and 100")
        if not isfinite(self.sample_rate_hz) or not 1.0 <= self.sample_rate_hz <= 100_000.0:
            raise ValueError("analog sample rate must be between 1 and 100000 Hz")
        if not isfinite(self.ema_alpha) or not 0.0 < self.ema_alpha <= 1.0:
            raise ValueError("analog EMA alpha must be within (0, 1]")
        if (
            not isfinite(self.differential_ema_alpha)
            or not 0.0 < self.differential_ema_alpha <= 1.0
        ):
            raise ValueError("differential EMA alpha must be within (0, 1]")
        limits = (
            self.line_spike_limit_voltage,
            self.differential_spike_limit_voltage,
        )
        if not all(isfinite(value) and value > 0.0 for value in limits):
            raise ValueError("analog spike limits must be positive and finite")
        if self.spike_confirmation_samples < 1:
            raise ValueError("analog spike confirmation count must be positive")
        if self.differential_spike_confirmation_samples < 1:
            raise ValueError(
                "differential spike confirmation count must be positive"
            )
        if not (
            isfinite(self.line_electrical_min_voltage)
            and isfinite(self.line_electrical_max_voltage)
            and self.line_electrical_min_voltage < self.line_electrical_max_voltage
        ):
            raise ValueError("line electrical voltage limits must be finite and ordered")
        if not (
            isfinite(self.line_physical_min_pressure_bar)
            and isfinite(self.line_physical_max_pressure_bar)
            and self.line_physical_min_pressure_bar < self.line_physical_max_pressure_bar
        ):
            raise ValueError("line physical pressure limits must be finite and ordered")
        if not isfinite(self.line_stale_timeout_seconds) or self.line_stale_timeout_seconds <= 0:
            raise ValueError("line stale timeout must be positive and finite")
        if not (
            isfinite(self.differential_electrical_min_voltage)
            and isfinite(self.differential_electrical_max_voltage)
            and self.differential_electrical_min_voltage
            < self.differential_electrical_max_voltage
        ):
            raise ValueError(
                "differential electrical voltage limits must be finite and ordered"
            )
        if not (
            isfinite(self.differential_physical_min_pressure_bar)
            and isfinite(self.differential_physical_max_pressure_bar)
            and self.differential_physical_min_pressure_bar
            < self.differential_physical_max_pressure_bar
        ):
            raise ValueError(
                "differential physical pressure limits must be finite and ordered"
            )
        if (
            not isfinite(self.differential_stale_timeout_seconds)
            or self.differential_stale_timeout_seconds <= 0
        ):
            raise ValueError("differential stale timeout must be positive and finite")


@dataclass(frozen=True, slots=True)
class FilteredAnalogValue:
    last_raw_voltage: float
    median_voltage: float
    filtered_voltage: float

    @property
    def raw_voltage(self) -> float:
        """Backward-compatible name for the non-EMA median value."""
        return self.median_voltage


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
        last_raw_voltage = float(samples[-1])
        raw_voltage = float(median(samples) if self._median_enabled else last_raw_voltage)
        if self._filtered_voltage is None:
            self._filtered_voltage = raw_voltage
            return FilteredAnalogValue(last_raw_voltage, raw_voltage, raw_voltage)

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
        return FilteredAnalogValue(last_raw_voltage, raw_voltage, self._filtered_voltage)

    def reset(self) -> None:
        self._filtered_voltage = None
        self._pending_spike_count = 0
