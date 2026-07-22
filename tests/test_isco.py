from dataclasses import dataclass, field

import pytest

from eor_control.dasnet import DasnetResponse
from eor_control.isco import IscoPump, IscoSerialConfig, parse_measurement


@dataclass
class ScriptedClient:
    responses: dict[str, list[str]]
    commands: list[str] = field(default_factory=list)
    closed: bool = False
    open_count: int = 0

    def open(self) -> None:
        self.open_count += 1
        self.closed = False

    def command(self, message: str) -> DasnetResponse:
        self.commands.append(message)
        try:
            response = self.responses[message].pop(0)
        except (KeyError, IndexError) as error:
            raise AssertionError(f"unexpected command: {message}") from error
        return DasnetResponse("R", 0, response)

    def close(self) -> None:
        self.closed = True


def connected_pump() -> tuple[IscoPump, ScriptedClient]:
    client = ScriptedClient(
        {
            "RSVP": ["READY"],
            "IDENTIFY": ["SERIES=1240-021, MODEL 260D PUMP, REV A"],
            "PRESSA": ["PRESSA=100.5 BAR"],
            "FLOWA": ["FLOWA=1.25 ML/MIN"],
            "VOLA": ["VOLA=200.1250 ML"],
            "STATUSA": ["STATUS=RUN"],
        }
    )
    pump = IscoPump(client, IscoSerialConfig("COM1", 6))
    pump.connect()
    return pump, client


def test_connect_identifies_260d_without_entering_remote_mode() -> None:
    pump, client = connected_pump()

    assert pump.identified_model is not None
    assert client.commands[:2] == ["RSVP", "IDENTIFY"]
    assert "REMOTE" not in client.commands


def test_disconnect_releases_client_and_next_connect_reopens_it() -> None:
    pump, client = connected_pump()
    pump.disconnect()
    assert client.closed
    client.responses["RSVP"] = ["READY"]
    client.responses["IDENTIFY"] = ["MODEL 260D PUMP"]

    pump.connect()

    assert not client.closed
    assert client.open_count == 2
    assert client.commands.count("RSVP") == 2


def test_status_queries_documented_fields_and_converts_flow_to_hour() -> None:
    pump, client = connected_pump()

    status = pump.read_status()

    assert status.pressure_bar == pytest.approx(100.5)
    assert status.flow_ml_per_hour == pytest.approx(75.0)
    assert status.remaining_volume_ml == pytest.approx(200.125)
    assert client.commands[-4:] == ["PRESSA", "FLOWA", "VOLA", "STATUSA"]


def test_non_260d_identity_is_rejected() -> None:
    client = ScriptedClient({"RSVP": ["READY"], "IDENTIFY": ["MODEL 500D PUMP"]})

    with pytest.raises(ConnectionError, match="not a 260D"):
        IscoPump(client, IscoSerialConfig("COM1", 6)).connect()
    assert client.closed


def test_pump_problem_in_status_is_rejected() -> None:
    pump, client = connected_pump()
    client.responses["STATUSA"] = ["STATUS=STOP PROBLEM=CYLINDER EMPTY"]

    with pytest.raises(ConnectionError, match="CYLINDER EMPTY"):
        pump.read_status()


def test_documented_control_command_sequences() -> None:
    pump, client = connected_pump()
    client.responses.update(
        {
            "REMOTE": [""],
            "CONST FLOW": [""],
            "FLOW=1": [""],
            "RUN": [""],
            "STOP": [""],
            "LOCAL": [""],
        }
    )

    pump.enter_remote()
    pump.set_constant_flow(1.0)
    pump.run()
    pump.request_stop()
    pump.return_local()

    assert client.commands[-6:] == ["REMOTE", "CONST FLOW", "FLOW=1", "RUN", "STOP", "LOCAL"]


@pytest.mark.parametrize(
    ("response", "command", "unit", "expected"),
    [
        ("100.2500", "PRESSA", "BAR", 100.25),
        ("PRESSA = 100.25 BAR", "PRESSA", "BAR", 100.25),
        ("1.25E+1 ML/HR", "FLOWA", "ML/HR", 12.5),
        ("VOLA=200.0000 ml", "VOLA", "ML", 200.0),
    ],
)
def test_measurement_parser(
    response: str, command: str, unit: str, expected: float
) -> None:
    assert parse_measurement(response, command=command, expected_unit=unit) == expected


def test_measurement_parser_rejects_wrong_key_unit_and_non_number() -> None:
    with pytest.raises(ValueError, match="response key"):
        parse_measurement("FLOWA=1.0 BAR", command="PRESSA", expected_unit="BAR")
    with pytest.raises(ValueError, match="unexpected ISCO unit"):
        parse_measurement("PRESSA=100 PSI", command="PRESSA", expected_unit="BAR")
    with pytest.raises(ValueError, match="invalid ISCO"):
        parse_measurement("PRESSA=NaN BAR", command="PRESSA", expected_unit="BAR")


def test_non_a_channel_control_commands_receive_channel_suffix() -> None:
    client = ScriptedClient(
        {
            "RSVPB": ["READY"],
            "IDENTIFY": ["MODEL 260D PUMP"],
            "CONST FLOWB": [""],
            "FLOWB=1": [""],
            "RUNB": [""],
            "STOPB": [""],
        }
    )
    pump = IscoPump(client, IscoSerialConfig("COM1", 6, pump_channel="B"))
    pump.connect()

    pump.set_constant_flow(1.0)
    pump.run()
    pump.request_stop()

    assert client.commands[-4:] == ["CONST FLOWB", "FLOWB=1", "RUNB", "STOPB"]
