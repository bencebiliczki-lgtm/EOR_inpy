from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from time import monotonic, sleep
from typing import Protocol, cast

from eor_control.diagnostics import DiagnosticCategory, DiagnosticLogger
from eor_control.domain import DataQuality, PumpStatus
from eor_control.pump_commands import (
    PumpCommand,
    PumpCommandKind,
    PumpCommandPriority,
    PumpCommandResult,
    PumpCommandStatus,
)
from eor_control.pump_telemetry import PumpWorkerSnapshot


class PumpRole(StrEnum):
    JACKET = "jacket"
    INJECTION = "injection"


class PumpOperatingMode(StrEnum):
    CONSTANT_FLOW = "constant_flow"
    CONSTANT_PRESSURE = "constant_pressure"


class _PreparationPhase(StrEnum):
    JACKET_REMOTE = "jacket_remote"
    INJECTION_REMOTE = "injection_remote"
    JACKET_PRESSURE_LIMIT = "jacket_pressure_limit"
    INJECTION_PRESSURE_LIMIT = "injection_pressure_limit"
    JACKET_FLOW = "jacket_flow"
    JACKET_RUN = "jacket_run"
    WAIT_MARGIN = "wait_margin"
    INJECTION_FLOW = "injection_flow"
    INJECTION_RUN = "injection_run"
    BUILD_PRESSURES = "build_pressures"
    INJECTION_MARGIN_STOP = "injection_margin_stop"
    WAIT_MARGIN_RECOVERY = "wait_margin_recovery"
    JACKET_TARGET_STOP = "jacket_target_stop"
    JACKET_CONSTANT_PRESSURE = "jacket_constant_pressure"
    JACKET_HOLD_RUN = "jacket_hold_run"
    INJECTION_TARGET_STOP = "injection_target_stop"
    VERIFY_TARGETS = "verify_targets"


class ControllablePump(Protocol):
    def connect(self) -> None: ...

    def read_status(self) -> PumpStatus: ...

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
class PumpPreparationState:
    remote: bool = False
    configured: bool = False
    running: bool = False
    mode: PumpOperatingMode | None = None
    target: float | None = None


@dataclass(frozen=True, slots=True)
class PumpStartupPlan:
    jacket_target_pressure_bar: float
    jacket_buildup_flow_ml_per_hour: float
    injection_start_pressure_bar: float
    injection_startup_flow_ml_per_hour: float
    injection_measurement_flow_ml_per_hour: float | None = None
    pressure_limit_bar: float | None = None
    minimum_jacket_margin_bar: float = 20.0
    margin_stability_seconds: float = 2.0
    jacket_pressure_limit_bar: float | None = None
    injection_pressure_limit_bar: float | None = None

    @property
    def injection_target_flow_ml_per_hour(self) -> float:
        """Compatibility alias for pre-split startup configurations."""
        return self.injection_startup_flow_ml_per_hour

    @property
    def effective_measurement_flow_ml_per_hour(self) -> float:
        value = self.injection_measurement_flow_ml_per_hour
        return self.injection_startup_flow_ml_per_hour if value is None else value

    @property
    def effective_jacket_pressure_limit_bar(self) -> float | None:
        return (
            self.pressure_limit_bar
            if self.jacket_pressure_limit_bar is None
            else self.jacket_pressure_limit_bar
        )

    @property
    def effective_injection_pressure_limit_bar(self) -> float | None:
        return (
            self.pressure_limit_bar
            if self.injection_pressure_limit_bar is None
            else self.injection_pressure_limit_bar
        )


@dataclass(frozen=True, slots=True)
class PumpControlTiming:
    control_interval_seconds: float = 0.2
    watchdog_tolerance_seconds: float = 0.05
    execution_timeout_seconds: float = 5.0
    queue_timeout_seconds: float = 5.0
    verification_timeout_seconds: float = 5.0


@dataclass(frozen=True, slots=True)
class _PendingPreparationCommand:
    role: PumpRole
    command_id: str
    command: PumpCommand
    next_phase: _PreparationPhase


@dataclass(frozen=True, slots=True)
class PumpPreparationProgress:
    phase: str
    jacket_pressure_bar: float
    jacket_target_pressure_bar: float
    injection_pressure_bar: float
    injection_target_pressure_bar: float
    pressure_margin_bar: float
    minimum_margin_bar: float
    jacket_state: str
    injection_state: str
    jacket_quality: DataQuality
    injection_quality: DataQuality
    jacket_age_seconds: float | None
    injection_age_seconds: float | None
    pending_command: str | None


