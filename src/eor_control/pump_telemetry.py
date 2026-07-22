from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from threading import Condition, Event, Lock, Thread, current_thread
from time import monotonic
from typing import Protocol, TypeVar

from eor_control.domain import DataQuality, PumpStatus

T = TypeVar("T")


class PollablePump(Protocol):
    def connect(self) -> None: ...

    def read_pressure_bar(self) -> float: ...

    def read_flow_ml_per_hour(self) -> float: ...

    def read_remaining_volume_ml(self) -> float: ...

    def read_operating_status(self) -> str: ...

    def enter_remote(self) -> None: ...

    def set_constant_flow(self, flow_ml_per_minute: float) -> None: ...

    def set_constant_pressure(self, pressure_bar: float) -> None: ...

    def run(self) -> None: ...

    def request_stop(self) -> None: ...

    def clear(self) -> None: ...

    def return_local(self) -> None: ...

    def disconnect(self) -> None: ...


@dataclass(frozen=True, slots=True)
class PumpPollingIntervals:
    pressure_seconds: float = 0.4
    slow_telemetry_seconds: float = 1.5
    pressure_stale_seconds: float = 1.0
    slow_telemetry_stale_seconds: float = 3.0
    startup_timeout_seconds: float = 3.0

    def __post_init__(self) -> None:
        values = (
            self.pressure_seconds,
            self.slow_telemetry_seconds,
            self.pressure_stale_seconds,
            self.slow_telemetry_stale_seconds,
            self.startup_timeout_seconds,
        )
        if not all(isfinite(value) and value > 0.0 for value in values):
            raise ValueError("pump polling intervals must be positive and finite")
        if self.pressure_stale_seconds < self.pressure_seconds:
            raise ValueError("pump pressure stale limit must cover one polling interval")
        if self.slow_telemetry_stale_seconds < self.slow_telemetry_seconds:
            raise ValueError("slow pump telemetry stale limit must cover one polling interval")


@dataclass(frozen=True, slots=True)
class _CachedTelemetry:
    status: PumpStatus
    pressure_at: float
    flow_at: float
    volume_at: float
    operating_status_at: float


