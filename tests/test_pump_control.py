from dataclasses import dataclass
from time import sleep

import pytest

from eor_control.domain import DataQuality, PumpStatus
from eor_control.pump_commands import (
    PumpCommand,
    PumpCommandKind,
    PumpCommandPriority,
    PumpCommandResult,
    PumpCommandStatus,
)
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


def test_control_write_skips_remote_when_device_already_confirms_remote() -> None:
    class AlreadyRemotePump(FakePump):
        def is_remote_mode(self) -> bool:
            return True

    jacket = AlreadyRemotePump(120.0, [])
    control = PumpControlService(
        jacket_pump=jacket,
        injection_pump=FakePump(100.0, []),
    )
    control.authorize(PumpControlService.AUTHORIZATION)
    control.connect(PumpRole.JACKET)
    jacket.commands.clear()

    control.configure(PumpRole.JACKET, PumpOperatingMode.CONSTANT_FLOW, 1.0)

    assert jacket.commands == ["FLOW=1.0"]
    assert control.state(PumpRole.JACKET).remote


def test_control_write_enters_remote_when_device_is_not_remote() -> None:
    class LocalPump(FakePump):
        def is_remote_mode(self) -> bool:
            return False

    jacket = LocalPump(120.0, [])
    control = PumpControlService(
        jacket_pump=jacket,
        injection_pump=FakePump(100.0, []),
    )
    control.authorize(PumpControlService.AUTHORIZATION)
    control.connect(PumpRole.JACKET)
    jacket.commands.clear()

    control.configure(PumpRole.JACKET, PumpOperatingMode.CONSTANT_FLOW, 1.0)

    assert jacket.commands == ["REMOTE", "FLOW=1.0"]
    assert control.state(PumpRole.JACKET).remote


