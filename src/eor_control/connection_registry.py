from dataclasses import dataclass
from threading import RLock


def normalize_serial_port(port: str) -> str:
    normalized = port.strip().casefold()
    if not normalized:
        raise ValueError("serial port must not be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class SerialPortReservation:
    port: str
    owner: str


class SerialPortRegistry:
    """Process-wide, case-insensitive ownership for physical serial ports."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._reservations: dict[str, SerialPortReservation] = {}

    def reserve(self, port: str, owner: str) -> SerialPortReservation:
        key = normalize_serial_port(port)
        with self._lock:
            current = self._reservations.get(key)
            if current is not None:
                raise RuntimeError(
                    f"serial port {port.strip()} is already reserved by {current.owner}"
                )
            reservation = SerialPortReservation(port=port.strip(), owner=owner)
            self._reservations[key] = reservation
            return reservation

    def release(self, reservation: SerialPortReservation) -> None:
        key = normalize_serial_port(reservation.port)
        with self._lock:
            current = self._reservations.get(key)
            if current == reservation:
                del self._reservations[key]

    def owner(self, port: str) -> str | None:
        with self._lock:
            reservation = self._reservations.get(normalize_serial_port(port))
            return None if reservation is None else reservation.owner

    def is_reserved(self, port: str) -> bool:
        return self.owner(port) is not None


SERIAL_PORT_REGISTRY = SerialPortRegistry()
