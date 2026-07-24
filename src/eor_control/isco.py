import re
from contextlib import suppress
from dataclasses import dataclass
from math import isfinite
from typing import Protocol, cast

import serial  # type: ignore[import-untyped]

from eor_control.dasnet import DasnetClient, DasnetResponse, SerialConnection
from eor_control.diagnostics import DiagnosticCategory, DiagnosticLogger
from eor_control.domain import PumpStatus


class DasnetCommandClient(Protocol):
    def open(self) -> None: ...

    def command(self, message: str) -> DasnetResponse: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class IscoSerialConfig:
    port: str
    unit_id: int
    pump_channel: str = "A"
    baud_rate: int = 9600
    timeout_seconds: float = 0.25
    attempts: int = 3
    pressure_unit: str = "BAR"
    flow_unit: str = "ML/MIN"

    def __post_init__(self) -> None:
        if not self.port.strip():
            raise ValueError("ISCO serial port must not be empty")
        if not 0 <= self.unit_id <= 9:
            raise ValueError("ISCO unit ID must be between 0 and 9")
        if self.pump_channel not in {"A", "B", "C", "D"}:
            raise ValueError("ISCO pump channel must be A, B, C or D")
        if self.baud_rate not in {300, 1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200}:
            raise ValueError("unsupported ISCO baud rate")
        if not isfinite(self.timeout_seconds) or self.timeout_seconds <= 0.0:
            raise ValueError("ISCO timeout must be positive and finite")
        if self.attempts < 1:
            raise ValueError("ISCO attempts must be positive")
        if self.pressure_unit != "BAR":
            raise ValueError("the application currently requires ISCO pressure in BAR")
        if self.flow_unit not in {"ML/MIN", "ML/HR"}:
            raise ValueError("ISCO flow unit must be ML/MIN or ML/HR")


class IscoPump:
    def __init__(self, client: DasnetCommandClient, config: IscoSerialConfig) -> None:
        self._client = client
        self._config = config
        self._connected = False
        self._identified_model: str | None = None

    @property
    def identified_model(self) -> str | None:
        return self._identified_model

    def connect(self) -> None:
        try:
            self._client.open()
            ready = self._client.command(self._channel_command("RSVP")).message.strip()
            if ready != "READY":
                raise ConnectionError(f"unexpected ISCO RSVP response: {ready!r}")
            identity = self._client.command("IDENTIFY").message.strip()
            if "MODEL 260D PUMP" not in identity.upper():
                raise ConnectionError(
                    f"connected ISCO device is not a 260D pump: {identity!r}"
                )
        except Exception:
            self._connected = False
            with suppress(Exception):
                self._client.close()
            raise
        self._identified_model = identity
        self._connected = True

    def read_status(self) -> PumpStatus:
        self._require_connected()
        pressure = self.read_pressure_bar()
        flow = self.read_flow_ml_per_hour()
        volume = self.read_remaining_volume_ml()
        self.read_operating_status()
        return PumpStatus(
            pressure_bar=pressure,
            flow_ml_per_hour=flow,
            remaining_volume_ml=volume,
            connected=True,
        )

    def read_pressure_bar(self) -> float:
        self._require_connected()
        return self._read_measurement(
            self._channel_command("PRESS"), expected_unit=self._config.pressure_unit
        )

    def read_flow_ml_per_hour(self) -> float:
        self._require_connected()
        command = self._channel_command("FLOW")
        response = self._client.command(command).message
        flow, reported_unit = _parse_measurement_parts(
            response,
            command=command,
        )
        unit = (reported_unit or self._config.flow_unit).upper()
        if unit not in {"ML/MIN", "ML/HR"}:
            raise ValueError(
                f"unexpected ISCO unit {unit!r} for {command}; "
                "expected ML/MIN or ML/HR"
            )
        return flow * 60.0 if unit == "ML/MIN" else flow

    def read_remaining_volume_ml(self) -> float:
        self._require_connected()
        return self._read_measurement(
            self._channel_command("VOL"), expected_unit="ML"
        )

    def read_operating_status(self) -> str:
        self._require_connected()
        status_message = self._client.command(self._channel_command("STATUS")).message
        if "PROBLEM=" in status_message.upper():
            raise ConnectionError(f"ISCO pump reported {status_message.strip()}")
        return status_message.strip()

    def enter_remote(self) -> None:
        self._require_connected()
        self._client.command("REMOTE")

    def set_constant_flow(self, flow_ml_per_hour: float) -> None:
        self._require_connected()
        value = self._format_nonnegative(flow_ml_per_hour, "flow")
        self._client.command(
            f"{self._channel_command('UNITS')}=ML/HR"
        )
        self._client.command(self._channel_command("CONST FLOW", suffix_a=False))
        self._client.command(
            f"{self._channel_command('FLOW', suffix_a=False)}={value}"
        )

    def set_constant_pressure(self, pressure_bar: float) -> None:
        self._require_connected()
        value = self._format_nonnegative(pressure_bar, "pressure")
        self._client.command(self._channel_command("CONST PRESS", suffix_a=False))
        self._client.command(
            f"{self._channel_command('PRESS', suffix_a=False)}={value}"
        )

    def set_pressure_limit(self, pressure_bar: float) -> None:
        """Program the documented MAXPRESS limit for the configured channel."""
        self._require_connected()
        value = self._format_nonnegative(pressure_bar, "pressure limit")
        self._client.command(f"{self._channel_command('MAXPRESS')}={value}")

    def run(self) -> None:
        self._require_connected()
        self._client.command(self._channel_command("RUN", suffix_a=False))

    def request_stop(self) -> None:
        self._client.command(self._channel_command("STOP", suffix_a=False))

    def clear(self) -> None:
        self._require_connected()
        self._client.command("CLEAR")

    def return_local(self) -> None:
        self._require_connected()
        self._client.command("LOCAL")

    def disconnect(self) -> None:
        self._connected = False
        self._client.close()

    def _read_measurement(self, command: str, *, expected_unit: str) -> float:
        response = self._client.command(command).message
        return parse_measurement(response, command=command, expected_unit=expected_unit)

    def _channel_command(self, command: str, *, suffix_a: bool = True) -> str:
        channel = self._config.pump_channel
        if channel == "A" and (command == "RSVP" or not suffix_a):
            return command
        return f"{command}{channel}"

    def _require_connected(self) -> None:
        if not self._connected:
            raise ConnectionError("ISCO pump is not connected and identified")

    @staticmethod
    def _format_nonnegative(value: float, label: str) -> str:
        if not isfinite(value) or value < 0.0:
            raise ValueError(f"ISCO {label} target must be nonnegative and finite")
        return format(value, ".7g")


