from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock, Thread, get_ident
from time import monotonic, sleep

import pytest

from eor_control.diagnostics import DiagnosticCategory, DiagnosticLogger
from eor_control.domain import DataQuality
from eor_control.pump_commands import (
    PumpCommand,
    PumpCommandKind,
    PumpCommandPriority,
    PumpCommandStatus,
)
from eor_control.pump_telemetry import (
    PollingPump,
    PumpConnectionState,
    PumpPollingIntervals,
)


class MemoryDiagnosticLogger(DiagnosticLogger):
    def _append_file(self, event: object) -> None:
        pass


@dataclass
class SlowPollablePump:
    delay_seconds: float = 0.02
    fail_stop: bool = False
    calls: Counter[str] = field(default_factory=Counter)
    operating_status: str = "STOP REMOTE"

    def _read(self, name: str, value: float | str) -> float | str:
        self.calls[name] += 1
        sleep(self.delay_seconds)
        return value

    def connect(self) -> None:
        self.calls["connect"] += 1

    def read_pressure_bar(self) -> float:
        return float(self._read("pressure", 123.0))

    def read_flow_ml_per_hour(self) -> float:
        return float(self._read("flow", 12.0))

    def read_remaining_volume_ml(self) -> float:
        return float(self._read("volume", 240.0))

    def read_operating_status(self) -> str:
        return str(self._read("status", self.operating_status))

    def enter_remote(self) -> None:
        self.calls["remote"] += 1
        self.operating_status = "STOP REMOTE"

    def set_constant_flow(self, flow_ml_per_hour: float) -> None:
        self.calls["set_flow"] += 1

    def set_constant_pressure(self, pressure_bar: float) -> None:
        self.calls["set_pressure"] += 1

    def run(self) -> None:
        self.calls["run"] += 1
        self.operating_status = "RUN REMOTE"

    def request_stop(self) -> None:
        self.calls["stop"] += 1
        if self.fail_stop:
            raise ConnectionError("PROBLEM=LOCAL MODE")
        self.operating_status = "STOP REMOTE"

    def clear(self) -> None:
        self.calls["clear"] += 1

    def return_local(self) -> None:
        self.calls["local"] += 1

    def disconnect(self) -> None:
        self.calls["disconnect"] += 1


@dataclass
class SlowTelemetryFailurePump(SlowPollablePump):
    def read_flow_ml_per_hour(self) -> float:
        self.calls["flow"] += 1
        raise TimeoutError("FLOW timeout")

    def read_remaining_volume_ml(self) -> float:
        self.calls["volume"] += 1
        raise TimeoutError("VOLA timeout")


@dataclass
class BlockingSlowTelemetryPump(SlowPollablePump):
    slow_delay_seconds: float = 0.12

    def read_flow_ml_per_hour(self) -> float:
        self.calls["flow"] += 1
        sleep(self.slow_delay_seconds)
        return 12.0

    def read_remaining_volume_ml(self) -> float:
        self.calls["volume"] += 1
        sleep(self.slow_delay_seconds)
        return 240.0


@dataclass
class CommandPriorityPump(SlowPollablePump):
    flow_started: Event = field(default_factory=Event)
    release_flow: Event = field(default_factory=Event)
    operations: list[str] = field(default_factory=list)

    def read_flow_ml_per_hour(self) -> float:
        self.operations.append("FLOW_START")
        self.flow_started.set()
        assert self.release_flow.wait(timeout=1.0)
        self.operations.append("FLOW_END")
        return 12.0

    def read_remaining_volume_ml(self) -> float:
        self.operations.append("VOLA")
        return 240.0

    def enter_remote(self) -> None:
        self.operations.append("REMOTE")
        super().enter_remote()


@dataclass
class ToggleFieldFailurePump(SlowPollablePump):
    failed_field: str | None = None
    failed_fields: set[str] = field(default_factory=set)

    def _field(self, name: str, value: float) -> float:
        if self.failed_field == name or name in self.failed_fields:
            self.calls[name] += 1
            raise TimeoutError(f"{name.upper()} timeout")
        return float(self._read(name, value))

    def read_pressure_bar(self) -> float:
        return self._field("pressure", 123.0)

    def read_flow_ml_per_hour(self) -> float:
        return self._field("flow", 12.0)

    def read_remaining_volume_ml(self) -> float:
        return self._field("volume", 240.0)


def slow_intervals() -> PumpPollingIntervals:
    return PumpPollingIntervals(
        pressure_seconds=10.0,
        slow_telemetry_seconds=10.0,
        pressure_stale_seconds=30.0,
        slow_telemetry_stale_seconds=20.0,
        status_stale_seconds=20.0,
        startup_timeout_seconds=1.0,
    )


def test_default_pressure_stale_window_covers_observed_serial_jitter() -> None:
    intervals = PumpPollingIntervals()

    assert intervals.pressure_seconds == pytest.approx(1.0)
    assert intervals.slow_telemetry_seconds == pytest.approx(10.0)
    assert intervals.status_poll_seconds == pytest.approx(4.0)
    assert intervals.pressure_stale_seconds == pytest.approx(6.0)
    assert intervals.pressure_stale_seconds >= 3.0 * intervals.pressure_seconds
    assert intervals.slow_telemetry_stale_seconds == pytest.approx(33.0)
    assert intervals.status_stale_seconds == pytest.approx(8.0)
    assert intervals.startup_timeout_seconds == pytest.approx(8.0)


def test_control_reads_use_initialized_cache_without_serial_delay() -> None:
    raw = SlowPollablePump()
    pump = PollingPump(raw, name="test", intervals=slow_intervals())
    pump.connect()
    initial_calls = raw.calls.copy()

    started = monotonic()
    first, quality = pump.read_cached_status()
    second = pump.read_status()
    elapsed = monotonic() - started

    assert first == second
    assert first.pressure_bar == pytest.approx(123.0)
    assert quality is DataQuality.GOOD
    assert elapsed < raw.delay_seconds
    assert raw.calls == initial_calls
    pump.disconnect()


def test_connect_is_idempotent_and_does_not_repeat_identification_lifecycle() -> None:
    raw = SlowPollablePump(delay_seconds=0.0)
    pump = PollingPump(raw, name="test", intervals=slow_intervals())

    pump.connect()
    pump.connect()

    assert raw.calls["connect"] == 1
    pump.disconnect()


