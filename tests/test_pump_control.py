from dataclasses import dataclass
from time import sleep

import pytest

from eor_control.domain import PumpStatus
from eor_control.pump_control import (
    PumpControlService,
    PumpControlTiming,
    PumpOperatingMode,
    PumpRole,
    PumpStartupPlan,
)
from eor_control.pump_telemetry import PollingPump, PumpPollingIntervals
from eor_control.safety import ManualSafetyMonitor


@dataclass
class FakePump:
    pressure: float
    commands: list[str]
    connected: bool = False
    configured_flow_readback: float | None = None

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

    def read_configured_flow_ml_per_hour(self) -> float:
        if self.configured_flow_readback is not None:
            return self.configured_flow_readback
        return float(self.commands[-1].split("=", 1)[1])

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
    enforce_injection_margin: bool = True,
) -> tuple[
    PumpControlService, FakePump, FakePump
]:
    jacket = FakePump(jacket_pressure, [])
    injection = FakePump(injection_pressure, [])
    control = PumpControlService(
        jacket_pump=jacket,
        injection_pump=injection,
        minimum_jacket_margin_bar=minimum_jacket_margin_bar,
        enforce_injection_margin=enforce_injection_margin,
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

    control.prepare_measurement_pumps(
        PumpStartupPlan(120.0, 1000.0, 100.0, 1000.0),
        timing=PumpControlTiming(),
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
    assert injection.commands == ["REMOTE", "FLOW=1000.0", "RUN", "STOP"]
    assert control.state(PumpRole.JACKET).running
    assert not control.state(PumpRole.INJECTION).running


def test_measurement_preparation_enforces_configured_control_deadline() -> None:
    control, jacket, injection = service()

    def slow_safety_check() -> tuple[str, ...]:
        sleep(0.02)
        return ()

    with pytest.raises(TimeoutError, match="control cycle deadline missed"):
        control.start_measurement_pumps(
            jacket_target_pressure_bar=120.0,
            jacket_buildup_flow_ml_per_hour=10.0,
            injection_start_pressure_bar=100.0,
            injection_target_flow_ml_per_hour=10.0,
            confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
            startup_safety_check=slow_safety_check,
            control_interval_seconds=0.005,
            control_watchdog_tolerance_seconds=0.001,
        )

    assert jacket.commands[-1] == "STOP"
    assert injection.commands[-1] == "STOP"


def test_blocking_preparation_commands_start_a_fresh_control_cycle() -> None:
    class SlowCommandPump(FakePump):
        def set_constant_pressure(self, target: float) -> None:
            sleep(0.05)
            super().set_constant_pressure(target)

        def run(self) -> None:
            sleep(0.05)
            super().run()

        def request_stop(self) -> None:
            sleep(0.05)
            super().request_stop()

    jacket = SlowCommandPump(120.0, [])
    injection = SlowCommandPump(100.0, [])
    control = PumpControlService(jacket_pump=jacket, injection_pump=injection)
    control.authorize(PumpControlService.AUTHORIZATION)
    control.connect(PumpRole.JACKET)
    control.connect(PumpRole.INJECTION)
    jacket.commands.clear()
    injection.commands.clear()

    control.prepare_measurement_pumps(
        PumpStartupPlan(120.0, 60.0, 100.0, 10.0),
        timing=PumpControlTiming(
                control_interval_seconds=0.01,
                watchdog_tolerance_seconds=0.02,
        ),
        confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
    )

    assert jacket.commands == [
        "REMOTE",
        "FLOW=60.0",
        "RUN",
        "STOP",
        "PRESS=120.0",
        "RUN",
    ]
    assert injection.commands == ["REMOTE", "FLOW=10.0", "RUN", "STOP"]


def test_async_pump_workers_keep_slow_stop_outside_control_deadline() -> None:
    @dataclass
    class WorkerPump(FakePump):
        operating_status: str = "STOP REMOTE"
        stop_delay_seconds: float = 0.0

        def read_pressure_bar(self) -> float:
            return self.pressure

        def read_flow_ml_per_hour(self) -> float:
            return 0.0

        def read_remaining_volume_ml(self) -> float:
            return 200.0

        def read_operating_status(self) -> str:
            return self.operating_status

        def enter_remote(self) -> None:
            super().enter_remote()
            self.operating_status = "STOP REMOTE"

        def run(self) -> None:
            super().run()
            self.operating_status = "RUN REMOTE"

        def request_stop(self) -> None:
            sleep(self.stop_delay_seconds)
            super().request_stop()
            self.operating_status = "STOP REMOTE"

    intervals = PumpPollingIntervals(
        pressure_seconds=0.02,
        slow_telemetry_seconds=0.03,
        pressure_stale_seconds=1.0,
        slow_telemetry_stale_seconds=1.0,
        startup_timeout_seconds=1.0,
    )
    jacket_raw = WorkerPump(120.0, [], stop_delay_seconds=0.07)
    injection_raw = WorkerPump(100.0, [], stop_delay_seconds=0.07)
    jacket = PollingPump(jacket_raw, name="jacket", intervals=intervals)
    injection = PollingPump(injection_raw, name="injection", intervals=intervals)
    control = PumpControlService(jacket_pump=jacket, injection_pump=injection)
    control.authorize(PumpControlService.AUTHORIZATION)
    control.connect(PumpRole.JACKET)
    control.connect(PumpRole.INJECTION)

    control.prepare_measurement_pumps(
        PumpStartupPlan(120.0, 60.0, 100.0, 10.0),
        timing=PumpControlTiming(
            control_interval_seconds=0.005,
            watchdog_tolerance_seconds=0.001,
            command_timeout_seconds=0.5,
        ),
        confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
    )

    assert not control.state(PumpRole.INJECTION).running
    assert control.state(PumpRole.JACKET).running
    jacket.disconnect()
    injection.disconnect()


def test_preparation_checks_safety_between_jacket_transition_commands() -> None:
    control, jacket, injection = service()
    observed_jacket_commands: list[tuple[str, ...]] = []

    control.prepare_measurement_pumps(
        PumpStartupPlan(120.0, 60.0, 100.0, 10.0),
        timing=PumpControlTiming(control_interval_seconds=0.001),
        confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
        startup_safety_check=lambda: (
            observed_jacket_commands.append(tuple(jacket.commands)) or ()
        ),
    )

    stop_index = observed_jacket_commands.index(
        ("REMOTE", "FLOW=60.0", "RUN", "STOP")
    )
    pressure_index = observed_jacket_commands.index(
        ("REMOTE", "FLOW=60.0", "RUN", "STOP", "PRESS=120.0")
    )
    hold_run_index = observed_jacket_commands.index(
        ("REMOTE", "FLOW=60.0", "RUN", "STOP", "PRESS=120.0", "RUN")
    )
    assert stop_index < pressure_index < hold_run_index
    assert injection.commands == ["REMOTE", "FLOW=10.0", "RUN", "STOP"]


def test_preparation_control_deadline_uses_absolute_cadence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [10.08]
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr("eor_control.pump_control.monotonic", lambda: now[0])
    monkeypatch.setattr("eor_control.pump_control.sleep", fake_sleep)

    deadline = PumpControlService._wait_for_control_deadline(
        10.0,
        0.1,
        10.0,
        1.0,
    )
    assert deadline == pytest.approx(10.1)
    assert sleeps == pytest.approx([0.02])

    now[0] = 10.36
    deadline = PumpControlService._wait_for_control_deadline(
        deadline,
        0.1,
        10.3,
        1.0,
    )
    assert deadline == pytest.approx(10.4)
    assert sleeps[-1] == pytest.approx(0.04)


def test_startup_targets_must_remain_stable_before_confirmation_state() -> None:
    class ChangingPump(FakePump):
        reads: int = 0

        def read_status(self) -> PumpStatus:
            self.reads += 1
            if self.reads == 6:
                self.pressure = 99.0
            return super().read_status()

    jacket = FakePump(120.0, [])
    injection = ChangingPump(100.0, [])
    control = PumpControlService(jacket_pump=jacket, injection_pump=injection)
    control.authorize(PumpControlService.AUTHORIZATION)
    control.connect(PumpRole.JACKET)
    control.connect(PumpRole.INJECTION)
    jacket.commands.clear()
    injection.commands.clear()

    with pytest.raises(TimeoutError, match="injection startup target"):
        control.start_measurement_pumps(
            jacket_target_pressure_bar=120.0,
            jacket_buildup_flow_ml_per_hour=60.0,
            injection_start_pressure_bar=100.0,
            injection_target_flow_ml_per_hour=10.0,
            confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
            margin_stability_seconds=0.01,
            pressure_buildup_timeout_seconds=0.02,
            control_interval_seconds=0.001,
        )

    assert injection.commands[-1] == "STOP"


def test_measurement_start_reads_both_pumps_before_first_write() -> None:
    class OrderedPump(FakePump):
        def read_status(self) -> PumpStatus:
            self.commands.append("READ")
            return super().read_status()

    jacket = OrderedPump(120.0, [])
    injection = OrderedPump(100.0, [])
    control = PumpControlService(jacket_pump=jacket, injection_pump=injection)
    control.authorize(PumpControlService.AUTHORIZATION)
    control.connect(PumpRole.JACKET)
    control.connect(PumpRole.INJECTION)
    jacket.commands.clear()
    injection.commands.clear()

    control.start_measurement_pumps(
        jacket_target_pressure_bar=120.0,
        jacket_buildup_flow_ml_per_hour=60.0,
        injection_start_pressure_bar=100.0,
        injection_target_flow_ml_per_hour=10.0,
        confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
    )

    assert jacket.commands[0] == "READ"
    assert injection.commands[0] == "READ"
    assert jacket.commands.index("READ") < jacket.commands.index("FLOW=60.0")
    assert injection.commands.index("READ") < injection.commands.index("FLOW=10.0")


def test_failed_initial_read_sends_no_startup_write() -> None:
    class ReadFailingPump(FakePump):
        def read_status(self) -> PumpStatus:
            self.commands.append("READ")
            raise ConnectionError("pressure unavailable")

    jacket = FakePump(120.0, [])
    injection = ReadFailingPump(100.0, [])
    control = PumpControlService(jacket_pump=jacket, injection_pump=injection)
    control.authorize(PumpControlService.AUTHORIZATION)
    control.connect(PumpRole.JACKET)
    with pytest.raises(ConnectionError):
        control.connect(PumpRole.INJECTION)
    injection.connected = True
    control._connected[PumpRole.INJECTION] = True
    jacket.commands.clear()
    injection.commands.clear()

    with pytest.raises(ConnectionError, match="pressure unavailable"):
        control.start_measurement_pumps(
            jacket_target_pressure_bar=120.0,
            jacket_buildup_flow_ml_per_hour=60.0,
            injection_start_pressure_bar=100.0,
            injection_target_flow_ml_per_hour=10.0,
            confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
        )

    assert injection.commands[0] == "READ"
    assert injection.commands[-1] == "STOP"
    assert not any(
        command == "REMOTE" or command == "RUN" or command.startswith("FLOW=")
        for command in injection.commands
    )
    assert not any(
        command in {"REMOTE", "RUN"} or command.startswith("FLOW=")
        for command in jacket.commands + injection.commands
    )


def test_measurement_flow_uses_stop_set_verify_run_sequence() -> None:
    control, _, injection = service()
    prepare(control, PumpRole.INJECTION)
    control.run(PumpRole.INJECTION, PumpControlService.RUN_INJECTION_CONFIRMATION)
    injection.commands.clear()

    applied = control.apply_measurement_flow(25.0)

    assert applied == pytest.approx(25.0)
    assert injection.commands == ["STOP", "FLOW=25.0", "RUN"]


def test_measurement_flow_rejects_firmware_readback_outside_tolerance() -> None:
    control, _, injection = service()
    prepare(control, PumpRole.INJECTION)
    control.run(PumpRole.INJECTION, PumpControlService.RUN_INJECTION_CONFIRMATION)
    injection.commands.clear()
    injection.configured_flow_readback = 24.0

    with pytest.raises(RuntimeError, match="flow verification failed"):
        control.apply_measurement_flow(25.0)

    assert injection.commands == ["STOP", "FLOW=25.0"]
    assert not control.state(PumpRole.INJECTION).running


def test_measurement_flow_uses_common_safety_gate_before_run() -> None:
    jacket = FakePump(120.0, [])
    injection = FakePump(100.0, [])
    control = PumpControlService(
        jacket_pump=jacket,
        injection_pump=injection,
        safety_check=lambda: ("blocked",),
    )
    control.authorize(PumpControlService.AUTHORIZATION)
    control.connect(PumpRole.JACKET)
    control.connect(PumpRole.INJECTION)
    prepare(control, PumpRole.INJECTION)
    injection.commands.clear()

    with pytest.raises(PermissionError, match="safety interlock active"):
        control.apply_measurement_flow(25.0)

    assert injection.commands == ["STOP", "FLOW=25.0"]
    assert not control.state(PumpRole.INJECTION).running


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


def test_injection_waits_for_jacket_target_and_pressure_holding() -> None:
    control, jacket, injection = service(
        jacket_pressure=20.0,
        injection_pressure=0.0,
    )

    with pytest.raises(TimeoutError, match="jacket startup target"):
        control.start_measurement_pumps(
            jacket_target_pressure_bar=120.0,
            jacket_buildup_flow_ml_per_hour=60.0,
            injection_start_pressure_bar=100.0,
            injection_target_flow_ml_per_hour=60.0,
            confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
            pressure_buildup_timeout_seconds=0.001,
            control_interval_seconds=0.001,
        )

    assert "RUN" not in injection.commands
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

    assert jacket.commands == ["STOP"]
    assert injection.commands == ["STOP"]
    assert not control.state(PumpRole.JACKET).running
    assert not control.state(PumpRole.INJECTION).running


def test_measurement_start_pressure_timeout_never_runs_injection() -> None:
    control, jacket, injection = service(jacket_pressure=119.0)

    with pytest.raises(TimeoutError, match="margin 19.000/20.000 bar"):
        control.start_measurement_pumps(
            jacket_target_pressure_bar=120.0,
            jacket_buildup_flow_ml_per_hour=60.0,
            injection_start_pressure_bar=100.0,
            injection_target_flow_ml_per_hour=60.0,
            confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
            pressure_buildup_timeout_seconds=0.001,
            control_interval_seconds=0.001,
        )

    assert jacket.commands == ["REMOTE", "FLOW=60.0", "RUN", "STOP"]
    assert injection.commands == ["STOP"]
    assert "RUN" not in injection.commands


def test_measurement_start_waits_for_injection_start_pressure() -> None:
    control, jacket, injection = service(injection_pressure=99.0)

    with pytest.raises(TimeoutError, match="pressure 99.000/100.000 bar"):
        control.start_measurement_pumps(
            jacket_target_pressure_bar=120.0,
            jacket_buildup_flow_ml_per_hour=60.0,
            injection_start_pressure_bar=100.0,
            injection_target_flow_ml_per_hour=60.0,
            confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
            pressure_buildup_timeout_seconds=0.001,
            control_interval_seconds=0.001,
        )

    assert jacket.commands[-1] == "STOP"
    assert injection.commands == ["REMOTE", "FLOW=60.0", "RUN", "STOP"]
    assert not control.state(PumpRole.JACKET).running
    assert not control.state(PumpRole.INJECTION).running


def test_measurement_start_rechecks_margin_immediately_before_injection_run() -> None:
    # The automatic preparation sequence owns this gate even when the service's
    # optional manual RUN margin policy is disabled.
    control, jacket, injection = service(enforce_injection_margin=False)

    def safety_check() -> tuple[str, ...]:
        if injection.commands and injection.commands[-1] == "FLOW=60.0":
            jacket.pressure = 119.0
        return ()

    with pytest.raises(PermissionError, match="is 19.000 bar"):
        control.start_measurement_pumps(
            jacket_target_pressure_bar=120.0,
            jacket_buildup_flow_ml_per_hour=60.0,
            injection_start_pressure_bar=100.0,
            injection_target_flow_ml_per_hour=60.0,
            confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
            startup_safety_check=safety_check,
            control_interval_seconds=0.001,
        )

    assert "RUN" not in injection.commands
    assert jacket.commands[-1] == "STOP"
    assert injection.commands[-1] == "STOP"


def test_jacket_reaches_twenty_bar_margin_before_any_injection_setup() -> None:
    control, jacket, injection = service(
        jacket_pressure=100.0,
        injection_pressure=100.0,
    )
    observed_injection_commands: list[tuple[float, tuple[str, ...]]] = []

    def build_jacket_margin() -> tuple[str, ...]:
        observed_injection_commands.append(
            (jacket.pressure - injection.pressure, tuple(injection.commands))
        )
        jacket.pressure = min(120.0, jacket.pressure + 5.0)
        return ()

    control.start_measurement_pumps(
        jacket_target_pressure_bar=120.0,
        jacket_buildup_flow_ml_per_hour=60.0,
        injection_start_pressure_bar=100.0,
        injection_target_flow_ml_per_hour=10.0,
        confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
        startup_safety_check=build_jacket_margin,
        control_interval_seconds=0.001,
    )

    assert all(
        commands == ()
        for margin, commands in observed_injection_commands
        if margin < 20.0
    )
    assert injection.commands[:3] == ["REMOTE", "FLOW=10.0", "RUN"]


def test_margin_may_fall_after_injection_run_while_jacket_holds_fixed_target() -> None:
    control, jacket, injection = service()

    def safety_check() -> tuple[str, ...]:
        if injection.commands and injection.commands[-1] == "RUN":
            injection.pressure = 105.0
        return ()

    control.start_measurement_pumps(
        jacket_target_pressure_bar=120.0,
        jacket_buildup_flow_ml_per_hour=60.0,
        injection_start_pressure_bar=100.0,
        injection_target_flow_ml_per_hour=60.0,
        confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
        startup_safety_check=safety_check,
        control_interval_seconds=0.001,
    )

    assert jacket.commands[-3:] == ["STOP", "PRESS=120.0", "RUN"]
    assert injection.commands[-2:] == ["RUN", "STOP"]
    assert jacket.pressure - injection.pressure == pytest.approx(15.0)
    assert control.state(PumpRole.JACKET).mode is PumpOperatingMode.CONSTANT_PRESSURE
    assert control.state(PumpRole.JACKET).target == pytest.approx(120.0)


def test_injection_run_requires_twenty_bar_jacket_margin() -> None:
    control, _, injection = service(jacket_pressure=119.9)
    prepare(control, PumpRole.INJECTION)

    with pytest.raises(PermissionError, match="at least 20.000 bar"):
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


def test_injection_run_uses_updated_configured_margin() -> None:
    control, _, injection = service(jacket_pressure=112.0)
    prepare(control, PumpRole.INJECTION)
    control.set_minimum_jacket_margin_bar(12.0)

    control.run(PumpRole.INJECTION, PumpControlService.RUN_INJECTION_CONFIRMATION)

    assert control.minimum_jacket_margin_bar == pytest.approx(12.0)
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


class LocalModeOncePump(FakePump):
    stop_attempts: int = 0

    def request_stop(self) -> None:
        self.stop_attempts += 1
        self.commands.append("STOP")
        if self.stop_attempts == 1:
            raise RuntimeError("PROBLEM=LOCAL MODE")


def test_rollback_recovers_local_mode_with_remote_then_stop() -> None:
    jacket = FakePump(120.0, [])
    injection = LocalModeOncePump(100.0, [])
    control = PumpControlService(jacket_pump=jacket, injection_pump=injection)
    control.authorize(PumpControlService.AUTHORIZATION)
    control.connect(PumpRole.JACKET)
    control.connect(PumpRole.INJECTION)
    jacket.commands.clear()
    injection.commands.clear()

    errors = control.stop_all()

    assert errors == ()
    assert jacket.commands == ["STOP"]
    assert injection.commands == ["STOP", "REMOTE", "STOP"]
    assert not control.state(PumpRole.INJECTION).running
    assert control.state(PumpRole.INJECTION).remote


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
