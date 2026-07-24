from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from threading import Condition, Event, Lock, Thread, current_thread
from time import monotonic
from typing import Protocol, TypeVar

from eor_control.diagnostics import DiagnosticCategory, DiagnosticLogger
from eor_control.domain import DataQuality, PumpStatus

T = TypeVar("T")


class PollablePump(Protocol):
    def connect(self) -> None: ...

    def read_pressure_bar(self) -> float: ...

    def read_flow_ml_per_hour(self) -> float: ...

    def read_remaining_volume_ml(self) -> float: ...

    def read_operating_status(self) -> str: ...

    def enter_remote(self) -> None: ...

    def set_constant_flow(self, flow_ml_per_hour: float) -> None: ...

    def set_constant_pressure(self, pressure_bar: float) -> None: ...

    def set_pressure_limit(self, pressure_bar: float) -> None: ...

    def run(self) -> None: ...

    def request_stop(self) -> None: ...

    def clear(self) -> None: ...

    def return_local(self) -> None: ...

    def disconnect(self) -> None: ...


@dataclass(frozen=True, slots=True)
class PumpPollingIntervals:
    pressure_seconds: float = 0.4
    slow_telemetry_seconds: float = 1.5
    pressure_stale_seconds: float = 2.0
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


class PumpConnectionState(StrEnum):
    CONNECTED = "CONNECTED"
    TELEMETRY_PARTIAL = "TELEMETRY_PARTIAL"
    READY = "READY"
    DEGRADED = "DEGRADED"
    DISCONNECTED = "DISCONNECTED"


@dataclass(frozen=True, slots=True)
class TelemetryFieldState:
    quality: DataQuality
    age_seconds: float | None
    last_update_monotonic: float | None
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class PumpTelemetrySnapshot:
    status: PumpStatus
    connection_state: PumpConnectionState
    pressure: TelemetryFieldState
    flow: TelemetryFieldState
    volume: TelemetryFieldState
    operating_status: TelemetryFieldState


@dataclass(frozen=True, slots=True)
class _CachedTelemetry:
    status: PumpStatus
    pressure_at: float
    flow_at: float | None
    volume_at: float | None
    operating_status_at: float
    errors: tuple[tuple[str, str], ...] = ()