def test_slow_field_failure_does_not_make_pressure_stale_or_stop_worker() -> None:
    raw = SlowTelemetryFailurePump(delay_seconds=0.0)
    intervals = PumpPollingIntervals(
        pressure_seconds=0.02,
        slow_telemetry_seconds=0.03,
        pressure_stale_seconds=0.2,
        slow_telemetry_stale_seconds=0.2,
        startup_timeout_seconds=1.0,
    )
    pump = PollingPump(raw, name="test", intervals=intervals)
    pump.connect()
    sleep(0.09)

    status, control_quality = pump.read_cached_status()
    telemetry = pump.read_telemetry()

    assert status.pressure_bar == pytest.approx(123.0)
    assert control_quality is DataQuality.GOOD
    assert telemetry.pressure.quality is DataQuality.GOOD
    assert telemetry.flow.quality is DataQuality.STALE
    assert telemetry.flow.last_error == "FLOW timeout"
    assert telemetry.connection_state is PumpConnectionState.DEGRADED
    assert raw.calls["pressure"] >= 2
    pump.disconnect()


def test_pressure_gets_priority_after_a_blocking_slow_field() -> None:
    @dataclass
    class PressurePriorityProbe(BlockingSlowTelemetryPump):
        pressure_reads_at_volume_start: list[int] = field(default_factory=list)

        def read_remaining_volume_ml(self) -> float:
            self.pressure_reads_at_volume_start.append(self.calls["pressure"])
            return super().read_remaining_volume_ml()

    raw = PressurePriorityProbe(delay_seconds=0.0)
    intervals = PumpPollingIntervals(
        pressure_seconds=0.02,
        slow_telemetry_seconds=0.03,
        pressure_stale_seconds=0.2,
        slow_telemetry_stale_seconds=0.5,
        startup_timeout_seconds=1.0,
    )
    pump = PollingPump(raw, name="test", intervals=intervals)
    pump.connect()
    sleep(0.22)

    telemetry = pump.read_telemetry()

    # As soon as the slow transaction finishes, the overdue pressure read wins
    # over VOLA/STATUS and refreshes the safety-critical value.
    assert telemetry.pressure.quality is DataQuality.GOOD
    assert raw.calls["pressure"] >= 2
    assert raw.calls["flow"] == 1
    assert len(raw.pressure_reads_at_volume_start) == 1
    assert raw.pressure_reads_at_volume_start[0] >= 2
    pump.disconnect()


def test_polling_deadline_uses_fixed_grid_and_skips_missed_fake_clock_slots() -> None:
    assert PollingPump._next_polling_deadline(10.0, 10.2, 0.5) == pytest.approx(10.5)
    assert PollingPump._next_polling_deadline(10.0, 11.2, 0.5) == pytest.approx(11.5)
    assert PollingPump._next_polling_deadline(10.0, 15.0, 0.5) == pytest.approx(15.5)


def test_slow_telemetry_is_round_robin_not_three_independent_periods() -> None:
    @dataclass
    class TimedSlowPump(SlowPollablePump):
        slow_events: list[tuple[str, float]] = field(default_factory=list)

        def read_flow_ml_per_hour(self) -> float:
            self.slow_events.append(("FLOW", monotonic()))
            return super().read_flow_ml_per_hour()

        def read_remaining_volume_ml(self) -> float:
            self.slow_events.append(("VOLA", monotonic()))
            return super().read_remaining_volume_ml()

        def read_operating_status(self) -> str:
            self.slow_events.append(("STATUS", monotonic()))
            return super().read_operating_status()

    raw = TimedSlowPump(delay_seconds=0.01)
    intervals = PumpPollingIntervals(
        pressure_seconds=0.02,
        slow_telemetry_seconds=0.04,
        pressure_stale_seconds=0.5,
        slow_telemetry_stale_seconds=0.5,
        startup_timeout_seconds=1.0,
    )
    pump = PollingPump(raw, name="test", intervals=intervals)
    pump.connect()
    deadline = monotonic() + 1.0
    while len(raw.slow_events) < 4 and monotonic() < deadline:
        sleep(0.01)

    # The first STATUS belongs to connection initialization. Runtime STATUS has
    # its own slower cadence; FLOW and VOLA remain a two-field round robin.
    runtime_events = raw.slow_events[1:4]
    assert [name for name, _ in runtime_events] == ["FLOW", "VOLA", "FLOW"]
    assert runtime_events[2][1] - runtime_events[0][1] >= (intervals.slow_telemetry_seconds)
    pump.disconnect()


def test_failed_stop_is_latched_until_acknowledgement() -> None:
    raw = SlowPollablePump(delay_seconds=0.0, fail_stop=True)
    pump = PollingPump(raw, name="test", intervals=slow_intervals())
    pump.connect()

    with pytest.raises(ConnectionError, match="LOCAL MODE"):
        pump.request_stop()
    pump.request_stop()
    assert raw.calls["stop"] == 1

    pump.acknowledge_stop_latch()
    with pytest.raises(ConnectionError, match="LOCAL MODE"):
        pump.request_stop()
    assert raw.calls["stop"] == 2
    pump.disconnect()


def test_concurrent_safe_state_stop_submissions_share_one_command() -> None:
    raw = SlowPollablePump(delay_seconds=0.01)
    pump = PollingPump(raw, name="test", intervals=slow_intervals())
    pump.connect()

    first = pump.submit_stop(emergency=True)
    second = pump.submit_stop(emergency=True)
    deadline = monotonic() + 1.0
    result = pump.command_result(first)
    while not result.status.terminal and monotonic() < deadline:
        sleep(0.005)
        result = pump.command_result(first)

    assert first == second
    assert result.status is PumpCommandStatus.SUCCEEDED
    assert raw.calls["stop"] == 1
    pump.disconnect()


def test_control_commands_trigger_intermediate_pressure_cache_refreshes() -> None:
    raw = SlowPollablePump(delay_seconds=0.0)
    pump = PollingPump(raw, name="test", intervals=slow_intervals())
    pump.connect()
    pressure_reads_after_connect = raw.calls["pressure"]

    pump.enter_remote()
    pump.set_constant_flow(10.0)
    pump.run()

    deadline = monotonic() + 1.0
    while raw.calls["pressure"] < pressure_reads_after_connect + 3:
        assert monotonic() < deadline
        sleep(0.01)
    assert raw.calls["pressure"] == pressure_reads_after_connect + 3
    assert pump.read_telemetry().pressure.quality is DataQuality.GOOD
    pump.disconnect()


