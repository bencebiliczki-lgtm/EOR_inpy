import sys
from collections import deque
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from threading import Thread
from typing import cast

import pyqtgraph as pg  # type: ignore[import-untyped]
from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from eor_control.application import ApplicationState, DeviceControlService, RunMode
from eor_control.calibration import LinearCalibration
from eor_control.control import (
    ControlDirection,
    ControlMode,
    PidController,
    PidParameters,
    PressureSource,
    ValveController,
)
from eor_control.control_loop import ControlCycleResult, ControlLoop
from eor_control.data_management import (
    BackgroundNasSynchronizer,
    MeasurementTable,
    NasSyncQueue,
    ProjectMeasurementWriter,
    export_measurement_csv,
    export_measurement_excel,
    numeric_series,
    read_measurement_table,
    safe_filename,
)
from eor_control.diagnostics import DiagnosticCategory, DiagnosticLogger
from eor_control.domain import PumpStatus
from eor_control.hardware import (
    ConnectionTestResult,
    HardwareConfiguration,
    HardwareConnectionTester,
    HardwareDiscovery,
    PhysicalHardwareConnectionTester,
    discover_hardware,
)
from eor_control.isco import open_isco_pump
from eor_control.measurement import MeasurementService
from eor_control.ni import AnalogValveActuator, NidaqmxBackend, NidaqmxDataAcquisition
from eor_control.projects import MeasurementProject, MeasurementStage, ProjectRepository
from eor_control.pump_control import PumpControlService, PumpOperatingMode, PumpRole
from eor_control.runtime import BackgroundControlRunner, RuntimeSettings
from eor_control.safety import SafetyLimits, SafetyMonitor
from eor_control.simulators import (
    SimulatedDataAcquisition,
    SimulatedPump,
    SimulatedValveActuator,
)

LIGHT_STYLESHEET = """
QMainWindow, QWidget { background: #f5f7fa; color: #1f2933; }
QGroupBox { background: #ffffff; border: 1px solid #d7dee7; border-radius: 8px;
            margin-top: 10px; padding: 8px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
QPushButton { background: #e8eef6; border: 1px solid #c4cfdd; border-radius: 6px;
              padding: 7px 10px; }
QPushButton:hover { background: #dce7f3; }
QPushButton:disabled { color: #9aa5b1; background: #edf1f5; }
QComboBox, QDoubleSpinBox { background: #ffffff; border: 1px solid #b8c4d2;
                            border-radius: 5px; padding: 5px; min-height: 22px; }
QMenuBar, QMenu { background: #ffffff; color: #1f2933; }
"""

DARK_STYLESHEET = """
QMainWindow, QWidget { background: #11151a; color: #e6edf3; }
QGroupBox { background: #1b2129; border: 1px solid #35404d; border-radius: 8px;
            margin-top: 10px; padding: 8px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
QPushButton { background: #28323d; color: #e6edf3; border: 1px solid #465362;
              border-radius: 6px; padding: 7px 10px; }
QPushButton:hover { background: #334150; }
QPushButton:disabled { color: #65717e; background: #1d242c; }
QComboBox, QDoubleSpinBox { background: #202832; color: #e6edf3;
                            border: 1px solid #465362; border-radius: 5px;
                            padding: 5px; min-height: 22px; }
QMenuBar, QMenu { background: #1b2129; color: #e6edf3; }
QMenu::item:selected { background: #334150; }
"""


def application_icon_path() -> Path:
    bundle_directory = getattr(sys, "_MEIPASS", None)
    if isinstance(bundle_directory, str):
        return Path(bundle_directory) / "img" / "icon.png"
    return Path(__file__).resolve().parents[2] / "img" / "icon.png"


def application_root_path() -> Path:
    if bool(getattr(sys, "frozen", False)):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def application_icon() -> QIcon:
    path = application_icon_path()
    return QIcon(str(path)) if path.is_file() else QIcon()


DEFAULT_STAGE_TEMPLATES = (
    ("Hidegvizes mérés", "víz"),
    ("Melegvizes mérés", "víz"),
    ("Olajkiszorítás", "olaj"),
    ("Vegyszeres mérés", ""),
    ("Öblítés", ""),
)


def create_default_stages(
    repository: ProjectRepository, project_id: int
) -> MeasurementStage:
    stages = tuple(
        repository.add_stage(project_id, name, fluid=fluid)
        for name, fluid in DEFAULT_STAGE_TEMPLATES
    )
    return stages[0]


def stage_snapshots(project: MeasurementProject) -> list[dict[str, object]]:
    return [
        {
            "id": stage.id,
            "name": stage.name,
            "position": stage.position,
            "type": stage.name,
            "fluid": stage.fluid,
            "target_pressure_bar": stage.target_pressure_bar,
            "target_flow_ml_per_hour": stage.target_flow_ml_per_hour,
            "notes": stage.notes,
        }
        for stage in project.stages
    ]


