from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import StrEnum
from threading import RLock


class DeviceId(StrEnum):
    JACKET_PUMP = "jacket_pump"
    INJECTION_PUMP = "injection_pump"
    LINE_PRESSURE = "line_pressure"
    DIFFERENTIAL_PRESSURE = "differential_pressure"
    VALVE = "valve"


class DeviceConnectionState(StrEnum):
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"


@dataclass(frozen=True, slots=True)
class DeviceConnectionStatus:
    device: DeviceId
    enabled: bool
    state: DeviceConnectionState
    endpoint: str
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class DeviceConnector:
    connect: Callable[[], object]
    disconnect: Callable[[], object]
    endpoint: str


class DeviceConnectionManager:
    """Own the independent lifecycle state of the five configured devices."""

    def __init__(
        self,
        connectors: Mapping[DeviceId, DeviceConnector],
        *,
        enabled_devices: frozenset[DeviceId],
        event_sink: Callable[[DeviceId, str, str | None], None] | None = None,
    ) -> None:
        missing = enabled_devices.difference(connectors)
        if missing:
            names = ", ".join(sorted(device.value for device in missing))
            raise ValueError(f"enabled devices have no connector: {names}")
        self._connectors = dict(connectors)
        self._enabled = enabled_devices
        self._event_sink = event_sink
        self._lock = RLock()
        self._device_locks = {device: RLock() for device in DeviceId}
        self._statuses = {
            device: DeviceConnectionStatus(
                device,
                device in enabled_devices,
                DeviceConnectionState.DISCONNECTED,
                connectors[device].endpoint if device in connectors else "—",
            )
            for device in DeviceId
        }

    def status(self, device: DeviceId) -> DeviceConnectionStatus:
        with self._lock:
            return self._statuses[device]

    def statuses(self) -> tuple[DeviceConnectionStatus, ...]:
        with self._lock:
            return tuple(self._statuses[device] for device in DeviceId)

    @property
    def all_enabled_connected(self) -> bool:
        with self._lock:
            return all(
                self._statuses[device].state is DeviceConnectionState.CONNECTED
                for device in self._enabled
            )

    def connect_device(self, device: DeviceId) -> DeviceConnectionStatus:
        with self._device_locks[device]:
            return self._connect_device_locked(device)

    def _connect_device_locked(self, device: DeviceId) -> DeviceConnectionStatus:
        if device not in self._enabled:
            return self.status(device)
        with self._lock:
            current = self._statuses[device]
            if current.state is DeviceConnectionState.CONNECTED:
                return current
        try:
            self._emit(device, "CONNECT_REQUESTED")
            self._connectors[device].connect()
        except Exception as error:
            self._set_status(device, DeviceConnectionState.DISCONNECTED, str(error))
            self._emit(device, "CONNECT_FAILED", str(error))
            raise
        status = self._set_status(device, DeviceConnectionState.CONNECTED, None)
        self._emit(device, "CONNECTED")
        return status

    def disconnect_device(self, device: DeviceId) -> DeviceConnectionStatus:
        with self._device_locks[device]:
            return self._disconnect_device_locked(device)

    def _disconnect_device_locked(self, device: DeviceId) -> DeviceConnectionStatus:
        if device not in self._enabled:
            return self.status(device)
        with self._lock:
            current = self._statuses[device]
            if current.state is DeviceConnectionState.DISCONNECTED:
                return current
        try:
            self._emit(device, "DISCONNECT_REQUESTED")
            self._connectors[device].disconnect()
        except Exception as error:
            # A failed close may still own a worker, port, or NI task. Retaining
            # CONNECTED prevents a second owner from being created accidentally.
            self._set_status(device, DeviceConnectionState.CONNECTED, str(error))
            self._emit(device, "DISCONNECT_FAILED", str(error))
            raise
        status = self._set_status(device, DeviceConnectionState.DISCONNECTED, None)
        self._emit(device, "DISCONNECTED")
        return status

    def connect_enabled(self) -> tuple[str, ...]:
        return self._run_for_enabled(self.connect_device)

    def disconnect_all(self) -> tuple[str, ...]:
        return self._run_for_enabled(self.disconnect_device)

    def _run_for_enabled(
        self,
        operation: Callable[[DeviceId], DeviceConnectionStatus],
    ) -> tuple[str, ...]:
        if not self._enabled:
            return ()
        errors: list[str] = []
        with ThreadPoolExecutor(
            max_workers=len(self._enabled), thread_name_prefix="eor-device-connection"
        ) as executor:
            futures = {executor.submit(operation, device): device for device in self._enabled}
            for future in as_completed(futures):
                device = futures[future]
                try:
                    future.result()
                except Exception as error:
                    errors.append(f"{device.value}: {error}")
        return tuple(sorted(errors))

    def _set_status(
        self,
        device: DeviceId,
        state: DeviceConnectionState,
        last_error: str | None,
    ) -> DeviceConnectionStatus:
        with self._lock:
            current = self._statuses[device]
            updated = DeviceConnectionStatus(
                device,
                current.enabled,
                state,
                current.endpoint,
                last_error,
            )
            self._statuses[device] = updated
            return updated

    def _emit(self, device: DeviceId, event: str, detail: str | None = None) -> None:
        if self._event_sink is not None:
            self._event_sink(device, event, detail)
