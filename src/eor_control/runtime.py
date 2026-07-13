from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from threading import Event, Lock, Thread
from time import monotonic

from eor_control.control import ControlMode, PressureSource
from eor_control.control_loop import ControlCycleResult, ControlLoop


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    active_stage: str
    mode: ControlMode
    manual_output_percent: float
    source: PressureSource
    setpoint_bar: float
    recording_interval_seconds: float = 1.0

    def __post_init__(self) -> None:
        if not self.active_stage.strip():
            raise ValueError("active measurement stage must not be empty")
        values = (
            self.manual_output_percent,
            self.setpoint_bar,
            self.recording_interval_seconds,
        )
        if not all(isfinite(value) for value in values):
            raise ValueError("runtime settings must be finite")
        if not 0.0 <= self.manual_output_percent <= 100.0:
            raise ValueError("manual output must be between 0 and 100 percent")
        if not 1.0 <= self.recording_interval_seconds <= 3600.0:
            raise ValueError("recording interval must be between 1 second and 1 hour")


class BackgroundControlRunner:
    def __init__(
        self,
        control_loop: ControlLoop,
        *,
        control_interval_seconds: float = 0.1,
        watchdog_tolerance_seconds: float = 0.05,
        on_cycle: Callable[[ControlCycleResult], None] | None = None,
        on_fault: Callable[[str], None] | None = None,
    ) -> None:
        if not isfinite(control_interval_seconds) or control_interval_seconds <= 0.0:
            raise ValueError("control interval must be positive and finite")
        if not isfinite(watchdog_tolerance_seconds) or watchdog_tolerance_seconds < 0.0:
            raise ValueError("watchdog tolerance must be nonnegative and finite")
        self._control_loop = control_loop
        self._interval = control_interval_seconds
        self._watchdog_tolerance = watchdog_tolerance_seconds
        self._on_cycle = on_cycle
        self._on_fault = on_fault
        self._settings: RuntimeSettings | None = None
        self._settings_lock = Lock()
        self._stop_event = Event()
        self._thread: Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, settings: RuntimeSettings) -> None:
        if self.running:
            raise RuntimeError("control runner is already running")
        self.update_settings(settings)
        self._stop_event.clear()
        self._thread = Thread(target=self._run, name="eor-control-loop", daemon=True)
        self._thread.start()

    def update_settings(self, settings: RuntimeSettings) -> None:
        with self._settings_lock:
            self._settings = settings

    def stop(self, timeout_seconds: float = 2.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout_seconds)
            if thread.is_alive():
                self._control_loop.request_safe_state()
                raise TimeoutError("control thread did not stop before its deadline")
        self._thread = None

    def _current_settings(self) -> RuntimeSettings:
        with self._settings_lock:
            if self._settings is None:
                raise RuntimeError("control runner has no settings")
            return self._settings

    def _run(self) -> None:
        next_control = monotonic()
        next_record = next_control
        previous_cycle = next_control
        try:
            while not self._stop_event.is_set():
                now = monotonic()
                wait_seconds = next_control - now
                if wait_seconds > 0.0 and self._stop_event.wait(wait_seconds):
                    break
                started = monotonic()
                settings = self._current_settings()
                deadline_missed = started > next_control + self._watchdog_tolerance
                persist = started >= next_record
                result = self._control_loop.execute_once(
                    active_stage=settings.active_stage,
                    mode=settings.mode,
                    dt_seconds=max(started - previous_cycle, self._interval),
                    manual_output_percent=settings.manual_output_percent,
                    source=settings.source,
                    setpoint_bar=settings.setpoint_bar,
                    persist=persist,
                    control_deadline_missed=deadline_missed,
                )
                cycle_elapsed = monotonic() - started
                if cycle_elapsed > self._interval + self._watchdog_tolerance:
                    raise TimeoutError(
                        f"control cycle deadline missed: {cycle_elapsed:.3f} seconds"
                    )
                if self._on_cycle is not None:
                    self._on_cycle(result)
                previous_cycle = started
                if persist:
                    next_record = started + settings.recording_interval_seconds
                next_control += self._interval
                if next_control < started:
                    next_control = started + self._interval
        except Exception as error:
            self._control_loop.request_safe_state()
            if self._on_fault is not None:
                self._on_fault(str(error))
