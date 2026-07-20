from pathlib import Path

import pytest

from eor_control.application import ApplicationState, RunMode
from eor_control.calibration import LinearCalibration
from eor_control.device_testing import (
    DeviceTestReport,
    DeviceTestStatus,
    FunctionalDeviceTestSession,
    FunctionalTestDevice,
    FunctionalTestPreconditions,
    acquire_sensor_statistics,
    configuration_hash,
)
from eor_control.hardware import (
    ConnectionTestResult,
    DeviceConnectionResult,
    HardwareTestDevice,
)


def successful_connections() -> ConnectionTestResult:
    return ConnectionTestResult(
        tuple(DeviceConnectionResult(device, True, device.value) for device in HardwareTestDevice)
    )


def checklist() -> FunctionalTestPreconditions:
    return FunctionalTestPreconditions(True, True, True, True, True)


def session(
    *, mode: RunMode = RunMode.HARDWARE, runtime_running: bool = False
) -> tuple[FunctionalDeviceTestSession, list[str], list[float], list[float]]:
    stops: list[str] = []
    voltages: list[float] = []
    positions: list[float] = []
    test_session = FunctionalDeviceTestSession(
        run_mode=mode,
        application_state=lambda: ApplicationState.READY,
        runtime_running=lambda: runtime_running,
        pumps_running=lambda: False,
        active_fault=lambda: False,
        connection_result=successful_connections(),
        stop_pumps=lambda: stops.append("STOP ALL") or (),
        set_safe_output=lambda: stops.append("SAFE OUTPUT"),
        write_voltage=voltages.append,
        write_valve_percent=positions.append,
        report=DeviceTestReport.create(
            application_version="test", configuration_hash="abc"
        ),
        sleep_function=lambda _seconds: None,
    )
    return test_session, stops, voltages, positions


def test_simulation_mode_and_running_measurement_block_functional_io() -> None:
    simulated, stops, voltages, _ = session(mode=RunMode.SIMULATION)
    with pytest.raises(PermissionError, match="hardware mode"):
        simulated.begin(checklist())
    running, *_ = session(runtime_running=True)
    with pytest.raises(PermissionError, match="runtime is active"):
        running.begin(checklist())
    assert stops == []
    assert voltages == []


def test_sensor_statistics_uses_all_samples_and_actual_calibration() -> None:
    samples = iter((1.0, 2.0, 3.0, 4.0))
    statistics = acquire_sensor_statistics(
        lambda: next(samples),
        LinearCalibration(1.0, 5.0, -10.0, 30.0),
        sample_rate_hz=2.0,
        duration_seconds=2.0,
        sleep_function=lambda _seconds: None,
    )

    assert statistics.sample_count == 4
    assert statistics.mean_voltage == pytest.approx(2.5)
    assert statistics.standard_deviation_voltage == pytest.approx(1.1180339887)
    assert statistics.mean_value == pytest.approx(5.0)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_sensor_statistics_rejects_non_finite_input(invalid: float) -> None:
    with pytest.raises(ValueError, match="non-finite"):
        acquire_sensor_statistics(
            lambda: invalid,
            LinearCalibration(1.0, 5.0, 0.0, 40.0),
            sample_rate_hz=2.0,
            duration_seconds=1.0,
            sleep_function=lambda _seconds: None,
        )


def test_ao_failure_and_valve_observation_failure_use_central_abort() -> None:
    test_session, stops, voltages, positions = session()
    test_session.begin(checklist())
    failed_ao = test_session.run_ao_step(
        expected_voltage=1.0,
        measured_voltage=1.5,
        tolerance_voltage=0.1,
        confirmation=FunctionalDeviceTestSession.AO_CONFIRMATION,
    )
    assert failed_ao.status is DeviceTestStatus.FAILED
    assert voltages == [1.0]
    assert stops == ["STOP ALL", "SAFE OUTPUT"]

    valve_session, valve_stops, _, positions = session()
    valve_session.begin(checklist())
    for expected in (1.0, 1.0, 2.0, 3.0, 4.0, 5.0, 1.0):
        passed_ao = valve_session.run_ao_step(
            expected_voltage=expected,
            measured_voltage=expected,
            tolerance_voltage=0.1,
            confirmation=FunctionalDeviceTestSession.AO_CONFIRMATION,
        )
    assert passed_ao.status is DeviceTestStatus.PASSED
    failed_valve = valve_session.run_valve_step(
        output_percent=0.0,
        moved=True,
        correct_direction=False,
        stable=True,
        abnormal_noise=False,
    )
    assert failed_valve.status is DeviceTestStatus.FAILED
    assert positions == [0.0]
    assert valve_stops == ["STOP ALL", "SAFE OUTPUT"]


def test_skip_requires_reason_and_report_round_trips_json(tmp_path: Path) -> None:
    test_session, *_ = session()
    test_session.begin(checklist())
    with pytest.raises(ValueError, match="requires a reason"):
        test_session.skip(FunctionalTestDevice.JACKET_PUMP, reason="")
    test_session.skip(FunctionalTestDevice.JACKET_PUMP, reason="dry rig unavailable")
    test_session.complete()
    path = tmp_path / "device-test.json"
    test_session.report.save(path)

    restored = DeviceTestReport.load(path)

    assert restored.test_id == test_session.report.test_id
    assert restored.overall_status is DeviceTestStatus.SKIPPED
    assert restored.device_results[-1].status is DeviceTestStatus.SKIPPED
    assert configuration_hash({"b": 2, "a": 1}) == configuration_hash({"a": 1, "b": 2})
