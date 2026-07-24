from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from random import Random
from time import monotonic, sleep
from typing import Protocol

from eor_control.domain import DataQuality, PumpStatus


class SimulationClock(Protocol):
    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class RealSimulationClock:
    def monotonic(self) -> float:
        return monotonic()

    def sleep(self, seconds: float) -> None:
        sleep(seconds)


@dataclass(slots=True)
class VirtualSimulationClock:
    """Deterministic clock for fast, repeatable simulation scenarios."""

    current_seconds: float = 0.0

    def monotonic(self) -> float:
        return self.current_seconds

    def sleep(self, seconds: float) -> None:
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        if not isfinite(seconds) or seconds < 0.0:
            raise ValueError("simulation time step must be nonnegative and finite")
        self.current_seconds += seconds


class SimulatedPumpState(StrEnum):
    DISCONNECTED = "disconnected"
    LOCAL = "local"
    REMOTE = "remote"
    CONFIGURED = "configured"
    RUNNING = "running"
    HOLDING = "holding"
    STOPPED = "stopped"
    FAULT = "fault"


class SimulatedPumpMode(StrEnum):
    CONSTANT_FLOW = "constant_flow"
    CONSTANT_PRESSURE = "constant_pressure"


class SimulatedPumpFault(StrEnum):
    PRESSURE_STALE = "pressure_stale"
    DISCONNECT = "disconnect"
    EMPTY_CYLINDER = "empty_cylinder"
    MOTOR_FAILURE = "motor_failure"
    OVERPRESSURE = "overpressure"


@dataclass(frozen=True, slots=True)
class SimulationDelay:
    minimum_seconds: float = 0.0
    maximum_seconds: float = 0.0

    def __post_init__(self) -> None:
        if (
            not isfinite(self.minimum_seconds)
            or not isfinite(self.maximum_seconds)
            or self.minimum_seconds < 0.0
            or self.maximum_seconds < self.minimum_seconds
        ):
            raise ValueError("simulation delay range is invalid")

    def choose(self, random: Random) -> float:
        if self.minimum_seconds == self.maximum_seconds:
            return self.minimum_seconds
        return random.uniform(self.minimum_seconds, self.maximum_seconds)


