from dataclasses import dataclass
from enum import StrEnum

from eor_control.devices import DataAcquisition, Pump


class RunMode(StrEnum):
    SIMULATION = "simulation"
    HARDWARE = "hardware"


class ApplicationState(StrEnum):
    IDLE = "idle"
    READY = "ready"
    RUNNING = "running"
    FAULT = "fault"


@dataclass(frozen=True, slots=True)
class ApplicationStatus:
    state: ApplicationState
    mode: RunMode
    fault_reason: str | None = None
    hardware_authorized: bool = False


class DeviceControlService:
    """Own device lifecycle and prevent accidental physical output."""

    HARDWARE_CONFIRMATION = "ENABLE PHYSICAL EOR HARDWARE"

    def __init__(
        self,
        *,
        jacket_pump: Pump,
        injection_pump: Pump,
        daq: DataAcquisition,
        mode: RunMode = RunMode.SIMULATION,
    ) -> None:
        self._jacket_pump = jacket_pump
        self._injection_pump = injection_pump
        self._daq = daq
        self._mode = mode
        self._state = ApplicationState.IDLE
        self._fault_reason: str | None = None
        self._hardware_authorized = False

    @property
    def status(self) -> ApplicationStatus:
        return ApplicationStatus(
            state=self._state,
            mode=self._mode,
            fault_reason=self._fault_reason,
            hardware_authorized=self._hardware_authorized,
        )

    def authorize_hardware(self, confirmation: str) -> None:
        if self._mode is not RunMode.HARDWARE:
            raise RuntimeError("hardware authorization is only valid in hardware mode")
        if confirmation != self.HARDWARE_CONFIRMATION:
            raise PermissionError("physical hardware confirmation did not match")
        self._hardware_authorized = True

    def connect(self) -> None:
        if self._state is not ApplicationState.IDLE:
            raise RuntimeError("devices can only be connected from idle state")
        self._require_hardware_authorization()
        try:
            self._jacket_pump.connect()
            self._injection_pump.connect()
        except Exception as error:
            self._enter_fault(f"device connection failed: {error}")
            raise
        self._state = ApplicationState.READY

    def start(self) -> None:
        if self._state is not ApplicationState.READY:
            raise RuntimeError("measurement can only start from ready state")
        self._require_hardware_authorization()
        self._state = ApplicationState.RUNNING

    def stop(self) -> None:
        if self._state not in (ApplicationState.READY, ApplicationState.RUNNING):
            raise RuntimeError("devices can only be stopped from ready or running state")
        errors = self._request_safe_state()
        if errors:
            self._fault_reason = "; ".join(errors)
            self._state = ApplicationState.FAULT
        else:
            self._state = ApplicationState.READY

    def emergency_stop(self, reason: str = "manual emergency stop") -> None:
        self._enter_fault(reason)

    def acknowledge_fault(self) -> None:
        if self._state is not ApplicationState.FAULT:
            raise RuntimeError("there is no fault to acknowledge")
        self._fault_reason = None
        self._state = ApplicationState.IDLE
        if self._mode is RunMode.HARDWARE:
            self._hardware_authorized = False

    def disconnect(self) -> None:
        if self._state is ApplicationState.RUNNING:
            self._request_safe_state()
        self._jacket_pump.disconnect()
        self._injection_pump.disconnect()
        self._state = ApplicationState.IDLE
        self._hardware_authorized = False

    def _require_hardware_authorization(self) -> None:
        if self._mode is RunMode.HARDWARE and not self._hardware_authorized:
            raise PermissionError("physical hardware mode requires explicit operator confirmation")

    def _enter_fault(self, reason: str) -> None:
        errors = self._request_safe_state()
        details = "; ".join(errors)
        self._fault_reason = f"{reason}; safe-state errors: {details}" if errors else reason
        self._state = ApplicationState.FAULT

    def _request_safe_state(self) -> tuple[str, ...]:
        errors: list[str] = []
        operations = (
            ("jacket pump STOP", self._jacket_pump.request_stop),
            ("injection pump STOP", self._injection_pump.request_stop),
            ("DAQ safe state", self._daq.set_safe_state),
        )
        for label, operation in operations:
            try:
                operation()
            except Exception as error:
                errors.append(f"{label} failed: {error}")
        return tuple(errors)