def test_queued_control_command_precedes_next_polling_transaction() -> None:
    raw = CommandPriorityPump(delay_seconds=0.0)
    intervals = PumpPollingIntervals(
        pressure_seconds=1.0,
        slow_telemetry_seconds=0.3,
        pressure_stale_seconds=3.0,
        slow_telemetry_stale_seconds=1.0,
        startup_timeout_seconds=1.0,
    )
    pump = PollingPump(raw, name="test", intervals=intervals)
    pump.connect()
    assert raw.flow_started.wait(timeout=1.0)
    command = Thread(target=pump.enter_remote)
    command.start()
    sleep(0.02)

    raw.release_flow.set()
    command.join(timeout=1.0)
    assert not command.is_alive()
    deadline = monotonic() + 2.0
    while "VOLA" not in raw.operations and monotonic() < deadline:
        sleep(0.01)

    assert raw.operations.index("FLOW_END") < raw.operations.index("REMOTE")
    assert raw.operations.index("REMOTE") < raw.operations.index("VOLA")
    pump.disconnect()


def test_all_control_commands_run_before_next_pressure_refresh() -> None:
    @dataclass
    class InterCommandPressurePump(SlowPollablePump):
        operations: list[str] = field(default_factory=list)

        def read_pressure_bar(self) -> float:
            self.operations.append("PRESS")
            return super().read_pressure_bar()

        def set_constant_flow(self, flow_ml_per_hour: float) -> None:
            self.operations.append("CONFIG_FLOW")
            super().set_constant_flow(flow_ml_per_hour)

        def set_constant_pressure(self, pressure_bar: float) -> None:
            self.operations.append("CONFIG_PRESSURE")
            super().set_constant_pressure(pressure_bar)

    raw = InterCommandPressurePump(delay_seconds=0.0)
    pump = PollingPump(raw, name="test", intervals=slow_intervals())
    pump.connect()
    raw.operations.clear()
    first = pump.submit_command(
        PumpCommand(
            PumpCommandKind.SET_CONSTANT_FLOW,
            PumpCommandPriority.NORMAL,
            value=10.0,
        )
    )
    second = pump.submit_command(
        PumpCommand(
            PumpCommandKind.SET_CONSTANT_PRESSURE,
            PumpCommandPriority.NORMAL,
            value=120.0,
        )
    )
    deadline = monotonic() + 2.0
    first_result = pump.command_result(first)
    second_result = pump.command_result(second)
    while not (first_result.status.terminal and second_result.status.terminal):
        if monotonic() >= deadline:
            break
        sleep(0.01)
        first_result = pump.command_result(first)
        second_result = pump.command_result(second)

    pump.disconnect()
    assert first_result.status is PumpCommandStatus.SUCCEEDED, raw.operations
    assert second_result.status is PumpCommandStatus.SUCCEEDED, raw.operations
    assert raw.operations.index("CONFIG_FLOW") < raw.operations.index("CONFIG_PRESSURE")
    assert raw.operations.index("CONFIG_PRESSURE") < raw.operations.index("PRESS")


def test_stop_precedes_overdue_pressure_and_normal_command() -> None:
    @dataclass
    class StopPriorityPump(CommandPriorityPump):
        def read_pressure_bar(self) -> float:
            self.operations.append("PRESS")
            return super().read_pressure_bar()

        def set_constant_flow(self, flow_ml_per_hour: float) -> None:
            self.operations.append("CONFIG")
            super().set_constant_flow(flow_ml_per_hour)

        def request_stop(self) -> None:
            self.operations.append("STOP")
            super().request_stop()

    raw = StopPriorityPump(delay_seconds=0.0)
    intervals = PumpPollingIntervals(
        pressure_seconds=0.02,
        slow_telemetry_seconds=0.3,
        pressure_stale_seconds=1.0,
        slow_telemetry_stale_seconds=1.0,
        startup_timeout_seconds=1.0,
    )
    pump = PollingPump(raw, name="test", intervals=intervals)
    pump.connect()
    assert raw.flow_started.wait(timeout=1.0)
    raw.operations.clear()
    normal = pump.submit_command(
        PumpCommand(
            PumpCommandKind.SET_CONSTANT_FLOW,
            PumpCommandPriority.NORMAL,
            value=10.0,
        )
    )
    stop = pump.submit_stop()
    sleep(0.03)
    raw.release_flow.set()
    deadline = monotonic() + 1.0
    while not (
        pump.command_result(normal).status.terminal and pump.command_result(stop).status.terminal
    ):
        assert monotonic() < deadline
        sleep(0.01)

    assert raw.operations.index("STOP") < raw.operations.index("PRESS")
    assert raw.operations.index("STOP") < raw.operations.index("CONFIG")
    assert raw.operations.index("CONFIG") < raw.operations.index("PRESS")
    pump.disconnect()


def test_command_timeout_is_not_reported_as_control_cycle_deadline() -> None:
    class DelayedStopPump(SlowPollablePump):
        def request_stop(self) -> None:
            sleep(0.12)
            super().request_stop()

    raw = DelayedStopPump(delay_seconds=0.0)
    pump = PollingPump(raw, name="injection", intervals=slow_intervals())
    pump.connect()
    command_id = pump.submit_command(
        PumpCommand(
            PumpCommandKind.STOP,
            PumpCommandPriority.HIGH,
            execution_timeout_seconds=0.05,
            verify_status=True,
        )
    )

    deadline = monotonic() + 1.0
    while True:
        result = pump.command_result(command_id)
        if result.status.terminal:
            break
        assert monotonic() < deadline
        sleep(0.01)

    assert result.status is PumpCommandStatus.TIMED_OUT
    assert result.error is not None
    assert "command execution timeout" in result.error
    assert "control cycle" not in result.error
    pump.disconnect()


