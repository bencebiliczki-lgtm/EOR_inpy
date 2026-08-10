from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from time import monotonic, sleep
from typing import Protocol, cast

from eor_control.diagnostics import DiagnosticCategory, DiagnosticLogger
from eor_control.domain import PumpStatus
from eor_control.pump_commands import (
    PumpCommand,
    PumpCommandKind,
    PumpCommandPriority,
    PumpCommandResult,
    PumpCommandStatus,
)


class PumpRole(StrEnum):
    JACKET = "jacket"
    INJECTION = "injection"


class PumpOperatingMode(StrEnum):
    CONSTANT_FLOW = "constant_flow"
    CONSTANT_PRESSURE = "constant_pressure"


class _PreparationPhase(StrEnum):
    JACKET_REMOTE = "jacket_remote"
    JACKET_PRESSURE_LIMIT = "jacket_pressure_limit"
    JACKET_FLOW = "jacket_flow"
    JACKET_RUN = "jacket_run"
    WAIT_JACKET_TARGET = "wait_jacket_target"
    JACKET_STOP = "jacket_stop"
    JACKET_CONSTANT_PRESSURE = "jacket_constant_pressure"
    JACKET_HOLD_RUN = "jacket_hold_run"
    WAIT_JACKET_STABLE = "wait_jacket_stable"
    INJECTION_REMOTE = "injection_remote"
    INJECTION_PRESSURE_LIMIT = "injection_pressure_limit"
    INJECTION_FLOW = "injection_flow"
    INJECTION_RUN = "injection_run"
    WAIT_INJECTION_TARGET = "wait_injection_target"
    INJECTION_STOP = "injection_stop"
    VERIFY_STOPPED = "verify_stopped"


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
    jacket_pressure_limit_bar: float | None = None
    injection_pressure_limit_bar: float | None = None
    margin_stability_seconds: float = 2.0

    @property
    def injection_target_flow_ml_per_hour(self) -> float:
        """Compatibility alias for pre-split startup configurations."""
        return self.injection_startup_flow_ml_per_hour

    @property
    def effective_measurement_flow_ml_per_hour(self) -> float:
        value = self.injection_measurement_flow_ml_per_hour
        return self.injection_startup_flow_ml_per_hour if value is None else value


