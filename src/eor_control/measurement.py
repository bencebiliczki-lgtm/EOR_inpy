from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Event
from time import monotonic, sleep

from eor_control.calibration import LinearCalibration
from eor_control.devices import Clock, DataAcquisition, Pump
from eor_control.domain import MeasurementRecord, MeasurementSnapshot
from eor_control.safety import SafetyMonitor
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
    line_pressure: str = "line_pressure"
    differential_pressure: str = "differential_pressure"


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
        channels: MeasurementChannels = MeasurementChannels(),
    ) -> None:
        self._jacket_pump = jacket_pump
        self._injection_pump = injection_pump
        self._daq = daq
        self._line_calibration = line_calibration
        self._differential_calibration = differential_calibration
        self._safety_monitor = safety_monitor
        self._writer = writer
        self._clock = clock or SystemClock()
        self._channels = channels
        self._initial_injection_volume_ml: float | None = None

    def sample_once(self, *, active_stage: str, valve_percent: float) -> MeasurementRecord:
        jacket = self._jacket_pump.read_status()
        injection = self._injection_pump.read_status()
        if self._initial_injection_volume_ml is None:
            self._initial_injection_volume_ml = injection.remaining_volume_ml

        snapshot = MeasurementSnapshot(
            recorded_at=self._clock.utc_now(),
            monotonic_seconds=self._clock.monotonic(),
            jacket_pump=jacket,
            injection_pump=injection,
            line_pressure_bar=self._line_calibration.convert(
                self._daq.read_voltage(self._channels.line_pressure)
            ),
            differential_pressure_bar=self._differential_calibration.convert(
                self._daq.read_voltage(self._channels.differential_pressure)
            ),
            valve_percent=valve_percent,
        )
        decision = self._safety_monitor.evaluate(snapshot)
        record = MeasurementRecord(
            snapshot=snapshot,
            injected_volume_ml=max(
                0.0, self._initial_injection_volume_ml - injection.remaining_volume_ml
            ),
            active_stage=active_stage,
            safety_reasons=decision.reasons,
        )
        self._writer.write(record)
        if not decision.safe:
            self.request_safe_state()
        return record

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
        self._jacket_pump.request_stop()
        self._injection_pump.request_stop()
        self._daq.set_safe_state()
