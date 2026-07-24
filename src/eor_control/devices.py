from datetime import datetime
from typing import Protocol

from eor_control.domain import PumpStatus


class Pump(Protocol):
    def connect(self) -> None: ...

    def read_status(self) -> PumpStatus: ...

    def request_stop(self) -> None: ...

    def disconnect(self) -> None: ...


class DataAcquisition(Protocol):
    def read_voltage(self, channel: str) -> float: ...

    def read_voltages(self, channel: str, number_of_samples: int) -> list[float]: ...

    def write_voltage(self, channel: str, voltage: float) -> None: ...

    def set_safe_state(self) -> None: ...


class Clock(Protocol):
    def utc_now(self) -> datetime: ...

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class ValveActuator(Protocol):
    def write_percent(self, output_percent: float) -> None: ...

    def set_safe_state(self) -> None: ...


class DisabledPump:
    """Non-I/O adapter for a pump role that is not part of the active device set."""

    def __init__(self, role: str) -> None:
        self._message = f"{role} pump is not added to the active hardware profile"

    def connect(self) -> None:
        raise ConnectionError(self._message)

    def read_status(self) -> PumpStatus:
        raise ConnectionError(self._message)

    def enter_remote(self) -> None:
        raise ConnectionError(self._message)

    def set_constant_flow(self, flow_ml_per_hour: float) -> None:
        raise ConnectionError(self._message)

    def set_constant_pressure(self, pressure_bar: float) -> None:
        raise ConnectionError(self._message)

    def set_pressure_limit(self, pressure_bar: float) -> None:
        raise ConnectionError(self._message)

    def run(self) -> None:
        raise ConnectionError(self._message)

    def request_stop(self) -> None:
        return

    def clear(self) -> None:
        raise ConnectionError(self._message)

    def return_local(self) -> None:
        raise ConnectionError(self._message)

    def disconnect(self) -> None:
        return
