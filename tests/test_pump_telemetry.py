from collections import Counter
from dataclasses import dataclass, field
from time import monotonic, sleep

import pytest

from eor_control.domain import DataQuality
from eor_control.pump_telemetry import PollingPump, PumpPollingIntervals


@dataclass
class SlowPollablePump:
    delay_seconds: float = 0.02
    fail_stop: bool = False
    calls: Counter[str] = field(default_factory=Counter)

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
        return str(self._read("status", "STOP REMOTE"))

    def enter_remote(self) -> None:
        self.calls["remote"] += 1

    def set_constant_flow(self, flow_ml_per_minute: float) -> None:
        self.calls["set_flow"] += 1

    def set_constant_pressure(self, pressure_bar: float) -> None:
        self.calls["set_pressure"] += 1

    def run(self) -> None:
        self.calls["run"] += 1

    def request_stop(self) -> None:
        self.calls["stop"] += 1
        if self.fail_stop:
            raise ConnectionError("PROBLEM=LOCAL MODE")

    def clear(self) -> None:
        self.calls["clear"] += 1

    def return_local(self) -> None:
        self.calls["local"] += 1

    def disconnect(self) -> None:
        self.calls["disconnect"] += 1


def slow_intervals() -> PumpPollingIntervals:
    return PumpPollingIntervals(
        pressure_seconds=10.0,
        slow_telemetry_seconds=10.0,
        pressure_stale_seconds=20.0,
        slow_telemetry_stale_seconds=20.0,
        startup_timeout_seconds=1.0,
    )


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
