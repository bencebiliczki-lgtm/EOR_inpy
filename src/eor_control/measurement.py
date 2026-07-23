from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Event
from time import monotonic, sleep
from typing import cast

from eor_control.calibration import LinearCalibration
from eor_control.devices import Clock, DataAcquisition, Pump
from eor_control.domain import (
    DataQuality,
    MeasurementRecord,
    MeasurementSnapshot,
    PumpStatus,
)
from eor_control.safety import SafetyDecision, SafetyLimits, SafetyMonitor
from eor_control.signal_filter import AnalogFilterConfig, AnalogSignalFilter
from eor_control.storage import MeasurementWriter


class SystemClock:
    def utc_now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return monotonic()

    def sleep(self, seconds: float) -> None:
        sleep(seconds)


@dataclass(frozen=True, slots=True)
class MeasurementChannels:
    line_pressure: str | None = "line_pressure"
    differential_pressure: str | None = "differential_pressure"


class MeasurementService:
    def __init__(
        self,
        *,
        jacket_pump: Pump,
        injection_pump: Pump,
        daq: DataAcquisition,
        line_calibration: LinearCalibration,
        differential_calibration: LinearCalibration,
        safety_monitor: SafetyMonitor,
        writer: MeasurementWriter,
        clock: Clock | None = None,
        channels: MeasurementChannels | None = None,
        persistence_enabled: bool = True,
        analog_filter_config: AnalogFilterConfig | None = None,
    ) -> None:
        self._jacket_pump = jacket_pump
        self._injection_pump = injection_pump
        self._daq = daq
        self._line_calibration = line_calibration
        self._differential_calibration = differential_calibration
        self._safety_monitor = safety_monitor
        self._writer = writer
        self._clock = clock or SystemClock()
        self._channels = channels or MeasurementChannels()
        self._persistence_enabled = persistence_enabled
        self._analog_filter_config = analog_filter_config or AnalogFilterConfig()
        self._analog_filters = {
            "line_pressure": self._make_analog_filter(
                self._analog_filter_config.line_spike_limit_voltage
            ),
            "differential_pressure": self._make_analog_filter(
                self._analog_filter_config.differential_spike_limit_voltage
            ),
        }
        self._initial_jacket_volume_ml: float | None = None
        self._initial_injection_volume_ml: float | None = None

    def reset_injected_volume_tracking(self) -> None:
        """Start the injected-volume counter from the next acquired pump status."""
        self._initial_jacket_volume_ml = None
        self._initial_injection_volume_ml = None
        for signal_filter in self._analog_filters.values():
            signal_filter.reset()

    def read_pressure_inputs_individually(
        self,
    ) -> tuple[dict[str, float], dict[str, str]]:
        """Read each NI pressure input independently for service telemetry."""

        values: dict[str, float] = {}
        errors: dict[str, str] = {}
        inputs = (
            (
                "line_pressure",
                self._channels.line_pressure,
                self._line_calibration,
            ),
            (
                "differential_pressure",
                self._channels.differential_pressure,
                self._differential_calibration,
            ),
        )
        for key, channel, calibration in inputs:
            if channel is None:
                continue
            try:
                voltage = self._daq.read_voltage(channel)
                values[key] = calibration.convert(voltage)
            except Exception as error:
                errors[key] = str(error)
        return values, errors

    def sample_once(
        self,
        *,
        active_stage: str,
        valve_percent: float,
        persist: bool = True,
        control_deadline_missed: bool = False,
        pressure_target_bar: float | None = None,
        use_line_pressure_for_control: bool = False,
        enforce_minimum_margin: bool = True,
    ) -> MeasurementRecord:
        jacket, jacket_quality = self._read_pump(self._jacket_pump)
        injection, injection_quality = self._read_pump(self._injection_pump)
        if self._initial_jacket_volume_ml is None:
            self._initial_jacket_volume_ml = jacket.remaining_volume_ml
        if self._initial_injection_volume_ml is None:
            self._initial_injection_volume_ml = injection.remaining_volume_ml

        line_pressure, raw_line_pressure = self._read_optional_pressure(
            self._channels.line_pressure,
            self._line_calibration,
            "line pressure input",
            "line_pressure",
        )
        differential_pressure, raw_differential_pressure = self._read_optional_pressure(
            self._channels.differential_pressure,
            self._differential_calibration,
            "differential pressure input",
            "differential_pressure",
        )

        snapshot = MeasurementSnapshot(
            recorded_at=self._clock.utc_now(),
            monotonic_seconds=self._clock.monotonic(),
            jacket_pump=jacket,
            injection_pump=injection,
            line_pressure_bar=line_pressure,
            differential_pressure_bar=differential_pressure,
            valve_percent=valve_percent,
            quality=self._combined_quality(jacket_quality, injection_quality),
            raw_line_pressure_bar=raw_line_pressure,
            raw_differential_pressure_bar=raw_differential_pressure,
        )
        if use_line_pressure_for_control:
            if snapshot.line_pressure_bar is None:
                raise ValueError(
                    "line pressure control source is selected but the sensor is not configured"
                )
            controlled_pressure = snapshot.line_pressure_bar
        else:
            controlled_pressure = snapshot.injection_pump.pressure_bar
        decision = self._safety_monitor.evaluate(
            snapshot,
            control_deadline_missed=control_deadline_missed,
            controlled_pressure_bar=(
                controlled_pressure if pressure_target_bar is not None else None
            ),
            pressure_target_bar=pressure_target_bar,
            enforce_minimum_margin=enforce_minimum_margin,
        )
        record = MeasurementRecord(
            snapshot=snapshot,
            injected_volume_ml=(
                self._initial_injection_volume_ml - injection.remaining_volume_ml
            ),
            active_stage=active_stage,
            jacket_net_volume_ml=(
                self._initial_jacket_volume_ml - jacket.remaining_volume_ml
            ),
            safety_reasons=decision.reasons,
        )
        if persist and self._persistence_enabled:
            self._writer.write(record)
        if not decision.safe:
            self.request_safe_state()
        return record

    def _read_optional_pressure(
        self,
        channel: str | None,
        calibration: LinearCalibration,
        label: str,
        filter_key: str,
    ) -> tuple[float | None, float | None]:
        if channel is None:
            return None, None
        config = self._analog_filter_config
        read_many = getattr(self._daq, "read_voltages", None)
        samples = (
            read_many(channel, config.samples_per_read)
            if config.enabled and callable(read_many)
            else [self._daq.read_voltage(channel)]
        )
        filtered = self._analog_filters[filter_key].process(samples)
        try:
            return (
                calibration.convert(filtered.filtered_voltage),
                calibration.convert(filtered.raw_voltage),
            )
        except ValueError as error:
            raise ValueError(f"{label}: {error}") from error

    def _make_analog_filter(self, spike_limit_voltage: float) -> AnalogSignalFilter:
        config = self._analog_filter_config
        return AnalogSignalFilter(
            alpha=config.ema_alpha if config.enabled else 1.0,
            median_enabled=config.median_enabled if config.enabled else False,
            spike_rejection_enabled=(
                config.spike_rejection_enabled if config.enabled else False
            ),
            spike_limit_voltage=spike_limit_voltage,
            spike_confirmation_samples=config.spike_confirmation_samples,
        )

    @staticmethod
    def _read_pump(pump: Pump) -> tuple[PumpStatus, DataQuality]:
        read_cached = getattr(pump, "read_cached_status", None)
        if callable(read_cached):
            return cast(tuple[PumpStatus, DataQuality], read_cached())
        return pump.read_status(), DataQuality.GOOD

    @staticmethod
    def _combined_quality(*qualities: DataQuality) -> DataQuality:
        priority = {
            DataQuality.GOOD: 0,
            DataQuality.STALE: 1,
            DataQuality.OUT_OF_RANGE: 2,
            DataQuality.DISCONNECTED: 3,
        }
        return max(qualities, key=priority.__getitem__)

    def run(
        self,
        stop_event: Event,
        *,
        interval_seconds: float,
        active_stage: str,
        valve_percent: float,
    ) -> None:
        if not 1.0 <= interval_seconds <= 3600.0:
            raise ValueError("measurement interval must be between 1 second and 1 hour")
        while not stop_event.is_set():
            started_at = self._clock.monotonic()
            try:
                self.sample_once(active_stage=active_stage, valve_percent=valve_percent)
            except (ConnectionError, ValueError):
                self.request_safe_state()
                raise
            elapsed = self._clock.monotonic() - started_at
            self._clock.sleep(max(0.0, interval_seconds - elapsed))

    def request_safe_state(self) -> None:
        for operation in (
            self._jacket_pump.request_stop,
            self._injection_pump.request_stop,
            self._daq.set_safe_state,
        ):
            try:
                operation()
            except Exception:
                # The application-level supervisor records detailed failures; this
                # lower-level fallback must still attempt every safe-state action.
                continue

    def configure_measurement(
        self,
        *,
        line_calibration: LinearCalibration,
        differential_calibration: LinearCalibration,
        safety_limits: SafetyLimits,
    ) -> None:
        self._line_calibration = line_calibration
        self._differential_calibration = differential_calibration
        self._safety_monitor.configure(safety_limits)
        for signal_filter in self._analog_filters.values():
            signal_filter.reset()

    def reset_safety_latch(self, snapshot: MeasurementSnapshot) -> SafetyDecision:
        """Clear the safety latch only when a fresh snapshot is currently safe."""

        return self._safety_monitor.reset(snapshot, operator_acknowledged=True)

    def close(self) -> None:
        try:
            close_daq = getattr(self._daq, "close", None)
            if callable(close_daq):
                close_daq()
        finally:
            self._writer.close()
