import shlex
from dataclasses import dataclass
from threading import Lock
from typing import TextIO

from eor_control.application import ApplicationState, DeviceControlService
from eor_control.calibration import LinearCalibration
from eor_control.control import (
    ControlMode,
    PidController,
    PidParameters,
    PressureSource,
    ValveController,
)
from eor_control.control_loop import ControlCycleResult, ControlLoop
from eor_control.domain import MeasurementRecord
from eor_control.measurement import MeasurementService
from eor_control.runtime import BackgroundControlRunner, RuntimeSettings
from eor_control.safety import SafetyLimits, SafetyMonitor
from eor_control.simulators import (
    SimulatedDataAcquisition,
    SimulatedPump,
    SimulatedValveActuator,
)


class DisabledMeasurementWriter:
    """Discard every record so terminal simulation can never persist measurements."""

    def write(self, record: MeasurementRecord) -> None:
        del record

    def close(self) -> None:
        return


@dataclass(frozen=True, slots=True)
class TerminalSnapshot:
    state: str
    mode: str
    stage: str
    control_mode: str
    valve_percent: float
    setpoint_bar: float
    fault_reason: str | None
    jacket_pressure_bar: float | None = None
    injection_pressure_bar: float | None = None
    line_pressure_bar: float | None = None
    differential_pressure_bar: float | None = None


