from pathlib import Path

import pytest

from eor_control.application import ApplicationState, DeviceControlService, RunMode
from eor_control.diagnostics import DiagnosticCategory, DiagnosticLogger
from eor_control.simulators import SimulatedDataAcquisition, SimulatedPump


def service(mode: RunMode = RunMode.SIMULATION) -> tuple[
    DeviceControlService, SimulatedPump, SimulatedPump, SimulatedDataAcquisition
]:
    jacket = SimulatedPump()
    injection = SimulatedPump()
    daq = SimulatedDataAcquisition()
    return (
        DeviceControlService(
            jacket_pump=jacket, injection_pump=injection, daq=daq, mode=mode
        ),
        jacket,
        injection,
        daq,
    )


def test_simulation_lifecycle() -> None:
    control, jacket, injection, daq = service()

    control.connect()
    assert control.status.state is ApplicationState.READY
    control.start()
    assert control.status.state is ApplicationState.RUNNING
    control.stop()

    assert control.status.state is ApplicationState.READY
    assert jacket.stop_requested
    assert injection.stop_requested
    assert daq.safe_state_requested


def test_hardware_mode_requires_exact_confirmation() -> None:
    control, *_ = service(RunMode.HARDWARE)

    with pytest.raises(PermissionError, match="explicit operator"):
        control.connect()
    with pytest.raises(PermissionError, match="did not match"):
        control.authorize_hardware("yes")

    control.authorize_hardware(DeviceControlService.HARDWARE_CONFIRMATION)
    control.connect()
    control.start()

    assert control.status.state is ApplicationState.RUNNING
    assert control.status.hardware_authorized


class OutputAuthorizationAwareDaq(SimulatedDataAcquisition):
    physical_output_required = True
    output_authorized = False

    def authorize_output(self) -> None:
        self.output_authorized = True

    def set_safe_state(self) -> None:
        super().set_safe_state()
        self.output_authorized = False


def test_hardware_connect_requires_ni_output_authorization_and_stop_revokes_both() -> None:
    jacket = SimulatedPump()
    injection = SimulatedPump()
    daq = OutputAuthorizationAwareDaq()
    control = DeviceControlService(
        jacket_pump=jacket,
        injection_pump=injection,
        daq=daq,
        mode=RunMode.HARDWARE,
    )
    control.authorize_hardware(DeviceControlService.HARDWARE_CONFIRMATION)

    with pytest.raises(PermissionError, match="NI physical output"):
        control.connect()

    daq.authorize_output()
    control.connect()
    control.start()
    control.stop()

    assert control.status.state is ApplicationState.READY
    assert not control.status.hardware_authorized
    assert not daq.output_authorized
    with pytest.raises(PermissionError, match="explicit operator"):
        control.start()


def test_emergency_stop_is_latched_until_acknowledged() -> None:
    control, jacket, injection, daq = service()
    control.connect()
    control.start()

    control.emergency_stop()

    assert control.status.state is ApplicationState.FAULT
    assert control.status.fault_reason == "manual emergency stop"
    assert jacket.stop_requested and injection.stop_requested
    assert daq.safe_state_requested
    with pytest.raises(RuntimeError, match="ready state"):
        control.start()

    control.acknowledge_fault()
    assert control.status.state is ApplicationState.IDLE


def test_safe_state_logs_every_action_and_result(tmp_path: Path) -> None:
    logger = DiagnosticLogger(tmp_path / "application.html")
    logger.configure(enabled=True, categories=DiagnosticCategory)
    control = DeviceControlService(
        jacket_pump=SimulatedPump(),
        injection_pump=SimulatedPump(),
        daq=SimulatedDataAcquisition(),
        mode=RunMode.SIMULATION,
        diagnostics=logger,
    )
    control.connect()
    control.start()

    control.emergency_stop("PUMP_TELEMETRY_STALE")

    actions = [
        event
        for event in logger.events_after(0)
        if event.event_id == "SAFETY_ACTION"
    ]
    assert len(actions) == 3
    assert {dict(event.fields)["action_result"] for event in actions} == {"OK"}
    assert {dict(event.fields)["selected_fault_strategy"] for event in actions} == {
        "FULL_SAFE_STOP"
    }


def test_hardware_authorization_is_cleared_on_disconnect_and_fault_acknowledgement() -> None:
    control, *_ = service(RunMode.HARDWARE)
    control.authorize_hardware(DeviceControlService.HARDWARE_CONFIRMATION)
    control.connect()
    control.disconnect()
    assert not control.status.hardware_authorized

    control.authorize_hardware(DeviceControlService.HARDWARE_CONFIRMATION)
    control.connect()
    control.emergency_stop()
    control.acknowledge_fault()
    assert not control.status.hardware_authorized


class FailingPump(SimulatedPump):
    def connect(self) -> None:
        raise ConnectionError("simulated connection failure")


class StopFailingPump(SimulatedPump):
    def request_stop(self) -> None:
        raise ConnectionError("simulated STOP failure")


class DisconnectFailingPump(SimulatedPump):
    disconnect_attempted: bool = False

    def disconnect(self) -> None:
        self.disconnect_attempted = True
        raise ConnectionError("simulated disconnect failure")


def test_connection_failure_enters_fault_and_requests_safe_state() -> None:
    jacket = FailingPump()
    injection = SimulatedPump()
    daq = SimulatedDataAcquisition()
    control = DeviceControlService(
        jacket_pump=jacket, injection_pump=injection, daq=daq
    )

    with pytest.raises(ConnectionError, match="simulated connection failure"):
        control.connect()

    assert control.status.state is ApplicationState.FAULT
    assert jacket.stop_requested and injection.stop_requested
    assert daq.safe_state_requested


def test_safe_state_attempts_every_device_when_first_stop_fails() -> None:
    jacket = StopFailingPump()
    injection = SimulatedPump()
    daq = SimulatedDataAcquisition()
    control = DeviceControlService(
        jacket_pump=jacket, injection_pump=injection, daq=daq
    )
    control.connect()

    control.emergency_stop()

    assert control.status.state is ApplicationState.FAULT
    assert control.status.fault_reason is not None
    assert "jacket pump STOP failed" in control.status.fault_reason
    assert injection.stop_requested
    assert daq.safe_state_requested


def test_disconnect_attempts_both_pumps_when_first_close_fails() -> None:
    jacket = DisconnectFailingPump()
    injection = SimulatedPump()
    daq = SimulatedDataAcquisition()
    control = DeviceControlService(
        jacket_pump=jacket, injection_pump=injection, daq=daq
    )
    control.connect()

    with pytest.raises(RuntimeError, match="jacket pump disconnect failed"):
        control.disconnect()

    assert jacket.disconnect_attempted
    assert not injection.connected
    assert control.status.state is ApplicationState.FAULT
