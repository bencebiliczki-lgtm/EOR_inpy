from dataclasses import dataclass

import pytest

from eor_control.domain import PumpStatus
from eor_control.pump_control import PumpControlService, PumpOperatingMode, PumpRole


@dataclass
class FakePump:
    pressure: float
    commands: list[str]

    def read_status(self) -> PumpStatus:
        return PumpStatus(self.pressure, 0.0, 200.0)

    def enter_remote(self) -> None:
        self.commands.append("REMOTE")

    def set_constant_flow(self, target: float) -> None:
        self.commands.append(f"FLOW={target}")

    def set_constant_pressure(self, target: float) -> None:
        self.commands.append(f"PRESS={target}")

    def run(self) -> None:
        self.commands.append("RUN")

    def request_stop(self) -> None:
        self.commands.append("STOP")

    def clear(self) -> None:
        self.commands.append("CLEAR")

    def return_local(self) -> None:
        self.commands.append("LOCAL")


def service(jacket_pressure: float = 120.0, injection_pressure: float = 100.0) -> tuple[
    PumpControlService, FakePump, FakePump
]:
    jacket = FakePump(jacket_pressure, [])
    injection = FakePump(injection_pressure, [])
    control = PumpControlService(jacket_pump=jacket, injection_pump=injection)
    control.authorize(PumpControlService.AUTHORIZATION)
    return control, jacket, injection


def prepare(control: PumpControlService, role: PumpRole) -> None:
    control.enter_remote(role)
    control.configure(role, PumpOperatingMode.CONSTANT_FLOW, 1.0)


def test_remote_configure_run_stop_and_local_sequence() -> None:
    control, jacket, _ = service()
    prepare(control, PumpRole.JACKET)

    control.run(PumpRole.JACKET, PumpControlService.RUN_JACKET_CONFIRMATION)
    control.stop(PumpRole.JACKET)
    control.return_local(PumpRole.JACKET)

    assert jacket.commands == ["REMOTE", "FLOW=1.0", "RUN", "STOP", "LOCAL"]


def test_injection_run_requires_twenty_bar_jacket_margin() -> None:
    control, _, injection = service(jacket_pressure=119.9)
    prepare(control, PumpRole.INJECTION)

    with pytest.raises(PermissionError, match="at least 20.00 bar"):
        control.run(PumpRole.INJECTION, PumpControlService.RUN_INJECTION_CONFIRMATION)

    assert "RUN" not in injection.commands


def test_injection_run_at_exact_margin_is_allowed() -> None:
    control, _, injection = service()
    prepare(control, PumpRole.INJECTION)

    control.run(PumpRole.INJECTION, PumpControlService.RUN_INJECTION_CONFIRMATION)

    assert injection.commands[-1] == "RUN"


def test_run_requires_exact_confirmation_and_configuration() -> None:
    control, _, _ = service()
    with pytest.raises(RuntimeError, match="configured"):
        control.run(PumpRole.JACKET, PumpControlService.RUN_JACKET_CONFIRMATION)
    prepare(control, PumpRole.JACKET)
    with pytest.raises(PermissionError, match="confirmation"):
        control.run(PumpRole.JACKET, "yes")


def test_global_safe_stop_observation_clears_running_state_without_new_command() -> None:
    control, jacket, _ = service()
    prepare(control, PumpRole.JACKET)
    control.run(PumpRole.JACKET, PumpControlService.RUN_JACKET_CONFIRMATION)
    command_count = len(jacket.commands)

    control.observe_safe_stop()

    assert not control.state(PumpRole.JACKET).running
    assert len(jacket.commands) == command_count


def test_full_safety_interlock_blocks_every_pump_run() -> None:
    jacket = FakePump(120.0, [])
    injection = FakePump(100.0, [])
    control = PumpControlService(
        jacket_pump=jacket,
        injection_pump=injection,
        safety_check=lambda: ("line pressure limit exceeded",),
    )
    control.authorize(PumpControlService.AUTHORIZATION)
    prepare(control, PumpRole.JACKET)

    with pytest.raises(PermissionError, match="safety interlock active"):
        control.run(PumpRole.JACKET, PumpControlService.RUN_JACKET_CONFIRMATION)

    assert "RUN" not in jacket.commands
