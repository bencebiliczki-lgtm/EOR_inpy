from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Protocol

from eor_control.diagnostics import DiagnosticCategory, DiagnosticLogger
from eor_control.domain import PumpStatus


class PumpRole(StrEnum):
    JACKET = "jacket"
    INJECTION = "injection"


class PumpOperatingMode(StrEnum):
    CONSTANT_FLOW = "constant_flow"
    CONSTANT_PRESSURE = "constant_pressure"


class ControllablePump(Protocol):
    def connect(self) -> None: ...

    def read_status(self) -> PumpStatus: ...

    def enter_remote(self) -> None: ...

    def set_constant_flow(self, flow_ml_per_minute: float) -> None: ...

    def set_constant_pressure(self, pressure_bar: float) -> None: ...

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


class PumpControlService:
    AUTHORIZATION = "ENABLE PHYSICAL EOR HARDWARE"
    RUN_JACKET_CONFIRMATION = "RUN JACKET PUMP"
    RUN_INJECTION_CONFIRMATION = "RUN INJECTION PUMP"

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
        return self._pumps[role].read_status()

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
            if self._connected[role]:
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
        if role is PumpRole.INJECTION and self._enforce_injection_margin:
            statuses = self.statuses()
            margin = (
                statuses[PumpRole.JACKET].pressure_bar
                - statuses[PumpRole.INJECTION].pressure_bar
            )
            if margin < self._minimum_margin:
                raise PermissionError(
                    f"jacket pressure margin is {margin:.2f} bar; "
                    f"at least {self._minimum_margin:.2f} bar is required"
                )
        self._pumps[role].run()
        self._states[role] = PumpPreparationState(
            remote=True,
            configured=True,
            running=True,
            mode=state.mode,
            target=state.target,
        )
        self._log(role.value, "RUN", level="WARNING")

    def stop(self, role: PumpRole) -> None:
        self._require_authorized()
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
        errors: list[str] = []
        for role in PumpRole:
            try:
                self.stop(role)
            except Exception as error:
                errors.append(f"{role.value}: {error}")
        return tuple(errors)

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
