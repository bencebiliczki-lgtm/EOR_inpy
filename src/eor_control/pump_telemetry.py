from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from heapq import heapify, heappop, heappush
from math import isfinite
from threading import Condition, Event, Thread, current_thread, get_ident
from time import monotonic
from typing import Protocol, TypeVar

from eor_control.diagnostics import DiagnosticCategory, DiagnosticLogger
from eor_control.domain import DataQuality, PumpStatus
from eor_control.pump_commands import (
    PumpCommand,
    PumpCommandKind,
    PumpCommandPriority,
    PumpCommandResult,
    PumpCommandStatus,
)
from eor_control.timezone import as_hungarian_time

T = TypeVar("T")


class PollablePump(Protocol):
    def connect(self) -> None: ...

    def read_pressure_bar(self) -> float: ...

    def read_flow_ml_per_hour(self) -> float: ...

    def read_remaining_volume_ml(self) -> float: ...

    def read_operating_status(self) -> str: ...

    def enter_remote(self) -> None: ...

    def set_constant_flow(self, flow_ml_per_hour: float) -> None: ...

    def read_configured_flow_ml_per_hour(self) -> float: ...

    def read_configured_pressure_bar(self) -> float: ...

    def set_constant_pressure(self, pressure_bar: float) -> None: ...

    def set_pressure_limit(self, pressure_bar: float) -> None: ...

    def run(self) -> None: ...

    def request_stop(self) -> None: ...

    def clear(self) -> None: ...

    def return_local(self) -> None: ...

    def disconnect(self) -> None: ...


@dataclass(frozen=True, slots=True)
class PumpPollingIntervals:
    pressure_seconds: float = 1.0
    slow_telemetry_seconds: float = 10.0
    status_poll_seconds: float = 4.0
    pressure_stale_seconds: float = 6.0
    slow_telemetry_stale_seconds: float = 33.0
    status_stale_seconds: float = 8.0
    startup_timeout_seconds: float = 8.0
    shutdown_timeout_seconds: float = 8.0

    def __post_init__(self) -> None:
        values = (
            self.pressure_seconds,
            self.slow_telemetry_seconds,
            self.status_poll_seconds,
            self.pressure_stale_seconds,
            self.slow_telemetry_stale_seconds,
            self.status_stale_seconds,
            self.startup_timeout_seconds,
            self.shutdown_timeout_seconds,
        )
        if not all(isfinite(value) and value > 0.0 for value in values):
            raise ValueError("pump polling intervals must be positive and finite")
        if self.pressure_stale_seconds < 3.0 * self.pressure_seconds:
            raise ValueError("pump pressure stale limit must cover three polling intervals")
        if self.slow_telemetry_stale_seconds < self.slow_telemetry_seconds:
            raise ValueError("slow pump telemetry stale limit must cover one polling interval")
        if self.status_stale_seconds < self.status_poll_seconds:
            raise ValueError("pump status stale limit must cover one polling interval")


class PumpConnectionState(StrEnum):
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"


@dataclass(frozen=True, slots=True)
class TelemetryFieldState:
    quality: DataQuality
    age_seconds: float | None
    last_update_monotonic: float | None
    last_error: str | None = None
    sequence: int = 0


@dataclass(frozen=True, slots=True)
class PumpTelemetrySnapshot:
    status: PumpStatus
    connection_state: PumpConnectionState
    pressure: TelemetryFieldState
    flow: TelemetryFieldState
    volume: TelemetryFieldState
    operating_status: TelemetryFieldState
    operating_status_text: str | None = None


@dataclass(frozen=True, slots=True)
class PumpWorkerSnapshot:
    """Cache-only diagnostics for one pump's isolated serial worker."""

    name: str
    serial_port: str
    worker_name: str
    worker_ident: int | None
    running: bool
    queue_size: int
    active_command: str | None
    pressure_age_seconds: float | None
    status_age_seconds: float | None
    last_transaction: str
    last_transaction_seconds: float
    polling_deadline_misses: int
    maximum_queue_size: int = 0
    transactions_total: int = 0
    last_polling_lateness_seconds: float = 0.0
    maximum_polling_lateness_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class _CachedTelemetry:
    status: PumpStatus
    pressure_at: float
    flow_at: float | None
    volume_at: float | None
    operating_status_at: float
    errors: tuple[tuple[str, str], ...] = ()
    pressure_sequence: int = 0