@dataclass(slots=True)
class SimulatedPump:
    pressure_bar: float = 0.0
    flow_ml_per_hour: float = 0.0
    remaining_volume_ml: float = 260.0
    connected: bool = False
    stop_requested: bool = False
    pressure_ramp_bar_per_second: float = 0.2
    pressure_fall_bar_per_second: float = 0.05
    hardware_pressure_limit_bar: float = 400.0
    response_delay: SimulationDelay = field(default_factory=SimulationDelay)
    clock: SimulationClock = field(default_factory=RealSimulationClock)
    random_seed: int = 42
    state: SimulatedPumpState = field(
        default=SimulatedPumpState.DISCONNECTED, init=False
    )
    mode: SimulatedPumpMode | None = field(default=None, init=False)
    target_pressure_bar: float | None = field(default=None, init=False)
    fault_reason: str | None = field(default=None, init=False)
    _last_update_seconds: float | None = field(default=None, init=False, repr=False)
    _faults: set[SimulatedPumpFault] = field(
        default_factory=set, init=False, repr=False
    )
    _fault_baseline_pressure_bar: float | None = field(
        default=None, init=False, repr=False
    )
    _fault_baseline_volume_ml: float | None = field(
        default=None, init=False, repr=False
    )
    _connected_before_fault: bool = field(default=False, init=False, repr=False)
    _random: Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        values = (
            self.pressure_bar,
            self.flow_ml_per_hour,
            self.remaining_volume_ml,
            self.pressure_ramp_bar_per_second,
            self.pressure_fall_bar_per_second,
            self.hardware_pressure_limit_bar,
        )
        if not all(isfinite(value) and value >= 0.0 for value in values):
            raise ValueError("simulated pump values must be nonnegative and finite")
        if self.hardware_pressure_limit_bar <= 0.0:
            raise ValueError("simulated hardware pressure limit must be positive")
        self._random = Random(self.random_seed)

    def connect(self) -> None:
        self._delay()
        if SimulatedPumpFault.DISCONNECT in self._faults:
            raise ConnectionError("simulated pump connection is unavailable")
        self.connected = True
        self.stop_requested = False
        self.state = SimulatedPumpState.LOCAL
        self._last_update_seconds = self.clock.monotonic()

    def read_status(self) -> PumpStatus:
        self._delay()
        self._advance()
        if not self.connected or SimulatedPumpFault.DISCONNECT in self._faults:
            raise ConnectionError("simulated pump is disconnected")
        return PumpStatus(
            pressure_bar=self.pressure_bar,
            flow_ml_per_hour=self.flow_ml_per_hour,
            remaining_volume_ml=self.remaining_volume_ml,
            connected=True,
        )

    def read_cached_status(self) -> tuple[PumpStatus, DataQuality]:
        status = self.read_status()
        quality = (
            DataQuality.STALE
            if SimulatedPumpFault.PRESSURE_STALE in self._faults
            else DataQuality.GOOD
        )
        return status, quality

    def enter_remote(self) -> None:
        self._require_connected()
        self._delay()
        self.state = SimulatedPumpState.REMOTE

    def set_constant_flow(self, flow_ml_per_hour: float) -> None:
        self._require_configurable()
        self._validate_target(flow_ml_per_hour, "flow")
        self._delay()
        self.flow_ml_per_hour = flow_ml_per_hour
        self.mode = SimulatedPumpMode.CONSTANT_FLOW
        self.target_pressure_bar = None
        self.state = SimulatedPumpState.CONFIGURED

    def set_constant_pressure(self, pressure_bar: float) -> None:
        self._require_configurable()
        self._validate_target(pressure_bar, "pressure")
        self._delay()
        self.target_pressure_bar = pressure_bar
        self.mode = SimulatedPumpMode.CONSTANT_PRESSURE
        self.state = SimulatedPumpState.CONFIGURED

    def set_pressure_limit(self, pressure_bar: float) -> None:
        self._require_configurable()
        if not isfinite(pressure_bar) or pressure_bar <= 0.0:
            raise ValueError("simulated hardware pressure limit must be positive")
        self._delay()
        self.hardware_pressure_limit_bar = pressure_bar

    def run(self) -> None:
        self._require_connected()
        if self.state is not SimulatedPumpState.CONFIGURED:
            raise RuntimeError("simulated pump must be configured before RUN")
        if self._faults & {
            SimulatedPumpFault.MOTOR_FAILURE,
            SimulatedPumpFault.EMPTY_CYLINDER,
            SimulatedPumpFault.OVERPRESSURE,
        }:
            raise RuntimeError("simulated pump fault blocks RUN")
        self._delay()
        self.stop_requested = False
        self.state = SimulatedPumpState.RUNNING
        self._last_update_seconds = self.clock.monotonic()

    def start_simulation(self) -> None:
        """Start a dashboard simulation without issuing physical-style commands."""
        self._require_connected()
        self.stop_requested = False
        if self.flow_ml_per_hour > 0.0:
            self.mode = SimulatedPumpMode.CONSTANT_FLOW
            self.state = SimulatedPumpState.RUNNING
        else:
            self.target_pressure_bar = self.pressure_bar
            self.mode = SimulatedPumpMode.CONSTANT_PRESSURE
            self.state = SimulatedPumpState.HOLDING
        self._last_update_seconds = self.clock.monotonic()

    def request_stop(self) -> None:
        self._advance()
        self.stop_requested = True
        self.flow_ml_per_hour = 0.0
        if self.connected:
            self.state = SimulatedPumpState.STOPPED

    def clear(self) -> None:
        self._require_connected()
        self.clear_faults()
        self.state = SimulatedPumpState.REMOTE

    def return_local(self) -> None:
        self._require_connected()
        if self.state is SimulatedPumpState.RUNNING:
            raise RuntimeError("simulated pump must be stopped before LOCAL")
        self.state = SimulatedPumpState.LOCAL

    def disconnect(self) -> None:
        self._advance()
        self.connected = False
        self.state = SimulatedPumpState.DISCONNECTED

    def inject_fault(self, fault: SimulatedPumpFault) -> None:
        if not self._faults:
            self._fault_baseline_pressure_bar = self.pressure_bar
            self._fault_baseline_volume_ml = self.remaining_volume_ml
            self._connected_before_fault = self.connected
        self._faults.add(fault)
        if fault is SimulatedPumpFault.DISCONNECT:
            self.connected = False
            self.state = SimulatedPumpState.DISCONNECTED
        elif fault is SimulatedPumpFault.EMPTY_CYLINDER:
            self.remaining_volume_ml = 0.0
            self._enter_internal_fault("EMPTY CYLINDER")
        elif fault is SimulatedPumpFault.MOTOR_FAILURE:
            self._enter_internal_fault("MOTOR FAILURE")
        elif fault is SimulatedPumpFault.OVERPRESSURE:
            self.pressure_bar = self.hardware_pressure_limit_bar
            self._enter_internal_fault("OVERPRESSURE")

    def clear_faults(self) -> None:
        restore_connection = (
            SimulatedPumpFault.DISCONNECT in self._faults
            and self._connected_before_fault
        )
        if self._fault_baseline_pressure_bar is not None:
            self.pressure_bar = self._fault_baseline_pressure_bar
        if self._fault_baseline_volume_ml is not None:
            self.remaining_volume_ml = self._fault_baseline_volume_ml
        self._faults.clear()
        self.fault_reason = None
        if restore_connection:
            self.connected = True
        if self.connected:
            self.state = SimulatedPumpState.STOPPED
            self._last_update_seconds = self.clock.monotonic()
        self._fault_baseline_pressure_bar = None
        self._fault_baseline_volume_ml = None
        self._connected_before_fault = False

    def _advance(self) -> None:
        now = self.clock.monotonic()
        previous = self._last_update_seconds
        self._last_update_seconds = now
        if previous is None:
            return
        dt = max(0.0, now - previous)
        if dt == 0.0:
            return
        if self.state is SimulatedPumpState.RUNNING:
            consumed = self.flow_ml_per_hour * dt / 3600.0
            self.remaining_volume_ml = max(0.0, self.remaining_volume_ml - consumed)
            if self.remaining_volume_ml == 0.0:
                self._faults.add(SimulatedPumpFault.EMPTY_CYLINDER)
                self._enter_internal_fault("EMPTY CYLINDER")
                return
            if self.mode is SimulatedPumpMode.CONSTANT_FLOW:
                self.pressure_bar += self.pressure_ramp_bar_per_second * dt
            elif (
                self.mode is SimulatedPumpMode.CONSTANT_PRESSURE
                and self.target_pressure_bar is not None
            ):
                delta = self.target_pressure_bar - self.pressure_bar
                step = self.pressure_ramp_bar_per_second * dt
                self.pressure_bar += max(-step, min(step, delta))
        elif self.state not in {
            SimulatedPumpState.HOLDING,
            SimulatedPumpState.FAULT,
        }:
            self.pressure_bar = max(
                0.0, self.pressure_bar - self.pressure_fall_bar_per_second * dt
            )
        if self.pressure_bar >= self.hardware_pressure_limit_bar:
            self.pressure_bar = self.hardware_pressure_limit_bar
            self._faults.add(SimulatedPumpFault.OVERPRESSURE)
            self._enter_internal_fault("OVERPRESSURE")

    def _enter_internal_fault(self, reason: str) -> None:
        self.fault_reason = reason
        self.flow_ml_per_hour = 0.0
        self.stop_requested = True
        self.state = SimulatedPumpState.FAULT

    def _delay(self) -> None:
        duration = self.response_delay.choose(self._random)
        if duration:
            self.clock.sleep(duration)

    def _require_connected(self) -> None:
        if not self.connected:
            raise ConnectionError("simulated pump is disconnected")

    def _require_configurable(self) -> None:
        self._require_connected()
        if self.state not in {
            SimulatedPumpState.REMOTE,
            SimulatedPumpState.CONFIGURED,
            SimulatedPumpState.STOPPED,
        }:
            raise RuntimeError(
                "simulated pump must be stopped in REMOTE mode before configuration"
            )

    @staticmethod
    def _validate_target(value: float, label: str) -> None:
        if not isfinite(value) or value < 0.0:
            raise ValueError(f"simulated pump {label} target is invalid")