def test_stop_fails_when_status_does_not_confirm_stop() -> None:
    class UnconfirmedStopPump(SlowPollablePump):
        def request_stop(self) -> None:
            self.calls["stop"] += 1

    raw = UnconfirmedStopPump(delay_seconds=0.0)
    pump = PollingPump(raw, name="injection", intervals=slow_intervals())
    pump.connect()
    pump.run()

    with pytest.raises(RuntimeError, match="did not confirm STOP"):
        pump.request_stop()

    assert raw.calls["stop"] == 1
    pump.disconnect()


def test_remote_command_fails_when_pump_remains_in_local_mode() -> None:
    class LocalModePump(SlowPollablePump):
        operating_status = "STOP LOCAL"

        def enter_remote(self) -> None:
            self.calls["remote"] += 1

    raw = LocalModePump(delay_seconds=0.0, operating_status="STOP LOCAL")
    pump = PollingPump(raw, name="injection", intervals=slow_intervals())
    pump.connect()

    assert pump.read_cached_status()[1] is DataQuality.GOOD
    assert pump.read_telemetry().operating_status.quality is DataQuality.GOOD
    assert pump.is_stopped_local()
    assert not pump.is_remote_mode()
    with pytest.raises(RuntimeError, match="did not confirm REMOTE"):
        pump.enter_remote()

    assert raw.calls["remote"] == 1
    pump.disconnect()


def test_remote_command_rejects_status_without_explicit_remote_confirmation() -> None:
    class StandardStatusPump(SlowPollablePump):
        def enter_remote(self) -> None:
            self.calls["remote"] += 1
            self.operating_status = "STATUS=STOP"

    raw = StandardStatusPump(delay_seconds=0.0, operating_status="STATUS=STOP")
    pump = PollingPump(raw, name="injection", intervals=slow_intervals())
    pump.connect()

    with pytest.raises(RuntimeError, match="did not confirm REMOTE"):
        pump.enter_remote()

    assert raw.calls["remote"] == 1
    assert not pump.is_remote_mode()
    assert pump.read_telemetry().operating_status.quality is DataQuality.GOOD
    pump.disconnect()


@pytest.mark.parametrize("status_text", ["STOP REMOTE", "RUN REMOTE"])
def test_explicit_remote_operating_status_is_recognized(status_text: str) -> None:
    raw = SlowPollablePump(delay_seconds=0.0, operating_status=status_text)
    pump = PollingPump(raw, name="test", intervals=slow_intervals())
    pump.connect()

    assert pump.is_remote_mode()
    assert not pump.is_stopped_local()
    pump.disconnect()


def test_periodic_status_restores_remote_only_when_control_supervision_is_active() -> None:
    raw = SlowPollablePump(delay_seconds=0.0, operating_status="STOP REMOTE")
    intervals = PumpPollingIntervals(
        pressure_seconds=0.01,
        slow_telemetry_seconds=0.01,
        status_poll_seconds=0.01,
        pressure_stale_seconds=1.0,
        slow_telemetry_stale_seconds=1.0,
        status_stale_seconds=1.0,
        startup_timeout_seconds=1.0,
    )
    pump = PollingPump(raw, name="test", intervals=intervals)
    pump.connect()
    initial_status_reads = raw.calls["status"]
    raw.operating_status = "RUN LOCAL"

    deadline = monotonic() + 1.0
    while raw.calls["status"] == initial_status_reads and monotonic() < deadline:
        sleep(0.005)

    assert raw.calls["status"] > initial_status_reads
    assert raw.calls["remote"] == 0

    pump.set_remote_supervision_active(True)
    reads_when_enabled = raw.calls["status"]
    deadline = monotonic() + 1.0
    while raw.calls["remote"] == 0 and monotonic() < deadline:
        sleep(0.005)

    assert raw.calls["remote"] == 1
    assert raw.calls["status"] >= reads_when_enabled + 3
    assert raw.operating_status == "STOP REMOTE"
    pump.disconnect()


def test_two_transient_local_samples_do_not_trigger_remote_recovery() -> None:
    raw = SlowPollablePump(delay_seconds=0.0, operating_status="STOP REMOTE")
    intervals = PumpPollingIntervals(
        pressure_seconds=0.01,
        slow_telemetry_seconds=0.01,
        status_poll_seconds=0.05,
        pressure_stale_seconds=1.0,
        slow_telemetry_stale_seconds=1.0,
        status_stale_seconds=1.0,
        startup_timeout_seconds=1.0,
    )
    pump = PollingPump(raw, name="test", intervals=intervals)
    pump.connect()
    pump.set_remote_supervision_active(True)
    baseline = raw.calls["status"]
    raw.operating_status = "RUN LOCAL"
    deadline = monotonic() + 1.0
    while raw.calls["status"] < baseline + 2 and monotonic() < deadline:
        sleep(0.005)
    raw.operating_status = "RUN REMOTE"
    sleep(0.08)

    assert raw.calls["remote"] == 0
    pump.disconnect()


def test_recovery_command_log_contains_queue_timing_priority_and_reason(
    tmp_path: Path,
) -> None:
    raw = SlowPollablePump(delay_seconds=0.0, operating_status="RUN LOCAL")
    logger = MemoryDiagnosticLogger(tmp_path / "remote-recovery.html")
    logger.configure(enabled=True, categories=DiagnosticCategory)
    intervals = PumpPollingIntervals(
        pressure_seconds=0.01,
        slow_telemetry_seconds=0.01,
        status_poll_seconds=0.01,
        pressure_stale_seconds=1.0,
        slow_telemetry_stale_seconds=1.0,
        status_stale_seconds=1.0,
        startup_timeout_seconds=1.0,
    )
    pump = PollingPump(
        raw,
        name="injection",
        intervals=intervals,
        diagnostics=logger,
        diagnostic_category=DiagnosticCategory.INJECTION_PUMP,
    )
    pump.connect()
    pump.set_remote_supervision_active(True)
    deadline = monotonic() + 1.0
    command_event = None
    while command_event is None and monotonic() < deadline:
        command_event = next(
            (
                event
                for event in logger.events_after(0)
                if event.event_id == "PUMP_COMMAND"
                and dict(event.fields).get("result") == "SUCCEEDED"
            ),
            None,
        )
        sleep(0.005)

    assert command_event is not None
    fields = dict(command_event.fields)
    assert fields["command"] == "REMOTE"
    assert fields["priority"] == str(int(PumpCommandPriority.HIGH))
    assert fields["queue_wait_ms"] != "NONE"
    assert fields["execution_ms"] != "NONE"
    assert fields["com_port"] == "—"
    assert fields["worker_name"] == "eor-injection-pump-poll"
    assert fields["thread_id"] != "NONE"
    assert fields["queued_monotonic"] != "NONE"
    assert fields["queue_size"] == "0"
    assert "3 consecutive LOCAL" in fields["recovery_reason"]
    pump.disconnect()


