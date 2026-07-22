from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock, Thread
from time import sleep

import pytest

from eor_control.dasnet import (
    DasnetClient,
    DasnetError,
    DasnetFrameError,
    DasnetTimeoutError,
    decode_response,
    encode_command,
)
from eor_control.diagnostics import DiagnosticCategory, DiagnosticLogger


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("IDENTIFY", b"6R008IDENTIFY84\r"),
        ("REMOTE", b"6R006REMOTE16\r"),
        ("CONST FLOW", b"6R00ACONST FLOWF8\r"),
        ("FLOW=1.00", b"6R009FLOW=1.00AB\r"),
        ("RUN", b"6R003RUNF0\r"),
    ],
)
def test_documented_command_frames(command: str, expected: bytes) -> None:
    assert encode_command(6, command) == expected


def test_documented_empty_poll_frame() -> None:
    assert encode_command(6, None) == b"6R 58\r"


@pytest.mark.parametrize(
    ("message_length", "length_field"),
    [
        (8, b"08"),
        (15, b"0F"),
        (16, b"10"),
        (26, b"1A"),
        (39, b"27"),
        (255, b"FF"),
        (256, b"00"),
    ],
)
def test_outbound_length_is_always_two_hex_characters(
    message_length: int, length_field: bytes
) -> None:
    frame = encode_command(6, "A" * message_length)

    assert frame[:3] == b"6R0"
    assert frame[3:5] == length_field
    assert frame[5 : 5 + message_length] == b"A" * message_length


def test_documented_empty_acknowledgement_response() -> None:
    assert decode_response(b"R 8E\r").message == ""


def response(message: str, *, acknowledgement: str = "R") -> bytes:
    prefix = f"{acknowledgement}0{len(message):02X}{message}"
    checksum = (-sum(prefix.encode("ascii"))) & 0xFF
    return f"{prefix}{checksum:02X}\r".encode("ascii")


def test_response_message_and_checksum_are_validated() -> None:
    decoded = decode_response(response("READY"))

    assert decoded.acknowledgement == "R"
    assert decoded.destination == 0
    assert decoded.message == "READY"

    with pytest.raises(DasnetFrameError, match="checksum"):
        decode_response(b"R005READY00\r")


@pytest.mark.parametrize("command", ["remote", "RUN;STOP", "ÁLLJ", ""])
def test_invalid_commands_are_rejected(command: str) -> None:
    with pytest.raises(ValueError):
        encode_command(6, command)


@dataclass
class FakeSerial:
    responses: list[bytes]
    writes: list[bytes] = field(default_factory=list)
    reset_count: int = 0
    closed: bool = False
    is_open: bool = True
    open_count: int = 0

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def read_until(self, expected: bytes = b"\n", size: int | None = None) -> bytes:
        return self.responses.pop(0) if self.responses else b""

    def reset_input_buffer(self) -> None:
        self.reset_count += 1

    def close(self) -> None:
        self.closed = True
        self.is_open = False

    def open(self) -> None:
        self.open_count += 1
        self.closed = False
        self.is_open = True


def test_client_starts_network_and_waits_for_delayed_response() -> None:
    serial = FakeSerial([b"", response("READY")])
    client = DasnetClient(serial, unit_id=6)

    assert client.command("RSVP").message == "READY"
    assert serial.writes[0] == b"\r"
    assert serial.writes.count(encode_command(6, "RSVP")) == 1
    assert serial.reset_count == 1


def test_client_close_releases_and_open_reacquires_serial_port() -> None:
    serial = FakeSerial([response("READY"), response("READY")])
    client = DasnetClient(serial, unit_id=6)
    client.command("RSVP")

    client.close()
    assert serial.closed
    assert not serial.is_open

    client.open()
    assert serial.is_open
    assert serial.open_count == 1
    client.command("RSVP")
    assert serial.writes.count(b"\r") == 2


def test_client_collects_fragmented_response_until_cr() -> None:
    complete = response("READY")
    serial = FakeSerial([complete[:5], complete[5:]])

    assert DasnetClient(serial, unit_id=6).command("RSVP").message == "READY"
    assert serial.writes.count(encode_command(6, "RSVP")) == 1


@dataclass
class BlockingSerial:
    writes: list[bytes] = field(default_factory=list)
    first_read_started: Event = field(default_factory=Event)
    release_first_read: Event = field(default_factory=Event)
    _read_count: int = 0
    _lock: Lock = field(default_factory=Lock)

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def read_until(self, expected: bytes = b"\n", size: int | None = None) -> bytes:
        with self._lock:
            self._read_count += 1
            read_count = self._read_count
        if read_count == 1:
            self.first_read_started.set()
            self.release_first_read.wait(timeout=1.0)
        return response("READY")

    def reset_input_buffer(self) -> None:
        pass

    def close(self) -> None:
        pass


def test_client_serializes_concurrent_commands() -> None:
    serial = BlockingSerial()
    client = DasnetClient(serial, unit_id=6)
    errors: list[Exception] = []

    def command(message: str) -> None:
        try:
            client.command(message)
        except Exception as error:
            errors.append(error)

    first = Thread(target=command, args=("RSVP",))
    second = Thread(target=command, args=("IDENTIFY",))
    first.start()
    assert serial.first_read_started.wait(timeout=1.0)
    second.start()
    sleep(0.05)

    assert encode_command(6, "IDENTIFY") not in serial.writes
    serial.release_first_read.set()
    first.join(timeout=1.0)
    second.join(timeout=1.0)

    assert errors == []
    assert serial.writes.count(encode_command(6, "RSVP")) == 1
    assert serial.writes.count(encode_command(6, "IDENTIFY")) == 1


def test_client_retries_invalid_frame_then_raises_last_error() -> None:
    serial = FakeSerial([b"bad\r", b"", b""])

    with pytest.raises(DasnetTimeoutError):
        DasnetClient(serial, unit_id=6).command("RSVP")


def test_client_raises_pump_problem_without_retrying() -> None:
    serial = FakeSerial([response("PROBLEM=INVALID COMMAND")])

    with pytest.raises(DasnetError, match="INVALID COMMAND"):
        DasnetClient(serial, unit_id=6).command("INVALID")
    assert serial.reset_count == 1


def test_client_emits_device_specific_tx_and_rx_diagnostics(tmp_path: Path) -> None:
    logger = DiagnosticLogger(tmp_path / "communication.log")
    logger.configure(enabled=True, categories=[DiagnosticCategory.JACKET_PUMP])
    serial = FakeSerial([response("READY")])
    client = DasnetClient(
        serial,
        unit_id=6,
        diagnostics=logger,
        diagnostic_category=DiagnosticCategory.JACKET_PUMP,
    )

    client.command("RSVP")

    events = logger.events_after(0)
    assert any(event.direction == "TX" and "RSVP" in event.message for event in events)
    assert any(event.direction == "RX" and "READY" in event.message for event in events)