class SimulatedDataAcquisition:
    def __init__(
        self,
        *,
        noise_voltage: float = 0.0,
        drift_voltage_per_second: float = 0.0,
        random_seed: int = 42,
        clock: SimulationClock | None = None,
    ) -> None:
        self.inputs: dict[str, float] = {}
        self.outputs: dict[str, float] = {}
        self.safe_state_requested = False
        self.connected = True
        self.noise_voltage = noise_voltage
        self.drift_voltage_per_second = drift_voltage_per_second
        self._clock = clock or RealSimulationClock()
        self._started_at = self._clock.monotonic()
        self._random = Random(random_seed)
        self._frozen: dict[str, float] = {}
        self._spikes: dict[str, float] = {}

    def read_voltage(self, channel: str) -> float:
        if not self.connected:
            raise ConnectionError("simulated NI device is disconnected")
        try:
            base = self._frozen.get(channel, self.inputs[channel])
        except KeyError as error:
            raise ConnectionError(f"no simulated input for {channel}") from error
        elapsed = self._clock.monotonic() - self._started_at
        noise = (
            self._random.uniform(-self.noise_voltage, self.noise_voltage)
            if self.noise_voltage
            else 0.0
        )
        return (
            self._spikes.pop(channel, 0.0)
            + base
            + self.drift_voltage_per_second * elapsed
            + noise
        )

    def read_voltages(self, channel: str, number_of_samples: int) -> list[float]:
        if number_of_samples < 1:
            raise ValueError("simulated sample count must be positive")
        return [self.read_voltage(channel) for _ in range(number_of_samples)]

    def write_voltage(self, channel: str, voltage: float) -> None:
        if not self.connected:
            raise ConnectionError("simulated NI device is disconnected")
        if not 1.0 <= voltage <= 5.0:
            raise ValueError("simulated analog output must be between 1 V and 5 V")
        self.outputs[channel] = voltage
        self.safe_state_requested = False

    def set_safe_state(self) -> None:
        self.safe_state_requested = True
        self.outputs.clear()

    def inject_spike(self, channel: str, voltage_delta: float) -> None:
        self._spikes[channel] = voltage_delta

    def freeze(self, channel: str) -> None:
        self._frozen[channel] = self.read_voltage(channel)

    def unfreeze(self, channel: str) -> None:
        self._frozen.pop(channel, None)

    def disconnect(self) -> None:
        self.connected = False

    def reconnect(self) -> None:
        self.connected = True