def test_immediately_repeated_successful_configuration_is_not_resent() -> None:
    raw = SlowPollablePump(delay_seconds=0.0)
    pump = PollingPump(raw, name="test", intervals=slow_intervals())
    pump.connect()

    pump.set_constant_flow(42.0)
    pump.set_constant_flow(42.0)

    assert raw.calls["set_flow"] == 1
    pump.disconnect()


def test_local_status_invalidates_previous_remote_deduplication() -> None:
    raw = SlowPollablePump(delay_seconds=0.0)
    intervals = PumpPollingIntervals(
        pressure_seconds=0.02,
        slow_telemetry_seconds=10.0,
        status_poll_seconds=0.02,
        pressure_stale_seconds=1.0,
        slow_telemetry_stale_seconds=20.0,
        status_stale_seconds=1.0,
        startup_timeout_seconds=1.0,
    )
    pump = PollingPump(raw, name="test", intervals=intervals)
    pump.connect()
    pump.enter_remote()
    assert raw.calls["remote"] == 1

    raw.operating_status = "STOP LOCAL"
    deadline = monotonic() + 1.0
    while pump.is_remote_mode():
        assert monotonic() < deadline
        sleep(0.005)
    pump.enter_remote()

    assert raw.calls["remote"] == 2
    pump.disconnect()


def test_targeted_command_verification_postpones_periodic_status_poll() -> None:
    raw = SlowPollablePump(delay_seconds=0.0)
    intervals = PumpPollingIntervals(
        pressure_seconds=0.02,
        slow_telemetry_seconds=0.02,
        status_poll_seconds=0.2,
        pressure_stale_seconds=1.0,
        slow_telemetry_stale_seconds=1.0,
        status_stale_seconds=1.0,
        startup_timeout_seconds=1.0,
    )
    pump = PollingPump(raw, name="test", intervals=intervals)
    pump.connect()
    pump.enter_remote()
    reads_after_verification = raw.calls["status"]

    sleep(0.1)

    assert raw.calls["status"] == reads_after_verification
    pump.disconnect()


@pytest.mark.parametrize("status_text", ["STOP LOCAL", "RUN LOCAL"])
def test_local_operating_status_is_valid_telemetry(status_text: str) -> None:
    raw = SlowPollablePump(delay_seconds=0.0, operating_status=status_text)
    pump = PollingPump(raw, name="test", intervals=slow_intervals())
    pump.connect()

    telemetry = pump.read_telemetry()

    assert telemetry.operating_status.quality is DataQuality.GOOD
    assert telemetry.operating_status.last_error is None
    assert pump.read_cached_status()[1] is DataQuality.GOOD
    assert pump.is_stopped_local() is status_text.startswith("STOP")
    pump.disconnect()


def test_queued_command_timeout_prevents_command_from_running_later() -> None:
    class ShortQueueWaitPump(PollingPump):
        COMMAND_QUEUE_WAIT_SECONDS = 0.05

    raw = CommandPriorityPump(delay_seconds=0.0)
    pump = ShortQueueWaitPump(raw, name="test", intervals=slow_intervals())
    pump.connect()
    assert raw.flow_started.wait(timeout=1.0)

    with pytest.raises(TimeoutError, match="queue timeout"):
        pump.set_constant_flow(10.0)

    raw.release_flow.set()
    sleep(0.1)
    assert raw.calls["set_flow"] == 0
    pump.disconnect()


def test_worker_discards_expired_queue_item_without_result_polling() -> None:
    raw = CommandPriorityPump(delay_seconds=0.0)
    pump = PollingPump(raw, name="test", intervals=slow_intervals())
    pump.connect()
    assert raw.flow_started.wait(timeout=1.0)
    command_id = pump.submit_command(
        PumpCommand(
            PumpCommandKind.SET_CONSTANT_FLOW,
            PumpCommandPriority.HIGH,
            value=10.0,
            queue_timeout_seconds=0.05,
        )
    )

    sleep(0.1)
    raw.release_flow.set()
    deadline = monotonic() + 1.0
    while not pump._command_results[command_id].status.terminal:
        assert monotonic() < deadline
        sleep(0.01)

    assert pump.command_result(command_id).status is PumpCommandStatus.TIMED_OUT
    assert raw.calls["set_flow"] == 0
    pump.disconnect()


def test_preparation_suspends_flow_and_volume_polling_until_released() -> None:
    raw = SlowPollablePump(delay_seconds=0.0)
    intervals = PumpPollingIntervals(
        pressure_seconds=0.02,
        slow_telemetry_seconds=0.02,
        status_poll_seconds=0.02,
        pressure_stale_seconds=1.0,
        slow_telemetry_stale_seconds=1.0,
        startup_timeout_seconds=1.0,
    )
    pump = PollingPump(raw, name="test", intervals=intervals)
    pump.set_preparation_active(True)
    pump.connect()
    sleep(0.1)

    assert raw.calls["pressure"] > 1
    assert raw.calls["status"] > 1
    assert raw.calls["flow"] == 0
    assert raw.calls["volume"] == 0

    pump.set_preparation_active(False)
    deadline = monotonic() + 1.0
    while raw.calls["flow"] == 0:
        assert monotonic() < deadline
        sleep(0.01)
    pump.disconnect()


