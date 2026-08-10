from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from heapq import heappop, heappush
from math import isfinite
from threading import Condition, Event, Lock, Thread, current_thread
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

    def set_constant_pressure(self, pressure_bar: float) -> None: ...

    def set_pressure_limit(self, pressure_bar: float) -> None: ...

    def run(self) -> None: ...

    def request_stop(self) -> None: ...

    def clear(self) -> None: ...

    def return_local(self) -> None: ...

    def disconnect(self) -> None: ...


@dataclass(frozen=True, slots=True)
class PumpPollingIntervals:
    pressure_seconds: float = 0.5
    slow_telemetry_seconds: float = 0.5
    pressure_stale_seconds: float = 6.0
    slow_telemetry_stale_seconds: float = 33.0
    status_stale_seconds: float = 8.0
    startup_timeout_seconds: float = 8.0

    def __post_init__(self) -> None:
        values = (
            self.pressure_seconds,
            self.slow_telemetry_seconds,
            self.pressure_stale_seconds,
            self.slow_telemetry_stale_seconds,
            self.status_stale_seconds,
            self.startup_timeout_seconds,
        )
        if not all(isfinite(value) and value > 0.0 for value in values):
            raise ValueError("pump polling intervals must be positive and finite")
        if self.pressure_stale_seconds < 3.0 * self.pressure_seconds:
            raise ValueError("pump pressure stale limit must cover three polling intervals")
        if self.slow_telemetry_stale_seconds < self.slow_telemetry_seconds:
            raise ValueError("slow pump telemetry stale limit must cover one polling interval")
        if self.status_stale_seconds < self.slow_telemetry_seconds:
            raise ValueError("pump status stale limit must cover one polling interval")


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
    operating_status_text: str | None = None


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

    COMMAND_QUEUE_WAIT_SECONDS = 2.0

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
        self._command_sequence = 0
        self._command_queue: list[tuple[int, int, str]] = []
        self._command_results: dict[str, PumpCommandResult] = {}
        self._reported_qualities: dict[str, DataQuality] = {}
        self._last_command = "NONE"
        self._last_command_elapsed_ms = 0.0
        self._field_error_counts: dict[str, int] = {}
        self._operating_status_text: str | None = None

    @property
    def polling_intervals(self) -> PumpPollingIntervals:
        """Return the immutable timing configuration active in this worker."""
        return self._intervals

    def connect(self) -> None:
        with self._condition:
            if self._connected:
                return
            if self._thread is not None:
                detail = (
                    "worker is still stopping"
                    if self._thread.is_alive()
                    else "previous worker still requires disconnect cleanup"
                )
                raise RuntimeError(f"{self._name} pump {detail}; reconnect denied")
        with self._command_lock:
            self._pump.connect()
        with self._condition:
            self._connected = True
            self._stop_latched = False
            self._worker_error = None
            self._cache = None
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
        if (
            operating_status.quality is DataQuality.GOOD
            and self._operating_status_text is not None
            and "LOCAL" in self._operating_status_text.upper()
        ):
            operating_status = TelemetryFieldState(
                DataQuality.INVALID,
                operating_status.age_seconds,
                operating_status.last_update_monotonic,
                "pump is in LOCAL mode",
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
            operating_status_text=self._operating_status_text,
        )

    def enter_remote(self) -> None:
        self._execute_command(PumpCommandKind.ENTER_REMOTE, verify_status=True)
        with self._condition:
            self._stop_latched = False

    def set_constant_flow(self, flow_ml_per_hour: float) -> None:
        self._execute_command(PumpCommandKind.SET_CONSTANT_FLOW, value=flow_ml_per_hour)

    def read_configured_flow_ml_per_hour(self) -> float:
        result = self._execute_command(PumpCommandKind.READ_CONFIGURED_FLOW)
        if result.value is None:
            raise RuntimeError("pump configured flow readback returned no value")
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
            # Latch before I/O: a LOCAL MODE response or a timeout must not create
            # an endless STOP/reply loop in subsequent fault handling paths.
            self._stop_latched = True
        self._execute_command(
            PumpCommandKind.STOP,
            priority=PumpCommandPriority.HIGH,
            verify_status=True,
            require_connected=False,
        )

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
            stop_event = self._stop_event
            stop_event.set()
            thread = self._thread
            self._condition.notify_all()
        self._log_connection_event("TELEMETRY_CONNECTION_LOST", "DISCONNECTED")
        if thread is not None and thread is not current_thread():
            thread.join(self._intervals.startup_timeout_seconds)
            if thread.is_alive():
                error = TimeoutError(
                    f"{self._name} pump worker did not stop within "
                    f"{self._intervals.startup_timeout_seconds:.1f} s; "
                    "serial port left open and reconnect denied"
                )
                with self._condition:
                    self._worker_error = error
                    self._condition.notify_all()
                raise error
        try:
            with self._command_lock:
                self._pump.disconnect()
        finally:
            with self._condition:
                self._thread = None
                self._condition.notify_all()

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
            self._pending_commands = len(self._command_queue)
            self._condition.notify_all()
        self._log_command_event(command_id, command, "QUEUED")
        return command_id

    def command_result(self, command_id: str) -> PumpCommandResult:
        with self._condition:
            result = self._command_results.get(command_id)
            if result is None:
                raise KeyError(f"unknown pump command id: {command_id}")
            if (
                result.status is PumpCommandStatus.RUNNING
                and result.started_monotonic is not None
                and monotonic() - result.started_monotonic
                > result.command.timeout_seconds
            ):
                result = PumpCommandResult(
                    command_id=result.command_id,
                    command=result.command,
                    status=PumpCommandStatus.TIMED_OUT,
                    submitted_monotonic=result.submitted_monotonic,
                    started_monotonic=result.started_monotonic,
                    completed_monotonic=monotonic(),
                    error=(
                        f"{self._name} command timeout: {result.command.kind.value} "
                        f"was not completed within "
                        f"{result.command.timeout_seconds:.1f} s"
                    ),
                )
                self._command_results[command_id] = result
            return result

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
            self._pending_commands = sum(
                queued_result.status is PumpCommandStatus.QUEUED
                for queued_result in self._command_results.values()
            )
            self._condition.notify_all()
            return True

    def submit_stop(self, *, emergency: bool = False) -> str:
        return self.submit_command(
            PumpCommand(
                PumpCommandKind.STOP,
                (
                    PumpCommandPriority.EMERGENCY
                    if emergency
                    else PumpCommandPriority.HIGH
                ),
                verify_status=True,
            ),
            require_connected=False,
        )

    def _execute_command(
        self,
        kind: PumpCommandKind,
        *,
        value: float | None = None,
        priority: PumpCommandPriority = PumpCommandPriority.NORMAL,
        verify_status: bool = False,
        require_connected: bool = True,
    ) -> PumpCommandResult:
        command = PumpCommand(kind, priority, value, verify_status=verify_status)
        command_id = self.submit_command(command, require_connected=require_connected)
        queue_deadline = monotonic() + self.COMMAND_QUEUE_WAIT_SECONDS
        while True:
            result = self.command_result(command_id)
            if result.status.terminal:
                if result.status is PumpCommandStatus.SUCCEEDED:
                    return result
                if result.status is PumpCommandStatus.TIMED_OUT:
                    raise TimeoutError(result.error or "pump command timed out")
                if result.error is not None and result.error.startswith(
                    "ConnectionError:"
                ):
                    raise ConnectionError(
                        result.error.removeprefix("ConnectionError:").strip()
                    )
                raise RuntimeError(result.error or f"pump command {result.status.value}")
            if result.status is PumpCommandStatus.QUEUED:
                remaining = queue_deadline - monotonic()
                if remaining <= 0.0:
                    message = (
                        f"{self._name} queued command wait timed out and was "
                        f"cancelled: {kind.value}"
                    )
                    if self.cancel_command(command_id, reason=message):
                        raise TimeoutError(message)
                    continue
            else:
                # RUNNING time is supervised independently by command_result().
                # Never count queueing delay against the serial transaction.
                remaining = 0.05
            with self._condition:
                self._condition.wait(timeout=min(remaining, 0.05))

    def _poll(self, stop_event: Event) -> None:
        try:
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
                )
                self._condition.notify_all()

            schedule_origin = monotonic()
            next_pressure = schedule_origin + self._intervals.pressure_seconds
            next_status = schedule_origin + self._intervals.slow_telemetry_seconds
            next_slow = schedule_origin
            slow_fields = ("flow", "volume")
            slow_field_index = 0
            consecutive_pressure_reads = 0
            while not stop_event.is_set():
                # Emergency and STOP commands are the only traffic allowed to
                # overtake an overdue safety-critical pressure refresh.
                queued = self._take_next_command(
                    maximum_priority=PumpCommandPriority.HIGH
                )
                if queued is not None:
                    self._run_queued_command(*queued)
                    command_completed = monotonic()
                    next_pressure = command_completed
                    consecutive_pressure_reads = 0
                    if queued[1].verify_status:
                        next_status = (
                            command_completed
                            + self._intervals.slow_telemetry_seconds
                        )
                    next_slow = (
                        command_completed + self._intervals.slow_telemetry_seconds
                    )
                    continue
                now = monotonic()
                if now >= next_pressure and not (
                    now >= next_slow and consecutive_pressure_reads >= 1
                ):
                    field, due = "pressure", next_pressure
                elif now >= next_status:
                    # STATUS is safety-critical and cannot be starved by a
                    # continuous stream of normal CONFIG/RUN commands.
                    field, due = "status", next_status
                else:
                    queued = self._take_next_command()
                    if queued is not None:
                        self._run_queued_command(*queued)
                        command_completed = monotonic()
                        next_pressure = command_completed
                        consecutive_pressure_reads = 0
                        if queued[1].verify_status:
                            next_status = (
                                command_completed
                                + self._intervals.slow_telemetry_seconds
                            )
                        next_slow = (
                            command_completed
                            + self._intervals.slow_telemetry_seconds
                        )
                        continue
                    if now >= next_slow:
                        field, due = slow_fields[slow_field_index], next_slow
                    else:
                        field, due = min(
                            (
                                ("pressure", next_pressure),
                                ("status", next_status),
                                (slow_fields[slow_field_index], next_slow),
                            ),
                            key=lambda candidate: candidate[1],
                        )
                with self._condition:
                    self._condition.wait_for(
                        lambda: bool(self._command_queue)
                        or stop_event.is_set(),
                        timeout=max(0.0, due - monotonic()),
                    )
                    if stop_event.is_set():
                        break
                    urgent_command_queued = any(
                        priority <= int(PumpCommandPriority.HIGH)
                        and self._command_results[command_id].status
                        is PumpCommandStatus.QUEUED
                        for priority, _, command_id in self._command_queue
                    )
                    if self._command_queue and (
                        field not in {"pressure", "status"}
                        or monotonic() < due
                        or urgent_command_queued
                    ):
                        continue
                started = monotonic()
                lateness = max(0.0, started - due)
                interval = (
                    self._intervals.pressure_seconds
                    if field == "pressure"
                    else self._intervals.slow_telemetry_seconds
                )
                if lateness > interval:
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
                        status_text = self._read(
                            "STATUS", self._pump.read_operating_status
                        )
                        self._update_operating_status(status_text)
                        log_health = True
                except Exception as field_error:
                    self._record_field_error(field, field_error)
                finally:
                    completed = monotonic()
                    if field == "pressure":
                        consecutive_pressure_reads += 1
                        next_pressure = self._next_polling_deadline(
                            completed,
                            self._intervals.pressure_seconds,
                        )
                    elif field == "status":
                        consecutive_pressure_reads = 0
                        next_status = self._next_polling_deadline(
                            completed,
                            self._intervals.slow_telemetry_seconds,
                        )
                    else:
                        consecutive_pressure_reads = 0
                        slow_field_index = (slow_field_index + 1) % len(slow_fields)
                        next_slow = self._next_polling_deadline(
                            completed,
                            self._intervals.slow_telemetry_seconds,
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

    @staticmethod
    def _next_polling_deadline(
        transaction_completed: float,
        interval: float,
    ) -> float:
        """Schedule from completion; serial response time is part of the cadence."""
        return transaction_completed + interval

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

    def _take_next_command(
        self,
        *,
        maximum_priority: PumpCommandPriority | None = None,
    ) -> tuple[str, PumpCommand] | None:
        with self._condition:
            while self._command_queue:
                priority, _, command_id = self._command_queue[0]
                result = self._command_results[command_id]
                if result.status is not PumpCommandStatus.QUEUED:
                    heappop(self._command_queue)
                    continue
                if (
                    maximum_priority is not None
                    and priority > int(maximum_priority)
                ):
                    return None
                heappop(self._command_queue)
                started = monotonic()
                self._command_results[command_id] = PumpCommandResult(
                    command_id=result.command_id,
                    command=result.command,
                    status=PumpCommandStatus.RUNNING,
                    submitted_monotonic=result.submitted_monotonic,
                    started_monotonic=started,
                )
                self._pending_commands = len(self._command_queue)
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
        try:
            value = self._perform_command(command)
            if command.verify_status:
                verification_started = monotonic()
                operating_status = self._pump.read_operating_status()
                self._verify_command_status(command.kind, operating_status)
                self._update_operating_status(operating_status)
                verification_ms = (monotonic() - verification_started) * 1000.0
            else:
                verification_ms = 0.0
        except Exception as command_error:
            error = command_error
            verification_ms = 0.0
        completed = monotonic()
        with self._condition:
            current = self._command_results[command_id]
            if current.status is PumpCommandStatus.TIMED_OUT:
                self._condition.notify_all()
                self._log_command_event(
                    command_id,
                    command,
                    "LATE_COMPLETION",
                    error=str(error) if error is not None else None,
                )
                return
            elapsed = completed - (result.started_monotonic or completed)
            timed_out = elapsed > command.timeout_seconds
            status = (
                PumpCommandStatus.TIMED_OUT
                if timed_out
                else PumpCommandStatus.FAILED
                if error is not None
                else PumpCommandStatus.SUCCEEDED
            )
            message = (
                f"{self._name} command timeout: {command.kind.value} was not "
                f"completed within {command.timeout_seconds:.1f} s"
                if timed_out
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
            )
            self._command_results[command_id] = final
            self._condition.notify_all()
        self._log_command_event(
            command_id,
            command,
            final.status.value,
            verification_ms=verification_ms,
            error=final.error,
        )

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
            stripped = normalized.strip()
            responsive_state = (
                "STATUS=STOP" in normalized
                or "STATUS=RUN" in normalized
                or stripped.startswith("STOP")
                or stripped.startswith("RUN")
            )
            if (
                "LOCAL" in normalized
                or "PROBLEM" in normalized
                or not responsive_state
            ):
                raise RuntimeError(
                    "pump STATUS did not confirm REMOTE: "
                    f"{status or 'empty response'}"
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
                    f"FAILED: {type(error).__name__}: {error}; "
                    f"repeat_count={error_count}"
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
        return TelemetryFieldState(quality, age, updated_at, error)

    def _log_quality_transitions(
        self, fields: dict[str, TelemetryFieldState]
    ) -> None:
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
                level=(
                    "INFO"
                    if field.quality is DataQuality.GOOD
                    else "WARNING"
                ),
            )

    def _field_age(self, field: str) -> float | None:
        with self._condition:
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
            else (
                as_hungarian_time(
                    datetime.now(UTC) - timedelta(seconds=age_seconds)
                ).isoformat()
            )
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
                "new_quality": (
                    new_quality.value if new_quality is not None else "UNKNOWN"
                ),
                "age_ms": (
                    "NONE"
                    if age_seconds is None
                    else round(age_seconds * 1000.0, 3)
                ),
                "stale_limit_ms": round(self._stale_limit(field) * 1000.0, 3),
                "last_success_timestamp": last_success,
                "last_command": self._last_command,
                "last_command_elapsed_ms": round(
                    self._last_command_elapsed_ms, 3
                ),
                "safety_rule": safety_rule,
                "selected_fault_strategy": (
                    "FULL_SAFE_STOP"
                    if field in {"pressure", "status"}
                    else "DEGRADED_TELEMETRY"
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
                    "disconnected"
                    if event_id == "TELEMETRY_CONNECTION_RESTORED"
                    else "good"
                ),
                "new_quality": (
                    "good"
                    if event_id == "TELEMETRY_CONNECTION_RESTORED"
                    else "disconnected"
                ),
                "last_command": self._last_command,
                "last_command_elapsed_ms": round(
                    self._last_command_elapsed_ms, 3
                ),
                "safety_rule": (
                    "NONE"
                    if event_id == "TELEMETRY_CONNECTION_RESTORED"
                    else f"{self._name.upper()}_CONNECTION_LOST"
                ),
                "selected_fault_strategy": (
                    "NONE"
                    if event_id == "TELEMETRY_CONNECTION_RESTORED"
                    else "FULL_SAFE_STOP"
                ),
                "action": (
                    "reconnect"
                    if event_id == "TELEMETRY_CONNECTION_RESTORED"
                    else "disconnect"
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
                "command_id": command_id,
                "command": command.kind.value,
                "priority": int(command.priority),
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
                "verification_ms": round(verification_ms, 3),
                "result": result,
                "error": error or "NONE",
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
