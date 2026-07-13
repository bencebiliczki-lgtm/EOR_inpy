from dataclasses import dataclass, field
from time import sleep

from eor_control.control import ControlMode, PressureSource
from eor_control.runtime import BackgroundControlRunner, RuntimeSettings


@dataclass
class FakeControlLoop:
    delay_seconds: float = 0.0
    persist_flags: list[bool] = field(default_factory=list)
    safe_state_count: int = 0

    def execute_once(self, **arguments: object) -> object:
        self.persist_flags.append(bool(arguments["persist"]))
        if self.delay_seconds:
            sleep(self.delay_seconds)
        return object()

    def request_safe_state(self) -> None:
        self.safe_state_count += 1


def settings(recording_interval: float = 1.0) -> RuntimeSettings:
    return RuntimeSettings(
        active_stage="water",
        mode=ControlMode.MANUAL,
        manual_output_percent=25.0,
        source=PressureSource.LINE_SENSOR,
        setpoint_bar=100.0,
        recording_interval_seconds=recording_interval,
    )


def test_control_cycles_run_faster_than_persistent_recording() -> None:
    loop = FakeControlLoop()
    runner = BackgroundControlRunner(loop, control_interval_seconds=0.02)  # type: ignore[arg-type]

    runner.start(settings())
    sleep(0.11)
    runner.stop()

    assert len(loop.persist_flags) >= 4
    assert loop.persist_flags.count(True) == 1


def test_slow_control_cycle_triggers_watchdog_and_safe_state() -> None:
    loop = FakeControlLoop(delay_seconds=0.05)
    faults: list[str] = []
    runner = BackgroundControlRunner(
        loop,  # type: ignore[arg-type]
        control_interval_seconds=0.01,
        watchdog_tolerance_seconds=0.005,
        on_fault=faults.append,
    )

    runner.start(settings())
    sleep(0.1)
    runner.stop()

    assert loop.safe_state_count == 1
    assert len(faults) == 1
    assert "deadline missed" in faults[0]