class StageSettingsDialog(QDialog):
    STAGE_NAMES = (
        "Hidegvizes mérés",
        "Melegvizes mérés",
        "Olajkiszorítás",
        "Vegyszeres mérés",
        "Öblítés",
    )

    def __init__(
        self, stage: MeasurementStage | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Mérési szakasz")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QComboBox()
        self.name.setEditable(True)
        self.name.addItems(self.STAGE_NAMES)
        self.name.setCurrentText(stage.name if stage else self.STAGE_NAMES[0])
        self.fluid = QLineEdit(stage.fluid if stage else "")
        self.target_pressure = self._optional_value(" bar")
        self.target_flow = self._optional_value(" ml/h")
        if stage is not None and stage.target_pressure_bar is not None:
            self.target_pressure.setValue(stage.target_pressure_bar)
        if stage is not None and stage.target_flow_ml_per_hour is not None:
            self.target_flow.setValue(stage.target_flow_ml_per_hour)
        self.notes = QLineEdit(stage.notes if stage else "")
        form.addRow("Szakasz neve és típusa", self.name)
        form.addRow("Folyadék / vegyszer", self.fluid)
        form.addRow("Cél nyomás", self.target_pressure)
        form.addRow("Cél térfogatáram", self.target_flow)
        form.addRow("Megjegyzés", self.notes)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _optional_value(suffix: str) -> QDoubleSpinBox:
        field = QDoubleSpinBox()
        field.setRange(-1.0, 1000000.0)
        field.setSpecialValueText("Nincs megadva")
        field.setValue(-1.0)
        field.setSuffix(suffix)
        return field

    def values(self) -> dict[str, object]:
        return {
            "name": self.name.currentText(),
            "fluid": self.fluid.text(),
            "target_pressure_bar": (
                None if self.target_pressure.value() < 0.0 else self.target_pressure.value()
            ),
            "target_flow_ml_per_hour": (
                None if self.target_flow.value() < 0.0 else self.target_flow.value()
            ),
            "notes": self.notes.text(),
        }


class ProjectSettingsDialog(QDialog):
    def __init__(
        self,
        repository: ProjectRepository,
        *,
        selected_project_id: int | None,
        selected_stage_id: int | None,
        configuration: dict[str, object],
        calibration_snapshot: dict[str, object],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._configuration = configuration
        self._calibration_snapshot = calibration_snapshot
        self.setWindowTitle("Projektbeállítások")
        self.resize(560, 280)
        layout = QVBoxLayout(self)
        form = QGridLayout()
        self.project_selector = QComboBox()
        self.project_selector.setObjectName("dialog_project_selector")
        self.stage_selector = QComboBox()
        self.stage_selector.setObjectName("dialog_stage_selector")
        new_project = QPushButton("Új projekt")
        add_stage = QPushButton("Új szakasz")
        rename_stage = QPushButton("Szakasz szerkesztése")
        move_up = QPushButton("Fel")
        move_down = QPushButton("Le")
        delete_stage = QPushButton("Törlés")
        self.project_selector.currentIndexChanged.connect(self._reload_stages)
        new_project.clicked.connect(self._create_project)
        add_stage.clicked.connect(self._add_stage)
        rename_stage.clicked.connect(self._rename_stage)
        move_up.clicked.connect(lambda: self._move_stage(-1))
        move_down.clicked.connect(lambda: self._move_stage(1))
        delete_stage.clicked.connect(self._delete_stage)
        form.addWidget(QLabel("Projekt"), 0, 0)
        form.addWidget(self.project_selector, 0, 1, 1, 2)
        form.addWidget(new_project, 1, 1, 1, 2)
        form.addWidget(QLabel("Aktív mérési szakasz"), 2, 0)
        form.addWidget(self.stage_selector, 2, 1, 1, 2)
        form.addWidget(add_stage, 3, 1)
        form.addWidget(rename_stage, 3, 2)
        form.addWidget(move_up, 4, 0)
        form.addWidget(move_down, 4, 1)
        form.addWidget(delete_stage, 4, 2)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_if_complete)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._reload_projects(selected_project_id, selected_stage_id)

    @property
    def selected_project_id(self) -> int | None:
        value = self.project_selector.currentData()
        return value if isinstance(value, int) else None

    @property
    def selected_stage_id(self) -> int | None:
        value = self.stage_selector.currentData()
        return value if isinstance(value, int) else None

    def _reload_projects(
        self, selected_project_id: int | None = None, selected_stage_id: int | None = None
    ) -> None:
        self.project_selector.blockSignals(True)
        self.project_selector.clear()
        for project in self._repository.list_projects():
            self.project_selector.addItem(project.name, project.id)
        self.project_selector.blockSignals(False)
        if selected_project_id is not None:
            index = self.project_selector.findData(selected_project_id)
            if index >= 0:
                self.project_selector.setCurrentIndex(index)
        self._reload_stages(selected_stage_id=selected_stage_id)

    def _reload_stages(
        self, *_args: object, selected_stage_id: int | None = None
    ) -> None:
        self.stage_selector.clear()
        project_id = self.selected_project_id
        if project_id is None:
            return
        for stage in self._repository.list_stages(project_id):
            self.stage_selector.addItem(stage.name, stage.id)
        if selected_stage_id is not None:
            self.stage_selector.setCurrentIndex(
                self.stage_selector.findData(selected_stage_id)
            )

    def _create_project(self) -> None:
        name, accepted = QInputDialog.getText(self, "Új projekt", "Projekt neve")
        if not accepted:
            return
        notes, accepted = QInputDialog.getMultiLineText(
            self, "Új projekt", "Megjegyzések"
        )
        if not accepted:
            return
        try:
            project = self._repository.create_project(
                name=name,
                notes=notes,
                configuration=self._configuration,
                calibration_snapshot=self._calibration_snapshot,
            )
            stage = create_default_stages(self._repository, project.id)
            self._reload_projects(project.id, stage.id)
        except ValueError as error:
            QMessageBox.critical(self, "EOR hiba", str(error))

    def _add_stage(self) -> None:
        project_id = self.selected_project_id
        if project_id is None:
            QMessageBox.critical(self, "EOR hiba", "Előbb hozz létre vagy válassz projektet.")
            return
        dialog = StageSettingsDialog(parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                values = dialog.values()
                stage = self._repository.add_stage(
                    project_id,
                    str(values["name"]),
                    fluid=str(values["fluid"]),
                    target_pressure_bar=cast(float | None, values["target_pressure_bar"]),
                    target_flow_ml_per_hour=cast(
                        float | None, values["target_flow_ml_per_hour"]
                    ),
                    notes=str(values["notes"]),
                )
                self._reload_stages(selected_stage_id=stage.id)
            except ValueError as error:
                QMessageBox.critical(self, "EOR hiba", str(error))

    def _rename_stage(self) -> None:
        stage_id = self.selected_stage_id
        if stage_id is None:
            QMessageBox.critical(self, "EOR hiba", "Nincs átnevezhető mérési szakasz.")
            return
        stage = self._repository.get_stage(stage_id)
        dialog = StageSettingsDialog(stage, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                values = dialog.values()
                self._repository.update_stage(
                    stage_id,
                    name=str(values["name"]),
                    fluid=str(values["fluid"]),
                    target_pressure_bar=cast(float | None, values["target_pressure_bar"]),
                    target_flow_ml_per_hour=cast(
                        float | None, values["target_flow_ml_per_hour"]
                    ),
                    notes=str(values["notes"]),
                )
                self._reload_stages(selected_stage_id=stage_id)
            except ValueError as error:
                QMessageBox.critical(self, "EOR hiba", str(error))

    def _move_stage(self, offset: int) -> None:
        stage_id = self.selected_stage_id
        if stage_id is None:
            return
        self._repository.move_stage(stage_id, offset)
        self._reload_stages(selected_stage_id=stage_id)

    def _delete_stage(self) -> None:
        stage_id = self.selected_stage_id
        if stage_id is None:
            return
        answer = QMessageBox.question(
            self,
            "Szakasz törlése",
            f"Biztosan törlöd ezt a szakaszt: {self.stage_selector.currentText()}?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._repository.delete_stage(stage_id)
            self._reload_stages()

    def _accept_if_complete(self) -> None:
        if self.selected_project_id is None or self.selected_stage_id is None:
            QMessageBox.critical(
                self, "EOR hiba", "Válassz projektet és aktív mérési szakaszt."
            )
            return
        self.accept()


class RuntimeBridge(QObject):
    cycle_completed = Signal(object)
    fault_raised = Signal(str)


class DeviceTestBridge(QObject):
    succeeded = Signal(object)
    failed = Signal(str)


class EditableSelectionComboBox(QComboBox):
    """Editable dropdown retaining the small QLineEdit API used by this dialog."""

    def __init__(self, value: str = "") -> None:
        super().__init__()
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setText(value)

    def text(self) -> str:
        return self.currentText()

    def setText(self, value: str) -> None:
        self.setCurrentText(value)


class DeviceSettingsDialog(QDialog):
    def __init__(
        self,
        tester: HardwareConnectionTester,
        *,
        settings: QSettings,
        current_mode: RunMode,
        discoverer: Callable[[], HardwareDiscovery] = discover_hardware,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._tester = tester
        self._settings = settings
        self._discoverer = discoverer
        self._test_succeeded = False
        self._configuration: HardwareConfiguration | None = None
        self.setWindowTitle("Eszközbeállítások")
        self.resize(720, 820)
        layout = QVBoxLayout(self)
        self._mode_label = QLabel(f"Jelenlegi mód: {current_mode.value.upper()}")
        self._mode_label.setObjectName("device_mode_label")
        self._mode_label.setStyleSheet(
            "padding:10px;background:#fff3cd;color:#664d03;font-weight:700;border-radius:6px"
        )
        layout.addWidget(self._mode_label)
        channel_help = QLabel(
            "A fizikai NI-csatornákat a felhasználó adja meg (például Dev1/ai0). "
            "A négy csatornának különbözőnek kell lennie; a három bemenetet a "
            "kapcsolatpróba ténylegesen beolvassa."
        )
        channel_help.setWordWrap(True)
        channel_help.setStyleSheet("padding:8px;color:#66788a")
        layout.addWidget(channel_help)
        form = QFormLayout()
        self.jacket_port = EditableSelectionComboBox(self._stored("jacket_port", "COM3"))
        self.jacket_id = self._integer_field("jacket_unit_id", 1, 0, 9)
        self.jacket_channel = self._channel_field("jacket_channel", "A")
        self.injection_port = EditableSelectionComboBox(
            self._stored("injection_port", "COM4")
        )
        self.injection_id = self._integer_field("injection_unit_id", 2, 0, 9)
        self.injection_channel = self._channel_field("injection_channel", "A")
        self.baud_rate = QComboBox()
        for baud in (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200):
            self.baud_rate.addItem(str(baud), baud)
        baud_index = self.baud_rate.findData(self._stored_int("baud_rate", 9600))
        self.baud_rate.setCurrentIndex(max(0, baud_index))
        self.line_channel = EditableSelectionComboBox(
            self._stored("line_pressure_channel", "Dev1/ai0")
        )
        self.delta_channel = EditableSelectionComboBox(
            self._stored("differential_pressure_channel", "Dev1/ai1")
        )
        self.valve_channel = EditableSelectionComboBox(
            self._stored("valve_output_channel", "Dev1/ao0")
        )
        self.terminal_configuration = QComboBox()
        for label, value in (
            ("Automatikus / eszköz alapérték", "DEFAULT"),
            ("RSE – földelt egyvégű", "RSE"),
            ("NRSE – nem földelt egyvégű", "NRSE"),
            ("Differenciális", "DIFFERENTIAL"),
            ("Pszeudodifferenciális", "PSEUDODIFFERENTIAL"),
        ):
            self.terminal_configuration.addItem(label, value)
        terminal_index = self.terminal_configuration.findData(
            self._stored("ni_terminal_configuration", "DEFAULT")
        )
        self.terminal_configuration.setCurrentIndex(max(0, terminal_index))
        self.pump_cabling_notes = QLineEdit(self._stored("pump_cabling_notes", ""))
        self.ni_wiring_notes = QLineEdit(self._stored("ni_wiring_notes", ""))
        self.safe_voltage = self._voltage_field("safe_output_voltage", 1.0)
        self.zero_voltage = self._voltage_field("valve_zero_percent_voltage", 1.0)
        self.hundred_voltage = self._voltage_field(
            "valve_hundred_percent_voltage", 5.0
        )
        fields = (
            ("Köpenypumpa COM-port", self.jacket_port),
            ("Köpenypumpa DASNET ID", self.jacket_id),
            ("Köpenypumpa csatorna", self.jacket_channel),
            ("Besajtolópumpa COM-port", self.injection_port),
            ("Besajtolópumpa DASNET ID", self.injection_id),
            ("Besajtolópumpa csatorna", self.injection_channel),
            ("Baud rate", self.baud_rate),
            ("Vonali nyomás NI csatorna", self.line_channel),
            ("Differenciálnyomás NI csatorna", self.delta_channel),
            ("Szelep NI kimenet", self.valve_channel),
            ("NI bemeneti bekötési mód", self.terminal_configuration),
            ("Pumpakábelezés megjegyzése", self.pump_cabling_notes),
            ("NI bekötés/földelés megjegyzése", self.ni_wiring_notes),
            ("Safe-state feszültség", self.safe_voltage),
            ("Szelep 0% feszültség", self.zero_voltage),
            ("Szelep 100% feszültség", self.hundred_voltage),
        )
        for label, widget in fields:
            form.addRow(label, widget)
        discovery_row = QHBoxLayout()
        self._discovery_status = QLabel()
        self._discovery_status.setWordWrap(True)
        refresh_button = QPushButton("Portok és NI-csatornák frissítése")
        refresh_button.clicked.connect(self._refresh_hardware_choices)
        discovery_row.addWidget(refresh_button)
        discovery_row.addWidget(self._discovery_status, 1)
        form.addRow("Eszközfelderítés", discovery_row)
        layout.addLayout(form)
        validation = QGroupBox("Helyszíni validáció felhasználói adatai")
        validation_form = QFormLayout(validation)
        self.supervised_test_minutes = self._integer_field(
            "supervised_test_minutes", 60, 1, 1440
        )
        self.supervised_test_minutes.setSuffix(" perc")
        self.cable_disconnect_test = QCheckBox("Sikeresen elvégezve")
        self.cable_disconnect_test.setChecked(
            self._stored_bool("cable_disconnect_test_completed", False)
        )
        self.emergency_stop_test = QCheckBox("Sikeresen elvégezve")
        self.emergency_stop_test.setChecked(
            self._stored_bool("emergency_stop_test_completed", False)
        )
        self.supervised_test = QCheckBox("Sikeresen elvégezve")
        self.supervised_test.setChecked(
            self._stored_bool("supervised_test_completed", False)
        )
        validation_form.addRow("Felügyelt próba előírt időtartama", self.supervised_test_minutes)
        validation_form.addRow(
            "Kábelkihúzási/kommunikációvesztési próba",
            self.cable_disconnect_test,
        )
        validation_form.addRow("Vészleállítás fizikai próbája", self.emergency_stop_test)
        validation_form.addRow("Felügyelt kommunikációs próba", self.supervised_test)
        layout.addWidget(validation)
        calibration_help = QLabel(
            "A differenciálnyomás-érzékelő tényleges feszültség–bar tartományát a "
            "Beállítások → Kalibráció és biztonság ablakban a felhasználó adja meg."
        )
        calibration_help.setWordWrap(True)
        layout.addWidget(calibration_help)
        save_button = QPushButton("Eszköz- és csatornabeállítások mentése")
        save_button.clicked.connect(self._save_only)
        layout.addWidget(save_button)
        self._test_button = QPushButton("Kapcsolatok tesztelése (csak olvasás)")
        self._test_button.clicked.connect(self._start_test)
        layout.addWidget(self._test_button)
        self._result_label = QLabel("A hardvermód aktiválásához sikeres kapcsolatpróba szükséges.")
        self._result_label.setWordWrap(True)
        layout.addWidget(self._result_label)
        self._activate_button = QPushButton("HARDVER mód aktiválása")
        self._activate_button.setEnabled(False)
        self._activate_button.clicked.connect(self._activate)
        layout.addWidget(self._activate_button)
        cancel = QPushButton("Mégse")
        cancel.clicked.connect(self.reject)
        layout.addWidget(cancel)
        self._bridge = DeviceTestBridge(self)
        self._bridge.succeeded.connect(self._test_passed)
        self._bridge.failed.connect(self._test_failed)
        self._refresh_hardware_choices()

    @property
    def configuration(self) -> HardwareConfiguration | None:
        return self._configuration

    def _stored(self, key: str, default: str) -> str:
        return str(self._settings.value(f"hardware/{key}", default))

    def _stored_int(self, key: str, default: int) -> int:
        try:
            return int(str(self._settings.value(f"hardware/{key}", default)))
        except (TypeError, ValueError):
            return default

    def _stored_float(self, key: str, default: float) -> float:
        try:
            return float(str(self._settings.value(f"hardware/{key}", default)))
        except (TypeError, ValueError):
            return default

    def _stored_bool(self, key: str, default: bool) -> bool:
        value = self._settings.value(f"hardware/{key}", default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _integer_field(self, key: str, default: int, minimum: int, maximum: int) -> QSpinBox:
        field = QSpinBox()
        field.setRange(minimum, maximum)
        field.setValue(self._stored_int(key, default))
        return field

    def _channel_field(self, key: str, default: str) -> QComboBox:
        field = QComboBox()
        for channel in ("A", "B", "C", "D"):
            field.addItem(channel)
        field.setCurrentText(self._stored(key, default))
        return field

    def _voltage_field(self, key: str, default: float) -> QDoubleSpinBox:
        field = QDoubleSpinBox()
        field.setRange(1.0, 5.0)
        field.setDecimals(4)
        field.setSuffix(" V")
        field.setValue(self._stored_float(key, default))
        return field

    @staticmethod
    def _replace_choices(field: QComboBox, choices: tuple[str, ...]) -> None:
        selected = field.currentText().strip()
        field.clear()
        field.addItems(choices)
        if selected and field.findText(selected, Qt.MatchFlag.MatchFixedString) < 0:
            field.addItem(selected)
        field.setCurrentText(selected or (choices[0] if choices else ""))

    def _refresh_hardware_choices(self) -> None:
        try:
            discovery = self._discoverer()
        except Exception as error:
            self._discovery_status.setText(f"A felderítés sikertelen: {error}")
            self._discovery_status.setStyleSheet("color:#b00020")
            return
        self._replace_choices(self.jacket_port, discovery.serial_ports)
        self._replace_choices(self.injection_port, discovery.serial_ports)
        self._replace_choices(self.line_channel, discovery.ni_input_channels)
        self._replace_choices(self.delta_channel, discovery.ni_input_channels)
        self._replace_choices(self.valve_channel, discovery.ni_output_channels)
        summary = (
            f"{len(discovery.serial_ports)} COM-port, "
            f"{len(discovery.ni_input_channels)} NI bemenet, "
            f"{len(discovery.ni_output_channels)} NI kimenet"
        )
        if discovery.warnings:
            summary = f"{summary}. " + " ".join(discovery.warnings)
            self._discovery_status.setStyleSheet("color:#8a5a00")
        else:
            self._discovery_status.setStyleSheet("color:#1b7f3a")
        self._discovery_status.setText(summary)

    def _read_configuration(self) -> HardwareConfiguration:
        return HardwareConfiguration(
            jacket_port=self.jacket_port.text(),
            jacket_unit_id=self.jacket_id.value(),
            jacket_channel=self.jacket_channel.currentText(),
            injection_port=self.injection_port.text(),
            injection_unit_id=self.injection_id.value(),
            injection_channel=self.injection_channel.currentText(),
            baud_rate=int(self.baud_rate.currentData()),
            line_pressure_channel=self.line_channel.text(),
            differential_pressure_channel=self.delta_channel.text(),
            valve_output_channel=self.valve_channel.text(),
            safe_output_voltage=self.safe_voltage.value(),
            valve_zero_percent_voltage=self.zero_voltage.value(),
            valve_hundred_percent_voltage=self.hundred_voltage.value(),
            ni_terminal_configuration=str(self.terminal_configuration.currentData()),
            pump_cabling_notes=self.pump_cabling_notes.text().strip(),
            ni_wiring_notes=self.ni_wiring_notes.text().strip(),
            supervised_test_minutes=self.supervised_test_minutes.value(),
            cable_disconnect_test_completed=self.cable_disconnect_test.isChecked(),
            emergency_stop_test_completed=self.emergency_stop_test.isChecked(),
            supervised_test_completed=self.supervised_test.isChecked(),
        )

    def _start_test(self) -> None:
        try:
            configuration = self._read_configuration()
        except ValueError as error:
            self._test_failed(str(error))
            return
        self._configuration = configuration
        self._store_configuration(configuration)
        self._test_succeeded = False
        self._activate_button.setEnabled(False)
        self._test_button.setEnabled(False)
        self._result_label.setText("Kapcsolatpróba folyamatban…")

        def execute() -> None:
            try:
                result = self._tester.test(configuration)
            except Exception as error:
                self._bridge.failed.emit(str(error))
            else:
                self._bridge.succeeded.emit(result)

        Thread(target=execute, name="eor-device-test", daemon=True).start()

    def _test_passed(self, result: object) -> None:
        if not isinstance(result, ConnectionTestResult):
            self._test_failed("érvénytelen kapcsolatpróba-eredmény")
            return
        self._test_succeeded = True
        self._test_button.setEnabled(True)
        self._activate_button.setEnabled(True)
        self._result_label.setText(
            "SIKERES KAPCSOLATPRÓBA\n"
            f"Köpenypumpa: {result.jacket_pump}\n"
            f"Besajtolópumpa: {result.injection_pump}\n"
            f"Vonali AI: {result.line_voltage:.4f} V\n"
            f"Differenciál AI: {result.differential_voltage:.4f} V"
        )
        self._result_label.setStyleSheet("color:#1b7f3a;font-weight:700")

    def _test_failed(self, message: str) -> None:
        self._test_succeeded = False
        self._test_button.setEnabled(True)
        self._activate_button.setEnabled(False)
        self._result_label.setText(f"SIKERTELEN KAPCSOLATPRÓBA: {message}")
        self._result_label.setStyleSheet("color:#b00020;font-weight:700")

    def _activate(self) -> None:
        if not self._test_succeeded or self._configuration is None:
            self._test_failed("előbb sikeres kapcsolatpróba szükséges")
            return
        self._store_configuration(self._configuration)
        self.accept()

    def _save_only(self) -> None:
        try:
            configuration = self._read_configuration()
        except ValueError as error:
            self._test_failed(str(error))
            return
        self._configuration = configuration
        self._store_configuration(configuration)
        self._result_label.setText(
            "Az eszköz- és NI-csatornabeállítások elmentve. "
            "Hardvermódhoz még sikeres kapcsolatpróba szükséges."
        )
        self._result_label.setStyleSheet("color:#1b7f3a;font-weight:700")

    def _store_configuration(self, configuration: HardwareConfiguration) -> None:
        for key, value in configuration.to_settings().items():
            self._settings.setValue(f"hardware/{key}", value)
        self._settings.sync()


class PumpControlDialog(QDialog):
    def __init__(self, service: PumpControlService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._buttons: list[QPushButton] = []
        self._status_labels: dict[PumpRole, QLabel] = {}
        self._modes: dict[PumpRole, QComboBox] = {}
        self._targets: dict[PumpRole, QDoubleSpinBox] = {}
        self._command_bridge = DeviceTestBridge(self)
        self._command_bridge.succeeded.connect(self._command_succeeded)
        self._command_bridge.failed.connect(self._command_failed)
        self.setWindowTitle("Felügyelt ISCO pumpavezérlés")
        self.resize(760, 520)
        layout = QVBoxLayout(self)
        warning = QLabel(
            "HARDVER MÓD — A RUN/STOP/REMOTE parancsok fizikai pumpákra kerülnek."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet(
            "padding:10px;background:#b00020;color:white;font-weight:800;border-radius:6px"
        )
        layout.addWidget(warning)
        pumps = QGridLayout()
        for column, role in enumerate(PumpRole):
            pumps.addWidget(self._pump_panel(role), 0, column)
        layout.addLayout(pumps)
        refresh = self._button("Állapotok frissítése", self._refresh_statuses)
        stop_all = self._button("MINDKÉT PUMPA STOP", self._stop_all)
        stop_all.setStyleSheet(
            "background:#b00020;color:white;font-weight:800;padding:10px"
        )
        layout.addWidget(refresh)
        layout.addWidget(stop_all)
        close = QPushButton("Bezárás")
        close.clicked.connect(self.accept)
        layout.addWidget(close)

    def _pump_panel(self, role: PumpRole) -> QGroupBox:
        title = "Köpenypumpa" if role is PumpRole.JACKET else "Besajtolópumpa"
        box = QGroupBox(title)
        form = QFormLayout(box)
        status = QLabel("Nincs lekérdezve")
        status.setWordWrap(True)
        self._status_labels[role] = status
        mode = QComboBox()
        mode.addItem("Állandó térfogatáram (ml/min)", PumpOperatingMode.CONSTANT_FLOW)
        mode.addItem("Állandó nyomás (bar)", PumpOperatingMode.CONSTANT_PRESSURE)
        self._modes[role] = mode
        target = QDoubleSpinBox()
        target.setRange(0.0, 10000.0)
        target.setDecimals(5)
        target.setValue(1.0)
        self._targets[role] = target
        form.addRow("Állapot", status)
        form.addRow("Üzemmód", mode)
        form.addRow("Célérték", target)
        form.addRow(self._button("REMOTE", lambda: self._remote(role)))
        form.addRow(self._button("Mód és célérték beállítása", lambda: self._configure(role)))
        form.addRow(self._button("RUN", lambda: self._run(role)))
        form.addRow(self._button("STOP", lambda: self._stop(role)))
        form.addRow(self._button("CLEAR", lambda: self._clear(role)))
        form.addRow(self._button("LOCAL", lambda: self._local(role)))
        return box

    def _button(self, text: str, callback: Callable[[], None]) -> QPushButton:
        button = QPushButton(text)
        button.clicked.connect(callback)
        self._buttons.append(button)
        return button

    def _execute(self, operation: Callable[[], object], success_message: str) -> None:
        for button in self._buttons:
            button.setEnabled(False)

        def execute() -> None:
            try:
                result = operation()
            except Exception as error:
                self._command_bridge.failed.emit(str(error))
            else:
                self._command_bridge.succeeded.emit((success_message, result))

        Thread(target=execute, name="eor-pump-command", daemon=True).start()

    def _command_succeeded(self, payload: object) -> None:
        for button in self._buttons:
            button.setEnabled(True)
        if not isinstance(payload, tuple) or len(payload) != 2:
            return
        _, result = payload
        if isinstance(result, dict):
            for role, status in result.items():
                if isinstance(role, PumpRole) and isinstance(status, PumpStatus):
                    self._status_labels[role].setText(
                        f"{status.pressure_bar:.2f} bar | "
                        f"{status.flow_ml_per_hour:.3f} ml/h | "
                        f"{status.remaining_volume_ml:.3f} ml"
                    )

    def _command_failed(self, message: str) -> None:
        for button in self._buttons:
            button.setEnabled(True)
        QMessageBox.critical(self, "Pumpavezérlési hiba", message)

    def _remote(self, role: PumpRole) -> None:
        self._execute(lambda: self._service.enter_remote(role), f"{role.value} REMOTE")

    def _configure(self, role: PumpRole) -> None:
        mode = PumpOperatingMode(self._modes[role].currentData())
        target = self._targets[role].value()
        self._execute(
            lambda: self._service.configure(role, mode, target),
            f"{role.value} configured",
        )

    def _run(self, role: PumpRole) -> None:
        expected = (
            PumpControlService.RUN_JACKET_CONFIRMATION
            if role is PumpRole.JACKET
            else PumpControlService.RUN_INJECTION_CONFIRMATION
        )
        confirmation, accepted = QInputDialog.getText(
            self, "Fizikai pumpa indítása", f"Írd be pontosan: {expected}"
        )
        if accepted:
            self._execute(
                lambda: self._service.run(role, confirmation), f"{role.value} RUN"
            )

    def _stop(self, role: PumpRole) -> None:
        self._execute(lambda: self._service.stop(role), f"{role.value} STOP")

    def _clear(self, role: PumpRole) -> None:
        self._execute(lambda: self._service.clear(role), f"{role.value} CLEAR")

    def _local(self, role: PumpRole) -> None:
        self._execute(lambda: self._service.return_local(role), f"{role.value} LOCAL")

    def _stop_all(self) -> None:
        def operation() -> None:
            errors = self._service.stop_all()
            if errors:
                raise RuntimeError("; ".join(errors))

        self._execute(operation, "STOP ALL")

    def _refresh_statuses(self) -> None:
        self._execute(self._service.statuses, "statuses refreshed")


class LoggingSettingsDialog(QDialog):
    CATEGORY_LABELS = {
        DiagnosticCategory.SYSTEM: "Rendszer és módváltás",
        DiagnosticCategory.RUNTIME: "Vezérlési runtime és watchdog",
        DiagnosticCategory.JACKET_PUMP: "Köpenypumpa DASNET",
        DiagnosticCategory.INJECTION_PUMP: "Besajtolópumpa DASNET",
        DiagnosticCategory.NI_LINE: "NI vonali nyomás bemenet",
        DiagnosticCategory.NI_DIFFERENTIAL: "NI differenciálnyomás bemenet",
        DiagnosticCategory.NI_VALVE: "NI szelep analóg kimenet",
    }

    def __init__(
        self, logger: DiagnosticLogger, settings: QSettings, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._logger = logger
        self._settings = settings
        self.setWindowTitle("Naplózási beállítások")
        layout = QVBoxLayout(self)
        self.enabled = QCheckBox("Kommunikációs és fejlesztői naplózás engedélyezése")
        self.enabled.setChecked(logger.enabled)
        layout.addWidget(self.enabled)
        categories_box = QGroupBox("Naplózott területek")
        categories_layout = QVBoxLayout(categories_box)
        self.category_checks: dict[DiagnosticCategory, QCheckBox] = {}
        enabled_categories = logger.categories
        for category, label in self.CATEGORY_LABELS.items():
            checkbox = QCheckBox(label)
            checkbox.setChecked(category in enabled_categories)
            categories_layout.addWidget(checkbox)
            self.category_checks[category] = checkbox
        layout.addWidget(categories_box)
        path_label = QLabel("Logfájl: data/logs/communication.log")
        path_label.setStyleSheet("color:#66788a")
        layout.addWidget(path_label)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self) -> None:
        categories = {
            category
            for category, checkbox in self.category_checks.items()
            if checkbox.isChecked()
        }
        self._logger.configure(enabled=self.enabled.isChecked(), categories=categories)
        self._settings.setValue("logging/enabled", self.enabled.isChecked())
        self._settings.setValue(
            "logging/categories", [category.value for category in sorted(categories)]
        )
        self._settings.sync()
        self.accept()


class DeveloperViewDialog(QDialog):
    def __init__(self, logger: DiagnosticLogger, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._logger = logger
        self._last_sequence = 0
        self.setWindowTitle("Developer nézet — eszközkommunikáció")
        self.resize(1100, 620)
        layout = QVBoxLayout(self)
        controls = QGridLayout()
        self._filter = QComboBox()
        self._filter.addItem("Minden kategória", None)
        for category, label in LoggingSettingsDialog.CATEGORY_LABELS.items():
            self._filter.addItem(label, category.value)
        self._filter.currentIndexChanged.connect(self._rebuild)
        clear = QPushButton("Memórianapló törlése")
        clear.clicked.connect(self._clear)
        self._status = QLabel()
        controls.addWidget(QLabel("Szűrés"), 0, 0)
        controls.addWidget(self._filter, 0, 1)
        controls.addWidget(clear, 0, 2)
        controls.addWidget(self._status, 0, 3)
        layout.addLayout(controls)
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ("UTC idő", "Monotonic", "Szint", "Eszköz", "Irány", "Üzenet")
        )
        self._table.setAlternatingRowColors(True)
        self._table.setWordWrap(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._table)
        close = QPushButton("Bezárás")
        close.clicked.connect(self.accept)
        layout.addWidget(close)
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()
        self._refresh()

    def _refresh(self) -> None:
        events = self._logger.events_after(self._last_sequence)
        selected = self._filter.currentData()
        for event in events:
            self._last_sequence = max(self._last_sequence, event.sequence)
            if selected is not None and event.category.value != selected:
                continue
            row = self._table.rowCount()
            self._table.insertRow(row)
            values = (
                event.recorded_at.isoformat(),
                f"{event.monotonic_seconds:.6f}",
                event.level,
                event.category.value,
                event.direction,
                event.message,
            )
            for column, value in enumerate(values):
                self._table.setItem(row, column, QTableWidgetItem(value))
        state = "BE" if self._logger.enabled else "KI"
        self._status.setText(f"Naplózás: {state} | sorok: {self._table.rowCount()}")

    def _clear(self) -> None:
        self._logger.clear_memory()
        self._table.setRowCount(0)
        self._last_sequence = 0

    def _rebuild(self, *_args: object) -> None:
        self._table.setRowCount(0)
        self._last_sequence = 0
        self._refresh()


class DataManagementBridge(QObject):
    completed = Signal(str)
    failed = Signal(str)


class DataManagementDialog(QDialog):
    def __init__(
        self,
        *,
        source_path: Path,
        project_name: str,
        data_root: Path,
        synchronizer: BackgroundNasSynchronizer,
        settings: QSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._source_path = source_path
        self._project_name = project_name
        self._data_root = data_root
        self._synchronizer = synchronizer
        self._settings = settings
        self._bridge = DataManagementBridge(self)
        self._bridge.completed.connect(self._operation_completed)
        self._bridge.failed.connect(self._operation_failed)
        self.setWindowTitle("Adatkezelés és export")
        self.resize(650, 360)
        layout = QVBoxLayout(self)

        source_box = QGroupBox("Aktív projekt nyers adatai")
        source_layout = QFormLayout(source_box)
        source_layout.addRow("Projekt", QLabel(project_name))
        source_path_label = QLabel(str(source_path))
        source_path_label.setWordWrap(True)
        source_layout.addRow("Helyi fájl", source_path_label)
        layout.addWidget(source_box)

        export_box = QGroupBox("Felhasználói export")
        export_layout = QGridLayout(export_box)
        self.decimal_comma = QCheckBox("Tizedesvessző")
        self.decimal_comma.setChecked(True)
        self.delimiter = QComboBox()
        self.delimiter.addItem("Pontosvessző (;) ", ";")
        self.delimiter.addItem("Vessző (,)", ",")
        self.delimiter.addItem("Tabulátor", "\t")
        csv_button = QPushButton("CSV exportálása…")
        excel_button = QPushButton("Excel exportálása…")
        csv_button.clicked.connect(self._export_csv)
        excel_button.clicked.connect(self._export_excel)
        export_layout.addWidget(self.decimal_comma, 0, 0)
        export_layout.addWidget(QLabel("Oszlopelválasztó"), 0, 1)
        export_layout.addWidget(self.delimiter, 0, 2)
        export_layout.addWidget(csv_button, 1, 0, 1, 2)
        export_layout.addWidget(excel_button, 1, 2)
        layout.addWidget(export_box)

        nas_box = QGroupBox("Háttérben futó NAS-mentés")
        nas_layout = QGridLayout(nas_box)
        self.nas_enabled = QCheckBox("Automatikus NAS-szinkron engedélyezése")
        self.nas_enabled.setChecked(
            str(settings.value("nas/enabled", "false")).lower() in {"1", "true", "yes"}
        )
        self.nas_path = QLineEdit(str(settings.value("nas/target_path", "")))
        browse = QPushButton("Tallózás…")
        apply_nas = QPushButton("NAS-beállítások mentése")
        retry = QPushButton("Szinkronizálás most")
        browse.clicked.connect(self._browse_nas)
        apply_nas.clicked.connect(self._save_nas)
        retry.clicked.connect(self._retry_nas)
        nas_layout.addWidget(self.nas_enabled, 0, 0, 1, 3)
        nas_layout.addWidget(QLabel("NAS célmappa"), 1, 0)
        nas_layout.addWidget(self.nas_path, 1, 1)
        nas_layout.addWidget(browse, 1, 2)
        nas_layout.addWidget(apply_nas, 2, 1)
        nas_layout.addWidget(retry, 2, 2)
        self.nas_status = QLabel()
        nas_layout.addWidget(self.nas_status, 3, 0, 1, 3)
        layout.addWidget(nas_box)
        self._refresh_nas_status()

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)

    def _run_background(self, operation: Callable[[], object], success: str) -> None:
        def execute() -> None:
            try:
                operation()
            except Exception as error:
                self._bridge.failed.emit(str(error))
            else:
                self._bridge.completed.emit(success)

        Thread(target=execute, name="eor-data-operation", daemon=True).start()

    def _export_csv(self) -> None:
        default = str(
            self._source_path.parent / f"{safe_filename(self._project_name)}_export.csv"
        )
        destination, _ = QFileDialog.getSaveFileName(
            self, "CSV export", default, "CSV fájl (*.csv)"
        )
        if not destination:
            return
        delimiter = str(self.delimiter.currentData())
        self._run_background(
            lambda: export_measurement_csv(
                self._source_path,
                Path(destination),
                decimal_comma=self.decimal_comma.isChecked(),
                delimiter=delimiter,
            ),
            f"CSV export elkészült: {destination}",
        )

    def _export_excel(self) -> None:
        default = str(
            self._source_path.parent / f"{safe_filename(self._project_name)}.xlsx"
        )
        destination, _ = QFileDialog.getSaveFileName(
            self, "Excel export", default, "Excel munkafüzet (*.xlsx)"
        )
        if destination:
            self._run_background(
                lambda: export_measurement_excel(self._source_path, Path(destination)),
                f"Excel export elkészült: {destination}",
            )

    def _browse_nas(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "NAS célmappa", self.nas_path.text()
        )
        if directory:
            self.nas_path.setText(directory)

    def _save_nas(self) -> None:
        enabled = self.nas_enabled.isChecked()
        path_text = self.nas_path.text().strip()
        try:
            self._synchronizer.configure(
                enabled=enabled, target_root=Path(path_text) if path_text else None
            )
        except ValueError as error:
            self._operation_failed(str(error))
            return
        self._settings.setValue("nas/enabled", enabled)
        self._settings.setValue("nas/target_path", path_text)
        self._settings.sync()
        if enabled:
            for source in (self._data_root / "projects").rglob("*_raw.csv"):
                relative = source.relative_to(self._data_root)
                self._synchronizer.enqueue(source, Path(*relative.parts[1:]))
        self._refresh_nas_status()

    def _retry_nas(self) -> None:
        self._run_background(
            lambda: self._synchronizer.sync_pending_once(),
            "A NAS-szinkronizálási kísérlet befejeződött.",
        )

    def _operation_completed(self, message: str) -> None:
        self._refresh_nas_status()
        QMessageBox.information(self, "Adatkezelés", message)

    def _operation_failed(self, message: str) -> None:
        self._refresh_nas_status()
        QMessageBox.critical(self, "Adatkezelési hiba", message)

    def _refresh_nas_status(self) -> None:
        state = "bekapcsolva" if self._synchronizer.enabled else "kikapcsolva"
        self.nas_status.setText(
            f"NAS-szinkron: {state}; várakozó fájlok: {self._synchronizer.pending_count}"
        )


class MeasurementHistoryDialog(QDialog):
    SERIES = (
        ("jacket_pressure_bar", "Köpenynyomás", "#1565c0"),
        ("injection_pressure_bar", "Besajtolási nyomás", "#c62828"),
        ("line_pressure_bar", "Vonali nyomás", "#2e7d32"),
        ("differential_pressure_bar", "Differenciálnyomás", "#8e24aa"),
        ("jacket_flow_ml_per_hour", "Köpeny térfogatáram", "#00838f"),
        ("jacket_remaining_volume_ml", "Köpeny maradék térfogat", "#5c6bc0"),
        ("injection_flow_ml_per_hour", "Besajtolási térfogatáram", "#ef6c00"),
        ("injection_remaining_volume_ml", "Besajtolás maradék térfogat", "#d81b60"),
        ("injected_volume_ml", "Besajtolt térfogat", "#6d4c41"),
        ("valve_percent", "Szelep", "#546e7a"),
    )

    def __init__(
        self, source_path: Path, project_name: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._source_path = source_path
        self.setWindowTitle(f"Teljes mérés — {project_name}")
        self.resize(1400, 800)
        layout = QVBoxLayout(self)
        controls = QGridLayout()
        self._checks: dict[str, QCheckBox] = {}
        for index, (key, label, _color) in enumerate(self.SERIES):
            checkbox = QCheckBox(label)
            checkbox.setChecked(index < 4)
            checkbox.toggled.connect(self._refresh_plot)
            self._checks[key] = checkbox
            row, column = divmod(index, 5)
            controls.addWidget(checkbox, row, column)
        layout.addLayout(controls)

        range_row = QHBoxLayout()
        self._time_range = QComboBox()
        self._time_range.addItem("Teljes mérés", None)
        self._time_range.addItem("Utolsó 10 perc", 600.0)
        self._time_range.addItem("Utolsó 1 óra", 3600.0)
        self._time_range.addItem("Utolsó 6 óra", 21600.0)
        self._time_range.addItem("Egyéni időtartomány", "custom")
        self._custom_minutes = QDoubleSpinBox()
        self._custom_minutes.setRange(0.1, 100000.0)
        self._custom_minutes.setValue(60.0)
        self._custom_minutes.setSuffix(" perc")
        self._custom_minutes.setEnabled(False)
        self._auto_y = QCheckBox("Automatikus Y tengely")
        self._auto_y.setChecked(True)
        self._y_min = QDoubleSpinBox()
        self._y_min.setRange(-1000000.0, 1000000.0)
        self._y_min.setValue(0.0)
        self._y_max = QDoubleSpinBox()
        self._y_max.setRange(-1000000.0, 1000000.0)
        self._y_max.setValue(400.0)
        refresh = QPushButton("Adatok újratöltése")
        refresh.clicked.connect(self._load)
        self._time_range.currentIndexChanged.connect(self._range_changed)
        self._custom_minutes.valueChanged.connect(self._refresh_plot)
        self._auto_y.toggled.connect(self._axis_changed)
        self._y_min.valueChanged.connect(self._refresh_plot)
        self._y_max.valueChanged.connect(self._refresh_plot)
        for widget in (
            QLabel("Időtartomány"),
            self._time_range,
            self._custom_minutes,
            self._auto_y,
            QLabel("Y minimum"),
            self._y_min,
            QLabel("Y maximum"),
            self._y_max,
            refresh,
        ):
            range_row.addWidget(widget)
        layout.addLayout(range_row)

        self._plot = pg.PlotWidget(title="Teljes rögzített mérés")
        self._plot.setLabel("left", "Érték")
        self._plot.setLabel("bottom", "Eltelt idő", units="s")
        self._plot.showGrid(x=True, y=True, alpha=0.25)
        self._plot.setMouseEnabled(x=True, y=True)
        layout.addWidget(self._plot, stretch=1)
        self._status = QLabel()
        layout.addWidget(self._status)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self._table = MeasurementTable((), ())
        self._load()

    def _load(self) -> None:
        try:
            self._table = read_measurement_table(self._source_path)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "Mérési adatok", str(error))
            return
        self._status.setText(
            f"{len(self._table.rows)} rögzített minta — {self._source_path}"
        )
        self._refresh_plot()

    def _range_changed(self, *_args: object) -> None:
        self._custom_minutes.setEnabled(self._time_range.currentData() == "custom")
        self._refresh_plot()

    def _axis_changed(self, checked: bool) -> None:
        self._y_min.setEnabled(not checked)
        self._y_max.setEnabled(not checked)
        self._refresh_plot()

    def _elapsed_times(self) -> tuple[float, ...]:
        values: list[float] = []
        first: datetime | None = None
        for value in self._table.column("recorded_at_utc"):
            timestamp = datetime.fromisoformat(value)
            first = first or timestamp
            values.append((timestamp - first).total_seconds())
        return tuple(values)

    def _refresh_plot(self, *_args: object) -> None:
        self._plot.clear()
        if not self._table.rows:
            return
        times = self._elapsed_times()
        seconds = self._time_range.currentData()
        if seconds == "custom":
            seconds = self._custom_minutes.value() * 60.0
        minimum_time = times[-1] - float(seconds) if isinstance(seconds, float) else times[0]
        start = next((index for index, value in enumerate(times) if value >= minimum_time), 0)
        series = numeric_series(self._table, (item[0] for item in self.SERIES))
        self._plot.addLegend()
        for key, label, color in self.SERIES:
            if self._checks[key].isChecked():
                self._plot.plot(times[start:], series[key][start:], pen=color, name=label)
        if self._auto_y.isChecked():
            self._plot.enableAutoRange(axis="y")
        elif self._y_max.value() > self._y_min.value():
            self._plot.setYRange(self._y_min.value(), self._y_max.value(), padding=0.0)
        if isinstance(seconds, float):
            self._plot.setXRange(max(times[0], minimum_time), times[-1], padding=0.0)
        else:
            self._plot.enableAutoRange(axis="x")


class DashboardWindow(QMainWindow):
    def __init__(
        self,
        *,
        devices: DeviceControlService,
        control_loop: ControlLoop,
        valve: SimulatedValveActuator,
        projects: ProjectRepository,
        data_directory: Path,
        measurement_writer: ProjectMeasurementWriter,
        nas_sync: BackgroundNasSynchronizer,
    ) -> None:
        super().__init__()
        self._user_settings = QSettings("AFKI", "EORControl")
        self._devices = devices
        self._control_loop = control_loop
        self._valve = valve
        self._projects = projects
        self._data_directory = data_directory
        self._measurement_writer = measurement_writer
        self._nas_sync = nas_sync
        self._run_mode = RunMode.SIMULATION
        self._pump_control: PumpControlService | None = None
        self._measurement_time_origin: float | None = None
        self._diagnostics = DiagnosticLogger(
            data_directory / "logs" / "communication.log"
        )
        self._restore_logging_settings()
        self._restore_nas_settings()
        self.setWindowIcon(application_icon())
        self._times: deque[float] = deque(maxlen=6000)
        self._jacket_pressures: deque[float] = deque(maxlen=6000)
        self._injection_pressures: deque[float] = deque(maxlen=6000)
        self._injection_flows: deque[float] = deque(maxlen=6000)
        self._line_pressures: deque[float] = deque(maxlen=6000)
        self._runtime_bridge = RuntimeBridge(self)
        self._runtime_bridge.cycle_completed.connect(self._handle_cycle)
        self._runtime_bridge.fault_raised.connect(self._handle_runtime_fault)
        self._runtime = BackgroundControlRunner(
            control_loop,
            control_interval_seconds=0.1,
            watchdog_tolerance_seconds=0.05,
            on_cycle=self._runtime_bridge.cycle_completed.emit,
            on_fault=self._runtime_bridge.fault_raised.emit,
        )
        self._build_ui()
        self._build_menu()
        self._restore_theme()
        self._restore_control_settings()
        self._restore_project_selection()
        self._refresh_state()

    def _build_ui(self) -> None:
        self.setWindowTitle("AFKI EOR mérőrendszer — szimuláció")
        self.resize(1100, 720)
        root = QWidget()
        layout = QVBoxLayout(root)

        self._dashboard_mode_label = QLabel()
        self._dashboard_mode_label.setObjectName("dashboard_mode_label")
        self._dashboard_mode_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._dashboard_mode_label)
        self._refresh_mode_label()

        status_container = QWidget()
        status_container.setObjectName("status_sidebar")
        status_container.setMinimumWidth(230)
        status_container.setMaximumWidth(360)
        status_layout = QVBoxLayout(status_container)
        status_layout.setContentsMargins(4, 0, 8, 4)
        status_title = QLabel("ÉLŐ ÁLLAPOTOK")
        status_title.setStyleSheet("font-size:13px;font-weight:700;padding:4px")
        status_layout.addWidget(status_title)
        self._state_label = QLabel()
        self._jacket_label = QLabel("— bar")
        self._injection_label = QLabel("— bar")
        self._jacket_remaining_label = QLabel("Maradék folyadék: — ml")
        self._injection_remaining_label = QLabel("Maradék folyadék: — ml")
        self._injection_flow_label = QLabel("Besajtolási sebesség: — ml/h")
        self._injected_volume_label = QLabel("Mérés óta besajtolt: — ml")
        self._line_label = QLabel("— bar")
        self._delta_label = QLabel("— bar")
        self._valve_label = QLabel("— %")
        labels = (
            ("Rendszerállapot", self._state_label),
            ("Köpenypumpa", self._jacket_label),
            ("Besajtolópumpa", self._injection_label),
            ("Vonali nyomás", self._line_label),
            ("Differenciálnyomás", self._delta_label),
            ("Szelep", self._valve_label),
        )
        self._connection_labels: dict[str, QLabel] = {}
        connection_keys = (
            None,
            "jacket",
            "injection",
            "line_daq",
            "delta_daq",
            "valve",
        )
        for index, (title, value) in enumerate(labels):
            box = QGroupBox(title)
            box.setMinimumHeight(76)
            box_layout = QVBoxLayout(box)
            value.setStyleSheet("font-size: 20px; font-weight: 600")
            box_layout.addWidget(value)
            if title == "Köpenypumpa":
                self._jacket_remaining_label.setStyleSheet(
                    "color:#66788a;font-size:12px;font-weight:600"
                )
                box_layout.addWidget(self._jacket_remaining_label)
            elif title == "Besajtolópumpa":
                for detail in (
                    self._injection_remaining_label,
                    self._injection_flow_label,
                    self._injected_volume_label,
                ):
                    detail.setStyleSheet("color:#66788a;font-size:12px;font-weight:600")
                    box_layout.addWidget(detail)
            connection_key = connection_keys[index]
            if connection_key is not None:
                connection = QLabel("NINCS ADAT")
                connection.setStyleSheet("color:#66788a;font-size:11px;font-weight:600")
                box_layout.addWidget(connection)
                self._connection_labels[connection_key] = connection
            status_layout.addWidget(box)
        status_layout.addStretch(1)

        right_container = QWidget()
        right_container.setMinimumWidth(340)
        right_container.setMaximumWidth(440)
        right_layout = QVBoxLayout(right_container)

        controls = QGridLayout()
        self._connect_button = QPushButton("Csatlakozás")
        self._disconnect_button = QPushButton("Leválasztás")
        self._start_button = QPushButton("Mérés indítása")
        self._stop_button = QPushButton("Leállítás")
        self._acknowledge_button = QPushButton("Hiba nyugtázása")
        self._emergency_button = QPushButton("VÉSZLEÁLLÍTÁS")
        self._pump_control_button = QPushButton("Pumpavezérlés…")
        self._emergency_button.setStyleSheet(
            "background:#b00020;color:white;font-weight:700;padding:10px"
        )
        self._connect_button.clicked.connect(self._connect_devices)
        self._disconnect_button.clicked.connect(self._disconnect_devices)
        self._start_button.clicked.connect(self._start)
        self._stop_button.clicked.connect(self._stop)
        self._acknowledge_button.clicked.connect(self._acknowledge_fault)
        self._emergency_button.clicked.connect(self._emergency_stop)
        self._pump_control_button.clicked.connect(self._open_pump_control)
        for index, button in enumerate((
            self._connect_button,
            self._disconnect_button,
            self._start_button,
            self._stop_button,
            self._acknowledge_button,
            self._emergency_button,
            self._pump_control_button,
        )):
            row, column = divmod(index, 2)
            controls.addWidget(button, row, column)
        right_layout.addLayout(controls)

        project_box = QGroupBox("Mérési projekt és szakasz")
        project_layout = QGridLayout(project_box)
        self._project = QComboBox()
        self._project.setObjectName("project_selector")
        self._stage = QComboBox()
        self._stage.setObjectName("stage_selector")
        new_project = QPushButton("Új projekt")
        add_stage = QPushButton("Új szakasz")
        rename_stage = QPushButton("Szakasz átnevezése")
        self._project.currentIndexChanged.connect(self._reload_stages)
        self._stage.currentIndexChanged.connect(self._stage_changed)
        new_project.clicked.connect(self._create_project)
        add_stage.clicked.connect(self._add_stage)
        rename_stage.clicked.connect(self._rename_stage)
        project_layout.addWidget(QLabel("Projekt"), 0, 0)
        project_layout.addWidget(self._project, 0, 1, 1, 2)
        project_layout.addWidget(new_project, 1, 0, 1, 3)
        project_layout.addWidget(QLabel("Aktív szakasz"), 2, 0)
        project_layout.addWidget(self._stage, 2, 1, 1, 2)
        project_layout.addWidget(add_stage, 3, 0)
        project_layout.addWidget(rename_stage, 3, 1, 1, 2)
        right_layout.addWidget(project_box)
        project_box.setVisible(False)
        project_summary = QGroupBox("Aktív projekt")
        project_summary_layout = QFormLayout(project_summary)
        self._active_project_label = QLabel("Nincs kiválasztva")
        self._active_stage_label = QLabel("Nincs kiválasztva")
        open_projects = QPushButton("Projektkezelő megnyitása…")
        open_projects.clicked.connect(self._open_project_settings)
        project_summary_layout.addRow("Projekt", self._active_project_label)
        project_summary_layout.addRow("Szakasz", self._active_stage_label)
        project_summary_layout.addRow(open_projects)
        right_layout.addWidget(project_summary)

        settings = QGroupBox("Szelepvezérlés")
        form = QFormLayout(settings)
        self._mode = QComboBox()
        self._mode.addItem("Kézi", ControlMode.MANUAL)
        self._mode.addItem("Automata", ControlMode.AUTOMATIC)
        self._source = QComboBox()
        self._source.addItem("Besajtolópumpa", PressureSource.INJECTION_PUMP)
        self._source.addItem("Vonali nyomás", PressureSource.LINE_SENSOR)
        self._manual_output = QDoubleSpinBox()
        self._manual_output.setRange(0.0, 100.0)
        self._manual_output.setValue(25.0)
        self._manual_output.setSuffix(" %")
        self._setpoint = QDoubleSpinBox()
        self._setpoint.setRange(0.0, 400.0)
        self._setpoint.setValue(100.0)
        self._setpoint.setSuffix(" bar")
        self._recording_interval = QSpinBox()
        self._recording_interval.setRange(1, 3600)
        self._recording_interval.setValue(1)
        self._recording_interval.setSuffix(" s")
        self._kp = self._pid_spinbox(1.0)
        self._ki = self._pid_spinbox(0.05)
        self._kd = self._pid_spinbox(0.0)
        self._output_min = self._percent_spinbox(0.0)
        self._output_max = self._percent_spinbox(100.0)
        self._direction = QComboBox()
        self._direction.addItem("Közvetlen", ControlDirection.DIRECT)
        self._direction.addItem("Fordított", ControlDirection.REVERSE)
        apply_pid = QPushButton("PID beállítások alkalmazása")
        apply_pid.clicked.connect(self._apply_pid)
        form.addRow("Mód", self._mode)
        form.addRow("Nyomásforrás", self._source)
        form.addRow("Kézi kimenet", self._manual_output)
        form.addRow("Célérték", self._setpoint)
        form.addRow("Adatrögzítési időköz", self._recording_interval)
        form.addRow("PID ciklus", QLabel("100 ms (háttérszál)"))
        form.addRow("Kp", self._kp)
        form.addRow("Ki", self._ki)
        form.addRow("Kd", self._kd)
        form.addRow("Hatásirány", self._direction)
        form.addRow("Kimeneti minimum", self._output_min)
        form.addRow("Kimeneti maximum", self._output_max)
        form.addRow(apply_pid)
        for widget in (
            self._mode,
            self._source,
            self._manual_output,
            self._setpoint,
            self._recording_interval,
        ):
            if isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self._update_runtime_settings)
            else:
                widget.valueChanged.connect(self._update_runtime_settings)
        right_layout.addWidget(settings)

        self._measurement_settings = QGroupBox("Kalibráció és biztonsági határértékek")
        measurement_form = QGridLayout(self._measurement_settings)
        self._line_voltage_min = self._value_spinbox(1.0, -10.0, 10.0, " V")
        self._line_voltage_max = self._value_spinbox(5.0, -10.0, 10.0, " V")
        self._line_value_min = self._value_spinbox(0.0, -1000.0, 1000.0, " bar")
        self._line_value_max = self._value_spinbox(400.0, -1000.0, 1000.0, " bar")
        self._delta_voltage_min = self._value_spinbox(1.0, -10.0, 10.0, " V")
        self._delta_voltage_max = self._value_spinbox(5.0, -10.0, 10.0, " V")
        self._delta_value_min = self._value_spinbox(0.0, -1000.0, 1000.0, " bar")
        self._delta_value_max = self._value_spinbox(40.0, -1000.0, 1000.0, " bar")
        self._max_jacket = self._value_spinbox(400.0, 0.1, 1000.0, " bar")
        self._max_injection = self._value_spinbox(350.0, 0.1, 1000.0, " bar")
        self._max_delta = self._value_spinbox(50.0, 0.1, 1000.0, " bar")
        self._max_line = self._value_spinbox(400.0, 0.1, 1000.0, " bar")
        self._minimum_margin = self._value_spinbox(20.0, 0.1, 1000.0, " bar")
        self._max_overshoot = self._value_spinbox(5.0, 0.1, 1000.0, " bar")
        calibration_fields = (
            ("Vonali U min", self._line_voltage_min),
            ("Vonali U max", self._line_voltage_max),
            ("Vonali érték min", self._line_value_min),
            ("Vonali érték max", self._line_value_max),
            ("Δp U min", self._delta_voltage_min),
            ("Δp U max", self._delta_voltage_max),
            ("Δp érték min", self._delta_value_min),
            ("Δp érték max", self._delta_value_max),
            ("Köpeny maximum", self._max_jacket),
            ("Besajtolás maximum", self._max_injection),
            ("Vonali maximum", self._max_line),
            ("Δp maximum", self._max_delta),
            ("Min. köpenytöbblet", self._minimum_margin),
            ("Max. céltúllövés", self._max_overshoot),
        )
        for index, (label, spinbox) in enumerate(calibration_fields):
            row, column = divmod(index, 2)
            measurement_form.addWidget(QLabel(label), row, column * 2)
            measurement_form.addWidget(spinbox, row, column * 2 + 1)
        apply_measurement = QPushButton("Kalibráció és határértékek alkalmazása")
        apply_measurement.clicked.connect(self._apply_measurement_settings)
        measurement_form.addWidget(apply_measurement, 10, 0, 1, 4)
        self._measurement_settings.setVisible(False)
        right_layout.addWidget(self._measurement_settings)
        right_layout.addStretch(1)

        self._alarm_label = QLabel("Nincs aktív riasztás")
        self._alarm_label.setStyleSheet(
            "padding:8px;background:#e8f5e9;color:#174d22;border-radius:6px"
        )
        layout.addWidget(self._alarm_label)

        self._plot = pg.PlotWidget(title="Elmúlt 10 perc nyomásai")
        self._plot.setObjectName("live_measurement_plot")
        self._plot.setMinimumWidth(480)
        self._plot.setLabel("left", "Nyomás", units="bar")
        self._plot.setLabel("bottom", "Mérés kezdete óta eltelt idő", units="s")
        self._plot.setLimits(xMin=0.0)
        self._plot.showGrid(x=True, y=True, alpha=0.22)
        self._plot.setMouseEnabled(x=True, y=True)
        self._plot.addLegend()
        self._jacket_curve = self._plot.plot(pen="#1565c0", name="Köpeny")
        self._injection_curve = self._plot.plot(pen="#c62828", name="Besajtolás")
        self._line_curve = self._plot.plot(pen="#2e7d32", name="Vonali")
        self._flow_plot = pg.PlotWidget(title="Elmúlt 10 perc besajtolási üteme")
        self._flow_plot.setObjectName("live_injection_flow_plot")
        self._flow_plot.setMinimumWidth(480)
        self._flow_plot.setLabel("left", "Besajtolási sebesség", units="ml/h")
        self._flow_plot.setLabel(
            "bottom", "Mérés kezdete óta eltelt idő", units="s"
        )
        self._flow_plot.setLimits(xMin=0.0)
        self._flow_plot.showGrid(x=True, y=True, alpha=0.22)
        self._flow_plot.setMouseEnabled(x=True, y=True)
        self._flow_curve = self._flow_plot.plot(
            pen=pg.mkPen("#8e24aa", width=2), name="Besajtolási ütem"
        )
        chart_splitter = QSplitter(Qt.Orientation.Vertical)
        chart_splitter.setObjectName("live_chart_splitter")
        chart_splitter.setChildrenCollapsible(False)
        chart_splitter.addWidget(self._plot)
        chart_splitter.addWidget(self._flow_plot)
        chart_splitter.setStretchFactor(0, 2)
        chart_splitter.setStretchFactor(1, 1)
        chart_splitter.setSizes([520, 260])
        left_scroll = QScrollArea()
        left_scroll.setObjectName("status_scroll_area")
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setWidget(status_container)
        right_scroll = QScrollArea()
        right_scroll.setObjectName("control_scroll_area")
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        right_scroll.setWidget(right_container)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("dashboard_splitter")
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(left_scroll)
        splitter.addWidget(chart_splitter)
        splitter.addWidget(right_scroll)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 5)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([270, 760, 340])
        layout.addWidget(splitter, stretch=1)
        self.setCentralWidget(root)

    def _build_menu(self) -> None:
        project_menu = self.menuBar().addMenu("Projekt")
        open_project_settings = QAction("Projektkezelő…", self)
        open_project_settings.setShortcut("Ctrl+Shift+P")
        open_project_settings.triggered.connect(self._open_project_settings)
        project_menu.addAction(open_project_settings)
        project_menu.addSeparator()
        data_management = QAction("Adatkezelés és export…", self)
        data_management.setShortcut("Ctrl+Shift+E")
        data_management.triggered.connect(self._open_data_management)
        project_menu.addAction(data_management)

        display_menu = self.menuBar().addMenu("Megjelenítés")
        full_history = QAction("Teljes rögzített mérés…", self)
        full_history.setShortcut("Ctrl+Shift+G")
        full_history.triggered.connect(self._open_measurement_history)
        display_menu.addAction(full_history)

        settings_menu = self.menuBar().addMenu("Beállítások")
        device_settings = QAction("Eszközök…", self)
        device_settings.setShortcut("Ctrl+Shift+D")
        device_settings.triggered.connect(self._open_device_settings)
        settings_menu.addAction(device_settings)
        pump_control = QAction("Felügyelt pumpavezérlés…", self)
        pump_control.triggered.connect(self._open_pump_control)
        settings_menu.addAction(pump_control)
        logging_settings = QAction("Naplózás…", self)
        logging_settings.triggered.connect(self._open_logging_settings)
        settings_menu.addAction(logging_settings)
        settings_menu.addSeparator()
        self._measurement_settings_action = QAction(
            "Kalibráció és biztonsági határértékek", self, checkable=True
        )
        self._measurement_settings_action.toggled.connect(
            self._measurement_settings.setVisible
        )
        settings_menu.addAction(self._measurement_settings_action)
        settings_menu.addSeparator()

        theme_menu = settings_menu.addMenu("Megjelenés")
        theme_group = QActionGroup(self)
        theme_group.setExclusive(True)
        self._theme_actions: dict[str, QAction] = {}
        for key, label in (
            ("system", "Rendszerbeállítás"),
            ("light", "Világos mód"),
            ("dark", "Sötét mód"),
        ):
            action = QAction(label, self, checkable=True)
            action.triggered.connect(
                lambda checked=False, theme=key: self._set_theme(theme)
            )
            theme_group.addAction(action)
            theme_menu.addAction(action)
            self._theme_actions[key] = action

        developer_menu = self.menuBar().addMenu("Developer")
        developer_view = QAction("Eszközkommunikáció…", self)
        developer_view.setShortcut("Ctrl+Shift+L")
        developer_view.triggered.connect(self._open_developer_view)
        developer_menu.addAction(developer_view)

    def _restore_logging_settings(self) -> None:
        enabled_value = str(
            self._user_settings.value("logging/enabled", "false")
        ).lower()
        enabled = enabled_value in {"1", "true", "yes"}
        raw_categories = self._user_settings.value(
            "logging/categories", [category.value for category in DiagnosticCategory]
        )
        values = raw_categories if isinstance(raw_categories, list) else [raw_categories]
        categories: set[DiagnosticCategory] = set()
        for value in values:
            try:
                categories.add(DiagnosticCategory(str(value)))
            except ValueError:
                continue
        self._diagnostics.configure(enabled=enabled, categories=categories)
        self._diagnostics.emit(
            DiagnosticCategory.SYSTEM, "MODE", "simulation mode initialized"
        )

    def _restore_nas_settings(self) -> None:
        enabled = str(self._user_settings.value("nas/enabled", "false")).lower() in {
            "1",
            "true",
            "yes",
        }
        target = str(self._user_settings.value("nas/target_path", "")).strip()
        try:
            self._nas_sync.configure(
                enabled=enabled, target_root=Path(target) if target else None
            )
        except ValueError:
            self._nas_sync.configure(enabled=False, target_root=None)

    def _current_project_file(self) -> Path | None:
        project_id = self._project.currentData()
        if not isinstance(project_id, int):
            return None
        project = self._projects.get_project(project_id)
        return self._measurement_writer.select_project_with_metadata(
            project.id,
            project.name,
            created_at=project.created_at,
            notes=project.notes,
            configuration=project.configuration,
            calibration_snapshot=project.calibration_snapshot,
            stages=stage_snapshots(project),
        )

    def _open_data_management(self) -> None:
        source_path = self._current_project_file()
        if source_path is None:
            self._show_error("Az adatkezeléshez előbb válassz projektet.")
            return
        DataManagementDialog(
            source_path=source_path,
            project_name=self._project.currentText(),
            data_root=self._data_directory,
            synchronizer=self._nas_sync,
            settings=self._user_settings,
            parent=self,
        ).exec()

    def _open_measurement_history(self) -> None:
        source_path = self._current_project_file()
        if source_path is None:
            self._show_error("A teljes grafikonhoz előbb válassz projektet.")
            return
        MeasurementHistoryDialog(
            source_path, self._project.currentText(), parent=self
        ).exec()

    def _open_logging_settings(self) -> None:
        LoggingSettingsDialog(
            self._diagnostics, self._user_settings, parent=self
        ).exec()

    def _open_developer_view(self) -> None:
        DeveloperViewDialog(self._diagnostics, parent=self).exec()

    def _restore_theme(self) -> None:
        theme = str(self._user_settings.value("theme", "system"))
        if theme not in self._theme_actions:
            theme = "system"
        self._theme_actions[theme].setChecked(True)
        self._set_theme(theme)

    def _set_theme(self, theme: str) -> None:
        application = QApplication.instance()
        if not isinstance(application, QApplication):
            return
        if theme == "dark":
            application.setStyleSheet(DARK_STYLESHEET)
            for plot in (self._plot, self._flow_plot):
                plot.setBackground("#15191f")
                plot.getAxis("left").setTextPen("#e6edf3")
                plot.getAxis("bottom").setTextPen("#e6edf3")
        elif theme == "light":
            application.setStyleSheet(LIGHT_STYLESHEET)
            for plot in (self._plot, self._flow_plot):
                plot.setBackground("#ffffff")
                plot.getAxis("left").setTextPen("#263238")
                plot.getAxis("bottom").setTextPen("#263238")
        else:
            application.setStyleSheet("")
            application.setPalette(application.style().standardPalette())
            self._plot.setBackground(None)
        self._user_settings.setValue("theme", theme)

    def _refresh_mode_label(self) -> None:
        if self._run_mode is RunMode.HARDWARE:
            self._dashboard_mode_label.setText("HARDVER MÓD — FIZIKAI ESZKÖZÖK")
            self._dashboard_mode_label.setStyleSheet(
                "padding:10px;background:#b00020;color:white;font-weight:800;"
                "font-size:15px;border-radius:6px"
            )
        else:
            self._dashboard_mode_label.setText("SZIMULÁCIÓS MÓD — NINCS FIZIKAI KIMENET")
            self._dashboard_mode_label.setStyleSheet(
                "padding:10px;background:#0d6efd;color:white;font-weight:800;"
                "font-size:15px;border-radius:6px"
            )

    def _open_device_settings(self) -> None:
        if self._devices.status.state is not ApplicationState.IDLE:
            self._show_error("Eszközmód csak leválasztott, IDLE állapotban módosítható.")
            return
        dialog = DeviceSettingsDialog(
            PhysicalHardwareConnectionTester(diagnostics=self._diagnostics),
            settings=self._user_settings,
            current_mode=self._run_mode,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.configuration is None:
            return
        try:
            self._activate_hardware(dialog.configuration)
        except Exception as error:
            self._show_error(f"A hardvermód aktiválása sikertelen: {error}")

    def _open_pump_control(self) -> None:
        if self._run_mode is not RunMode.HARDWARE or self._pump_control is None:
            self._show_error("A pumpavezérlés csak sikeresen aktivált hardvermódban érhető el.")
            return
        if self._devices.status.state is not ApplicationState.READY:
            self._show_error(
                "A pumpavezérléshez előbb csatlakozz az eszközökhöz; a mérés legyen leállítva."
            )
            return
        PumpControlDialog(self._pump_control, parent=self).exec()

    def _activate_hardware(self, configuration: HardwareConfiguration) -> None:
        jacket = open_isco_pump(
            configuration.jacket_config(),
            diagnostics=self._diagnostics,
            diagnostic_category=DiagnosticCategory.JACKET_PUMP,
        )
        try:
            injection = open_isco_pump(
                configuration.injection_config(),
                diagnostics=self._diagnostics,
                diagnostic_category=DiagnosticCategory.INJECTION_PUMP,
            )
        except Exception:
            jacket.disconnect()
            raise
        daq = NidaqmxDataAcquisition(
            NidaqmxBackend(configuration.ni_terminal_configuration),
            configuration.ni_config(),
            self._diagnostics,
        )
        daq.authorize_output(NidaqmxDataAcquisition.HARDWARE_CONFIRMATION)
        actuator = AnalogValveActuator(
            daq,
            voltage_at_zero_percent=configuration.valve_zero_percent_voltage,
            voltage_at_hundred_percent=configuration.valve_hundred_percent_voltage,
        )
        writer = ProjectMeasurementWriter(self._data_directory, self._nas_sync)
        project_id = self._project.currentData()
        if isinstance(project_id, int):
            project = self._projects.get_project(project_id)
            writer.select_project_with_metadata(
                project.id,
                project.name,
                created_at=project.created_at,
                notes=project.notes,
                configuration=project.configuration,
                calibration_snapshot=project.calibration_snapshot,
                stages=stage_snapshots(project),
            )
        measurement = MeasurementService(
            jacket_pump=jacket,
            injection_pump=injection,
            daq=daq,
            line_calibration=LinearCalibration(*self._line_calibration_values()),
            differential_calibration=LinearCalibration(*self._delta_calibration_values()),
            safety_monitor=SafetyMonitor(
                SafetyLimits(
                    self._max_jacket.value(),
                    self._max_injection.value(),
                    self._max_delta.value(),
                    self._minimum_margin.value(),
                    self._max_overshoot.value(),
                    self._max_line.value(),
                )
            ),
            writer=writer,
        )
        controller = ValveController(
            PidController(
                PidParameters(
                    self._kp.value(),
                    self._ki.value(),
                    self._kd.value(),
                    output_min_percent=self._output_min.value(),
                    output_max_percent=self._output_max.value(),
                    direction=ControlDirection(self._direction.currentData()),
                )
            )
        )
        new_loop = ControlLoop(
            measurement=measurement, controller=controller, actuator=actuator
        )
        new_devices = DeviceControlService(
            jacket_pump=jacket,
            injection_pump=injection,
            daq=daq,
            mode=RunMode.HARDWARE,
        )
        new_devices.authorize_hardware(DeviceControlService.HARDWARE_CONFIRMATION)
        pump_control = PumpControlService(
            jacket_pump=jacket,
            injection_pump=injection,
            minimum_jacket_margin_bar=self._minimum_margin.value(),
            diagnostics=self._diagnostics,
        )
        pump_control.authorize(PumpControlService.AUTHORIZATION)
        self._control_loop.close()
        self._control_loop = new_loop
        self._measurement_writer = writer
        self._devices = new_devices
        self._pump_control = pump_control
        self._runtime = self._make_runtime(new_loop)
        self._run_mode = RunMode.HARDWARE
        self._diagnostics.emit(
            DiagnosticCategory.SYSTEM, "MODE", "hardware mode activated"
        )
        self._user_settings.setValue("hardware/last_test_succeeded", True)
        self._refresh_mode_label()
        self._refresh_state()

    def _make_runtime(self, control_loop: ControlLoop) -> BackgroundControlRunner:
        return BackgroundControlRunner(
            control_loop,
            control_interval_seconds=0.1,
            watchdog_tolerance_seconds=0.05,
            on_cycle=self._runtime_bridge.cycle_completed.emit,
            on_fault=self._runtime_bridge.fault_raised.emit,
        )

    def _restore_control_settings(self) -> None:
        combo_settings = (
            (self._mode, "control/mode"),
            (self._source, "control/source"),
            (self._direction, "pid/direction"),
        )
        for combo, key in combo_settings:
            stored = self._user_settings.value(key)
            if stored is not None:
                index = combo.findData(str(stored))
                if index >= 0:
                    combo.setCurrentIndex(index)

        numeric_settings = (
            (self._manual_output, "control/manual_output"),
            (self._setpoint, "control/setpoint_bar"),
            (self._recording_interval, "recording/interval_seconds"),
            (self._kp, "pid/kp"),
            (self._ki, "pid/ki"),
            (self._kd, "pid/kd"),
            (self._output_min, "pid/output_min"),
            (self._output_max, "pid/output_max"),
            (self._line_voltage_min, "calibration/line_voltage_min"),
            (self._line_voltage_max, "calibration/line_voltage_max"),
            (self._line_value_min, "calibration/line_value_min"),
            (self._line_value_max, "calibration/line_value_max"),
            (self._delta_voltage_min, "calibration/delta_voltage_min"),
            (self._delta_voltage_max, "calibration/delta_voltage_max"),
            (self._delta_value_min, "calibration/delta_value_min"),
            (self._delta_value_max, "calibration/delta_value_max"),
            (self._max_jacket, "safety/max_jacket"),
            (self._max_injection, "safety/max_injection"),
            (self._max_delta, "safety/max_delta"),
            (self._max_line, "safety/max_line"),
            (self._minimum_margin, "safety/minimum_margin"),
            (self._max_overshoot, "safety/max_overshoot"),
        )
        for widget, key in numeric_settings:
            stored = self._user_settings.value(key)
            if stored is None:
                continue
            try:
                numeric_value = float(stored)
                if isinstance(widget, QSpinBox):
                    widget.setValue(int(numeric_value))
                else:
                    widget.setValue(numeric_value)
            except (TypeError, ValueError):
                continue

    def _restore_project_selection(self) -> None:
        project_id = self._stored_int("project/last_project_id")
        stage_id = self._stored_int("project/last_stage_id")
        self._reload_projects(project_id)
        if stage_id is not None:
            index = self._stage.findData(stage_id)
            if index >= 0:
                self._stage.setCurrentIndex(index)
                self._active_stage_label.setText(self._stage.currentText())

    def _stored_int(self, key: str) -> int | None:
        value = self._user_settings.value(key)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _save_user_settings(self) -> None:
        values: dict[str, object] = {
            "control/mode": ControlMode(self._mode.currentData()).value,
            "control/source": PressureSource(self._source.currentData()).value,
            "control/manual_output": self._manual_output.value(),
            "control/setpoint_bar": self._setpoint.value(),
            "recording/interval_seconds": self._recording_interval.value(),
            "pid/kp": self._kp.value(),
            "pid/ki": self._ki.value(),
            "pid/kd": self._kd.value(),
            "pid/direction": ControlDirection(self._direction.currentData()).value,
            "pid/output_min": self._output_min.value(),
            "pid/output_max": self._output_max.value(),
            "calibration/line_voltage_min": self._line_voltage_min.value(),
            "calibration/line_voltage_max": self._line_voltage_max.value(),
            "calibration/line_value_min": self._line_value_min.value(),
            "calibration/line_value_max": self._line_value_max.value(),
            "calibration/delta_voltage_min": self._delta_voltage_min.value(),
            "calibration/delta_voltage_max": self._delta_voltage_max.value(),
            "calibration/delta_value_min": self._delta_value_min.value(),
            "calibration/delta_value_max": self._delta_value_max.value(),
            "safety/max_jacket": self._max_jacket.value(),
            "safety/max_injection": self._max_injection.value(),
            "safety/max_delta": self._max_delta.value(),
            "safety/max_line": self._max_line.value(),
            "safety/minimum_margin": self._minimum_margin.value(),
            "safety/max_overshoot": self._max_overshoot.value(),
        }
        project_id = self._project.currentData()
        stage_id = self._stage.currentData()
        if isinstance(project_id, int):
            values["project/last_project_id"] = project_id
        if isinstance(stage_id, int):
            values["project/last_stage_id"] = stage_id
        for key, value in values.items():
            self._user_settings.setValue(key, value)
        self._user_settings.sync()

    def _connect_devices(self) -> None:
        try:
            self._devices.connect()
        except Exception as error:
            self._show_error(str(error))
        self._refresh_state()

    def _disconnect_devices(self) -> None:
        try:
            if self._runtime.running:
                self._runtime.stop()
            self._devices.disconnect()
            if self._pump_control is not None:
                self._pump_control.revoke()
                self._pump_control = None
        except Exception as error:
            self._show_error(str(error))
        self._set_all_connections("LEVÁLASZTVA", ok=None)
        self._refresh_state()

    def _acknowledge_fault(self) -> None:
        try:
            self._devices.acknowledge_fault()
            self._alarm_label.setText("Nincs aktív riasztás")
            self._alarm_label.setStyleSheet(
                "padding:8px;background:#e8f5e9;color:#174d22;border-radius:6px"
            )
        except RuntimeError as error:
            self._show_error(str(error))
        self._refresh_state()

    @staticmethod
    def _pid_spinbox(value: float) -> QDoubleSpinBox:
        spinbox = QDoubleSpinBox()
        spinbox.setRange(0.0, 10000.0)
        spinbox.setDecimals(4)
        spinbox.setValue(value)
        return spinbox

    @staticmethod
    def _percent_spinbox(value: float) -> QDoubleSpinBox:
        spinbox = QDoubleSpinBox()
        spinbox.setRange(0.0, 100.0)
        spinbox.setValue(value)
        spinbox.setSuffix(" %")
        return spinbox

    @staticmethod
    def _value_spinbox(
        value: float, minimum: float, maximum: float, suffix: str
    ) -> QDoubleSpinBox:
        spinbox = QDoubleSpinBox()
        spinbox.setRange(minimum, maximum)
        spinbox.setDecimals(4)
        spinbox.setValue(value)
        spinbox.setSuffix(suffix)
        return spinbox

    def _reload_projects(self, selected_project_id: int | None = None) -> None:
        self._project.blockSignals(True)
        self._project.clear()
        for project in self._projects.list_projects():
            self._project.addItem(project.name, project.id)
        self._project.blockSignals(False)
        if selected_project_id is not None:
            index = self._project.findData(selected_project_id)
            if index >= 0:
                self._project.setCurrentIndex(index)
        self._reload_stages()

    def _reload_stages(self, *_args: object) -> None:
        self._stage.clear()
        project_id = self._project.currentData()
        if project_id is None:
            self._active_project_label.setText("Nincs kiválasztva")
            self._active_stage_label.setText("Nincs kiválasztva")
            return
        self._active_project_label.setText(self._project.currentText())
        project = self._projects.get_project(int(project_id))
        self._measurement_writer.select_project_with_metadata(
            project.id,
            project.name,
            created_at=project.created_at,
            notes=project.notes,
            configuration=project.configuration,
            calibration_snapshot=project.calibration_snapshot,
            stages=stage_snapshots(project),
        )
        for stage in self._projects.list_stages(project.id):
            self._stage.addItem(stage.name, stage.id)
        self._active_stage_label.setText(
            self._stage.currentText() or "Nincs kiválasztva"
        )

    def _stage_changed(self, *_args: object) -> None:
        self._active_stage_label.setText(
            self._stage.currentText() or "Nincs kiválasztva"
        )
        stage_id = self._stage.currentData()
        if isinstance(stage_id, int):
            stage = self._projects.get_stage(stage_id)
            details: list[str] = []
            if stage.fluid:
                details.append(stage.fluid)
            if stage.target_pressure_bar is not None:
                self._setpoint.setValue(stage.target_pressure_bar)
                details.append(f"cél: {stage.target_pressure_bar:g} bar")
            if stage.target_flow_ml_per_hour is not None:
                details.append(f"áram: {stage.target_flow_ml_per_hour:g} ml/h")
            self._active_stage_label.setToolTip("; ".join(details))
        self._update_runtime_settings()

    def _open_project_settings(self) -> None:
        if self._devices.status.state is ApplicationState.RUNNING:
            self._show_error("Futó mérés közben az aktív projekt nem módosítható.")
            return
        dialog = ProjectSettingsDialog(
            self._projects,
            selected_project_id=self._project.currentData(),
            selected_stage_id=self._stage.currentData(),
            configuration=self._current_configuration(),
            calibration_snapshot={
                "line_pressure": self._line_calibration_values(),
                "differential_pressure": self._delta_calibration_values(),
            },
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._reload_projects(dialog.selected_project_id)
        if dialog.selected_stage_id is not None:
            self._stage.setCurrentIndex(self._stage.findData(dialog.selected_stage_id))
            self._active_stage_label.setText(self._stage.currentText())
        self._save_user_settings()

    def _create_project(self) -> None:
        name, accepted = QInputDialog.getText(self, "Új projekt", "Projekt neve")
        if not accepted:
            return
        notes, accepted = QInputDialog.getMultiLineText(
            self, "Új projekt", "Megjegyzések"
        )
        if not accepted:
            return
        try:
            project = self._projects.create_project(
                name=name,
                notes=notes,
                configuration=self._current_configuration(),
                calibration_snapshot={
                    "line_pressure": self._line_calibration_values(),
                    "differential_pressure": self._delta_calibration_values(),
                },
            )
            create_default_stages(self._projects, project.id)
            self._reload_projects(project.id)
        except ValueError as error:
            self._show_error(str(error))

    def _add_stage(self) -> None:
        project_id = self._project.currentData()
        if project_id is None:
            self._show_error("Előbb hozz létre vagy válassz projektet.")
            return
        name, accepted = QInputDialog.getText(self, "Új szakasz", "Szakasz neve")
        if accepted:
            try:
                stage = self._projects.add_stage(project_id, name)
                self._reload_stages()
                self._stage.setCurrentIndex(self._stage.findData(stage.id))
            except ValueError as error:
                self._show_error(str(error))

    def _rename_stage(self) -> None:
        stage_id = self._stage.currentData()
        if stage_id is None:
            self._show_error("Nincs átnevezhető mérési szakasz.")
            return
        name, accepted = QInputDialog.getText(
            self, "Szakasz átnevezése", "Új név", text=self._stage.currentText()
        )
        if accepted:
            try:
                self._projects.rename_stage(stage_id, name)
                self._reload_stages()
                self._stage.setCurrentIndex(self._stage.findData(stage_id))
            except ValueError as error:
                self._show_error(str(error))

    def _apply_pid(self) -> None:
        try:
            self._control_loop.configure_pid(
                PidParameters(
                    self._kp.value(),
                    self._ki.value(),
                    self._kd.value(),
                    output_min_percent=self._output_min.value(),
                    output_max_percent=self._output_max.value(),
                    direction=ControlDirection(self._direction.currentData()),
                )
            )
        except ValueError as error:
            self._show_error(str(error))

    def _apply_measurement_settings(self) -> None:
        if self._devices.status.state is ApplicationState.RUNNING:
            self._show_error("Futó mérés közben a kalibráció nem módosítható.")
            return
        try:
            self._control_loop.configure_measurement(
                line_calibration=LinearCalibration(*self._line_calibration_values()),
                differential_calibration=LinearCalibration(
                    *self._delta_calibration_values()
                ),
                safety_limits=SafetyLimits(
                    self._max_jacket.value(),
                    self._max_injection.value(),
                    self._max_delta.value(),
                    self._minimum_margin.value(),
                    self._max_overshoot.value(),
                    self._max_line.value(),
                ),
            )
        except ValueError as error:
            self._show_error(str(error))

    def _line_calibration_values(self) -> list[float]:
        return [
            self._line_voltage_min.value(),
            self._line_voltage_max.value(),
            self._line_value_min.value(),
            self._line_value_max.value(),
        ]

    def _delta_calibration_values(self) -> list[float]:
        return [
            self._delta_voltage_min.value(),
            self._delta_voltage_max.value(),
            self._delta_value_min.value(),
            self._delta_value_max.value(),
        ]

    def _current_configuration(self) -> dict[str, object]:
        return {
            "mode": "simulation",
            "pid": {
                "kp": self._kp.value(),
                "ki": self._ki.value(),
                "kd": self._kd.value(),
                "direction": ControlDirection(self._direction.currentData()).value,
                "output_min_percent": self._output_min.value(),
                "output_max_percent": self._output_max.value(),
            },
            "recording_interval_seconds": self._recording_interval.value(),
        }

    def _runtime_settings(self) -> RuntimeSettings:
        return RuntimeSettings(
            active_stage=self._stage.currentText(),
            mode=ControlMode(self._mode.currentData()),
            manual_output_percent=self._manual_output.value(),
            source=PressureSource(self._source.currentData()),
            setpoint_bar=self._setpoint.value(),
            recording_interval_seconds=float(self._recording_interval.value()),
        )

    def _update_runtime_settings(self, *_args: object) -> None:
        if self._runtime.running and self._stage.currentData() is not None:
            try:
                self._runtime.update_settings(self._runtime_settings())
            except ValueError as error:
                self._handle_runtime_fault(str(error))

    def _start(self) -> None:
        if self._stage.currentData() is None:
            self._show_error("A méréshez válassz projektet és mérési szakaszt.")
            return
        try:
            if self._current_project_file() is None:
                raise RuntimeError("A projektspecifikus mérési fájl nem érhető el.")
            self._devices.start()
            self._control_loop.reset_injected_volume_tracking()
            self._measurement_time_origin = None
            self._times.clear()
            self._jacket_pressures.clear()
            self._injection_pressures.clear()
            self._injection_flows.clear()
            self._line_pressures.clear()
            self._runtime.start(self._runtime_settings())
        except Exception as error:
            self._show_error(str(error))
        self._refresh_state()

    def _stop(self) -> None:
        try:
            self._runtime.stop()
            self._devices.stop()
            if self._pump_control is not None:
                self._pump_control.observe_safe_stop()
        except Exception as error:
            self._show_error(str(error))
        self._refresh_state()

    def _emergency_stop(self) -> None:
        if self._runtime.running:
            self._runtime.stop()
        self._devices.emergency_stop()
        if self._pump_control is not None:
            self._pump_control.revoke()
            self._pump_control = None
        self._alarm_label.setText("RETESSZELT HIBA: kézi vészleállítás")
        self._alarm_label.setStyleSheet(
            "padding:8px;background:#ffcdd2;color:#7f0015;font-weight:700;border-radius:6px"
        )
        self._refresh_state()

    def _handle_cycle(self, result: object) -> None:
        if not isinstance(result, ControlCycleResult):
            self._handle_runtime_fault("invalid result from control thread")
            return
        snapshot = result.record.snapshot
        self._diagnostics.emit(
            DiagnosticCategory.RUNTIME,
            "CYCLE",
            f"jacket={snapshot.jacket_pump.pressure_bar:.3f} bar; "
            f"injection={snapshot.injection_pump.pressure_bar:.3f} bar; "
            f"line={snapshot.line_pressure_bar:.3f} bar; "
            f"valve={result.command.output_percent}",
        )
        self._set_connection("jacket", snapshot.jacket_pump.connected)
        self._set_connection("injection", snapshot.injection_pump.connected)
        self._set_connection("line_daq", True)
        self._set_connection("delta_daq", True)
        self._set_connection("valve", result.command.enabled)
        self._jacket_label.setText(f"{snapshot.jacket_pump.pressure_bar:.1f} bar")
        self._injection_label.setText(f"{snapshot.injection_pump.pressure_bar:.1f} bar")
        self._jacket_remaining_label.setText(
            f"Maradék folyadék: {snapshot.jacket_pump.remaining_volume_ml:.1f} ml"
        )
        self._injection_remaining_label.setText(
            f"Maradék folyadék: {snapshot.injection_pump.remaining_volume_ml:.1f} ml"
        )
        self._injection_flow_label.setText(
            f"Besajtolási sebesség: {snapshot.injection_pump.flow_ml_per_hour:.1f} ml/h"
        )
        self._injected_volume_label.setText(
            f"Mérés óta besajtolt: {result.record.injected_volume_ml:.1f} ml"
        )
        self._line_label.setText(f"{snapshot.line_pressure_bar:.1f} bar")
        self._delta_label.setText(f"{snapshot.differential_pressure_bar:.1f} bar")
        output = result.command.output_percent
        self._valve_label.setText("SAFE" if output is None else f"{output:.1f} %")
        if (
            self._measurement_time_origin is None
            or snapshot.monotonic_seconds < self._measurement_time_origin
        ):
            self._measurement_time_origin = snapshot.monotonic_seconds
        self._times.append(snapshot.monotonic_seconds)
        self._jacket_pressures.append(snapshot.jacket_pump.pressure_bar)
        self._injection_pressures.append(snapshot.injection_pump.pressure_bar)
        self._injection_flows.append(snapshot.injection_pump.flow_ml_per_hour)
        self._line_pressures.append(snapshot.line_pressure_bar)
        elapsed_times = [
            value - self._measurement_time_origin for value in self._times
        ]
        self._jacket_curve.setData(elapsed_times, list(self._jacket_pressures))
        self._injection_curve.setData(elapsed_times, list(self._injection_pressures))
        self._line_curve.setData(elapsed_times, list(self._line_pressures))
        self._flow_curve.setData(elapsed_times, list(self._injection_flows))
        latest = elapsed_times[-1]
        x_minimum = max(0.0, latest - 600.0)
        x_maximum = max(1.0, latest)
        self._plot.setXRange(x_minimum, x_maximum, padding=0.0)
        self._flow_plot.setXRange(x_minimum, x_maximum, padding=0.0)
        if result.record.safety_reasons:
            self._alarm_label.setText("; ".join(result.record.safety_reasons))
            self._alarm_label.setStyleSheet(
                "padding:8px;background:#ffcdd2;color:#7f0015;"
                "font-weight:700;border-radius:6px"
            )

    def _handle_runtime_fault(self, message: str) -> None:
        self._diagnostics.emit(
            DiagnosticCategory.RUNTIME, "FAULT", message, level="ERROR"
        )
        if self._devices.status.state is not ApplicationState.FAULT:
            self._devices.emergency_stop(f"control runtime failed: {message}")
        if self._pump_control is not None:
            self._pump_control.revoke()
            self._pump_control = None
        self._alarm_label.setText(f"RETESSZELT VEZÉRLÉSI HIBA: {message}")
        self._alarm_label.setStyleSheet(
            "padding:8px;background:#ffcdd2;color:#7f0015;"
            "font-weight:700;border-radius:6px"
        )
        self._set_all_connections("HIBA", ok=False)
        self._refresh_state()

    def _set_connection(self, key: str, connected: bool) -> None:
        label = self._connection_labels[key]
        label.setText("KAPCSOLÓDVA" if connected else "HIBA")
        label.setStyleSheet(
            "color:#1b7f3a;font-size:11px;font-weight:700"
            if connected
            else "color:#b00020;font-size:11px;font-weight:700"
        )

    def _set_all_connections(self, text: str, *, ok: bool | None) -> None:
        color = "#1b7f3a" if ok is True else "#b00020" if ok is False else "#66788a"
        for label in self._connection_labels.values():
            label.setText(text)
            label.setStyleSheet(f"color:{color};font-size:11px;font-weight:700")

    def _refresh_state(self) -> None:
        state = self._devices.status.state
        self._state_label.setText(state.value.upper())
        self._connect_button.setEnabled(state is ApplicationState.IDLE)
        self._disconnect_button.setEnabled(
            state in (ApplicationState.READY, ApplicationState.RUNNING)
        )
        self._start_button.setEnabled(state is ApplicationState.READY)
        self._stop_button.setEnabled(state in (ApplicationState.READY, ApplicationState.RUNNING))
        self._acknowledge_button.setEnabled(state is ApplicationState.FAULT)
        self._pump_control_button.setEnabled(
            self._run_mode is RunMode.HARDWARE
            and self._pump_control is not None
            and state is ApplicationState.READY
        )
        self._measurement_settings.setEnabled(state is not ApplicationState.RUNNING)

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "EOR hiba", message)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._save_user_settings()
        if self._runtime.running:
            self._runtime.stop()
        if self._devices.status.state is not ApplicationState.IDLE:
            self._devices.disconnect()
        self._control_loop.close()
        self._nas_sync.close()
        self._projects.close()
        event.accept()


def build_simulated_dashboard(
    data_path: Path, project_path: Path | None = None
) -> DashboardWindow:
    jacket = SimulatedPump(pressure_bar=120.0)
    injection = SimulatedPump(
        pressure_bar=100.0, flow_ml_per_hour=10.0, remaining_volume_ml=260.0
    )
    daq = SimulatedDataAcquisition()
    daq.inputs.update(line_pressure=2.0, differential_pressure=1.5)
    valve = SimulatedValveActuator()
    safety = SafetyMonitor(SafetyLimits(400.0, 350.0, 50.0))
    queue = NasSyncQueue(data_path.parent / "nas_sync_queue.sqlite3")
    nas_sync = BackgroundNasSynchronizer(queue)
    writer = ProjectMeasurementWriter(data_path.parent, nas_sync)
    measurement = MeasurementService(
        jacket_pump=jacket,
        injection_pump=injection,
        daq=daq,
        line_calibration=LinearCalibration(1.0, 5.0, 0.0, 400.0),
        differential_calibration=LinearCalibration(1.0, 5.0, 0.0, 40.0),
        safety_monitor=safety,
        writer=writer,
    )
    return DashboardWindow(
        devices=DeviceControlService(jacket_pump=jacket, injection_pump=injection, daq=daq),
        control_loop=ControlLoop(
            measurement=measurement,
            controller=ValveController(PidController(PidParameters(1.0, 0.05, 0.0))),
            actuator=valve,
        ),
        valve=valve,
        projects=ProjectRepository(project_path or data_path.parent / "projects.sqlite3"),
        data_directory=data_path.parent,
        measurement_writer=writer,
        nas_sync=nas_sync,
    )


def run_ui() -> int:
    root = application_root_path()
    config_directory = root / "config"
    config_directory.mkdir(parents=True, exist_ok=True)
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        str(config_directory),
    )
    instance = QApplication.instance()
    application = instance if isinstance(instance, QApplication) else QApplication(sys.argv)
    application.setWindowIcon(application_icon())
    window = build_simulated_dashboard(root / "data" / "simulated_measurements.csv")
    window.show()
    return application.exec()
