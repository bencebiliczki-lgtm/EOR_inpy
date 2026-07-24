from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic, sleep

import pytest

from eor_control.diagnostics import DiagnosticCategory, DiagnosticLogger
from eor_control.domain import DataQuality
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

    def set_constant_flow(self, flow_ml_per_hour: float) -> None:
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


@dataclass
class SlowTelemetryFailurePump(SlowPollablePump):
    def read_flow_ml_per_hour(self) -> float:
        self.calls["flow"] += 1
        raise TimeoutError("FLOW timeout")

    def read_remaining_volume_ml(self) -> float:
        self.calls["volume"] += 1
        raise TimeoutError("VOLA timeout")


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
        pressure_stale_seconds=20.0,
        slow_telemetry_stale_seconds=20.0,
        startup_timeout_seconds=1.0,
    )


def test_default_pressure_stale_window_covers_observed_serial_jitter() -> None:
    intervals = PumpPollingIntervals()

    assert intervals.pressure_seconds == pytest.approx(0.4)
    assert intervals.pressure_stale_seconds == pytest.approx(2.0)
    assert intervals.slow_telemetry_stale_seconds == pytest.approx(3.0)


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
        slow_telemetry_stale_seconds=0.06,
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
        pressure_stale_seconds=0.05,
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
    sleep(0.04)
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
        for event in logger.events_after(0)
        if event.event_id == "TELEMETRY_QUALITY_CHANGED"
        and dict(event.fields)["field"] == failed_field
        and dict(event.fields)["new_quality"] == DataQuality.STALE.value
    ]
    assert len(stale_events) == 1
    assert float(dict(stale_events[0].fields)["age_ms"]) >= 50.0
    assert dict(stale_events[0].fields)["stale_limit_ms"] == "50.0"

    raw.failed_field = None
    recovery_deadline = monotonic() + 1.0
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
        for event in logger.events_after(0)
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
        pressure_stale_seconds=0.05,
        slow_telemetry_stale_seconds=0.05,
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
    sleep(0.08)
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
        event.event_id == "TELEMETRY_PARSE_FAILED"
        and dict(event.fields)["field"] == "flow"
        for event in logger.events_after(0)
    )
    raw.read_flow_ml_per_hour = valid_flow  # type: ignore[method-assign]
    recovery_deadline = monotonic() + 1.0
    while pump.read_telemetry().flow.quality is not DataQuality.GOOD:
        assert monotonic() < recovery_deadline
        sleep(0.02)
    assert any(
        event.event_id == "TELEMETRY_QUALITY_RECOVERED"
        and dict(event.fields)["field"] == "flow"
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
