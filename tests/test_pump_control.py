from dataclasses import dataclass

import pytest

from eor_control.domain import PumpStatus
from eor_control.pump_control import PumpControlService, PumpOperatingMode, PumpRole
from eor_control.safety import ManualSafetyMonitor


@dataclass
class FakePump:
    pressure: float
    commands: list[str]
    connected: bool = False

    def connect(self) -> None:
        self.connected = True
        self.commands.append("CONNECT")

    def read_status(self) -> PumpStatus:
        if not self.connected:
            raise ConnectionError("pump disconnected")
        return PumpStatus(self.pressure, 0.0, 200.0)

    def enter_remote(self) -> None:
        self.commands.append("REMOTE")

    def set_constant_flow(self, target: float) -> None:
        self.commands.append(f"FLOW={target}")

    def set_constant_pressure(self, target: float) -> None:
        self.commands.append(f"PRESS={target}")

    def set_pressure_limit(self, target: float) -> None:
        self.commands.append(f"MAXPRESS={target}")

    def run(self) -> None:
        self.commands.append("RUN")

    def request_stop(self) -> None:
        self.commands.append("STOP")

    def clear(self) -> None:
        self.commands.append("CLEAR")

    def return_local(self) -> None:
        self.commands.append("LOCAL")

    def disconnect(self) -> None:
        self.connected = False
        self.commands.append("DISCONNECT")


def service(
    jacket_pressure: float = 120.0,
    injection_pressure: float = 100.0,
    minimum_jacket_margin_bar: float = 20.0,
) -> tuple[
    PumpControlService, FakePump, FakePump
]:
    jacket = FakePump(jacket_pressure, [])
    injection = FakePump(injection_pressure, [])
    control = PumpControlService(
        jacket_pump=jacket,
        injection_pump=injection,
        minimum_jacket_margin_bar=minimum_jacket_margin_bar,
    )
    control.authorize(PumpControlService.AUTHORIZATION)
    control.connect(PumpRole.JACKET)
    control.connect(PumpRole.INJECTION)
    jacket.commands.clear()
    injection.commands.clear()
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


def test_measurement_start_preserves_hourly_flow_targets() -> None:
    control, jacket, injection = service()

    control.start_measurement_pumps(
        jacket_target_pressure_bar=120.0,
        jacket_buildup_flow_ml_per_hour=1000.0,
        injection_start_pressure_bar=100.0,
        injection_target_flow_ml_per_hour=1000.0,
        confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
    )

    assert jacket.commands == [
        "REMOTE",
        "FLOW=1000.0",
        "RUN",
        "STOP",
        "PRESS=120.0",
        "RUN",
    ]
    assert injection.commands == ["REMOTE", "FLOW=1000.0", "RUN"]
    assert control.state(PumpRole.JACKET).running
    assert control.state(PumpRole.INJECTION).running


def test_measurement_start_programs_both_hardware_pressure_limits() -> None:
    control, jacket, injection = service()

    control.start_measurement_pumps(
        jacket_target_pressure_bar=120.0,
        jacket_buildup_flow_ml_per_hour=1000.0,
        injection_start_pressure_bar=100.0,
        injection_target_flow_ml_per_hour=1000.0,
        jacket_pressure_limit_bar=150.0,
        injection_pressure_limit_bar=130.0,
        confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
    )

    assert "MAXPRESS=150.0" in jacket.commands
    assert "MAXPRESS=130.0" in injection.commands


def test_injection_starts_after_margin_before_jacket_reaches_full_target() -> None:
    control, jacket, injection = service(
        jacket_pressure=20.0,
        injection_pressure=0.0,
    )

    with pytest.raises(TimeoutError, match="targets were not reached"):
        control.start_measurement_pumps(
            jacket_target_pressure_bar=120.0,
            jacket_buildup_flow_ml_per_hour=60.0,
            injection_start_pressure_bar=100.0,
            injection_target_flow_ml_per_hour=60.0,
            confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
            pressure_buildup_timeout_seconds=0.001,
            polling_interval_seconds=0.001,
        )

    assert "RUN" in injection.commands
    assert "PRESS=120.0" not in jacket.commands


