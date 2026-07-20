import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from pathlib import Path
from statistics import fmean, pstdev
from time import sleep
from uuid import uuid4

from eor_control.application import ApplicationState, RunMode
from eor_control.calibration import LinearCalibration
from eor_control.diagnostics import DiagnosticCategory, DiagnosticLogger
from eor_control.hardware import ConnectionTestResult


class DeviceTestStatus(StrEnum):
    NOT_TESTED = "NOT_TESTED"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"
    SKIPPED = "SKIPPED"


class FunctionalTestDevice(StrEnum):
    SAFETY_PRECONDITIONS = "safety_preconditions"
    JACKET_PUMP = "jacket_pump"
    INJECTION_PUMP = "injection_pump"
    LINE_PRESSURE = "line_pressure"
    DIFFERENTIAL_PRESSURE = "differential_pressure"
    NI_ANALOG_OUTPUT = "ni_analog_output"
    HANBAY_VALVE = "hanbay_valve"
    EMERGENCY_AND_COMMUNICATION = "emergency_and_communication"


@dataclass(frozen=True, slots=True)
class SensorStatistics:
    sample_count: int
    current_voltage: float
    mean_voltage: float
    minimum_voltage: float
    maximum_voltage: float
    standard_deviation_voltage: float
    mean_value: float
    minimum_value: float
    maximum_value: float


@dataclass(slots=True)
class DeviceTestResult:
    device: str
    test_type: str
    status: DeviceTestStatus = DeviceTestStatus.NOT_TESTED
    started_at: str | None = None
    completed_at: str | None = None
    measurements: dict[str, object] = field(default_factory=dict)
    operator_confirmations: dict[str, object] = field(default_factory=dict)
    failure_reason: str | None = None
    diagnostic_event_references: list[int] = field(default_factory=list)