class PollingPump:
    """Keep blocking DASNET reads outside the control loop.

    One instance owns one pump and one worker.  The first complete telemetry set is
    acquired during connection; subsequent ``read_status`` calls only read the
    timestamped cache.
    """

    def __init__(
        self,
        pump: PollablePump,
        *,
        name: str,
        intervals: PumpPollingIntervals | None = None,
    ) -> None:
        self._pump = pump
        self._name = name
        self._intervals = intervals or PumpPollingIntervals()
        self._condition = Condition()
        self._command_lock = Lock()
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._cache: _CachedTelemetry | None = None
        self._worker_error: Exception | None = None
        self._connected = False
        self._stop_latched = False

    def connect(self) -> None:
        with self._condition:
            if self._connected:
                return
        with self._command_lock:
            self._pump.connect()
        with self._condition:
            self._connected = True
            self._stop_latched = False
            self._worker_error = None
            self._cache = None
            self._stop_event.clear()
            self._thread = Thread(
                target=self._poll,
                name=f"eor-{self._name}-pump-poll",
                daemon=True,
            )
            self._thread.start()
            ready = self._condition.wait_for(
                lambda: self._cache is not None or self._worker_error is not None,
                timeout=self._intervals.startup_timeout_seconds,
            )
            error = self._worker_error
        if not ready or error is not None:
            self.disconnect()
            if error is not None:
                raise ConnectionError(
                    f"{self._name} pump initial telemetry failed: {error}"
                ) from error
            raise TimeoutError(f"{self._name} pump initial telemetry timed out")

    def read_status(self) -> PumpStatus:
        status, _ = self.read_cached_status()
        return status

    def read_cached_status(self) -> tuple[PumpStatus, DataQuality]:
        with self._condition:
            cache = self._cache
            error = self._worker_error
            connected = self._connected
        if cache is None:
            detail = f": {error}" if error is not None else ""
            raise ConnectionError(f"{self._name} pump has no telemetry{detail}")
        now = monotonic()
        quality = DataQuality.GOOD
        if error is not None or not connected:
            quality = DataQuality.DISCONNECTED
        elif (
            now - cache.pressure_at > self._intervals.pressure_stale_seconds
            or any(
                now - timestamp > self._intervals.slow_telemetry_stale_seconds
                for timestamp in (
                    cache.flow_at,
                    cache.volume_at,
                    cache.operating_status_at,
                )
            )
        ):
            quality = DataQuality.STALE
        status = cache.status
        if quality is DataQuality.DISCONNECTED:
            status = PumpStatus(
                pressure_bar=status.pressure_bar,
                flow_ml_per_hour=status.flow_ml_per_hour,
                remaining_volume_ml=status.remaining_volume_ml,
                connected=False,
            )
        return status, quality

    def enter_remote(self) -> None:
        self._execute(self._pump.enter_remote)
        with self._condition:
            self._stop_latched = False

    def set_constant_flow(self, flow_ml_per_minute: float) -> None:
        self._execute(lambda: self._pump.set_constant_flow(flow_ml_per_minute))

    def set_constant_pressure(self, pressure_bar: float) -> None:
        self._execute(lambda: self._pump.set_constant_pressure(pressure_bar))

    def run(self) -> None:
        self._execute(self._pump.run)
        with self._condition:
            self._stop_latched = False

    def request_stop(self) -> None:
        with self._condition:
            if self._stop_latched:
                return
            # Latch before I/O: a LOCAL MODE response or a timeout must not create
            # an endless STOP/reply loop in subsequent fault handling paths.
            self._stop_latched = True
        self._execute(self._pump.request_stop, require_connected=False)

    def acknowledge_stop_latch(self) -> None:
        with self._condition:
            self._stop_latched = False

    def clear(self) -> None:
        self._execute(self._pump.clear)

    def return_local(self) -> None:
        self._execute(self._pump.return_local)

    def disconnect(self) -> None:
        with self._condition:
            self._connected = False
            self._stop_event.set()
            thread = self._thread
        if thread is not None and thread is not current_thread():
            thread.join(self._intervals.startup_timeout_seconds)
        try:
            with self._command_lock:
                self._pump.disconnect()
        finally:
            with self._condition:
                self._thread = None
                self._condition.notify_all()

    def _execute(
        self, operation: Callable[[], object], *, require_connected: bool = True
    ) -> None:
        if require_connected:
            with self._condition:
                if not self._connected:
                    raise ConnectionError(f"{self._name} pump is disconnected")
        with self._command_lock:
            operation()

    def _poll(self) -> None:
        try:
            pressure = self._read(self._pump.read_pressure_bar)
            pressure_at = monotonic()
            flow = self._read(self._pump.read_flow_ml_per_hour)
            flow_at = monotonic()
            volume = self._read(self._pump.read_remaining_volume_ml)
            volume_at = monotonic()
            self._read(self._pump.read_operating_status)
            status_at = monotonic()
            with self._condition:
                self._cache = _CachedTelemetry(
                    PumpStatus(pressure, flow, volume),
                    pressure_at,
                    flow_at,
                    volume_at,
                    status_at,
                )
                self._condition.notify_all()

            next_pressure = monotonic() + self._intervals.pressure_seconds
            slow_step = self._intervals.slow_telemetry_seconds / 3.0
            next_flow = monotonic() + slow_step
            next_volume = monotonic() + 2.0 * slow_step
            next_status = monotonic() + self._intervals.slow_telemetry_seconds
            while not self._stop_event.is_set():
                due = min(next_pressure, next_flow, next_volume, next_status)
                if self._stop_event.wait(max(0.0, due - monotonic())):
                    break
                now = monotonic()
                if now >= next_pressure:
                    self._update("pressure", self._read(self._pump.read_pressure_bar))
                    next_pressure = monotonic() + self._intervals.pressure_seconds
                elif now >= next_flow:
                    self._update("flow", self._read(self._pump.read_flow_ml_per_hour))
                    next_flow = monotonic() + self._intervals.slow_telemetry_seconds
                elif now >= next_volume:
                    self._update(
                        "volume", self._read(self._pump.read_remaining_volume_ml)
                    )
                    next_volume = monotonic() + self._intervals.slow_telemetry_seconds
                else:
                    self._read(self._pump.read_operating_status)
                    self._update("status", None)
                    next_status = monotonic() + self._intervals.slow_telemetry_seconds
        except Exception as error:
            with self._condition:
                if not self._stop_event.is_set():
                    self._worker_error = error
                    self._connected = False
                self._condition.notify_all()

    def _read(self, operation: Callable[[], T]) -> T:
        with self._command_lock:
            return operation()

    def _update(self, field: str, value: float | None) -> None:
        now = monotonic()
        numeric_value = 0.0 if value is None else value
        with self._condition:
            cache = self._cache
            if cache is None:
                return
            status = cache.status
            self._cache = _CachedTelemetry(
                PumpStatus(
                    pressure_bar=(
                        numeric_value if field == "pressure" else status.pressure_bar
                    ),
                    flow_ml_per_hour=(
                        numeric_value if field == "flow" else status.flow_ml_per_hour
                    ),
                    remaining_volume_ml=(
                        numeric_value
                        if field == "volume"
                        else status.remaining_volume_ml
                    ),
                ),
                now if field == "pressure" else cache.pressure_at,
                now if field == "flow" else cache.flow_at,
                now if field == "volume" else cache.volume_at,
                now if field == "status" else cache.operating_status_at,
            )