def test_measurement_start_preserves_hourly_flow_targets() -> None:
    control, jacket, injection = service(100.0, 80.0)

    def build_pressures() -> tuple[str, ...]:
        if control.state(PumpRole.JACKET).running:
            jacket.pressure = min(120.0, jacket.pressure + 5.0)
        if control.state(PumpRole.INJECTION).running:
            injection.pressure = min(100.0, injection.pressure + 5.0)
        return ()

    control.prepare_measurement_pumps(
        PumpStartupPlan(120.0, 1000.0, 100.0, 1000.0, margin_stability_seconds=0.0),
        timing=PumpControlTiming(),
        confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
        startup_safety_check=build_pressures,
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


def test_measurement_preparation_skips_redundant_remote_commands() -> None:
    class AlreadyRemotePump(FakePump):
        supervision: list[bool]

        def __init__(self, pressure: float) -> None:
            super().__init__(pressure, [])
            self.supervision = []

        def is_remote_mode(self) -> bool:
            return True

        def set_remote_supervision_active(self, active: bool) -> None:
            self.supervision.append(active)

    jacket = AlreadyRemotePump(100.0)
    injection = AlreadyRemotePump(80.0)
    control = PumpControlService(jacket_pump=jacket, injection_pump=injection)
    control.authorize(PumpControlService.AUTHORIZATION)
    control.connect(PumpRole.JACKET)
    control.connect(PumpRole.INJECTION)
    jacket.commands.clear()
    injection.commands.clear()

    def build_pressures() -> tuple[str, ...]:
        if control.state(PumpRole.JACKET).running:
            jacket.pressure = min(120.0, jacket.pressure + 5.0)
        if control.state(PumpRole.INJECTION).running:
            injection.pressure = min(100.0, injection.pressure + 5.0)
        return ()

    control.prepare_measurement_pumps(
        PumpStartupPlan(120.0, 1000.0, 100.0, 1000.0, margin_stability_seconds=0.0),
        timing=PumpControlTiming(),
        confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
        startup_safety_check=build_pressures,
    )

    assert "REMOTE" not in jacket.commands
    assert "REMOTE" not in injection.commands
    assert jacket.commands == [
        "FLOW=1000.0",
        "RUN",
        "STOP",
        "PRESS=120.0",
        "RUN",
    ]
    assert injection.commands == ["FLOW=1000.0", "RUN", "STOP"]
    assert jacket.supervision == [True]
    assert injection.supervision == [True]


def test_local_pump_remote_failure_has_specific_preparation_error() -> None:
    class AsyncPreparationPump(FakePump):
        def __init__(self, pressure: float, *, stopped_local: bool) -> None:
            super().__init__(pressure, [])
            self.stopped_local = stopped_local
            self.submitted: dict[str, PumpCommand] = {}
            self.preparation_states: list[bool] = []

        def read_cached_status(self) -> tuple[PumpStatus, DataQuality]:
            return self.read_status(), DataQuality.GOOD

        def set_preparation_active(self, active: bool) -> None:
            self.preparation_states.append(active)

        def is_stopped_local(self) -> bool:
            return self.stopped_local

        def cancel_pending_commands(self) -> None:
            return

        def submit_command(self, command: PumpCommand) -> str:
            command_id = f"command-{len(self.submitted) + 1}"
            self.submitted[command_id] = command
            return command_id

        def submit_stop(self, *, emergency: bool = False) -> str:
            assert emergency
            if self.stopped_local:
                pytest.fail("STOP LOCAL pump must not receive rollback STOP")
            return self.submit_command(
                PumpCommand(
                    PumpCommandKind.STOP,
                    PumpCommandPriority.EMERGENCY,
                    verify_status=True,
                )
            )

        def command_result(self, command_id: str) -> PumpCommandResult:
            command = self.submitted[command_id]
            failed_remote = (
                command.kind is PumpCommandKind.ENTER_REMOTE and self.stopped_local
            )
            return PumpCommandResult(
                command_id,
                command,
                (
                    PumpCommandStatus.FAILED
                    if failed_remote
                    else PumpCommandStatus.SUCCEEDED
                ),
                submitted_monotonic=0.0,
                started_monotonic=0.0,
                completed_monotonic=0.0,
                operating_status="STOP LOCAL" if failed_remote else "STOP REMOTE",
                error=(
                    "RuntimeError: pump STATUS did not confirm REMOTE"
                    if failed_remote
                    else None
                ),
            )

    jacket = AsyncPreparationPump(120.0, stopped_local=True)
    injection = AsyncPreparationPump(100.0, stopped_local=False)
    control = PumpControlService(jacket_pump=jacket, injection_pump=injection)
    control.authorize(PumpControlService.AUTHORIZATION)
    control.connect(PumpRole.JACKET)
    control.connect(PumpRole.INJECTION)

    with pytest.raises(
        RuntimeError,
        match=r"A jacket pumpa nem állítható Remote módba\.",
    ):
        control.prepare_measurement_pumps(
            PumpStartupPlan(120.0, 60.0, 100.0, 10.0),
            timing=PumpControlTiming(control_interval_seconds=0.001),
            confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
        )

    remote = next(iter(jacket.submitted.values()))
    assert remote.kind is PumpCommandKind.ENTER_REMOTE
    assert remote.verify_status
    assert jacket.preparation_states == [True, False]
    assert injection.preparation_states == [True, False]


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

    assert jacket.commands == []
    assert injection.commands == []


def test_blocking_preparation_commands_start_a_fresh_control_cycle() -> None:
    class SlowCommandPump(FakePump):
        def set_constant_pressure(self, target: float) -> None:
            sleep(0.1)
            super().set_constant_pressure(target)

        def run(self) -> None:
            sleep(0.1)
            super().run()

        def request_stop(self) -> None:
            sleep(0.1)
            super().request_stop()

    jacket = SlowCommandPump(100.0, [])
    injection = SlowCommandPump(80.0, [])
    control = PumpControlService(jacket_pump=jacket, injection_pump=injection)
    control.authorize(PumpControlService.AUTHORIZATION)
    control.connect(PumpRole.JACKET)
    control.connect(PumpRole.INJECTION)
    jacket.commands.clear()
    injection.commands.clear()

    def build_pressures() -> tuple[str, ...]:
        if control.state(PumpRole.JACKET).running:
            jacket.pressure = 120.0
        if control.state(PumpRole.INJECTION).running:
            injection.pressure = 100.0
        return ()

    control.prepare_measurement_pumps(
        PumpStartupPlan(120.0, 60.0, 100.0, 10.0, margin_stability_seconds=0.0),
        timing=PumpControlTiming(
            control_interval_seconds=0.01,
            watchdog_tolerance_seconds=0.04,
        ),
        confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
        startup_safety_check=build_pressures,
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
    class CacheOnlySupervisionPump(PollingPump):
        def read_status(self) -> PumpStatus:
            raise AssertionError("fast supervision must use read_cached_status")

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
    jacket_raw = WorkerPump(120.0, [], stop_delay_seconds=0.12)
    injection_raw = WorkerPump(100.0, [], stop_delay_seconds=0.12)
    jacket = CacheOnlySupervisionPump(
        jacket_raw,
        name="jacket",
        intervals=intervals,
    )
    injection = CacheOnlySupervisionPump(
        injection_raw,
        name="injection",
        intervals=intervals,
    )
    control = PumpControlService(jacket_pump=jacket, injection_pump=injection)
    control.authorize(PumpControlService.AUTHORIZATION)
    control.connect(PumpRole.JACKET)
    control.connect(PumpRole.INJECTION)

    control.prepare_measurement_pumps(
        PumpStartupPlan(120.0, 60.0, 100.0, 10.0),
            timing=PumpControlTiming(
                control_interval_seconds=0.01,
                watchdog_tolerance_seconds=0.04,
                execution_timeout_seconds=0.5,
            ),
        confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
    )

    assert not control.state(PumpRole.INJECTION).running
    assert control.state(PumpRole.JACKET).running
    queued_commands = [
        result.command
        for result in (*jacket._command_results.values(), *injection._command_results.values())
    ]
    assert queued_commands
    assert all(
        command.priority is PumpCommandPriority.HIGH for command in queued_commands
    )
    assert all(command.queue_timeout_seconds == 5.0 for command in queued_commands)
    assert all(
        command.execution_timeout_seconds == 0.5 for command in queued_commands
    )
    assert all(
        command.verification_timeout_seconds == 5.0
        for command in queued_commands
    )
    assert not jacket._preparation_active
    assert not injection._preparation_active
    jacket.disconnect()
    injection.disconnect()


def test_preparation_checks_safety_between_jacket_transition_commands() -> None:
    control, jacket, injection = service(100.0, 80.0)
    observed_jacket_commands: list[tuple[str, ...]] = []

    def build_pressures() -> tuple[str, ...]:
        observed_jacket_commands.append(tuple(jacket.commands))
        if control.state(PumpRole.JACKET).running:
            jacket.pressure = 120.0
        if control.state(PumpRole.INJECTION).running:
            injection.pressure = 100.0
        return ()

    control.prepare_measurement_pumps(
        PumpStartupPlan(120.0, 60.0, 100.0, 10.0),
        timing=PumpControlTiming(control_interval_seconds=0.001),
        confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
        startup_safety_check=build_pressures,
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


def test_pressure_buildup_has_no_elapsed_time_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control, jacket, injection = service(
        jacket_pressure=0.0,
        injection_pressure=0.0,
    )
    elapsed = [0.0]

    def simulated_clock() -> float:
        elapsed[0] += 5.0
        return elapsed[0]

    monkeypatch.setattr("eor_control.pump_control.monotonic", simulated_clock)
    monkeypatch.setattr(
        PumpControlService,
        "_require_control_deadline",
        staticmethod(lambda *_args: None),
    )
    monkeypatch.setattr(
        PumpControlService,
        "_wait_for_control_deadline",
        staticmethod(lambda deadline, *_args: deadline),
    )
    monkeypatch.setattr(
        PumpControlService,
        "_wait_after_preparation_command",
        staticmethod(lambda _interval: 0.0),
    )

    with pytest.raises(InterruptedError, match="cancelled"):
        control.prepare_measurement_pumps(
            PumpStartupPlan(120.0, 60.0, 100.0, 10.0),
            timing=PumpControlTiming(control_interval_seconds=0.001),
            confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
            cancel_check=lambda: elapsed[0] > 130.0,
        )

    assert elapsed[0] > 120.0
    assert "RUN" in jacket.commands
    assert "RUN" not in injection.commands


def test_measurement_start_reads_both_pumps_before_first_write() -> None:
    class OrderedPump(FakePump):
        def read_status(self) -> PumpStatus:
            self.commands.append("READ")
            return super().read_status()

    jacket = OrderedPump(100.0, [])
    injection = OrderedPump(80.0, [])
    control = PumpControlService(jacket_pump=jacket, injection_pump=injection)
    control.authorize(PumpControlService.AUTHORIZATION)
    control.connect(PumpRole.JACKET)
    control.connect(PumpRole.INJECTION)
    jacket.commands.clear()
    injection.commands.clear()

    def build_pressures() -> tuple[str, ...]:
        if control.state(PumpRole.JACKET).running:
            jacket.pressure = 120.0
        if control.state(PumpRole.INJECTION).running:
            injection.pressure = 100.0
        return ()

    control.start_measurement_pumps(
        jacket_target_pressure_bar=120.0,
        jacket_buildup_flow_ml_per_hour=60.0,
        injection_start_pressure_bar=100.0,
        injection_target_flow_ml_per_hour=10.0,
        confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
        startup_safety_check=build_pressures,
        margin_stability_seconds=0.0,
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
    assert injection.commands[-1] == "READ"
    assert not any(
        command == "REMOTE" or command == "RUN" or command.startswith("FLOW=")
        for command in injection.commands
    )
    assert not any(
        command in {"REMOTE", "RUN"} or command.startswith("FLOW=")
        for command in jacket.commands + injection.commands
    )


def test_stale_cached_pressure_is_reported_as_telemetry_failure() -> None:
    class StalePump(FakePump):
        def read_cached_status(self) -> tuple[PumpStatus, DataQuality]:
            return self.read_status(), DataQuality.STALE

    jacket = StalePump(120.0, [])
    injection = FakePump(100.0, [])
    control = PumpControlService(jacket_pump=jacket, injection_pump=injection)
    control.authorize(PumpControlService.AUTHORIZATION)
    control.connect(PumpRole.JACKET)
    control.connect(PumpRole.INJECTION)
    jacket.commands.clear()
    injection.commands.clear()

    with pytest.raises(RuntimeError, match="telemetry quality is stale"):
        control.prepare_measurement_pumps(
            PumpStartupPlan(120.0, 60.0, 100.0, 10.0),
            timing=PumpControlTiming(control_interval_seconds=0.001),
            confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
        )

    assert "REMOTE" not in jacket.commands + injection.commands


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

    # The pump was already stopped, so the duplicate STOP is intentionally skipped.
    assert injection.commands == ["FLOW=25.0"]
    assert not control.state(PumpRole.INJECTION).running


def test_measurement_start_programs_common_hardware_pressure_limit_on_both_pumps() -> None:
    control, jacket, injection = service()

    control.start_measurement_pumps(
        jacket_target_pressure_bar=120.0,
        jacket_buildup_flow_ml_per_hour=1000.0,
        injection_start_pressure_bar=100.0,
        injection_target_flow_ml_per_hour=1000.0,
        pressure_limit_bar=150.0,
        confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
    )

    assert "MAXPRESS=150.0" in jacket.commands
    assert "MAXPRESS=150.0" in injection.commands


def test_common_pressure_limit_apply_enters_remote_and_programs_both_pumps() -> None:
    control, jacket, injection = service()

    control.apply_common_pressure_limit(175.0)

    assert jacket.commands[-2:] == ["REMOTE", "MAXPRESS=175.0"]
    assert injection.commands[-2:] == ["REMOTE", "MAXPRESS=175.0"]


def test_injection_waits_for_margin_not_final_jacket_target() -> None:
    control, jacket, injection = service(
        jacket_pressure=100.0,
        injection_pressure=100.0,
    )
    jacket_at_injection_run: list[float] = []

    def build_both_pressures() -> tuple[str, ...]:
        if control.state(PumpRole.JACKET).running:
            jacket.pressure = min(140.0, jacket.pressure + 5.0)
        if control.state(PumpRole.INJECTION).running:
            if not jacket_at_injection_run:
                jacket_at_injection_run.append(jacket.pressure)
            injection.pressure = min(115.0, injection.pressure + 3.0)
        return ()

    control.start_measurement_pumps(
        jacket_target_pressure_bar=140.0,
        jacket_buildup_flow_ml_per_hour=60.0,
        injection_start_pressure_bar=115.0,
        injection_target_flow_ml_per_hour=60.0,
        confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
        startup_safety_check=build_both_pressures,
        margin_stability_seconds=0.0,
        control_interval_seconds=0.001,
    )

    assert jacket_at_injection_run
    assert 120.0 <= jacket_at_injection_run[0] < 140.0
    assert jacket.pressure == pytest.approx(140.0)
    assert injection.pressure == pytest.approx(115.0)
    assert injection.commands.count("RUN") == 1


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


def test_measurement_start_safety_failure_is_left_to_safe_state_owner() -> None:
    class SupervisedPump(FakePump):
        def __init__(self, pressure: float) -> None:
            super().__init__(pressure, [])
            self.supervision: list[bool] = []

        def set_remote_supervision_active(self, active: bool) -> None:
            self.supervision.append(active)

    jacket = SupervisedPump(120.0)
    injection = SupervisedPump(100.0)
    control = PumpControlService(jacket_pump=jacket, injection_pump=injection)
    control.authorize(PumpControlService.AUTHORIZATION)
    control.connect(PumpRole.JACKET)
    control.connect(PumpRole.INJECTION)
    jacket.commands.clear()
    injection.commands.clear()

    with pytest.raises(PermissionError, match="line pressure limit"):
        control.start_measurement_pumps(
            jacket_target_pressure_bar=120.0,
            jacket_buildup_flow_ml_per_hour=60.0,
            injection_start_pressure_bar=100.0,
            injection_target_flow_ml_per_hour=60.0,
            confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
            startup_safety_check=lambda: ("line pressure limit exceeded",),
        )

    assert jacket.commands == []
    assert injection.commands == []
    assert jacket.supervision == []
    assert injection.supervision == []


def test_measurement_start_rechecks_margin_immediately_before_injection_run() -> None:
    # The automatic preparation sequence owns this gate even when the service's
    # optional manual RUN margin policy is disabled.
    control, jacket, injection = service(
        injection_pressure=90.0,
        enforce_injection_margin=False,
    )

    def safety_check() -> tuple[str, ...]:
        if injection.commands and injection.commands[-1] == "FLOW=60.0":
            jacket.pressure = 109.0
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
    assert jacket.commands[-1] == "RUN"
    assert injection.commands[-1] == "FLOW=60.0"


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
        if control.state(PumpRole.JACKET).running:
            jacket.pressure = min(140.0, jacket.pressure + 5.0)
        if control.state(PumpRole.INJECTION).running:
            injection.pressure = min(110.0, injection.pressure + 2.0)
        return ()

    control.start_measurement_pumps(
        jacket_target_pressure_bar=140.0,
        jacket_buildup_flow_ml_per_hour=60.0,
        injection_start_pressure_bar=110.0,
        injection_target_flow_ml_per_hour=10.0,
        confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
        startup_safety_check=build_jacket_margin,
        margin_stability_seconds=0.0,
        control_interval_seconds=0.001,
    )

    assert all(
        "FLOW=10.0" not in commands and "RUN" not in commands
        for margin, commands in observed_injection_commands
        if margin < 20.0
    )
    assert injection.commands[:3] == ["REMOTE", "FLOW=10.0", "RUN"]


def test_margin_loss_stops_and_hysteresis_restarts_injection() -> None:
    control, jacket, injection = service(
        jacket_pressure=120.0,
        injection_pressure=100.0,
    )
    forced_loss = False
    restored = False

    def safety_check() -> tuple[str, ...]:
        nonlocal forced_loss, restored
        if injection.commands.count("RUN") == 1 and not forced_loss:
            injection.pressure = 105.0
            forced_loss = True
        elif forced_loss and injection.commands[-1:] == ["STOP"] and not restored:
            jacket.pressure = 126.0
            restored = True
        elif injection.commands.count("RUN") >= 2:
            jacket.pressure = 140.0
            injection.pressure = 115.0
        return ()

    control.start_measurement_pumps(
        jacket_target_pressure_bar=140.0,
        jacket_buildup_flow_ml_per_hour=60.0,
        injection_start_pressure_bar=115.0,
        injection_target_flow_ml_per_hour=60.0,
        confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
        startup_safety_check=safety_check,
        margin_stability_seconds=0.0,
        control_interval_seconds=0.001,
    )

    assert forced_loss and restored
    assert injection.commands.count("RUN") == 2
    assert injection.commands.count("STOP") == 2
    assert injection.commands.index("STOP") < len(injection.commands) - 2
    assert jacket.pressure - injection.pressure == pytest.approx(25.0)
    assert control.state(PumpRole.JACKET).mode is PumpOperatingMode.CONSTANT_PRESSURE
    assert control.state(PumpRole.JACKET).target == pytest.approx(140.0)


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


def test_preparation_plan_updates_the_minimum_jacket_margin() -> None:
    control, _, _ = service(
        jacket_pressure=112.0,
        injection_pressure=100.0,
    )

    control.prepare_measurement_pumps(
        PumpStartupPlan(
            112.0,
            60.0,
            100.0,
            10.0,
            minimum_jacket_margin_bar=12.0,
            margin_stability_seconds=0.0,
        ),
        timing=PumpControlTiming(control_interval_seconds=0.001),
        confirmation=PumpControlService.START_MEASUREMENT_CONFIRMATION,
    )

    assert control.minimum_jacket_margin_bar == pytest.approx(12.0)


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


def test_remote_supervision_is_enabled_only_for_connected_pumps_and_stops_safely(
) -> None:
    class SupervisedPump(FakePump):
        supervision: list[bool]

        def __init__(self, pressure: float) -> None:
            super().__init__(pressure, [])
            self.supervision = []

        def set_remote_supervision_active(self, active: bool) -> None:
            self.supervision.append(active)

    jacket = SupervisedPump(120.0)
    injection = SupervisedPump(100.0)
    control = PumpControlService(jacket_pump=jacket, injection_pump=injection)
    control.authorize(PumpControlService.AUTHORIZATION)
    control.connect(PumpRole.JACKET)

    control.set_remote_supervision_active(True)
    control.observe_safe_stop()

    assert jacket.supervision == [True, False]
    assert injection.supervision == [False]


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


def test_manual_connect_does_not_change_pump_mode() -> None:
    jacket = FakePump(120.0, [])
    control = PumpControlService(
        jacket_pump=jacket,
        injection_pump=FakePump(100.0, []),
    )
    control.authorize(PumpControlService.AUTHORIZATION)

    status = control.connect(PumpRole.JACKET)

    assert status.pressure_bar == 120.0
    assert jacket.commands == ["CONNECT"]
    assert control.connected(PumpRole.JACKET)
    assert not control.state(PumpRole.JACKET).remote


def test_disconnect_skips_stop_when_cached_status_is_already_stopped() -> None:
    class StoppedPump(FakePump):
        def is_stopped(self) -> bool:
            return True

    jacket = StoppedPump(120.0, [])
    control = PumpControlService(
        jacket_pump=jacket,
        injection_pump=FakePump(100.0, []),
    )
    control.authorize(PumpControlService.AUTHORIZATION)
    control.connect(PumpRole.JACKET)
    jacket.commands.clear()

    control.disconnect(PumpRole.JACKET)

    assert jacket.commands == ["DISCONNECT"]


def test_raw_multi_transaction_pump_is_rejected_from_control_path() -> None:
    class RawPump(FakePump):
        def read_pressure_bar(self) -> float:
            return self.pressure

        def read_operating_status(self) -> str:
            return "STOP REMOTE"

    jacket = RawPump(120.0, [])
    control = PumpControlService(
        jacket_pump=jacket,
        injection_pump=FakePump(100.0, []),
    )
    control.authorize(PumpControlService.AUTHORIZATION)

    with pytest.raises(RuntimeError, match="PollingPump required"):
        control.connect(PumpRole.JACKET)


class RemoteFailingPump(FakePump):
    def enter_remote(self) -> None:
        raise ConnectionError("REMOTE unavailable")


def test_first_control_operation_reports_remote_failure_after_connect() -> None:
    jacket = RemoteFailingPump(120.0, [])
    control = PumpControlService(
        jacket_pump=jacket,
        injection_pump=FakePump(100.0, []),
    )
    control.authorize(PumpControlService.AUTHORIZATION)

    control.connect(PumpRole.JACKET)
    with pytest.raises(ConnectionError, match="REMOTE unavailable"):
        control.configure(PumpRole.JACKET, PumpOperatingMode.CONSTANT_FLOW, 1.0)

    assert jacket.commands == ["CONNECT"]
    assert control.connected(PumpRole.JACKET)


def test_control_operation_restores_remote_mode_after_runtime_loss() -> None:
    class ModeTrackingPump(FakePump):
        remote_mode = False

        def is_remote_mode(self) -> bool:
            return self.remote_mode

        def enter_remote(self) -> None:
            super().enter_remote()
            self.remote_mode = True

    jacket = ModeTrackingPump(120.0, [])
    control = PumpControlService(
        jacket_pump=jacket,
        injection_pump=FakePump(100.0, []),
    )
    control.authorize(PumpControlService.AUTHORIZATION)
    control.connect(PumpRole.JACKET)
    control.configure(PumpRole.JACKET, PumpOperatingMode.CONSTANT_FLOW, 1.0)
    jacket.commands.clear()
    jacket.remote_mode = False

    control.run(PumpRole.JACKET, PumpControlService.RUN_JACKET_CONFIRMATION)

    assert jacket.commands == ["REMOTE", "RUN"]
    assert control.state(PumpRole.JACKET).running


def test_control_operation_recovers_local_mode_race_and_retries_once() -> None:
    class LocalRacePump(FakePump):
        flow_attempts = 0

        def is_remote_mode(self) -> bool:
            return True

        def set_constant_flow(self, target: float) -> None:
            self.flow_attempts += 1
            if self.flow_attempts == 1:
                raise RuntimeError("PROBLEM=LOCAL MODE")
            super().set_constant_flow(target)

    jacket = LocalRacePump(120.0, [])
    control = PumpControlService(
        jacket_pump=jacket,
        injection_pump=FakePump(100.0, []),
    )
    control.authorize(PumpControlService.AUTHORIZATION)
    control.connect(PumpRole.JACKET)
    jacket.commands.clear()

    control.configure(PumpRole.JACKET, PumpOperatingMode.CONSTANT_FLOW, 1.0)

    assert jacket.flow_attempts == 2
    assert jacket.commands == ["REMOTE", "FLOW=1.0"]


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


def test_shutdown_skips_stop_for_pumps_already_known_stopped() -> None:
    jacket = StopFailingPump(120.0, [])
    injection = FakePump(100.0, [])
    control = PumpControlService(jacket_pump=jacket, injection_pump=injection)
    control.authorize(PumpControlService.AUTHORIZATION)
    control.connect(PumpRole.JACKET)
    control.connect(PumpRole.INJECTION)
    jacket.commands.clear()
    injection.commands.clear()

    errors = control.shutdown_connections()

    assert errors == ()
    assert jacket.commands == ["DISCONNECT"]
    assert injection.commands == ["DISCONNECT"]
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