class PollingPump:
    """Keep blocking DASNET reads outside the control loop.

    One instance owns one pump and one worker. Pressure and basic operating status
    are required during connection; slower flow and volume telemetry is filled in
    by the worker afterwards. Control reads only use the timestamped cache.
    """

    def __init__(
        self,
        pump: PollablePump,
        *,
        name: str,
        intervals: PumpPollingIntervals | None = None,
        diagnostics: DiagnosticLogger | None = None,
        diagnostic_category: DiagnosticCategory = DiagnosticCategory.SYSTEM,
    ) -> None:
        self._pump = pump
        self._name = name
        self._intervals = intervals or PumpPollingIntervals()
        self._diagnostics = diagnostics
        self._diagnostic_category = diagnostic_category
        self._condition = Condition()
        self._command_lock = Lock()
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._cache: _CachedTelemetry | None = None
        self._worker_error: Exception | None = None
        self._connected = False
        self._stop_latched = False
        self._pending_commands = 0

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
        """Return the status and the safety-critical pressure quality.

        Flow and remaining volume have their own quality in ``read_telemetry``.
        Their age must not stop pressure control or the complete measurement.
        """
        telemetry = self.read_telemetry()
        return telemetry.status, telemetry.pressure.quality

    def read_telemetry(self) -> PumpTelemetrySnapshot:
        with self._condition:
            cache = self._cache
            error = self._worker_error
            connected = self._connected
        if cache is None:
            detail = f": {error}" if error is not None else ""
            raise ConnectionError(f"{self._name} pump has no telemetry{detail}")
        now = monotonic()
        errors = dict(cache.errors)
        pressure = self._field_state(
            now,
            cache.pressure_at,
            self._intervals.pressure_stale_seconds,
            errors.get("pressure"),
            connected=connected,
        )
        flow = self._field_state(
            now,
            cache.flow_at,
            self._intervals.slow_telemetry_stale_seconds,
            errors.get("flow"),
            connected=connected,
        )
        volume = self._field_state(
            now,
            cache.volume_at,
            self._intervals.slow_telemetry_stale_seconds,
            errors.get("volume"),
            connected=connected,
        )
        operating_status = self._field_state(
            now,
            cache.operating_status_at,
            self._intervals.slow_telemetry_stale_seconds,
            errors.get("status"),
            connected=connected,
        )
        status = cache.status
        if not connected:
            status = PumpStatus(
                pressure_bar=status.pressure_bar,
                flow_ml_per_hour=status.flow_ml_per_hour,
                remaining_volume_ml=status.remaining_volume_ml,
                connected=False,
            )
            state = PumpConnectionState.DISCONNECTED
        elif (
            pressure.quality is not DataQuality.GOOD
            or any(
                field.last_error is not None
                for field in (flow, volume, operating_status)
            )
        ):
            state = PumpConnectionState.DEGRADED
        elif flow.age_seconds is None or volume.age_seconds is None:
            state = PumpConnectionState.TELEMETRY_PARTIAL
        elif any(
            field.quality is not DataQuality.GOOD
            for field in (flow, volume, operating_status)
        ):
            state = PumpConnectionState.DEGRADED
        else:
            state = PumpConnectionState.READY
        return PumpTelemetrySnapshot(
            status=status,
            connection_state=state,
            pressure=pressure,
            flow=flow,
            volume=volume,
            operating_status=operating_status,
        )

    def enter_remote(self) -> None:
        self._execute(self._pump.enter_remote)
        with self._condition:
            self._stop_latched = False

    def set_constant_flow(self, flow_ml_per_hour: float) -> None:
        self._execute(lambda: self._pump.set_constant_flow(flow_ml_per_hour))

    def set_constant_pressure(self, pressure_bar: float) -> None:
        self._execute(lambda: self._pump.set_constant_pressure(pressure_bar))

    def set_pressure_limit(self, pressure_bar: float) -> None:
        self._execute(lambda: self._pump.set_pressure_limit(pressure_bar))

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
        with self._condition:
            self._pending_commands += 1
            self._condition.notify_all()
        try:
            with self._command_lock:
                operation()
        finally:
            with self._condition:
                self._pending_commands -= 1
                self._condition.notify_all()

    def _poll(self) -> None:
        try:
            pressure = self._read(self._pump.read_pressure_bar)
            pressure_at = monotonic()
            self._read(self._pump.read_operating_status)
            status_at = monotonic()
            with self._condition:
                self._cache = _CachedTelemetry(
                    PumpStatus(pressure, 0.0, 0.0),
                    pressure_at,
                    None,
                    None,
                    status_at,
                )
                self._condition.notify_all()

            next_pressure = monotonic() + self._intervals.pressure_seconds
            slow_step = self._intervals.slow_telemetry_seconds / 3.0
            next_flow = monotonic()
            next_volume = monotonic() + slow_step
            next_status = monotonic() + self._intervals.slow_telemetry_seconds
            while not self._stop_event.is_set():
                deadlines = {
                    "pressure": next_pressure,
                    "flow": next_flow,
                    "volume": next_volume,
                    "status": next_status,
                }
                field, due = min(
                    deadlines.items(),
                    key=lambda item: (
                        item[1],
                        0 if item[0] == "pressure" else 1,
                    ),
                )
                if self._stop_event.wait(max(0.0, due - monotonic())):
                    break
                try:
                    if field == "pressure":
                        self._update(
                            field, self._read(self._pump.read_pressure_bar)
                        )
                        next_pressure = monotonic() + self._intervals.pressure_seconds
                    elif field == "flow":
                        self._update(
                            field, self._read(self._pump.read_flow_ml_per_hour)
                        )
                        next_flow = (
                            monotonic() + self._intervals.slow_telemetry_seconds
                        )
                    elif field == "volume":
                        self._update(
                            field,
                            self._read(self._pump.read_remaining_volume_ml),
                        )
                        next_volume = (
                            monotonic() + self._intervals.slow_telemetry_seconds
                        )
                    else:
                        self._read(self._pump.read_operating_status)
                        self._update(field, None)
                        self._log_telemetry_health()
                        next_status = (
                            monotonic() + self._intervals.slow_telemetry_seconds
                        )
                except Exception as field_error:
                    self._record_field_error(field, field_error)
                    retry_at = monotonic() + (
                        self._intervals.pressure_seconds
                        if field == "pressure"
                        else self._intervals.slow_telemetry_seconds
                    )
                    if field == "pressure":
                        next_pressure = retry_at
                    elif field == "flow":
                        next_flow = retry_at
                    elif field == "volume":
                        next_volume = retry_at
                    else:
                        next_status = retry_at
        except Exception as error:
            with self._condition:
                if not self._stop_event.is_set():
                    self._worker_error = error
                    self._connected = False
                self._condition.notify_all()

    def _read(self, operation: Callable[[], T]) -> T:
        # Operator and safety commands have priority over the next scheduled
        # telemetry transaction. An already-running serial read is allowed to
        # finish, then the queued command gets the line before polling resumes.
        with self._condition:
            self._condition.wait_for(
                lambda: self._pending_commands == 0
                or self._stop_event.is_set()
            )
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
            errors = dict(cache.errors)
            errors.pop(field, None)
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
                tuple(sorted(errors.items())),
            )
            self._condition.notify_all()

    def _record_field_error(self, field: str, error: Exception) -> None:
        with self._condition:
            cache = self._cache
            if cache is None:
                self._worker_error = error
                self._condition.notify_all()
                return
            errors = dict(cache.errors)
            errors[field] = str(error)
            self._cache = _CachedTelemetry(
                cache.status,
                cache.pressure_at,
                cache.flow_at,
                cache.volume_at,
                cache.operating_status_at,
                tuple(sorted(errors.items())),
            )
            self._condition.notify_all()
        self._log(
            f"{field} telemetry failed; last successful field update retained: "
            f"{error}",
            level="WARNING",
        )

    @staticmethod
    def _field_state(
        now: float,
        updated_at: float | None,
        stale_after: float,
        error: str | None,
        *,
        connected: bool,
    ) -> TelemetryFieldState:
        age = None if updated_at is None else max(0.0, now - updated_at)
        if not connected:
            quality = DataQuality.DISCONNECTED
        elif updated_at is None or age is not None and age > stale_after:
            quality = DataQuality.STALE
        else:
            quality = DataQuality.GOOD
        return TelemetryFieldState(quality, age, updated_at, error)

    def _log_telemetry_health(self) -> None:
        telemetry = self.read_telemetry()

        def field_text(field: TelemetryFieldState) -> str:
            age = "never" if field.age_seconds is None else f"{field.age_seconds:.3f}s"
            updated = (
                "never"
                if field.last_update_monotonic is None
                else f"{field.last_update_monotonic:.6f}"
            )
            return (
                f"{field.quality.value},age={age},"
                f"last_success_monotonic={updated}"
            )

        self._log(
            f"state={telemetry.connection_state.value}; "
            f"pressure[{field_text(telemetry.pressure)}]; "
            f"flow[{field_text(telemetry.flow)}]; "
            f"volume[{field_text(telemetry.volume)}]; "
            f"status[{field_text(telemetry.operating_status)}]"
        )

    def _log(self, message: str, *, level: str = "INFO") -> None:
        if self._diagnostics is not None:
            self._diagnostics.emit(
                self._diagnostic_category,
                "TELEMETRY",
                message,
                level=level,
            )