@dataclass(slots=True)
class SimulatedValveActuator:
    output_percent: float | None = None
    safe_state_requested: bool = False
    actual_position_percent: float = 0.0
    maximum_speed_percent_per_second: float = 25.0
    stuck: bool = False
    reverse_direction: bool = False
    clock: SimulationClock = field(default_factory=RealSimulationClock)
    _last_update_seconds: float | None = field(default=None, init=False, repr=False)

    def write_percent(self, output_percent: float) -> None:
        if not 0.0 <= output_percent <= 100.0:
            raise ValueError("simulated valve output must be between 0 and 100 percent")
        self._advance()
        self.output_percent = output_percent
        self.safe_state_requested = False

    def set_safe_state(self) -> None:
        self._advance()
        self.output_percent = None
        self.safe_state_requested = True

    def actual_position(self) -> float:
        self._advance()
        return self.actual_position_percent

    def _advance(self) -> None:
        now = self.clock.monotonic()
        previous = self._last_update_seconds
        self._last_update_seconds = now
        if previous is None or self.stuck:
            return
        target = 0.0 if self.output_percent is None else self.output_percent
        if self.reverse_direction:
            target = 100.0 - target
        maximum_step = self.maximum_speed_percent_per_second * max(0.0, now - previous)
        delta = target - self.actual_position_percent
        self.actual_position_percent += max(-maximum_step, min(maximum_step, delta))