class TerminalApplication:
    """Interactive terminal facade over the same safety and control services as the UI."""

    def __init__(self, output: TextIO) -> None:
        self._output = output
        self._jacket = SimulatedPump(pressure_bar=120.0, flow_ml_per_hour=10.0)
        self._injection = SimulatedPump(
            pressure_bar=100.0,
            flow_ml_per_hour=10.0,
            remaining_volume_ml=260.0,
        )
        self._daq = SimulatedDataAcquisition()
        self._daq.inputs.update(line_pressure=2.0, differential_pressure=1.5)
        self._valve = SimulatedValveActuator()
        self._devices = DeviceControlService(
            jacket_pump=self._jacket,
            injection_pump=self._injection,
            daq=self._daq,
        )
        measurement = MeasurementService(
            jacket_pump=self._jacket,
            injection_pump=self._injection,
            daq=self._daq,
            line_calibration=LinearCalibration(1.0, 5.0, 0.0, 400.0),
            differential_calibration=LinearCalibration(1.0, 5.0, 0.0, 40.0),
            safety_monitor=SafetyMonitor(SafetyLimits(400.0, 350.0, 50.0)),
            writer=DisabledMeasurementWriter(),
            persistence_enabled=False,
        )
        self._loop = ControlLoop(
            measurement=measurement,
            controller=ValveController(PidController(PidParameters(1.0, 0.05, 0.0))),
            actuator=self._valve,
        )
        self._latest_lock = Lock()
        self._latest_result: ControlCycleResult | None = None
        self._runner = BackgroundControlRunner(
            self._loop,
            on_cycle=self._cycle_completed,
            on_fault=self._runtime_fault,
        )
        self._settings = RuntimeSettings(
            active_stage="Terminál szimuláció",
            mode=ControlMode.MANUAL,
            manual_output_percent=0.0,
            source=PressureSource.INJECTION_PUMP,
            setpoint_bar=100.0,
            recording_interval_seconds=1.0,
        )

    def execute(self, command_line: str) -> bool:
        try:
            arguments = shlex.split(command_line)
        except ValueError as error:
            self._write(f"HIBA: {error}")
            return True
        if not arguments:
            return True
        command, *parameters = arguments
        normalized = command.casefold()
        try:
            if normalized in {"quit", "exit", "kilépés"}:
                return False
            if normalized in {"help", "súgó", "?"}:
                self._show_help()
            elif normalized == "status":
                self._show_status()
            elif normalized in {"connect", "kapcsolódás"}:
                self._require_no_parameters(parameters)
                self._devices.connect()
                self._write("OK: a szimulált eszközök csatlakoztatva.")
            elif normalized in {"start", "indítás"}:
                self._require_no_parameters(parameters)
                self._start()
            elif normalized in {"stop", "leállítás"}:
                self._require_no_parameters(parameters)
                self._stop()
            elif normalized in {"emergency-stop", "vészleállítás"}:
                self._emergency_stop(" ".join(parameters).strip())
            elif normalized in {"acknowledge", "nyugtázás"}:
                self._require_no_parameters(parameters)
                self._devices.acknowledge_fault()
                self._write("OK: a hiba nyugtázva; az állapot IDLE.")
            elif normalized in {"disconnect", "leválasztás"}:
                self._require_no_parameters(parameters)
                self._disconnect()
            elif normalized == "set":
                self._set(parameters)
            else:
                self._write(f"HIBA: ismeretlen parancs: {command!r}. Írd be: help")
        except (ConnectionError, PermissionError, RuntimeError, ValueError) as error:
            self._write(f"HIBA: {error}")
        return True

    def close(self) -> None:
        try:
            if self._runner.running:
                self._runner.stop()
            state = self._devices.status.state
            if state in (ApplicationState.READY, ApplicationState.RUNNING):
                self._devices.stop()
            self._devices.disconnect()
        finally:
            self._loop.close()

    def snapshot(self) -> TerminalSnapshot:
        status = self._devices.status
        with self._latest_lock:
            latest = self._latest_result
        record = latest.record if latest is not None else None
        return TerminalSnapshot(
            state=status.state.value,
            mode=status.mode.value,
            stage=self._settings.active_stage,
            control_mode=self._settings.mode.value,
            valve_percent=self._settings.manual_output_percent,
            setpoint_bar=self._settings.setpoint_bar,
            fault_reason=status.fault_reason,
            jacket_pressure_bar=(record.snapshot.jacket_pump.pressure_bar if record else None),
            injection_pressure_bar=(
                record.snapshot.injection_pump.pressure_bar if record else None
            ),
            line_pressure_bar=(record.snapshot.line_pressure_bar if record else None),
            differential_pressure_bar=(
                record.snapshot.differential_pressure_bar if record else None
            ),
        )

    def _start(self) -> None:
        self._devices.start()
        self._loop.reset_injected_volume_tracking()
        try:
            self._runner.start(self._settings)
        except Exception:
            self._devices.emergency_stop("terminal runtime start failed")
            raise
        self._write("OK: a szimulációs mérés elindult; adatmentés nincs.")

    def _stop(self) -> None:
        if self._runner.running:
            self._runner.stop()
        self._devices.stop()
        self._write("OK: mérés leállítva, biztonságos állapot kérve.")

    def _emergency_stop(self, reason: str) -> None:
        if self._runner.running:
            self._runner.stop()
        self._devices.emergency_stop(reason or "terminal emergency stop")
        self._write("RIASZTÁS: vészleállítás végrehajtva; az állapot FAULT.")

    def _disconnect(self) -> None:
        if self._runner.running:
            self._runner.stop()
        self._devices.disconnect()
        self._write("OK: az eszközök leválasztva.")

    def _set(self, parameters: list[str]) -> None:
        if not parameters:
            raise ValueError("használat: set manual|automatic|stage|interval ...")
        setting, *values = parameters
        normalized = setting.casefold()
        if normalized == "manual":
            if len(values) != 1:
                raise ValueError("használat: set manual <0-100>")
            output_percent = float(values[0])
            self._replace_settings(
                mode=ControlMode.MANUAL,
                manual_output_percent=output_percent,
            )
        elif normalized == "automatic":
            if len(values) != 2:
                raise ValueError("használat: set automatic <injection|line> <bar>")
            sources = {
                "injection": PressureSource.INJECTION_PUMP,
                "line": PressureSource.LINE_SENSOR,
            }
            try:
                source = sources[values[0].casefold()]
            except KeyError as error:
                raise ValueError("a nyomásforrás injection vagy line lehet") from error
            self._replace_settings(
                mode=ControlMode.AUTOMATIC,
                source=source,
                setpoint_bar=float(values[1]),
            )
        elif normalized == "stage":
            stage = " ".join(values).strip()
            if not stage:
                raise ValueError('használat: set stage "Szakasz neve"')
            self._replace_settings(active_stage=stage)
        elif normalized == "interval":
            if len(values) != 1:
                raise ValueError("használat: set interval <1-3600 másodperc>")
            self._replace_settings(recording_interval_seconds=float(values[0]))
        else:
            raise ValueError(f"ismeretlen beállítás: {setting!r}")
        self._write("OK: beállítás alkalmazva.")

    def _replace_settings(
        self,
        *,
        active_stage: str | None = None,
        mode: ControlMode | None = None,
        manual_output_percent: float | None = None,
        source: PressureSource | None = None,
        setpoint_bar: float | None = None,
        recording_interval_seconds: float | None = None,
    ) -> None:
        if setpoint_bar is not None and setpoint_bar < 0.0:
            raise ValueError("a nyomás célértéke nem lehet negatív")
        self._settings = RuntimeSettings(
            active_stage=active_stage or self._settings.active_stage,
            mode=mode or self._settings.mode,
            manual_output_percent=(
                self._settings.manual_output_percent
                if manual_output_percent is None
                else manual_output_percent
            ),
            source=source or self._settings.source,
            setpoint_bar=(
                self._settings.setpoint_bar if setpoint_bar is None else setpoint_bar
            ),
            recording_interval_seconds=(
                self._settings.recording_interval_seconds
                if recording_interval_seconds is None
                else recording_interval_seconds
            ),
        )
        if self._runner.running:
            self._runner.update_settings(self._settings)

    def _cycle_completed(self, result: ControlCycleResult) -> None:
        with self._latest_lock:
            self._latest_result = result

    def _runtime_fault(self, reason: str) -> None:
        if self._devices.status.state is not ApplicationState.FAULT:
            self._devices.emergency_stop(f"terminal control loop failed: {reason}")
        self._write(f"RIASZTÁS: vezérlési hiba: {reason}")

    def _show_status(self) -> None:
        snapshot = self.snapshot()
        self._write(
            f"Állapot={snapshot.state.upper()} mód={snapshot.mode} "
            f"szakasz={snapshot.stage!r} vezérlés={snapshot.control_mode}"
        )
        if snapshot.jacket_pressure_bar is not None:
            injection_pressure = snapshot.injection_pressure_bar
            line_pressure = snapshot.line_pressure_bar
            differential_pressure = snapshot.differential_pressure_bar
            assert injection_pressure is not None
            line_text = (
                "nincs hozzáadva"
                if line_pressure is None
                else f"{line_pressure:.2f} bar"
            )
            differential_text = (
                "nincs hozzáadva"
                if differential_pressure is None
                else f"{differential_pressure:.2f} bar"
            )
            self._write(
                f"Köpeny={snapshot.jacket_pressure_bar:.2f} bar; "
                f"besajtolás={injection_pressure:.2f} bar; "
                f"vonali={line_text}; "
                f"differenciál={differential_text}"
            )
        if snapshot.fault_reason:
            self._write(f"Hiba={snapshot.fault_reason}")

    def _show_help(self) -> None:
        self._write(
            "Parancsok:\n"
            "  status\n"
            "  connect | start | stop | disconnect\n"
            "  emergency-stop [ok]\n"
            "  acknowledge\n"
            "  set manual <0-100>\n"
            "  set automatic <injection|line> <bar>\n"
            '  set stage "Szakasz neve"\n'
            "  set interval <1-3600>\n"
            "  help | exit"
        )

    @staticmethod
    def _require_no_parameters(parameters: list[str]) -> None:
        if parameters:
            raise ValueError("ez a parancs nem fogad paramétert")

    def _write(self, message: str) -> None:
        self._output.write(f"{message}\n")
        self._output.flush()


def run_terminal(input_stream: TextIO, output_stream: TextIO) -> int:
    terminal = TerminalApplication(output_stream)
    output_stream.write(
        "AFKI EOR terminál — SZIMULÁCIÓ, NINCS ADATMENTÉS, NINCS FIZIKAI KIMENET\n"
        "Parancslista: help\n"
    )
    output_stream.flush()
    try:
        while True:
            if input_stream.isatty():
                output_stream.write("eor> ")
                output_stream.flush()
            line = input_stream.readline()
            if not line or not terminal.execute(line):
                break
    except KeyboardInterrupt:
        output_stream.write("\nMegszakítás; biztonságos leállítás...\n")
    finally:
        terminal.close()
    return 0
