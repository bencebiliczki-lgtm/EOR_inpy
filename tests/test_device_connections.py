from dataclasses import dataclass
from threading import Event, Thread

from eor_control.device_connections import (
    DeviceConnectionManager,
    DeviceConnectionState,
    DeviceConnector,
    DeviceId,
)


@dataclass
class Probe:
    connected: int = 0
    disconnected: int = 0
    fail_connect: bool = False
    fail_disconnect: bool = False

    def connect(self) -> None:
        self.connected += 1
        if self.fail_connect:
            raise ConnectionError("connect failed")

    def disconnect(self) -> None:
        self.disconnected += 1
        if self.fail_disconnect:
            raise TimeoutError("worker still running")


def manager_for(
    probes: dict[DeviceId, Probe], enabled: frozenset[DeviceId]
) -> DeviceConnectionManager:
    return DeviceConnectionManager(
        {
            device: DeviceConnector(probe.connect, probe.disconnect, device.value)
            for device, probe in probes.items()
        },
        enabled_devices=enabled,
    )


def test_disabled_device_does_not_execute_or_block_readiness() -> None:
    jacket = Probe()
    disabled = Probe(fail_connect=True)
    manager = manager_for(
        {DeviceId.JACKET_PUMP: jacket, DeviceId.INJECTION_PUMP: disabled},
        frozenset({DeviceId.JACKET_PUMP}),
    )

    assert manager.connect_enabled() == ()

    assert manager.all_enabled_connected
    assert jacket.connected == 1
    assert disabled.connected == 0
    assert manager.status(DeviceId.INJECTION_PUMP).state is DeviceConnectionState.DISCONNECTED


def test_connect_all_keeps_independent_device_results() -> None:
    jacket = Probe()
    injection = Probe(fail_connect=True)
    manager = manager_for(
        {DeviceId.JACKET_PUMP: jacket, DeviceId.INJECTION_PUMP: injection},
        frozenset({DeviceId.JACKET_PUMP, DeviceId.INJECTION_PUMP}),
    )

    errors = manager.connect_enabled()

    assert len(errors) == 1
    assert manager.status(DeviceId.JACKET_PUMP).state is DeviceConnectionState.CONNECTED
    assert manager.status(DeviceId.INJECTION_PUMP).state is DeviceConnectionState.DISCONNECTED
    assert not manager.all_enabled_connected


def test_failed_disconnect_retains_connected_ownership_state() -> None:
    probe = Probe(fail_disconnect=True)
    manager = manager_for(
        {DeviceId.JACKET_PUMP: probe}, frozenset({DeviceId.JACKET_PUMP})
    )
    manager.connect_enabled()

    errors = manager.disconnect_all()

    assert errors == ("jacket_pump: worker still running",)
    status = manager.status(DeviceId.JACKET_PUMP)
    assert status.state is DeviceConnectionState.CONNECTED
    assert status.last_error == "worker still running"


def test_enabled_connections_run_independently() -> None:
    first_started = Event()
    second_started = Event()

    def first_connect() -> None:
        first_started.set()
        assert second_started.wait(1.0)

    def second_connect() -> None:
        second_started.set()
        assert first_started.wait(1.0)

    manager = DeviceConnectionManager(
        {
            DeviceId.JACKET_PUMP: DeviceConnector(first_connect, lambda: None, "COM1"),
            DeviceId.INJECTION_PUMP: DeviceConnector(second_connect, lambda: None, "COM2"),
        },
        enabled_devices=frozenset(
            {DeviceId.JACKET_PUMP, DeviceId.INJECTION_PUMP}
        ),
    )

    assert manager.connect_enabled() == ()
    assert manager.all_enabled_connected


def test_parallel_connect_for_same_device_executes_connector_once() -> None:
    entered = Event()
    release = Event()
    calls: list[str] = []

    def connect() -> None:
        calls.append("connect")
        entered.set()
        assert release.wait(1.0)

    manager = DeviceConnectionManager(
        {
            DeviceId.JACKET_PUMP: DeviceConnector(
                connect, lambda: None, "COM1"
            )
        },
        enabled_devices=frozenset({DeviceId.JACKET_PUMP}),
    )
    first = Thread(target=manager.connect_device, args=(DeviceId.JACKET_PUMP,))
    second = Thread(target=manager.connect_device, args=(DeviceId.JACKET_PUMP,))

    first.start()
    assert entered.wait(1.0)
    second.start()
    release.set()
    first.join(1.0)
    second.join(1.0)

    assert calls == ["connect"]


def test_connection_order_is_parallel_pumps_then_shared_ai_then_valve() -> None:
    events: list[str] = []
    jacket_started = Event()
    injection_started = Event()

    def jacket_connect() -> None:
        jacket_started.set()
        assert injection_started.wait(1.0)
        events.append("jacket")

    def injection_connect() -> None:
        injection_started.set()
        assert jacket_started.wait(1.0)
        events.append("injection")

    def shared_ai_connect() -> None:
        assert set(events) == {"jacket", "injection"}
        events.append("shared_ai")

    def valve_connect() -> None:
        assert events[-1] == "shared_ai"
        events.append("valve")

    manager = DeviceConnectionManager(
        {
            DeviceId.JACKET_PUMP: DeviceConnector(jacket_connect, lambda: None, "COM1"),
            DeviceId.INJECTION_PUMP: DeviceConnector(
                injection_connect, lambda: None, "COM2"
            ),
            DeviceId.LINE_PRESSURE: DeviceConnector(
                shared_ai_connect, lambda: None, "Dev1/ai0"
            ),
            DeviceId.DIFFERENTIAL_PRESSURE: DeviceConnector(
                shared_ai_connect, lambda: None, "Dev1/ai1"
            ),
            DeviceId.VALVE: DeviceConnector(valve_connect, lambda: None, "Dev1/ao0"),
        },
        enabled_devices=frozenset(DeviceId),
    )

    assert manager.connect_enabled() == ()
    assert events.count("shared_ai") == 1
    assert events[-2:] == ["shared_ai", "valve"]
    assert manager.all_enabled_connected


def test_shared_ai_failure_marks_both_inputs_failed_and_skips_valve() -> None:
    valve_calls: list[str] = []

    def fail_ai() -> None:
        raise RuntimeError("resource reserved")

    manager = DeviceConnectionManager(
        {
            DeviceId.LINE_PRESSURE: DeviceConnector(
                fail_ai, lambda: None, "Dev1/ai0"
            ),
            DeviceId.DIFFERENTIAL_PRESSURE: DeviceConnector(
                fail_ai, lambda: None, "Dev1/ai1"
            ),
            DeviceId.VALVE: DeviceConnector(
                lambda: valve_calls.append("valve"), lambda: None, "Dev1/ao0"
            ),
        },
        enabled_devices=frozenset(
            {
                DeviceId.LINE_PRESSURE,
                DeviceId.DIFFERENTIAL_PRESSURE,
                DeviceId.VALVE,
            }
        ),
    )

    assert manager.connect_enabled() == ("ni_pressure_inputs: resource reserved",)
    assert valve_calls == []
    assert manager.status(DeviceId.LINE_PRESSURE).last_error == "resource reserved"
    assert (
        manager.status(DeviceId.DIFFERENTIAL_PRESSURE).last_error
        == "resource reserved"
    )