def test_preparation_suspension_measurably_reduces_pressure_age() -> None:
    raw = BlockingSlowTelemetryPump(delay_seconds=0.0, slow_delay_seconds=0.12)
    intervals = PumpPollingIntervals(
        pressure_seconds=0.02,
        slow_telemetry_seconds=0.01,
        status_poll_seconds=10.0,
        pressure_stale_seconds=1.0,
        slow_telemetry_stale_seconds=1.0,
        status_stale_seconds=20.0,
        startup_timeout_seconds=1.0,
    )
    pump = PollingPump(raw, name="test", intervals=intervals)
    pump.connect()
    loaded_max_age = 0.0
    loaded_deadline = monotonic() + 0.35
    while monotonic() < loaded_deadline:
        age = pump.worker_snapshot().pressure_age_seconds
        loaded_max_age = max(loaded_max_age, age or 0.0)
        sleep(0.005)

    pump.set_preparation_active(True)
    settle_deadline = monotonic() + 1.0
    while True:
        prepared_age = pump.worker_snapshot().pressure_age_seconds
        if prepared_age is not None and prepared_age < 0.04:
            break
        assert monotonic() < settle_deadline
        sleep(0.005)
    prepared_max_age = 0.0
    prepared_deadline = monotonic() + 0.15
    while monotonic() < prepared_deadline:
        age = pump.worker_snapshot().pressure_age_seconds
        prepared_max_age = max(prepared_max_age, age or 0.0)
        sleep(0.005)

    assert loaded_max_age > 0.08
    assert prepared_max_age < 0.05
    pump.disconnect()


def test_verification_timeout_is_separate_from_execution_timeout() -> None:
    class SlowVerificationPump(SlowPollablePump):
        delay_verification = False

        def request_stop(self) -> None:
            super().request_stop()
            self.delay_verification = True

        def read_operating_status(self) -> str:
            if self.delay_verification:
                sleep(0.12)
            return super().read_operating_status()

    raw = SlowVerificationPump(delay_seconds=0.0)
    pump = PollingPump(raw, name="test", intervals=slow_intervals())
    pump.connect()
    command_id = pump.submit_command(
        PumpCommand(
            PumpCommandKind.STOP,
            PumpCommandPriority.HIGH,
            execution_timeout_seconds=0.5,
            verify_status=True,
            verification_timeout_seconds=0.05,
        )
    )

    deadline = monotonic() + 1.0
    while True:
        result = pump.command_result(command_id)
        if result.status.terminal:
            break
        assert monotonic() < deadline
        sleep(0.01)

    assert result.status is PumpCommandStatus.TIMED_OUT
    assert result.error is not None
    assert "verification timeout" in result.error
    assert result.execution_seconds is not None
    assert result.execution_seconds < 0.05
    pump.disconnect()


def test_command_submission_requires_a_running_worker() -> None:
    pump = PollingPump(
        SlowPollablePump(delay_seconds=0.0),
        name="injection",
        intervals=slow_intervals(),
    )

    with pytest.raises(ConnectionError, match="worker is not running"):
        pump.submit_stop(emergency=True)


def test_connect_transactions_and_single_close_stay_on_the_owned_worker() -> None:
    caller_ident = get_ident()

    @dataclass
    class ThreadAffinityPump(SlowPollablePump):
        transaction_threads: list[tuple[str, int]] = field(default_factory=list)

        def connect(self) -> None:
            self.transaction_threads.append(("connect", get_ident()))
            super().connect()

        def _read(self, name: str, value: float | str) -> float | str:
            self.transaction_threads.append((name, get_ident()))
            return super()._read(name, value)

        def disconnect(self) -> None:
            self.transaction_threads.append(("disconnect", get_ident()))
            super().disconnect()

    raw = ThreadAffinityPump(delay_seconds=0.0)
    pump = PollingPump(raw, name="jacket", intervals=slow_intervals())
    pump.connect()
    pump.connect()
    worker_ident = pump.worker_snapshot().worker_ident
    pump.disconnect()

    transaction_idents = {thread_id for _, thread_id in raw.transaction_threads}
    assert transaction_idents == {worker_ident}
    assert caller_ident not in transaction_idents
    assert raw.calls["connect"] == 1
    assert raw.calls["disconnect"] == 1


def test_bounded_command_queue_admits_emergency_stop_by_preemption() -> None:
    raw = CommandPriorityPump(delay_seconds=0.0)
    pump = PollingPump(
        raw,
        name="injection",
        intervals=slow_intervals(),
        command_queue_capacity=4,
    )
    pump.connect()
    assert raw.flow_started.wait(timeout=1.0)
    normal_ids = [
        pump.submit_command(PumpCommand(kind, PumpCommandPriority.NORMAL, value))
        for kind, value in (
            (PumpCommandKind.CLEAR, None),
            (PumpCommandKind.RETURN_LOCAL, None),
            (PumpCommandKind.RUN, None),
            (PumpCommandKind.SET_CONSTANT_PRESSURE, 5.0),
        )
    ]

    stop_id = pump.submit_stop(emergency=True)
    assert pump.worker_snapshot().queue_size == 4
    assert len(pump._command_queue) == 4
    assert sum(
        pump.command_result(command_id).status is PumpCommandStatus.CANCELLED
        for command_id in normal_ids
    ) == 1
    raw.release_flow.set()
    deadline = monotonic() + 1.0
    while not pump.command_result(stop_id).status.terminal:
        assert monotonic() < deadline
        sleep(0.01)
    assert pump.command_result(stop_id).status is PumpCommandStatus.SUCCEEDED
    pump.disconnect()


def test_one_pump_worker_progresses_while_other_pump_is_blocked() -> None:
    blocked_raw = CommandPriorityPump(delay_seconds=0.0)
    blocked = PollingPump(blocked_raw, name="injection", intervals=slow_intervals())
    responsive_raw = SlowPollablePump(delay_seconds=0.0)
    responsive = PollingPump(
        responsive_raw,
        name="jacket",
        intervals=slow_intervals(),
    )
    blocked.connect()
    responsive.connect()
    assert blocked_raw.flow_started.wait(timeout=1.0)

    responsive.enter_remote()

    assert responsive_raw.calls["remote"] == 1
    blocked_raw.release_flow.set()
    blocked.disconnect()
    responsive.disconnect()


