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
    def read_status(self) -> PumpStatus: ...

    def enter_remote(self) -> None: ...

    def set_constant_flow(self, flow_ml_per_minute: float) -> None: ...

    def set_constant_pressure(self, pressure_bar: float) -> None: ...

    def run(self) -> None: ...

    def request_stop(self) -> None: ...

    def clear(self) -> None: ...

    def return_local(self) -> None: ...


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
        self._minimum_margin = minimum_jacket_margin_bar
        self._diagnostics = diagnostics
        self._authorized = False

    def authorize(self, confirmation: str) -> None:
        if confirmation != self.AUTHORIZATION:
            raise PermissionError("pump control authorization did not match")
        self._authorized = True
        self._log("AUTH", "physical pump control authorized")

    def state(self, role: PumpRole) -> PumpPreparationState:
        return self._states[role]

    def statuses(self) -> dict[PumpRole, PumpStatus]:
        self._require_authorized()
        return {role: pump.read_status() for role, pump in self._pumps.items()}

    def enter_remote(self, role: PumpRole) -> None:
        self._require_authorized()
        self._pumps[role].enter_remote()
        self._states[role] = PumpPreparationState(remote=True)
        self._log(role.value, "REMOTE")

    def configure(self, role: PumpRole, mode: PumpOperatingMode, target: float) -> None:
        self._require_authorized()
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
        expected = (
            self.RUN_JACKET_CONFIRMATION
            if role is PumpRole.JACKET
            else self.RUN_INJECTION_CONFIRMATION
        )
        if confirmation != expected:
            raise PermissionError("pump RUN confirmation did not match")
        state = self._states[role]
        if not state.remote or not state.configured or state.running:
            raise RuntimeError("pump must be configured and stopped in REMOTE mode")
        if role is PumpRole.INJECTION:
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
        if self._states[role].running:
            raise RuntimeError("pump must be stopped before CLEAR")
        self._pumps[role].clear()
        self._states[role] = PumpPreparationState(remote=True)
        self._log(role.value, "CLEAR", level="WARNING")

    def return_local(self, role: PumpRole) -> None:
        self._require_authorized()
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

    def _log(self, direction: str, message: str, *, level: str = "INFO") -> None:
        if self._diagnostics is not None:
            self._diagnostics.emit(
                DiagnosticCategory.SYSTEM, direction, message, level=level
            )
