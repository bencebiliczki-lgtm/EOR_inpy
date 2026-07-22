from dataclasses import dataclass
from threading import Lock
from typing import Protocol

from eor_control.diagnostics import DiagnosticCategory, DiagnosticLogger


class DasnetError(Exception):
    pass


class DasnetTimeoutError(DasnetError):
    pass


class DasnetFrameError(DasnetError):
    pass


class SerialConnection(Protocol):
    def write(self, data: bytes) -> int: ...

    def read_until(self, expected: bytes = b"\n", size: int | None = None) -> bytes: ...

    def reset_input_buffer(self) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class DasnetResponse:
    acknowledgement: str
    destination: int | None
    message: str


def checksum(prefix: str) -> int:
    try:
        encoded = prefix.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError("DASNET frames only support ASCII") from error
    return (-sum(encoded)) & 0xFF


def encode_command(
    unit_id: int,
    command: str | None,
    *,
    source_id: int = 0,
    acknowledgement: str = "R",
) -> bytes:
    if not 0 <= unit_id <= 9 or not 0 <= source_id <= 9:
        raise ValueError("DASNET unit identifiers must be single decimal digits")
    if acknowledgement not in {"R", "B", "E"}:
        raise ValueError("invalid DASNET acknowledgement")
    if command is None:
        prefix = f"{unit_id}{acknowledgement} "
    else:
        _validate_command(command)
        encoded_length = len(command.encode("ascii"))
        if encoded_length > 256:
            raise ValueError("DASNET message must not exceed 256 characters")
        length_field = "00" if encoded_length == 256 else f"{encoded_length:02X}"
        # These are separate fields: destination | ACK | source | 2-hex length.
        prefix = f"{unit_id}{acknowledgement}{source_id}{length_field}{command}"
    return f"{prefix}{checksum(prefix):02X}\r".encode("ascii")


def decode_response(frame: bytes) -> DasnetResponse:
    if not frame.endswith(b"\r"):
        raise DasnetFrameError("DASNET response is not terminated by CR")
    try:
        text = frame[:-1].decode("ascii")
    except UnicodeDecodeError as error:
        raise DasnetFrameError("DASNET response is not ASCII") from error
    if len(text) < 4:
        raise DasnetFrameError("DASNET response is too short")
    prefix, checksum_text = text[:-2], text[-2:]
    try:
        received_checksum = int(checksum_text, 16)
    except ValueError as error:
        raise DasnetFrameError("DASNET response checksum is not hexadecimal") from error
    if (sum(prefix.encode("ascii")) + received_checksum) & 0xFF:
        raise DasnetFrameError("DASNET response checksum mismatch")
    if prefix[0] not in {"R", "B"}:
        raise DasnetFrameError("invalid DASNET response acknowledgement")
    destination_text = prefix[1]
    destination = None if destination_text == " " else _parse_digit(destination_text)
    if len(prefix) == 2:
        return DasnetResponse(prefix[0], destination, "")
    if len(prefix) < 4:
        raise DasnetFrameError("DASNET response has an incomplete length field")
    try:
        declared_length = int(prefix[2:4], 16)
    except ValueError as error:
        raise DasnetFrameError("DASNET response length is not hexadecimal") from error
    message = prefix[4:]
    expected_length = 256 if declared_length == 0 else declared_length
    if len(message.encode("ascii")) != expected_length:
        raise DasnetFrameError("DASNET response length mismatch")
    return DasnetResponse(prefix[0], destination, message)


def _validate_command(command: str) -> None:
    if not command:
        raise ValueError("DASNET command must not be empty")
    if command != command.upper():
        raise ValueError("DASNET commands must be uppercase")
    if ";" in command:
        raise ValueError("D-Series accepts only one command per DASNET message")
    try:
        encoded = command.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError("DASNET commands only support ASCII") from error
    if any(value < 0x20 or value > 0x7E for value in encoded):
        raise ValueError("DASNET command contains a non-printable character")


def _parse_digit(value: str) -> int:
    if len(value) != 1 or not value.isdecimal():
        raise DasnetFrameError("DASNET destination is not a decimal digit")
    return int(value)


class DasnetClient:
    def __init__(
        self,
        connection: SerialConnection,
        *,
        unit_id: int,
        source_id: int = 0,
        attempts: int = 3,
        response_reads_per_attempt: int = 4,
        diagnostics: DiagnosticLogger | None = None,
        diagnostic_category: DiagnosticCategory = DiagnosticCategory.SYSTEM,
    ) -> None:
        if attempts < 1:
            raise ValueError("DASNET attempts must be positive")
        if response_reads_per_attempt < 1:
            raise ValueError("DASNET response reads per attempt must be positive")
        self._connection = connection
        self._unit_id = unit_id
        self._source_id = source_id
        self._attempts = attempts
        self._response_reads_per_attempt = response_reads_per_attempt
        self._network_started = False
        self._command_lock = Lock()
        self._diagnostics = diagnostics
        self._diagnostic_category = diagnostic_category

    def command(self, message: str) -> DasnetResponse:
        with self._command_lock:
            return self._command_locked(message)

    def _command_locked(self, message: str) -> DasnetResponse:
        frame = encode_command(self._unit_id, message, source_id=self._source_id)
        last_error: DasnetError | None = None
        for _ in range(self._attempts):
            self._connection.reset_input_buffer()
            if not self._network_started:
                self._log("TX", "[CR] network start")
                self._connection.write(b"\r")
                self._network_started = True
            self._log("TX", frame.decode("ascii"))
            self._connection.write(frame)
            response_frame = self._read_response_frame()
            if not response_frame:
                self._log("TIMEOUT", "no response", level="ERROR")
                last_error = DasnetTimeoutError("DASNET pump did not respond")
                continue
            self._log("RX", response_frame.decode("ascii", errors="replace"))
            try:
                response = decode_response(response_frame)
            except DasnetFrameError as error:
                self._log("ERROR", str(error), level="ERROR")
                last_error = error
                continue
            if response.acknowledgement == "B":
                last_error = DasnetError("DASNET pump is busy")
                continue
            if response.destination not in (None, self._source_id):
                last_error = DasnetFrameError("DASNET response has an unexpected destination")
                continue
            if response.message.startswith("PROBLEM="):
                self._log("PROBLEM", response.message, level="ERROR")
                raise DasnetError(response.message)
            return response
        if last_error is None:
            raise DasnetTimeoutError("DASNET command failed without a response")
        raise last_error

    def _read_response_frame(self) -> bytes:
        """Collect a possibly fragmented response across bounded serial reads."""
        response = bytearray()
        for _ in range(self._response_reads_per_attempt):
            remaining = 512 - len(response)
            if remaining <= 0:
                break
            chunk = self._connection.read_until(b"\r", remaining)
            if chunk:
                response.extend(chunk)
                if response.endswith(b"\r"):
                    break
        return bytes(response)

    def close(self) -> None:
        self._connection.close()

    def _log(self, direction: str, message: str, *, level: str = "INFO") -> None:
        if self._diagnostics is not None:
            self._diagnostics.emit(
                self._diagnostic_category, direction, message, level=level
            )
