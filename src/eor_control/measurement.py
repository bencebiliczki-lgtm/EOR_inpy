from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Event
from time import monotonic, sleep
from typing import cast

from eor_control.calibration import LinearCalibration
from eor_control.devices import Clock, DataAcquisition, Pump
from eor_control.domain import (
    AnalogPressureReading,
    DataQuality,
    MeasurementRecord,
    MeasurementSnapshot,
    PumpPressureReading,
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
        enabled_pumps: frozenset[str] = frozenset({"jacket", "injection"}),
    ) -> None:
        if not enabled_pumps.issubset({"jacket", "injection"}):
            raise ValueError("enabled pumps must be jacket and/or injection")
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
        self._enabled_pumps = enabled_pumps
        self._analog_filters = {
            "line_pressure": self._make_analog_filter("line_pressure"),
            "differential_pressure": self._make_analog_filter(
                "differential_pressure"
            ),
        }
        self._latest_pressure_readings: dict[str, AnalogPressureReading] = {}
        self._initial_jacket_volume_ml: float | None = None
        self._initial_injection_volume_ml: float | None = None
        self._pump_sequences = {"jacket": 0, "injection": 0}
        self._analog_sequences = {"line_pressure": 0, "differential_pressure": 0}

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
            reading = self._read_optional_pressure(
                channel,
                calibration,
                f"{key.replace('_', ' ')} input",
                key,
            )
            if reading is not None and reading.filtered_pressure_bar is not None:
                values[key] = reading.filtered_pressure_bar
                self._latest_pressure_readings[key] = reading
            if reading is not None and reading.quality is not DataQuality.GOOD:
                errors[key] = reading.quality_reason or reading.quality.value
        return values, errors

    def latest_pressure_readings(self) -> dict[str, AnalogPressureReading]:
        return dict(self._latest_pressure_readings)

    def sample_once(
        self,
        *,
        active_stage: str,
        valve_percent: float,
        persist: bool = True,
        control_deadline_missed: bool = False,
        use_line_pressure_for_control: bool = False,
        enforce_minimum_margin: bool = False,
    ) -> MeasurementRecord:
        jacket, jacket_quality, jacket_pressure_reading = self._read_enabled_pump(
            self._jacket_pump, "jacket"
        )
        injection, injection_quality, injection_pressure_reading = self._read_enabled_pump(
            self._injection_pump, "injection"
        )
        if "jacket" in self._enabled_pumps and self._initial_jacket_volume_ml is None:
            self._initial_jacket_volume_ml = jacket.remaining_volume_ml
        if "injection" in self._enabled_pumps and self._initial_injection_volume_ml is None:
            self._initial_injection_volume_ml = injection.remaining_volume_ml

        line_reading = self._read_optional_pressure(
            self._channels.line_pressure,
            self._line_calibration,
            "line pressure input",
            "line_pressure",
        )
        differential_reading = self._read_optional_pressure(
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
            line_pressure_bar=(
                None if line_reading is None else line_reading.filtered_pressure_bar
            ),
            differential_pressure_bar=(
                None
                if differential_reading is None
                else differential_reading.filtered_pressure_bar
            ),
            valve_percent=valve_percent,
            quality=self._combined_quality(
                *(
                    quality
                    for name, quality in (
                        ("jacket", jacket_quality),
                        ("injection", injection_quality),
                    )
                    if name in self._enabled_pumps
                )
            ),
            raw_line_pressure_bar=(
                None if line_reading is None else line_reading.raw_pressure_bar
            ),
            raw_differential_pressure_bar=(
                None
                if differential_reading is None
                else differential_reading.raw_pressure_bar
            ),
            raw_line_voltage=(
                None if line_reading is None else line_reading.last_raw_voltage
            ),
            raw_differential_voltage=(
                None
                if differential_reading is None
                else differential_reading.last_raw_voltage
            ),
            line_pressure_reading=line_reading,
            differential_pressure_reading=differential_reading,
            jacket_pressure_reading=jacket_pressure_reading,
            injection_pressure_reading=injection_pressure_reading,
        )
        decision = self._safety_monitor.evaluate(
            snapshot,
            control_deadline_missed=control_deadline_missed,
            enforce_minimum_margin=enforce_minimum_margin,
            require_good_line_pressure=use_line_pressure_for_control,
            require_good_differential_pressure=(
                self._channels.differential_pressure is not None
            ),
            require_jacket_pump="jacket" in self._enabled_pumps,
            require_injection_pump="injection" in self._enabled_pumps,
        )
        record = MeasurementRecord(
            snapshot=snapshot,
            injected_volume_ml=(
                (
                    injection.remaining_volume_ml
                    if self._initial_injection_volume_ml is None
                    else self._initial_injection_volume_ml
                )
                - injection.remaining_volume_ml
            ),
            active_stage=active_stage,
            jacket_net_volume_ml=(
                (
                    jacket.remaining_volume_ml
                    if self._initial_jacket_volume_ml is None
                    else self._initial_jacket_volume_ml
                )
                - jacket.remaining_volume_ml
            ),
            safety_reasons=decision.reasons,
        )
        if persist and self._persistence_enabled:
            self._writer.write(record)
        if not decision.safe:
            self.request_fault_state()
        return record

    def _read_enabled_pump(
        self, pump: Pump, role: str
    ) -> tuple[PumpStatus, DataQuality, PumpPressureReading | None]:
        if role in self._enabled_pumps:
            return self._read_pump(pump, role)
        return PumpStatus(0.0, 0.0, 0.0, connected=False), DataQuality.GOOD, None

    def _read_optional_pressure(
        self,
        channel: str | None,
        calibration: LinearCalibration,
        label: str,
        filter_key: str,
    ) -> AnalogPressureReading | None:
        if channel is None:
            return None
        self._analog_sequences[filter_key] += 1
        sequence = self._analog_sequences[filter_key]
        config = self._analog_filter_config
        started = self._clock.monotonic()
        try:
            read_many = getattr(self._daq, "read_voltages", None)
            samples = (
                read_many(channel, config.samples_per_read)
                if config.enabled and callable(read_many)
                else [self._daq.read_voltage(channel)]
            )
            sample_timestamp = self._clock.monotonic()
            filtered = self._analog_filters[filter_key].process(
                samples,
                timestamp_monotonic=sample_timestamp,
            )
            raw_pressure = calibration.convert(filtered.median_voltage)
            filtered_pressure = calibration.convert(filtered.filtered_voltage)
        except Exception as error:
            quality = (
                DataQuality.INVALID
                if isinstance(error, ValueError)
                else DataQuality.DISCONNECTED
            )
            reading = AnalogPressureReading(
                None,
                None,
                None,
                None,
                None,
                self._clock.utc_now(),
                self._clock.monotonic(),
                max(0.0, self._clock.monotonic() - started),
                quality,
                f"{label}: {type(error).__name__}: {error}",
                physical_channel=self._physical_channel(channel),
                terminal_configuration=self._terminal_configuration(),
                sequence=sequence,
            )
            self._latest_pressure_readings[filter_key] = reading
            self._log_pressure_reading(filter_key, reading)
            return reading
        measured_at = self._clock.utc_now()
        measured_monotonic = sample_timestamp
        age = max(0.0, measured_monotonic - started)
        quality = DataQuality.GOOD
        reason = ""
        if filter_key == "line_pressure":
            if any(
                voltage < config.line_electrical_min_voltage
                or voltage > config.line_electrical_max_voltage
                for voltage in samples
            ):
                quality = DataQuality.OUT_OF_RANGE
                reason = "line voltage is outside configured electrical limits"
            elif not (
                config.line_physical_min_pressure_bar
                <= raw_pressure
                <= config.line_physical_max_pressure_bar
            ):
                quality = DataQuality.OUT_OF_RANGE
                reason = "line pressure is outside configured physical limits"
            elif age > config.line_stale_timeout_seconds:
                quality = DataQuality.STALE
                reason = "line pressure sample is older than the configured timeout"
        elif any(
            voltage < config.differential_electrical_min_voltage
            or voltage > config.differential_electrical_max_voltage
            for voltage in samples
        ):
            quality = DataQuality.OUT_OF_RANGE
            reason = "differential voltage is outside configured electrical limits"
        elif not (
            config.differential_physical_min_pressure_bar
            <= raw_pressure
            <= config.differential_physical_max_pressure_bar
        ):
            quality = DataQuality.OUT_OF_RANGE
            reason = "differential pressure is outside configured physical limits"
        elif age > config.differential_stale_timeout_seconds:
            quality = DataQuality.STALE
            reason = "differential pressure sample is older than the configured timeout"
        reading = AnalogPressureReading(
            filtered.last_raw_voltage,
            filtered.median_voltage,
            filtered.filtered_voltage,
            raw_pressure,
            filtered_pressure,
            measured_at,
            measured_monotonic,
            age,
            quality,
            reason,
            len(samples),
            min(samples),
            max(samples),
            self._physical_channel(channel),
            self._terminal_configuration(),
            sequence,
        )
        self._latest_pressure_readings[filter_key] = reading
        self._log_pressure_reading(filter_key, reading)
        return reading

    def _log_pressure_reading(
        self, filter_key: str, reading: AnalogPressureReading
    ) -> None:
        logger = getattr(self._daq, "log_pressure_reading", None)
        if callable(logger):
            logger(filter_key, reading)

    def _physical_channel(self, channel: str) -> str:
        resolver = getattr(self._daq, "physical_channel", None)
        if callable(resolver):
            return str(resolver(channel) or channel)
        return channel

    def _terminal_configuration(self) -> str:
        return str(getattr(self._daq, "terminal_configuration", "SIMULATION"))

    def _make_analog_filter(self, filter_key: str) -> AnalogSignalFilter:
        config = self._analog_filter_config
        differential = filter_key == "differential_pressure"
        return AnalogSignalFilter(
            alpha=(
                config.differential_ema_alpha if differential else config.ema_alpha
            )
            if config.enabled
            else 1.0,
            median_enabled=(
                config.differential_median_enabled
                if differential
                else config.median_enabled
            )
            if config.enabled
            else False,
            spike_rejection_enabled=(
                (
                    config.differential_spike_rejection_enabled
                    if differential
                    else config.spike_rejection_enabled
                )
                if config.enabled
                else False
            ),
            spike_limit_voltage=(
                config.differential_spike_limit_voltage
                if differential
                else config.line_spike_limit_voltage
            ),
            spike_confirmation_samples=(
                config.differential_spike_confirmation_samples
                if differential
                else config.spike_confirmation_samples
            ),
            ema_enabled=(
                config.differential_ema_enabled
                if differential
                else config.line_ema_enabled
            ),
            time_constant_seconds=(
                config.differential_ema_time_constant_seconds
                if differential
                else config.line_ema_time_constant_seconds
            ),
        )

    def _read_pump(
        self, pump: Pump, role: str
    ) -> tuple[PumpStatus, DataQuality, PumpPressureReading]:
        read_telemetry = getattr(pump, "read_telemetry", None)
        if callable(read_telemetry):
            telemetry = read_telemetry()
            pressure = telemetry.pressure
            status_quality = telemetry.operating_status.quality
            quality = self._combined_quality(pressure.quality, status_quality)
            timestamp = pressure.last_update_monotonic
            return (
                telemetry.status,
                quality,
                PumpPressureReading(
                    telemetry.status.pressure_bar,
                    self._clock.monotonic() if timestamp is None else timestamp,
                    0.0 if pressure.age_seconds is None else pressure.age_seconds,
                    pressure.sequence,
                    pressure.quality,
                    pressure.last_error or "",
                    status_quality,
                ),
            )
        read_cached = getattr(pump, "read_cached_status", None)
        if callable(read_cached):
            status, quality = cast(tuple[PumpStatus, DataQuality], read_cached())
            self._pump_sequences[role] += 1
            now = self._clock.monotonic()
            return status, quality, PumpPressureReading(
                status.pressure_bar,
                now,
                0.0,
                self._pump_sequences[role],
                quality,
            )
        if callable(getattr(pump, "read_pressure_bar", None)) and callable(
            getattr(pump, "read_operating_status", None)
        ):
            raise RuntimeError(
                "raw pump cannot be used for measurement control; PollingPump required"
            )
        status = pump.read_status()
        self._pump_sequences[role] += 1
        now = self._clock.monotonic()
        return status, DataQuality.GOOD, PumpPressureReading(
            status.pressure_bar,
            now,
            0.0,
            self._pump_sequences[role],
            DataQuality.GOOD,
        )

    @staticmethod
    def _combined_quality(*qualities: DataQuality) -> DataQuality:
        priority = {
            DataQuality.GOOD: 0,
            DataQuality.STALE: 1,
            DataQuality.INVALID: 2,
            DataQuality.OUT_OF_RANGE: 3,
            DataQuality.DISCONNECTED: 4,
        }
        return max(qualities, key=priority.__getitem__) if qualities else DataQuality.GOOD

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
                self.request_fault_state()
                raise
            elapsed = self._clock.monotonic() - started_at
            self._clock.sleep(max(0.0, interval_seconds - elapsed))

    def request_safe_state(self) -> None:
        operations = [self._daq.set_safe_state]
        if "jacket" in self._enabled_pumps:
            operations.append(self._jacket_pump.request_stop)
        if "injection" in self._enabled_pumps:
            operations.append(self._injection_pump.request_stop)
        for operation in operations:
            try:
                operation()
            except Exception:
                # The application-level supervisor records detailed failures; this
                # lower-level fallback must still attempt every safe-state action.
                continue

    def request_fault_state(self) -> None:
        """Stop injection while preserving jacket pressure on a latched fault.

        The jacket pump's separately programmed MAXPRESS limit remains the
        independent protection that stops it on an internal overpressure.
        """
        operations = [self._daq.set_safe_state]
        if "injection" in self._enabled_pumps:
            operations.append(self._injection_pump.request_stop)
        for operation in operations:
            try:
                operation()
            except Exception:
                # The application-level supervisor records detailed failures;
                # still attempt every remaining fault-state action.
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

        return self._safety_monitor.reset(
            snapshot,
            operator_acknowledged=True,
            enforce_minimum_margin=False,
            require_good_differential_pressure=(
                self._channels.differential_pressure is not None
            ),
        )

    def close(self) -> None:
        try:
            close_daq = getattr(self._daq, "close", None)
            if callable(close_daq):
                close_daq()
        finally:
            self._writer.close()