class PumpControlService:
    AUTHORIZATION = "ENABLE PHYSICAL EOR HARDWARE"
    RUN_JACKET_CONFIRMATION = "RUN JACKET PUMP"
    RUN_INJECTION_CONFIRMATION = "RUN INJECTION PUMP"
    START_MEASUREMENT_CONFIRMATION = "START MEASUREMENT PUMPS"

    def __init__(
        self,
        *,
        jacket_pump: ControllablePump,
        injection_pump: ControllablePump,
        minimum_jacket_margin_bar: float = 20.0,
        diagnostics: DiagnosticLogger | None = None,
        safety_check: Callable[[], tuple[str, ...]] | None = None,
        manual_safety_check: Callable[[PumpRole, PumpStatus], tuple[str, ...]] | None = None,
        enforce_injection_margin: bool = True,
    ) -> None:
        if not isfinite(minimum_jacket_margin_bar) or minimum_jacket_margin_bar <= 0.0:
            raise ValueError("minimum jacket margin must be positive and finite")
        self._pumps = {
            PumpRole.JACKET: jacket_pump,
            PumpRole.INJECTION: injection_pump,
        }
        self._states = {
            PumpRole.JACKET: PumpPreparationState(),
            PumpRole.INJECTION: PumpPreparationState(),
        }
        self._connected = {role: False for role in PumpRole}
        self._minimum_margin = minimum_jacket_margin_bar
        self._diagnostics = diagnostics
        self._safety_check = safety_check
        self._manual_safety_check = manual_safety_check
        self._enforce_injection_margin = enforce_injection_margin
        self._authorized = False

    @property
    def minimum_jacket_margin_bar(self) -> float:
        return self._minimum_margin

    def set_minimum_jacket_margin_bar(self, value: float) -> None:
        if not isfinite(value) or value <= 0.0:
            raise ValueError("minimum jacket margin must be positive and finite")
        self._minimum_margin = value

    def authorize(self, confirmation: str) -> None:
        if confirmation != self.AUTHORIZATION:
            raise PermissionError("pump control authorization did not match")
        self._authorized = True
        self._log("AUTH", "physical pump control authorized")

    def state(self, role: PumpRole) -> PumpPreparationState:
        return self._states[role]

    def command_queue_snapshot(
        self,
    ) -> dict[PumpRole, tuple[PumpCommandResult, ...]]:
        """Expose the physical worker queues without issuing serial traffic."""

        snapshots: dict[PumpRole, tuple[PumpCommandResult, ...]] = {}
        for role, pump in self._pumps.items():
            reader = getattr(pump, "command_queue_snapshot", None)
            snapshots[role] = tuple(reader()) if callable(reader) else ()
        return snapshots

    def worker_snapshots(self) -> dict[PumpRole, PumpWorkerSnapshot | None]:
        """Expose cache-only worker diagnostics independently for each pump."""

        snapshots: dict[PumpRole, PumpWorkerSnapshot | None] = {}
        for role, pump in self._pumps.items():
            reader = getattr(pump, "worker_snapshot", None)
            snapshots[role] = reader() if callable(reader) else None
        return snapshots

    def connected(self, role: PumpRole) -> bool:
        return self._connected[role]

    def connect(self, role: PumpRole) -> PumpStatus:
        """Identify and connect one pump without requiring any other device."""

        self._require_authorized()
        self._pumps[role].connect()
        self._connected[role] = True
        self._states[role] = PumpPreparationState()
        self._log(role.value, "CONNECTED")
        return self._supervision_status(role)

    def disconnect(self, role: PumpRole) -> None:
        """Stop and disconnect one pump independently."""

        self._require_authorized()
        if self._states[role].running:
            raise RuntimeError("pump must be stopped before disconnect")
        if not self._is_stopped(role):
            self.stop(role)
        self._pumps[role].disconnect()
        self._connected[role] = False
        self._states[role] = PumpPreparationState()
        self._log(role.value, "DISCONNECTED")

    def observe_connected(self, *roles: PumpRole) -> None:
        """Synchronize state after the application service connected devices."""

        for role in roles:
            self._connected[role] = True

    def observe_disconnected(self, *roles: PumpRole) -> None:
        for role in roles:
            self._set_pump_remote_supervision(role, False)
            self._connected[role] = False
            self._states[role] = PumpPreparationState()

    def set_remote_supervision_active(self, active: bool) -> None:
        """Enable periodic Remote recovery for connected controlled pumps."""

        self._require_authorized()
        for role in PumpRole:
            if active and not self._connected[role]:
                continue
            self._set_pump_remote_supervision(role, active)

    def _set_pump_remote_supervision(self, role: PumpRole, active: bool) -> None:
        setter = getattr(self._pumps[role], "set_remote_supervision_active", None)
        if callable(setter):
            setter(active)

    def shutdown_connections(self) -> tuple[str, ...]:
        """Attempt STOP and disconnect for every individually connected pump."""

        for role in PumpRole:
            self._set_pump_remote_supervision(role, False)
        errors: list[str] = []
        for role, pump in self._pumps.items():
            if self._connected[role] and not self._is_stopped(role):
                try:
                    pump.request_stop()
                except Exception as error:
                    errors.append(f"{role.value} STOP: {error}")
            try:
                pump.disconnect()
            except Exception as error:
                errors.append(f"{role.value} disconnect: {error}")
            self._connected[role] = False
            self._states[role] = PumpPreparationState()
        self._authorized = False
        return tuple(errors)

    def read_available_statuses(
        self,
    ) -> tuple[dict[PumpRole, PumpStatus], dict[PumpRole, str]]:
        """Read each connected pump independently and retain partial success."""

        self._require_authorized()
        statuses: dict[PumpRole, PumpStatus] = {}
        errors: dict[PumpRole, str] = {}
        for role in self._pumps:
            if not self._connected[role]:
                errors[role] = "nincs csatlakoztatva"
                continue
            try:
                statuses[role] = self._supervision_status(role)
            except Exception as error:
                errors[role] = str(error)
        return statuses, errors

    def statuses(self) -> dict[PumpRole, PumpStatus]:
        self._require_authorized()
        for role in PumpRole:
            self._require_connected(role)
        return {role: self._supervision_status(role) for role in PumpRole}

    def _supervision_status(self, role: PumpRole) -> PumpStatus:
        """Use cache-only status for pumps that expose an asynchronous worker."""
        return self._supervision_sample(role)[0]

    def _supervision_sample(
        self,
        role: PumpRole,
    ) -> tuple[PumpStatus, DataQuality]:
        """Read cached control telemetry and its safety-critical quality."""
        pump = self._pumps[role]
        cache_reader = getattr(pump, "read_cached_status", None)
        if callable(cache_reader):
            status, quality = cache_reader()
            return cast(PumpStatus, status), cast(DataQuality, quality)
        submit = getattr(pump, "submit_command", None)
        result_reader = getattr(pump, "command_result", None)
        if callable(submit) or callable(result_reader):
            if not callable(submit) or not callable(result_reader):
                raise RuntimeError(f"{role.value} pump has an incomplete asynchronous interface")
            raise RuntimeError(f"{role.value} asynchronous pump has no cache-only status reader")
        if callable(getattr(pump, "read_pressure_bar", None)) and callable(
            getattr(pump, "read_operating_status", None)
        ):
            raise RuntimeError(
                f"{role.value} raw pump cannot be used by control; PollingPump required"
            )
        return pump.read_status(), DataQuality.GOOD

    def _supervision_statuses(self) -> dict[PumpRole, PumpStatus]:
        self._require_authorized()
        for role in PumpRole:
            self._require_connected(role)
        return {role: self._supervision_status(role) for role in PumpRole}

    def enter_remote(self, role: PumpRole) -> None:
        self._require_authorized()
        self._require_connected(role)
        self._pumps[role].enter_remote()
        self._states[role] = PumpPreparationState(remote=True)
        self._log(role.value, "REMOTE")

    def _enter_remote_preserving_state(
        self,
        role: PumpRole,
        *,
        reason: str | None = None,
    ) -> None:
        recovery = getattr(self._pumps[role], "enter_remote_for_recovery", None)
        if reason is not None and callable(recovery):
            recovery(reason)
        else:
            self._pumps[role].enter_remote()
        self._mark_remote(role)
        self._log(
            role.value,
            "REMOTE" if reason is None else f"REMOTE recovery; reason={reason}",
        )

    def _mark_remote(self, role: PumpRole) -> None:
        state = self._states[role]
        self._states[role] = PumpPreparationState(
            remote=True,
            configured=state.configured,
            running=state.running,
            mode=state.mode,
            target=state.target,
        )

    def _execute_remote_write(
        self,
        role: PumpRole,
        operation: Callable[[], None],
    ) -> None:
        """Apply the single Remote precheck and retry once after a Local error."""

        self._require_authorized()
        self._require_connected(role)
        if self._is_remote_mode(role):
            self._mark_remote(role)
            self._log(role.value, "REMOTE already confirmed; command skipped")
        else:
            self._enter_remote_preserving_state(role)
        try:
            operation()
        except Exception as error:
            if "LOCAL" not in str(error).upper():
                raise
            self._enter_remote_preserving_state(
                role,
                reason=f"{type(error).__name__}: {error}",
            )
            operation()

    def configure(self, role: PumpRole, mode: PumpOperatingMode, target: float) -> None:
        self._require_authorized()
        self._require_connected(role)
        if not isfinite(target) or target < 0.0:
            raise ValueError("pump target must be nonnegative and finite")
        pump = self._pumps[role]

        def operation() -> None:
            state = self._states[role]
            if not state.remote or state.running:
                raise RuntimeError("pump must be stopped in REMOTE mode before configuration")
            if mode is PumpOperatingMode.CONSTANT_FLOW:
                pump.set_constant_flow(target)
            else:
                pump.set_constant_pressure(target)

        self._execute_remote_write(role, operation)
        self._states[role] = PumpPreparationState(
            remote=True, configured=True, mode=mode, target=target
        )
        self._log(role.value, f"configured {mode.value} target={target}")

    def run(self, role: PumpRole, confirmation: str) -> None:
        self._run_configured(
            role,
            confirmation,
            enforce_injection_margin=self._enforce_injection_margin,
        )

    def _run_configured(
        self,
        role: PumpRole,
        confirmation: str,
        *,
        enforce_injection_margin: bool,
    ) -> None:
        self._require_authorized()
        self._require_connected(role)
        expected = (
            self.RUN_JACKET_CONFIRMATION
            if role is PumpRole.JACKET
            else self.RUN_INJECTION_CONFIRMATION
        )
        if confirmation != expected:
            raise PermissionError("pump RUN confirmation did not match")
        if self._safety_check is not None:
            reasons = self._safety_check()
            if reasons:
                raise PermissionError("safety interlock active: " + "; ".join(reasons))
        if self._manual_safety_check is not None:
            status = self._supervision_status(role)
            reasons = self._manual_safety_check(role, status)
            if reasons:
                raise PermissionError("manual safety interlock active: " + "; ".join(reasons))

        def operation() -> None:
            state = self._states[role]
            if not state.remote or not state.configured or state.running:
                raise RuntimeError("pump must be configured and stopped in REMOTE mode")
            if role is PumpRole.INJECTION and enforce_injection_margin:
                self._require_injection_start_margin()
            self._pumps[role].run()

        self._execute_remote_write(role, operation)
        state = self._states[role]
        self._states[role] = PumpPreparationState(
            remote=True,
            configured=True,
            running=True,
            mode=state.mode,
            target=state.target,
        )
        self._log(role.value, "RUN", level="WARNING")

    def start_measurement_pumps(
        self,
        *,
        jacket_target_pressure_bar: float,
        jacket_buildup_flow_ml_per_hour: float,
        injection_start_pressure_bar: float,
        injection_target_flow_ml_per_hour: float,
        pressure_limit_bar: float | None = None,
        jacket_pressure_limit_bar: float | None = None,
        injection_pressure_limit_bar: float | None = None,
        minimum_jacket_margin_bar: float | None = None,
        confirmation: str,
        startup_safety_check: Callable[[], tuple[str, ...]] | None = None,
        control_interval_seconds: float = 0.2,
        control_watchdog_tolerance_seconds: float = 0.05,
        margin_stability_seconds: float = 0.0,
    ) -> None:
        """Compatibility entry point for the canonical preparation workflow."""
        self.prepare_measurement_pumps(
            PumpStartupPlan(
                jacket_target_pressure_bar=jacket_target_pressure_bar,
                jacket_buildup_flow_ml_per_hour=jacket_buildup_flow_ml_per_hour,
                injection_start_pressure_bar=injection_start_pressure_bar,
                injection_startup_flow_ml_per_hour=injection_target_flow_ml_per_hour,
                pressure_limit_bar=pressure_limit_bar,
                jacket_pressure_limit_bar=jacket_pressure_limit_bar,
                injection_pressure_limit_bar=injection_pressure_limit_bar,
                minimum_jacket_margin_bar=(
                    self._minimum_margin
                    if minimum_jacket_margin_bar is None
                    else minimum_jacket_margin_bar
                ),
                margin_stability_seconds=margin_stability_seconds,
            ),
            timing=PumpControlTiming(
                control_interval_seconds=control_interval_seconds,
                watchdog_tolerance_seconds=control_watchdog_tolerance_seconds,
            ),
            confirmation=confirmation,
            startup_safety_check=startup_safety_check,
        )

    def prepare_measurement_pumps(
        self,
        plan: PumpStartupPlan,
        *,
        timing: PumpControlTiming,
        confirmation: str,
        startup_safety_check: Callable[[], tuple[str, ...]] | None = None,
        progress_callback: Callable[[PumpPreparationProgress], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> None:
        """Run the single supervised pump-preparation state machine."""
        self._require_authorized()
        if confirmation != self.START_MEASUREMENT_CONFIRMATION:
            raise PermissionError("measurement pump-start confirmation did not match")
        self._validate_startup(plan, timing)
        self.set_minimum_jacket_margin_bar(plan.minimum_jacket_margin_bar)
        for role in PumpRole:
            self._require_connected(role)

        self._set_preparation_polling(True)
        try:
            self._run_preparation_state_machine(
                plan,
                timing,
                startup_safety_check,
                progress_callback,
                cancel_check,
            )
        finally:
            self._set_preparation_polling(False)
        self.set_remote_supervision_active(True)

    def _run_preparation_state_machine(
        self,
        plan: PumpStartupPlan,
        timing: PumpControlTiming,
        startup_safety_check: Callable[[], tuple[str, ...]] | None,
        progress_callback: Callable[[PumpPreparationProgress], None] | None,
        cancel_check: Callable[[], bool] | None,
    ) -> None:
        """Advance state from cache while pump workers execute serial commands."""
        phase = _PreparationPhase.JACKET_REMOTE
        next_control = monotonic()
        stable_since: float | None = None
        pending: _PendingPreparationCommand | None = None
        resume_after_jacket_hold = _PreparationPhase.WAIT_MARGIN
        margin_recovery_hysteresis_bar = 1.0

        while True:
            cycle_started = monotonic()
            if cancel_check is not None and cancel_check():
                raise InterruptedError("measurement pump preparation was cancelled")
            self._require_startup_safe(startup_safety_check)
            samples = {role: self._supervision_sample(role) for role in PumpRole}
            self._require_control_deadline(
                cycle_started,
                timing.control_interval_seconds,
                timing.watchdog_tolerance_seconds,
            )
            jacket_status, jacket_quality = samples[PumpRole.JACKET]
            injection_status, injection_quality = samples[PumpRole.INJECTION]
            if jacket_quality is not DataQuality.GOOD:
                raise RuntimeError(
                    f"jacket pump telemetry quality is {jacket_quality.value} during preparation"
                )
            if injection_quality is not DataQuality.GOOD:
                raise RuntimeError(
                    "injection pump telemetry quality is "
                    f"{injection_quality.value} during preparation"
                )
            jacket_pressure = jacket_status.pressure_bar
            injection_pressure = injection_status.pressure_bar
            margin = jacket_pressure - injection_pressure
            now = monotonic()
            command_executed = False

            if progress_callback is not None:
                progress_callback(
                    self._preparation_progress(
                        phase,
                        plan,
                        jacket_pressure,
                        injection_pressure,
                        jacket_quality,
                        injection_quality,
                        pending,
                    )
                )

            if pending is not None:
                result = self._preparation_command_result(pending)
                target_reached_while_run_queued = (
                    result.status is PumpCommandStatus.QUEUED
                    and pending.command.kind is PumpCommandKind.RUN
                    and (
                        pending.role is PumpRole.JACKET
                        and self._states[PumpRole.JACKET].mode is PumpOperatingMode.CONSTANT_FLOW
                        and jacket_pressure >= plan.jacket_target_pressure_bar
                        or pending.role is PumpRole.INJECTION
                        and injection_pressure >= plan.injection_start_pressure_bar
                    )
                )
                if target_reached_while_run_queued:
                    cancel = getattr(
                        self._pumps[pending.role],
                        "cancel_command",
                        None,
                    )
                    if callable(cancel) and cancel(
                        pending.command_id,
                        reason="preparation target reached before queued RUN",
                    ):
                        phase = (
                            _PreparationPhase.JACKET_CONSTANT_PRESSURE
                            if pending.role is PumpRole.JACKET
                            else _PreparationPhase.VERIFY_TARGETS
                        )
                        pending = None
                        next_control = self._wait_for_control_deadline(
                            next_control,
                            timing.control_interval_seconds,
                            cycle_started,
                            timing.watchdog_tolerance_seconds,
                        )
                        continue
                if result.status is PumpCommandStatus.QUEUED:
                    if now - result.submitted_monotonic > timing.queue_timeout_seconds:
                        raise TimeoutError(
                            f"{pending.role.value} command queue timeout: "
                            f"{pending.command.kind.value} waited more than "
                            f"{timing.queue_timeout_seconds:.1f} s"
                        )
                elif result.status is PumpCommandStatus.RUNNING:
                    pass
                elif result.status is PumpCommandStatus.SUCCEEDED:
                    self._apply_preparation_command_success(pending)
                    phase = pending.next_phase
                    pending = None
                elif result.status is PumpCommandStatus.TIMED_OUT:
                    if pending.command.kind is PumpCommandKind.ENTER_REMOTE:
                        raise RuntimeError(
                            f"A {pending.role.value} pumpa nem állítható Remote módba."
                        )
                    raise TimeoutError(
                        result.error
                        or f"{pending.role.value} command timed out: {pending.command.kind.value}"
                    )
                else:
                    if pending.command.kind is PumpCommandKind.ENTER_REMOTE:
                        raise RuntimeError(
                            f"A {pending.role.value} pumpa nem állítható Remote módba."
                        )
                    raise RuntimeError(
                        result.error
                        or f"{pending.role.value} command failed: "
                        f"{pending.command.kind.value} ({result.status.value})"
                    )
                next_control = self._wait_for_control_deadline(
                    next_control,
                    timing.control_interval_seconds,
                    cycle_started,
                    timing.watchdog_tolerance_seconds,
                )
                continue

            if phase is _PreparationPhase.JACKET_REMOTE:
                pending = self._start_preparation_command(
                    PumpRole.JACKET,
                    PumpCommandKind.ENTER_REMOTE,
                    _PreparationPhase.INJECTION_REMOTE,
                    timing,
                )
                if pending is None:
                    phase = _PreparationPhase.INJECTION_REMOTE
                command_executed = True
            elif phase is _PreparationPhase.INJECTION_REMOTE:
                pending = self._start_preparation_command(
                    PumpRole.INJECTION,
                    PumpCommandKind.ENTER_REMOTE,
                    _PreparationPhase.JACKET_PRESSURE_LIMIT,
                    timing,
                )
                if pending is None:
                    phase = _PreparationPhase.JACKET_PRESSURE_LIMIT
                command_executed = True
            elif phase is _PreparationPhase.JACKET_PRESSURE_LIMIT:
                if plan.effective_jacket_pressure_limit_bar is not None:
                    pending = self._start_preparation_command(
                        PumpRole.JACKET,
                        PumpCommandKind.SET_PRESSURE_LIMIT,
                        _PreparationPhase.INJECTION_PRESSURE_LIMIT,
                        timing,
                        value=plan.effective_jacket_pressure_limit_bar,
                    )
                    command_executed = True
                    if pending is None:
                        phase = _PreparationPhase.INJECTION_PRESSURE_LIMIT
                else:
                    phase = _PreparationPhase.INJECTION_PRESSURE_LIMIT
            elif phase is _PreparationPhase.INJECTION_PRESSURE_LIMIT:
                if plan.effective_injection_pressure_limit_bar is not None:
                    pending = self._start_preparation_command(
                        PumpRole.INJECTION,
                        PumpCommandKind.SET_PRESSURE_LIMIT,
                        _PreparationPhase.JACKET_FLOW,
                        timing,
                        value=plan.effective_injection_pressure_limit_bar,
                    )
                    command_executed = True
                    if pending is None:
                        phase = _PreparationPhase.JACKET_FLOW
                else:
                    phase = _PreparationPhase.JACKET_FLOW
            elif phase is _PreparationPhase.JACKET_FLOW:
                if jacket_pressure >= plan.jacket_target_pressure_bar:
                    resume_after_jacket_hold = _PreparationPhase.WAIT_MARGIN
                    phase = _PreparationPhase.JACKET_CONSTANT_PRESSURE
                else:
                    pending = self._start_preparation_command(
                        PumpRole.JACKET,
                        PumpCommandKind.SET_CONSTANT_FLOW,
                        _PreparationPhase.JACKET_RUN,
                        timing,
                        value=plan.jacket_buildup_flow_ml_per_hour,
                    )
                    if pending is None:
                        phase = _PreparationPhase.JACKET_RUN
                    command_executed = True
            elif phase is _PreparationPhase.JACKET_RUN:
                if jacket_pressure >= plan.jacket_target_pressure_bar:
                    resume_after_jacket_hold = _PreparationPhase.WAIT_MARGIN
                    phase = _PreparationPhase.JACKET_CONSTANT_PRESSURE
                else:
                    pending = self._start_preparation_command(
                        PumpRole.JACKET,
                        PumpCommandKind.RUN,
                        _PreparationPhase.WAIT_MARGIN,
                        timing,
                    )
                    if pending is None:
                        phase = _PreparationPhase.WAIT_MARGIN
                    command_executed = True
            elif phase is _PreparationPhase.WAIT_MARGIN:
                if (
                    jacket_pressure >= plan.jacket_target_pressure_bar
                    and self._states[PumpRole.JACKET].mode
                    is not PumpOperatingMode.CONSTANT_PRESSURE
                ):
                    resume_after_jacket_hold = _PreparationPhase.WAIT_MARGIN
                    phase = _PreparationPhase.JACKET_TARGET_STOP
                else:
                    margin_ready = margin >= plan.minimum_jacket_margin_bar
                    stable_since = (
                        now
                        if margin_ready and stable_since is None
                        else stable_since
                        if margin_ready
                        else None
                    )
                    if (
                        stable_since is not None
                        and now - stable_since >= plan.margin_stability_seconds
                    ):
                        phase = _PreparationPhase.INJECTION_FLOW
            elif phase is _PreparationPhase.JACKET_TARGET_STOP:
                pending = self._start_preparation_command(
                    PumpRole.JACKET,
                    PumpCommandKind.STOP,
                    _PreparationPhase.JACKET_CONSTANT_PRESSURE,
                    timing,
                )
                if pending is None:
                    phase = _PreparationPhase.JACKET_CONSTANT_PRESSURE
                command_executed = True
            elif phase is _PreparationPhase.JACKET_CONSTANT_PRESSURE:
                pending = self._start_preparation_command(
                    PumpRole.JACKET,
                    PumpCommandKind.SET_CONSTANT_PRESSURE,
                    _PreparationPhase.JACKET_HOLD_RUN,
                    timing,
                    value=plan.jacket_target_pressure_bar,
                )
                if pending is None:
                    phase = _PreparationPhase.JACKET_HOLD_RUN
                command_executed = True
            elif phase is _PreparationPhase.JACKET_HOLD_RUN:
                pending = self._start_preparation_command(
                    PumpRole.JACKET,
                    PumpCommandKind.RUN,
                    resume_after_jacket_hold,
                    timing,
                )
                if pending is None:
                    phase = resume_after_jacket_hold
                command_executed = True
            elif phase is _PreparationPhase.INJECTION_FLOW:
                if injection_pressure >= plan.injection_start_pressure_bar:
                    phase = _PreparationPhase.BUILD_PRESSURES
                else:
                    pending = self._start_preparation_command(
                        PumpRole.INJECTION,
                        PumpCommandKind.SET_CONSTANT_FLOW,
                        _PreparationPhase.INJECTION_RUN,
                        timing,
                        value=plan.injection_startup_flow_ml_per_hour,
                    )
                    if pending is None:
                        phase = _PreparationPhase.INJECTION_RUN
                    command_executed = True
            elif phase is _PreparationPhase.INJECTION_RUN:
                if injection_pressure >= plan.injection_start_pressure_bar:
                    phase = _PreparationPhase.BUILD_PRESSURES
                else:
                    pending = self._start_preparation_command(
                        PumpRole.INJECTION,
                        PumpCommandKind.RUN,
                        _PreparationPhase.BUILD_PRESSURES,
                        timing,
                    )
                    if pending is None:
                        phase = _PreparationPhase.BUILD_PRESSURES
                    command_executed = True
            elif phase is _PreparationPhase.INJECTION_MARGIN_STOP:
                pending = self._start_preparation_command(
                    PumpRole.INJECTION,
                    PumpCommandKind.STOP,
                    _PreparationPhase.WAIT_MARGIN_RECOVERY,
                    timing,
                )
                if pending is None:
                    phase = _PreparationPhase.WAIT_MARGIN_RECOVERY
                command_executed = True
            elif phase is _PreparationPhase.WAIT_MARGIN_RECOVERY:
                if injection_pressure >= plan.injection_start_pressure_bar:
                    phase = _PreparationPhase.BUILD_PRESSURES
                elif margin >= (
                    plan.minimum_jacket_margin_bar + margin_recovery_hysteresis_bar
                ):
                    phase = _PreparationPhase.INJECTION_RUN
            elif phase is _PreparationPhase.INJECTION_TARGET_STOP:
                pending = self._start_preparation_command(
                    PumpRole.INJECTION,
                    PumpCommandKind.STOP,
                    _PreparationPhase.VERIFY_TARGETS,
                    timing,
                )
                if pending is None:
                    phase = _PreparationPhase.VERIFY_TARGETS
                command_executed = True
            elif phase in {
                _PreparationPhase.BUILD_PRESSURES,
                _PreparationPhase.VERIFY_TARGETS,
            }:
                jacket_state = self._states[PumpRole.JACKET]
                injection_state = self._states[PumpRole.INJECTION]
                jacket_at_target = jacket_pressure >= plan.jacket_target_pressure_bar
                injection_at_target = injection_pressure >= plan.injection_start_pressure_bar
                jacket_holding = (
                    jacket_state.mode is PumpOperatingMode.CONSTANT_PRESSURE
                    and jacket_state.running
                )
                if margin < plan.minimum_jacket_margin_bar and injection_state.running:
                    phase = _PreparationPhase.INJECTION_MARGIN_STOP
                elif injection_at_target and injection_state.running:
                    phase = _PreparationPhase.INJECTION_TARGET_STOP
                elif jacket_at_target and not jacket_holding:
                    resume_after_jacket_hold = _PreparationPhase.BUILD_PRESSURES
                    phase = _PreparationPhase.JACKET_TARGET_STOP
                elif (
                    not injection_at_target
                    and not injection_state.running
                    and margin >= plan.minimum_jacket_margin_bar
                ):
                    phase = _PreparationPhase.INJECTION_RUN
                elif (
                    jacket_at_target
                    and injection_at_target
                    and margin >= plan.minimum_jacket_margin_bar
                    and jacket_holding
                    and not injection_state.running
                ):
                    return

            if command_executed:
                next_control = self._wait_after_preparation_command(timing.control_interval_seconds)
            else:
                next_control = self._wait_for_control_deadline(
                    next_control,
                    timing.control_interval_seconds,
                    cycle_started,
                    timing.watchdog_tolerance_seconds,
                )

    @staticmethod
    def _wait_after_preparation_command(interval_seconds: float) -> float:
        """Start a fresh supervision cadence after submitting one transition."""
        next_control = monotonic() + interval_seconds
        sleep(max(0.0, next_control - monotonic()))
        return next_control

    def _start_preparation_command(
        self,
        role: PumpRole,
        kind: PumpCommandKind,
        next_phase: _PreparationPhase,
        timing: PumpControlTiming,
        *,
        value: float | None = None,
    ) -> _PendingPreparationCommand | None:
        self._validate_preparation_command(role, kind, value)
        if kind is PumpCommandKind.ENTER_REMOTE and self._is_remote_mode(role):
            self._states[role] = PumpPreparationState(remote=True)
            self._log(
                role.value,
                f"REMOTE already confirmed; command skipped; next={next_phase.value}",
            )
            return None
        pump = self._pumps[role]
        submit = getattr(pump, "submit_command", None)
        result_reader = getattr(pump, "command_result", None)
        if callable(submit) != callable(result_reader):
            raise RuntimeError(
                f"{role.value} pump has an incomplete asynchronous command interface"
            )
        if callable(submit) and callable(result_reader):
            command = PumpCommand(
                kind,
                PumpCommandPriority.HIGH,
                value,
                execution_timeout_seconds=timing.execution_timeout_seconds,
                verify_status=kind
                in {
                    PumpCommandKind.ENTER_REMOTE,
                    PumpCommandKind.RUN,
                    PumpCommandKind.STOP,
                },
                queue_timeout_seconds=timing.queue_timeout_seconds,
                verification_timeout_seconds=timing.verification_timeout_seconds,
            )
            command_id = cast(str, submit(command))
            return _PendingPreparationCommand(role, command_id, command, next_phase)

        if kind is PumpCommandKind.ENTER_REMOTE:
            self._execute_remote_write(role, lambda: None)
        elif kind is PumpCommandKind.SET_PRESSURE_LIMIT:
            assert value is not None
            self.set_pressure_limit(role, value)
        elif kind is PumpCommandKind.SET_CONSTANT_FLOW:
            assert value is not None
            self.configure(role, PumpOperatingMode.CONSTANT_FLOW, value)
        elif kind is PumpCommandKind.SET_CONSTANT_PRESSURE:
            assert value is not None
            self.configure(role, PumpOperatingMode.CONSTANT_PRESSURE, value)
        elif kind is PumpCommandKind.RUN:
            self._run_configured(
                role,
                (
                    self.RUN_JACKET_CONFIRMATION
                    if role is PumpRole.JACKET
                    else self.RUN_INJECTION_CONFIRMATION
                ),
                enforce_injection_margin=role is PumpRole.INJECTION,
            )
        else:
            self.stop(role)
        return None

    def _preparation_command_result(self, pending: _PendingPreparationCommand) -> PumpCommandResult:
        reader = getattr(self._pumps[pending.role], "command_result", None)
        if not callable(reader):
            raise RuntimeError("asynchronous pump command result reader disappeared")
        return cast(PumpCommandResult, reader(pending.command_id))

    def _set_preparation_polling(self, active: bool) -> None:
        for pump in self._pumps.values():
            setter = getattr(pump, "set_preparation_active", None)
            if callable(setter):
                setter(active)

    def _preparation_progress(
        self,
        phase: _PreparationPhase,
        plan: PumpStartupPlan,
        jacket_pressure: float,
        injection_pressure: float,
        jacket_quality: DataQuality,
        injection_quality: DataQuality,
        pending: _PendingPreparationCommand | None,
    ) -> PumpPreparationProgress:
        def state_text(role: PumpRole) -> str:
            pump = self._pumps[role]
            telemetry_reader = getattr(pump, "read_telemetry", None)
            if callable(telemetry_reader):
                text = getattr(telemetry_reader(), "operating_status_text", None)
                if text:
                    return str(text)
            state = self._states[role]
            mode = "REMOTE" if state.remote else "LOCAL/UNKNOWN"
            motion = "RUN" if state.running else "STOP"
            return f"{motion} {mode}"

        def pressure_age(role: PumpRole) -> float | None:
            telemetry_reader = getattr(self._pumps[role], "read_telemetry", None)
            if not callable(telemetry_reader):
                return None
            telemetry = telemetry_reader()
            age = getattr(getattr(telemetry, "pressure", None), "age_seconds", None)
            return float(age) if age is not None else None

        return PumpPreparationProgress(
            phase=phase.value,
            jacket_pressure_bar=jacket_pressure,
            jacket_target_pressure_bar=plan.jacket_target_pressure_bar,
            injection_pressure_bar=injection_pressure,
            injection_target_pressure_bar=plan.injection_start_pressure_bar,
            pressure_margin_bar=jacket_pressure - injection_pressure,
            minimum_margin_bar=plan.minimum_jacket_margin_bar,
            jacket_state=state_text(PumpRole.JACKET),
            injection_state=state_text(PumpRole.INJECTION),
            jacket_quality=jacket_quality,
            injection_quality=injection_quality,
            jacket_age_seconds=pressure_age(PumpRole.JACKET),
            injection_age_seconds=pressure_age(PumpRole.INJECTION),
            pending_command=(
                None if pending is None else f"{pending.role.value}: {pending.command.kind.value}"
            ),
        )

    def _validate_preparation_command(
        self,
        role: PumpRole,
        kind: PumpCommandKind,
        value: float | None,
    ) -> None:
        self._require_authorized()
        self._require_connected(role)
        state = self._states[role]
        if kind in {
            PumpCommandKind.SET_PRESSURE_LIMIT,
            PumpCommandKind.SET_CONSTANT_FLOW,
            PumpCommandKind.SET_CONSTANT_PRESSURE,
        }:
            if not state.remote or state.running:
                raise RuntimeError("pump must be stopped in REMOTE mode before configuration")
            if value is None or not isfinite(value) or value < 0.0:
                raise ValueError("pump command target must be nonnegative and finite")
        elif kind is PumpCommandKind.RUN:
            if not state.remote or not state.configured or state.running:
                raise RuntimeError("pump must be configured and stopped in REMOTE mode")
            if role is PumpRole.INJECTION:
                self._require_injection_start_margin()

    def _apply_preparation_command_success(self, pending: _PendingPreparationCommand) -> None:
        role = pending.role
        state = self._states[role]
        kind = pending.command.kind
        if kind is PumpCommandKind.ENTER_REMOTE:
            self._states[role] = PumpPreparationState(remote=True)
        elif kind is PumpCommandKind.SET_CONSTANT_FLOW:
            self._states[role] = PumpPreparationState(
                remote=True,
                configured=True,
                mode=PumpOperatingMode.CONSTANT_FLOW,
                target=pending.command.value,
            )
        elif kind is PumpCommandKind.SET_CONSTANT_PRESSURE:
            self._states[role] = PumpPreparationState(
                remote=True,
                configured=True,
                mode=PumpOperatingMode.CONSTANT_PRESSURE,
                target=pending.command.value,
            )
        elif kind is PumpCommandKind.RUN:
            self._states[role] = PumpPreparationState(
                remote=True,
                configured=True,
                running=True,
                mode=state.mode,
                target=state.target,
            )
        elif kind is PumpCommandKind.STOP:
            self._states[role] = PumpPreparationState(
                remote=state.remote,
                configured=state.configured,
                running=False,
                mode=state.mode,
                target=state.target,
            )
        self._log(
            role.value,
            f"preparation transition command_id={pending.command_id}; "
            f"command={kind.value}; next={pending.next_phase.value}",
        )

    @staticmethod
    def _validate_startup(
        plan: PumpStartupPlan,
        timing: PumpControlTiming,
    ) -> None:
        values = (
            plan.jacket_target_pressure_bar,
            plan.jacket_buildup_flow_ml_per_hour,
            plan.injection_start_pressure_bar,
            plan.injection_startup_flow_ml_per_hour,
            timing.control_interval_seconds,
            timing.execution_timeout_seconds,
            timing.queue_timeout_seconds,
            timing.verification_timeout_seconds,
        )
        if not all(isfinite(value) and value > 0.0 for value in values):
            raise ValueError("measurement pump targets and timings must be positive and finite")
        if (
            not isfinite(timing.watchdog_tolerance_seconds)
            or timing.watchdog_tolerance_seconds < 0.0
        ):
            raise ValueError("control watchdog tolerance must be nonnegative and finite")
        if not isfinite(plan.margin_stability_seconds) or plan.margin_stability_seconds < 0.0:
            raise ValueError("margin stability time must be nonnegative and finite")
        if (
            not isfinite(plan.minimum_jacket_margin_bar)
            or plan.minimum_jacket_margin_bar <= 0.0
        ):
            raise ValueError("minimum jacket margin must be positive and finite")
        jacket_limit = plan.effective_jacket_pressure_limit_bar
        injection_limit = plan.effective_injection_pressure_limit_bar
        for limit in (jacket_limit, injection_limit):
            if limit is not None and (not isfinite(limit) or limit <= 0.0):
                raise ValueError("pump pressure limits must be positive and finite")
        if jacket_limit is not None and plan.jacket_target_pressure_bar > jacket_limit:
            raise ValueError("jacket target pressure exceeds the jacket pump pressure limit")
        if injection_limit is not None and plan.injection_start_pressure_bar > injection_limit:
            raise ValueError("injection target pressure exceeds the injection pump pressure limit")

    def set_pressure_limit(self, role: PumpRole, pressure_bar: float) -> None:
        self._require_authorized()
        self._require_connected(role)
        if not isfinite(pressure_bar) or pressure_bar <= 0.0:
            raise ValueError("pump pressure limit must be positive and finite")

        def operation() -> None:
            state = self._states[role]
            if not state.remote or state.running:
                raise RuntimeError(
                    "pump must be stopped in REMOTE mode before setting pressure limit"
                )
            self._pumps[role].set_pressure_limit(pressure_bar)

        self._execute_remote_write(
            role,
            operation,
        )
        self._log(
            role.value,
            f"hardware pressure limit={pressure_bar}",
            level="WARNING",
        )

    def apply_common_pressure_limit(self, pressure_bar: float) -> None:
        """Put both stopped pumps in REMOTE and program the common MAXPRESS limit."""
        self._require_authorized()
        if not isfinite(pressure_bar) or pressure_bar <= 0.0:
            raise ValueError("pump pressure limit must be positive and finite")
        for role in PumpRole:
            self._require_connected(role)
            if self._states[role].running:
                raise RuntimeError("both pumps must be stopped before setting pressure limit")
        for role in PumpRole:
            if not self._states[role].remote:
                self.enter_remote(role)
        for role in PumpRole:
            self.set_pressure_limit(role, pressure_bar)

    def apply_pressure_limits(
        self,
        jacket_pressure_bar: float,
        injection_pressure_bar: float,
    ) -> None:
        """Program each stopped pump with its separately configured MAXPRESS limit."""
        self._require_authorized()
        limits = {
            PumpRole.JACKET: jacket_pressure_bar,
            PumpRole.INJECTION: injection_pressure_bar,
        }
        if not all(isfinite(value) and value > 0.0 for value in limits.values()):
            raise ValueError("pump pressure limits must be positive and finite")
        for role in PumpRole:
            self._require_connected(role)
            if self._states[role].running:
                raise RuntimeError("both pumps must be stopped before setting pressure limits")
        for role in PumpRole:
            if not self._states[role].remote:
                self.enter_remote(role)
        for role, pressure_bar in limits.items():
            self.set_pressure_limit(role, pressure_bar)

    @staticmethod
    def _require_startup_safe(
        startup_safety_check: Callable[[], tuple[str, ...]] | None,
    ) -> None:
        if startup_safety_check is None:
            return
        reasons = startup_safety_check()
        if reasons:
            raise PermissionError("pump startup safety interlock active: " + "; ".join(reasons))

    @staticmethod
    def _wait_for_control_deadline(
        previous_deadline: float,
        interval_seconds: float,
        cycle_started: float,
        watchdog_tolerance_seconds: float,
    ) -> float:
        """Wait on an absolute cadence and skip slots that are already missed."""
        PumpControlService._require_control_deadline(
            cycle_started,
            interval_seconds,
            watchdog_tolerance_seconds,
        )
        next_deadline = previous_deadline + interval_seconds
        now = monotonic()
        if next_deadline <= now:
            missed = int((now - next_deadline) // interval_seconds) + 1
            next_deadline += missed * interval_seconds
        sleep(max(0.0, next_deadline - monotonic()))
        return next_deadline

    @staticmethod
    def _require_control_deadline(
        cycle_started: float,
        interval_seconds: float,
        watchdog_tolerance_seconds: float,
    ) -> None:
        elapsed = monotonic() - cycle_started
        if elapsed > interval_seconds + watchdog_tolerance_seconds:
            raise TimeoutError(
                f"control cycle deadline missed during pump preparation: {elapsed:.3f} seconds"
            )

    def _require_injection_start_margin(self) -> float:
        statuses = self._supervision_statuses()
        margin = statuses[PumpRole.JACKET].pressure_bar - statuses[PumpRole.INJECTION].pressure_bar
        if margin < self._minimum_margin:
            raise PermissionError(
                f"jacket pressure margin is {margin:.3f} bar; "
                f"at least {self._minimum_margin:.3f} bar is required"
            )
        return margin

    def apply_measurement_flow(
        self,
        flow_ml_per_hour: float,
        *,
        verification_tolerance_ml_per_hour: float = 1e-6,
    ) -> float:
        """Apply and verify the BES measurement flow with a documented sequence.

        The available 260D documentation in this repository does not establish
        that changing FLOW while RUN is safe.  Therefore this uses the explicit
        STOP -> CONST FLOW/FLOW -> SETFLOW readback -> RUN sequence.
        """
        self._require_authorized()
        self._require_connected(PumpRole.INJECTION)
        if not isfinite(flow_ml_per_hour) or flow_ml_per_hour <= 0.0:
            raise ValueError("measurement flow must be positive and finite")
        if (
            not isfinite(verification_tolerance_ml_per_hour)
            or verification_tolerance_ml_per_hour < 0.0
        ):
            raise ValueError("flow verification tolerance must be finite and nonnegative")
        self.stop(PumpRole.INJECTION)
        pump = self._pumps[PumpRole.INJECTION]
        self._execute_remote_write(
            PumpRole.INJECTION,
            lambda: pump.set_constant_flow(flow_ml_per_hour),
        )
        readback = pump.read_configured_flow_ml_per_hour()
        if (
            not isfinite(readback)
            or abs(readback - flow_ml_per_hour) > verification_tolerance_ml_per_hour
        ):
            raise RuntimeError(
                "BES flow verification failed: "
                f"requested={flow_ml_per_hour:.7g} ml/h, "
                f"readback={readback:.7g} ml/h"
            )
        self._states[PumpRole.INJECTION] = PumpPreparationState(
            remote=True,
            configured=True,
            running=False,
            mode=PumpOperatingMode.CONSTANT_FLOW,
            target=readback,
        )
        # This is an explicit in-measurement flow change. The startup-only
        # jacket margin gate does not apply, but the common safety checks do.
        self._run_configured(
            PumpRole.INJECTION,
            self.RUN_INJECTION_CONFIRMATION,
            enforce_injection_margin=False,
        )
        self._log(
            PumpRole.INJECTION.value,
            f"measurement flow applied and verified target={readback:.7g} ml/h",
            level="WARNING",
        )
        return readback

    def stop(self, role: PumpRole) -> None:
        self._require_authorized()
        self._require_connected(role)
        if self._is_stopped(role):
            state = self._states[role]
            self._states[role] = PumpPreparationState(
                remote=state.remote,
                configured=state.configured,
                running=False,
                mode=state.mode,
                target=state.target,
            )
            self._log(role.value, "STOP skipped: pump is already stopped")
            return
        self._execute_remote_write(role, self._pumps[role].request_stop)
        state = self._states[role]
        self._states[role] = PumpPreparationState(
            remote=state.remote,
            configured=state.configured,
            running=False,
            mode=state.mode,
            target=state.target,
        )
        self._log(role.value, "STOP", level="WARNING")

    def stop_all(self) -> tuple[str, ...]:
        self._require_authorized()
        if all(
            callable(getattr(self._pumps[role], "submit_stop", None))
            and callable(getattr(self._pumps[role], "command_result", None))
            for role in PumpRole
        ):
            return self._stop_all_queued()
        errors: list[str] = []
        for role in PumpRole:
            if self._is_stopped_local(role):
                self._states[role] = PumpPreparationState()
                self._log(
                    role.value,
                    "STOP skipped: pump is already STOP LOCAL",
                    level="WARNING",
                )
                continue
            try:
                # Safety rollback must not wait for an ordinary Remote-mode
                # precheck. Issue STOP directly, then recover LOCAL MODE only
                # when the pump is not already confirmed as STOP LOCAL.
                self._pumps[role].request_stop()
                state = self._states[role]
                self._states[role] = PumpPreparationState(
                    remote=state.remote,
                    configured=state.configured,
                    running=False,
                    mode=state.mode,
                    target=state.target,
                )
                self._log(role.value, "STOP", level="WARNING")
            except Exception as error:
                if "LOCAL MODE" not in str(error).upper():
                    errors.append(f"{role.value}: {error}")
                    continue
                if self._is_stopped_local(role):
                    self._states[role] = PumpPreparationState()
                    self._log(
                        role.value,
                        "STOP already satisfied by STOP LOCAL",
                        level="WARNING",
                    )
                    continue
                try:
                    # A pump that fell back to LOCAL rejects STOP. During a
                    # safety rollback, re-enter REMOTE and retry STOP once;
                    # PollingPump also clears its STOP latch on REMOTE.
                    self._pumps[role].enter_remote()
                    self._pumps[role].request_stop()
                    state = self._states[role]
                    self._states[role] = PumpPreparationState(
                        remote=True,
                        configured=state.configured,
                        running=False,
                        mode=state.mode,
                        target=state.target,
                    )
                    self._log(
                        role.value,
                        "rollback recovered LOCAL MODE with REMOTE -> STOP",
                        level="WARNING",
                    )
                except Exception as recovery_error:
                    errors.append(f"{role.value}: {error}; REMOTE/STOP recovery: {recovery_error}")
        return tuple(errors)

    def _stop_all_queued(self) -> tuple[str, ...]:
        pending: dict[PumpRole, str] = {}
        errors: list[str] = []
        for role in PumpRole:
            pump = self._pumps[role]
            try:
                cancel = getattr(pump, "cancel_pending_commands", None)
                if callable(cancel):
                    cancel()
                if self._is_stopped_local(role):
                    self._states[role] = PumpPreparationState()
                    self._log(
                        role.value,
                        "STOP skipped: pump is already STOP LOCAL",
                        level="WARNING",
                    )
                    continue
                submit_stop = getattr(pump, "submit_stop", None)
                if not callable(submit_stop):
                    raise RuntimeError("pump emergency command queue is unavailable")
                pending[role] = cast(str, submit_stop(emergency=True))
            except Exception as error:
                errors.append(f"{role.value}: {error}")

        deadline = monotonic() + 7.0
        unfinished = set(pending)
        while unfinished and monotonic() < deadline:
            for role in tuple(unfinished):
                result_reader = getattr(self._pumps[role], "command_result", None)
                if not callable(result_reader):
                    errors.append(f"{role.value}: command result reader unavailable")
                    unfinished.remove(role)
                    continue
                result = cast(
                    PumpCommandResult,
                    result_reader(pending[role]),
                )
                if not result.status.terminal:
                    continue
                unfinished.remove(role)
                if result.status is PumpCommandStatus.SUCCEEDED:
                    state = self._states[role]
                    self._states[role] = PumpPreparationState(
                        remote=state.remote,
                        configured=state.configured,
                        running=False,
                        mode=state.mode,
                        target=state.target,
                    )
                elif result.error is not None and "LOCAL MODE" in result.error.upper():
                    if self._is_stopped_local(role):
                        self._states[role] = PumpPreparationState()
                        self._log(
                            role.value,
                            "STOP already satisfied by STOP LOCAL",
                            level="WARNING",
                        )
                        continue
                    try:
                        self._pumps[role].enter_remote()
                        self._pumps[role].request_stop()
                        state = self._states[role]
                        self._states[role] = PumpPreparationState(
                            remote=True,
                            configured=state.configured,
                            running=False,
                            mode=state.mode,
                            target=state.target,
                        )
                        self._log(
                            role.value,
                            "rollback recovered LOCAL MODE with REMOTE -> STOP",
                            level="WARNING",
                        )
                    except Exception as recovery_error:
                        errors.append(
                            f"{role.value}: {result.error}; REMOTE/STOP recovery: {recovery_error}"
                        )
                else:
                    errors.append(f"{role.value}: {result.error or result.status.value}")
            if unfinished:
                sleep(0.01)
        for role in unfinished:
            errors.append(f"{role.value}: STOP acknowledgement timed out")
        return tuple(errors)

    def _is_stopped_local(self, role: PumpRole) -> bool:
        checker = getattr(self._pumps[role], "is_stopped_local", None)
        return bool(checker()) if callable(checker) else False

    def _is_stopped(self, role: PumpRole) -> bool:
        checker = getattr(self._pumps[role], "is_stopped", None)
        if callable(checker):
            return bool(checker())
        return not self._states[role].running

    def _is_remote_mode(self, role: PumpRole) -> bool:
        checker = getattr(self._pumps[role], "is_remote_mode", None)
        if callable(checker):
            return bool(checker())
        return self._states[role].remote

    def clear(self, role: PumpRole) -> None:
        self._require_authorized()
        self._require_connected(role)

        def operation() -> None:
            if self._states[role].running:
                raise RuntimeError("pump must be stopped before CLEAR")
            self._pumps[role].clear()

        self._execute_remote_write(role, operation)
        self._states[role] = PumpPreparationState(remote=True)
        self._log(role.value, "CLEAR", level="WARNING")

    def return_local(self, role: PumpRole) -> None:
        self._require_authorized()
        self._require_connected(role)
        if self._states[role].running:
            raise RuntimeError("pump must be stopped before LOCAL")
        self._pumps[role].return_local()
        self._states[role] = PumpPreparationState()
        self._log(role.value, "LOCAL")

    def revoke(self) -> None:
        for role in PumpRole:
            self._set_pump_remote_supervision(role, False)
        self._authorized = False

    def observe_safe_stop(self) -> None:
        for role in PumpRole:
            self._set_pump_remote_supervision(role, False)
        for role, state in self._states.items():
            self._states[role] = PumpPreparationState(
                remote=state.remote,
                configured=state.configured,
                running=False,
                mode=state.mode,
                target=state.target,
            )

    def _require_authorized(self) -> None:
        if not self._authorized:
            raise PermissionError("physical pump control is not authorized")

    def _require_connected(self, role: PumpRole) -> None:
        if not self._connected[role]:
            raise ConnectionError(f"{role.value} pump is not connected")

    def _log(self, direction: str, message: str, *, level: str = "INFO") -> None:
        if self._diagnostics is not None:
            self._diagnostics.emit(DiagnosticCategory.SYSTEM, direction, message, level=level)