def test_two_pumps_own_distinct_workers_queues_and_serial_transactions_overlap() -> None:
    records: list[tuple[str, int, float, float]] = []
    records_lock = Lock()

    @dataclass
    class TransactionProbe(SlowPollablePump):
        role: str = "pump"
        active_transactions: int = 0
        maximum_active_transactions: int = 0
        transaction_lock: Lock = field(default_factory=Lock)

        def _read(self, name: str, value: float | str) -> float | str:
            started = monotonic()
            with self.transaction_lock:
                self.active_transactions += 1
                self.maximum_active_transactions = max(
                    self.maximum_active_transactions,
                    self.active_transactions,
                )
            try:
                result = super()._read(name, value)
            finally:
                completed = monotonic()
                with self.transaction_lock:
                    self.active_transactions -= 1
                with records_lock:
                    records.append((self.role, get_ident(), started, completed))
            return result

    intervals = PumpPollingIntervals(
        pressure_seconds=0.02,
        slow_telemetry_seconds=10.0,
        status_poll_seconds=10.0,
        pressure_stale_seconds=1.0,
        slow_telemetry_stale_seconds=20.0,
        status_stale_seconds=20.0,
        startup_timeout_seconds=1.0,
    )
    jacket_raw = TransactionProbe(delay_seconds=0.04, role="jacket")
    injection_raw = TransactionProbe(delay_seconds=0.04, role="injection")
    jacket = PollingPump(jacket_raw, name="jacket", serial_port="COM1", intervals=intervals)
    injection = PollingPump(
        injection_raw,
        name="injection",
        serial_port="COM2",
        intervals=intervals,
    )
    jacket.connect()
    injection.connect()
    sleep(0.25)

    jacket_snapshot = jacket.worker_snapshot()
    injection_snapshot = injection.worker_snapshot()
    assert jacket_snapshot.worker_ident != injection_snapshot.worker_ident
    assert jacket_snapshot.serial_port == "COM1"
    assert injection_snapshot.serial_port == "COM2"
    assert jacket._command_queue is not injection._command_queue
    assert jacket._condition is not injection._condition
    assert jacket_raw.maximum_active_transactions == 1
    assert injection_raw.maximum_active_transactions == 1
    worker_ids = {jacket_snapshot.worker_ident, injection_snapshot.worker_ident}
    worker_records = [record for record in records if record[1] in worker_ids]
    assert any(
        left[0] != right[0] and left[2] < right[3] and right[2] < left[3]
        for left in worker_records
        for right in worker_records
    )
    jacket.disconnect()
    injection.disconnect()


def test_slow_pump_does_not_age_other_pumps_pressure_cache() -> None:
    intervals = PumpPollingIntervals(
        pressure_seconds=0.02,
        slow_telemetry_seconds=10.0,
        status_poll_seconds=10.0,
        pressure_stale_seconds=1.0,
        slow_telemetry_stale_seconds=20.0,
        status_stale_seconds=20.0,
        startup_timeout_seconds=1.0,
    )
    slow_raw = SlowPollablePump(delay_seconds=0.15)
    fast_raw = SlowPollablePump(delay_seconds=0.002)
    slow = PollingPump(slow_raw, name="jacket", intervals=intervals)
    fast = PollingPump(fast_raw, name="injection", intervals=intervals)
    slow.connect()
    fast.connect()
    baseline = fast_raw.calls["pressure"]
    command_id = fast.submit_command(
        PumpCommand(
            PumpCommandKind.SET_CONSTANT_FLOW,
            PumpCommandPriority.HIGH,
            value=7.0,
        )
    )
    sleep(0.2)

    # The exact count is scheduler-dependent on Windows; four independent
    # refreshes within the slow pump's single 150 ms transaction prove progress.
    assert fast_raw.calls["pressure"] >= baseline + 4
    age = fast.worker_snapshot().pressure_age_seconds
    assert age is not None and age < 0.08
    command_result = fast.command_result(command_id)
    assert command_result.status is PumpCommandStatus.SUCCEEDED
    assert command_result.queue_wait_seconds is not None
    assert command_result.queue_wait_seconds < 0.05
    slow.disconnect()
    fast.disconnect()


@pytest.mark.parametrize("failed_field", ["pressure", "flow", "volume"])
def test_each_field_logs_single_stale_transition_and_recovery(
    tmp_path: Path, failed_field: str
) -> None:
    raw = ToggleFieldFailurePump(delay_seconds=0.0)
    logger = MemoryDiagnosticLogger(tmp_path / f"{failed_field}.html")
    logger.configure(enabled=True, categories=DiagnosticCategory)
    intervals = PumpPollingIntervals(
        pressure_seconds=0.02,
        slow_telemetry_seconds=0.03,
        pressure_stale_seconds=0.06,
        slow_telemetry_stale_seconds=0.05,
        startup_timeout_seconds=1.0,
    )
    pump = PollingPump(
        raw,
        name="jacket",
        intervals=intervals,
        diagnostics=logger,
        diagnostic_category=DiagnosticCategory.JACKET_PUMP,
    )
    pump.connect()
    initialization_deadline = monotonic() + 1.0
    while True:
        initialized = pump.read_telemetry()
        if all(
            state.last_update_monotonic is not None
            for state in (initialized.pressure, initialized.flow, initialized.volume)
        ):
            break
        assert monotonic() < initialization_deadline
        sleep(0.01)
    existing_events = logger.events_after(0)
    baseline_sequence = existing_events[-1].sequence if existing_events else 0
    raw.failed_field = failed_field
    sleep(0.09)

    first = pump.read_telemetry()
    pump.read_telemetry()

    failed_state = {
        "pressure": first.pressure,
        "flow": first.flow,
        "volume": first.volume,
    }[failed_field]
    assert failed_state.quality is DataQuality.STALE
    stale_events = [
        event
        for event in logger.events_after(baseline_sequence)
        if event.event_id == "TELEMETRY_QUALITY_CHANGED"
        and dict(event.fields)["field"] == failed_field
        and dict(event.fields)["new_quality"] == DataQuality.STALE.value
    ]
    assert len(stale_events) == 1
    expected_stale_ms = 60.0 if failed_field == "pressure" else 50.0
    assert float(dict(stale_events[0].fields)["age_ms"]) >= expected_stale_ms
    assert dict(stale_events[0].fields)["stale_limit_ms"] == str(expected_stale_ms)

    raw.failed_field = None
    recovery_deadline = monotonic() + 2.0
    while True:
        recovered = pump.read_telemetry()
        recovered_state = {
            "pressure": recovered.pressure,
            "flow": recovered.flow,
            "volume": recovered.volume,
        }[failed_field]
        if recovered_state.quality is DataQuality.GOOD:
            break
        assert monotonic() < recovery_deadline
        sleep(0.02)

    assert recovered_state.quality is DataQuality.GOOD
    recovery_events = [
        event
        for event in logger.events_after(baseline_sequence)
        if event.event_id == "TELEMETRY_QUALITY_RECOVERED"
        and dict(event.fields)["field"] == failed_field
    ]
    assert len(recovery_events) == 1
    pump.disconnect()