@dataclass(slots=True)
class DeviceTestReport:
    test_id: str
    started_at: str
    application_version: str
    configuration_hash: str
    overall_status: DeviceTestStatus = DeviceTestStatus.RUNNING
    completed_at: str | None = None
    device_results: list[DeviceTestResult] = field(default_factory=list)

    @classmethod
    def create(cls, *, application_version: str, configuration_hash: str) -> "DeviceTestReport":
        return cls(
            test_id=str(uuid4()),
            started_at=datetime.now(UTC).isoformat(),
            application_version=application_version,
            configuration_hash=configuration_hash,
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    @classmethod
    def load(cls, path: Path) -> "DeviceTestReport":
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["overall_status"] = DeviceTestStatus(payload["overall_status"])
        payload["device_results"] = [
            DeviceTestResult(
                **{
                    **item,
                    "status": DeviceTestStatus(item["status"]),
                }
            )
            for item in payload.get("device_results", [])
        ]
        return cls(**payload)


def configuration_hash(configuration: Mapping[str, object]) -> str:
    import hashlib

    encoded = json.dumps(
        configuration, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def acquire_sensor_statistics(
    read_voltage: Callable[[], float],
    calibration: LinearCalibration,
    *,
    sample_rate_hz: float = 10.0,
    duration_seconds: float = 10.0,
    sleep_function: Callable[[float], None] = sleep,
) -> SensorStatistics:
    if not isfinite(sample_rate_hz) or sample_rate_hz <= 0.0:
        raise ValueError("sample rate must be positive and finite")
    if not isfinite(duration_seconds) or duration_seconds <= 0.0:
        raise ValueError("sample duration must be positive and finite")
    sample_count = max(2, round(sample_rate_hz * duration_seconds))
    voltages: list[float] = []
    values: list[float] = []
    for index in range(sample_count):
        voltage = read_voltage()
        if not isfinite(voltage):
            raise ValueError("sensor returned a non-finite voltage")
        value = calibration.convert(voltage)
        voltages.append(voltage)
        values.append(value)
        if index + 1 < sample_count:
            sleep_function(1.0 / sample_rate_hz)
    return SensorStatistics(
        sample_count=sample_count,
        current_voltage=voltages[-1],
        mean_voltage=fmean(voltages),
        minimum_voltage=min(voltages),
        maximum_voltage=max(voltages),
        standard_deviation_voltage=pstdev(voltages),
        mean_value=fmean(values),
        minimum_value=min(values),
        maximum_value=max(values),
    )


@dataclass(frozen=True, slots=True)
class FunctionalTestPreconditions:
    system_depressurized: bool
    no_hazardous_fluid_motion: bool
    vent_path_safe: bool
    emergency_stop_accessible: bool
    operator_supervising: bool

    @property
    def all_confirmed(self) -> bool:
        return all(asdict(self).values())


class FunctionalDeviceTestSession:
    """Hardware-agnostic functional-test state machine with one central abort path."""

    AO_CONFIRMATION = "ENABLE GUIDED AO TEST"

    def __init__(
        self,
        *,
        run_mode: RunMode,
        application_state: Callable[[], ApplicationState],
        runtime_running: Callable[[], bool],
        pumps_running: Callable[[], bool],
        active_fault: Callable[[], bool],
        connection_result: ConnectionTestResult,
        stop_pumps: Callable[[], Sequence[str]],
        set_safe_output: Callable[[], None],
        write_voltage: Callable[[float], None],
        write_valve_percent: Callable[[float], None],
        report: DeviceTestReport,
        diagnostics: DiagnosticLogger | None = None,
        safe_output_voltage: float = 1.0,
        valve_dwell_seconds: float = 1.0,
        maximum_valve_rate_percent_per_second: float = 10.0,
        sleep_function: Callable[[float], None] = sleep,
        latch_fault: Callable[[str], None] | None = None,
    ) -> None:
        self._run_mode = run_mode
        self._application_state = application_state
        self._runtime_running = runtime_running
        self._pumps_running = pumps_running
        self._active_fault = active_fault
        self._connection_result = connection_result
        self._stop_pumps = stop_pumps
        self._set_safe_output = set_safe_output
        self._write_voltage = write_voltage
        self._write_valve_percent = write_valve_percent
        self.report = report
        self._diagnostics = diagnostics
        self._commands_enabled = False
        self._ao_passed = False
        self._ao_sequence = (safe_output_voltage, 1.0, 2.0, 3.0, 4.0, 5.0, safe_output_voltage)
        self._ao_step_index = 0
        self._valve_sequence = (0.0, 5.0, 10.0, 20.0, 10.0, 0.0)
        self._valve_step_index = 0
        self._valve_dwell_seconds = valve_dwell_seconds
        self._maximum_valve_rate = maximum_valve_rate_percent_per_second
        self._sleep = sleep_function
        self._latch_fault = latch_fault
        if valve_dwell_seconds < 0.0 or maximum_valve_rate_percent_per_second <= 0.0:
            raise ValueError("valve timing limits are invalid")

    @property
    def next_ao_voltage(self) -> float | None:
        return (
            self._ao_sequence[self._ao_step_index]
            if self._ao_step_index < len(self._ao_sequence)
            else None
        )

    @property
    def ao_complete(self) -> bool:
        return self._ao_passed

    @property
    def next_valve_percent(self) -> float | None:
        return (
            self._valve_sequence[self._valve_step_index]
            if self._valve_step_index < len(self._valve_sequence)
            else None
        )

    @property
    def valve_complete(self) -> bool:
        return self._valve_step_index == len(self._valve_sequence)

    def begin(self, preconditions: FunctionalTestPreconditions) -> None:
        failures: list[str] = []
        if self._run_mode is not RunMode.HARDWARE:
            failures.append("functional testing requires hardware mode")
        if self._application_state() is not ApplicationState.READY:
            failures.append("application must be in READY state")
        if self._runtime_running():
            failures.append("normal measurement runtime is active")
        if self._pumps_running():
            failures.append("a pump is in RUN state")
        if self._active_fault():
            failures.append("an active or latched safety fault exists")
        if not self._connection_result.all_successful:
            failures.append("current connection test is incomplete or failed")
        if not preconditions.all_confirmed:
            failures.append("operator safety checklist is incomplete")
        if failures:
            raise PermissionError("; ".join(failures))
        self._commands_enabled = True
        self._append_result(
            FunctionalTestDevice.SAFETY_PRECONDITIONS,
            "operator_checklist",
            DeviceTestStatus.PASSED,
            confirmations=asdict(preconditions),
        )
        self._log("BEGIN", "guided functional test started", level="WARNING")

    def run_ao_step(
        self,
        *,
        expected_voltage: float,
        measured_voltage: float,
        tolerance_voltage: float,
        confirmation: str,
    ) -> DeviceTestResult:
        self._require_commands()
        if confirmation != self.AO_CONFIRMATION:
            raise PermissionError("AO test confirmation did not match")
        if self._ao_step_index >= len(self._ao_sequence):
            raise RuntimeError("AO test sequence is already complete")
        required_voltage = self._ao_sequence[self._ao_step_index]
        if abs(expected_voltage - required_voltage) > 1e-9:
            raise ValueError(
                f"AO test step requires {required_voltage:g} V before {expected_voltage:g} V"
            )
        if not all(
            isfinite(value)
            for value in (expected_voltage, measured_voltage, tolerance_voltage)
        ) or tolerance_voltage < 0.0:
            raise ValueError("AO test values must be finite with nonnegative tolerance")
        result = self._new_result(FunctionalTestDevice.NI_ANALOG_OUTPUT, "voltage_step")
        try:
            self._write_voltage(expected_voltage)
            deviation = abs(measured_voltage - expected_voltage)
            result.measurements.update(
                expected_voltage=expected_voltage,
                measured_voltage=measured_voltage,
                deviation_voltage=deviation,
                tolerance_voltage=tolerance_voltage,
            )
            if deviation > tolerance_voltage:
                raise ValueError("measured AO voltage exceeds tolerance")
            result.status = DeviceTestStatus.PASSED
            self._ao_step_index += 1
            self._ao_passed = self._ao_step_index == len(self._ao_sequence)
        except Exception as error:
            result.status = DeviceTestStatus.FAILED
            result.failure_reason = str(error)
            self.abort(f"AO test failed: {error}")
        finally:
            result.completed_at = datetime.now(UTC).isoformat()
        return result

    def run_valve_step(
        self,
        *,
        output_percent: float,
        moved: bool,
        correct_direction: bool,
        stable: bool,
        abnormal_noise: bool,
    ) -> DeviceTestResult:
        self._require_commands()
        if not self._ao_passed:
            raise PermissionError("valve test requires a successful AO test")
        result = self._new_result(FunctionalTestDevice.HANBAY_VALVE, "position_step")
        result.operator_confirmations.update(
            moved=moved,
            correct_direction=correct_direction,
            stable=stable,
            abnormal_noise=abnormal_noise,
        )
        try:
            if self._valve_step_index >= len(self._valve_sequence):
                raise RuntimeError("guided valve sequence is already complete")
            required_percent = self._valve_sequence[self._valve_step_index]
            if abs(output_percent - required_percent) > 1e-9:
                raise ValueError(
                    f"guided valve step requires {required_percent:g}% before "
                    f"{output_percent:g}%"
                )
            previous_percent = (
                self._valve_sequence[self._valve_step_index - 1]
                if self._valve_step_index > 0
                else 0.0
            )
            rate_wait = (
                abs(output_percent - previous_percent) / self._maximum_valve_rate
            )
            self._sleep(max(self._valve_dwell_seconds, rate_wait))
            self._write_valve_percent(output_percent)
            result.measurements["commanded_percent"] = output_percent
            if not moved or not correct_direction or not stable or abnormal_noise:
                raise RuntimeError("valve motion or acoustic observation failed")
            result.status = DeviceTestStatus.PASSED
            self._valve_step_index += 1
            if self._valve_step_index == len(self._valve_sequence):
                self._set_safe_output()
        except Exception as error:
            result.status = DeviceTestStatus.FAILED
            result.failure_reason = str(error)
            self.abort(f"valve test failed: {error}")
        finally:
            result.completed_at = datetime.now(UTC).isoformat()
        return result

    def skip(self, device: FunctionalTestDevice, *, reason: str) -> DeviceTestResult:
        if not reason.strip():
            raise ValueError("skipping a test requires a reason")
        return self._append_result(
            device,
            "guided_test",
            DeviceTestStatus.SKIPPED,
            failure_reason=reason.strip(),
        )

    def record_result(
        self,
        device: FunctionalTestDevice,
        *,
        test_type: str,
        passed: bool,
        measurements: Mapping[str, object] | None = None,
        confirmations: Mapping[str, object] | None = None,
        failure_reason: str | None = None,
    ) -> DeviceTestResult:
        self._require_commands()
        status = DeviceTestStatus.PASSED if passed else DeviceTestStatus.FAILED
        result = self._append_result(
            device,
            test_type,
            status,
            confirmations=confirmations,
            failure_reason=failure_reason,
        )
        result.measurements.update(measurements or {})
        if not passed:
            self.abort(failure_reason or f"{device.value} test failed")
        return result

    def run_emergency_communication_test(
        self,
        *,
        emergency_stop_confirmed: bool,
        communication_loss_confirmed: bool,
    ) -> DeviceTestResult:
        self._require_commands()
        result = self._new_result(
            FunctionalTestDevice.EMERGENCY_AND_COMMUNICATION,
            "safe_state_verification",
        )
        result.operator_confirmations.update(
            emergency_stop_confirmed=emergency_stop_confirmed,
            communication_loss_confirmed=communication_loss_confirmed,
        )
        errors: list[str] = []
        try:
            errors.extend(self._stop_pumps())
        except Exception as error:
            errors.append(f"pump STOP failed: {error}")
        try:
            self._set_safe_output()
        except Exception as error:
            errors.append(f"safe output failed: {error}")
        passed = (
            emergency_stop_confirmed
            and communication_loss_confirmed
            and not errors
        )
        result.status = (
            DeviceTestStatus.PASSED if passed else DeviceTestStatus.FAILED
        )
        result.failure_reason = "; ".join(errors) or (
            None if passed else "operator did not confirm both safety tests"
        )
        result.completed_at = datetime.now(UTC).isoformat()
        if not passed:
            self._commands_enabled = False
            self.report.overall_status = DeviceTestStatus.FAILED
            if errors and self._latch_fault is not None:
                self._latch_fault("; ".join(errors))
        return result

    def abort(self, reason: str) -> tuple[str, ...]:
        self._commands_enabled = False
        errors: list[str] = []
        try:
            errors.extend(self._stop_pumps())
        except Exception as error:
            errors.append(f"pump STOP failed: {error}")
        try:
            self._set_safe_output()
        except Exception as error:
            errors.append(f"safe output failed: {error}")
        for result in self.report.device_results:
            if result.status is DeviceTestStatus.RUNNING:
                result.status = DeviceTestStatus.ABORTED
                result.failure_reason = reason
                result.completed_at = datetime.now(UTC).isoformat()
        self.report.overall_status = DeviceTestStatus.ABORTED
        self.report.completed_at = datetime.now(UTC).isoformat()
        self._log("ABORT", f"{reason}; safe-state errors={errors}", level="ERROR")
        if errors and self._latch_fault is not None:
            self._latch_fault("; ".join(errors))
        return tuple(errors)

    def complete(self) -> None:
        self._commands_enabled = False
        statuses = {result.status for result in self.report.device_results}
        self.report.overall_status = (
            DeviceTestStatus.FAILED
            if DeviceTestStatus.FAILED in statuses
            else (
                DeviceTestStatus.SKIPPED
                if DeviceTestStatus.SKIPPED in statuses
                else DeviceTestStatus.PASSED
            )
        )
        self.report.completed_at = datetime.now(UTC).isoformat()
        self._set_safe_output()

    def _new_result(
        self, device: FunctionalTestDevice, test_type: str
    ) -> DeviceTestResult:
        result = DeviceTestResult(
            device=device.value,
            test_type=test_type,
            status=DeviceTestStatus.RUNNING,
            started_at=datetime.now(UTC).isoformat(),
        )
        self.report.device_results.append(result)
        return result

    def _append_result(
        self,
        device: FunctionalTestDevice,
        test_type: str,
        status: DeviceTestStatus,
        *,
        confirmations: Mapping[str, object] | None = None,
        failure_reason: str | None = None,
    ) -> DeviceTestResult:
        now = datetime.now(UTC).isoformat()
        result = DeviceTestResult(
            device=device.value,
            test_type=test_type,
            status=status,
            started_at=now,
            completed_at=now,
            operator_confirmations=dict(confirmations or {}),
            failure_reason=failure_reason,
        )
        self.report.device_results.append(result)
        return result

    def _require_commands(self) -> None:
        if not self._commands_enabled:
            raise PermissionError("functional test commands are disabled")

    def _log(self, direction: str, message: str, *, level: str) -> None:
        if self._diagnostics is not None:
            self._diagnostics.emit(
                DiagnosticCategory.SYSTEM, direction, message, level=level
            )