@dataclass(frozen=True, slots=True)
class PumpControlTiming:
    pressure_buildup_timeout_seconds: float = 120.0
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
        manual_safety_check: Callable[[PumpRole, PumpStatus], tuple[str, ...]]
        | None = None,
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

    def connect_remote(self, role: PumpRole) -> PumpStatus:
        """Connect, identify and enter REMOTE mode as one manual operation."""

        try:
            status = self.connect(role)
            self.enter_remote(role)
        except Exception:
            if self._connected[role]:
                with suppress(Exception):
                    self._pumps[role].disconnect()
                self._connected[role] = False
                self._states[role] = PumpPreparationState()
            raise
        return status

    def disconnect(self, role: PumpRole) -> None:
        """Stop and disconnect one pump independently."""

        self._require_authorized()
        if self._states[role].running:
            raise RuntimeError("pump must be stopped before disconnect")
        self._pumps[role].request_stop()
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
            self._connected[role] = False
            self._states[role] = PumpPreparationState()

    def shutdown_connections(self) -> tuple[str, ...]:
        """Attempt STOP and disconnect for every individually connected pump."""

        errors: list[str] = []
        for role, pump in self._pumps.items():
            if self._connected[role] and not self._is_stopped_local(role):
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
        for role, pump in self._pumps.items():
            if not self._connected[role]:
                errors[role] = "nincs csatlakoztatva"
                continue
            try:
                statuses[role] = pump.read_status()
            except Exception as error:
                errors[role] = str(error)
        return statuses, errors

    def statuses(self) -> dict[PumpRole, PumpStatus]:
        self._require_authorized()
        for role in PumpRole:
            self._require_connected(role)
        return {role: pump.read_status() for role, pump in self._pumps.items()}

    def _supervision_status(self, role: PumpRole) -> PumpStatus:
        """Use cache-only status for pumps that expose an asynchronous worker."""
        pump = self._pumps[role]
        submit = getattr(pump, "submit_command", None)
        result_reader = getattr(pump, "command_result", None)
        if callable(submit) or callable(result_reader):
            if not callable(submit) or not callable(result_reader):
                raise RuntimeError(
                    f"{role.value} pump has an incomplete asynchronous interface"
                )
            cache_reader = getattr(pump, "read_cached_status", None)
            if not callable(cache_reader):
                raise RuntimeError(
                    f"{role.value} asynchronous pump has no cache-only status reader"
                )
            status, _quality = cache_reader()
            return cast(PumpStatus, status)
        return pump.read_status()

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

    def configure(self, role: PumpRole, mode: PumpOperatingMode, target: float) -> None:
        self._require_authorized()
        self._require_connected(role)
        state = self._states[role]
        if not state.remote or state.running:
            raise RuntimeError("pump must be stopped in REMOTE mode before configuration")
        if not isfinite(target) or target < 0.0:
            raise ValueError("pump target must be nonnegative and finite")
        pump = self._pumps[role]
        if mode is PumpOperatingMode.CONSTANT_FLOW:
            pump.set_constant_flow(target)
        else:
            pump.set_constant_pressure(target)
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
                raise PermissionError(
                    "safety interlock active: " + "; ".join(reasons)
                )
        if self._manual_safety_check is not None:
            status = self._pumps[role].read_status()
            reasons = self._manual_safety_check(role, status)
            if reasons:
                raise PermissionError(
                    "manual safety interlock active: " + "; ".join(reasons)
                )
        state = self._states[role]
        if not state.remote or not state.configured or state.running:
            raise RuntimeError("pump must be configured and stopped in REMOTE mode")
        if role is PumpRole.INJECTION and enforce_injection_margin:
            self._require_injection_start_margin()
        self._pumps[role].run()
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
        jacket_pressure_limit_bar: float | None = None,
        injection_pressure_limit_bar: float | None = None,
        confirmation: str,
        startup_safety_check: Callable[[], tuple[str, ...]] | None = None,
        pressure_buildup_timeout_seconds: float = 120.0,
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
                jacket_pressure_limit_bar=jacket_pressure_limit_bar,
                injection_pressure_limit_bar=injection_pressure_limit_bar,
                margin_stability_seconds=margin_stability_seconds,
            ),
            timing=PumpControlTiming(
                pressure_buildup_timeout_seconds=(
                    pressure_buildup_timeout_seconds
                ),
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
    ) -> None:
        """Run the single supervised pump-preparation state machine."""
        self._require_authorized()
        if confirmation != self.START_MEASUREMENT_CONFIRMATION:
            raise PermissionError("measurement pump-start confirmation did not match")
        self._validate_startup(plan, timing)
        for role in PumpRole:
            self._require_connected(role)

        self._set_preparation_polling(True)
        try:
            self._run_preparation_state_machine(
                plan,
                timing,
                startup_safety_check,
            )
        except Exception as error:
            stop_errors = self.stop_all()
            if stop_errors:
                raise RuntimeError(
                    f"{error}; pump startup rollback errors: {'; '.join(stop_errors)}"
                ) from error
            raise
        finally:
            self._set_preparation_polling(False)

    def _run_preparation_state_machine(
        self,
        plan: PumpStartupPlan,
        timing: PumpControlTiming,
        startup_safety_check: Callable[[], tuple[str, ...]] | None,
    ) -> None:
        """Advance state from cache while pump workers execute serial commands."""
        phase = _PreparationPhase.JACKET_REMOTE
        phase_deadline = monotonic() + timing.pressure_buildup_timeout_seconds
        next_control = monotonic()
        stable_since: float | None = None
        pending: _PendingPreparationCommand | None = None

        while True:
            cycle_started = monotonic()
            self._require_startup_safe(startup_safety_check)
            statuses = self._supervision_statuses()
            self._require_control_deadline(
                cycle_started,
                timing.control_interval_seconds,
                timing.watchdog_tolerance_seconds,
            )
            jacket_pressure = statuses[PumpRole.JACKET].pressure_bar
            injection_pressure = statuses[PumpRole.INJECTION].pressure_bar
            margin = jacket_pressure - injection_pressure
            now = monotonic()
            command_executed = False

            if pending is not None:
                result = self._preparation_command_result(pending)
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
                    if phase in {
                        _PreparationPhase.WAIT_JACKET_TARGET,
                        _PreparationPhase.WAIT_JACKET_STABLE,
                        _PreparationPhase.WAIT_INJECTION_TARGET,
                    }:
                        phase_deadline = (
                            monotonic() + timing.pressure_buildup_timeout_seconds
                        )
                    if phase is _PreparationPhase.WAIT_JACKET_STABLE:
                        stable_since = None
                    pending = None
                elif result.status is PumpCommandStatus.TIMED_OUT:
                    if pending.command.kind is PumpCommandKind.ENTER_REMOTE:
                        raise RuntimeError(
                            f"A {pending.role.value} pumpa nem állítható Remote módba."
                        )
                    raise TimeoutError(
                        result.error
                        or f"{pending.role.value} command timed out: "
                        f"{pending.command.kind.value}"
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

            if phase in {
                _PreparationPhase.INJECTION_REMOTE,
                _PreparationPhase.INJECTION_PRESSURE_LIMIT,
                _PreparationPhase.INJECTION_FLOW,
                _PreparationPhase.INJECTION_RUN,
            } and margin < self._minimum_margin:
                raise PermissionError(
                    f"jacket pressure margin is {margin:.3f} bar; "
                    f"at least {self._minimum_margin:.3f} bar is required"
                )

            if phase is _PreparationPhase.JACKET_REMOTE:
                pending = self._start_preparation_command(
                    PumpRole.JACKET,
                    PumpCommandKind.ENTER_REMOTE,
                    _PreparationPhase.JACKET_PRESSURE_LIMIT,
                    timing,
                )
                if pending is None:
                    phase = _PreparationPhase.JACKET_PRESSURE_LIMIT
                command_executed = True
            elif phase is _PreparationPhase.JACKET_PRESSURE_LIMIT:
                if plan.jacket_pressure_limit_bar is not None:
                    pending = self._start_preparation_command(
                        PumpRole.JACKET,
                        PumpCommandKind.SET_PRESSURE_LIMIT,
                        _PreparationPhase.JACKET_FLOW,
                        timing,
                        value=plan.jacket_pressure_limit_bar,
                    )
                    command_executed = True
                    if pending is None:
                        phase = _PreparationPhase.JACKET_FLOW
                else:
                    phase = _PreparationPhase.JACKET_FLOW
            elif phase is _PreparationPhase.JACKET_FLOW:
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
                pending = self._start_preparation_command(
                    PumpRole.JACKET,
                    PumpCommandKind.RUN,
                    _PreparationPhase.WAIT_JACKET_TARGET,
                    timing,
                )
                if pending is None:
                    phase = _PreparationPhase.WAIT_JACKET_TARGET
                    phase_deadline = (
                        monotonic() + timing.pressure_buildup_timeout_seconds
                    )
                command_executed = True
            elif phase is _PreparationPhase.WAIT_JACKET_TARGET:
                if (
                    jacket_pressure >= plan.jacket_target_pressure_bar
                    and margin >= self._minimum_margin
                ):
                    phase = _PreparationPhase.JACKET_STOP
                elif now >= phase_deadline:
                    raise TimeoutError(
                        "jacket startup target was not reached: "
                        f"pressure {jacket_pressure:.3f}/"
                        f"{plan.jacket_target_pressure_bar:.3f} bar, margin "
                        f"{margin:.3f}/{self._minimum_margin:.3f} bar"
                    )
            elif phase is _PreparationPhase.JACKET_STOP:
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
                    _PreparationPhase.WAIT_JACKET_STABLE,
                    timing,
                )
                if pending is None:
                    phase = _PreparationPhase.WAIT_JACKET_STABLE
                    phase_deadline = (
                        monotonic() + timing.pressure_buildup_timeout_seconds
                    )
                    stable_since = None
                command_executed = True
            elif phase is _PreparationPhase.WAIT_JACKET_STABLE:
                jacket_stable = (
                    jacket_pressure >= plan.jacket_target_pressure_bar
                    and margin >= self._minimum_margin
                )
                stable_since = (
                    now
                    if jacket_stable and stable_since is None
                    else stable_since
                    if jacket_stable
                    else None
                )
                if (
                    stable_since is not None
                    and now - stable_since >= plan.margin_stability_seconds
                ):
                    phase = _PreparationPhase.INJECTION_REMOTE
                elif now >= phase_deadline:
                    raise TimeoutError(
                        "jacket holding pressure was not stable: "
                        f"pressure {jacket_pressure:.3f}/"
                        f"{plan.jacket_target_pressure_bar:.3f} bar, margin "
                        f"{margin:.3f}/{self._minimum_margin:.3f} bar"
                    )
            elif phase is _PreparationPhase.INJECTION_REMOTE:
                pending = self._start_preparation_command(
                    PumpRole.INJECTION,
                    PumpCommandKind.ENTER_REMOTE,
                    _PreparationPhase.INJECTION_PRESSURE_LIMIT,
                    timing,
                )
                if pending is None:
                    phase = _PreparationPhase.INJECTION_PRESSURE_LIMIT
                command_executed = True
            elif phase is _PreparationPhase.INJECTION_PRESSURE_LIMIT:
                if plan.injection_pressure_limit_bar is not None:
                    pending = self._start_preparation_command(
                        PumpRole.INJECTION,
                        PumpCommandKind.SET_PRESSURE_LIMIT,
                        _PreparationPhase.INJECTION_FLOW,
                        timing,
                        value=plan.injection_pressure_limit_bar,
                    )
                    command_executed = True
                    if pending is None:
                        phase = _PreparationPhase.INJECTION_FLOW
                else:
                    phase = _PreparationPhase.INJECTION_FLOW
            elif phase is _PreparationPhase.INJECTION_FLOW:
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
                pending = self._start_preparation_command(
                    PumpRole.INJECTION,
                    PumpCommandKind.RUN,
                    _PreparationPhase.WAIT_INJECTION_TARGET,
                    timing,
                )
                if pending is None:
                    phase = _PreparationPhase.WAIT_INJECTION_TARGET
                    phase_deadline = (
                        monotonic() + timing.pressure_buildup_timeout_seconds
                    )
                command_executed = True
            elif phase is _PreparationPhase.WAIT_INJECTION_TARGET:
                if injection_pressure >= plan.injection_start_pressure_bar:
                    phase = _PreparationPhase.INJECTION_STOP
                elif now >= phase_deadline:
                    raise TimeoutError(
                        "injection startup target was not reached: "
                        f"pressure {injection_pressure:.3f}/"
                        f"{plan.injection_start_pressure_bar:.3f} bar"
                    )
            elif phase is _PreparationPhase.INJECTION_STOP:
                pending = self._start_preparation_command(
                    PumpRole.INJECTION,
                    PumpCommandKind.STOP,
                    _PreparationPhase.VERIFY_STOPPED,
                    timing,
                )
                if pending is None:
                    phase = _PreparationPhase.VERIFY_STOPPED
                command_executed = True
            else:
                # VERIFY_STOPPED is reached only in a fresh, safety-checked
                # cycle after the STOP acknowledgement.
                return

            if command_executed:
                next_control = self._wait_after_preparation_command(
                    timing.control_interval_seconds
                )
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
            self.enter_remote(role)
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

    def _preparation_command_result(
        self, pending: _PendingPreparationCommand
    ) -> PumpCommandResult:
        reader = getattr(self._pumps[pending.role], "command_result", None)
        if not callable(reader):
            raise RuntimeError("asynchronous pump command result reader disappeared")
        return cast(PumpCommandResult, reader(pending.command_id))

    def _set_preparation_polling(self, active: bool) -> None:
        for pump in self._pumps.values():
            setter = getattr(pump, "set_preparation_active", None)
            if callable(setter):
                setter(active)

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
                raise RuntimeError(
                    "pump must be stopped in REMOTE mode before configuration"
                )
            if value is None or not isfinite(value) or value < 0.0:
                raise ValueError("pump command target must be nonnegative and finite")
        elif kind is PumpCommandKind.RUN:
            if not state.remote or not state.configured or state.running:
                raise RuntimeError(
                    "pump must be configured and stopped in REMOTE mode"
                )
            if role is PumpRole.INJECTION:
                self._require_injection_start_margin()

    def _apply_preparation_command_success(
        self, pending: _PendingPreparationCommand
    ) -> None:
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
            timing.pressure_buildup_timeout_seconds,
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
        if (
            not isfinite(plan.margin_stability_seconds)
            or plan.margin_stability_seconds < 0.0
        ):
            raise ValueError("margin stability time must be nonnegative and finite")
        limits = (plan.jacket_pressure_limit_bar, plan.injection_pressure_limit_bar)
        if not all(
            limit is None or isfinite(limit) and limit > 0.0
            for limit in limits
        ):
            raise ValueError("pump pressure limits must be positive and finite")
        if (
            plan.jacket_pressure_limit_bar is not None
            and plan.jacket_target_pressure_bar > plan.jacket_pressure_limit_bar
        ):
            raise ValueError("jacket target pressure exceeds its pump pressure limit")
        if (
            plan.injection_pressure_limit_bar is not None
            and plan.injection_start_pressure_bar > plan.injection_pressure_limit_bar
        ):
            raise ValueError("injection target pressure exceeds its pump pressure limit")

    def set_pressure_limit(self, role: PumpRole, pressure_bar: float) -> None:
        self._require_authorized()
        self._require_connected(role)
        state = self._states[role]
        if not state.remote or state.running:
            raise RuntimeError(
                "pump must be stopped in REMOTE mode before setting pressure limit"
            )
        if not isfinite(pressure_bar) or pressure_bar <= 0.0:
            raise ValueError("pump pressure limit must be positive and finite")
        self._pumps[role].set_pressure_limit(pressure_bar)
        self._log(
            role.value,
            f"hardware pressure limit={pressure_bar}",
            level="WARNING",
        )

    @staticmethod
    def _require_startup_safe(
        startup_safety_check: Callable[[], tuple[str, ...]] | None,
    ) -> None:
        if startup_safety_check is None:
            return
        reasons = startup_safety_check()
        if reasons:
            raise PermissionError(
                "pump startup safety interlock active: " + "; ".join(reasons)
            )

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
                "control cycle deadline missed during pump preparation: "
                f"{elapsed:.3f} seconds"
            )

    def _require_injection_start_margin(self) -> float:
        statuses = self._supervision_statuses()
        margin = (
            statuses[PumpRole.JACKET].pressure_bar
            - statuses[PumpRole.INJECTION].pressure_bar
        )
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
        state = self._states[PumpRole.INJECTION]
        if not state.remote:
            raise RuntimeError("injection pump must be in REMOTE mode")

        self.stop(PumpRole.INJECTION)
        pump = self._pumps[PumpRole.INJECTION]
        pump.set_constant_flow(flow_ml_per_hour)
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
                self.stop(role)
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
                    errors.append(
                        f"{role.value}: {error}; REMOTE/STOP recovery: "
                        f"{recovery_error}"
                    )
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
                            f"{role.value}: {result.error}; REMOTE/STOP recovery: "
                            f"{recovery_error}"
                        )
                else:
                    errors.append(
                        f"{role.value}: {result.error or result.status.value}"
                    )
            if unfinished:
                sleep(0.01)
        for role in unfinished:
            errors.append(f"{role.value}: STOP acknowledgement timed out")
        return tuple(errors)

    def _is_stopped_local(self, role: PumpRole) -> bool:
        checker = getattr(self._pumps[role], "is_stopped_local", None)
        return bool(checker()) if callable(checker) else False

    def clear(self, role: PumpRole) -> None:
        self._require_authorized()
        self._require_connected(role)
        if self._states[role].running:
            raise RuntimeError("pump must be stopped before CLEAR")
        self._pumps[role].clear()
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
        self._authorized = False

    def observe_safe_stop(self) -> None:
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
            self._diagnostics.emit(
                DiagnosticCategory.SYSTEM, direction, message, level=level
            )
