import ctypes
import sys
from collections import deque
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from threading import Thread
from typing import cast

import pyqtgraph as pg  # type: ignore[import-untyped]
from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QIcon, QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
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
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
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
    filter_measurement_table_by_stage,
    measurement_stage_segments,
    measurement_stages,
    numeric_series,
    read_measurement_tables,
    safe_filename,
)
from eor_control.diagnostics import DiagnosticCategory, DiagnosticLogger
from eor_control.domain import PumpStatus
from eor_control.hardware import (
    ConnectionTestResult,
    HardwareConfiguration,
    HardwareConnectionTester,
    HardwareDiscovery,
    NiPhysicalChannelInfo,
    PhysicalHardwareConnectionTester,
    SerialPortInfo,
    discover_hardware,
)
from eor_control.isco import open_isco_pump
from eor_control.measurement import MeasurementService
from eor_control.ni import AnalogValveActuator, NidaqmxBackend, NidaqmxDataAcquisition
from eor_control.projects import (
    MeasurementProject,
    MeasurementStage,
    PidProfile,
    ProjectRepository,
)
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
QLabel { background: transparent; }
QGroupBox { background: #ffffff; border: 1px solid #d7dee7; border-radius: 8px;
            margin-top: 10px; padding: 8px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
QTabWidget::pane { border: 1px solid #d7dee7; border-radius: 6px; background: #ffffff; }
QTabBar::tab {
    background: #e8eef6; border: 1px solid #c4cfdd; padding: 8px 18px;
    border-top-left-radius: 6px; border-top-right-radius: 6px;
}
QTabBar::tab:selected { background: #ffffff; font-weight: 600; }
QPushButton { background: #e8eef6; border: 1px solid #c4cfdd; border-radius: 6px;
              padding: 7px 10px; }
QPushButton:hover { background: #dce7f3; }
QPushButton:disabled { color: #9aa5b1; background: #edf1f5; }
QComboBox, QDoubleSpinBox, QSpinBox {
    background: #ffffff; color: #1f2933; border: 1px solid #b8c4d2;
    border-radius: 6px; padding: 5px 34px 5px 8px; min-height: 24px;
    selection-background-color: #dce7f3; selection-color: #1f2933;
}
QComboBox:hover, QDoubleSpinBox:hover, QSpinBox:hover {
    border-color: #8296aa;
}
QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus {
    border: 2px solid #2878b5; padding: 4px 33px 4px 7px;
}
QComboBox:disabled, QDoubleSpinBox:disabled, QSpinBox:disabled {
    background: #edf1f5; color: #8a98a8; border-color: #d5dde6;
}
QLineEdit, QTextEdit, QPlainTextEdit {
    background: transparent; color: #1f2933; border: 1px solid #b8c4d2;
    border-radius: 6px; padding: 5px 8px; min-height: 24px;
    selection-background-color: #dce7f3; selection-color: #1f2933;
}
QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover { border-color: #8296aa; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {
    background: transparent; border: 2px solid #2878b5; padding: 4px 7px;
}
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {
    background: transparent; color: #8a98a8; border-color: #d5dde6;
}
QComboBox::drop-down {
    subcontrol-origin: border; subcontrol-position: top right; width: 30px;
    background: #e8eef6; border-left: 1px solid #b8c4d2;
    border-top-right-radius: 6px; border-bottom-right-radius: 6px;
}
QComboBox::drop-down:hover { background: #d7e4f1; }
QComboBox::down-arrow, QDoubleSpinBox::down-arrow, QSpinBox::down-arrow {
    image: url(__THEME_DOWN_ARROW__); width: 10px; height: 6px;
}
QDoubleSpinBox::up-arrow, QSpinBox::up-arrow {
    image: url(__THEME_UP_ARROW__); width: 10px; height: 6px;
}
QComboBox QAbstractItemView {
    background: #ffffff; color: #1f2933; border: 1px solid #8296aa;
    border-radius: 5px; padding: 4px; outline: 0;
    selection-background-color: #dce7f3; selection-color: #1f2933;
}
QDoubleSpinBox::up-button, QSpinBox::up-button {
    subcontrol-origin: border; subcontrol-position: top right; width: 28px;
    background: #e8eef6; border-left: 1px solid #b8c4d2;
    border-bottom: 1px solid #c4cfdd; border-top-right-radius: 6px;
}
QDoubleSpinBox::down-button, QSpinBox::down-button {
    subcontrol-origin: border; subcontrol-position: bottom right; width: 28px;
    background: #e8eef6; border-left: 1px solid #b8c4d2;
    border-bottom-right-radius: 6px;
}
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover,
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: #d7e4f1; }
QScrollBar:vertical {
    background: #e7edf3; width: 12px; margin: 0; border: none; border-radius: 6px;
}
QScrollBar::handle:vertical {
    background: #9aabba; min-height: 32px; margin: 2px; border-radius: 4px;
}
QScrollBar::handle:vertical:hover { background: #72889b; }
QScrollBar::handle:vertical:pressed { background: #526d82; }
QScrollBar:horizontal {
    background: #e7edf3; height: 12px; margin: 0; border: none; border-radius: 6px;
}
QScrollBar::handle:horizontal {
    background: #9aabba; min-width: 32px; margin: 2px; border-radius: 4px;
}
QScrollBar::handle:horizontal:hover { background: #72889b; }
QScrollBar::handle:horizontal:pressed { background: #526d82; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0; background: transparent; border: none;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0; background: transparent; border: none;
}
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QAbstractScrollArea::corner { background: #e7edf3; border: none; }
QSplitter::handle { background: #c4cfdd; border-radius: 3px; }
QSplitter::handle:horizontal { margin: 4px 1px; }
QSplitter::handle:vertical { margin: 1px 4px; }
QSplitter::handle:hover { background: #2878b5; }
QSplitter::handle:pressed { background: #1f5f91; }
QMenuBar, QMenu { background: #ffffff; color: #1f2933; }
"""

DARK_STYLESHEET = """
QMainWindow, QWidget { background: #11151a; color: #e6edf3; }
QLabel { background: transparent; }
QGroupBox { background: #1b2129; border: 1px solid #35404d; border-radius: 8px;
            margin-top: 10px; padding: 8px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
QTabWidget::pane { border: 1px solid #35404d; border-radius: 6px; background: #1b2129; }
QTabBar::tab {
    background: #202832; border: 1px solid #465362; padding: 8px 18px;
    border-top-left-radius: 6px; border-top-right-radius: 6px;
}
QTabBar::tab:selected { background: #1b2129; font-weight: 600; }
QPushButton { background: #28323d; color: #e6edf3; border: 1px solid #465362;
              border-radius: 6px; padding: 7px 10px; }
QPushButton:hover { background: #334150; }
QPushButton:disabled { color: #65717e; background: #1d242c; }
QComboBox, QDoubleSpinBox, QSpinBox {
    background: #202832; color: #e6edf3; border: 1px solid #465362;
    border-radius: 6px; padding: 5px 34px 5px 8px; min-height: 24px;
    selection-background-color: #355a78; selection-color: #ffffff;
}
QComboBox:hover, QDoubleSpinBox:hover, QSpinBox:hover {
    border-color: #71849a;
}
QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus {
    border: 2px solid #4da3df; padding: 4px 33px 4px 7px;
}
QComboBox:disabled, QDoubleSpinBox:disabled, QSpinBox:disabled {
    background: #1a2027; color: #65717e; border-color: #303944;
}
QLineEdit, QTextEdit, QPlainTextEdit {
    background: transparent; color: #e6edf3; border: 1px solid #465362;
    border-radius: 6px; padding: 5px 8px; min-height: 24px;
    selection-background-color: #355a78; selection-color: #ffffff;
}
QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover { border-color: #71849a; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {
    background: transparent; border: 2px solid #4da3df; padding: 4px 7px;
}
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {
    background: transparent; color: #65717e; border-color: #303944;
}
QComboBox::drop-down {
    subcontrol-origin: border; subcontrol-position: top right; width: 30px;
    background: #2b3642; border-left: 1px solid #465362;
    border-top-right-radius: 6px; border-bottom-right-radius: 6px;
}
QComboBox::drop-down:hover { background: #39495a; }
QComboBox::down-arrow, QDoubleSpinBox::down-arrow, QSpinBox::down-arrow {
    image: url(__THEME_DOWN_ARROW__); width: 10px; height: 6px;
}
QDoubleSpinBox::up-arrow, QSpinBox::up-arrow {
    image: url(__THEME_UP_ARROW__); width: 10px; height: 6px;
}
QComboBox QAbstractItemView {
    background: #202832; color: #e6edf3; border: 1px solid #5a6b7d;
    border-radius: 5px; padding: 4px; outline: 0;
    selection-background-color: #355a78; selection-color: #ffffff;
}
QDoubleSpinBox::up-button, QSpinBox::up-button {
    subcontrol-origin: border; subcontrol-position: top right; width: 28px;
    background: #2b3642; border-left: 1px solid #465362;
    border-bottom: 1px solid #465362; border-top-right-radius: 6px;
}
QDoubleSpinBox::down-button, QSpinBox::down-button {
    subcontrol-origin: border; subcontrol-position: bottom right; width: 28px;
    background: #2b3642; border-left: 1px solid #465362;
    border-bottom-right-radius: 6px;
}
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover,
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: #39495a; }
QScrollBar:vertical {
    background: #171d24; width: 12px; margin: 0; border: none; border-radius: 6px;
}
QScrollBar::handle:vertical {
    background: #526170; min-height: 32px; margin: 2px; border-radius: 4px;
}
QScrollBar::handle:vertical:hover { background: #6d8092; }
QScrollBar::handle:vertical:pressed { background: #8ca0b2; }
QScrollBar:horizontal {
    background: #171d24; height: 12px; margin: 0; border: none; border-radius: 6px;
}
QScrollBar::handle:horizontal {
    background: #526170; min-width: 32px; margin: 2px; border-radius: 4px;
}
QScrollBar::handle:horizontal:hover { background: #6d8092; }
QScrollBar::handle:horizontal:pressed { background: #8ca0b2; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0; background: transparent; border: none;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0; background: transparent; border: none;
}
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QAbstractScrollArea::corner { background: #171d24; border: none; }
QSplitter::handle { background: #465362; border-radius: 3px; }
QSplitter::handle:horizontal { margin: 4px 1px; }
QSplitter::handle:vertical { margin: 1px 4px; }
QSplitter::handle:hover { background: #4da3df; }
QSplitter::handle:pressed { background: #77bceb; }
QMenuBar, QMenu { background: #1b2129; color: #e6edf3; }
QMenu::item:selected { background: #334150; }
"""

SYSTEM_STYLESHEET = """
QLabel { background: transparent; }
QLineEdit, QTextEdit, QPlainTextEdit { background: transparent; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus { background: transparent; }
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {
    background: transparent;
}
QSplitter::handle { background: #7b8794; border-radius: 3px; }
QSplitter::handle:hover { background: #2878b5; }
"""

WINDOWS_APP_USER_MODEL_ID = "AFKI.EOR.Control"


def configure_windows_application_identity() -> None:
    """Give Windows a stable taskbar identity before QApplication is created."""
    if sys.platform != "win32":
        return
    try:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        setter = shell32.SetCurrentProcessExplicitAppUserModelID
        setter.argtypes = [ctypes.c_wchar_p]
        setter.restype = ctypes.c_long
        setter(WINDOWS_APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        # Older or restricted Windows environments can omit this shell API.
        # Qt still receives the explicit icon below.
        return


def application_icon_path() -> Path:
    bundle_directory = getattr(sys, "_MEIPASS", None)
    if isinstance(bundle_directory, str):
        return Path(bundle_directory) / "img" / "icon.png"
    return Path(__file__).resolve().parents[2] / "img" / "icon.png"


def resolved_theme_stylesheet(stylesheet: str, theme: str) -> str:
    asset_directory = application_icon_path().parent
    down_arrow = (asset_directory / f"arrow-down-{theme}.svg").as_posix()
    up_arrow = (asset_directory / f"arrow-up-{theme}.svg").as_posix()
    return stylesheet.replace("__THEME_DOWN_ARROW__", down_arrow).replace(
        "__THEME_UP_ARROW__", up_arrow
    )


def application_root_path() -> Path:
    if bool(getattr(sys, "frozen", False)):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def portable_user_settings(
    root: Path | None = None, *, migrate_legacy: bool = True
) -> QSettings:
    """Open the explicit portable INI and migrate older Registry settings once."""

    settings_path = (root or application_root_path()) / "config" / "AFKI" / "EORControl.ini"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
    if migrate_legacy and not settings.allKeys():
        legacy = QSettings(
            QSettings.Format.NativeFormat,
            QSettings.Scope.UserScope,
            "AFKI",
            "EORControl",
        )
        for key in legacy.allKeys():
            settings.setValue(key, legacy.value(key))
        settings.sync()
    return settings


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
ADD_STAGE_ACTION_DATA = "__add_measurement_stage__"


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
        self.setWindowTitle(
            "Új mérési szakasz" if stage is None else "Mérési szakasz szerkesztése"
        )
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


class ProjectSelectionDialog(QDialog):
    """Startup-friendly project and measurement-stage chooser."""

    def __init__(
        self,
        repository: ProjectRepository,
        *,
        settings: QSettings,
        selected_project_id: int | None,
        selected_stage_id: int | None,
        configuration: dict[str, object],
        calibration_snapshot: dict[str, object],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._settings = settings
        self._configuration = configuration
        self._calibration_snapshot = calibration_snapshot
        self._preferred_stage_id = selected_stage_id
        self.setWindowTitle("Projekt kiválasztása")
        self.resize(760, 480)
        layout = QVBoxLayout(self)

        title = QLabel("Melyik projekttel szeretnél dolgozni?")
        title.setStyleSheet("font-size:18px;font-weight:700")
        layout.addWidget(title)
        help_text = QLabel(
            "Válassz egy korábbi projektet és mérési fázist, vagy hozz létre "
            "egy új projektet."
        )
        help_text.setWordWrap(True)
        help_text.setStyleSheet("color:#66788a")
        layout.addWidget(help_text)

        self.project_table = QTableWidget(0, 3)
        self.project_table.setObjectName("project_selection_table")
        self.project_table.setHorizontalHeaderLabels(
            ("Projekt", "Utoljára használt mérési fázis", "Létrehozva")
        )
        self.project_table.horizontalHeader().setStretchLastSection(False)
        self.project_table.horizontalHeader().setSectionResizeMode(
            0, self.project_table.horizontalHeader().ResizeMode.Stretch
        )
        self.project_table.horizontalHeader().setSectionResizeMode(
            1, self.project_table.horizontalHeader().ResizeMode.Stretch
        )
        self.project_table.setColumnWidth(2, 145)
        self.project_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.project_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.project_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.project_table.currentCellChanged.connect(self._project_changed)
        self.project_table.cellDoubleClicked.connect(
            lambda _row, _column: self._accept_if_complete()
        )
        layout.addWidget(self.project_table, 1)

        self.empty_message = QLabel(
            "Még nincs korábbi projekt. Hozd létre az első projektet az alábbi gombbal."
        )
        self.empty_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_message.setStyleSheet("padding:16px;color:#66788a")
        layout.addWidget(self.empty_message)

        stage_row = QHBoxLayout()
        stage_row.addWidget(QLabel("Megnyitandó mérési fázis"))
        self.stage_selector = QComboBox()
        self.stage_selector.setObjectName("project_selection_stage")
        self.stage_selector.currentIndexChanged.connect(self._update_open_button)
        stage_row.addWidget(self.stage_selector, 1)
        layout.addLayout(stage_row)

        actions = QHBoxLayout()
        create_button = QPushButton("Új projekt létrehozása…")
        create_button.setObjectName("create_project_from_selector")
        create_button.clicked.connect(self._create_project)
        actions.addWidget(create_button)
        self.delete_button = QPushButton("Kijelölt projekt törlése…")
        self.delete_button.setObjectName("delete_project_from_selector")
        self.delete_button.clicked.connect(self._delete_project)
        actions.addWidget(self.delete_button)
        actions.addStretch()
        cancel_button = QPushButton("Mégse")
        cancel_button.clicked.connect(self.reject)
        actions.addWidget(cancel_button)
        self.open_button = QPushButton("Projekt megnyitása")
        self.open_button.setDefault(True)
        self.open_button.clicked.connect(self._accept_if_complete)
        actions.addWidget(self.open_button)
        layout.addLayout(actions)

        self._reload_projects(selected_project_id)

    @property
    def selected_project_id(self) -> int | None:
        row = self.project_table.currentRow()
        item = self.project_table.item(row, 0) if row >= 0 else None
        value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return value if isinstance(value, int) else None

    @property
    def selected_stage_id(self) -> int | None:
        value = self.stage_selector.currentData()
        return value if isinstance(value, int) else None

    def _stored_stage_id(self, project_id: int) -> int | None:
        value = self._settings.value(f"project/last_stage_by_project/{project_id}")
        if value is None and self._stored_int("project/last_project_id") == project_id:
            value = self._settings.value("project/last_stage_id")
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _stored_int(self, key: str) -> int | None:
        value = self._settings.value(key)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _reload_projects(self, selected_project_id: int | None) -> None:
        projects = tuple(reversed(self._repository.list_projects()))
        self.project_table.blockSignals(True)
        self.project_table.setRowCount(len(projects))
        selected_row = -1
        for row, project in enumerate(projects):
            stages = self._repository.list_stages(project.id)
            stored_stage_id = self._stored_stage_id(project.id)
            last_stage = next(
                (stage for stage in stages if stage.id == stored_stage_id), None
            )
            project_item = QTableWidgetItem(project.name)
            project_item.setData(Qt.ItemDataRole.UserRole, project.id)
            project_item.setToolTip(project.notes)
            self.project_table.setItem(row, 0, project_item)
            self.project_table.setItem(
                row,
                1,
                QTableWidgetItem(
                    last_stage.name if last_stage is not None else "Nincs korábbi adat"
                ),
            )
            self.project_table.setItem(
                row,
                2,
                QTableWidgetItem(project.created_at.astimezone().strftime("%Y-%m-%d %H:%M")),
            )
            if project.id == selected_project_id:
                selected_row = row
        self.project_table.blockSignals(False)
        self.empty_message.setVisible(not projects)
        if projects:
            self.project_table.selectRow(selected_row if selected_row >= 0 else 0)
            self.project_table.setCurrentCell(selected_row if selected_row >= 0 else 0, 0)
            self._project_changed()
        else:
            self.stage_selector.clear()
            self._update_open_button()

    def _project_changed(self, *_args: object) -> None:
        self.stage_selector.clear()
        project_id = self.selected_project_id
        if project_id is None:
            self._update_open_button()
            return
        stages = self._repository.list_stages(project_id)
        for stage in stages:
            self.stage_selector.addItem(stage.name, stage.id)
        preferred_stage_id = self._preferred_stage_id or self._stored_stage_id(project_id)
        self._preferred_stage_id = None
        if preferred_stage_id is not None:
            index = self.stage_selector.findData(preferred_stage_id)
            if index >= 0:
                self.stage_selector.setCurrentIndex(index)
        self._update_open_button()

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
            self._preferred_stage_id = stage.id
            self._reload_projects(project.id)
        except ValueError as error:
            QMessageBox.critical(self, "EOR hiba", str(error))

    def _delete_project(self) -> None:
        project_id = self.selected_project_id
        if project_id is None:
            return
        project_name = self._repository.get_project(project_id).name
        answer = QMessageBox.question(
            self,
            "Projekt törlése",
            f"Biztosan törlöd ezt a projektet: {project_name}?\n\n"
            "A projekt és a mérési fázisok eltűnnek a projektlistából. "
            "A korábban rögzített nyers mérési CSV-fájlok biztonsági okból "
            "megmaradnak.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._repository.delete_project(project_id)
        self._settings.remove(f"project/last_stage_by_project/{project_id}")
        if self._stored_int("project/last_project_id") == project_id:
            self._settings.remove("project/last_project_id")
            self._settings.remove("project/last_stage_id")
        self._settings.sync()
        self._preferred_stage_id = None
        self._reload_projects(None)

    def _update_open_button(self, *_args: object) -> None:
        self.delete_button.setEnabled(self.selected_project_id is not None)
        self.open_button.setEnabled(
            self.selected_project_id is not None and self.selected_stage_id is not None
        )

    def _accept_if_complete(self) -> None:
        if self.selected_project_id is None or self.selected_stage_id is None:
            QMessageBox.critical(
                self, "EOR hiba", "Válassz projektet és mérési fázist."
            )
            return
        self.accept()


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
        self._projects_changed = False
        self.setWindowTitle("Projektbeállítások")
        self.resize(560, 280)
        layout = QVBoxLayout(self)
        form = QGridLayout()
        self.project_selector = QComboBox()
        self.project_selector.setObjectName("dialog_project_selector")
        self.stage_selector = QComboBox()
        self.stage_selector.setObjectName("dialog_stage_selector")
        new_project = QPushButton("Új projekt")
        delete_project = QPushButton("Projekt törlése…")
        delete_project.setObjectName("delete_project_from_settings")
        add_stage = QPushButton("Új szakasz")
        rename_stage = QPushButton("Szakasz szerkesztése")
        move_up = QPushButton("Fel")
        move_down = QPushButton("Le")
        delete_stage = QPushButton("Törlés")
        self.project_selector.currentIndexChanged.connect(self._reload_stages)
        new_project.clicked.connect(self._create_project)
        delete_project.clicked.connect(self._delete_project)
        add_stage.clicked.connect(self._add_stage)
        rename_stage.clicked.connect(self._rename_stage)
        move_up.clicked.connect(lambda: self._move_stage(-1))
        move_down.clicked.connect(lambda: self._move_stage(1))
        delete_stage.clicked.connect(self._delete_stage)
        form.addWidget(QLabel("Projekt"), 0, 0)
        form.addWidget(self.project_selector, 0, 1, 1, 2)
        form.addWidget(new_project, 1, 1)
        form.addWidget(delete_project, 1, 2)
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

    @property
    def projects_changed(self) -> bool:
        return self._projects_changed

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
            self._projects_changed = True
            self._reload_projects(project.id, stage.id)
        except ValueError as error:
            QMessageBox.critical(self, "EOR hiba", str(error))

    def _delete_project(self) -> None:
        project_id = self.selected_project_id
        if project_id is None:
            return
        project_name = self.project_selector.currentText()
        answer = QMessageBox.question(
            self,
            "Projekt törlése",
            f"Biztosan törlöd ezt a projektet: {project_name}?\n\n"
            "A projekt és a mérési fázisok eltűnnek a projektlistából. "
            "A korábban rögzített nyers mérési CSV-fájlok biztonsági okból "
            "megmaradnak.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._repository.delete_project(project_id)
        self._projects_changed = True
        self._reload_projects()

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
        index = self.currentIndex()
        if index >= 0 and self.currentText() == self.itemText(index):
            value = self.itemData(index)
            if isinstance(value, str) and value:
                return value
        return self.currentText()

    def setText(self, value: str) -> None:
        index = self.findData(value)
        if index >= 0:
            self.setCurrentIndex(index)
        else:
            self.setCurrentText(value)


class DeviceSettingsDialog(QDialog):
    def __init__(
        self,
        tester: HardwareConnectionTester,
        *,
        settings: QSettings,
        current_mode: RunMode,
        discoverer: Callable[[], HardwareDiscovery] = discover_hardware,
        diagnostics: DiagnosticLogger | None = None,
        developer_mode: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._tester = tester
        self._settings = settings
        self._discoverer = discoverer
        self._diagnostics = diagnostics
        self._developer_mode = developer_mode
        self._test_succeeded = False
        self._configuration: HardwareConfiguration | None = None
        self.setWindowTitle("Eszközbeállítások")
        self.resize(720, 820)
        layout = QVBoxLayout(self)
        self._mode_label = QLabel(f"Jelenlegi mód: {current_mode.value.upper()}")
        self._mode_label.setObjectName("device_mode_label")
        self._mode_label.setStyleSheet(
            "padding:10px;background:transparent;color:#9a6700;font-weight:700;"
            "border:1px solid #c58a00;border-radius:6px"
        )
        layout.addWidget(self._mode_label)
        channel_help = QLabel(
            "Először keresd meg az eszközöket, majd válaszd ki a két pumpa "
            "csatlakozóját és az NI adatgyűjtőt. Ezután csak a kiválasztott NI "
            "eszközhöz tartozó bemenetek és kimenetek jelennek meg."
        )
        channel_help.setWordWrap(True)
        channel_help.setStyleSheet("padding:8px;color:#66788a")
        layout.addWidget(channel_help)
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
        self.ni_device = QComboBox()
        self.ni_device.addItem("Előbb deríts fel és válassz NI eszközt…", None)
        self._discovered_ni_inputs: tuple[NiPhysicalChannelInfo, ...] = ()
        self._discovered_ni_outputs: tuple[NiPhysicalChannelInfo, ...] = ()
        self._active_ni_device: str | None = None
        self._ni_channel_selections: dict[str, tuple[str, str, str]] = {}
        self._changing_ni_inputs = False
        self.ni_device.currentIndexChanged.connect(self._ni_device_changed)
        self.line_channel.currentIndexChanged.connect(
            lambda _index: self._ensure_distinct_ni_inputs(self.line_channel)
        )
        self.delta_channel.currentIndexChanged.connect(
            lambda _index: self._ensure_distinct_ni_inputs(self.delta_channel)
        )
        for field in (self.line_channel, self.delta_channel, self.valve_channel):
            field.setEnabled(False)
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
        pump_box = QGroupBox("Pumpák csatlakoztatása")
        pump_form = QFormLayout(pump_box)
        for label, pump_widget in (
            ("Köpenypumpa csatlakozója", self.jacket_port),
            ("Köpenypumpa vezérlőazonosítója", self.jacket_id),
            ("Köpenypumpa csatlakozási helye", self.jacket_channel),
            ("Besajtolópumpa csatlakozója", self.injection_port),
            ("Besajtolópumpa vezérlőazonosítója", self.injection_id),
            ("Besajtolópumpa csatlakozási helye", self.injection_channel),
            ("Kapcsolati sebesség", self.baud_rate),
            ("Kábelezési megjegyzés", self.pump_cabling_notes),
        ):
            pump_form.addRow(label, pump_widget)

        ni_box = QGroupBox("Nyomásmérés és szelepvezérlés")
        ni_form = QFormLayout(ni_box)
        for label, ni_widget in (
            ("NI adatgyűjtő", self.ni_device),
            ("Vonali nyomás bemenete", self.line_channel),
            ("Differenciálnyomás bemenete", self.delta_channel),
            ("Szelepvezérlés kimenete", self.valve_channel),
            ("Bemenetek bekötési módja", self.terminal_configuration),
            ("Bekötési megjegyzés", self.ni_wiring_notes),
            ("Biztonságos szelepjel", self.safe_voltage),
            ("Szelep 0%-os jele", self.zero_voltage),
            ("Szelep 100%-os jele", self.hundred_voltage),
        ):
            ni_form.addRow(label, ni_widget)

        self.device_tabs = QTabWidget()
        self.device_tabs.setObjectName("device_settings_tabs")
        pump_tab = QWidget()
        pump_tab_layout = QVBoxLayout(pump_tab)
        pump_tab_layout.addWidget(pump_box)
        pump_tab_layout.addStretch()
        ni_tab = QWidget()
        ni_tab_layout = QVBoxLayout(ni_tab)
        ni_tab_layout.addWidget(ni_box)
        ni_tab_layout.addStretch()
        self.device_tabs.addTab(pump_tab, "Pumpák")
        self.device_tabs.addTab(ni_tab, "NI mérés és szelep")
        layout.addWidget(self.device_tabs)

        discovery_row = QHBoxLayout()
        self._discovery_status = QLabel()
        self._discovery_status.setWordWrap(True)
        self._discovery_status.setVisible(developer_mode)
        refresh_button = QPushButton("Csatlakoztatott eszközök keresése")
        refresh_button.clicked.connect(self._refresh_hardware_choices)
        discovery_row.addWidget(refresh_button)
        discovery_row.addWidget(self._discovery_status, 1)
        layout.addLayout(discovery_row)
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
    def _replace_port_choices(
        field: EditableSelectionComboBox,
        ports: tuple[SerialPortInfo, ...],
        stored_value: str,
    ) -> None:
        selected_data = field.currentData()
        selected = selected_data if isinstance(selected_data, str) else stored_value
        field.clear()
        field.setEditable(False)
        if not ports:
            field.addItem("Nincs elérhető soros eszköz", None)
            field.setEnabled(False)
            return
        field.setEnabled(True)
        field.addItem("Válassz soros eszközt…", None)
        for port in ports:
            field.addItem(port.display_name, port.device)
        selected_index = field.findData(selected)
        if selected_index >= 0:
            field.setCurrentIndex(selected_index)
        else:
            field.setCurrentIndex(0)

    @staticmethod
    def _replace_ni_choices(
        field: EditableSelectionComboBox,
        channels: tuple[NiPhysicalChannelInfo, ...],
        selected: str,
        fallback_index: int,
    ) -> None:
        field.blockSignals(True)
        field.clear()
        if not channels:
            field.addItem("Nincs elérhető csatorna", None)
            field.blockSignals(False)
            return
        for channel in channels:
            field.addItem(channel.display_name, channel.channel)
            field.setItemData(
                field.count() - 1,
                channel.tooltip,
                Qt.ItemDataRole.ToolTipRole,
            )
        normalized_selected = selected.strip().casefold()
        selected_index = next(
            (
                index
                for index in range(field.count())
                if isinstance(field.itemData(index), str)
                and field.itemData(index).strip().casefold() == normalized_selected
            ),
            -1,
        )
        if selected_index >= 0:
            field.setCurrentIndex(selected_index)
        elif channels:
            field.setCurrentIndex(min(fallback_index, len(channels) - 1))
        field.blockSignals(False)

    def _ensure_distinct_ni_inputs(self, changed: QComboBox) -> None:
        if self._changing_ni_inputs:
            return
        line_value = self.line_channel.currentData()
        delta_value = self.delta_channel.currentData()
        if not isinstance(line_value, str) or line_value != delta_value:
            return
        target = self.delta_channel if changed is self.line_channel else self.line_channel
        self._changing_ni_inputs = True
        try:
            for index in range(target.count()):
                if target.itemData(index) != line_value:
                    target.setCurrentIndex(index)
                    break
        finally:
            self._changing_ni_inputs = False

    def _replace_ni_devices(
        self,
        inputs: tuple[NiPhysicalChannelInfo, ...],
        outputs: tuple[NiPhysicalChannelInfo, ...],
    ) -> None:
        self._discovered_ni_inputs = inputs
        self._discovered_ni_outputs = outputs
        devices: dict[str, NiPhysicalChannelInfo] = {}
        for channel in (*inputs, *outputs):
            devices.setdefault(channel.device_name, channel)
        selected = self._stored("ni_device_name", "")
        self.ni_device.blockSignals(True)
        self.ni_device.clear()
        if not devices:
            self.ni_device.addItem("Nincs elérhető NI eszköz", None)
            self.ni_device.setEnabled(False)
        else:
            self.ni_device.setEnabled(True)
            self.ni_device.addItem("Válassz felismert NI eszközt…", None)
        for name in sorted(devices, key=str.casefold):
            channel = devices[name]
            self.ni_device.addItem(channel.device_display_name, name)
            self.ni_device.setItemData(
                self.ni_device.count() - 1,
                channel.tooltip,
                Qt.ItemDataRole.ToolTipRole,
            )
        selected_index = self.ni_device.findData(selected)
        self.ni_device.setCurrentIndex(max(0, selected_index))
        self.ni_device.blockSignals(False)
        self._ni_device_changed()

    def _ni_device_changed(self, *_args: object) -> None:
        if self._active_ni_device is not None:
            self._ni_channel_selections[self._active_ni_device] = (
                self.line_channel.text(),
                self.delta_channel.text(),
                self.valve_channel.text(),
            )
        selected = self.ni_device.currentData()
        if not isinstance(selected, str) or not selected:
            self._active_ni_device = None
            for field in (self.line_channel, self.delta_channel, self.valve_channel):
                field.clear()
                field.addItem("Előbb válassz NI eszközt", None)
                field.setEnabled(False)
            return
        inputs = tuple(
            channel
            for channel in self._discovered_ni_inputs
            if channel.device_name == selected
        )
        outputs = tuple(
            channel
            for channel in self._discovered_ni_outputs
            if channel.device_name == selected
        )
        saved = self._ni_channel_selections.get(
            selected,
            (
                self._stored("line_pressure_channel", ""),
                self._stored("differential_pressure_channel", ""),
                self._stored("valve_output_channel", ""),
            ),
        )
        self._replace_ni_choices(self.line_channel, inputs, saved[0], 0)
        self._replace_ni_choices(self.delta_channel, inputs, saved[1], 1)
        self._replace_ni_choices(self.valve_channel, outputs, saved[2], 0)
        self._ensure_distinct_ni_inputs(self.line_channel)
        self.line_channel.setEnabled(bool(inputs))
        self.delta_channel.setEnabled(bool(inputs))
        self.valve_channel.setEnabled(bool(outputs))
        self._active_ni_device = selected

    def _refresh_hardware_choices(self) -> None:
        try:
            discovery = self._discoverer()
        except Exception as error:
            message = f"A felderítés sikertelen: {type(error).__name__}: {error}"
            self._discovery_status.setText(message)
            self._discovery_status.setStyleSheet("color:#b00020")
            self._discovery_status.setVisible(True)
            self._log_discovery(message, level="ERROR")
            return
        self._replace_port_choices(
            self.jacket_port,
            discovery.serial_ports,
            self._stored("jacket_port", ""),
        )
        self._replace_port_choices(
            self.injection_port,
            discovery.serial_ports,
            self._stored("injection_port", ""),
        )
        self._replace_ni_devices(
            discovery.ni_input_channels,
            discovery.ni_output_channels,
        )
        ni_device_count = len(
            {
                channel.device_name
                for channel in (*discovery.ni_input_channels, *discovery.ni_output_channels)
            }
        )
        summary = (
            f"{len(discovery.serial_ports)} soros csatlakozó és "
            f"{ni_device_count} NI eszköz található."
        )
        diagnostic_summary = (
            f"{len(discovery.serial_ports)} COM-port, "
            f"{len(discovery.ni_input_channels)} NI bemenet, "
            f"{len(discovery.ni_output_channels)} NI kimenet"
        )
        if discovery.serial_ports:
            port_names = "; ".join(port.display_name for port in discovery.serial_ports)
            diagnostic_summary = f"{diagnostic_summary}. Portok: {port_names}"
        ni_channels = (*discovery.ni_input_channels, *discovery.ni_output_channels)
        if ni_channels:
            channel_names = "; ".join(channel.display_name for channel in ni_channels)
            diagnostic_summary = f"{diagnostic_summary}. NI: {channel_names}"
        if discovery.warnings:
            summary = f"{summary} Egyes eszközök nem érhetők el; részletek a Developer naplóban."
            diagnostic_summary = f"{diagnostic_summary}. " + " ".join(
                discovery.warnings
            )
            self._discovery_status.setStyleSheet("color:#8a5a00")
            self._discovery_status.setVisible(True)
        else:
            self._discovery_status.setStyleSheet("color:#1b7f3a")
            self._discovery_status.setVisible(self._developer_mode)
        self._discovery_status.setText(summary)
        self._log_discovery(
            diagnostic_summary,
            level="WARNING" if discovery.warnings else "INFO",
        )

    def _log_discovery(self, message: str, *, level: str) -> None:
        if self._diagnostics is not None:
            self._diagnostics.emit(
                DiagnosticCategory.SYSTEM,
                "DISCOVERY",
                message,
                level=level,
            )

    def _read_configuration(self) -> HardwareConfiguration:
        if not isinstance(self.jacket_port.currentData(), str):
            raise ValueError("előbb válaszd ki a köpenypumpa elérhető csatlakozóját")
        if not isinstance(self.injection_port.currentData(), str):
            raise ValueError("előbb válaszd ki a besajtolópumpa elérhető csatlakozóját")
        if not isinstance(self.ni_device.currentData(), str):
            raise ValueError("előbb válassz egy felismert NI eszközt")
        if not isinstance(self.line_channel.currentData(), str):
            raise ValueError("nincs elérhető vonali nyomáscsatorna")
        if not isinstance(self.delta_channel.currentData(), str):
            raise ValueError("nincs elérhető differenciálnyomás-csatorna")
        if not isinstance(self.valve_channel.currentData(), str):
            raise ValueError("nincs elérhető szelepvezérlő csatorna")
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
        self._settings.setValue("hardware/ni_device_name", self.ni_device.currentData())
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
            "padding:10px;background:transparent;color:#d32f4b;font-weight:800;"
            "border:1px solid #d32f4b;border-radius:6px"
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
        path_label = QLabel(
            f"Alkalmazásnapló: {logger.path}\n"
            f"Hardverkommunikáció: {logger.hardware_path}"
        )
        path_label.setStyleSheet("color:#66788a")
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
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
        phase_name: str,
        data_root: Path,
        synchronizer: BackgroundNasSynchronizer,
        settings: QSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._source_path = source_path
        self._project_name = project_name
        self._phase_name = phase_name
        self._export_name = f"{project_name}_{phase_name}"
        self._data_root = data_root
        self._synchronizer = synchronizer
        self._settings = settings
        self._bridge = DataManagementBridge(self)
        self._bridge.completed.connect(self._operation_completed)
        self._bridge.failed.connect(self._operation_failed)
        self.setWindowTitle("Adatkezelés és export")
        self.resize(650, 360)
        layout = QVBoxLayout(self)

        source_box = QGroupBox("Aktív mérési fázis nyers adatai")
        source_layout = QFormLayout(source_box)
        source_layout.addRow("Projekt", QLabel(project_name))
        source_layout.addRow("Mérési fázis", QLabel(phase_name))
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
            self._source_path.parent / f"{safe_filename(self._export_name)}_export.csv"
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
            self._source_path.parent / f"{safe_filename(self._export_name)}.xlsx"
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
            for source in (self._data_root / "projects").rglob("*_live_raw.csv"):
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


class MeasurementHistoryView(QWidget):
    SERIES = (
        ("jacket_pressure_bar", "Köpenynyomás", "#1565c0"),
        ("injection_pressure_bar", "Besajtolási nyomás", "#c62828"),
        ("line_pressure_bar", "Vonali nyomás", "#2e7d32"),
        ("differential_pressure_bar", "Differenciálnyomás", "#8e24aa"),
        ("jacket_flow_ml_per_hour", "Köpeny térfogatáram", "#00838f"),
        ("jacket_remaining_volume_ml", "Köpeny maradék térfogat", "#5c6bc0"),
        ("injection_flow_ml_per_hour", "Besajtolási térfogatáram", "#ef6c00"),
        ("injection_remaining_volume_ml", "Besajtolás maradék térfogat", "#d81b60"),
        ("jacket_net_volume_ml", "Köpeny nettó térfogat", "#5e35b1"),
        ("injection_net_volume_ml", "Besajtolás nettó térfogat", "#6d4c41"),
        ("valve_percent", "Szelep", "#546e7a"),
    )

    def __init__(
        self,
        source_path: Path | Iterable[Path] = (),
        project_name: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._source_paths = (
            (source_path,) if isinstance(source_path, Path) else tuple(source_path)
        )
        self._project_name = project_name
        self.setObjectName("measurement_history_view")
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        controls = QGridLayout()
        self._checks: dict[str, QCheckBox] = {}
        for index, (key, label, _color) in enumerate(self.SERIES):
            checkbox = QCheckBox(label)
            checkbox.setChecked(index < 4)
            checkbox.toggled.connect(self._refresh_plot)
            self._checks[key] = checkbox
            row, column = divmod(index, 4)
            controls.addWidget(checkbox, row, column)
        layout.addLayout(controls)

        range_grid = QGridLayout()
        self._stage_filter = QComboBox()
        self._stage_filter.setObjectName("history_stage_filter")
        self._stage_filter.addItem("Összes mérési fázis", None)
        self._stage_filter.currentIndexChanged.connect(self._refresh_plot)
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
        range_grid.addWidget(QLabel("Mérési fázis"), 0, 0)
        range_grid.addWidget(self._stage_filter, 0, 1)
        range_grid.addWidget(QLabel("Időtartomány"), 0, 2)
        range_grid.addWidget(self._time_range, 0, 3)
        range_grid.addWidget(self._custom_minutes, 0, 4)
        range_grid.addWidget(self._auto_y, 1, 0, 1, 2)
        range_grid.addWidget(QLabel("Y minimum"), 1, 2)
        range_grid.addWidget(self._y_min, 1, 3)
        range_grid.addWidget(QLabel("Y maximum"), 1, 4)
        range_grid.addWidget(self._y_max, 1, 5)
        range_grid.addWidget(refresh, 0, 5)
        range_grid.setColumnStretch(1, 1)
        range_grid.setColumnStretch(3, 1)
        layout.addLayout(range_grid)

        self._plot = pg.PlotWidget(title="Teljes rögzített mérés")
        self._plot.setLabel("left", "Érték")
        self._plot.setLabel("bottom", "Eltelt idő", units="s")
        self._plot.showGrid(x=True, y=True, alpha=0.25)
        self._plot.setMouseEnabled(x=True, y=True)
        layout.addWidget(self._plot, stretch=1)
        self._stage_plot = pg.PlotWidget(title="Mérési fázisok idővonala")
        self._stage_plot.setObjectName("measurement_stage_timeline")
        self._stage_plot.setMaximumHeight(130)
        self._stage_plot.setMouseEnabled(x=True, y=False)
        self._stage_plot.setYRange(0.0, 1.0, padding=0.0)
        self._stage_plot.hideAxis("left")
        self._stage_plot.setLabel("bottom", "Eltelt idő", units="s")
        self._stage_plot.setXLink(self._plot)
        layout.addWidget(self._stage_plot)
        self._status = QLabel()
        layout.addWidget(self._status)
        self._table = MeasurementTable((), ())
        self._load()

    def set_sources(
        self, source_paths: Iterable[Path], project_name: str = ""
    ) -> None:
        self._source_paths = tuple(source_paths)
        self._project_name = project_name
        self._load()

    def _load(self) -> None:
        try:
            self._table = read_measurement_tables(self._source_paths)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "Mérési adatok", str(error))
            return
        selected_stage = self._stage_filter.currentData()
        self._stage_filter.blockSignals(True)
        self._stage_filter.clear()
        self._stage_filter.addItem("Összes mérési fázis", None)
        for stage in measurement_stages(self._table):
            self._stage_filter.addItem(stage, stage)
        selected_index = self._stage_filter.findData(selected_stage)
        self._stage_filter.setCurrentIndex(max(0, selected_index))
        self._stage_filter.blockSignals(False)
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
        self._stage_plot.clear()
        if not self._table.rows:
            self._status.setText(
                f"Nincs rögzített minta — {len(self._source_paths)} fázisfájl"
            )
            return
        times = self._elapsed_times()
        selected_stage = self._stage_filter.currentData()
        stage = selected_stage if isinstance(selected_stage, str) else None
        filtered_table = filter_measurement_table_by_stage(self._table, stage)
        stage_index = self._table.header.index("active_stage")
        selected_indices = [
            index
            for index, row in enumerate(self._table.rows)
            if stage is None or row[stage_index] == stage
        ]
        seconds = self._time_range.currentData()
        if seconds == "custom":
            seconds = self._custom_minutes.value() * 60.0
        minimum_time = times[-1] - float(seconds) if isinstance(seconds, float) else times[0]
        selected_indices = [
            index for index in selected_indices if times[index] >= minimum_time
        ]
        series = numeric_series(self._table, (item[0] for item in self.SERIES))
        self._plot.addLegend()
        for key, label, color in self.SERIES:
            if self._checks[key].isChecked():
                self._plot.plot(
                    [times[index] for index in selected_indices],
                    [series[key][index] for index in selected_indices],
                    pen=color,
                    name=label,
                )
        self._draw_stage_timeline(times, stage)
        duration = (
            times[selected_indices[-1]] - times[selected_indices[0]]
            if len(selected_indices) > 1
            else 0.0
        )
        stage_label = stage or "Összes mérési fázis"
        self._status.setText(
            f"{stage_label}: {len(filtered_table.rows)} rögzített minta, "
            f"{duration:.1f} s megjelenített időtartam — "
            f"{len(self._source_paths)} fázisfájl"
        )
        if self._auto_y.isChecked():
            self._plot.enableAutoRange(axis="y")
        elif self._y_max.value() > self._y_min.value():
            self._plot.setYRange(self._y_min.value(), self._y_max.value(), padding=0.0)
        if isinstance(seconds, float):
            self._plot.setXRange(max(times[0], minimum_time), times[-1], padding=0.0)
        else:
            self._plot.enableAutoRange(axis="x")

    def _draw_stage_timeline(
        self, times: tuple[float, ...], selected_stage: str | None
    ) -> None:
        palette = (
            "#1565c0",
            "#2e7d32",
            "#ef6c00",
            "#8e24aa",
            "#00838f",
            "#c62828",
        )
        colors = {
            stage: palette[index % len(palette)]
            for index, stage in enumerate(measurement_stages(self._table))
        }
        for stage, start, end in measurement_stage_segments(self._table):
            if selected_stage is not None and stage != selected_stage:
                continue
            start_time = times[start]
            if end < len(times):
                end_time = times[end]
            elif end - start > 1:
                end_time = times[end - 1] + (times[end - 1] - times[end - 2])
            else:
                end_time = start_time + 1.0
            color = colors.get(stage, "#607d8b")
            region = pg.LinearRegionItem(
                values=(start_time, end_time),
                movable=False,
                brush=pg.mkBrush(f"{color}66"),
                pen=pg.mkPen(color),
            )
            self._stage_plot.addItem(region)
            label = pg.TextItem(stage or "Nincs fázis", color="#e6edf3", anchor=(0.5, 0.5))
            label.setPos((start_time + end_time) / 2.0, 0.5)
            self._stage_plot.addItem(label)


class CalibrationSettingsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Kalibráció és biztonsági határértékek")
        self.resize(620, 520)
        layout = QVBoxLayout(self)
        help_text = QLabel(
            "A kalibráció a mért feszültséget alakítja fizikai nyomássá. "
            "A biztonsági határértékek túllépése safe-state állapotot vált ki."
        )
        help_text.setWordWrap(True)
        help_text.setStyleSheet("padding:8px;color:#66788a")
        layout.addWidget(help_text)
        tabs = QTabWidget()
        tabs.setObjectName("calibration_settings_tabs")
        layout.addWidget(tabs, 1)

        calibration_page = QWidget()
        calibration_layout = QVBoxLayout(calibration_page)
        self.line_voltage_min = self._value_spinbox(1.0, -10.0, 10.0, " V")
        self.line_voltage_max = self._value_spinbox(5.0, -10.0, 10.0, " V")
        self.line_value_min = self._value_spinbox(0.0, -1000.0, 1000.0, " bar")
        self.line_value_max = self._value_spinbox(400.0, -1000.0, 1000.0, " bar")
        self.delta_voltage_min = self._value_spinbox(1.0, -10.0, 10.0, " V")
        self.delta_voltage_max = self._value_spinbox(5.0, -10.0, 10.0, " V")
        self.delta_value_min = self._value_spinbox(0.0, -1000.0, 1000.0, " bar")
        self.delta_value_max = self._value_spinbox(40.0, -1000.0, 1000.0, " bar")
        for title, fields in (
            (
                "Vonali nyomásérzékelő",
                (
                    ("Minimum bemeneti feszültség", self.line_voltage_min),
                    ("Maximum bemeneti feszültség", self.line_voltage_max),
                    ("Minimum nyomásérték", self.line_value_min),
                    ("Maximum nyomásérték", self.line_value_max),
                ),
            ),
            (
                "Differenciálnyomás-érzékelő",
                (
                    ("Minimum bemeneti feszültség", self.delta_voltage_min),
                    ("Maximum bemeneti feszültség", self.delta_voltage_max),
                    ("Minimum nyomásérték", self.delta_value_min),
                    ("Maximum nyomásérték", self.delta_value_max),
                ),
            ),
        ):
            box = QGroupBox(title)
            form = QFormLayout(box)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            for label, field in fields:
                form.addRow(label, field)
            calibration_layout.addWidget(box)
        calibration_layout.addStretch()
        tabs.addTab(calibration_page, "Érzékelők kalibrációja")

        safety_page = QWidget()
        safety_layout = QVBoxLayout(safety_page)
        safety_box = QGroupBox("Nyomás- és szabályozási korlátok")
        safety_form = QFormLayout(safety_box)
        safety_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        self.max_jacket = self._value_spinbox(400.0, 0.1, 1000.0, " bar")
        self.max_injection = self._value_spinbox(350.0, 0.1, 1000.0, " bar")
        self.max_line = self._value_spinbox(400.0, 0.1, 1000.0, " bar")
        self.max_delta = self._value_spinbox(50.0, 0.1, 1000.0, " bar")
        self.minimum_margin = self._value_spinbox(20.0, 20.0, 1000.0, " bar")
        self.max_overshoot = self._value_spinbox(5.0, 0.1, 1000.0, " bar")
        for label, field in (
            ("Köpenypumpa maximális nyomása", self.max_jacket),
            ("Besajtolópumpa maximális nyomása", self.max_injection),
            ("Vonali nyomás maximuma", self.max_line),
            ("Differenciálnyomás maximuma", self.max_delta),
            ("Köpenynyomás minimális többlete", self.minimum_margin),
            ("Célérték maximális túllövése", self.max_overshoot),
        ):
            safety_form.addRow(label, field)
        safety_layout.addWidget(safety_box)
        safety_note = QLabel(
            "A köpenynyomás minimális többlete üzemi mérésnél nem lehet 20 bar alatti."
        )
        safety_note.setWordWrap(True)
        safety_note.setStyleSheet("padding:8px;color:#8a5a00;font-weight:600")
        safety_layout.addWidget(safety_note)
        safety_layout.addStretch()
        tabs.addTab(safety_page, "Biztonsági határértékek")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(
            "Mentés és alkalmazás"
        )
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _value_spinbox(
        value: float, minimum: float, maximum: float, suffix: str
    ) -> QDoubleSpinBox:
        spinbox = QDoubleSpinBox()
        spinbox.setRange(minimum, maximum)
        spinbox.setDecimals(4)
        spinbox.setValue(value)
        spinbox.setSuffix(suffix)
        spinbox.setMinimumWidth(150)
        return spinbox

    def snapshot(self) -> tuple[float, ...]:
        return tuple(field.value() for field in self._fields())

    def restore_snapshot(self, values: tuple[float, ...]) -> None:
        for field, value in zip(self._fields(), values, strict=True):
            field.setValue(value)

    def _fields(self) -> tuple[QDoubleSpinBox, ...]:
        return (
            self.line_voltage_min,
            self.line_voltage_max,
            self.line_value_min,
            self.line_value_max,
            self.delta_voltage_min,
            self.delta_voltage_max,
            self.delta_value_min,
            self.delta_value_max,
            self.max_jacket,
            self.max_injection,
            self.max_delta,
            self.max_line,
            self.minimum_margin,
            self.max_overshoot,
        )

    def _accept_if_valid(self) -> None:
        try:
            LinearCalibration(*self.line_values())
            LinearCalibration(*self.delta_values())
            SafetyLimits(*self.safety_values())
        except ValueError as error:
            QMessageBox.critical(self, "Érvénytelen beállítás", str(error))
            return
        self.accept()

    def line_values(self) -> list[float]:
        return [
            self.line_voltage_min.value(),
            self.line_voltage_max.value(),
            self.line_value_min.value(),
            self.line_value_max.value(),
        ]

    def delta_values(self) -> list[float]:
        return [
            self.delta_voltage_min.value(),
            self.delta_voltage_max.value(),
            self.delta_value_min.value(),
            self.delta_value_max.value(),
        ]

    def safety_values(self) -> tuple[float, ...]:
        return (
            self.max_jacket.value(),
            self.max_injection.value(),
            self.max_delta.value(),
            self.minimum_margin.value(),
            self.max_overshoot.value(),
            self.max_line.value(),
        )


class MeasurementOverviewDialog(QDialog):
    calibration_requested = Signal()

    SECTIONS = (
        (
            "Aktív mérés",
            (
                ("state", "Rendszerállapot"),
                ("mode", "Üzemmód"),
                ("project", "Aktív projekt"),
                ("stage", "Mérési fázis"),
                ("control_mode", "Szelepvezérlés módja"),
                ("pressure_source", "Szabályozott nyomásforrás"),
                ("setpoint", "Beállított célérték"),
                ("recording_interval", "Adatrögzítési időköz"),
                ("last_update", "Utolsó adatfrissítés"),
                ("data_quality", "Adatminőség"),
                ("alarm", "Riasztás / biztonsági állapot"),
            ),
        ),
        (
            "Pumpák élő állapota",
            (
                ("jacket_connection", "Köpenypumpa kapcsolat"),
                ("jacket_pressure", "Köpenypumpa nyomása"),
                ("jacket_remaining", "Köpenypumpa maradék térfogata"),
                ("jacket_net_volume", "Indítás óta nettó köpenytérfogat"),
                ("injection_connection", "Besajtolópumpa kapcsolat"),
                ("injection_pressure", "Besajtolópumpa nyomása"),
                ("injection_remaining", "Besajtolópumpa maradék térfogata"),
                ("injection_flow", "Besajtolási sebesség"),
                ("injected_volume", "Indítás óta nettó besajtolt térfogat"),
            ),
        ),
        (
            "NI mérés és szelep",
            (
                ("line_connection", "Vonali nyomás kapcsolat"),
                ("line_pressure", "Vonali nyomás"),
                ("delta_connection", "Differenciálnyomás kapcsolat"),
                ("delta_pressure", "Differenciálnyomás"),
                ("valve_connection", "Szelep kapcsolat"),
                ("valve_output", "Szelep kimenete"),
            ),
        ),
        (
            "Kalibráció és biztonság",
            (
                ("line_calibration", "Vonali érzékelő kalibrációja"),
                ("delta_calibration", "Differenciálérzékelő kalibrációja"),
                ("max_jacket", "Köpenynyomás maximuma"),
                ("max_injection", "Besajtolási nyomás maximuma"),
                ("max_line", "Vonali nyomás maximuma"),
                ("max_delta", "Differenciálnyomás maximuma"),
                ("minimum_margin", "Minimális köpenynyomás-többlet"),
                ("max_overshoot", "Maximális céltúllövés"),
            ),
        ),
    )

    def __init__(
        self,
        provider: Callable[[], dict[str, str]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._provider = provider
        self.setWindowTitle("Mérési áttekintés")
        self.resize(860, 720)
        layout = QVBoxLayout(self)
        title = QLabel("Részletes mérési és rendszeráttekintés")
        title.setStyleSheet("font-size:18px;font-weight:700")
        layout.addWidget(title)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        self.value_labels: dict[str, QLabel] = {}
        for section_title, fields in self.SECTIONS:
            box = QGroupBox(section_title)
            form = QFormLayout(box)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            for key, label in fields:
                value = QLabel("—")
                value.setWordWrap(True)
                value.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse
                )
                form.addRow(label, value)
                self.value_labels[key] = value
            content_layout.addWidget(box)
        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        actions = QHBoxLayout()
        calibration_button = QPushButton("Kalibráció és határértékek beállítása…")
        calibration_button.clicked.connect(self.calibration_requested.emit)
        actions.addWidget(calibration_button)
        actions.addStretch()
        close_button = QPushButton("Bezárás")
        close_button.clicked.connect(self.close)
        actions.addWidget(close_button)
        layout.addLayout(actions)
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        values = self._provider()
        for key, label in self.value_labels.items():
            label.setText(values.get(key, "—"))

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.refresh()
        self._timer.start()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._timer.stop()
        super().closeEvent(event)


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
        settings: QSettings | None = None,
    ) -> None:
        super().__init__()
        self._user_settings = settings or portable_user_settings()
        self._developer_mode = str(
            self._user_settings.value("developer/enabled", "false")
        ).lower() in {"1", "true", "yes", "on"}
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
        self._last_cycle_result: ControlCycleResult | None = None
        self._overview_dialog: MeasurementOverviewDialog | None = None
        self._active_alarm_text = "Nincs aktív riasztás"
        self._current_mode_message = ""
        self._last_notification_key: str | None = None
        self._tray_icon = QSystemTrayIcon(application_icon(), self)
        self._tray_icon.setToolTip("AFKI EOR mérőrendszer")
        self._tray_available = QSystemTrayIcon.isSystemTrayAvailable()
        if self._tray_available:
            self._tray_icon.show()
        self._diagnostics = DiagnosticLogger(
            data_directory / "logs" / "application.log",
            hardware_path=data_directory / "logs" / "hardware_communication.log",
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
        self._project_selector_required = not self._restore_project_selection()
        self._project_selector_prompted = False
        self._refresh_state()

    def _build_ui(self) -> None:
        self.setWindowTitle("AFKI EOR mérőrendszer — szimuláció")
        self.resize(1100, 720)
        root = QWidget()
        layout = QVBoxLayout(root)

        self._refresh_mode_label()

        status_container = QWidget()
        status_container.setObjectName("status_sidebar")
        status_container.setMinimumWidth(0)
        status_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding
        )
        status_layout = QVBoxLayout(status_container)
        status_layout.setContentsMargins(4, 0, 4, 4)
        status_title = QLabel("ÉLŐ ÁLLAPOTOK")
        status_title.setStyleSheet("font-size:13px;font-weight:700;padding:4px")
        status_layout.addWidget(status_title)
        self._state_label = QLabel()
        self._jacket_label = QLabel("— bar")
        self._injection_label = QLabel("— bar")
        self._jacket_remaining_label = QLabel("Maradék folyadék: — ml")
        self._jacket_net_volume_label = QLabel(
            "Indítás óta nettó köpenytérfogat: — ml"
        )
        self._injection_remaining_label = QLabel("Maradék folyadék: — ml")
        self._injection_flow_label = QLabel("Besajtolási sebesség: — ml/h")
        self._injected_volume_label = QLabel("Indítás óta nettó besajtolt: — ml")
        volume_tooltip = (
            "Negatív érték esetén a pumpa maradék térfogata az indításkori "
            "érték fölé nőtt."
        )
        self._jacket_net_volume_label.setToolTip(volume_tooltip)
        self._injected_volume_label.setToolTip(volume_tooltip)
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
            value.setStyleSheet(
                "background:transparent;font-size:20px;font-weight:600"
            )
            value.setWordWrap(True)
            box_layout.addWidget(value)
            if title == "Köpenypumpa":
                self._jacket_remaining_label.setStyleSheet(
                    "background:transparent;color:#66788a;font-size:12px;font-weight:600"
                )
                self._jacket_remaining_label.setWordWrap(True)
                box_layout.addWidget(self._jacket_remaining_label)
                self._jacket_net_volume_label.setStyleSheet(
                    "background:transparent;color:#66788a;font-size:12px;font-weight:600"
                )
                self._jacket_net_volume_label.setWordWrap(True)
                box_layout.addWidget(self._jacket_net_volume_label)
            elif title == "Besajtolópumpa":
                for detail in (
                    self._injection_remaining_label,
                    self._injection_flow_label,
                    self._injected_volume_label,
                ):
                    detail.setStyleSheet(
                        "background:transparent;color:#66788a;"
                        "font-size:12px;font-weight:600"
                    )
                    detail.setWordWrap(True)
                    box_layout.addWidget(detail)
            connection_key = connection_keys[index]
            if connection_key is not None:
                connection = QLabel("NINCS ADAT")
                connection.setStyleSheet(
                    "background:transparent;color:#66788a;"
                    "font-size:11px;font-weight:600"
                )
                connection.setWordWrap(True)
                box_layout.addWidget(connection)
                self._connection_labels[connection_key] = connection
            status_layout.addWidget(box)
        status_layout.addStretch(1)

        right_container = QWidget()
        right_container.setObjectName("control_sidebar")
        right_container.setMinimumWidth(0)
        right_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding
        )
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(4, 0, 4, 4)

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
        self._last_selected_stage_id: int | None = None
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
        project_layout.addWidget(add_stage, 3, 0)
        project_layout.addWidget(rename_stage, 3, 1, 1, 2)
        right_layout.addWidget(project_box)
        project_box.setVisible(False)
        project_summary = QGroupBox("Aktív projekt")
        project_summary.setObjectName("active_project_summary")
        project_summary_layout = QFormLayout(project_summary)
        project_summary_layout.setRowWrapPolicy(
            QFormLayout.RowWrapPolicy.WrapLongRows
        )
        project_summary_layout.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        self._active_project_label = QLabel("Nincs kiválasztva")
        self._active_stage_label = QLabel("Nincs kiválasztva", project_summary)
        self._active_stage_label.hide()
        open_projects = QPushButton("Másik projekt megnyitása…")
        open_projects.clicked.connect(self._open_project_selector)
        open_overview = QPushButton("Részletes mérési áttekintés…")
        open_overview.clicked.connect(self._open_measurement_overview)
        project_summary_layout.addRow("Projekt", self._active_project_label)
        project_summary_layout.addRow("Szakasz", self._stage)
        project_summary_layout.addRow(open_projects)
        project_summary_layout.addRow(open_overview)
        right_layout.addWidget(project_summary)

        settings = QGroupBox("Szelepvezérlés")
        settings.setObjectName("valve_control_settings")
        form = QFormLayout(settings)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
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
        self._loading_pid_profile = False
        self._pid_profile = QComboBox()
        self._pid_profile.setObjectName("pid_profile_selector")
        self._save_pid_profile_button = QPushButton("Mentés…")
        self._delete_pid_profile_button = QPushButton("Törlés")
        profile_actions = QWidget()
        profile_actions_layout = QHBoxLayout(profile_actions)
        profile_actions_layout.setContentsMargins(0, 0, 0, 0)
        profile_actions_layout.addWidget(self._save_pid_profile_button)
        profile_actions_layout.addWidget(self._delete_pid_profile_button)
        apply_pid = QPushButton("PID beállítások alkalmazása")
        apply_pid.clicked.connect(self._apply_pid)
        form.addRow("Mód", self._mode)
        form.addRow("Nyomásforrás", self._source)
        form.addRow("Kézi kimenet", self._manual_output)
        form.addRow("Célérték", self._setpoint)
        form.addRow("Adatrögzítési időköz", self._recording_interval)
        form.addRow("PID ciklus", QLabel("100 ms (háttérszál)"))
        form.addRow("PID profil", self._pid_profile)
        form.addRow("", profile_actions)
        form.addRow("Kp", self._kp)
        form.addRow("Ki", self._ki)
        form.addRow("Kd", self._kd)
        form.addRow("Hatásirány", self._direction)
        form.addRow("Kimeneti minimum", self._output_min)
        form.addRow("Kimeneti maximum", self._output_max)
        form.addRow(apply_pid)
        for field in (
            self._mode,
            self._source,
            self._manual_output,
            self._setpoint,
            self._recording_interval,
            self._pid_profile,
            self._kp,
            self._ki,
            self._kd,
            self._direction,
            self._output_min,
            self._output_max,
        ):
            field.setMinimumWidth(0)
            policy = field.sizePolicy()
            policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
            field.setSizePolicy(policy)
            label = form.labelForField(field)
            if isinstance(label, QLabel):
                label.setWordWrap(True)
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
        self._pid_profile.currentIndexChanged.connect(self._pid_profile_changed)
        self._save_pid_profile_button.clicked.connect(self._save_pid_profile)
        self._delete_pid_profile_button.clicked.connect(self._delete_pid_profile)
        for widget in (
            self._source,
            self._kp,
            self._ki,
            self._kd,
            self._direction,
            self._output_min,
            self._output_max,
        ):
            if isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self._pid_values_changed)
            else:
                widget.valueChanged.connect(self._pid_values_changed)
        self._reload_pid_profiles()
        right_layout.addWidget(settings)

        self._measurement_settings = CalibrationSettingsDialog(self)
        self._line_voltage_min = self._measurement_settings.line_voltage_min
        self._line_voltage_max = self._measurement_settings.line_voltage_max
        self._line_value_min = self._measurement_settings.line_value_min
        self._line_value_max = self._measurement_settings.line_value_max
        self._delta_voltage_min = self._measurement_settings.delta_voltage_min
        self._delta_voltage_max = self._measurement_settings.delta_voltage_max
        self._delta_value_min = self._measurement_settings.delta_value_min
        self._delta_value_max = self._measurement_settings.delta_value_max
        self._max_jacket = self._measurement_settings.max_jacket
        self._max_injection = self._measurement_settings.max_injection
        self._max_delta = self._measurement_settings.max_delta
        self._max_line = self._measurement_settings.max_line
        self._minimum_margin = self._measurement_settings.minimum_margin
        self._max_overshoot = self._measurement_settings.max_overshoot
        right_layout.addStretch(1)

        self._plot = pg.PlotWidget(title="Elmúlt 10 perc nyomásai")
        self._plot.setObjectName("live_measurement_plot")
        self._plot.setMinimumWidth(0)
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
        self._flow_plot.setMinimumWidth(0)
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
        live_measurement_page = QWidget()
        live_measurement_page.setMinimumWidth(0)
        live_measurement_page.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        live_measurement_layout = QVBoxLayout(live_measurement_page)
        live_measurement_layout.setContentsMargins(0, 0, 0, 0)
        live_measurement_layout.addWidget(chart_splitter)
        self._history_view = MeasurementHistoryView(parent=self)
        self._measurement_tabs = QTabWidget()
        self._measurement_tabs.setObjectName("dashboard_measurement_tabs")
        self._measurement_tabs.setMinimumWidth(0)
        self._measurement_tabs.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        self._measurement_tabs.addTab(live_measurement_page, "Élő mérés")
        self._measurement_tabs.addTab(self._history_view, "Teljes mérés")
        self._measurement_tabs.currentChanged.connect(self._measurement_tab_changed)
        left_scroll = QScrollArea()
        left_scroll.setObjectName("status_scroll_area")
        left_scroll.setMinimumWidth(170)
        left_scroll.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        left_scroll.setSizeAdjustPolicy(
            QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored
        )
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        left_scroll.setWidget(status_container)
        right_scroll = QScrollArea()
        right_scroll.setObjectName("control_scroll_area")
        right_scroll.setMinimumWidth(260)
        right_scroll.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        right_scroll.setSizeAdjustPolicy(
            QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored
        )
        right_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        right_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        right_scroll.setWidget(right_container)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("dashboard_splitter")
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(5)
        splitter.setOpaqueResize(True)
        splitter.addWidget(left_scroll)
        splitter.addWidget(self._measurement_tabs)
        splitter.addWidget(right_scroll)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 5)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([270, 760, 340])
        layout.addWidget(splitter, stretch=1)
        self.setCentralWidget(root)

    def _build_menu(self) -> None:
        project_menu = self.menuBar().addMenu("Projekt")
        select_project = QAction("Projekt kiválasztása…", self)
        select_project.setShortcut("Ctrl+Shift+P")
        select_project.triggered.connect(self._open_project_selector)
        project_menu.addAction(select_project)
        open_project_settings = QAction("Projektkezelő…", self)
        open_project_settings.triggered.connect(self._open_project_settings)
        project_menu.addAction(open_project_settings)
        project_menu.addSeparator()
        data_management = QAction("Adatkezelés és export…", self)
        data_management.setShortcut("Ctrl+Shift+E")
        data_management.triggered.connect(self._open_data_management)
        project_menu.addAction(data_management)

        display_menu = self.menuBar().addMenu("Megjelenítés")
        overview = QAction("Mérési áttekintés…", self)
        overview.setShortcut("Ctrl+Shift+O")
        overview.triggered.connect(self._open_measurement_overview)
        display_menu.addAction(overview)
        full_history = QAction("Teljes mérés fül", self)
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
            "Kalibráció és biztonsági határértékek…", self
        )
        self._measurement_settings_action.triggered.connect(
            self._open_calibration_settings
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
        developer_mode = QAction("Developer mód", self, checkable=True)
        developer_mode.setChecked(self._developer_mode)
        developer_mode.toggled.connect(self._set_developer_mode)
        developer_menu.addAction(developer_mode)
        self._simulation_mode_action = QAction(
            "Szimulációs mód", self, checkable=True
        )
        self._simulation_mode_action.setChecked(
            self._run_mode is RunMode.SIMULATION
        )
        self._simulation_mode_action.setToolTip(
            "Szimulációban nincs fizikai kimenet és nem készül mérési adatfájl. "
            "Kikapcsoláskor az Eszközbeállítások ablakban aktiválható az éles mód."
        )
        self._simulation_mode_action.setVisible(self._developer_mode)
        self._simulation_mode_action.toggled.connect(self._simulation_mode_toggled)
        developer_menu.addAction(self._simulation_mode_action)
        self._developer_view_action = QAction("Eszközkommunikáció…", self)
        self._developer_view_action.setShortcut("Ctrl+Shift+L")
        self._developer_view_action.setVisible(self._developer_mode)
        self._developer_view_action.triggered.connect(self._open_developer_view)
        developer_menu.addAction(self._developer_view_action)

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
        stage_name = self._stage.currentText().strip()
        if not isinstance(project_id, int) or not stage_name:
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
            stage_name=stage_name,
        )

    def _open_data_management(self) -> None:
        source_path = self._current_project_file()
        if source_path is None:
            self._show_error("Az adatkezeléshez előbb válassz projektet.")
            return
        if not source_path.is_file():
            self._show_error(
                "Ehhez a fázishoz nincs mentett éles mérési adat. "
                "A szimulációs mérések nem kerülnek fájlba."
            )
            return
        DataManagementDialog(
            source_path=source_path,
            project_name=self._project.currentText(),
            phase_name=self._stage.currentText(),
            data_root=self._data_directory,
            synchronizer=self._nas_sync,
            settings=self._user_settings,
            parent=self,
        ).exec()

    def _open_measurement_history(self) -> None:
        if not self._refresh_measurement_history():
            return
        self._measurement_tabs.setCurrentWidget(self._history_view)

    def _refresh_measurement_history(self) -> bool:
        source_path = self._current_project_file()
        if source_path is None:
            self._show_error("A teljes grafikonhoz előbb válassz projektet.")
            return False
        phase_paths = self._measurement_writer.phase_paths or (source_path,)
        self._history_view.set_sources(phase_paths, self._project.currentText())
        return True

    def _measurement_tab_changed(self, index: int) -> None:
        if self._measurement_tabs.widget(index) is self._history_view:
            self._refresh_measurement_history()

    def _open_calibration_settings(self) -> None:
        if self._devices.status.state is ApplicationState.RUNNING:
            self._show_error("Futó mérés közben a kalibráció nem módosítható.")
            return
        snapshot = self._measurement_settings.snapshot()
        if self._measurement_settings.exec() != QDialog.DialogCode.Accepted:
            self._measurement_settings.restore_snapshot(snapshot)
            return
        self._apply_measurement_settings()
        self._save_user_settings()
        if self._overview_dialog is not None:
            self._overview_dialog.refresh()

    def _open_measurement_overview(self) -> None:
        if self._overview_dialog is None:
            self._overview_dialog = MeasurementOverviewDialog(
                self._overview_values, self
            )
            self._overview_dialog.calibration_requested.connect(
                self._open_calibration_settings
            )
        self._overview_dialog.show()
        self._overview_dialog.raise_()
        self._overview_dialog.activateWindow()

    def _overview_values(self) -> dict[str, str]:
        line = self._line_calibration_values()
        delta = self._delta_calibration_values()
        latest = self._last_cycle_result
        snapshot = latest.record.snapshot if latest is not None else None
        return {
            "state": self._state_label.text() or "—",
            "mode": (
                "ÉLES MÉRÉS (HARDVER, ADATMENTÉS AKTÍV)"
                if self._run_mode is RunMode.HARDWARE
                else "SZIMULÁCIÓ (NINCS ADATMENTÉS)"
            ),
            "project": self._active_project_label.text(),
            "stage": self._active_stage_label.text(),
            "control_mode": self._mode.currentText(),
            "pressure_source": self._source.currentText(),
            "setpoint": f"{self._setpoint.value():g} bar",
            "recording_interval": f"{self._recording_interval.value()} s",
            "last_update": (
                snapshot.recorded_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
                if snapshot is not None
                else "Nincs mérési adat"
            ),
            "data_quality": (
                snapshot.quality.value if snapshot is not None else "Nincs mérési adat"
            ),
            "alarm": self._active_alarm_text,
            "jacket_connection": self._connection_labels["jacket"].text(),
            "jacket_pressure": self._jacket_label.text(),
            "jacket_remaining": self._jacket_remaining_label.text(),
            "jacket_net_volume": self._jacket_net_volume_label.text(),
            "injection_connection": self._connection_labels["injection"].text(),
            "injection_pressure": self._injection_label.text(),
            "injection_remaining": self._injection_remaining_label.text(),
            "injection_flow": self._injection_flow_label.text(),
            "injected_volume": self._injected_volume_label.text(),
            "line_connection": self._connection_labels["line_daq"].text(),
            "line_pressure": self._line_label.text(),
            "delta_connection": self._connection_labels["delta_daq"].text(),
            "delta_pressure": self._delta_label.text(),
            "valve_connection": self._connection_labels["valve"].text(),
            "valve_output": self._valve_label.text(),
            "line_calibration": (
                f"{line[0]:g}–{line[1]:g} V → {line[2]:g}–{line[3]:g} bar"
            ),
            "delta_calibration": (
                f"{delta[0]:g}–{delta[1]:g} V → {delta[2]:g}–{delta[3]:g} bar"
            ),
            "max_jacket": f"{self._max_jacket.value():g} bar",
            "max_injection": f"{self._max_injection.value():g} bar",
            "max_line": f"{self._max_line.value():g} bar",
            "max_delta": f"{self._max_delta.value():g} bar",
            "minimum_margin": f"{self._minimum_margin.value():g} bar",
            "max_overshoot": f"{self._max_overshoot.value():g} bar",
        }

    def _open_logging_settings(self) -> None:
        LoggingSettingsDialog(
            self._diagnostics, self._user_settings, parent=self
        ).exec()

    def _open_developer_view(self) -> None:
        DeveloperViewDialog(self._diagnostics, parent=self).exec()

    def _set_developer_mode(self, enabled: bool) -> None:
        self._developer_mode = enabled
        self._simulation_mode_action.setVisible(enabled)
        self._developer_view_action.setVisible(enabled)
        self._user_settings.setValue("developer/enabled", enabled)
        self._user_settings.sync()

    def _simulation_mode_toggled(self, enabled: bool) -> None:
        try:
            if enabled:
                self._activate_simulation()
            elif self._run_mode is RunMode.SIMULATION:
                self._open_device_settings()
        except Exception as error:
            self._show_error(f"A szimulációs mód aktiválása sikertelen: {error}")
        self._sync_simulation_mode_action()

    def _sync_simulation_mode_action(self) -> None:
        blocked = self._simulation_mode_action.blockSignals(True)
        self._simulation_mode_action.setChecked(
            self._run_mode is RunMode.SIMULATION
        )
        self._simulation_mode_action.blockSignals(blocked)

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
        plots = (
            self._plot,
            self._flow_plot,
            self._history_view._plot,
            self._history_view._stage_plot,
        )
        if theme == "dark":
            application.setStyleSheet(resolved_theme_stylesheet(DARK_STYLESHEET, "dark"))
            for plot in plots:
                plot.setBackground("#15191f")
                plot.getAxis("left").setTextPen("#e6edf3")
                plot.getAxis("bottom").setTextPen("#e6edf3")
        elif theme == "light":
            application.setStyleSheet(
                resolved_theme_stylesheet(LIGHT_STYLESHEET, "light")
            )
            for plot in plots:
                plot.setBackground("#ffffff")
                plot.getAxis("left").setTextPen("#263238")
                plot.getAxis("bottom").setTextPen("#263238")
        else:
            application.setPalette(application.style().standardPalette())
            application.setStyleSheet(SYSTEM_STYLESHEET)
            for plot in plots:
                plot.setBackground(None)
        self._user_settings.setValue("theme", theme)
        self._user_settings.sync()
        if self._user_settings.status() != QSettings.Status.NoError:
            self.statusBar().showMessage(
                f"A témabeállítás nem menthető: {self._user_settings.fileName()}"
            )

    def _refresh_mode_label(self) -> None:
        if self._run_mode is RunMode.HARDWARE:
            self.setWindowTitle("AFKI EOR mérőrendszer — éles mérés")
            message = (
                "ÉLES MÉRÉS — FIZIKAI ESZKÖZÖK, ADATMENTÉS AKTÍV"
            )
            title = "Éles mérési mód"
        else:
            self.setWindowTitle("AFKI EOR mérőrendszer — szimuláció")
            message = (
                "SZIMULÁCIÓ — NINCS ADATMENTÉS, NINCS FIZIKAI KIMENET"
            )
            title = "Szimulációs mód"
        self._current_mode_message = message
        self._notify_user(
            title,
            message,
            critical=self._run_mode is RunMode.HARDWARE,
            notification_key=f"mode:{self._run_mode.value}",
        )

    def _notify_user(
        self,
        title: str,
        message: str,
        *,
        critical: bool,
        notification_key: str,
    ) -> None:
        if notification_key == self._last_notification_key:
            return
        self._last_notification_key = notification_key
        self.statusBar().showMessage(message, 10_000)
        if self._tray_available:
            icon = (
                QSystemTrayIcon.MessageIcon.Critical
                if critical
                else QSystemTrayIcon.MessageIcon.Information
            )
            self._tray_icon.showMessage(title, message, icon, 10_000)
        if self.isMinimized() or not self.isActiveWindow():
            self._request_taskbar_attention()

    def _request_taskbar_attention(self) -> None:
        QApplication.alert(self, 0)

    def _set_active_alarm(self, message: str) -> None:
        self._active_alarm_text = message
        self._notify_user(
            "EOR biztonsági riasztás",
            message,
            critical=True,
            notification_key=f"alarm:{message}",
        )

    def _clear_active_alarm(self) -> None:
        self._active_alarm_text = "Nincs aktív riasztás"
        if self._last_notification_key is not None and self._last_notification_key.startswith(
            "alarm:"
        ):
            self._last_notification_key = None

    def _open_device_settings(self) -> None:
        if (
            self._devices.status.state is not ApplicationState.IDLE
            or self._runtime.running
        ):
            self._show_error("Eszközmód csak leválasztott, IDLE állapotban módosítható.")
            return
        dialog = DeviceSettingsDialog(
            PhysicalHardwareConnectionTester(diagnostics=self._diagnostics),
            settings=self._user_settings,
            current_mode=self._run_mode,
            diagnostics=self._diagnostics,
            developer_mode=self._developer_mode,
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
            configuration_snapshot = self._current_configuration()
            configuration_snapshot["mode"] = "hardware"
            configuration_snapshot["measurement_kind"] = "live"
            writer.select_project_with_metadata(
                project.id,
                project.name,
                created_at=project.created_at,
                notes=project.notes,
                configuration=configuration_snapshot,
                calibration_snapshot=project.calibration_snapshot,
                stages=stage_snapshots(project),
                stage_name=self._stage.currentText() or "Mérés",
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
        self._sync_simulation_mode_action()
        self._diagnostics.emit(
            DiagnosticCategory.SYSTEM, "MODE", "hardware mode activated"
        )
        self._user_settings.setValue("hardware/last_test_succeeded", True)
        self._refresh_mode_label()
        self._refresh_state()

    def _activate_simulation(self) -> None:
        if self._run_mode is RunMode.SIMULATION:
            return
        if self._devices.status.state is not ApplicationState.IDLE:
            self._show_error(
                "Szimulációs módra csak leválasztott, IDLE állapotban lehet váltani."
            )
            return

        jacket = SimulatedPump(pressure_bar=120.0)
        injection = SimulatedPump(
            pressure_bar=100.0,
            flow_ml_per_hour=10.0,
            remaining_volume_ml=260.0,
        )
        daq = SimulatedDataAcquisition()
        daq.inputs.update(line_pressure=2.0, differential_pressure=1.5)
        actuator = SimulatedValveActuator()
        writer = ProjectMeasurementWriter(
            self._data_directory, self._nas_sync, enabled=False
        )
        measurement = MeasurementService(
            jacket_pump=jacket,
            injection_pump=injection,
            daq=daq,
            line_calibration=LinearCalibration(*self._line_calibration_values()),
            differential_calibration=LinearCalibration(
                *self._delta_calibration_values()
            ),
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
            persistence_enabled=False,
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
        try:
            self._control_loop.close()
        except Exception:
            new_loop.close()
            raise
        if self._pump_control is not None:
            self._pump_control.revoke()
        self._control_loop = new_loop
        self._measurement_writer = writer
        self._devices = DeviceControlService(
            jacket_pump=jacket, injection_pump=injection, daq=daq
        )
        self._pump_control = None
        self._runtime = self._make_runtime(new_loop)
        self._run_mode = RunMode.SIMULATION
        self._sync_simulation_mode_action()
        self._diagnostics.emit(
            DiagnosticCategory.SYSTEM, "MODE", "simulation mode activated"
        )
        self._refresh_mode_label()
        self._set_all_connections("LEVÁLASZTVA", ok=None)
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
        profile_id = self._stored_int("pid/last_profile_id")
        if profile_id is not None:
            profile_index = self._pid_profile.findData(profile_id)
            if profile_index >= 0:
                self._pid_profile.setCurrentIndex(profile_index)

    def _restore_project_selection(self) -> bool:
        project_id = self._stored_int("project/last_project_id")
        stage_id = self._stored_int("project/last_stage_id")
        self._reload_projects(project_id)
        if not isinstance(project_id, int) or self._project.currentData() != project_id:
            return False
        if stage_id is not None:
            index = self._stage.findData(stage_id)
            if index >= 0:
                self._stage.setCurrentIndex(index)
                self._active_stage_label.setText(self._stage.currentText())
        return True

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
        profile_id = self._pid_profile.currentData()
        if isinstance(profile_id, int):
            values["pid/last_profile_id"] = profile_id
        else:
            self._user_settings.remove("pid/last_profile_id")
        if isinstance(project_id, int):
            values["project/last_project_id"] = project_id
        else:
            self._user_settings.remove("project/last_project_id")
            self._user_settings.remove("project/last_stage_id")
        if isinstance(stage_id, int):
            values["project/last_stage_id"] = stage_id
            if isinstance(project_id, int):
                values[f"project/last_stage_by_project/{project_id}"] = stage_id
        elif isinstance(project_id, int):
            self._user_settings.remove("project/last_stage_id")
            self._user_settings.remove(f"project/last_stage_by_project/{project_id}")
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
            self._clear_active_alarm()
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

    def _reload_projects(self, selected_project_id: int | None = None) -> None:
        self._project.blockSignals(True)
        self._project.clear()
        for project in self._projects.list_projects():
            self._project.addItem(project.name, project.id)
        selected_index = -1
        if selected_project_id is not None:
            index = self._project.findData(selected_project_id)
            if index >= 0:
                selected_index = index
        self._project.setCurrentIndex(selected_index)
        self._project.blockSignals(False)
        self._reload_stages()

    def _reload_stages(
        self, *_args: object, selected_stage_id: int | None = None
    ) -> None:
        current_stage_id = self._stage.currentData()
        if selected_stage_id is None and isinstance(current_stage_id, int):
            selected_stage_id = current_stage_id
        self._stage.blockSignals(True)
        self._stage.clear()
        project_id = self._project.currentData()
        if project_id is None:
            self._active_project_label.setText("Nincs kiválasztva")
            self._active_stage_label.setText("Nincs kiválasztva")
            self._stage.addItem("Nincs kiválasztott projekt", None)
            self._stage.setEnabled(False)
            self._stage.blockSignals(False)
            self._stage_changed()
            return
        self._active_project_label.setText(self._project.currentText())
        project = self._projects.get_project(int(project_id))
        stages = self._projects.list_stages(project.id)
        for stage in stages:
            self._stage.addItem(stage.name, stage.id)
        if not stages:
            self._stage.addItem("Nincs elérhető mérési szakasz", None)
        if selected_stage_id is not None:
            selected_index = self._stage.findData(selected_stage_id)
            if selected_index >= 0:
                self._stage.setCurrentIndex(selected_index)
        if stages and not isinstance(self._stage.currentData(), int):
            self._stage.setCurrentIndex(0)
        self._stage.insertSeparator(self._stage.count())
        self._stage.addItem("+ Új szakasz hozzáadása…", ADD_STAGE_ACTION_DATA)
        self._stage.setEnabled(True)
        self._stage.blockSignals(False)
        self._stage_changed()

    def _stage_changed(self, *_args: object) -> None:
        stage_id = self._stage.currentData()
        if stage_id == ADD_STAGE_ACTION_DATA:
            previous_index = self._stage.findData(self._last_selected_stage_id)
            if previous_index < 0:
                previous_index = 0
            self._stage.blockSignals(True)
            self._stage.setCurrentIndex(previous_index)
            self._stage.blockSignals(False)
            self._add_stage()
            return
        self._active_stage_label.setText(
            self._stage.currentText() or "Nincs kiválasztva"
        )
        if isinstance(stage_id, int):
            self._last_selected_stage_id = stage_id
            self._current_project_file()
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
            self._stage.setToolTip("; ".join(details))
        else:
            self._active_stage_label.setToolTip("")
            self._stage.setToolTip("")
        if self._measurement_tabs.currentWidget() is self._history_view:
            if isinstance(stage_id, int):
                self._refresh_measurement_history()
            else:
                self._history_view.set_sources(())
        self._update_runtime_settings()

    def _open_project_settings(self) -> None:
        if self._devices.status.state is ApplicationState.RUNNING:
            self._show_error("Futó mérés közben az aktív projekt nem módosítható.")
            return
        self._save_user_settings()
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
        result = dialog.exec()
        if result != QDialog.DialogCode.Accepted and not dialog.projects_changed:
            return
        self._reload_projects(dialog.selected_project_id)
        if (
            result == QDialog.DialogCode.Accepted
            and dialog.selected_stage_id is not None
        ):
            self._stage.setCurrentIndex(self._stage.findData(dialog.selected_stage_id))
            self._active_stage_label.setText(self._stage.currentText())
        self._save_user_settings()

    def _open_project_selector(self) -> None:
        if self._devices.status.state is ApplicationState.RUNNING:
            self._show_error("Futó mérés közben az aktív projekt nem módosítható.")
            return
        self._save_user_settings()
        dialog = ProjectSelectionDialog(
            self._projects,
            settings=self._user_settings,
            selected_project_id=(
                self._project.currentData()
                if isinstance(self._project.currentData(), int)
                else None
            ),
            selected_stage_id=(
                self._stage.currentData()
                if isinstance(self._stage.currentData(), int)
                else None
            ),
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
            index = self._stage.findData(dialog.selected_stage_id)
            if index >= 0:
                self._stage.setCurrentIndex(index)
        self._project_selector_required = False
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
        if self._devices.status.state is ApplicationState.RUNNING:
            self._show_error("Futó mérés közben új szakasz nem hozható létre.")
            return
        dialog = StageSettingsDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            values = dialog.values()
            stage = self._projects.add_stage(
                int(project_id),
                str(values["name"]),
                fluid=str(values["fluid"]),
                target_pressure_bar=cast(
                    float | None, values["target_pressure_bar"]
                ),
                target_flow_ml_per_hour=cast(
                    float | None, values["target_flow_ml_per_hour"]
                ),
                notes=str(values["notes"]),
            )
            self._reload_stages(selected_stage_id=stage.id)
            self._save_user_settings()
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

    def _reload_pid_profiles(self, selected_profile_id: int | None = None) -> None:
        self._pid_profile.blockSignals(True)
        self._pid_profile.clear()
        self._pid_profile.addItem("Egyéni beállítások", None)
        for profile in self._projects.list_pid_profiles():
            self._pid_profile.addItem(profile.name, profile.id)
        selected_index = self._pid_profile.findData(selected_profile_id)
        self._pid_profile.setCurrentIndex(max(0, selected_index))
        self._pid_profile.blockSignals(False)
        self._delete_pid_profile_button.setEnabled(selected_index > 0)

    def _pid_profile_changed(self, *_args: object) -> None:
        profile_id = self._pid_profile.currentData()
        self._delete_pid_profile_button.setEnabled(isinstance(profile_id, int))
        if not isinstance(profile_id, int):
            return
        try:
            self._load_pid_profile(self._projects.get_pid_profile(profile_id))
        except (KeyError, ValueError) as error:
            self._show_error(str(error))
            self._reload_pid_profiles()

    def _load_pid_profile(self, profile: PidProfile) -> None:
        self._loading_pid_profile = True
        try:
            self._kp.setValue(profile.kp)
            self._ki.setValue(profile.ki)
            self._kd.setValue(profile.kd)
            self._output_min.setValue(profile.output_min_percent)
            self._output_max.setValue(profile.output_max_percent)
            direction_index = self._direction.findData(profile.direction)
            source_index = self._source.findData(profile.pressure_source)
            if direction_index < 0 or source_index < 0:
                raise ValueError("A PID-profil ismeretlen vezérlési beállítást tartalmaz.")
            self._direction.setCurrentIndex(direction_index)
            self._source.setCurrentIndex(source_index)
        finally:
            self._loading_pid_profile = False
        self._apply_pid()
        self._update_runtime_settings()
        self._save_user_settings()

    def _pid_values_changed(self, *_args: object) -> None:
        if self._loading_pid_profile or not isinstance(
            self._pid_profile.currentData(), int
        ):
            return
        self._pid_profile.blockSignals(True)
        self._pid_profile.setCurrentIndex(0)
        self._pid_profile.blockSignals(False)
        self._delete_pid_profile_button.setEnabled(False)

    def _save_pid_profile(
        self, _checked: bool = False, *, profile_name: str | None = None
    ) -> None:
        del _checked
        current_name = (
            self._pid_profile.currentText()
            if isinstance(self._pid_profile.currentData(), int)
            else ""
        )
        if profile_name is None:
            profile_name, accepted = QInputDialog.getText(
                self,
                "PID-profil mentése",
                "Profil neve",
                text=current_name,
            )
            if not accepted:
                return
        existing = self._projects.get_pid_profile_by_name(profile_name)
        if existing is not None:
            answer = QMessageBox.question(
                self,
                "PID-profil felülírása",
                f"A(z) „{existing.name}” profil már létezik. Felülírod?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            profile = self._projects.save_pid_profile(
                name=profile_name,
                kp=self._kp.value(),
                ki=self._ki.value(),
                kd=self._kd.value(),
                direction=ControlDirection(self._direction.currentData()).value,
                output_min_percent=self._output_min.value(),
                output_max_percent=self._output_max.value(),
                pressure_source=PressureSource(self._source.currentData()).value,
            )
            self._reload_pid_profiles(profile.id)
            self._save_user_settings()
        except ValueError as error:
            self._show_error(str(error))

    def _delete_pid_profile(self) -> None:
        profile_id = self._pid_profile.currentData()
        if not isinstance(profile_id, int):
            return
        name = self._pid_profile.currentText()
        answer = QMessageBox.question(
            self,
            "PID-profil törlése",
            f"Biztosan törlöd ezt a PID-profilt: {name}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._projects.delete_pid_profile(profile_id)
            self._reload_pid_profiles()
            self._user_settings.remove("pid/last_profile_id")
            self._user_settings.sync()
        except KeyError as error:
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
            "mode": self._run_mode.value,
            "measurement_kind": (
                "live" if self._run_mode is RunMode.HARDWARE else "simulation"
            ),
            "pid": {
                "profile_id": (
                    self._pid_profile.currentData()
                    if isinstance(self._pid_profile.currentData(), int)
                    else None
                ),
                "profile_name": (
                    self._pid_profile.currentText()
                    if isinstance(self._pid_profile.currentData(), int)
                    else ""
                ),
                "kp": self._kp.value(),
                "ki": self._ki.value(),
                "kd": self._kd.value(),
                "direction": ControlDirection(self._direction.currentData()).value,
                "output_min_percent": self._output_min.value(),
                "output_max_percent": self._output_max.value(),
                "pressure_source": PressureSource(self._source.currentData()).value,
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
            self._last_cycle_result = None
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
        self._set_active_alarm("RETESSZELT HIBA: kézi vészleállítás")
        self._refresh_state()

    def _handle_cycle(self, result: object) -> None:
        if not isinstance(result, ControlCycleResult):
            self._handle_runtime_fault("invalid result from control thread")
            return
        self._last_cycle_result = result
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
        self._jacket_net_volume_label.setText(
            f"Indítás óta nettó köpenytérfogat: "
            f"{result.record.jacket_net_volume_ml:.1f} ml"
        )
        self._injection_remaining_label.setText(
            f"Maradék folyadék: {snapshot.injection_pump.remaining_volume_ml:.1f} ml"
        )
        self._injection_flow_label.setText(
            f"Besajtolási sebesség: {snapshot.injection_pump.flow_ml_per_hour:.1f} ml/h"
        )
        self._injected_volume_label.setText(
            f"Indítás óta nettó besajtolt: "
            f"{result.record.injection_net_volume_ml:.1f} ml"
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
            self._set_active_alarm("; ".join(result.record.safety_reasons))

    def _handle_runtime_fault(self, message: str) -> None:
        self._diagnostics.emit(
            DiagnosticCategory.RUNTIME, "FAULT", message, level="ERROR"
        )
        if self._devices.status.state is not ApplicationState.FAULT:
            self._devices.emergency_stop(f"control runtime failed: {message}")
        if self._pump_control is not None:
            self._pump_control.revoke()
            self._pump_control = None
        self._set_active_alarm(f"RETESSZELT VEZÉRLÉSI HIBA: {message}")
        self._set_all_connections("HIBA", ok=False)
        self._refresh_state()

    def _set_connection(self, key: str, connected: bool) -> None:
        label = self._connection_labels[key]
        label.setText("KAPCSOLÓDVA" if connected else "HIBA")
        label.setStyleSheet(
            "background:transparent;color:#1b7f3a;font-size:11px;font-weight:700"
            if connected
            else "background:transparent;color:#b00020;font-size:11px;font-weight:700"
        )

    def _set_all_connections(self, text: str, *, ok: bool | None) -> None:
        color = "#1b7f3a" if ok is True else "#b00020" if ok is False else "#66788a"
        for label in self._connection_labels.values():
            label.setText(text)
            label.setStyleSheet(
                f"background:transparent;color:{color};font-size:11px;font-weight:700"
            )

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
        self._measurement_settings_action.setEnabled(
            state is not ApplicationState.RUNNING
        )

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "EOR hiba", message)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self._project_selector_required and not self._project_selector_prompted:
            self._project_selector_prompted = True
            QTimer.singleShot(0, self._open_project_selector)

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
    data_path: Path,
    project_path: Path | None = None,
    *,
    settings: QSettings | None = None,
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
    writer = ProjectMeasurementWriter(data_path.parent, nas_sync, enabled=False)
    measurement = MeasurementService(
        jacket_pump=jacket,
        injection_pump=injection,
        daq=daq,
        line_calibration=LinearCalibration(1.0, 5.0, 0.0, 400.0),
        differential_calibration=LinearCalibration(1.0, 5.0, 0.0, 40.0),
        safety_monitor=safety,
        writer=writer,
        persistence_enabled=False,
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
        settings=settings,
    )


def run_ui() -> int:
    root = application_root_path()
    settings = portable_user_settings(root)
    configure_windows_application_identity()
    instance = QApplication.instance()
    application = instance if isinstance(instance, QApplication) else QApplication(sys.argv)
    application.setWindowIcon(application_icon())
    window = build_simulated_dashboard(
        root / "data" / "simulated_measurements.csv", settings=settings
    )
    window.show()
    return application.exec()
