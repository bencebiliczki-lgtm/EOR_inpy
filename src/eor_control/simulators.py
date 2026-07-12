from dataclasses import dataclass

from eor_control.domain import PumpStatus


@dataclass(slots=True)
class SimulatedPump:
    pressure_bar: float = 0.0
    flow_ml_per_hour: float = 0.0
    remaining_volume_ml: float = 260.0
    connected: bool = False
    stop_requested: bool = False

    def connect(self) -> None:
        self.connected = True

    def read_status(self) -> PumpStatus:
        if not self.connected:
            raise ConnectionError("simulated pump is disconnected")
        return PumpStatus(
            pressure_bar=self.pressure_bar,
            flow_ml_per_hour=self.flow_ml_per_hour,
            remaining_volume_ml=self.remaining_volume_ml,
        )

    def request_stop(self) -> None:
        self.stop_requested = True
        self.flow_ml_per_hour = 0.0

    def disconnect(self) -> None:
        self.connected = False


class SimulatedDataAcquisition:
    def __init__(self) -> None:
        self.inputs: dict[str, float] = {}
        self.outputs: dict[str, float] = {}
        self.safe_state_requested = False

    def read_voltage(self, channel: str) -> float:
        try:
            return self.inputs[channel]
        except KeyError as error:
            raise ConnectionError(f"no simulated input for {channel}") from error

    def write_voltage(self, channel: str, voltage: float) -> None:
        if not 1.0 <= voltage <= 5.0:
            raise ValueError("simulated analog output must be between 1 V and 5 V")
        self.outputs[channel] = voltage

    def set_safe_state(self) -> None:
        self.safe_state_requested = True
        self.outputs.clear()