def test_measurement_start_requires_exact_confirmation() -> None:
    control, jacket, injection = service()

    with pytest.raises(PermissionError, match="confirmation"):
        control.start_measurement_pumps(
            jacket_target_pressure_bar=120.0,
            jacket_buildup_flow_ml_per_hour=60.0,
            injection_start_pressure_bar=100.0,
            injection_target_flow_ml_per_hour=60.0,
            confirmation="yes",
        )

    assert jacket.commands == []
    assert injection.commands == []


def test_measurement_start_safety_failure_stops_both_pumps() -> None:
    control, jacket, injection = service()

    with pytest.raises(PermissionError, match="line pressure limit"):
        control.start_measurement_pumps(
            jacket_target_pressure_bar=120.0,
            jacket_buildup_flow_ml_per_hour=60.0,
            injection_start_pressure_bar=100.0,
            injection_target_flow_ml_per_hour=60.0,
            confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
            startup_safety_check=lambda: ("line pressure limit exceeded",),
        )

    assert jacket.commands == ["REMOTE", "FLOW=60.0", "RUN", "STOP"]
    assert injection.commands == ["STOP"]
    assert not control.state(PumpRole.JACKET).running
    assert not control.state(PumpRole.INJECTION).running


def test_measurement_start_pressure_timeout_never_runs_injection() -> None:
    control, jacket, injection = service(jacket_pressure=119.0)

    with pytest.raises(TimeoutError, match="margin remained 19.00 bar"):
        control.start_measurement_pumps(
            jacket_target_pressure_bar=120.0,
            jacket_buildup_flow_ml_per_hour=60.0,
            injection_start_pressure_bar=100.0,
            injection_target_flow_ml_per_hour=60.0,
            confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
            pressure_buildup_timeout_seconds=0.001,
            polling_interval_seconds=0.001,
        )

    assert jacket.commands == ["REMOTE", "FLOW=60.0", "RUN", "STOP"]
    assert injection.commands == ["STOP"]
    assert "RUN" not in injection.commands


def test_measurement_start_waits_for_injection_start_pressure() -> None:
    control, jacket, injection = service(injection_pressure=99.0)

    with pytest.raises(TimeoutError, match="injection 99.00/100.00 bar"):
        control.start_measurement_pumps(
            jacket_target_pressure_bar=120.0,
            jacket_buildup_flow_ml_per_hour=60.0,
            injection_start_pressure_bar=100.0,
            injection_target_flow_ml_per_hour=60.0,
            confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
            pressure_buildup_timeout_seconds=0.001,
            polling_interval_seconds=0.001,
        )

    assert jacket.commands[-1] == "STOP"
    assert injection.commands == ["REMOTE", "FLOW=60.0", "RUN", "STOP"]
    assert not control.state(PumpRole.JACKET).running
    assert not control.state(PumpRole.INJECTION).running


def test_measurement_start_rechecks_margin_immediately_before_injection_run() -> None:
    control, jacket, injection = service()
    checks = 0

    def safety_check() -> tuple[str, ...]:
        nonlocal checks
        checks += 1
        if checks == 2:
            jacket.pressure = 119.0
        return ()

    with pytest.raises(PermissionError, match="is 19.00 bar"):
        control.start_measurement_pumps(
            jacket_target_pressure_bar=120.0,
            jacket_buildup_flow_ml_per_hour=60.0,
            injection_start_pressure_bar=100.0,
            injection_target_flow_ml_per_hour=60.0,
            confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
            startup_safety_check=safety_check,
        )

    assert "RUN" not in injection.commands
    assert jacket.commands[-1] == "STOP"
    assert injection.commands[-1] == "STOP"


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


def test_injection_run_uses_configured_margin_below_twenty_bar() -> None:
    control, _, injection = service(
        jacket_pressure=110.0,
        minimum_jacket_margin_bar=10.0,
    )
    prepare(control, PumpRole.INJECTION)

    control.run(PumpRole.INJECTION, PumpControlService.RUN_INJECTION_CONFIRMATION)

    assert injection.commands[-1] == "RUN"