class PollingPump:
    """Keep blocking DASNET reads outside the control loop.

    One instance owns one pump and one worker. Pressure and basic operating status
    are required during connection; slower flow and volume telemetry is filled in
    by the worker afterwards. Control reads only use the timestamped cache.
    """

    COMMAND_QUEUE_WAIT_SECONDS = 5.0

    def __init__(
        self,
        pump: PollablePump,
        *,
        name: str,
        serial_port: str = "—",
        intervals: PumpPollingIntervals | None = None,
        diagnostics: DiagnosticLogger | None = None,
        diagnostic_category: DiagnosticCategory = DiagnosticCategory.SYSTEM,
        command_queue_capacity: int = 256,
    ) -> None:
        if command_queue_capacity < 4:
            raise ValueError("pump command queue capacity must be at least four")
        self._pump = pump
        self._name = name
        self._serial_port = serial_port
        self._intervals = intervals or PumpPollingIntervals()
        self._diagnostics = diagnostics
        self._diagnostic_category = diagnostic_category
        self._condition = Condition()
        self._command_queue_capacity = command_queue_capacity
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._cache: _CachedTelemetry | None = None
        self._worker_error: Exception | None = None
        self._close_error: Exception | None = None
        self._connected = False
        self._stop_latched = False
        self._stop_command_id: str | None = None
        self._pending_commands = 0
        self._command_sequence = 0
        self._command_queue: list[tuple[int, int, str]] = []
        self._command_results: dict[str, PumpCommandResult] = {}
        self._reported_qualities: dict[str, DataQuality] = {}
        self._last_command = "NONE"
        self._last_command_elapsed_ms = 0.0
        self._field_error_counts: dict[str, int] = {}
        self._operating_status_text: str | None = None
        self._preparation_active = False
        self._remote_supervision_active = False
        self._remote_recovery_command_id: str | None = None
        self._local_status_streak = 0
        self._active_command_id: str | None = None
        self._polling_deadline_misses = 0
        self._maximum_queue_size = 0
        self._transactions_total = 0
        self._last_polling_lateness_seconds = 0.0
        self._maximum_polling_lateness_seconds = 0.0
        self._last_successful_write: tuple[PumpCommandKind, float | None] | None = None
        self._last_successful_command_id: str | None = None

    @property
    def polling_intervals(self) -> PumpPollingIntervals:
        """Return the immutable timing configuration active in this worker."""
        return self._intervals

    @property
    def serial_is_open(self) -> bool:
        return bool(getattr(self._pump, "serial_is_open", False))

    def worker_snapshot(self) -> PumpWorkerSnapshot:
        """Return worker/queue health without initiating serial traffic."""

        with self._condition:
            thread = self._thread
            active = self._active_command_id
            active_result = self._command_results.get(active) if active else None
            return PumpWorkerSnapshot(
                name=self._name,
                serial_port=self._serial_port,
                worker_name=(thread.name if thread is not None else f"eor-{self._name}-pump-poll"),
                worker_ident=thread.ident if thread is not None else None,
                running=bool(thread is not None and thread.is_alive()),
                queue_size=self._pending_commands,
                active_command=(
                    active_result.command.kind.value if active_result is not None else None
                ),
                pressure_age_seconds=self._field_age_locked("pressure"),
                status_age_seconds=self._field_age_locked("status"),
                last_transaction=self._last_command,
                last_transaction_seconds=self._last_command_elapsed_ms / 1000.0,
                polling_deadline_misses=self._polling_deadline_misses,
                maximum_queue_size=self._maximum_queue_size,
                transactions_total=self._transactions_total,
                last_polling_lateness_seconds=self._last_polling_lateness_seconds,
                maximum_polling_lateness_seconds=self._maximum_polling_lateness_seconds,
            )

    def connect(self) -> None:
        with self._condition:
            if self._connected:
                return
            if self._close_error is not None:
                raise RuntimeError(
                    f"{self._name} pump previous serial close failed; reconnect denied"
                ) from self._close_error
            if self._thread is not None:
                detail = (
                    "worker is still stopping"
                    if self._thread.is_alive()
                    else "previous worker still requires disconnect cleanup"
                )
                raise RuntimeError(f"{self._name} pump {detail}; reconnect denied")
        with self._condition:
            self._stop_latched = False
            self._worker_error = None
            self._close_error = None
            self._cache = None
            self._last_successful_write = None
            self._last_successful_command_id = None
            self._polling_deadline_misses = 0
            self._maximum_queue_size = 0
            self._transactions_total = 0
            self._last_polling_lateness_seconds = 0.0
            self._maximum_polling_lateness_seconds = 0.0
            stop_event = Event()
            self._stop_event = stop_event
            self._thread = Thread(
                target=self._poll,
                args=(stop_event,),
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
        self._log_connection_event("TELEMETRY_CONNECTION_RESTORED", "SUCCESS")

    def read_status(self) -> PumpStatus:
        status, _ = self.read_cached_status()
        return status

    def read_cached_status(self) -> tuple[PumpStatus, DataQuality]:
        """Return the status and the safety-critical pressure quality.

        Pressure and operating mode are safety-critical. Flow and remaining
        volume retain independent quality and do not stop pressure control.
        """
        telemetry = self.read_telemetry()
        priority = {
            DataQuality.GOOD: 0,
            DataQuality.STALE: 1,
            DataQuality.INVALID: 2,
            DataQuality.OUT_OF_RANGE: 3,
            DataQuality.DISCONNECTED: 4,
        }
        quality = max(
            (telemetry.pressure.quality, telemetry.operating_status.quality),
            key=priority.__getitem__,
        )
        return telemetry.status, quality

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
            sequence=cache.pressure_sequence,
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
            self._intervals.status_stale_seconds,
            errors.get("status"),
            connected=connected,
        )
        fields = {
            "pressure": pressure,
            "flow": flow,
            "volume": volume,
            "status": operating_status,
        }
        self._log_quality_transitions(fields)
        status = cache.status
        if not connected:
            status = PumpStatus(
                pressure_bar=status.pressure_bar,
                flow_ml_per_hour=status.flow_ml_per_hour,
                remaining_volume_ml=status.remaining_volume_ml,
                connected=False,
            )
            state = PumpConnectionState.DISCONNECTED
        else:
            state = PumpConnectionState.CONNECTED
        return PumpTelemetrySnapshot(
            status=status,
            connection_state=state,
            pressure=pressure,
            flow=flow,
            volume=volume,
            operating_status=operating_status,
            operating_status_text=self._operating_status_text,
        )

    def is_stopped_local(self) -> bool:
        """Return cached STOP LOCAL state without issuing another serial read."""

        with self._condition:
            status_text = self._operating_status_text
        if status_text is None:
            return False
        status_tokens = set(status_text.upper().replace("=", " ").split())
        return {"STOP", "LOCAL"}.issubset(status_tokens)

    def is_stopped(self) -> bool:
        """Return cached STOP state in either Local or Remote mode."""

        with self._condition:
            status_text = self._operating_status_text
        if status_text is None:
            return False
        status_tokens = set(status_text.upper().replace("=", " ").split())
        return "STOP" in status_tokens

    def is_remote_mode(self) -> bool:
        """Return whether cached STATUS explicitly confirms Remote mode."""

        with self._condition:
            status_text = self._operating_status_text
        if status_text is None:
            return False
        normalized = status_text.upper()
        status_tokens = set(normalized.replace("=", " ").split())
        return (
            "REMOTE" in status_tokens
            and "LOCAL" not in status_tokens
            and "PROBLEM" not in normalized
        )

    def enter_remote(self) -> None:
        self._execute_command(PumpCommandKind.ENTER_REMOTE, verify_status=True)
        with self._condition:
            self._stop_latched = False

    def enter_remote_for_recovery(self, reason: str) -> None:
        with self._condition:
            if self._last_successful_write == (PumpCommandKind.ENTER_REMOTE, None):
                self._last_successful_write = None
                self._last_successful_command_id = None
        command = PumpCommand(
            PumpCommandKind.ENTER_REMOTE,
            PumpCommandPriority.HIGH,
            verify_status=True,
            queue_timeout_seconds=self.COMMAND_QUEUE_WAIT_SECONDS,
            reason=reason,
        )
        self._await_command_result(self.submit_command(command))
        with self._condition:
            self._stop_latched = False

    def set_constant_flow(self, flow_ml_per_hour: float) -> None:
        self._execute_command(PumpCommandKind.SET_CONSTANT_FLOW, value=flow_ml_per_hour)

    def read_configured_flow_ml_per_hour(self) -> float:
        result = self._execute_command(PumpCommandKind.READ_CONFIGURED_FLOW)
        if result.value is None:
            raise RuntimeError("pump configured flow readback returned no value")
        return result.value

    def read_configured_pressure_bar(self) -> float:
        result = self._execute_command(PumpCommandKind.READ_CONFIGURED_PRESSURE)
        if result.value is None:
            raise RuntimeError("pump configured pressure readback returned no value")
        return result.value

    def set_constant_pressure(self, pressure_bar: float) -> None:
        self._execute_command(
            PumpCommandKind.SET_CONSTANT_PRESSURE,
            value=pressure_bar,
        )

    def set_pressure_limit(self, pressure_bar: float) -> None:
        self._execute_command(PumpCommandKind.SET_PRESSURE_LIMIT, value=pressure_bar)

    def run(self) -> None:
        self._execute_command(
            PumpCommandKind.RUN,
            priority=PumpCommandPriority.NORMAL,
            verify_status=True,
        )
        with self._condition:
            self._stop_latched = False

    def request_stop(self) -> None:
        with self._condition:
            if self._stop_latched:
                return
        self._await_command_result(self.submit_stop(emergency=True))

    def acknowledge_stop_latch(self) -> None:
        with self._condition:
            self._stop_latched = False

    def clear(self) -> None:
        self._execute_command(PumpCommandKind.CLEAR)

    def return_local(self) -> None:
        self._execute_command(PumpCommandKind.RETURN_LOCAL)

    def disconnect(self) -> None:
        self.cancel_pending_commands()
        with self._condition:
            self._connected = False
            self._remote_supervision_active = False
            self._remote_recovery_command_id = None
            stop_event = self._stop_event
            stop_event.set()
            thread = self._thread
            self._condition.notify_all()
        self._log_connection_event("TELEMETRY_CONNECTION_LOST", "DISCONNECTED")
        if thread is not None and thread is not current_thread():
            cancel_read = getattr(self._pump, "cancel_pending_read", None)
            if callable(cancel_read):
                # Some serial backends do not support cancelling a read. The
                # independent shutdown timeout remains the hard bound.
                with suppress(Exception):
                    cancel_read()
            thread.join(self._intervals.shutdown_timeout_seconds)
            if thread.is_alive():
                error = TimeoutError(
                    f"{self._name} pump worker did not stop within "
                    f"{self._intervals.shutdown_timeout_seconds:.1f} s; "
                    "serial port left open and reconnect denied"
                )
                with self._condition:
                    self._worker_error = error
                    self._condition.notify_all()
                raise error
        with self._condition:
            close_error = self._close_error
            self._thread = None
            self._condition.notify_all()
        if close_error is not None:
            raise close_error

    def submit_command(
        self,
        command: PumpCommand,
        *,
        require_connected: bool = True,
    ) -> str:
        with self._condition:
            if require_connected and not self._connected:
                raise ConnectionError(f"{self._name} pump is disconnected")
            if self._thread is None or not self._thread.is_alive():
                raise ConnectionError(f"{self._name} pump worker is not running")
            deduplicated_kinds = {
                PumpCommandKind.ENTER_REMOTE,
                PumpCommandKind.SET_PRESSURE_LIMIT,
                PumpCommandKind.SET_CONSTANT_FLOW,
                PumpCommandKind.SET_CONSTANT_PRESSURE,
            }
            signature = (command.kind, command.value)
            if command.kind in deduplicated_kinds:
                for existing_id, existing in self._command_results.items():
                    if (
                        existing.command.kind is command.kind
                        and existing.command.value == command.value
                        and existing.status in {PumpCommandStatus.QUEUED, PumpCommandStatus.RUNNING}
                    ):
                        return existing_id
                if (
                    self._last_successful_write == signature
                    and self._last_successful_command_id is not None
                ):
                    return self._last_successful_command_id
            queued_results = [
                (command_id, result)
                for command_id, result in self._command_results.items()
                if result.status is PumpCommandStatus.QUEUED
            ]
            if len(queued_results) >= self._command_queue_capacity:
                if command.priority is not PumpCommandPriority.EMERGENCY:
                    raise RuntimeError(
                        f"{self._name} pump command queue is full "
                        f"({self._command_queue_capacity})"
                    )
                evictable = [
                    (command_id, result)
                    for command_id, result in queued_results
                    if result.command.priority is not PumpCommandPriority.EMERGENCY
                ]
                if not evictable:
                    raise RuntimeError(
                        f"{self._name} pump emergency command queue is full"
                    )
                evicted_id, evicted = max(
                    evictable,
                    key=lambda item: (
                        int(item[1].command.priority),
                        item[1].submitted_monotonic,
                    ),
                )
                self._command_results[evicted_id] = PumpCommandResult(
                    command_id=evicted.command_id,
                    command=evicted.command,
                    status=PumpCommandStatus.CANCELLED,
                    submitted_monotonic=evicted.submitted_monotonic,
                    completed_monotonic=monotonic(),
                    error="preempted by emergency STOP",
                )
                self._remove_queued_command_locked(evicted_id)
            self._command_sequence += 1
            command_id = f"{self._name}-{self._command_sequence:06d}"
            submitted = monotonic()
            self._command_results[command_id] = PumpCommandResult(
                command_id,
                command,
                PumpCommandStatus.QUEUED,
                submitted,
            )
            heappush(
                self._command_queue,
                (int(command.priority), self._command_sequence, command_id),
            )
            self._pending_commands = sum(
                result.status is PumpCommandStatus.QUEUED
                for result in self._command_results.values()
            )
            self._maximum_queue_size = max(
                self._maximum_queue_size,
                self._pending_commands,
            )
            self._condition.notify_all()
        self._log_command_event(command_id, command, "QUEUED")
        return command_id

    def set_preparation_active(self, active: bool) -> None:
        """Suspend nonessential FLOW/VOLA polling during pump preparation."""

        with self._condition:
            self._preparation_active = active
            self._condition.notify_all()

    def set_remote_supervision_active(self, active: bool) -> None:
        """Restore Remote mode from periodic STATUS only during active control."""

        with self._condition:
            self._remote_supervision_active = active
            self._local_status_streak = 0
            if not active and self._remote_recovery_command_id is not None:
                command_id = self._remote_recovery_command_id
                result = self._command_results.get(command_id)
                if result is not None and result.status is PumpCommandStatus.QUEUED:
                    self._command_results[command_id] = PumpCommandResult(
                        command_id=result.command_id,
                        command=result.command,
                        status=PumpCommandStatus.CANCELLED,
                        submitted_monotonic=result.submitted_monotonic,
                        completed_monotonic=monotonic(),
                        error="Remote supervision disabled",
                    )
                self._remote_recovery_command_id = None
            self._condition.notify_all()

    def command_result(self, command_id: str) -> PumpCommandResult:
        with self._condition:
            result = self._command_results.get(command_id)
            if result is None:
                raise KeyError(f"unknown pump command id: {command_id}")
            now = monotonic()
            timeout_phase: str | None = None
            timeout_limit = 0.0
            if (
                result.status is PumpCommandStatus.QUEUED
                and now - result.submitted_monotonic > result.command.queue_timeout_seconds
            ):
                timeout_phase = "queue"
                timeout_limit = result.command.queue_timeout_seconds
            elif result.status is PumpCommandStatus.RUNNING:
                if (
                    result.started_monotonic is not None
                    and result.execution_completed_monotonic is None
                    and now - result.started_monotonic > result.command.execution_timeout_seconds
                ):
                    timeout_phase = "execution"
                    timeout_limit = result.command.execution_timeout_seconds
                elif (
                    result.verification_started_monotonic is not None
                    and now - result.verification_started_monotonic
                    > result.command.verification_timeout_seconds
                ):
                    timeout_phase = "verification"
                    timeout_limit = result.command.verification_timeout_seconds
            if timeout_phase is not None:
                result = PumpCommandResult(
                    command_id=result.command_id,
                    command=result.command,
                    status=PumpCommandStatus.TIMED_OUT,
                    submitted_monotonic=result.submitted_monotonic,
                    started_monotonic=result.started_monotonic,
                    completed_monotonic=now,
                    error=(
                        f"{self._name} command {timeout_phase} timeout: "
                        f"{result.command.kind.value} exceeded {timeout_limit:.3g} s"
                    ),
                    execution_completed_monotonic=(result.execution_completed_monotonic),
                    verification_started_monotonic=(result.verification_started_monotonic),
                )
                self._command_results[command_id] = result
                if timeout_phase == "queue":
                    self._remove_queued_command_locked(command_id)
                self._pending_commands = sum(
                    queued_result.status is PumpCommandStatus.QUEUED
                    for queued_result in self._command_results.values()
                )
                self._condition.notify_all()
            return result

    def command_queue_snapshot(self) -> tuple[PumpCommandResult, ...]:
        """Return running and queued commands in their effective execution order."""

        with self._condition:
            active = tuple(
                result
                for result in self._command_results.values()
                if result.status
                in {
                    PumpCommandStatus.RUNNING,
                    PumpCommandStatus.QUEUED,
                }
            )
        return tuple(
            sorted(
                active,
                key=lambda result: (
                    0 if result.status is PumpCommandStatus.RUNNING else 1,
                    int(result.command.priority),
                    result.submitted_monotonic,
                ),
            )
        )

    def cancel_pending_commands(self) -> None:
        with self._condition:
            now = monotonic()
            while self._command_queue:
                _, _, command_id = heappop(self._command_queue)
                result = self._command_results[command_id]
                self._command_results[command_id] = PumpCommandResult(
                    command_id=result.command_id,
                    command=result.command,
                    status=PumpCommandStatus.CANCELLED,
                    submitted_monotonic=result.submitted_monotonic,
                    completed_monotonic=now,
                    error="cancelled by safety supervisor",
                )
            self._pending_commands = 0
            self._condition.notify_all()

    def cancel_command(self, command_id: str, *, reason: str) -> bool:
        """Cancel a command only while it is still queued.

        The heap entry is discarded lazily by ``_take_next_command``.  A
        command that has already started cannot be interrupted safely in the
        middle of a DASNET frame.
        """
        with self._condition:
            result = self._command_results.get(command_id)
            if result is None:
                raise KeyError(f"unknown pump command id: {command_id}")
            if result.status is not PumpCommandStatus.QUEUED:
                return False
            self._command_results[command_id] = PumpCommandResult(
                command_id=result.command_id,
                command=result.command,
                status=PumpCommandStatus.CANCELLED,
                submitted_monotonic=result.submitted_monotonic,
                completed_monotonic=monotonic(),
                error=reason,
            )
            self._remove_queued_command_locked(command_id)
            self._pending_commands = sum(
                queued_result.status is PumpCommandStatus.QUEUED
                for queued_result in self._command_results.values()
            )
            self._condition.notify_all()
            return True

    def _remove_queued_command_locked(self, command_id: str) -> None:
        """Remove a terminal queue entry immediately; caller owns the condition."""

        retained = [entry for entry in self._command_queue if entry[2] != command_id]
        if len(retained) == len(self._command_queue):
            return
        self._command_queue = retained
        heapify(self._command_queue)

    def submit_stop(self, *, emergency: bool = False) -> str:
        with self._condition:
            if self._stop_command_id is not None:
                previous = self._command_results.get(self._stop_command_id)
                if (
                    previous is not None
                    and (self._stop_latched or not previous.status.terminal)
                    and previous.status is not PumpCommandStatus.CANCELLED
                ):
                    return self._stop_command_id
            # Latch before queueing so concurrent safe-state paths share one STOP.
            self._stop_latched = True
            command_id = self.submit_command(
                PumpCommand(
                    PumpCommandKind.STOP,
                    (PumpCommandPriority.EMERGENCY if emergency else PumpCommandPriority.HIGH),
                    verify_status=True,
                ),
                require_connected=False,
            )
            self._stop_command_id = command_id
            return command_id

    def _execute_command(
        self,
        kind: PumpCommandKind,
        *,
        value: float | None = None,
        priority: PumpCommandPriority = PumpCommandPriority.HIGH,
        verify_status: bool = False,
        require_connected: bool = True,
    ) -> PumpCommandResult:
        command = PumpCommand(
            kind,
            priority,
            value,
            verify_status=verify_status,
            queue_timeout_seconds=self.COMMAND_QUEUE_WAIT_SECONDS,
        )
        command_id = self.submit_command(command, require_connected=require_connected)
        return self._await_command_result(command_id)

    def _await_command_result(self, command_id: str) -> PumpCommandResult:
        while True:
            result = self.command_result(command_id)
            if result.status.terminal:
                if result.status is PumpCommandStatus.SUCCEEDED:
                    return result
                if result.status is PumpCommandStatus.TIMED_OUT:
                    raise TimeoutError(result.error or "pump command timed out")
                if result.error is not None and result.error.startswith("ConnectionError:"):
                    raise ConnectionError(result.error.removeprefix("ConnectionError:").strip())
                raise RuntimeError(result.error or f"pump command {result.status.value}")
            with self._condition:
                # Queueing, execution and verification are supervised separately.
                self._condition.wait(timeout=0.05)

    def _poll(self, stop_event: Event) -> None:
        pump_opened = False
        try:
            self._pump.connect()
            pump_opened = True
            with self._condition:
                self._connected = True
                self._condition.notify_all()
            pressure = self._read("PRESS", self._pump.read_pressure_bar)
            pressure_at = monotonic()
            initial_status = self._read("STATUS", self._pump.read_operating_status)
            status_at = monotonic()
            with self._condition:
                self._operating_status_text = initial_status
                self._cache = _CachedTelemetry(
                    PumpStatus(pressure, 0.0, 0.0),
                    pressure_at,
                    None,
                    None,
                    status_at,
                    pressure_sequence=1,
                )
                self._condition.notify_all()

            schedule_origin = monotonic()
            next_pressure = schedule_origin + self._intervals.pressure_seconds
            next_status = schedule_origin + self._intervals.status_poll_seconds
            next_slow = schedule_origin
            slow_fields = ("flow", "volume")
            slow_step_seconds = self._intervals.slow_telemetry_seconds / len(
                slow_fields
            )
            slow_field_index = 0
            while not stop_event.is_set():
                # Every control command precedes scheduling another telemetry
                # transaction. The heap still guarantees emergency STOP first.
                queued = self._take_next_command()
                if queued is not None:
                    self._run_queued_command(*queued)
                    command_completed = monotonic()
                    next_pressure = command_completed
                    command_result = self.command_result(queued[0])
                    if (
                        queued[1].verify_status
                        and command_result.status is PumpCommandStatus.SUCCEEDED
                    ):
                        next_status = command_completed + self._intervals.status_poll_seconds
                    next_slow = command_completed + slow_step_seconds
                    continue
                now = monotonic()
                if now >= next_pressure:
                    field, due = "pressure", next_pressure
                elif now >= next_status:
                    field, due = "status", next_status
                elif not self._preparation_active and now >= next_slow:
                    field, due = slow_fields[slow_field_index], next_slow
                else:
                    candidates = [
                        ("pressure", next_pressure),
                        ("status", next_status),
                    ]
                    if not self._preparation_active:
                        candidates.append((slow_fields[slow_field_index], next_slow))
                    field, due = min(candidates, key=lambda candidate: candidate[1])
                with self._condition:
                    self._condition.wait_for(
                        lambda: bool(self._command_queue) or stop_event.is_set(),
                        timeout=max(0.0, due - monotonic()),
                    )
                    if stop_event.is_set():
                        break
                    if self._command_queue:
                        continue
                started = monotonic()
                lateness = max(0.0, started - due)
                with self._condition:
                    self._last_polling_lateness_seconds = lateness
                    self._maximum_polling_lateness_seconds = max(
                        self._maximum_polling_lateness_seconds,
                        lateness,
                    )
                interval = (
                    self._intervals.pressure_seconds
                    if field == "pressure"
                    else self._intervals.status_poll_seconds
                    if field == "status"
                    else slow_step_seconds
                )
                if lateness > interval:
                    with self._condition:
                        self._polling_deadline_misses += 1
                    self._log_field_event(
                        "POLLING_DEADLINE_MISSED",
                        field,
                        previous_quality=self._reported_qualities.get(field),
                        new_quality=self._reported_qualities.get(field),
                        age_seconds=lateness,
                        action="poll_field",
                        action_result="DEADLINE_MISSED",
                        level="WARNING",
                    )
                log_health = False
                try:
                    if field == "pressure":
                        self._update(
                            field,
                            self._read("PRESS", self._pump.read_pressure_bar),
                        )
                    elif field == "flow":
                        self._update(
                            field,
                            self._read("FLOW", self._pump.read_flow_ml_per_hour),
                        )
                    elif field == "volume":
                        self._update(
                            field,
                            self._read("VOLA", self._pump.read_remaining_volume_ml),
                        )
                    else:
                        status_text = self._read("STATUS", self._pump.read_operating_status)
                        self._update_operating_status(status_text)
                        self._schedule_remote_recovery(status_text)
                        log_health = True
                except Exception as field_error:
                    self._record_field_error(field, field_error)
                finally:
                    completed = monotonic()
                    if field == "pressure":
                        next_pressure = self._next_polling_deadline(
                            next_pressure,
                            completed,
                            self._intervals.pressure_seconds,
                        )
                    elif field == "status":
                        next_status = self._next_polling_deadline(
                            next_status,
                            completed,
                            self._intervals.status_poll_seconds,
                        )
                    else:
                        slow_field_index = (slow_field_index + 1) % len(slow_fields)
                        next_slow = self._next_polling_deadline(
                            next_slow,
                            completed,
                            slow_step_seconds,
                        )
                    if log_health:
                        self._log_telemetry_health()
        except Exception as error:
            with self._condition:
                if not stop_event.is_set():
                    self._worker_error = error
                    self._connected = False
                self._condition.notify_all()
            if not stop_event.is_set():
                self._log_connection_event(
                    "TELEMETRY_CONNECTION_LOST",
                    f"FAILED: {type(error).__name__}: {error}",
                )
        finally:
            if pump_opened:
                try:
                    self._pump.disconnect()
                except Exception as close_error:
                    with self._condition:
                        self._close_error = close_error
                        if self._worker_error is None:
                            self._worker_error = close_error
                        self._condition.notify_all()
            with self._condition:
                self._connected = False
                self._condition.notify_all()

    @staticmethod
    def _next_polling_deadline(
        previous_deadline: float,
        transaction_completed: float,
        interval: float,
    ) -> float:
        """Advance on a fixed grid and skip missed slots without catch-up bursts."""
        if transaction_completed < previous_deadline:
            return previous_deadline
        elapsed_intervals = int(
            (transaction_completed - previous_deadline) // interval
        )
        return previous_deadline + (elapsed_intervals + 1) * interval

    def _read(self, command: str, operation: Callable[[], T]) -> T:
        # Only this pump's worker calls the raw adapter. A transaction already
        # in progress finishes normally; queued controller commands are chosen
        # before the worker schedules another telemetry read.
        started = monotonic()
        with self._condition:
            self._last_command = command
        try:
            return operation()
        finally:
            with self._condition:
                self._last_command_elapsed_ms = (monotonic() - started) * 1000.0
                self._transactions_total += 1

    def _take_next_command(self) -> tuple[str, PumpCommand] | None:
        with self._condition:
            while self._command_queue:
                _, _, command_id = self._command_queue[0]
                result = self.command_result(command_id)
                if result.status is not PumpCommandStatus.QUEUED:
                    heappop(self._command_queue)
                    continue
                heappop(self._command_queue)
                started = monotonic()
                self._command_results[command_id] = PumpCommandResult(
                    command_id=result.command_id,
                    command=result.command,
                    status=PumpCommandStatus.RUNNING,
                    submitted_monotonic=result.submitted_monotonic,
                    started_monotonic=started,
                )
                self._pending_commands = sum(
                    queued.status is PumpCommandStatus.QUEUED
                    for queued in self._command_results.values()
                )
                self._active_command_id = command_id
                self._condition.notify_all()
                return command_id, result.command
            self._pending_commands = 0
            return None

    def _run_queued_command(self, command_id: str, command: PumpCommand) -> None:
        result = self.command_result(command_id)
        self._log_command_event(command_id, command, "RUNNING")
        value: float | None = None
        operating_status: str | None = None
        error: Exception | None = None
        verification_started: float | None = None
        verification_ms = 0.0
        try:
            value = self._read(
                command.kind.value,
                lambda: self._perform_command(command),
            )
        except Exception as command_error:
            error = command_error
        execution_completed = monotonic()
        execution_elapsed = execution_completed - (result.started_monotonic or execution_completed)

        if (
            error is None
            and command.verify_status
            and execution_elapsed <= command.execution_timeout_seconds
        ):
            verification_started = monotonic()
            with self._condition:
                current = self._command_results[command_id]
                if current.status is PumpCommandStatus.TIMED_OUT:
                    self._active_command_id = None
                    self._condition.notify_all()
                    self._log_command_event(
                        command_id,
                        command,
                        "LATE_COMPLETION",
                        error=current.error,
                    )
                    return
                self._command_results[command_id] = PumpCommandResult(
                    command_id=command_id,
                    command=command,
                    status=PumpCommandStatus.RUNNING,
                    submitted_monotonic=result.submitted_monotonic,
                    started_monotonic=result.started_monotonic,
                    value=value,
                    execution_completed_monotonic=execution_completed,
                    verification_started_monotonic=verification_started,
                )
                self._condition.notify_all()
            try:
                operating_status = self._read(
                    "STATUS_VERIFY",
                    self._pump.read_operating_status,
                )
                self._verify_command_status(command.kind, operating_status)
                self._update_operating_status(operating_status)
            except Exception as verification_error:
                error = verification_error
            verification_ms = (monotonic() - verification_started) * 1000.0
        completed = monotonic()
        with self._condition:
            current = self._command_results[command_id]
            if current.status is PumpCommandStatus.TIMED_OUT:
                self._active_command_id = None
                self._condition.notify_all()
                self._log_command_event(
                    command_id,
                    command,
                    "LATE_COMPLETION",
                    error=str(error) if error is not None else None,
                )
                return
            verification_elapsed = (
                completed - verification_started if verification_started is not None else 0.0
            )
            timeout_phase = (
                "execution"
                if execution_elapsed > command.execution_timeout_seconds
                else "verification"
                if verification_started is not None
                and verification_elapsed > command.verification_timeout_seconds
                else None
            )
            timeout_limit = (
                command.execution_timeout_seconds
                if timeout_phase == "execution"
                else command.verification_timeout_seconds
            )
            status = (
                PumpCommandStatus.TIMED_OUT
                if timeout_phase is not None
                else PumpCommandStatus.FAILED
                if error is not None
                else PumpCommandStatus.SUCCEEDED
            )
            message = (
                f"{self._name} command {timeout_phase} timeout: "
                f"{command.kind.value} exceeded "
                f"{timeout_limit:.3g} s"
                if timeout_phase is not None
                else f"{type(error).__name__}: {error}"
                if error is not None
                else None
            )
            final = PumpCommandResult(
                command_id=command_id,
                command=command,
                status=status,
                submitted_monotonic=result.submitted_monotonic,
                started_monotonic=result.started_monotonic,
                completed_monotonic=completed,
                value=value,
                operating_status=operating_status,
                error=message,
                execution_completed_monotonic=execution_completed,
                verification_started_monotonic=verification_started,
            )
            self._command_results[command_id] = final
            self._active_command_id = None
            if final.status is PumpCommandStatus.SUCCEEDED and command.kind in {
                PumpCommandKind.ENTER_REMOTE,
                PumpCommandKind.RUN,
            }:
                self._stop_latched = False
            elif (
                final.status is PumpCommandStatus.SUCCEEDED and command.kind is PumpCommandKind.STOP
            ):
                self._stop_latched = True
            if final.status is PumpCommandStatus.SUCCEEDED:
                self._last_successful_write = (command.kind, command.value)
                self._last_successful_command_id = command_id
            self._condition.notify_all()
        self._log_command_event(
            command_id,
            command,
            final.status.value,
            verification_ms=verification_ms,
            error=final.error,
        )

    def _schedule_remote_recovery(self, status_text: str) -> None:
        normalized = status_text.upper()
        tokens = set(normalized.replace("=", " ").split())
        with self._condition:
            if not self._remote_supervision_active:
                self._local_status_streak = 0
                return
            if "LOCAL" not in tokens:
                self._local_status_streak = 0
                return
            self._local_status_streak += 1
            if self._local_status_streak < 3:
                return
            if self._last_successful_write == (PumpCommandKind.ENTER_REMOTE, None):
                self._last_successful_write = None
                self._last_successful_command_id = None
            previous_id = self._remote_recovery_command_id
            if previous_id is not None:
                previous = self._command_results.get(previous_id)
                if previous is not None and not previous.status.terminal:
                    return
                if previous is not None:
                    # Automatic recovery results have no external consumer.
                    self._command_results.pop(previous_id, None)
            # Keep check, enqueue and tracked id atomic against supervision
            # shutdown so no late REMOTE can be queued after safe-state begins.
            command_id = self.submit_command(
                PumpCommand(
                    PumpCommandKind.ENTER_REMOTE,
                    PumpCommandPriority.HIGH,
                    verify_status=True,
                    queue_timeout_seconds=self.COMMAND_QUEUE_WAIT_SECONDS,
                    reason=("automatic recovery after 3 consecutive LOCAL periodic STATUS samples"),
                )
            )
            self._remote_recovery_command_id = command_id
            self._local_status_streak = 0

    def _perform_command(self, command: PumpCommand) -> float | None:
        value = command.value
        if command.kind is PumpCommandKind.ENTER_REMOTE:
            self._pump.enter_remote()
        elif command.kind is PumpCommandKind.SET_PRESSURE_LIMIT:
            assert value is not None
            self._pump.set_pressure_limit(value)
        elif command.kind is PumpCommandKind.SET_CONSTANT_FLOW:
            assert value is not None
            self._pump.set_constant_flow(value)
        elif command.kind is PumpCommandKind.SET_CONSTANT_PRESSURE:
            assert value is not None
            self._pump.set_constant_pressure(value)
        elif command.kind is PumpCommandKind.READ_CONFIGURED_FLOW:
            return self._pump.read_configured_flow_ml_per_hour()
        elif command.kind is PumpCommandKind.READ_CONFIGURED_PRESSURE:
            return self._pump.read_configured_pressure_bar()
        elif command.kind is PumpCommandKind.RUN:
            self._pump.run()
        elif command.kind is PumpCommandKind.STOP:
            self._pump.request_stop()
        elif command.kind is PumpCommandKind.CLEAR:
            self._pump.clear()
        else:
            self._pump.return_local()
        return None

    @staticmethod
    def _verify_command_status(kind: PumpCommandKind, status: str) -> None:
        normalized = status.upper()
        if kind is PumpCommandKind.ENTER_REMOTE:
            status_tokens = set(normalized.replace("=", " ").split())
            if "LOCAL" in status_tokens or "PROBLEM" in normalized or "REMOTE" not in status_tokens:
                raise RuntimeError(
                    f"pump STATUS did not confirm REMOTE: {status or 'empty response'}"
                )
            return
        expected = (
            "STOP"
            if kind is PumpCommandKind.STOP
            else "RUN"
            if kind is PumpCommandKind.RUN
            else "REMOTE"
        )
        if expected not in normalized:
            raise RuntimeError(
                f"pump STATUS did not confirm {expected}: {status or 'empty response'}"
            )

    def _update(self, field: str, value: float | None) -> None:
        now = monotonic()
        numeric_value = 0.0 if value is None else value
        with self._condition:
            cache = self._cache
            if cache is None:
                return
            status = cache.status
            errors = dict(cache.errors)
            recovered_error_count = self._field_error_counts.pop(field, 0)
            errors.pop(field, None)
            self._cache = _CachedTelemetry(
                PumpStatus(
                    pressure_bar=(numeric_value if field == "pressure" else status.pressure_bar),
                    flow_ml_per_hour=(
                        numeric_value if field == "flow" else status.flow_ml_per_hour
                    ),
                    remaining_volume_ml=(
                        numeric_value if field == "volume" else status.remaining_volume_ml
                    ),
                ),
                now if field == "pressure" else cache.pressure_at,
                now if field == "flow" else cache.flow_at,
                now if field == "volume" else cache.volume_at,
                now if field == "status" else cache.operating_status_at,
                tuple(sorted(errors.items())),
                cache.pressure_sequence + (1 if field == "pressure" else 0),
            )
            self._condition.notify_all()
        if recovered_error_count:
            self._log_field_event(
                "TELEMETRY_POLLING_RECOVERED",
                field,
                previous_quality=self._reported_qualities.get(field),
                new_quality=DataQuality.GOOD,
                age_seconds=0.0,
                action="poll_field",
                action_result=f"SUCCESS_AFTER_{recovered_error_count}_FAILURES",
                level="INFO",
            )

    def _update_operating_status(self, status_text: str) -> None:
        with self._condition:
            self._operating_status_text = status_text
            tokens = set(status_text.upper().replace("=", " ").split())
            if (
                "LOCAL" in tokens
                and self._last_successful_write
                == (PumpCommandKind.ENTER_REMOTE, None)
            ):
                self._last_successful_write = None
                self._last_successful_command_id = None
        self._update("status", None)

    def _record_field_error(self, field: str, error: Exception) -> None:
        with self._condition:
            cache = self._cache
            if cache is None:
                self._worker_error = error
                self._condition.notify_all()
                return
            errors = dict(cache.errors)
            error_count = self._field_error_counts.get(field, 0) + 1
            self._field_error_counts[field] = error_count
            errors[field] = str(error)
            self._cache = _CachedTelemetry(
                cache.status,
                cache.pressure_at,
                cache.flow_at,
                cache.volume_at,
                cache.operating_status_at,
                tuple(sorted(errors.items())),
                cache.pressure_sequence,
            )
            self._condition.notify_all()
        if error_count == 1 or error_count % 10 == 0:
            self._log(
                f"{field} telemetry failed; last successful field update retained; "
                f"repeat_count={error_count}: {error}",
                level="WARNING",
            )
        event_id = (
            "TELEMETRY_TIMEOUT"
            if isinstance(error, TimeoutError) or "timeout" in str(error).lower()
            else "TELEMETRY_PARSE_FAILED"
        )
        if error_count == 1 or error_count % 10 == 0:
            self._log_field_event(
                event_id,
                field,
                previous_quality=self._reported_qualities.get(field),
                new_quality=(
                    self._reported_qualities.get(field)
                    if event_id == "TELEMETRY_TIMEOUT"
                    else DataQuality.INVALID
                ),
                age_seconds=self._field_age(field),
                action="retain_last_successful_value",
                action_result=(
                    f"FAILED: {type(error).__name__}: {error}; repeat_count={error_count}"
                ),
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
        sequence: int = 0,
    ) -> TelemetryFieldState:
        age = None if updated_at is None else max(0.0, now - updated_at)
        if not connected:
            quality = DataQuality.DISCONNECTED
        elif error is not None and "timeout" not in error.lower():
            quality = DataQuality.INVALID
        elif updated_at is None or age is not None and age > stale_after:
            quality = DataQuality.STALE
        else:
            quality = DataQuality.GOOD
        return TelemetryFieldState(quality, age, updated_at, error, sequence)

    def _log_quality_transitions(self, fields: dict[str, TelemetryFieldState]) -> None:
        for name, field in fields.items():
            with self._condition:
                previous = self._reported_qualities.get(name)
                if previous is field.quality:
                    continue
                self._reported_qualities[name] = field.quality
            if previous is None and field.quality is DataQuality.GOOD:
                continue
            event_id = (
                "TELEMETRY_QUALITY_RECOVERED"
                if field.quality is DataQuality.GOOD
                else (
                    "TELEMETRY_CONNECTION_LOST"
                    if field.quality is DataQuality.DISCONNECTED
                    else "TELEMETRY_QUALITY_CHANGED"
                )
            )
            self._log_field_event(
                event_id,
                name,
                previous_quality=previous,
                new_quality=field.quality,
                age_seconds=field.age_seconds,
                action=(
                    "resume_fresh_telemetry"
                    if field.quality is DataQuality.GOOD
                    else "evaluate_safety_policy"
                ),
                action_result="SUCCESS",
                level=("INFO" if field.quality is DataQuality.GOOD else "WARNING"),
            )

    def _field_age(self, field: str) -> float | None:
        with self._condition:
            return self._field_age_locked(field)

    def _field_age_locked(self, field: str) -> float | None:
        cache = self._cache
        if cache is None:
            return None
        updated_at = {
            "pressure": cache.pressure_at,
            "flow": cache.flow_at,
            "volume": cache.volume_at,
            "status": cache.operating_status_at,
        }.get(field)
        return None if updated_at is None else max(0.0, monotonic() - updated_at)

    def _stale_limit(self, field: str) -> float:
        if field == "pressure":
            return self._intervals.pressure_stale_seconds
        if field == "status":
            return self._intervals.status_stale_seconds
        return self._intervals.slow_telemetry_stale_seconds

    def _log_field_event(
        self,
        event_id: str,
        field: str,
        *,
        previous_quality: DataQuality | None,
        new_quality: DataQuality | None,
        age_seconds: float | None,
        action: str,
        action_result: str,
        level: str,
    ) -> None:
        if self._diagnostics is None:
            return
        last_success = (
            "NONE"
            if age_seconds is None
            else (as_hungarian_time(datetime.now(UTC) - timedelta(seconds=age_seconds)).isoformat())
        )
        safety_rule = (
            f"{self._name.upper()}_{field.upper()}_"
            f"{(new_quality or previous_quality or DataQuality.GOOD).value.upper()}"
        )
        self._diagnostics.emit_event(
            self._diagnostic_category,
            event_id,
            fields={
                "device": f"{self._name}_pump",
                "field": field,
                "previous_quality": (
                    previous_quality.value if previous_quality is not None else "UNKNOWN"
                ),
                "new_quality": (new_quality.value if new_quality is not None else "UNKNOWN"),
                "age_ms": ("NONE" if age_seconds is None else round(age_seconds * 1000.0, 3)),
                "stale_limit_ms": round(self._stale_limit(field) * 1000.0, 3),
                "last_success_timestamp": last_success,
                "last_command": self._last_command,
                "last_command_elapsed_ms": round(self._last_command_elapsed_ms, 3),
                "safety_rule": safety_rule,
                "selected_fault_strategy": (
                    "FULL_SAFE_STOP" if field in {"pressure", "status"} else "DEGRADED_TELEMETRY"
                ),
                "action": action,
                "action_result": action_result,
            },
            direction="TELEMETRY",
            level=level,
        )

    def _log_connection_event(self, event_id: str, result: str) -> None:
        if self._diagnostics is None:
            return
        self._diagnostics.emit_event(
            self._diagnostic_category,
            event_id,
            fields={
                "device": f"{self._name}_pump",
                "field": "connection",
                "previous_quality": (
                    "disconnected" if event_id == "TELEMETRY_CONNECTION_RESTORED" else "good"
                ),
                "new_quality": (
                    "good" if event_id == "TELEMETRY_CONNECTION_RESTORED" else "disconnected"
                ),
                "last_command": self._last_command,
                "last_command_elapsed_ms": round(self._last_command_elapsed_ms, 3),
                "safety_rule": (
                    "NONE"
                    if event_id == "TELEMETRY_CONNECTION_RESTORED"
                    else f"{self._name.upper()}_CONNECTION_LOST"
                ),
                "selected_fault_strategy": (
                    "NONE" if event_id == "TELEMETRY_CONNECTION_RESTORED" else "FULL_SAFE_STOP"
                ),
                "action": (
                    "reconnect" if event_id == "TELEMETRY_CONNECTION_RESTORED" else "disconnect"
                ),
                "action_result": result,
            },
            direction="TELEMETRY",
            level="WARNING",
        )

    def _log_telemetry_health(self) -> None:
        telemetry = self.read_telemetry()

        def field_text(field: TelemetryFieldState) -> str:
            age = "never" if field.age_seconds is None else f"{field.age_seconds:.3f}s"
            updated = (
                "never"
                if field.last_update_monotonic is None
                else f"{field.last_update_monotonic:.6f}"
            )
            return f"{field.quality.value},age={age},last_success_monotonic={updated}"

        self._log(
            f"state={telemetry.connection_state.value}; "
            f"pressure[{field_text(telemetry.pressure)}]; "
            f"flow[{field_text(telemetry.flow)}]; "
            f"volume[{field_text(telemetry.volume)}]; "
            f"status[{field_text(telemetry.operating_status)}]"
        )

    def _log_command_event(
        self,
        command_id: str,
        command: PumpCommand,
        result: str,
        *,
        verification_ms: float = 0.0,
        error: str | None = None,
    ) -> None:
        if self._diagnostics is None:
            return
        snapshot = self.command_result(command_id)
        self._diagnostics.emit_event(
            self._diagnostic_category,
            "PUMP_COMMAND",
            fields={
                "pump": self._name,
                "com_port": self._serial_port,
                "worker_name": (self._thread.name if self._thread is not None else "NONE"),
                "thread_id": get_ident(),
                "command_id": command_id,
                "command": command.kind.value,
                "priority": int(command.priority),
                "queued_monotonic": round(snapshot.submitted_monotonic, 6),
                "queue_size": self._pending_commands,
                "queue_wait_ms": (
                    "NONE"
                    if snapshot.queue_wait_seconds is None
                    else round(snapshot.queue_wait_seconds * 1000.0, 3)
                ),
                "transaction_ms": (
                    "NONE"
                    if snapshot.transaction_seconds is None
                    else round(snapshot.transaction_seconds * 1000.0, 3)
                ),
                "execution_ms": (
                    "NONE"
                    if snapshot.execution_seconds is None
                    else round(snapshot.execution_seconds * 1000.0, 3)
                ),
                "verification_ms": round(verification_ms, 3),
                "result": result,
                "error": error or "NONE",
                "recovery_reason": command.reason or "NONE",
            },
            direction="COMMAND",
            level=("ERROR" if error is not None else "WARNING"),
        )

    def _log(self, message: str, *, level: str = "INFO") -> None:
        if self._diagnostics is not None:
            self._diagnostics.emit(
                self._diagnostic_category,
                "TELEMETRY",
                message,
                level=level,
            )