_MEASUREMENT_PATTERN = re.compile(
    r"^\s*(?:(?P<key>[A-Z]+)\s*=)?\s*"
    r"(?P<value>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:E[+-]?\d+)?)"
    r"\s*(?P<unit>[A-Z]+(?:/[A-Z]+)?)?\s*$",
    re.IGNORECASE,
)


def parse_measurement(response: str, *, command: str, expected_unit: str) -> float:
    value, unit = _parse_measurement_parts(response, command=command)
    if unit is not None and unit.upper() != expected_unit.upper():
        raise ValueError(
            f"unexpected ISCO unit {unit!r} for {command}; expected {expected_unit}"
        )
    return value


def _parse_measurement_parts(
    response: str, *, command: str
) -> tuple[float, str | None]:
    match = _MEASUREMENT_PATTERN.fullmatch(response)
    if match is None:
        raise ValueError(f"invalid ISCO {command} response: {response!r}")
    key = match.group("key")
    if key is not None and key.upper() != command.upper():
        raise ValueError(f"unexpected ISCO response key {key!r} for {command}")
    value = float(match.group("value"))
    if not isfinite(value):
        raise ValueError(f"ISCO {command} response is not finite")
    return value, match.group("unit")


def open_isco_pump(
    config: IscoSerialConfig,
    *,
    diagnostics: DiagnosticLogger | None = None,
    diagnostic_category: DiagnosticCategory = DiagnosticCategory.SYSTEM,
) -> IscoPump:
    connection = cast(
        SerialConnection,
        serial.Serial(
            port=config.port,
            baudrate=config.baud_rate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=config.timeout_seconds,
            write_timeout=config.timeout_seconds,
        ),
    )
    return IscoPump(
        DasnetClient(
            connection,
            unit_id=config.unit_id,
            attempts=config.attempts,
            diagnostics=diagnostics,
            diagnostic_category=diagnostic_category,
        ),
        config,
    )
