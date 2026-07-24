from dataclasses import dataclass
from enum import StrEnum

from eor_control.devices import DataAcquisition, Pump
from eor_control.diagnostics import DiagnosticCategory, DiagnosticLogger


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
        diagnostics: DiagnosticLogger | None = None,
    ) -> None:
        self._jacket_pump = jacket_pump
        self._injection_pump = injection_pump
        self._daq = daq
        self._mode = mode
        self._diagnostics = diagnostics
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

    @property
    def jacket_pump(self) -> Pump:
        return self._jacket_pump

    @property
    def injection_pump(self) -> Pump:
        return self._injection_pump

    @property
    def data_acquisition(self) -> DataAcquisition:
        return self._daq

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
        self._require_physical_output_authorization()
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
        self._require_physical_output_authorization()
        for pump in (self._jacket_pump, self._injection_pump):
            acknowledge = getattr(pump, "acknowledge_stop_latch", None)
            if callable(acknowledge):
                acknowledge()
            if self._mode is RunMode.SIMULATION:
                start_simulation = getattr(pump, "start_simulation", None)
                if callable(start_simulation):
                    start_simulation()
        self._state = ApplicationState.RUNNING

    def stop(self) -> None:
        if self._state not in (ApplicationState.READY, ApplicationState.RUNNING):
            raise RuntimeError("devices can only be stopped from ready or running state")
        errors = self._request_safe_state("NORMAL_STOP")
        if errors:
            self._fault_reason = "; ".join(errors)
            self._state = ApplicationState.FAULT
        else:
            self._state = ApplicationState.READY
            if self._mode is RunMode.HARDWARE:
                # The DAQ safe-state revokes its independent physical-output
                # authorization. Do not leave the application-level hardware
                # authorization looking valid after that point.
                self._hardware_authorized = False

    def emergency_stop(self, reason: str = "manual emergency stop") -> None:
        self._enter_fault(reason)

    def acknowledge_fault(self) -> None:
        if self._state is not ApplicationState.FAULT:
            raise RuntimeError("there is no fault to acknowledge")
        self._fault_reason = None
        self._state = ApplicationState.IDLE
        for pump in (self._jacket_pump, self._injection_pump):
            acknowledge = getattr(pump, "acknowledge_stop_latch", None)
            if callable(acknowledge):
                acknowledge()
        if self._mode is RunMode.HARDWARE:
            self._hardware_authorized = False

    def disconnect(self) -> None:
        errors: list[str] = []
        if self._state is ApplicationState.RUNNING:
            errors.extend(self._request_safe_state("DISCONNECT_WHILE_RUNNING"))
        for label, pump in (
            ("jacket pump", self._jacket_pump),
            ("injection pump", self._injection_pump),
        ):
            try:
                pump.disconnect()
            except Exception as error:
                errors.append(f"{label} disconnect failed: {error}")
        self._hardware_authorized = False
        if errors:
            self._fault_reason = "; ".join(errors)
            self._state = ApplicationState.FAULT
            raise RuntimeError(self._fault_reason)
        self._fault_reason = None
        self._state = ApplicationState.IDLE

    def _require_hardware_authorization(self) -> None:
        if self._mode is RunMode.HARDWARE and not self._hardware_authorized:
            raise PermissionError("physical hardware mode requires explicit operator confirmation")

    def _require_physical_output_authorization(self) -> None:
        if self._mode is not RunMode.HARDWARE:
            return
        output_required = bool(
            getattr(self._daq, "physical_output_required", False)
        )
        output_authorized = bool(
            getattr(self._daq, "output_authorized", False)
        )
        if output_required and not output_authorized:
            raise PermissionError(
                "NI physical output requires separate hardware-mode authorization"
            )

    def _enter_fault(self, reason: str) -> None:
        errors = self._request_safe_state(reason)
        details = "; ".join(errors)
        self._fault_reason = f"{reason}; safe-state errors: {details}" if errors else reason
        self._state = ApplicationState.FAULT

    def _request_safe_state(self, safety_rule: str) -> tuple[str, ...]:
        errors: list[str] = []
        operations = (
            (
                "jacket pump STOP",
                self._jacket_pump.request_stop,
                DiagnosticCategory.JACKET_PUMP,
            ),
            (
                "injection pump STOP",
                self._injection_pump.request_stop,
                DiagnosticCategory.INJECTION_PUMP,
            ),
            (
                "DAQ safe state",
                self._daq.set_safe_state,
                DiagnosticCategory.NI_VALVE,
            ),
        )
        for label, operation, category in operations:
            try:
                operation()
            except Exception as error:
                errors.append(f"{label} failed: {error}")
                self._log_safe_state(
                    category,
                    label,
                    f"FAILED: {error}",
                    "ERROR",
                    safety_rule,
                )
            else:
                self._log_safe_state(
                    category,
                    label,
                    "OK",
                    "WARNING",
                    safety_rule,
                )
        return tuple(errors)

    def _log_safe_state(
        self,
        category: DiagnosticCategory,
        label: str,
        result: str,
        level: str,
        safety_rule: str,
    ) -> None:
        if self._diagnostics is not None:
            self._diagnostics.emit_event(
                category,
                "SAFETY_ACTION",
                fields={
                    "device": category.value,
                    "field": "control",
                    "safety_rule": safety_rule,
                    "selected_fault_strategy": "FULL_SAFE_STOP",
                    "action": label,
                    "action_result": result,
                },
                direction="SAFE_STATE",
                level=level,
            )