def test_multiple_fields_log_distinct_stale_transitions_in_order(
    tmp_path: Path,
) -> None:
    raw = ToggleFieldFailurePump(delay_seconds=0.0)
    logger = MemoryDiagnosticLogger(tmp_path / "multiple.html")
    logger.configure(enabled=True, categories=DiagnosticCategory)
    intervals = PumpPollingIntervals(
        pressure_seconds=0.02,
        slow_telemetry_seconds=0.03,
        pressure_stale_seconds=0.06,
        slow_telemetry_stale_seconds=0.2,
        startup_timeout_seconds=1.0,
    )
    pump = PollingPump(
        raw,
        name="injection",
        intervals=intervals,
        diagnostics=logger,
        diagnostic_category=DiagnosticCategory.INJECTION_PUMP,
    )
    pump.connect()
    sleep(0.04)
    raw.failed_fields.add("pressure")
    sleep(0.08)
    pump.read_telemetry()
    raw.failed_fields.add("flow")
    sleep(0.25)
    telemetry = pump.read_telemetry()

    assert telemetry.pressure.quality is DataQuality.STALE
    assert telemetry.flow.quality is DataQuality.STALE
    transitions = [
        dict(event.fields)["field"]
        for event in logger.events_after(0)
        if event.event_id == "TELEMETRY_QUALITY_CHANGED"
        and dict(event.fields)["field"] in {"pressure", "flow"}
        and dict(event.fields)["new_quality"] == DataQuality.STALE.value
    ]
    assert transitions == ["pressure", "flow"]
    pump.disconnect()


def test_parse_failure_changes_field_to_invalid_then_recovers(
    tmp_path: Path,
) -> None:
    raw = ToggleFieldFailurePump(delay_seconds=0.0)
    logger = MemoryDiagnosticLogger(tmp_path / "invalid.html")
    logger.configure(enabled=True, categories=DiagnosticCategory)
    intervals = PumpPollingIntervals(
        pressure_seconds=0.02,
        slow_telemetry_seconds=0.03,
        pressure_stale_seconds=0.1,
        slow_telemetry_stale_seconds=0.1,
        startup_timeout_seconds=1.0,
    )
    pump = PollingPump(
        raw,
        name="jacket",
        intervals=intervals,
        diagnostics=logger,
        diagnostic_category=DiagnosticCategory.JACKET_PUMP,
    )
    pump.connect()
    sleep(0.04)
    valid_flow = raw.read_flow_ml_per_hour

    def invalid_flow() -> float:
        raise ValueError("FLOW parse failed")

    raw.read_flow_ml_per_hour = invalid_flow  # type: ignore[method-assign]
    invalid_deadline = monotonic() + 1.0
    while True:
        telemetry = pump.read_telemetry()
        if telemetry.flow.quality is DataQuality.INVALID:
            break
        assert monotonic() < invalid_deadline
        sleep(0.02)

    assert telemetry.flow.quality is DataQuality.INVALID
    assert any(
        event.event_id == "TELEMETRY_PARSE_FAILED" and dict(event.fields)["field"] == "flow"
        for event in logger.events_after(0)
    )
    raw.read_flow_ml_per_hour = valid_flow  # type: ignore[method-assign]
    recovery_deadline = monotonic() + 1.0
    while pump.read_telemetry().flow.quality is not DataQuality.GOOD:
        assert monotonic() < recovery_deadline
        sleep(0.02)
    assert any(
        event.event_id == "TELEMETRY_QUALITY_RECOVERED" and dict(event.fields)["field"] == "flow"
        for event in logger.events_after(0)
    )
    pump.disconnect()


def test_disconnect_and_reconnect_are_separate_events(tmp_path: Path) -> None:
    logger = MemoryDiagnosticLogger(tmp_path / "connection.html")
    logger.configure(enabled=True, categories=DiagnosticCategory)
    pump = PollingPump(
        SlowPollablePump(delay_seconds=0.0),
        name="jacket",
        intervals=slow_intervals(),
        diagnostics=logger,
        diagnostic_category=DiagnosticCategory.JACKET_PUMP,
    )

    pump.connect()
    pump.disconnect()
    pump.connect()

    event_ids = [event.event_id for event in logger.events_after(0)]
    assert event_ids.count("TELEMETRY_CONNECTION_LOST") == 1
    assert event_ids.count("TELEMETRY_CONNECTION_RESTORED") == 2
    pump.disconnect()


def test_disconnect_does_not_close_or_reconnect_while_worker_is_blocked() -> None:
    @dataclass
    class BlockingDisconnectPump(SlowPollablePump):
        flow_started: Event = field(default_factory=Event)
        release_flow: Event = field(default_factory=Event)

        def read_flow_ml_per_hour(self) -> float:
            self.calls["flow"] += 1
            self.flow_started.set()
            self.release_flow.wait()
            return 12.0

    raw = BlockingDisconnectPump(delay_seconds=0.0)
    intervals = PumpPollingIntervals(
        pressure_seconds=0.02,
        slow_telemetry_seconds=0.03,
        pressure_stale_seconds=0.2,
        slow_telemetry_stale_seconds=0.2,
        startup_timeout_seconds=0.05,
    )
    pump = PollingPump(raw, name="test", intervals=intervals)
    pump.connect()
    assert raw.flow_started.wait(timeout=1.0)

    with pytest.raises(TimeoutError, match="serial port left open"):
        pump.disconnect()
    assert raw.calls["disconnect"] == 0
    with pytest.raises(RuntimeError, match="worker is still stopping"):
        pump.connect()

    raw.release_flow.set()
    deadline = monotonic() + 1.0
    while pump._thread is not None and pump._thread.is_alive():
        assert monotonic() < deadline
        sleep(0.01)
    with pytest.raises(RuntimeError, match="requires disconnect cleanup"):
        pump.connect()
    pump.disconnect()
    assert raw.calls["disconnect"] == 1