def test_manual_pump_safety_does_not_require_other_devices_or_cross_pump_margin() -> None:
    jacket = FakePump(0.0, [])
    injection = FakePump(100.0, [])
    control = PumpControlService(
        jacket_pump=jacket,
        injection_pump=injection,
        manual_safety_check=lambda _role, status: ManualSafetyMonitor.evaluate_pump(
            status, maximum_pressure_bar=150.0
        ).reasons,
        enforce_injection_margin=False,
    )
    control.authorize(PumpControlService.AUTHORIZATION)
    control.connect(PumpRole.INJECTION)
    prepare(control, PumpRole.INJECTION)

    control.run(PumpRole.INJECTION, PumpControlService.RUN_INJECTION_CONFIRMATION)

    assert injection.commands[-1] == "RUN"
    assert jacket.commands == []


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
    control.connect(PumpRole.JACKET)
    control.connect(PumpRole.INJECTION)
    jacket.commands.clear()
    injection.commands.clear()
    prepare(control, PumpRole.JACKET)

    with pytest.raises(PermissionError, match="safety interlock active"):
        control.run(PumpRole.JACKET, PumpControlService.RUN_JACKET_CONFIRMATION)

    assert "RUN" not in jacket.commands


class ConnectFailingPump(FakePump):
    def connect(self) -> None:
        raise ConnectionError("sensor-side pump unavailable")


def test_pumps_connect_and_report_status_independently() -> None:
    jacket = FakePump(120.0, [])
    injection = ConnectFailingPump(100.0, [])
    control = PumpControlService(jacket_pump=jacket, injection_pump=injection)
    control.authorize(PumpControlService.AUTHORIZATION)

    jacket_status = control.connect(PumpRole.JACKET)
    with pytest.raises(ConnectionError, match="unavailable"):
        control.connect(PumpRole.INJECTION)
    statuses, errors = control.read_available_statuses()

    assert jacket_status.pressure_bar == 120.0
    assert statuses[PumpRole.JACKET].pressure_bar == 120.0
    assert PumpRole.INJECTION not in statuses
    assert errors[PumpRole.INJECTION] == "nincs csatlakoztatva"


def test_manual_connect_enters_remote_as_one_operation() -> None:
    jacket = FakePump(120.0, [])
    control = PumpControlService(
        jacket_pump=jacket,
        injection_pump=FakePump(100.0, []),
    )
    control.authorize(PumpControlService.AUTHORIZATION)

    status = control.connect_remote(PumpRole.JACKET)

    assert status.pressure_bar == 120.0
    assert jacket.commands == ["CONNECT", "REMOTE"]
    assert control.connected(PumpRole.JACKET)
    assert control.state(PumpRole.JACKET).remote


class RemoteFailingPump(FakePump):
    def enter_remote(self) -> None:
        raise ConnectionError("REMOTE unavailable")


def test_manual_connect_closes_port_when_remote_fails() -> None:
    jacket = RemoteFailingPump(120.0, [])
    control = PumpControlService(
        jacket_pump=jacket,
        injection_pump=FakePump(100.0, []),
    )
    control.authorize(PumpControlService.AUTHORIZATION)

    with pytest.raises(ConnectionError, match="REMOTE unavailable"):
        control.connect_remote(PumpRole.JACKET)

    assert jacket.commands == ["CONNECT", "DISCONNECT"]
    assert not control.connected(PumpRole.JACKET)


class StopFailingPump(FakePump):
    def request_stop(self) -> None:
        raise ConnectionError("STOP unavailable")


def test_partial_shutdown_attempts_every_stop_and_disconnect_independently() -> None:
    jacket = StopFailingPump(120.0, [])
    injection = FakePump(100.0, [])
    control = PumpControlService(jacket_pump=jacket, injection_pump=injection)
    control.authorize(PumpControlService.AUTHORIZATION)
    control.connect(PumpRole.JACKET)
    control.connect(PumpRole.INJECTION)
    jacket.commands.clear()
    injection.commands.clear()

    errors = control.shutdown_connections()

    assert errors == ("jacket STOP: STOP unavailable",)
    assert jacket.commands == ["DISCONNECT"]
    assert injection.commands == ["STOP", "DISCONNECT"]
    assert not control.connected(PumpRole.JACKET)
    assert not control.connected(PumpRole.INJECTION)


def test_shutdown_closes_ports_even_when_pumps_were_not_connected() -> None:
    jacket = FakePump(120.0, [])
    injection = FakePump(100.0, [])
    control = PumpControlService(jacket_pump=jacket, injection_pump=injection)
    control.authorize(PumpControlService.AUTHORIZATION)

    errors = control.shutdown_connections()

    assert errors == ()
    assert jacket.commands == ["DISCONNECT"]
    assert injection.commands == ["DISCONNECT"]
