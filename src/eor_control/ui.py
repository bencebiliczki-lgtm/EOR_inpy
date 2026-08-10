import ctypes
import os
import shutil
import sqlite3
import sys
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread
from typing import cast
from uuid import uuid4

import pyqtgraph as pg  # type: ignore[import-untyped]
from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QSettings,
    QStandardPaths,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QColor,
    QIcon,
    QResizeEvent,
    QShowEvent,
)
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
    QFileSystemModel,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QSystemTrayIcon,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolTip,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from eor_control import __version__
from eor_control.application import (
    ApplicationState,
    DeviceControlService,
    MeasurementState,
    RunMode,
)
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
    MeasurementEvent,
    MeasurementTable,
    NasConnectionTestResult,
    NasSyncQueue,
    ProjectMeasurementWriter,
    export_measurement_csv,
    export_measurement_excel,
    measurement_stage_segments,
    measurement_stages,
    numeric_series,
    project_excel_path,
    read_measurement_events,
    read_measurement_tables,
    safe_filename,
    test_nas_connection,
)
from eor_control.device_testing import (
    DeviceTestReport,
    DeviceTestResult,
    DeviceTestStatus,
    FunctionalDeviceTestSession,
    FunctionalTestDevice,
    FunctionalTestPreconditions,
    acquire_sensor_statistics,
    configuration_hash,
)
from eor_control.devices import DisabledPump
from eor_control.diagnostics import (
    DiagnosticCategory,
    DiagnosticEvent,
    DiagnosticLogger,
    LogRetentionSettings,
)
from eor_control.domain import DataQuality, MeasurementRecord, PumpStatus
from eor_control.hardware import (
    ConnectionTestRegistry,
    ConnectionTestResult,
    DeviceConnectionResult,
    HardwareConfiguration,
    HardwareConnectionTester,
    HardwareDiscovery,
    HardwareTestDevice,
    NiPhysicalChannelInfo,
    PhysicalHardwareConnectionTester,
    SerialPortInfo,
    discover_hardware,
)
from eor_control.isco import IscoSerialConfig, open_isco_pump
from eor_control.measurement import MeasurementChannels, MeasurementService
from eor_control.ni import AnalogValveActuator, NidaqmxBackend, NidaqmxDataAcquisition
from eor_control.preflight import (
    PreflightItem,
    PreflightReport,
    PreflightStatus,
)
from eor_control.projects import (
    MeasurementProject,
    MeasurementStage,
    PidProfile,
    ProjectRepository,
)
from eor_control.pump_control import (
    PumpControlService,
    PumpControlTiming,
    PumpOperatingMode,
    PumpPreparationProgress,
    PumpRole,
    PumpStartupPlan,
)
from eor_control.pump_telemetry import (
    PollingPump,
    PumpPollingIntervals,
    PumpTelemetrySnapshot,
)
from eor_control.runtime import BackgroundControlRunner, RuntimeSettings
from eor_control.safety import ManualSafetyMonitor, SafetyLimits, SafetyMonitor
from eor_control.simulators import (
    SimulatedDataAcquisition,
    SimulatedPump,
    SimulatedPumpFault,
    SimulatedValveActuator,
    SimulationDelay,
)
from eor_control.stable_profile import (
    load_stable_profile,
    software_settings,
)
from eor_control.timezone import format_hungarian_time


def _authorize_physical_hardware(
    devices: DeviceControlService,
    daq: NidaqmxDataAcquisition,
    *,
    valve_output_enabled: bool,
    hardware_confirmation: str,
) -> None:
    """Preserve the two-step operator then NI-output authorization order."""
    devices.authorize_hardware(hardware_confirmation)
    if valve_output_enabled:
        daq.authorize_output(NidaqmxDataAcquisition.HARDWARE_CONFIRMATION)


def _observe_hardware_pump_connections(
    pump_control: PumpControlService,
    *,
    jacket_enabled: bool,
    injection_enabled: bool,
) -> None:
    """Record connected pumps without issuing a control command."""

    enabled_roles = tuple(
        role
        for role, enabled in (
            (PumpRole.JACKET, jacket_enabled),
            (PumpRole.INJECTION, injection_enabled),
        )
        if enabled
    )
    pump_control.observe_connected(*enabled_roles)


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
QTableWidget {
    background: #ffffff; alternate-background-color: #f5f7fa;
    border: 1px solid #d7dee7; border-radius: 6px; gridline-color: #d7dee7;
    selection-background-color: #dce7f3; selection-color: #1f2933;
}
QHeaderView::section {
    background: #e8eef6; color: #1f2933; border: none;
    border-right: 1px solid #c4cfdd; border-bottom: 1px solid #c4cfdd;
    padding: 7px 8px; font-weight: 600;
}
QTableCornerButton::section { background: #e8eef6; border: 1px solid #c4cfdd; }
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
QTableWidget {
    background: #1b2129; alternate-background-color: #202832;
    border: 1px solid #35404d; border-radius: 6px; gridline-color: #35404d;
    selection-background-color: #355a78; selection-color: #ffffff;
}
QHeaderView::section {
    background: #28323d; color: #e6edf3; border: none;
    border-right: 1px solid #465362; border-bottom: 1px solid #465362;
    padding: 7px 8px; font-weight: 600;
}
QTableCornerButton::section { background: #28323d; border: 1px solid #465362; }
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


def user_data_root_path(documents_root: Path | None = None) -> Path:
    """Return the per-user EOR directory below the Windows Documents folder."""
    if documents_root is None:
        location = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DocumentsLocation
        )
        documents_root = Path(location) if location else Path.home() / "Documents"
    return documents_root / "EOR"


def portable_user_settings(
    root: Path | None = None,
    *,
    migrate_legacy: bool = True,
    user_data_root: Path | None = None,
) -> QSettings:
    """Open the Documents/EOR INI and migrate older settings once."""

    application_root = root or application_root_path()
    settings_path = (user_data_root or user_data_root_path()) / "EORControl.ini"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
    if migrate_legacy and not settings.allKeys():
        legacy_file = QSettings(
            str(application_root / "config" / "AFKI" / "EORControl.ini"),
            QSettings.Format.IniFormat,
        )
        if legacy_file.allKeys():
            for key in legacy_file.allKeys():
                settings.setValue(key, legacy_file.value(key))
        else:
            legacy_registry = QSettings(
                QSettings.Format.NativeFormat,
                QSettings.Scope.UserScope,
                "AFKI",
                "EORControl",
            )
            for key in legacy_registry.allKeys():
                settings.setValue(key, legacy_registry.value(key))
        settings.sync()
    profile_path = application_root / "config" / "stable-defaults.json"
    if profile_path.is_file():
        profile = load_stable_profile(profile_path)
        for key, value in software_settings(profile).items():
            if not settings.contains(key):
                settings.setValue(key, value)
        settings.sync()
    return settings


def migrate_legacy_project_database(
    legacy_path: Path, destination_path: Path
) -> bool:
    """Copy a legacy project database safely, including committed WAL content."""
    if destination_path.exists() or not legacy_path.is_file():
        return False
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(legacy_path) as source, sqlite3.connect(
            destination_path
        ) as destination:
            source.backup(destination)
    except Exception:
        with suppress(OSError):
            destination_path.unlink()
        raise
    return True


def migrate_legacy_project_files(
    legacy_directory: Path, destination_directory: Path
) -> bool:
    """Copy the legacy raw project tree once without merging or overwriting."""
    if destination_directory.exists() or not legacy_directory.is_dir():
        return False
    destination_directory.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(legacy_directory, destination_directory)
    return True


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
PROJECT_DEVICE_FIELDS = (
    ("jacket_pump_enabled", "Köpenypumpa"),
    ("injection_pump_enabled", "Besajtolópumpa"),
    ("line_pressure_enabled", "Vonali nyomásmérő"),
    ("differential_pressure_enabled", "Differenciálnyomás-mérő"),
    ("valve_output_enabled", "Szelep analóg kimenet"),
)


def project_device_profile(configuration: Mapping[str, object]) -> dict[str, bool]:
    stored = configuration.get("devices")
    values = stored if isinstance(stored, Mapping) else {}
    return {
        key: value if isinstance((value := values.get(key)), bool) else True
        for key, _label in PROJECT_DEVICE_FIELDS
    }


def hardware_device_profile(configuration: HardwareConfiguration) -> dict[str, bool]:
    return {
        key: bool(getattr(configuration, key))
        for key, _label in PROJECT_DEVICE_FIELDS
    }


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


def input_field_label(text: str, field: QWidget) -> QLabel:
    """Create a visible label explicitly associated with its input widget."""
    label = QLabel(text)
    label.setBuddy(field)
    field.setAccessibleName(text)
    return label


def format_dashboard_pressure(value: float) -> str:
    """Format a dashboard pressure with up to three Hungarian decimal places."""
    number = f"{value:.3f}"
    if number == "-0.000":
        number = "0.000"
    number = number.rstrip("0").rstrip(".").replace(".", ",")
    return f"{number} bar"


class ResizableDialog(QDialog):
    """Common base for application dialogs that may be freely resized."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizeGripEnabled(True)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)


class EditableDashboardBox(QGroupBox):
    """Dashboard card with an editor-only close affordance."""

    hide_requested = Signal(str)

    def __init__(self, key: str, title: str, parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        self.layout_key = key
        self.setObjectName(f"dashboard_box_{key}")
        self._editor_close = QPushButton("×", self)
        self._editor_close.setObjectName(f"layout_hide_{key}")
        self._editor_close.setAccessibleName(f"{title} kártya elrejtése")
        self._editor_close.setToolTip(f"{title} elrejtése")
        self._editor_close.setFixedSize(24, 24)
        self._editor_close.setStyleSheet(
            "QPushButton { background:#b00020;color:white;font-weight:900;"
            "border-radius:12px;padding:0; }"
        )
        self._editor_close.clicked.connect(
            lambda _checked=False: self.hide_requested.emit(self.layout_key)
        )
        self._editor_close.hide()

    @property
    def editor_close_button(self) -> QPushButton:
        return self._editor_close

    def set_editor_active(self, active: bool) -> None:
        self._editor_close.setVisible(active)
        if active:
            self._editor_close.raise_()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._editor_close.move(max(0, self.width() - 29), 2)


class SettingsHubDialog(ResizableDialog):
    """Visual-Studio-like container for embedded application settings pages."""

    def __init__(
        self,
        pages: tuple[tuple[str, str, str, Callable[[], QWidget]], ...],
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("settings_hub_dialog")
        self.setWindowTitle("Beállítások")
        self.setMinimumSize(720, 480)
        self.resize(940, 640)
        layout = QVBoxLayout(self)
        title = QLabel("Beállítások")
        title.setStyleSheet("font-size:22px;font-weight:700;padding:4px")
        layout.addWidget(title)

        content = QSplitter()
        content.setObjectName("settings_hub_splitter")
        self.navigation = QListWidget()
        self.navigation.setObjectName("settings_navigation")
        self.navigation.setMinimumWidth(210)
        self.navigation.setMaximumWidth(320)
        self.pages = QStackedWidget()
        self.pages.setObjectName("settings_pages")
        self._page_definitions = pages
        self._loaded_pages: dict[int, QWidget] = {}
        for key, label, _description, _factory in pages:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.navigation.addItem(item)
            placeholder = QWidget()
            placeholder.setObjectName(f"settings_placeholder_{key}")
            self.pages.addWidget(placeholder)
        self.navigation.currentRowChanged.connect(self._page_changed)
        content.addWidget(self.navigation)
        content.addWidget(self.pages)
        content.setStretchFactor(0, 0)
        content.setStretchFactor(1, 1)
        content.setSizes([240, 680])
        layout.addWidget(content, 1)
        close = QPushButton("Bezárás")
        close.clicked.connect(self.accept)
        layout.addWidget(close)

    def select_page(self, key: str) -> None:
        for row in range(self.navigation.count()):
            if (
                self.navigation.item(row).data(Qt.ItemDataRole.UserRole)
                == key
            ):
                self.navigation.setCurrentRow(row)
                return
        if self.navigation.count():
            self.navigation.setCurrentRow(0)

    def _page_changed(self, row: int) -> None:
        if row < 0:
            return
        loaded = self._loaded_pages.get(row)
        if loaded is not None:
            self.pages.setCurrentWidget(loaded)
            return
        key, label, description, factory = self._page_definitions[row]
        editor = factory()
        editor.setObjectName(editor.objectName() or f"settings_editor_{key}")
        page = QWidget()
        page.setObjectName(f"settings_page_{key}")
        page_layout = QVBoxLayout(page)
        page_title = QLabel(label)
        page_title.setStyleSheet("font-size:18px;font-weight:700")
        page_layout.addWidget(page_title)
        help_text = QLabel(description)
        help_text.setWordWrap(True)
        help_text.setStyleSheet("color:#66788a;padding:4px 0 12px 0")
        page_layout.addWidget(help_text)
        page_layout.addWidget(editor, 1)
        placeholder = self.pages.widget(row)
        if placeholder is not None:
            self.pages.removeWidget(placeholder)
            placeholder.deleteLater()
        self.pages.insertWidget(row, page)
        self._loaded_pages[row] = page
        self.pages.setCurrentWidget(page)


class StageSettingsDialog(ResizableDialog):
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


class ProjectSelectionDialog(ResizableDialog):
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
        self.stage_selector = QComboBox()
        self.stage_selector.setObjectName("project_selection_stage")
        self.stage_selector.currentIndexChanged.connect(self._update_open_button)
        stage_row.addWidget(
            input_field_label("Megnyitandó mérési fázis", self.stage_selector)
        )
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
                QTableWidgetItem(
                    format_hungarian_time(project.created_at, "%Y-%m-%d %H:%M")
                ),
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


class ProjectSettingsDialog(ResizableDialog):
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
        self.resize(620, 520)
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
        form.addWidget(input_field_label("Projekt", self.project_selector), 0, 0)
        form.addWidget(self.project_selector, 0, 1, 1, 2)
        form.addWidget(new_project, 1, 1)
        form.addWidget(delete_project, 1, 2)
        form.addWidget(
            input_field_label("Aktív mérési szakasz", self.stage_selector), 2, 0
        )
        form.addWidget(self.stage_selector, 2, 1, 1, 2)
        form.addWidget(add_stage, 3, 1)
        form.addWidget(rename_stage, 3, 2)
        form.addWidget(move_up, 4, 0)
        form.addWidget(move_down, 4, 1)
        form.addWidget(delete_stage, 4, 2)
        layout.addLayout(form)
        devices = QGroupBox("A projekthez hozzáadott eszközök")
        device_layout = QGridLayout(devices)
        self.device_checks: dict[str, QCheckBox] = {}
        for index, (key, label) in enumerate(PROJECT_DEVICE_FIELDS):
            checkbox = QCheckBox(label)
            checkbox.setObjectName(f"project_device_{key}")
            self.device_checks[key] = checkbox
            device_layout.addWidget(checkbox, index // 2, index % 2)
        device_help = QLabel(
            "A kijelölés a projekt eszközprofilja. Mentéséhez nem szükséges "
            "helyszíni validáció vagy kapcsolatpróba."
        )
        device_help.setWordWrap(True)
        device_layout.addWidget(device_help, 3, 0, 1, 2)
        save_devices = QPushButton("Projekt eszközprofiljának mentése")
        save_devices.setObjectName("save_project_device_profile")
        save_devices.clicked.connect(self._save_device_profile)
        device_layout.addWidget(save_devices, 4, 0, 1, 2)
        layout.addWidget(devices)
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
            for checkbox in self.device_checks.values():
                checkbox.setEnabled(False)
            return
        self._load_device_profile(project_id)
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
            configuration = dict(self._configuration)
            configuration["devices"] = self._selected_device_profile()
            project = self._repository.create_project(
                name=name,
                notes=notes,
                configuration=configuration,
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
        self._save_device_profile()
        self.accept()

    def _selected_device_profile(self) -> dict[str, bool]:
        return {
            key: checkbox.isChecked()
            for key, checkbox in self.device_checks.items()
        }

    def _load_device_profile(self, project_id: int) -> None:
        profile = project_device_profile(
            self._repository.get_project(project_id).configuration
        )
        for key, checkbox in self.device_checks.items():
            checkbox.setEnabled(True)
            checkbox.setChecked(profile[key])

    def _save_device_profile(self) -> None:
        project_id = self.selected_project_id
        if project_id is None:
            return
        project = self._repository.get_project(project_id)
        configuration = dict(project.configuration)
        profile = self._selected_device_profile()
        if project_device_profile(configuration) == profile and isinstance(
            configuration.get("devices"), Mapping
        ):
            return
        configuration["devices"] = profile
        self._repository.update_project_configuration(project_id, configuration)
        self._projects_changed = True


class RuntimeBridge(QObject):
    cycle_completed = Signal(object)
    fault_raised = Signal(str)
    preflight_completed = Signal(object)
    preflight_failed = Signal(str)
    pump_startup_progress = Signal(object)
    pump_preparation_progress = Signal(object)
    pump_startup_completed = Signal()
    pump_startup_failed = Signal(str)
    flow_change_completed = Signal(float)
    flow_change_failed = Signal(str)
    stage_export_batch_finished = Signal()
    hardware_status_completed = Signal(object)
    hardware_status_failed = Signal(object)


@dataclass(frozen=True, slots=True)
class HardwareDashboardStatus:
    generation: int
    record: MeasurementRecord
    jacket_connection: str
    jacket_connection_ok: bool | None
    injection_connection: str
    injection_connection_ok: bool | None


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


class DeviceSettingsDialog(ResizableDialog):
    def __init__(
        self,
        tester: HardwareConnectionTester,
        *,
        settings: QSettings,
        current_mode: RunMode,
        device_profile: Mapping[str, bool] | None = None,
        discoverer: Callable[[], HardwareDiscovery] = discover_hardware,
        diagnostics: DiagnosticLogger | None = None,
        developer_mode: bool = False,
        line_voltage_range: tuple[float, float] = (1.0, 5.0),
        differential_voltage_range: tuple[float, float] = (1.0, 5.0),
        functional_test_opener: Callable[
            [HardwareConfiguration, ConnectionTestResult], None
        ]
        | None = None,
        direct_control_opener: Callable[[HardwareConfiguration], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._tester = tester
        self._settings = settings
        self._discoverer = discoverer
        self._diagnostics = diagnostics
        self._developer_mode = developer_mode
        self._current_mode = current_mode
        self._functional_test_opener = functional_test_opener
        self._direct_control_opener = direct_control_opener
        self._voltage_ranges = {
            HardwareTestDevice.LINE_PRESSURE: line_voltage_range,
            HardwareTestDevice.DIFFERENTIAL_PRESSURE: differential_voltage_range,
        }
        self._device_profile = device_profile
        self._test_succeeded = False
        self._connection_registry = ConnectionTestRegistry()
        self._active_test_configuration: HardwareConfiguration | None = None
        self._configuration: HardwareConfiguration | None = None
        self.setWindowTitle("Eszközbeállítások")
        self.setMinimumSize(420, 360)
        screen = QApplication.primaryScreen()
        available_size = screen.availableGeometry().size() if screen is not None else None
        width = min(720, max(420, available_size.width() - 40)) if available_size else 720
        height = min(820, max(360, available_size.height() - 40)) if available_size else 820
        self.resize(width, height)

        outer_layout = QVBoxLayout(self)
        self._content_scroll = QScrollArea()
        self._content_scroll.setObjectName("device_settings_scroll")
        self._content_scroll.setWidgetResizable(True)
        self._content_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._content_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._content_widget = QWidget()
        self._content_widget.setMinimumWidth(0)
        self._content_widget.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        layout = QVBoxLayout(self._content_widget)
        self._content_scroll.setWidget(self._content_widget)
        outer_layout.addWidget(self._content_scroll, 1)
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
        self.jacket_enabled = QCheckBox("Köpenypumpa hozzáadva")
        self.jacket_enabled.setChecked(
            self._profile_or_stored("jacket_pump_enabled")
        )
        self.injection_enabled = QCheckBox("Besajtolópumpa hozzáadva")
        self.injection_enabled.setChecked(
            self._profile_or_stored("injection_pump_enabled")
        )
        self.line_enabled = QCheckBox("Vonali nyomásmérő hozzáadva")
        self.line_enabled.setChecked(self._profile_or_stored("line_pressure_enabled"))
        self.delta_enabled = QCheckBox("Differenciálnyomás-mérő hozzáadva")
        self.delta_enabled.setChecked(
            self._profile_or_stored("differential_pressure_enabled")
        )
        self.valve_enabled = QCheckBox("Szelep analóg kimenet hozzáadva")
        self.valve_enabled.setChecked(self._profile_or_stored("valve_output_enabled"))
        self.jacket_port = EditableSelectionComboBox(self._stored("jacket_port", ""))
        self.jacket_id = self._integer_field("jacket_unit_id", 1, 0, 9)
        self.jacket_channel = self._channel_field("jacket_channel", "A")
        self.injection_port = EditableSelectionComboBox(
            self._stored("injection_port", "")
        )
        self.injection_id = self._integer_field("injection_unit_id", 2, 0, 9)
        self.injection_channel = self._channel_field("injection_channel", "A")
        self.baud_rate = QComboBox()
        for baud in (300, 1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200):
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
        pump_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        pump_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        for label, pump_widget in (
            ("Aktív eszköz", self.jacket_enabled),
            ("Köpenypumpa soros portja (COM)", self.jacket_port),
            ("Köpenypumpa DASNET eszközazonosítója (0–9)", self.jacket_id),
            ("Köpenypumpa pumpacsatornája (A–D)", self.jacket_channel),
            ("Aktív eszköz", self.injection_enabled),
            ("Besajtolópumpa soros portja (COM)", self.injection_port),
            ("Besajtolópumpa DASNET eszközazonosítója (0–9)", self.injection_id),
            ("Besajtolópumpa pumpacsatornája (A–D)", self.injection_channel),
            ("Soros kommunikáció sebessége (baud)", self.baud_rate),
            ("Pumpák kábelezési megjegyzése", self.pump_cabling_notes),
        ):
            self._add_responsive_form_row(pump_form, label, pump_widget)

        ni_box = QGroupBox("Nyomásmérés és szelepvezérlés")
        ni_form = QFormLayout(ni_box)
        ni_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        ni_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        for label, ni_widget in (
            ("NI adatgyűjtő", self.ni_device),
            ("Aktív eszköz", self.line_enabled),
            ("Vonali nyomás bemenete", self.line_channel),
            ("Aktív eszköz", self.delta_enabled),
            ("Differenciálnyomás bemenete", self.delta_channel),
            ("Aktív eszköz", self.valve_enabled),
            ("Szelepvezérlés kimenete", self.valve_channel),
            ("Bemenetek bekötési módja", self.terminal_configuration),
            ("Bekötési megjegyzés", self.ni_wiring_notes),
            ("Biztonságos szelepjel", self.safe_voltage),
            ("Szelep 0%-os jele", self.zero_voltage),
            ("Szelep 100%-os jele", self.hundred_voltage),
        ):
            self._add_responsive_form_row(ni_form, label, ni_widget)

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

        discovery_row = QVBoxLayout()
        self._discovery_status = QLabel()
        self._discovery_status.setWordWrap(True)
        self._discovery_status.setMinimumWidth(0)
        self._discovery_status.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self._discovery_status.setVisible(developer_mode)
        refresh_button = QPushButton("Csatlakoztatott eszközök keresése")
        refresh_button.clicked.connect(self._refresh_hardware_choices)
        discovery_row.addWidget(refresh_button)
        discovery_row.addWidget(self._discovery_status)
        layout.addLayout(discovery_row)
        connection_box = QGroupBox("Eszközönkénti kapcsolatpróba")
        connection_grid = QGridLayout(connection_box)
        self._connection_result_labels: dict[HardwareTestDevice, QLabel] = {}
        self._device_test_buttons: dict[HardwareTestDevice, QPushButton] = {}
        for row, (device, label) in enumerate(
            (
                (HardwareTestDevice.JACKET_PUMP, "Köpenypumpa"),
                (HardwareTestDevice.INJECTION_PUMP, "Besajtolópumpa"),
                (HardwareTestDevice.LINE_PRESSURE, "Vonali nyomás NI bemenet"),
                (
                    HardwareTestDevice.DIFFERENTIAL_PRESSURE,
                    "Differenciálnyomás NI bemenet",
                ),
            )
        ):
            status = QLabel("NINCS TESZTELVE")
            status.setWordWrap(True)
            button = QPushButton("Kapcsolat tesztelése")
            button.clicked.connect(
                lambda _checked=False, selected=device: self._start_device_test(
                    selected
                )
            )
            connection_grid.addWidget(QLabel(label), row, 0)
            connection_grid.addWidget(status, row, 1)
            connection_grid.addWidget(button, row, 2)
            self._connection_result_labels[device] = status
            self._device_test_buttons[device] = button
        self._valve_test_status = QLabel(
            "ÖNÁLLÓAN KEZELHETŐ — a közvetlen eszközkezelőben, "
            "külön kimeneti megerősítéssel"
        )
        self._valve_test_status.setWordWrap(True)
        connection_grid.addWidget(QLabel("Szelep NI analóg kimenet"), 4, 0)
        connection_grid.addWidget(self._valve_test_status, 4, 1, 1, 2)
        connection_grid.setColumnStretch(1, 1)
        layout.addWidget(connection_box)
        # Legacy report fields remain false for snapshot compatibility, but the
        # former on-site validation form is no longer part of device setup.
        self.supervised_test_minutes = self._integer_field(
            "supervised_test_minutes", 60, 1, 1440
        )
        self.cable_disconnect_test = QCheckBox("Sikeresen elvégezve")
        self.emergency_stop_test = QCheckBox("Sikeresen elvégezve")
        self.supervised_test = QCheckBox("Sikeresen elvégezve")
        calibration_help = QLabel(
            "A differenciálnyomás-érzékelő tényleges feszültség–bar tartományát a "
            "Beállítások → Kalibráció és biztonság ablakban a felhasználó adja meg."
        )
        calibration_help.setWordWrap(True)
        layout.addWidget(calibration_help)
        self._save_button = QPushButton("Beállítások mentése")
        self._save_button.setToolTip(
            "Eszköz- és csatornabeállítások mentése"
        )
        self._save_button.clicked.connect(self._save_only)
        self._test_button = QPushButton("Kapcsolatok tesztelése")
        self._test_button.setToolTip(
            "Csak olvasási kapcsolatpróba minden hozzáadott eszközön"
        )
        self._test_button.clicked.connect(self._start_test)
        self._result_label = QLabel("A hardvermód aktiválásához sikeres kapcsolatpróba szükséges.")
        self._result_label.setWordWrap(True)
        self._activate_button = QPushButton("HARDVER aktiválása")
        self._activate_button.setEnabled(False)
        self._activate_button.clicked.connect(self._activate)
        self._direct_control_button = QPushButton(
            "Közvetlen kezelés…"
        )
        self._direct_control_button.setVisible(
            developer_mode and direct_control_opener is not None
        )
        self._direct_control_button.setEnabled(False)
        self._direct_control_button.setToolTip(
            "A kiválasztott eszközök külön kezelőfelülete. Nem aktivál "
            "hardvermódot és nem enged mérésindítást."
        )
        self._direct_control_button.clicked.connect(self._open_direct_control)
        self._functional_test_button = QPushButton(
            "Vezetett funkcionális eszközteszt…"
        )
        self._functional_test_button.setVisible(False)
        self._functional_test_button.setEnabled(False)
        self._functional_test_button.clicked.connect(self._open_functional_test)
        self._cancel_button = QPushButton("Mégse")
        self._cancel_button.clicked.connect(self.reject)
        outer_layout.addWidget(self._result_label)
        self._action_row = QHBoxLayout()
        self._action_row.setSpacing(8)
        for action_button in (
            self._save_button,
            self._test_button,
            self._activate_button,
            self._direct_control_button,
            self._cancel_button,
        ):
            action_button.setMinimumWidth(0)
            action_button.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            self._action_row.addWidget(action_button, 1)
        outer_layout.addLayout(self._action_row)
        self._bridge = DeviceTestBridge(self)
        self._bridge.succeeded.connect(self._test_passed)
        self._bridge.failed.connect(self._test_failed)
        self._refresh_hardware_choices()
        for selector in (
            self.jacket_enabled,
            self.injection_enabled,
            self.line_enabled,
            self.delta_enabled,
            self.valve_enabled,
        ):
            selector.toggled.connect(self._device_selection_changed)
        self._apply_device_selection()
        for connection_field in (
            self.jacket_port,
            self.jacket_id,
            self.jacket_channel,
            self.injection_port,
            self.injection_id,
            self.injection_channel,
            self.baud_rate,
            self.ni_device,
            self.line_channel,
            self.delta_channel,
            self.terminal_configuration,
            self.jacket_enabled,
            self.injection_enabled,
            self.line_enabled,
            self.delta_enabled,
            self.valve_enabled,
        ):
            if isinstance(connection_field, QCheckBox):
                connection_field.toggled.connect(
                    self._connection_configuration_changed
                )
            elif isinstance(connection_field, (QSpinBox, QDoubleSpinBox)):
                connection_field.valueChanged.connect(
                    self._connection_configuration_changed
                )
            else:
                connection_field.currentIndexChanged.connect(
                    self._connection_configuration_changed
                )
        self._connection_configuration_changed()

    @property
    def configuration(self) -> HardwareConfiguration | None:
        return self._configuration

    @property
    def connection_result(self) -> ConnectionTestResult | None:
        if self._configuration is None:
            return None
        return self._connection_registry.aggregate(self._configuration)

    @staticmethod
    def _add_responsive_form_row(
        form: QFormLayout, label: str, field: QWidget
    ) -> None:
        label_widget = input_field_label(label, field)
        label_widget.setWordWrap(True)
        label_widget.setMinimumWidth(0)
        label_widget.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )
        field.setMinimumWidth(0)
        field.setSizePolicy(
            QSizePolicy.Policy.Ignored, field.sizePolicy().verticalPolicy()
        )
        form.addRow(label_widget, field)

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

    def _profile_or_stored(self, key: str) -> bool:
        if self._device_profile is not None:
            value = self._device_profile.get(key)
            if isinstance(value, bool):
                return value
        return self._stored_bool(key, True)

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
            if hasattr(self, "line_enabled"):
                self._apply_device_selection()
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
        if hasattr(self, "line_enabled"):
            self._apply_device_selection()

    def _device_selection_changed(self, *_args: object) -> None:
        self._apply_device_selection()

    def _apply_device_selection(self) -> None:
        pump_fields = (
            (
                self.jacket_enabled.isChecked(),
                (self.jacket_port, self.jacket_id, self.jacket_channel),
            ),
            (
                self.injection_enabled.isChecked(),
                (self.injection_port, self.injection_id, self.injection_channel),
            ),
        )
        for enabled, fields in pump_fields:
            for field in fields:
                if field in (self.jacket_port, self.injection_port):
                    has_port = any(
                        isinstance(field.itemData(index), str)
                        for index in range(field.count())
                    )
                    field.setEnabled(enabled and has_port)
                else:
                    field.setEnabled(enabled)
        ni_selected = isinstance(self.ni_device.currentData(), str)
        has_ni_device = any(
            isinstance(self.ni_device.itemData(index), str)
            for index in range(self.ni_device.count())
        )
        self.ni_device.setEnabled(
            has_ni_device
            and (
                self.line_enabled.isChecked()
                or self.delta_enabled.isChecked()
                or self.valve_enabled.isChecked()
            )
        )
        self.line_channel.setEnabled(self.line_enabled.isChecked() and ni_selected)
        self.delta_channel.setEnabled(self.delta_enabled.isChecked() and ni_selected)
        self.valve_channel.setEnabled(self.valve_enabled.isChecked() and ni_selected)
        self.safe_voltage.setEnabled(self.valve_enabled.isChecked())
        self.zero_voltage.setEnabled(self.valve_enabled.isChecked())
        self.hundred_voltage.setEnabled(self.valve_enabled.isChecked())
        selectors = {
            HardwareTestDevice.JACKET_PUMP: self.jacket_enabled,
            HardwareTestDevice.INJECTION_PUMP: self.injection_enabled,
            HardwareTestDevice.LINE_PRESSURE: self.line_enabled,
            HardwareTestDevice.DIFFERENTIAL_PRESSURE: self.delta_enabled,
        }
        for device, selector in selectors.items():
            enabled = selector.isChecked()
            self._device_test_buttons[device].setEnabled(enabled)
            if not enabled:
                label = self._connection_result_labels[device]
                label.setText("NINCS HOZZÁADVA — nem része az aktív profilnak")
                label.setStyleSheet("color:#66788a;font-weight:700")
        if self.valve_enabled.isChecked():
            self._valve_test_status.setText(
                "ÖNÁLLÓAN KEZELHETŐ — a közvetlen eszközkezelőben, "
                "külön kimeneti megerősítéssel"
            )
            self._valve_test_status.setStyleSheet(
                "color:#1b7f3a;font-weight:700"
            )
        else:
            self._valve_test_status.setText(
                "NINCS HOZZÁADVA — nem része az aktív profilnak"
            )
            self._valve_test_status.setStyleSheet(
                "color:#66788a;font-weight:700"
            )

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
        self._apply_device_selection()

    def _log_discovery(self, message: str, *, level: str) -> None:
        if self._diagnostics is not None:
            self._diagnostics.emit(
                DiagnosticCategory.SYSTEM,
                "DISCOVERY",
                message,
                level=level,
            )

    def _read_configuration(self) -> HardwareConfiguration:
        if self.jacket_enabled.isChecked() and not isinstance(
            self.jacket_port.currentData(), str
        ):
            raise ValueError("előbb válaszd ki a köpenypumpa elérhető csatlakozóját")
        if self.injection_enabled.isChecked() and not isinstance(
            self.injection_port.currentData(), str
        ):
            raise ValueError("előbb válaszd ki a besajtolópumpa elérhető csatlakozóját")
        ni_enabled = any(
            selector.isChecked()
            for selector in (self.line_enabled, self.delta_enabled, self.valve_enabled)
        )
        if ni_enabled and not isinstance(self.ni_device.currentData(), str):
            raise ValueError("előbb válassz egy felismert NI eszközt")
        if self.line_enabled.isChecked() and not isinstance(
            self.line_channel.currentData(), str
        ):
            raise ValueError("nincs elérhető vonali nyomáscsatorna")
        if self.delta_enabled.isChecked() and not isinstance(
            self.delta_channel.currentData(), str
        ):
            raise ValueError("nincs elérhető differenciálnyomás-csatorna")
        if self.valve_enabled.isChecked() and not isinstance(
            self.valve_channel.currentData(), str
        ):
            raise ValueError("nincs elérhető szelepvezérlő csatorna")
        return HardwareConfiguration(
            jacket_port=self.jacket_port.text() if self.jacket_enabled.isChecked() else "",
            jacket_unit_id=self.jacket_id.value(),
            jacket_channel=self.jacket_channel.currentText(),
            injection_port=(
                self.injection_port.text()
                if self.injection_enabled.isChecked()
                else ""
            ),
            injection_unit_id=self.injection_id.value(),
            injection_channel=self.injection_channel.currentText(),
            baud_rate=int(self.baud_rate.currentData()),
            serial_command_timeout_seconds=float(
                str(
                    self._settings.value(
                        "hardware/serial_command_timeout_seconds", 2.0
                    )
                )
            ),
            serial_command_retries=int(
                str(self._settings.value("hardware/serial_command_retries", 2))
            ),
            line_pressure_channel=(
                self.line_channel.text() if self.line_enabled.isChecked() else ""
            ),
            differential_pressure_channel=(
                self.delta_channel.text() if self.delta_enabled.isChecked() else ""
            ),
            valve_output_channel=(
                self.valve_channel.text() if self.valve_enabled.isChecked() else ""
            ),
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
            jacket_pump_enabled=self.jacket_enabled.isChecked(),
            injection_pump_enabled=self.injection_enabled.isChecked(),
            line_pressure_enabled=self.line_enabled.isChecked(),
            differential_pressure_enabled=self.delta_enabled.isChecked(),
            valve_output_enabled=self.valve_enabled.isChecked(),
        )

    def _start_test(self) -> None:
        try:
            configuration = self._read_configuration()
        except ValueError as error:
            self._test_failed(str(error))
            return
        self._configuration = configuration
        self._active_test_configuration = configuration
        self._store_configuration(configuration)
        self._test_succeeded = False
        self._activate_button.setEnabled(False)
        self._test_button.setEnabled(False)
        self._set_device_test_buttons_enabled(False)
        self._result_label.setText("Kapcsolatpróba folyamatban…")

        def execute() -> None:
            try:
                result = self._tester.test(configuration)
            except Exception as error:
                self._bridge.failed.emit(str(error))
            else:
                self._bridge.succeeded.emit(result)

        Thread(target=execute, name="eor-device-test", daemon=True).start()

    def _start_device_test(self, device: HardwareTestDevice) -> None:
        try:
            configuration = self._read_configuration()
            operation = self._device_test_operation(device)
        except ValueError as error:
            self._show_device_connection_result(
                DeviceConnectionResult(device, False, str(error))
            )
            return
        self._active_test_configuration = configuration
        self._test_succeeded = False
        self._activate_button.setEnabled(False)
        self._test_button.setEnabled(False)
        self._set_device_test_buttons_enabled(False)
        self._connection_result_labels[device].setText("TESZT FOLYAMATBAN…")

        def execute() -> None:
            try:
                result = operation()
            except Exception as error:
                self._bridge.failed.emit(str(error))
            else:
                self._bridge.succeeded.emit(result)

        Thread(target=execute, name=f"eor-{device.value}-test", daemon=True).start()

    def _device_test_operation(
        self, device: HardwareTestDevice
    ) -> Callable[[], DeviceConnectionResult]:
        enabled = {
            HardwareTestDevice.JACKET_PUMP: self.jacket_enabled.isChecked(),
            HardwareTestDevice.INJECTION_PUMP: self.injection_enabled.isChecked(),
            HardwareTestDevice.LINE_PRESSURE: self.line_enabled.isChecked(),
            HardwareTestDevice.DIFFERENTIAL_PRESSURE: self.delta_enabled.isChecked(),
        }
        if not enabled[device]:
            raise ValueError("az eszközt előbb add hozzá az aktív profilhoz")
        if device in (
            HardwareTestDevice.JACKET_PUMP,
            HardwareTestDevice.INJECTION_PUMP,
        ):
            port_field = (
                self.jacket_port
                if device is HardwareTestDevice.JACKET_PUMP
                else self.injection_port
            )
            port = port_field.currentData()
            if not isinstance(port, str) or not port:
                raise ValueError("ehhez a pumpához előbb válassz soros csatlakozót")
            unit_id = (
                self.jacket_id.value()
                if device is HardwareTestDevice.JACKET_PUMP
                else self.injection_id.value()
            )
            channel = (
                self.jacket_channel.currentText()
                if device is HardwareTestDevice.JACKET_PUMP
                else self.injection_channel.currentText()
            )
            configuration = IscoSerialConfig(
                port, unit_id, channel, int(self.baud_rate.currentData())
            )
            return lambda: self._tester.test_pump(configuration, device)

        channel_field = (
            self.line_channel
            if device is HardwareTestDevice.LINE_PRESSURE
            else self.delta_channel
        )
        channel = channel_field.currentData()
        if not isinstance(channel, str) or not channel:
            raise ValueError("ehhez a méréshez előbb válassz NI bemeneti csatornát")
        terminal = str(self.terminal_configuration.currentData())
        return lambda: self._tester.test_ni_input(channel, terminal, device)

    def _test_passed(self, result: object) -> None:
        self._test_button.setEnabled(True)
        self._set_device_test_buttons_enabled(True)
        if isinstance(result, DeviceConnectionResult):
            result = self._validate_input_voltage(result)
            configuration = self._active_test_configuration_or_current()
            if configuration is None:
                self._show_device_connection_result(result)
                state = "SIKERES" if result.successful else "SIKERTELEN"
                self._result_label.setText(
                    f"{state} EGYEDI KAPCSOLATPRÓBA — {result.detail}"
                )
                return
            self._connection_registry.record(configuration, result)
            self._show_connection_summary(configuration)
            return
        if not isinstance(result, ConnectionTestResult):
            self._test_failed("érvénytelen kapcsolatpróba-eredmény")
            return
        result = ConnectionTestResult(
            tuple(self._validate_input_voltage(item) for item in result.devices),
            result.required_devices,
        )
        configuration = self._active_test_configuration_or_current()
        if configuration is None:
            for device_result in result.devices:
                self._show_device_connection_result(device_result)
            self._test_succeeded = result.all_successful
            self._activate_button.setEnabled(result.all_successful)
            successful = sum(item.successful for item in result.devices)
            self._result_label.setText(
                f"Sikeres kapcsolatok: {successful}/{len(result.devices)}"
            )
            return
        self._connection_registry.record_all(configuration, result)
        self._show_connection_summary(configuration)

    def _active_test_configuration_or_current(
        self,
    ) -> HardwareConfiguration | None:
        if self._active_test_configuration is not None:
            return self._active_test_configuration
        try:
            return self._read_configuration()
        except ValueError:
            return None

    def _show_connection_summary(self, configuration: HardwareConfiguration) -> None:
        result = self._connection_registry.aggregate(configuration)
        for device_result in result.devices:
            self._show_device_connection_result(device_result)
        enabled_devices = configuration.enabled_test_devices()
        selected_successful = result.successful_for(enabled_devices)
        self._test_succeeded = selected_successful
        if selected_successful:
            self._configuration = configuration
        self._activate_button.setEnabled(
            selected_successful and configuration.measurement_ready
        )
        self._direct_control_button.setEnabled(
            self._developer_mode
            and self._direct_control_opener is not None
            and (
                bool(configuration.enabled_test_devices())
                or configuration.valve_output_enabled
            )
        )
        self._functional_test_button.setEnabled(
            selected_successful
            and configuration.measurement_ready
            and self._current_mode is RunMode.HARDWARE
            and self._functional_test_opener is not None
        )
        enabled_set = set(enabled_devices)
        successful = [
            item.detail
            for item in result.devices
            if item.device in enabled_set and item.successful
        ]
        failed = [
            item.detail
            for item in result.devices
            if item.device in enabled_set and not item.successful
        ]
        missing = len(enabled_devices) - len(result.devices)
        summary = [
            f"Sikeres kapcsolatok: {len(successful)}/{len(enabled_devices)} aktív eszköz",
            *(f"✓ {detail}" for detail in successful),
            *(f"✗ {detail}" for detail in failed),
        ]
        if missing:
            summary.append(f"○ Még nem tesztelt eszközök: {missing}")
        self._result_label.setText("\n".join(summary))
        self._result_label.setStyleSheet(
            "color:#1b7f3a;font-weight:700"
            if self._test_succeeded
            else "color:#9a6700;font-weight:700"
        )
        self._apply_device_selection()

    def _connection_configuration_changed(self, *_args: object) -> None:
        self._test_succeeded = False
        self._activate_button.setEnabled(False)
        self._functional_test_button.setEnabled(False)
        try:
            configuration = self._read_configuration()
        except ValueError:
            return
        result = self._connection_registry.aggregate(configuration)
        current_devices = {item.device for item in result.devices}
        for device in HardwareTestDevice:
            if device not in current_devices:
                label = self._connection_result_labels[device]
                label.setText("NINCS TESZTELVE — a kapcsolódó beállítás megváltozott")
                label.setStyleSheet("color:#66788a;font-weight:700")
        self._show_connection_summary(configuration)
        self._apply_device_selection()

    def _validate_input_voltage(
        self, result: DeviceConnectionResult
    ) -> DeviceConnectionResult:
        expected_range = self._voltage_ranges.get(result.device)
        if not result.successful or result.value is None or expected_range is None:
            return result
        voltage_min, voltage_max = expected_range
        if voltage_min <= result.value <= voltage_max:
            return result
        return DeviceConnectionResult(
            result.device,
            True,
            f"{result.detail}; a mért {result.value:.3f} V kívül esik a beállított "
            f"{voltage_min:g}–{voltage_max:g} V kalibrációs tartományon. "
            "Ez diagnosztikai figyelmeztetés, nem biztonsági leállítási határ.",
            result.value,
        )

    def _test_failed(self, message: str) -> None:
        self._test_succeeded = False
        self._test_button.setEnabled(True)
        self._set_device_test_buttons_enabled(True)
        self._activate_button.setEnabled(False)
        self._functional_test_button.setEnabled(False)
        self._result_label.setText(f"SIKERTELEN KAPCSOLATPRÓBA: {message}")
        self._result_label.setStyleSheet("color:#b00020;font-weight:700")

    def _show_device_connection_result(
        self, result: DeviceConnectionResult
    ) -> None:
        label = self._connection_result_labels[result.device]
        label.setText(
            ("SIKERES — " if result.successful else "SIKERTELEN — ")
            + result.detail
        )
        label.setStyleSheet(
            "color:#1b7f3a;font-weight:700"
            if result.successful
            else "color:#b00020;font-weight:700"
        )

    def _set_device_test_buttons_enabled(self, enabled: bool) -> None:
        selections = {
            HardwareTestDevice.JACKET_PUMP: self.jacket_enabled.isChecked(),
            HardwareTestDevice.INJECTION_PUMP: self.injection_enabled.isChecked(),
            HardwareTestDevice.LINE_PRESSURE: self.line_enabled.isChecked(),
            HardwareTestDevice.DIFFERENTIAL_PRESSURE: self.delta_enabled.isChecked(),
        }
        for device, button in self._device_test_buttons.items():
            button.setEnabled(enabled and selections[device])

    def _activate(self) -> None:
        if not self._test_succeeded or self._configuration is None:
            self._test_failed("előbb sikeres kapcsolatpróba szükséges")
            return
        self._store_configuration(self._configuration)
        self.accept()

    def _open_direct_control(self) -> None:
        try:
            configuration = self._read_configuration()
        except ValueError as error:
            self._test_failed(str(error))
            return
        if self._direct_control_opener is None:
            return
        self._store_configuration(configuration)
        try:
            self._direct_control_opener(configuration)
        except Exception as error:
            QMessageBox.critical(
                self,
                "Közvetlen eszközkezelés",
                "A közvetlen eszközkapcsolat nem nyitható meg:\n"
                f"{type(error).__name__}: {error}",
            )

    def _open_functional_test(self) -> None:
        configuration = self._configuration
        result = self.connection_result
        if (
            configuration is None
            or result is None
            or not result.all_successful
            or self._functional_test_opener is None
        ):
            self._test_failed("a funkcionális teszthez aktuális kapcsolatpróba szükséges")
            return
        self._functional_test_opener(configuration, result)

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


class DeviceTestWizard(ResizableDialog):
    STEPS = (
        (FunctionalTestDevice.SAFETY_PRECONDITIONS, "Biztonsági előfeltételek"),
        (FunctionalTestDevice.JACKET_PUMP, "Köpenypumpa"),
        (FunctionalTestDevice.INJECTION_PUMP, "Besajtolópumpa"),
        (FunctionalTestDevice.LINE_PRESSURE, "Vonali nyomásmérő"),
        (
            FunctionalTestDevice.DIFFERENTIAL_PRESSURE,
            "Differenciálnyomás-mérő",
        ),
        (FunctionalTestDevice.NI_ANALOG_OUTPUT, "NI analóg kimenet"),
        (FunctionalTestDevice.HANBAY_VALVE, "HANBAY MCJ-050AF szelep"),
        (
            FunctionalTestDevice.EMERGENCY_AND_COMMUNICATION,
            "Vészleállítás és kommunikációvesztés",
        ),
    )

    def __init__(
        self,
        session: FunctionalDeviceTestSession,
        *,
        report_path: Path,
        operations: Mapping[
            FunctionalTestDevice, Callable[[], DeviceTestResult]
        ] | None = None,
        ao_tolerance_voltage: float = 0.05,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._session = session
        self._report_path = report_path
        self._operations = dict(operations or {})
        self._ao_tolerance_voltage = ao_tolerance_voltage
        self._started = False
        self._finished = False
        self._operation_active = False
        self._current_step = 0
        self._valve_prerequisites_confirmed = False
        self.setWindowTitle("Vezetett funkcionális eszközteszt")
        self.resize(820, 680)
        layout = QVBoxLayout(self)
        warning = QLabel(
            "FIZIKAI FUNKCIONÁLIS TESZT — minden kimeneti lépés külön kezelői "
            "jóváhagyást igényel."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet(
            "padding:10px;color:#b00020;font-weight:800;border:2px solid #b00020"
        )
        layout.addWidget(warning)
        checklist_box = QGroupBox("Kezelői biztonsági ellenőrzőlista")
        checklist_layout = QVBoxLayout(checklist_box)
        self._checklist: list[QCheckBox] = []
        for text_value in (
            "A rendszer nyomásmentes",
            "Nincs veszélyes folyadékmozgás",
            "A lefúvatási útvonal biztonságos",
            "A vészleállító elérhető",
            "A tesztet kezelő személy folyamatosan felügyeli",
        ):
            checkbox = QCheckBox(text_value)
            checklist_layout.addWidget(checkbox)
            self._checklist.append(checkbox)
        layout.addWidget(checklist_box)
        self._table = QTableWidget(len(self.STEPS), 3)
        self._table.setHorizontalHeaderLabels(("Lépés", "Állapot", "Részletek"))
        for row, (_device, label) in enumerate(self.STEPS):
            self._table.setItem(row, 0, QTableWidgetItem(label))
            self._table.setItem(row, 1, QTableWidgetItem(DeviceTestStatus.NOT_TESTED))
            self._table.setItem(row, 2, QTableWidgetItem(""))
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.selectRow(0)
        layout.addWidget(self._table, 1)
        actual_position = QLabel("Tényleges szelepállás: nincs visszajelzés")
        actual_position.setStyleSheet("color:#9a6700;font-weight:700")
        layout.addWidget(actual_position)
        controls = QHBoxLayout()
        self._begin_button = QPushButton("Teszt indítása")
        self._begin_button.clicked.connect(self._begin)
        self._run_button = QPushButton("Aktuális lépés futtatása")
        self._run_button.setEnabled(False)
        self._run_button.clicked.connect(self._run_current_step)
        self._skip_button = QPushButton("Lépés kihagyása indoklással")
        self._skip_button.setEnabled(False)
        self._skip_button.clicked.connect(self._skip_current_step)
        export_button = QPushButton("Jelentés exportálása…")
        export_button.clicked.connect(self._export_report)
        controls.addWidget(self._begin_button)
        controls.addWidget(self._run_button)
        controls.addWidget(self._skip_button)
        controls.addWidget(export_button)
        layout.addLayout(controls)
        self._abort_button = QPushButton(
            "TESZT MEGSZAKÍTÁSA ÉS BIZTONSÁGOS LEÁLLÍTÁS"
        )
        self._abort_button.setStyleSheet(
            "padding:12px;background:#b00020;color:white;font-weight:900"
        )
        self._abort_button.clicked.connect(self._abort)
        layout.addWidget(self._abort_button)
        self._status = QLabel("A teszt még nem indult el.")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        self._bridge = DeviceTestBridge(self)
        self._bridge.succeeded.connect(self._operation_completed)
        self._bridge.failed.connect(self._operation_failed)

    def _begin(self) -> None:
        confirmations = FunctionalTestPreconditions(
            *(checkbox.isChecked() for checkbox in self._checklist)
        )
        try:
            self._session.begin(confirmations)
        except Exception as error:
            self._status.setText(f"A teszt nem indítható: {error}")
            self._status.setStyleSheet("color:#b00020;font-weight:700")
            return
        self._started = True
        self._begin_button.setEnabled(False)
        self._run_button.setEnabled(True)
        self._skip_button.setEnabled(True)
        self._set_row_result(0, DeviceTestStatus.PASSED, "Ellenőrzőlista elfogadva")
        self._current_step = 1
        self._table.selectRow(self._current_step)
        self._save_report()

    def _run_current_step(self) -> None:
        if self._operation_active or self._current_step >= len(self.STEPS):
            return
        device = self.STEPS[self._current_step][0]
        if device in (
            FunctionalTestDevice.JACKET_PUMP,
            FunctionalTestDevice.INJECTION_PUMP,
        ):
            role_name = (
                "köpenypumpa"
                if device is FunctionalTestDevice.JACKET_PUMP
                else "besajtolópumpa"
            )
            answer = QMessageBox.question(
                self,
                "Pumpaszerep megerősítése",
                f"Megerősíted, hogy a fizikailag azonosított eszköz a {role_name}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        operation = self._operations.get(device)
        if device is FunctionalTestDevice.NI_ANALOG_OUTPUT:
            operation = self._prepare_ao_operation()
        elif device is FunctionalTestDevice.HANBAY_VALVE:
            operation = self._prepare_valve_operation()
        elif device is FunctionalTestDevice.EMERGENCY_AND_COMMUNICATION:
            operation = self._prepare_emergency_operation()
        if operation is None:
            self._status.setText(
                "Ehhez a lépéshez nincs engedélyezett hardveradapter; csak indokolt "
                "kihagyás lehetséges."
            )
            return
        self._operation_active = True
        self._run_button.setEnabled(False)
        self._skip_button.setEnabled(False)
        self._set_row_result(self._current_step, DeviceTestStatus.RUNNING, "Folyamatban…")

        def execute() -> None:
            try:
                result = operation()
            except Exception as error:
                self._bridge.failed.emit(str(error))
            else:
                self._bridge.succeeded.emit(result)

        Thread(target=execute, name="eor-guided-device-test", daemon=True).start()

    def _prepare_ao_operation(self) -> Callable[[], DeviceTestResult] | None:
        expected = self._session.next_ao_voltage
        if expected is None:
            return None
        answer = QMessageBox.question(
            self,
            "NI AO lépés engedélyezése",
            f"A következő művelet {expected:g} V fizikai analóg kimenetet állít be.\n\n"
            "Engedélyezed ezt az egy AO-írást?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return None
        measured, accepted = QInputDialog.getDouble(
            self,
            "Multiméteres ellenőrzés",
            "Mért AO feszültség",
            expected,
            -10.0,
            10.0,
            4,
        )
        if not accepted:
            return None
        return lambda: self._session.run_ao_step(
            expected_voltage=expected,
            measured_voltage=measured,
            tolerance_voltage=self._ao_tolerance_voltage,
            confirmation=FunctionalDeviceTestSession.AO_CONFIRMATION,
        )

    def _prepare_valve_operation(self) -> Callable[[], DeviceTestResult] | None:
        output = self._session.next_valve_percent
        if output is None:
            return None

        if not self._valve_prerequisites_confirmed:
            prerequisites = (
                "Megerősítetted a szelep konfigurált 0%-os és 100%-os feszültségét?",
                "Megerősítetted, hogy a növekvő feszültség nyitja vagy zárja a szelepet?",
                "Megerősítetted a fizikailag biztonságos szelepállapotot?",
                "A rendszer nyomásmentes a szelepteszthez?",
            )
            for question in prerequisites:
                answer = QMessageBox.question(
                    self,
                    "Szelepteszt előfeltétele",
                    question,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return None
            self._valve_prerequisites_confirmed = True

        def confirmed(question: str, *, default_yes: bool = True) -> bool:
            default = (
                QMessageBox.StandardButton.Yes
                if default_yes
                else QMessageBox.StandardButton.No
            )
            return (
                QMessageBox.question(
                    self,
                    f"Szelepteszt — {output:g}%",
                    question,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    default,
                )
                == QMessageBox.StandardButton.Yes
            )

        moved = confirmed("A szelep megmozdult?")
        correct_direction = confirmed("A szelep a megfelelő irányba mozdult?")
        stable = confirmed("A szelep stabilan megállt?")
        abnormal_noise = confirmed(
            "Hallható kattogás vagy rendellenes hang?", default_yes=False
        )
        return lambda: self._session.run_valve_step(
            output_percent=output,
            moved=moved,
            correct_direction=correct_direction,
            stable=stable,
            abnormal_noise=abnormal_noise,
        )

    def _prepare_emergency_operation(
        self,
    ) -> Callable[[], DeviceTestResult] | None:
        emergency = QMessageBox.question(
            self,
            "Vészleállítás tesztje",
            "A kezelő elvégezte a fizikai vészleállítás próbáját, és a rendszer "
            "biztonságosan megállt?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        communication = QMessageBox.question(
            self,
            "Kommunikációvesztés tesztje",
            "A kezelő elvégezte a kommunikáció megszakításának próbáját, és a "
            "rendszer biztonságosan megállt?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return lambda: self._session.run_emergency_communication_test(
            emergency_stop_confirmed=(
                emergency == QMessageBox.StandardButton.Yes
            ),
            communication_loss_confirmed=(
                communication == QMessageBox.StandardButton.Yes
            ),
        )

    def _operation_completed(self, payload: object) -> None:
        self._operation_active = False
        if not isinstance(payload, DeviceTestResult):
            self._operation_failed("érvénytelen teszteredmény")
            return
        if self.STEPS[self._current_step][0] in (
            FunctionalTestDevice.JACKET_PUMP,
            FunctionalTestDevice.INJECTION_PUMP,
        ):
            payload.operator_confirmations["physical_role_confirmed"] = True
        self._set_row_result(
            self._current_step,
            payload.status,
            payload.failure_reason or str(payload.measurements),
        )
        self._save_report()
        if payload.status is DeviceTestStatus.FAILED:
            self._finished = True
            self._disable_progression()
            return
        device = self.STEPS[self._current_step][0]
        if (
            device is FunctionalTestDevice.NI_ANALOG_OUTPUT
            and not self._session.ao_complete
        ) or (
            device is FunctionalTestDevice.HANBAY_VALVE
            and not self._session.valve_complete
        ):
            self._run_button.setEnabled(True)
            self._skip_button.setEnabled(True)
            return
        self._advance()

    def _operation_failed(self, message: str) -> None:
        self._operation_active = False
        self._session.abort(message)
        self._set_row_result(self._current_step, DeviceTestStatus.FAILED, message)
        self._finished = True
        self._disable_progression()
        self._save_report()

    def _skip_current_step(self) -> None:
        device = self.STEPS[self._current_step][0]
        reason, accepted = QInputDialog.getText(
            self, "Teszt kihagyása", "Kötelező indoklás"
        )
        if not accepted:
            return
        try:
            result = self._session.skip(device, reason=reason)
        except ValueError as error:
            self._show_error(str(error))
            return
        self._set_row_result(self._current_step, result.status, reason)
        self._save_report()
        self._advance()

    def _advance(self) -> None:
        self._current_step += 1
        if self._current_step < len(self.STEPS):
            self._table.selectRow(self._current_step)
            self._run_button.setEnabled(True)
            self._skip_button.setEnabled(True)
            return
        self._session.complete()
        self._finished = True
        self._disable_progression()
        self._status.setText("A vezetett eszközteszt befejeződött.")
        self._save_report()

    def _abort(self) -> None:
        errors = self._session.abort("operator aborted guided device test")
        if self._current_step < len(self.STEPS):
            self._set_row_result(
                self._current_step,
                DeviceTestStatus.ABORTED,
                "; ".join(errors) or "Biztonságos leállítás kérve",
            )
        self._finished = True
        self._disable_progression()
        self._save_report()

    def _disable_progression(self) -> None:
        self._run_button.setEnabled(False)
        self._skip_button.setEnabled(False)
        self._begin_button.setEnabled(False)

    def _set_row_result(
        self, row: int, status: DeviceTestStatus, detail: str
    ) -> None:
        self._table.setItem(row, 1, QTableWidgetItem(status.value))
        self._table.setItem(row, 2, QTableWidgetItem(detail))

    def _save_report(self) -> None:
        self._session.report.save(self._report_path)

    def _export_report(self) -> None:
        target, _ = QFileDialog.getSaveFileName(
            self,
            "Eszközteszt-jelentés exportálása",
            str(self._report_path),
            "JSON fájl (*.json)",
        )
        if target:
            self._session.report.save(Path(target))

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "Eszközteszt hiba", message)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._started and not self._finished:
            self._session.abort("guided device test window closed")
            self._save_report()
        super().closeEvent(event)


@dataclass(frozen=True, slots=True)
class ManualTelemetryResult:
    pump_statuses: dict[PumpRole, PumpStatus]
    pump_errors: dict[PumpRole, str]
    pressure_values: dict[str, float]
    pressure_errors: dict[str, str]


@dataclass(frozen=True, slots=True)
class ManualQueuedCommand:
    command_id: str
    operation: Callable[[], object]
    success_message: str
    safety_protected: bool = False


MeasurementPumpPlan = PumpStartupPlan


class PumpControlDialog(ResizableDialog):
    _CLOSE_OPERATION = "manual-control-close"

    def __init__(
        self,
        service: PumpControlService,
        control_loop: ControlLoop,
        active_stage_provider: Callable[[], str],
        *,
        enabled_pumps: frozenset[PumpRole] | None = None,
        enabled_pressure_inputs: frozenset[str] | None = None,
        valve_enabled: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._control_loop = control_loop
        self._active_stage_provider = active_stage_provider
        self._enabled_pumps = (
            enabled_pumps if enabled_pumps is not None else frozenset(PumpRole)
        )
        self._enabled_pressure_inputs = (
            enabled_pressure_inputs
            if enabled_pressure_inputs is not None
            else frozenset({"line_pressure", "differential_pressure"})
        )
        self._valve_enabled = valve_enabled
        self._command_active = False
        self._telemetry_active = False
        self._closing = False
        self._shutdown_started = False
        self._shutdown_complete = False
        self._pending_commands: deque[ManualQueuedCommand] = deque()
        self._active_command: ManualQueuedCommand | None = None
        self._buttons: list[QPushButton] = []
        self._status_labels: dict[PumpRole, QLabel] = {}
        self._modes: dict[PumpRole, QComboBox] = {}
        self._targets: dict[PumpRole, QDoubleSpinBox] = {}
        self._command_bridge = DeviceTestBridge(self)
        self._command_bridge.succeeded.connect(self._command_succeeded)
        self._command_bridge.failed.connect(self._command_failed)
        self._telemetry_bridge = DeviceTestBridge(self)
        self._telemetry_bridge.succeeded.connect(self._telemetry_succeeded)
        self._telemetry_bridge.failed.connect(self._telemetry_failed)
        self.setObjectName("developer_manual_hardware_control")
        self.setWindowTitle("Developer – moduláris manuális eszközvezérlés")
        self.resize(760, 620)
        layout = QVBoxLayout(self)
        warning = QLabel(
            "DEVELOPER / HARDVER MÓD — A pumpa- és szelepparancsok fizikai "
            "eszközökre kerülnek. A manuális biztonsági profil a kiválasztott "
            "eszköz kapcsolatát, saját visszajelzését és határértékét ellenőrzi."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet(
            "padding:10px;background:transparent;color:#d32f4b;font-weight:800;"
            "border:1px solid #d32f4b;border-radius:6px"
        )
        layout.addWidget(warning)
        self._operation_status = QLabel("Vezérlésre kész")
        self._operation_status.setObjectName("manual_control_operation_status")
        self._operation_status.setWordWrap(True)
        self._operation_status.setStyleSheet("color:#66788a;font-weight:700")
        layout.addWidget(self._operation_status)
        layout.addWidget(self._command_queue_panel())
        pumps = QGridLayout()
        for column, role in enumerate(PumpRole):
            panel = self._pump_panel(role)
            panel.setEnabled(role in self._enabled_pumps)
            if role not in self._enabled_pumps:
                panel.setTitle(panel.title() + " — NINCS HOZZÁADVA")
            pumps.addWidget(panel, 0, column)
        layout.addLayout(pumps)
        valve_panel = self._valve_panel()
        valve_panel.setEnabled(self._valve_enabled)
        if not self._valve_enabled:
            valve_panel.setTitle(valve_panel.title() + " — NINCS HOZZÁADVA")
        layout.addWidget(valve_panel)
        telemetry = QGroupBox("Eszközönkénti kapcsolat és élő adatok")
        telemetry_form = QFormLayout(telemetry)
        self._line_pressure_status = QLabel("— bar")
        self._differential_pressure_status = QLabel("— bar")
        self._valve_status = QLabel("— %")
        self._safety_status = QLabel("Nincs lekérdezve")
        self._safety_status.setWordWrap(True)
        telemetry_form.addRow("Vonali nyomás", self._line_pressure_status)
        telemetry_form.addRow(
            "Differenciálnyomás", self._differential_pressure_status
        )
        telemetry_form.addRow("Szelep kimenete", self._valve_status)
        telemetry_form.addRow("Biztonsági állapot", self._safety_status)
        layout.addWidget(telemetry)
        stop_all = self._button("MINDKÉT PUMPA STOP", self._stop_all)
        stop_all.setStyleSheet(
            "background:#b00020;color:white;font-weight:800;padding:10px"
        )
        layout.addWidget(stop_all)
        close = QPushButton("Bezárás")
        close.clicked.connect(self._request_close)
        layout.addWidget(close)
        self._telemetry_timer = QTimer(self)
        self._telemetry_timer.setInterval(1000)
        self._telemetry_timer.timeout.connect(self._refresh_statuses)
        self._telemetry_timer.start()

    def _command_queue_panel(self) -> QGroupBox:
        box = QGroupBox("Parancssor megtekintése és szerkesztése")
        layout = QVBoxLayout(box)
        note = QLabel(
            "A várakozó normál parancsok átrendezhetők vagy törölhetők. "
            "A futó parancs és minden biztonsági művelet zárolt."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self._command_queue_table = QTableWidget(0, 2)
        self._command_queue_table.setObjectName("manual_command_queue")
        self._command_queue_table.setHorizontalHeaderLabels(("Állapot", "Parancs"))
        self._command_queue_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._command_queue_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._command_queue_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._command_queue_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._command_queue_table)
        actions = QHBoxLayout()
        move_up = QPushButton("Fel")
        move_up.clicked.connect(lambda: self._move_selected_command(-1))
        move_down = QPushButton("Le")
        move_down.clicked.connect(lambda: self._move_selected_command(1))
        remove = QPushButton("Kijelölt törlése")
        remove.clicked.connect(self._remove_selected_command)
        for button in (move_up, move_down, remove):
            actions.addWidget(button)
            self._buttons.append(button)
        layout.addLayout(actions)
        self._refresh_command_queue_table()
        return box

    def _refresh_command_queue_table(self) -> None:
        table = self._command_queue_table
        selected_id = self._selected_command_id()
        rows: list[tuple[str, ManualQueuedCommand]] = []
        if self._active_command is not None:
            rows.append(("FOLYAMATBAN — ZÁROLT", self._active_command))
        rows.extend(
            (
                "VÁRAKOZIK — BIZTONSÁGI, ZÁROLT"
                if command.safety_protected
                else "VÁRAKOZIK",
                command,
            )
            for command in self._pending_commands
        )
        table.setRowCount(len(rows))
        for row, (status, command) in enumerate(rows):
            status_item = QTableWidgetItem(status)
            status_item.setData(Qt.ItemDataRole.UserRole, command.command_id)
            table.setItem(row, 0, status_item)
            table.setItem(row, 1, QTableWidgetItem(command.success_message))
            if command.command_id == selected_id:
                table.selectRow(row)

    def _selected_command_id(self) -> str | None:
        table = getattr(self, "_command_queue_table", None)
        if table is None:
            return None
        selected_rows = table.selectionModel().selectedRows()
        if not selected_rows:
            return None
        item = table.item(selected_rows[0].row(), 0)
        if item is None:
            return None
        command_id = item.data(Qt.ItemDataRole.UserRole)
        return command_id if isinstance(command_id, str) else None

    def _move_selected_command(self, offset: int) -> None:
        command_id = self._selected_command_id()
        commands = list(self._pending_commands)
        index = next(
            (i for i, command in enumerate(commands) if command.command_id == command_id),
            None,
        )
        if index is None or commands[index].safety_protected:
            return
        target = index + offset
        if (
            target < 0
            or target >= len(commands)
            or commands[target].safety_protected
        ):
            return
        commands[index], commands[target] = commands[target], commands[index]
        self._pending_commands = deque(commands)
        self._refresh_command_queue_table()

    def _remove_selected_command(self) -> None:
        command_id = self._selected_command_id()
        command = next(
            (
                pending
                for pending in self._pending_commands
                if pending.command_id == command_id
            ),
            None,
        )
        if command is None or command.safety_protected:
            return
        self._pending_commands = deque(
            pending
            for pending in self._pending_commands
            if pending.command_id != command_id
        )
        self._operation_status.setText(
            f"TÖRÖLVE A PARANCSSORBÓL — {command.success_message}"
        )
        self._refresh_command_queue_table()

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
        form.addRow(
            self._button("CSATLAKOZÁS", lambda: self._connect_pump(role))
        )
        form.addRow(self._button("LEVÁLASZTÁS", lambda: self._disconnect_pump(role)))
        form.addRow(self._button("BEÁLLÍTÁS", lambda: self._configure(role)))
        form.addRow(self._button("RUN", lambda: self._run(role)))
        form.addRow(self._button("STOP", lambda: self._stop(role)))
        form.addRow(self._button("CLEAR", lambda: self._clear(role)))
        return box

    def _valve_panel(self) -> QGroupBox:
        box = QGroupBox("Szelep kézi vezérlése")
        form = QFormLayout(box)
        self._valve_target = QDoubleSpinBox()
        self._valve_target.setObjectName("developer_valve_target")
        self._valve_target.setRange(0.0, 100.0)
        self._valve_target.setDecimals(1)
        self._valve_target.setSuffix(" %")
        apply_output = self._button("Szelep kimenet alkalmazása", self._set_valve)
        safe_state = self._button("MINDEN KIMENET BIZTONSÁGOS ÁLLAPOTBA", self._safe_state)
        safe_state.setStyleSheet(
            "background:#b00020;color:white;font-weight:800;padding:10px"
        )
        form.addRow("Kézi kimenet", self._valve_target)
        form.addRow(apply_output)
        form.addRow(safe_state)
        return box

    def _button(self, text: str, callback: Callable[[], None]) -> QPushButton:
        button = QPushButton(text)
        button.clicked.connect(callback)
        self._buttons.append(button)
        return button

    def _execute(
        self,
        operation: Callable[[], object],
        success_message: str,
        *,
        priority: bool = False,
    ) -> None:
        command = ManualQueuedCommand(
            uuid4().hex,
            operation,
            success_message,
            safety_protected=priority,
        )
        if priority:
            self._pending_commands.appendleft(command)
        else:
            self._pending_commands.append(command)
        self._refresh_command_queue_table()
        if self._command_active or self._telemetry_active:
            self._operation_status.setText(
                f"{len(self._pending_commands)} parancs várakozik; a DASNET "
                "műveletek sorrendben hajtódnak végre…"
            )
            self._operation_status.setStyleSheet("color:#9a6700;font-weight:700")
            return
        self._run_next_command()

    def _run_next_command(self) -> None:
        if self._command_active or self._telemetry_active:
            return
        if self._closing:
            self._start_shutdown()
            return
        if not self._pending_commands:
            QTimer.singleShot(100, self._refresh_statuses)
            return
        command = self._pending_commands.popleft()
        operation = command.operation
        success_message = command.success_message
        self._active_command = command
        self._command_active = True
        self._refresh_command_queue_table()
        self._operation_status.setText(f"Folyamatban: {success_message}")
        self._operation_status.setStyleSheet("color:#1565c0;font-weight:700")

        def execute() -> None:
            try:
                result = operation()
            except Exception as error:
                self._command_bridge.failed.emit(str(error))
            else:
                self._command_bridge.succeeded.emit((success_message, result))

        Thread(target=execute, name="eor-pump-command", daemon=True).start()

    def _command_succeeded(self, payload: object) -> None:
        self._command_active = False
        self._active_command = None
        self._refresh_command_queue_table()
        if not isinstance(payload, tuple) or len(payload) != 2:
            self._run_next_command()
            return
        success_message, result = payload
        if success_message == self._CLOSE_OPERATION:
            self._finish_close()
            return
        self._operation_status.setText(f"SIKERES — {success_message}")
        self._operation_status.setStyleSheet("color:#1b7f3a;font-weight:700")
        if isinstance(result, dict):
            for role, status in result.items():
                if isinstance(role, PumpRole) and isinstance(status, PumpStatus):
                    self._status_labels[role].setText(
                        f"{status.pressure_bar:.3f} bar | "
                        f"{status.flow_ml_per_hour:.3f} ml/h | "
                        f"{status.remaining_volume_ml:.3f} ml"
                    )
        elif isinstance(result, ControlCycleResult):
            self._update_live_values(result.record)
        elif isinstance(result, MeasurementRecord):
            self._update_live_values(result)
        QTimer.singleShot(0, self._run_next_command)

    def _command_failed(self, message: str) -> None:
        self._command_active = False
        self._active_command = None
        self._refresh_command_queue_table()
        if self._closing:
            if self._shutdown_started:
                self._finish_close()
            else:
                self._start_shutdown()
            return
        self._operation_status.setText(f"SIKERTELEN — {message}")
        self._operation_status.setStyleSheet("color:#b00020;font-weight:700")
        QMessageBox.critical(self, "Manuális vezérlési hiba", message)
        QTimer.singleShot(0, self._run_next_command)

    def _connect_pump(self, role: PumpRole) -> None:
        self._execute(
            lambda: self._service.connect(role),
            f"{role.value} pump connected",
        )

    def _disconnect_pump(self, role: PumpRole) -> None:
        self._execute(
            lambda: self._service.disconnect(role),
            f"{role.value} pump safely disconnected",
        )

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
        role_name = "köpenypumpát" if role is PumpRole.JACKET else "besajtolópumpát"
        answer = QMessageBox.question(
            self,
            "Fizikai pumpa indítása",
            f"A következő művelet fizikailag elindítja a {role_name}.\n\n"
            "Ellenőrizted a célértéket és engedélyezed a RUN parancsot?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._execute(
                lambda: self._service.run(role, expected), f"{role.value} RUN"
            )

    def _stop(self, role: PumpRole) -> None:
        self._execute(
            lambda: self._service.stop(role), f"{role.value} STOP", priority=True
        )

    def _clear(self, role: PumpRole) -> None:
        self._execute(lambda: self._service.clear(role), f"{role.value} CLEAR")

    def _stop_all(self) -> None:
        def operation() -> None:
            errors = self._service.stop_all()
            if errors:
                raise RuntimeError("; ".join(errors))

        self._execute(operation, "STOP ALL", priority=True)

    def _refresh_statuses(self) -> None:
        if self._command_active or self._telemetry_active or self._pending_commands:
            return
        self._telemetry_active = True

        def execute() -> None:
            try:
                statuses, pump_errors = self._service.read_available_statuses()
            except Exception as error:
                self._telemetry_bridge.failed.emit(str(error))
                return
            pressure_values, pressure_errors = (
                self._control_loop.read_pressure_inputs_individually()
            )
            self._telemetry_bridge.succeeded.emit(
                ManualTelemetryResult(
                    statuses,
                    pump_errors,
                    pressure_values,
                    pressure_errors,
                )
            )

        Thread(target=execute, name="eor-manual-telemetry", daemon=True).start()

    def _telemetry_succeeded(self, result: object) -> None:
        self._telemetry_active = False
        if isinstance(result, ManualTelemetryResult):
            for role in PumpRole:
                if role not in self._enabled_pumps:
                    self._status_labels[role].setText("NINCS HOZZÁADVA")
                    continue
                status = result.pump_statuses.get(role)
                if status is not None:
                    self._status_labels[role].setText(
                        f"KAPCSOLÓDVA | {status.pressure_bar:.3f} bar | "
                        f"{status.flow_ml_per_hour:.3f} ml/h | "
                        f"{status.remaining_volume_ml:.3f} ml"
                    )
                else:
                    self._status_labels[role].setText(
                        "NINCS KAPCSOLAT — "
                        + result.pump_errors.get(role, "ismeretlen hiba")
                    )
            line_pressure = result.pressure_values.get("line_pressure")
            differential_pressure = result.pressure_values.get(
                "differential_pressure"
            )
            self._line_pressure_status.setText(
                "NINCS HOZZÁADVA"
                if "line_pressure" not in self._enabled_pressure_inputs
                else f"KAPCSOLÓDVA | {line_pressure:.3f} bar"
                if line_pressure is not None
                else "NINCS KAPCSOLAT — "
                + result.pressure_errors.get("line_pressure", "ismeretlen hiba")
            )
            self._differential_pressure_status.setText(
                "NINCS HOZZÁADVA"
                if "differential_pressure" not in self._enabled_pressure_inputs
                else f"KAPCSOLÓDVA | {differential_pressure:.3f} bar"
                if differential_pressure is not None
                else "NINCS KAPCSOLAT — "
                + result.pressure_errors.get(
                    "differential_pressure", "ismeretlen hiba"
                )
            )
            connection_errors = [
                *(
                    f"{role.value}: {message}"
                    for role, message in result.pump_errors.items()
                    if role in self._enabled_pumps
                ),
                *(
                    f"{key}: {message}"
                    for key, message in result.pressure_errors.items()
                    if key in self._enabled_pressure_inputs
                ),
            ]
            if connection_errors:
                self._safety_status.setText(
                    "RÉSZLEGES KAPCSOLAT — a sikeres eszközök ettől függetlenül "
                    "kezelhetők. A manuális biztonsági profil csak a megcélzott "
                    "eszközt ellenőrzi. Hibák: " + "; ".join(connection_errors)
                )
                self._safety_status.setStyleSheet("color:#b00020;font-weight:700")
            else:
                self._safety_status.setText(
                    "AZ AKTÍV ESZKÖZÖK ELÉRHETŐK — minden manuális parancs "
                    "céleszköz-specifikus biztonsági ellenőrzést kap."
                )
                self._safety_status.setStyleSheet("color:#1b7f3a;font-weight:700")
        elif isinstance(result, MeasurementRecord):
            self._update_live_values(result)
        self._run_next_command()

    def _telemetry_failed(self, message: str) -> None:
        self._telemetry_active = False
        self._safety_status.setText(f"TELEMETRIA HIBA — {message}")
        self._safety_status.setStyleSheet("color:#b00020;font-weight:700")
        self._run_next_command()

    def _request_close(self) -> None:
        if self._shutdown_complete:
            QDialog.accept(self)
            return
        if self._closing:
            return
        self._closing = True
        self._telemetry_timer.stop()
        self._pending_commands.clear()
        self._refresh_command_queue_table()
        for button in self._buttons:
            button.setEnabled(False)
        self._operation_status.setText(
            "Bezárás: pumpák leállítása és COM-portok lezárása…"
        )
        self._operation_status.setStyleSheet("color:#9a6700;font-weight:700")
        if not self._command_active and not self._telemetry_active:
            self._start_shutdown()

    def _start_shutdown(self) -> None:
        if self._shutdown_started:
            return
        shutdown = getattr(self._service, "shutdown_connections", None)
        if not callable(shutdown):
            self._finish_close()
            return
        self._shutdown_started = True
        self._command_active = True

        def execute() -> None:
            try:
                errors = shutdown()
            except Exception as error:
                self._command_bridge.failed.emit(str(error))
            else:
                self._command_bridge.succeeded.emit(
                    (self._CLOSE_OPERATION, errors)
                )

        Thread(target=execute, name="eor-manual-close", daemon=True).start()

    def _finish_close(self) -> None:
        self._command_active = False
        self._shutdown_complete = True
        QDialog.accept(self)

    def accept(self) -> None:
        self._request_close()

    def reject(self) -> None:
        self._request_close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._shutdown_complete:
            event.accept()
            super().closeEvent(event)
            return
        event.ignore()
        self._request_close()

    def _set_valve(self) -> None:
        target = self._valve_target.value()
        answer = QMessageBox.question(
            self,
            "Fizikai szelep kimenetének módosítása",
            f"A következő művelet {target:g}%-ra állítja a fizikai szelep "
            "analóg kimenetét.\n\nEngedélyezed ezt a kimeneti írást?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._execute(
            lambda: self._control_loop.write_manual_output(target),
            "valve output applied",
        )

    def _safe_state(self) -> None:
        self._execute(
            self._control_loop.request_safe_state,
            "safe state requested",
            priority=True,
        )

    def _update_live_values(self, record: MeasurementRecord) -> None:
        snapshot = record.snapshot
        self._status_labels[PumpRole.JACKET].setText(
            f"{snapshot.jacket_pump.pressure_bar:.3f} bar | "
            f"{snapshot.jacket_pump.flow_ml_per_hour:.3f} ml/h | "
            f"{snapshot.jacket_pump.remaining_volume_ml:.3f} ml"
        )
        self._status_labels[PumpRole.INJECTION].setText(
            f"{snapshot.injection_pump.pressure_bar:.3f} bar | "
            f"{snapshot.injection_pump.flow_ml_per_hour:.3f} ml/h | "
            f"{snapshot.injection_pump.remaining_volume_ml:.3f} ml"
        )
        self._line_pressure_status.setText(
            "NINCS HOZZÁADVA"
            if snapshot.line_pressure_bar is None
            else f"{snapshot.line_pressure_bar:.3f} bar"
        )
        self._differential_pressure_status.setText(
            "NINCS HOZZÁADVA"
            if snapshot.differential_pressure_bar is None
            else f"{snapshot.differential_pressure_bar:.3f} bar"
        )
        self._valve_status.setText(f"{snapshot.valve_percent:.1f} %")
        if record.safety_reasons:
            self._safety_status.setText(
                "RETESZELVE — " + "; ".join(record.safety_reasons)
            )
            self._safety_status.setStyleSheet("color:#b00020;font-weight:700")
        else:
            self._safety_status.setText("RENDBEN — kimenet engedélyezhető")
            self._safety_status.setStyleSheet("color:#1b7f3a;font-weight:700")


class LoggingSettingsDialog(ResizableDialog):
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
        self._content_scroll = QScrollArea()
        self._content_scroll.setObjectName("logging_settings_scroll")
        self._content_scroll.setWidgetResizable(True)
        self._content_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._content_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        content = QWidget()
        content.setMinimumWidth(0)
        content.setMinimumHeight(620)
        content_layout = QVBoxLayout(content)
        self.enabled = QCheckBox("Kommunikációs és fejlesztői naplózás engedélyezése")
        self.enabled.setChecked(logger.enabled)
        content_layout.addWidget(self.enabled)
        categories_box = QGroupBox("Naplózott területek")
        categories_layout = QVBoxLayout(categories_box)
        self.category_checks: dict[DiagnosticCategory, QCheckBox] = {}
        enabled_categories = logger.categories
        for category, label in self.CATEGORY_LABELS.items():
            checkbox = QCheckBox(label)
            checkbox.setChecked(category in enabled_categories)
            categories_layout.addWidget(checkbox)
            self.category_checks[category] = checkbox
        content_layout.addWidget(categories_box)
        retention = logger.retention_settings
        retention_box = QGroupBox("Szerviz — automatikus naplómegőrzés")
        retention_form = QFormLayout(retention_box)
        retention_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        retention_form.setVerticalSpacing(8)
        self.retention_days = QSpinBox()
        self.retention_days.setObjectName("logging_retention_days")
        self.retention_days.setRange(1, 3650)
        self.retention_days.setValue(retention.retention_days)
        self.measurement_retention_days = QSpinBox()
        self.measurement_retention_days.setObjectName(
            "logging_measurement_retention_days"
        )
        self.measurement_retention_days.setRange(1, 3650)
        self.measurement_retention_days.setValue(
            retention.measurement_retention_days
        )
        self.maximum_file_size = QSpinBox()
        self.maximum_file_size.setObjectName("logging_maximum_file_size_mb")
        self.maximum_file_size.setRange(1, 2048)
        self.maximum_file_size.setSuffix(" MiB")
        self.maximum_file_size.setValue(retention.maximum_file_size_mb)
        self.maximum_rotated_files = QSpinBox()
        self.maximum_rotated_files.setObjectName("logging_maximum_rotated_files")
        self.maximum_rotated_files.setRange(1, 1000)
        self.maximum_rotated_files.setValue(retention.maximum_rotated_files)
        self.total_storage_limit = QSpinBox()
        self.total_storage_limit.setObjectName("logging_total_storage_limit_mb")
        self.total_storage_limit.setRange(1, 102400)
        self.total_storage_limit.setSuffix(" MiB")
        self.total_storage_limit.setValue(retention.total_storage_limit_mb)
        self.compression_enabled = QCheckBox("Lezárt naplók tömörítése")
        self.compression_enabled.setObjectName("logging_compression_enabled")
        self.compression_enabled.setChecked(retention.compression_enabled)
        self.automatic_cleanup = QCheckBox(
            "Automatikus tisztítás indításkor és naponta"
        )
        self.automatic_cleanup.setObjectName("logging_automatic_cleanup_enabled")
        self.automatic_cleanup.setChecked(retention.automatic_cleanup_enabled)
        for field in (
            self.retention_days,
            self.measurement_retention_days,
            self.maximum_file_size,
            self.maximum_rotated_files,
            self.total_storage_limit,
        ):
            field.setMinimumHeight(38)
        retention_form.addRow("Alkalmazás- és hardvernaplók:", self.retention_days)
        retention_form.addRow("Mérési diagnosztikai naplók:", self.measurement_retention_days)
        retention_form.addRow("Aktív fájl mérethatára:", self.maximum_file_size)
        retention_form.addRow("Rotált fájlok maximuma:", self.maximum_rotated_files)
        retention_form.addRow("Összesített tárhelykorlát:", self.total_storage_limit)
        retention_form.addRow(self.compression_enabled)
        retention_form.addRow(self.automatic_cleanup)
        content_layout.addWidget(retention_box)
        path_label = QLabel(
            f"Alkalmazásnapló: {logger.path}\n"
            f"Hardverkommunikáció: {logger.hardware_path}\n"
            f"Naplókönyvtár mérete: {logger.directory_size_bytes / 1024 / 1024:.2f} MiB\n"
            "Utolsó tisztítás: "
            + (
                logger.last_maintenance.summary
                if logger.last_maintenance is not None
                else "még nem futott"
            )
        )
        path_label.setStyleSheet("color:#66788a")
        path_label.setWordWrap(True)
        path_label.setMinimumWidth(0)
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        content_layout.addWidget(path_label)
        content_layout.addStretch(1)
        self._content_scroll.setWidget(content)
        layout.addWidget(self._content_scroll, 1)
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Save).setText("Mentés")
        self._buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Mégse")
        self._buttons.accepted.connect(self._save)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

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
        retention = LogRetentionSettings(
            retention_days=self.retention_days.value(),
            measurement_retention_days=self.measurement_retention_days.value(),
            maximum_file_size_mb=self.maximum_file_size.value(),
            maximum_rotated_files=self.maximum_rotated_files.value(),
            total_storage_limit_mb=self.total_storage_limit.value(),
            compression_enabled=self.compression_enabled.isChecked(),
            automatic_cleanup_enabled=self.automatic_cleanup.isChecked(),
        )
        self._logger.configure_retention(retention)
        for key, value in (
            ("retention_days", retention.retention_days),
            ("measurement_retention_days", retention.measurement_retention_days),
            ("maximum_file_size_mb", retention.maximum_file_size_mb),
            ("maximum_rotated_files", retention.maximum_rotated_files),
            ("total_storage_limit_mb", retention.total_storage_limit_mb),
            ("compression_enabled", retention.compression_enabled),
            (
                "automatic_cleanup_enabled",
                retention.automatic_cleanup_enabled,
            ),
        ):
            self._settings.setValue(f"logging/{key}", value)
        self._settings.sync()
        if retention.automatic_cleanup_enabled:
            self._logger.cleanup_logs_async()
        self.accept()


class ControlCycleSettingsDialog(ResizableDialog):
    DEFAULT_INTERVAL_SECONDS = 0.2
    DEFAULT_WATCHDOG_TOLERANCE_SECONDS = 0.05

    def __init__(
        self,
        settings: QSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("Developer – vezérlési ciklus")
        self.resize(560, 300)
        layout = QVBoxLayout(self)

        help_text = QLabel(
            "A ciklusidő határozza meg a vezérlés tervezett gyakoriságát. A watchdog "
            "akkor jelez hibát, ha a ciklus futása hosszabb a ciklusidő és a tűrés "
            "összegénél. A nagyobb érték lassítja a PID-et és a biztonsági felügyeletet."
        )
        help_text.setWordWrap(True)
        layout.addWidget(help_text)

        form = QFormLayout()
        self.control_interval = self._seconds_field(0.05, 10.0, 0.05)
        self.control_interval.setObjectName("developer_control_interval_seconds")
        self.control_interval.setValue(
            self.setting_value(
                settings,
                "developer/control_interval_seconds",
                self.DEFAULT_INTERVAL_SECONDS,
            )
        )
        self.watchdog_tolerance = self._seconds_field(0.0, 10.0, 0.05)
        self.watchdog_tolerance.setObjectName(
            "developer_watchdog_tolerance_seconds"
        )
        self.watchdog_tolerance.setValue(
            self.setting_value(
                settings,
                "developer/watchdog_tolerance_seconds",
                self.DEFAULT_WATCHDOG_TOLERANCE_SECONDS,
            )
        )
        form.addRow(
            input_field_label("Vezérlési ciklusidő", self.control_interval),
            self.control_interval,
        )
        form.addRow(
            input_field_label("Watchdog-tűrés", self.watchdog_tolerance),
            self.watchdog_tolerance,
        )
        layout.addLayout(form)

        self.deadline = QLabel()
        self.deadline.setObjectName("developer_control_deadline_summary")
        self.deadline.setWordWrap(True)
        layout.addWidget(self.deadline)
        self.control_interval.valueChanged.connect(self._refresh_deadline)
        self.watchdog_tolerance.valueChanged.connect(self._refresh_deadline)
        self._refresh_deadline()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _seconds_field(minimum: float, maximum: float, step: float) -> QDoubleSpinBox:
        field = QDoubleSpinBox()
        field.setRange(minimum, maximum)
        field.setDecimals(3)
        field.setSingleStep(step)
        field.setSuffix(" s")
        return field

    @staticmethod
    def setting_value(settings: QSettings, key: str, default: float) -> float:
        try:
            value = float(str(settings.value(key, default)))
        except (TypeError, ValueError):
            return default
        if not isfinite(value):
            return default
        if key.endswith("control_interval_seconds"):
            return min(10.0, max(0.05, value))
        if key.endswith("watchdog_tolerance_seconds"):
            return min(10.0, max(0.0, value))
        return value

    def _refresh_deadline(self, *_args: object) -> None:
        maximum_duration = self.control_interval.value() + self.watchdog_tolerance.value()
        self.deadline.setText(
            f"Jelenlegi végrehajtási határidő: {maximum_duration:.3f} s. "
            "A 0,859 s-os ciklushoz például 1,000 s ciklusidő használható."
        )

    def _save(self) -> None:
        self._settings.setValue(
            "developer/control_interval_seconds", self.control_interval.value()
        )
        self._settings.setValue(
            "developer/watchdog_tolerance_seconds", self.watchdog_tolerance.value()
        )
        self._settings.sync()
        self.accept()


class PumpTelemetrySettingsDialog(ResizableDialog):
    """Service-only polling and STALE thresholds for physical ISCO pumps."""

    SETTINGS = {
        "pressure_seconds": "developer/pump_pressure_poll_seconds",
        "slow_telemetry_seconds": "developer/pump_slow_poll_seconds",
        "status_poll_seconds": "developer/pump_status_poll_seconds",
        "pressure_stale_seconds": "developer/pump_pressure_stale_seconds",
        "slow_telemetry_stale_seconds": "developer/pump_slow_stale_seconds",
        "status_stale_seconds": "developer/pump_status_stale_seconds",
        "startup_timeout_seconds": "developer/pump_startup_timeout_seconds",
    }

    def __init__(
        self,
        settings: QSettings,
        parent: QWidget | None = None,
        *,
        active_intervals: PumpPollingIntervals | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("Developer – pumpatelemetria és STALE")
        self.resize(620, 470)
        layout = QVBoxLayout(self)

        warning = QLabel(
            "SZERVIZBEÁLLÍTÁS — A nyomás STALE-határa biztonsági időkorlát. "
            "Növelése késlelteti a pumpakapcsolat elvesztésének felismerését. "
            "A mentett értékek a következő hardveraktiváláskor lépnek életbe."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet(
            "color:#9a6700;font-weight:700;padding:8px;"
            "border:1px solid #9a6700;border-radius:6px"
        )
        layout.addWidget(warning)

        defaults = self.intervals(settings)
        self.saved_settings = QLabel(self._interval_summary("Mentett", defaults))
        self.saved_settings.setObjectName("pump_telemetry_saved_settings")
        self.saved_settings.setWordWrap(True)
        layout.addWidget(self.saved_settings)
        active_text = (
            self._interval_summary("Aktív", active_intervals)
            if active_intervals is not None
            else "Aktív: nincs fizikai pumpaworker. A mentett értékek a következő "
            "hardveraktiváláskor lépnek életbe."
        )
        self.active_settings = QLabel(active_text)
        self.active_settings.setObjectName("pump_telemetry_active_settings")
        self.active_settings.setWordWrap(True)
        layout.addWidget(self.active_settings)
        form = QFormLayout()
        self.pressure_poll = self._seconds_field(0.1, 10.0, 0.1)
        self.pressure_poll.setObjectName("pump_pressure_poll_seconds")
        self.pressure_poll.setValue(defaults.pressure_seconds)
        self.slow_poll = self._seconds_field(0.3, 60.0, 0.1)
        self.slow_poll.setObjectName("pump_slow_poll_seconds")
        self.slow_poll.setValue(defaults.slow_telemetry_seconds)
        self.status_poll = self._seconds_field(1.0, 60.0, 0.5)
        self.status_poll.setObjectName("pump_status_poll_seconds")
        self.status_poll.setValue(defaults.status_poll_seconds)
        self.pressure_stale = self._seconds_field(0.2, 60.0, 0.1)
        self.pressure_stale.setObjectName("pump_pressure_stale_seconds")
        self.pressure_stale.setValue(defaults.pressure_stale_seconds)
        self.slow_stale = self._seconds_field(0.5, 600.0, 0.5)
        self.slow_stale.setObjectName("pump_slow_stale_seconds")
        self.slow_stale.setValue(defaults.slow_telemetry_stale_seconds)
        self.status_stale = self._seconds_field(0.5, 120.0, 0.5)
        self.status_stale.setObjectName("pump_status_stale_seconds")
        self.status_stale.setValue(defaults.status_stale_seconds)
        self.startup_timeout = self._seconds_field(1.0, 120.0, 0.5)
        self.startup_timeout.setObjectName("pump_startup_timeout_seconds")
        self.startup_timeout.setValue(defaults.startup_timeout_seconds)
        form.addRow(
            input_field_label("Nyomás polling időköze", self.pressure_poll),
            self.pressure_poll,
        )
        form.addRow(
            input_field_label(
                "Telemetria-lekérdezések szünete",
                self.slow_poll,
            ),
            self.slow_poll,
        )
        form.addRow(
            input_field_label("STATUS polling időköze", self.status_poll),
            self.status_poll,
        )
        form.addRow(
            input_field_label("Nyomás STALE-határa", self.pressure_stale),
            self.pressure_stale,
        )
        form.addRow(
            input_field_label("FLOW/VOLA STALE-határa", self.slow_stale),
            self.slow_stale,
        )
        form.addRow(
            input_field_label("STATUS STALE-határa", self.status_stale),
            self.status_stale,
        )
        form.addRow(
            input_field_label("Kezdő telemetria timeout", self.startup_timeout),
            self.startup_timeout,
        )
        layout.addLayout(form)

        self.validation = QLabel()
        self.validation.setObjectName("pump_telemetry_validation")
        self.validation.setWordWrap(True)
        layout.addWidget(self.validation)
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        self._save_button = self._buttons.button(
            QDialogButtonBox.StandardButton.Save
        )
        self._buttons.accepted.connect(self._save)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)
        for field in (
            self.pressure_poll,
            self.slow_poll,
            self.status_poll,
            self.pressure_stale,
            self.slow_stale,
            self.status_stale,
            self.startup_timeout,
        ):
            field.valueChanged.connect(self._refresh_validation)
        self._refresh_validation()

    @staticmethod
    def _seconds_field(
        minimum: float, maximum: float, step: float
    ) -> QDoubleSpinBox:
        field = QDoubleSpinBox()
        field.setRange(minimum, maximum)
        field.setDecimals(3)
        field.setSingleStep(step)
        field.setSuffix(" s")
        return field

    @staticmethod
    def _interval_summary(
        label: str,
        intervals: PumpPollingIntervals,
    ) -> str:
        return (
            f"{label}: PRESS {intervals.pressure_seconds:.3f} s; STATUS "
            f"{intervals.status_poll_seconds:.3f} s; lassú szünet "
            f"{intervals.slow_telemetry_seconds:.3f} s; sorrend: PRESS elsőbbség, "
            "STATUS, majd FLOW/VOLA körforgás; STALE PRESS/FLOW-VOLA/STATUS "
            f"{intervals.pressure_stale_seconds:.3f} / "
            f"{intervals.slow_telemetry_stale_seconds:.3f} / "
            f"{intervals.status_stale_seconds:.3f} s."
        )

    def selected_intervals(self) -> PumpPollingIntervals:
        pressure_poll = self.pressure_poll.value()
        minimum_pressure_stale = self.minimum_pressure_stale_seconds(
            self._settings, pressure_poll
        )
        if self.pressure_stale.value() < minimum_pressure_stale:
            raise ValueError(
                "pump pressure stale limit must cover the configured DASNET "
                f"timeout/retry budget ({minimum_pressure_stale:.3f} s)"
            )
        minimum_slow_stale = self.minimum_slow_stale_seconds(
            self._settings, self.slow_poll.value()
        )
        if self.slow_stale.value() < minimum_slow_stale:
            raise ValueError(
                "FLOW/VOLA stale limit must cover a complete prioritized poll "
                f"round ({minimum_slow_stale:.3f} s serial retry budget)"
            )
        minimum_status_stale = self.minimum_status_stale_seconds()
        if self.status_stale.value() < minimum_status_stale:
            raise ValueError(
                "STATUS stale limit must cover the prioritized polling round "
                f"({minimum_status_stale:.3f} s serial retry budget)"
            )
        minimum_startup_timeout = self.minimum_startup_timeout_seconds(
            self._settings
        )
        if self.startup_timeout.value() < minimum_startup_timeout:
            raise ValueError(
                "startup telemetry timeout must cover PRESS and STATUS retry "
                f"budgets ({minimum_startup_timeout:.3f} s)"
            )
        return PumpPollingIntervals(
            pressure_seconds=pressure_poll,
            slow_telemetry_seconds=self.slow_poll.value(),
            status_poll_seconds=self.status_poll.value(),
            pressure_stale_seconds=self.pressure_stale.value(),
            slow_telemetry_stale_seconds=self.slow_stale.value(),
            status_stale_seconds=self.status_stale.value(),
            startup_timeout_seconds=self.startup_timeout.value(),
        )

    @classmethod
    def intervals(
        cls,
        settings: QSettings,
        *,
        command_timeout_seconds: float | None = None,
        command_attempts: int | None = None,
    ) -> PumpPollingIntervals:
        defaults = PumpPollingIntervals()

        def value(field: str, fallback: float) -> float:
            try:
                parsed = float(
                    str(settings.value(cls.SETTINGS[field], fallback))
                )
            except (TypeError, ValueError):
                return fallback
            return parsed if isfinite(parsed) else fallback

        pressure_stale_fallback = defaults.pressure_stale_seconds
        if not settings.contains(cls.SETTINGS["pressure_stale_seconds"]):
            try:
                legacy_pressure_stale = float(
                    str(
                        settings.value(
                            "hardware/stale_timeout_seconds",
                            defaults.pressure_stale_seconds,
                        )
                    )
                )
            except (TypeError, ValueError):
                legacy_pressure_stale = defaults.pressure_stale_seconds
            if isfinite(legacy_pressure_stale):
                pressure_stale_fallback = legacy_pressure_stale
        pressure_seconds = value("pressure_seconds", defaults.pressure_seconds)
        slow_telemetry_seconds = value(
            "slow_telemetry_seconds", defaults.slow_telemetry_seconds
        )
        status_poll_seconds = value(
            "status_poll_seconds", defaults.status_poll_seconds
        )
        configured_pressure_stale = value(
            "pressure_stale_seconds", pressure_stale_fallback
        )
        minimum_pressure_stale = cls.minimum_pressure_stale_seconds(
            settings,
            pressure_seconds,
            command_timeout_seconds=command_timeout_seconds,
            command_attempts=command_attempts,
        )
        minimum_startup_timeout = cls.minimum_startup_timeout_seconds(
            settings,
            command_timeout_seconds=command_timeout_seconds,
            command_attempts=command_attempts,
        )
        minimum_slow_stale = cls.minimum_slow_stale_seconds(
            settings,
            slow_telemetry_seconds,
            command_timeout_seconds=command_timeout_seconds,
            command_attempts=command_attempts,
        )
        minimum_status_stale = cls.minimum_status_stale_seconds()
        try:
            return PumpPollingIntervals(
                pressure_seconds=pressure_seconds,
                slow_telemetry_seconds=slow_telemetry_seconds,
                status_poll_seconds=status_poll_seconds,
                pressure_stale_seconds=max(
                    configured_pressure_stale, minimum_pressure_stale
                ),
                slow_telemetry_stale_seconds=max(
                    value(
                        "slow_telemetry_stale_seconds",
                        defaults.slow_telemetry_stale_seconds,
                    ),
                    minimum_slow_stale,
                ),
                status_stale_seconds=max(
                    value("status_stale_seconds", defaults.status_stale_seconds),
                    minimum_status_stale,
                    status_poll_seconds,
                ),
                startup_timeout_seconds=max(
                    value(
                        "startup_timeout_seconds",
                        defaults.startup_timeout_seconds,
                    ),
                    minimum_startup_timeout,
                ),
            )
        except ValueError:
            return PumpPollingIntervals(
                pressure_seconds=defaults.pressure_seconds,
                slow_telemetry_seconds=defaults.slow_telemetry_seconds,
                status_poll_seconds=defaults.status_poll_seconds,
                pressure_stale_seconds=max(
                    defaults.pressure_stale_seconds,
                    cls.minimum_pressure_stale_seconds(
                        settings,
                        defaults.pressure_seconds,
                        command_timeout_seconds=command_timeout_seconds,
                        command_attempts=command_attempts,
                    ),
                ),
                slow_telemetry_stale_seconds=(
                    max(defaults.slow_telemetry_stale_seconds, minimum_slow_stale)
                ),
                status_stale_seconds=max(
                    defaults.status_stale_seconds, minimum_status_stale
                ),
                startup_timeout_seconds=max(
                    defaults.startup_timeout_seconds,
                    minimum_startup_timeout,
                ),
            )

    @staticmethod
    def minimum_startup_timeout_seconds(
        settings: QSettings,
        *,
        command_timeout_seconds: float | None = None,
        command_attempts: int | None = None,
    ) -> float:
        timeout, attempts = PumpTelemetrySettingsDialog._serial_retry_budget(
            settings,
            command_timeout_seconds=command_timeout_seconds,
            command_attempts=command_attempts,
        )
        return 2.0 * timeout * attempts

    @staticmethod
    def minimum_pressure_stale_seconds(
        settings: QSettings,
        pressure_seconds: float,
        *,
        command_timeout_seconds: float | None = None,
        command_attempts: int | None = None,
    ) -> float:
        timeout, attempts = PumpTelemetrySettingsDialog._serial_retry_budget(
            settings,
            command_timeout_seconds=command_timeout_seconds,
            command_attempts=command_attempts,
        )
        return max(
            3.0 * pressure_seconds,
            timeout * attempts + 2.0 * pressure_seconds,
        )

    @staticmethod
    def minimum_status_stale_seconds() -> float:
        """Return the explicitly approved safety recognition limit."""
        return 8.0

    @staticmethod
    def minimum_slow_stale_seconds(
        settings: QSettings,
        slow_telemetry_seconds: float,
        *,
        command_timeout_seconds: float | None = None,
        command_attempts: int | None = None,
    ) -> float:
        timeout, attempts = PumpTelemetrySettingsDialog._serial_retry_budget(
            settings,
            command_timeout_seconds=command_timeout_seconds,
            command_attempts=command_attempts,
        )
        # FLOW and VOLA alternate; prioritized PRESS/STATUS traffic comes first.
        return 8.0 * timeout * attempts + 2.0 * slow_telemetry_seconds

    @staticmethod
    def _serial_retry_budget(
        settings: QSettings,
        *,
        command_timeout_seconds: float | None,
        command_attempts: int | None,
    ) -> tuple[float, int]:
        try:
            timeout = (
                float(command_timeout_seconds)
                if command_timeout_seconds is not None
                else float(
                    str(settings.value("hardware/serial_command_timeout_seconds", 2.0))
                )
            )
            attempts = (
                int(command_attempts)
                if command_attempts is not None
                else int(str(settings.value("hardware/serial_command_retries", 2)))
            )
        except (TypeError, ValueError):
            timeout, attempts = 2.0, 2
        if not isfinite(timeout) or timeout <= 0.0 or attempts < 1:
            return 2.0, 2
        return timeout, attempts

    def _refresh_validation(self, *_args: object) -> None:
        try:
            intervals = self.selected_intervals()
        except ValueError as error:
            self.validation.setText(f"HIBÁS BEÁLLÍTÁS: {error}")
            self.validation.setStyleSheet("color:#b00020;font-weight:700")
            self._save_button.setEnabled(False)
            return
        self.validation.setText(
            "Érvényes beállítás. Nyomáskimaradás legkésőbb "
            f"{intervals.pressure_stale_seconds:.3f} s után válik STALE-lé."
        )
        self.validation.setStyleSheet("color:#1b7f3a;font-weight:700")
        self._save_button.setEnabled(True)

    def _save(self) -> None:
        intervals = self.selected_intervals()
        values = {
            "pressure_seconds": intervals.pressure_seconds,
            "slow_telemetry_seconds": intervals.slow_telemetry_seconds,
            "status_poll_seconds": intervals.status_poll_seconds,
            "pressure_stale_seconds": intervals.pressure_stale_seconds,
            "slow_telemetry_stale_seconds": (
                intervals.slow_telemetry_stale_seconds
            ),
            "status_stale_seconds": intervals.status_stale_seconds,
            "startup_timeout_seconds": intervals.startup_timeout_seconds,
        }
        for field, value in values.items():
            self._settings.setValue(self.SETTINGS[field], value)
        self._settings.setValue(
            "hardware/stale_timeout_seconds", intervals.pressure_stale_seconds
        )
        self._settings.sync()
        self.saved_settings.setText(self._interval_summary("Mentett", intervals))
        self.accept()


class DeveloperViewDialog(ResizableDialog):
    APPLICATION_ENDPOINT = "EOR vezérlőalkalmazás"
    ENDPOINT_LABELS = {
        DiagnosticCategory.SYSTEM: "EOR alkalmazásrendszer",
        DiagnosticCategory.RUNTIME: "Vezérlési runtime / watchdog",
        DiagnosticCategory.JACKET_PUMP: "Köpenypumpa",
        DiagnosticCategory.INJECTION_PUMP: "Besajtolópumpa",
        DiagnosticCategory.NI_LINE: "NI USB-6001 – vonali nyomásbemenet",
        DiagnosticCategory.NI_DIFFERENTIAL: "NI USB-6001 – differenciálnyomás-bemenet",
        DiagnosticCategory.NI_VALVE: "NI USB-6001 – szelep analóg kimenet",
    }
    TRANSPORT_LABELS = {
        DiagnosticCategory.SYSTEM: "Belső esemény",
        DiagnosticCategory.RUNTIME: "Belső esemény",
        DiagnosticCategory.JACKET_PUMP: "DASNET / RS-232",
        DiagnosticCategory.INJECTION_PUMP: "DASNET / RS-232",
        DiagnosticCategory.NI_LINE: "NI-DAQmx",
        DiagnosticCategory.NI_DIFFERENTIAL: "NI-DAQmx",
        DiagnosticCategory.NI_VALVE: "NI-DAQmx",
    }

    def __init__(self, logger: DiagnosticLogger, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._logger = logger
        self._last_sequence = 0
        self.setObjectName("device_communication_dialog")
        self.setWindowTitle("Eszközkommunikáció")
        self.setMinimumSize(620, 400)
        screen = QApplication.primaryScreen()
        available_size = screen.availableGeometry().size() if screen is not None else None
        width = min(1180, max(620, available_size.width() - 40)) if available_size else 1100
        height = min(720, max(400, available_size.height() - 40)) if available_size else 620
        self.resize(width, height)
        layout = QVBoxLayout(self)

        title = QLabel("Eszközkommunikáció")
        title.setObjectName("device_communication_title")
        title.setStyleSheet("font-size:18px;font-weight:700")
        layout.addWidget(title)
        help_text = QLabel(
            "A napló megmutatja, melyik komponens honnan, hová és milyen "
            "kapcsolaton küldött vagy fogadott adatot. A megjelenítés nem indít "
            "új hardverműveletet."
        )
        help_text.setObjectName("device_communication_help")
        help_text.setWordWrap(True)
        help_text.setStyleSheet("color:#66788a")
        layout.addWidget(help_text)

        controls_box = QGroupBox("Megjelenítés")
        controls = QGridLayout(controls_box)
        self._filter = QComboBox()
        self._filter.setObjectName("device_communication_filter")
        self._filter.addItem("Minden kategória", None)
        for category, label in LoggingSettingsDialog.CATEGORY_LABELS.items():
            self._filter.addItem(label, category.value)
        self._filter.currentIndexChanged.connect(self._rebuild)
        self._clear_button = QPushButton("Memórianapló törlése")
        self._clear_button.clicked.connect(self._clear)
        self._status = QLabel()
        self._status.setObjectName("device_communication_status")
        controls.addWidget(input_field_label("Szűrés", self._filter), 0, 0)
        controls.addWidget(self._filter, 0, 1, 1, 2)
        controls.addWidget(self._status, 1, 0, 1, 2)
        controls.addWidget(self._clear_button, 1, 2)
        controls.setColumnStretch(1, 1)
        layout.addWidget(controls_box)

        events_box = QGroupBox("Kommunikációs események")
        events_layout = QVBoxLayout(events_box)
        self._table = QTableWidget(0, 8)
        self._table.setObjectName("device_communication_table")
        self._table.setHorizontalHeaderLabels(
            (
                "Magyar idő",
                "Monotonic",
                "Szint",
                "Forrás",
                "Irány",
                "Cél / címzett",
                "Kapcsolat",
                "Küldött / fogadott tartalom",
            )
        )
        self._table.setAlternatingRowColors(True)
        self._table.setWordWrap(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setSizeAdjustPolicy(
            QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored
        )
        self._table.setMinimumWidth(0)
        self._table.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        header = self._table.horizontalHeader()
        header.setStretchLastSection(False)
        for column in (0, 1, 2, 4, 6):
            header.setSectionResizeMode(column, header.ResizeMode.ResizeToContents)
        for column in (3, 5, 7):
            header.setSectionResizeMode(column, header.ResizeMode.Stretch)
        events_layout.addWidget(self._table)
        layout.addWidget(events_box, 1)

        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self._buttons.setObjectName("device_communication_buttons")
        self._buttons.button(QDialogButtonBox.StandardButton.Close).setText("Bezárás")
        self._buttons.rejected.connect(self.accept)
        layout.addWidget(self._buttons)
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
            source, destination, transport = self._event_route(event)
            values = (
                format_hungarian_time(event.recorded_at, "%Y-%m-%d %H:%M:%S.%f %Z"),
                f"{event.monotonic_seconds:.6f}",
                event.level,
                source,
                event.direction,
                destination,
                transport,
                event.message,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(
                    f"{source} → {destination}\n"
                    f"Kapcsolat: {transport}\n"
                    f"Irány/esemény: {event.direction}\n"
                    f"Tartalom: {event.message}"
                )
                self._table.setItem(row, column, item)
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

    @classmethod
    def _event_route(cls, event: DiagnosticEvent) -> tuple[str, str, str]:
        endpoint = cls.ENDPOINT_LABELS[event.category]
        transport = cls.TRANSPORT_LABELS[event.category]
        direction = event.direction.upper()
        if "RX" in direction:
            return endpoint, cls.APPLICATION_ENDPOINT, transport
        if "TX" in direction or direction == "SAFE":
            return cls.APPLICATION_ENDPOINT, endpoint, transport
        if event.category in DiagnosticLogger.HARDWARE_CATEGORIES:
            return endpoint, cls.APPLICATION_ENDPOINT, transport
        return endpoint, "Kezelői felület / diagnosztika", transport


class DataManagementBridge(QObject):
    completed = Signal(str)
    failed = Signal(str)


class NasSettingsBridge(QObject):
    tested = Signal(object)
    failed = Signal(str)
    synchronized = Signal(int)


class NasSettingsPage(QWidget):
    """Persistent NAS configuration using the current Windows credentials."""

    def __init__(
        self,
        synchronizer: BackgroundNasSynchronizer,
        settings: QSettings,
        data_root: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._synchronizer = synchronizer
        self._settings = settings
        self._data_root = data_root
        self._bridge = NasSettingsBridge(self)
        self._bridge.tested.connect(self._test_completed)
        self._bridge.failed.connect(self._operation_failed)
        self._bridge.synchronized.connect(self._sync_completed)
        self.setObjectName("nas_settings_page")
        layout = QVBoxLayout(self)

        explanation = QLabel(
            "A NAS hitelesítését a Windows kezeli. Előbb csatlakoztasd a "
            "megosztást a Windows Intézőben vagy a Hitelesítőadat-kezelőben; "
            "az alkalmazás felhasználónevet és jelszót nem tárol."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        form = QGridLayout()
        self.enabled = QCheckBox("Automatikus háttérszinkron engedélyezése")
        self.enabled.setObjectName("nas_enabled")
        self.enabled.setChecked(
            str(settings.value("nas/enabled", "false")).lower()
            in {"1", "true", "yes"}
        )
        self.target_path = QLineEdit(str(settings.value("nas/target_path", "")))
        self.target_path.setObjectName("nas_target_path")
        self.target_path.setReadOnly(True)
        self.target_path.setPlaceholderText("Nincs kiválasztott NAS célmappa")
        choose = QPushButton("Mappa kiválasztása…")
        choose.setObjectName("nas_choose_target")
        choose.clicked.connect(self._choose_target)
        form.addWidget(self.enabled, 0, 0, 1, 3)
        form.addWidget(input_field_label("NAS célmappa", self.target_path), 1, 0)
        form.addWidget(self.target_path, 1, 1)
        form.addWidget(choose, 1, 2)
        layout.addLayout(form)

        actions = QHBoxLayout()
        save = QPushButton("Beállítások mentése")
        save.setObjectName("nas_save")
        save.clicked.connect(self._save)
        self.test_button = QPushButton("Kapcsolat és írás tesztelése")
        self.test_button.setObjectName("nas_test_connection")
        self.test_button.clicked.connect(self._test)
        self.sync_button = QPushButton("Szinkronizálás most")
        self.sync_button.setObjectName("nas_sync_now")
        self.sync_button.clicked.connect(self._sync_now)
        actions.addWidget(save)
        actions.addWidget(self.test_button)
        actions.addWidget(self.sync_button)
        layout.addLayout(actions)

        self.status = QLabel()
        self.status.setObjectName("nas_status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        browser_box = QGroupBox("NAS fájlrendszer")
        browser_layout = QVBoxLayout(browser_box)
        self.file_model = QFileSystemModel(self)
        self.file_model.setReadOnly(True)
        self.file_view = QTreeView()
        self.file_view.setObjectName("nas_file_browser")
        self.file_view.setModel(self.file_model)
        self.file_view.setAlternatingRowColors(True)
        self.file_view.setSortingEnabled(True)
        self.file_view.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        for column in range(1, 4):
            self.file_view.resizeColumnToContents(column)
        browser_layout.addWidget(self.file_view)
        layout.addWidget(browser_box, 1)
        self._refresh_browser()
        self._refresh_status()

    def _selected_target(self) -> Path | None:
        value = self.target_path.text().strip()
        return Path(value) if value else None

    def _choose_target(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "NAS célmappa kiválasztása",
            self.target_path.text(),
            QFileDialog.Option.ShowDirsOnly,
        )
        if selected:
            self.target_path.setText(selected)
            self._refresh_browser()
            self.status.setText(
                "A kiválasztott cél még nincs tesztelve és elmentve."
            )

    def _save(self) -> None:
        target = self._selected_target()
        try:
            self._synchronizer.configure(
                enabled=self.enabled.isChecked(), target_root=target
            )
        except ValueError as error:
            self._operation_failed(str(error))
            return
        self._settings.setValue("nas/enabled", self.enabled.isChecked())
        self._settings.setValue("nas/target_path", "" if target is None else str(target))
        self._settings.sync()
        if self.enabled.isChecked():
            projects_root = self._data_root / "projects"
            if projects_root.is_dir():
                for source in projects_root.rglob("*"):
                    if source.is_file() and ".tmp" not in source.suffixes:
                        relative = source.relative_to(self._data_root)
                        self._synchronizer.enqueue(
                            source, Path(*relative.parts[1:])
                        )
        self._refresh_status("A NAS-beállítások elmentve.")

    def _test(self) -> None:
        target = self._selected_target()
        if target is None:
            self._operation_failed("Előbb válassz NAS célmappát.")
            return
        self.test_button.setEnabled(False)
        self.status.setText("NAS kapcsolat- és írhatósági teszt folyamatban…")

        def execute() -> None:
            try:
                result = test_nas_connection(target)
            except (ConnectionError, NotADirectoryError, PermissionError) as error:
                self._bridge.failed.emit(str(error))
            else:
                self._bridge.tested.emit(result)

        Thread(target=execute, name="eor-nas-connection-test", daemon=True).start()

    def _test_completed(self, payload: object) -> None:
        self.test_button.setEnabled(True)
        if not isinstance(payload, NasConnectionTestResult):
            self._operation_failed("Érvénytelen NAS-teszteredmény.")
            return
        self._refresh_browser()
        self._refresh_status(
            "Sikeres kapcsolat-, olvasási és írási teszt; "
            f"szabad hely: {payload.free_bytes / 1024**3:.1f} GiB; "
            f"látható elemek: {len(payload.visible_entries)}."
        )

    def _sync_now(self) -> None:
        if not self._synchronizer.enabled:
            self._operation_failed(
                "A kézi szinkron előtt engedélyezd és mentsd a NAS-beállítást."
            )
            return
        self.sync_button.setEnabled(False)
        self.status.setText("NAS-szinkronizálás folyamatban…")

        def execute() -> None:
            try:
                completed = self._synchronizer.sync_pending_once()
            except (OSError, RuntimeError) as error:
                self._bridge.failed.emit(str(error))
            else:
                self._bridge.synchronized.emit(completed)

        Thread(target=execute, name="eor-nas-sync-now", daemon=True).start()

    def _sync_completed(self, completed: int) -> None:
        self.sync_button.setEnabled(True)
        self._refresh_status(f"Szinkronizált fájlok: {completed}.")

    def _operation_failed(self, message: str) -> None:
        self.test_button.setEnabled(True)
        self.sync_button.setEnabled(True)
        self.status.setText(f"SIKERTELEN — {message}")
        self.status.setStyleSheet("color:#b00020;font-weight:700")

    def _refresh_browser(self) -> None:
        target = self._selected_target()
        if target is None:
            self.file_view.setRootIndex(QModelIndex())
            return
        self.file_view.setRootIndex(self.file_model.setRootPath(str(target)))

    def _refresh_status(self, prefix: str = "") -> None:
        state = "bekapcsolva" if self._synchronizer.enabled else "kikapcsolva"
        errors = self._synchronizer.pending_errors
        details = (
            f"NAS-szinkron: {state}; várakozó fájlok: "
            f"{self._synchronizer.pending_count}."
        )
        if errors:
            details += f" Utolsó hiba: {errors[-1]}"
        self.status.setText(f"{prefix} {details}".strip())
        self.status.setStyleSheet(
            "color:#b00020;font-weight:700" if errors else "color:#1b7f3a;font-weight:700"
        )


class DataManagementDialog(ResizableDialog):
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
        csv_button.clicked.connect(self._export_csv)
        export_layout.addWidget(self.decimal_comma, 0, 0)
        export_layout.addWidget(
            input_field_label("Oszlopelválasztó", self.delimiter), 0, 1
        )
        export_layout.addWidget(self.delimiter, 0, 2)
        export_layout.addWidget(csv_button, 1, 0, 1, 2)
        excel_notice = QLabel(
            "A projekt Excel-munkafüzetében a mérési szakasz saját munkalapja "
            "a szakasz lezárásakor automatikusan készül el vagy frissül, így "
            "futó szakaszból nem jön létre félkész Excel."
        )
        excel_notice.setWordWrap(True)
        export_layout.addWidget(excel_notice, 2, 0, 1, 3)
        layout.addWidget(export_box)

        nas_notice = QLabel(
            "A NAS célmappa, kapcsolatpróba, várólista és fájlrendszer a "
            "Beállítások → NAS és tárhely oldalon kezelhető."
        )
        nas_notice.setWordWrap(True)
        layout.addWidget(nas_notice)

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
        export_directory = Path(
            str(
                self._settings.value(
                    "export/last_directory", str(self._source_path.parent)
                )
            )
        )
        default = str(
            export_directory / f"{safe_filename(self._export_name)}_export.csv"
        )
        destination, _ = QFileDialog.getSaveFileName(
            self, "CSV export", default, "CSV fájl (*.csv)"
        )
        if not destination:
            return
        self._settings.setValue("export/last_directory", str(Path(destination).parent))
        self._settings.sync()
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

    def _operation_completed(self, message: str) -> None:
        QMessageBox.information(self, "Adatkezelés", message)

    def _operation_failed(self, message: str) -> None:
        QMessageBox.critical(self, "Adatkezelési hiba", message)


class MeasurementTableModel(QAbstractTableModel):
    """Lazy Qt adapter over one bounded page of the measurement table."""

    ROOT_INDEX = QModelIndex()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._table = MeasurementTable((), ())
        self._events: tuple[MeasurementEvent, ...] = ()
        self._event_points: list[dict[str, object]] = []
        self._row_indices: tuple[int, ...] = ()

    def set_page(
        self, table: MeasurementTable, row_indices: Iterable[int]
    ) -> None:
        self.beginResetModel()
        self._table = table
        self._row_indices = tuple(row_indices)
        self.endResetModel()

    def rowCount(
        self, parent: QModelIndex | QPersistentModelIndex = ROOT_INDEX
    ) -> int:
        return 0 if parent.isValid() else len(self._row_indices)

    def columnCount(
        self, parent: QModelIndex | QPersistentModelIndex = ROOT_INDEX
    ) -> int:
        return 0 if parent.isValid() else len(self._table.header)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> object | None:
        if (
            role != int(Qt.ItemDataRole.DisplayRole)
            or not index.isValid()
            or not 0 <= index.row() < len(self._row_indices)
            or not 0 <= index.column() < len(self._table.header)
        ):
            return None
        source_row = self._table.rows[self._row_indices[index.row()]]
        value = source_row[index.column()]
        if self._table.header[index.column()] == "recorded_at_utc":
            try:
                timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
                local_value = format_hungarian_time(
                    timestamp, "%Y-%m-%d %H:%M:%S.%f"
                )
                return f"{local_value[:-3]} {format_hungarian_time(timestamp, '%Z')}"
            except ValueError:
                return value
        return value

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> object | None:
        if role != int(Qt.ItemDataRole.DisplayRole):
            return None
        if orientation is Qt.Orientation.Horizontal:
            if 0 <= section < len(self._table.header):
                return self._table.header[section]
            return None
        return section + 1


class MeasurementHistoryView(QWidget):
    TABLE_PAGE_SIZE = 1000
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
        self._settings_toggle = QPushButton("Beállítások elrejtése ▲")
        self._settings_toggle.setObjectName("history_settings_toggle")
        self._settings_toggle.setCheckable(True)
        self._settings_toggle.setChecked(True)
        self._settings_toggle.setMinimumWidth(0)
        self._settings_toggle.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._settings_toggle.setAccessibleName(
            "Teljes mérési nézet beállításainak elrejtése vagy megjelenítése"
        )
        self._settings_toggle.setToolTip(
            "A teljes mérési diagram beállításainak elrejtése vagy megjelenítése"
        )
        layout.addWidget(self._settings_toggle)
        self._settings_panel = QWidget()
        self._settings_panel.setObjectName("history_settings_panel")
        settings_layout = QVBoxLayout(self._settings_panel)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        controls = QGridLayout()
        self._checks: dict[str, QCheckBox] = {}
        for index, (key, label, _color) in enumerate(self.SERIES):
            checkbox = QCheckBox(label)
            checkbox.setChecked(index < 4)
            checkbox.toggled.connect(self._refresh_plot)
            self._checks[key] = checkbox
            row, column = divmod(index, 4)
            controls.addWidget(checkbox, row, column)
        settings_layout.addLayout(controls)

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
        range_grid.addWidget(
            input_field_label("Mérési fázis", self._stage_filter), 0, 0
        )
        range_grid.addWidget(self._stage_filter, 0, 1)
        range_grid.addWidget(
            input_field_label("Időtartomány", self._time_range), 0, 2
        )
        range_grid.addWidget(self._time_range, 0, 3)
        range_grid.addWidget(refresh, 0, 4, 1, 2)
        range_grid.addWidget(
            input_field_label("Egyéni időtartam", self._custom_minutes), 1, 0
        )
        range_grid.addWidget(self._custom_minutes, 1, 1)
        range_grid.addWidget(self._auto_y, 1, 2, 1, 2)
        range_grid.addWidget(input_field_label("Y minimum", self._y_min), 2, 0)
        range_grid.addWidget(self._y_min, 2, 1)
        range_grid.addWidget(input_field_label("Y maximum", self._y_max), 2, 2)
        range_grid.addWidget(self._y_max, 2, 3)
        range_grid.setColumnStretch(1, 1)
        range_grid.setColumnStretch(3, 1)
        settings_layout.addLayout(range_grid)
        layout.addWidget(self._settings_panel)
        self._settings_toggle.toggled.connect(self._toggle_settings)

        self._content_tabs = QTabWidget()
        self._content_tabs.setObjectName("measurement_history_content_tabs")
        graph_page = QWidget()
        graph_layout = QVBoxLayout(graph_page)
        graph_layout.setContentsMargins(0, 0, 0, 0)
        self._plot = pg.PlotWidget(title="Teljes rögzített mérés")
        self._plot.setLabel("left", "Érték")
        self._plot.setLabel("bottom", "Eltelt idő", units="s")
        self._plot.showGrid(x=True, y=True, alpha=0.25)
        self._plot.setMouseEnabled(x=True, y=True)
        graph_layout.addWidget(self._plot, stretch=1)
        self._stage_plot = pg.PlotWidget(title="Mérési fázisok idővonala")
        self._stage_plot.setObjectName("measurement_stage_timeline")
        self._stage_plot.setMaximumHeight(130)
        self._stage_plot.setMouseEnabled(x=True, y=False)
        self._stage_plot.setYRange(0.0, 1.0, padding=0.0)
        self._stage_plot.hideAxis("left")
        self._stage_plot.setLabel("bottom", "Eltelt idő", units="s")
        self._stage_plot.setXLink(self._plot)
        graph_layout.addWidget(self._stage_plot)
        self._content_tabs.addTab(graph_page, "Grafikon")

        table_page = QWidget()
        table_layout = QVBoxLayout(table_page)
        table_layout.setContentsMargins(0, 0, 0, 0)
        self._table_model = MeasurementTableModel(self)
        self._table_view = QTableView()
        self._table_view.setObjectName("measurement_results_table")
        self._table_view.setModel(self._table_model)
        self._table_view.setAlternatingRowColors(True)
        self._table_view.setSortingEnabled(False)
        self._table_view.setWordWrap(False)
        self._table_view.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._table_view.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._table_view.verticalHeader().setDefaultSectionSize(22)
        table_layout.addWidget(self._table_view, 1)
        paging = QHBoxLayout()
        self._previous_page = QPushButton("← Előző oldal")
        self._next_page = QPushButton("Következő oldal →")
        self._page_status = QLabel()
        self._previous_page.clicked.connect(self._show_previous_table_page)
        self._next_page.clicked.connect(self._show_next_table_page)
        paging.addWidget(self._previous_page)
        paging.addWidget(self._page_status)
        paging.addStretch()
        paging.addWidget(self._next_page)
        table_layout.addLayout(paging)
        self._content_tabs.addTab(table_page, "Táblázat")
        layout.addWidget(self._content_tabs, stretch=1)
        self._status = QLabel()
        layout.addWidget(self._status)
        self._table = MeasurementTable((), ())
        self._filtered_row_indices: tuple[int, ...] = ()
        self._table_page_index = 0
        self._load()

    def _toggle_settings(self, expanded: bool) -> None:
        self._settings_panel.setVisible(expanded)
        self._settings_toggle.setText(
            "Beállítások elrejtése ▲"
            if expanded
            else "Beállítások megjelenítése ▼"
        )

    def set_sources(
        self, source_paths: Iterable[Path], project_name: str = ""
    ) -> None:
        self._source_paths = tuple(source_paths)
        self._project_name = project_name
        self._load()

    def _load(self) -> None:
        try:
            self._table = read_measurement_tables(self._source_paths)
            self._events = read_measurement_events(self._source_paths)
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
            self._filtered_row_indices = ()
            self._table_page_index = 0
            self._refresh_table_page()
            self._status.setText(
                f"Nincs rögzített minta — {len(self._source_paths)} fázisfájl"
            )
            return
        times = self._elapsed_times()
        selected_stage = self._stage_filter.currentData()
        stage = selected_stage if isinstance(selected_stage, str) else None
        stage_index = self._table.header.index("active_stage")
        stage_indices = [
            index
            for index, row in enumerate(self._table.rows)
            if stage is None or row[stage_index] == stage
        ]
        seconds = self._time_range.currentData()
        if seconds == "custom":
            seconds = self._custom_minutes.value() * 60.0
        minimum_time = times[-1] - float(seconds) if isinstance(seconds, float) else times[0]
        selected_indices = [
            index for index in stage_indices if times[index] >= minimum_time
        ]
        self._filtered_row_indices = tuple(selected_indices)
        self._table_page_index = 0
        self._refresh_table_page()
        self._draw_stage_timeline(times, stage)
        stage_label = stage or "Összes mérési fázis"
        if not selected_indices:
            self._status.setText(
                f"{stage_label}: a kiválasztott időtartományban nincs minta — "
                f"{len(self._source_paths)} fázisfájl"
            )
            return
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
        self._draw_events(stage, minimum_time, times[-1])
        duration = (
            times[selected_indices[-1]] - times[selected_indices[0]]
            if len(selected_indices) > 1
            else 0.0
        )
        self._status.setText(
            f"{stage_label}: {len(stage_indices)} rögzített minta, "
            f"{len(selected_indices)} megjelenítve, "
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

    def add_event(self, event: MeasurementEvent) -> None:
        """Refresh both-history markers from the same durable event identity."""
        events = {item.event_id: item for item in self._events}
        events[event.event_id] = event
        self._events = tuple(
            sorted(events.values(), key=lambda item: item.recorded_at_utc)
        )
        self._refresh_plot()

    def _draw_events(
        self, stage: str | None, minimum_time: float, maximum_time: float
    ) -> None:
        colors = {
            "critical": "#d50000",
            "warning": "#f9a825",
            "operator": "#1565c0",
            "info": "#1565c0",
        }
        self._event_points = []
        for event in self._events:
            if stage is not None and event.active_stage != stage:
                continue
            if not minimum_time <= event.elapsed_seconds <= maximum_time:
                continue
            y_value = next(
                (
                    value
                    for value in (
                        event.injection_pressure_bar,
                        event.line_pressure_bar,
                        event.jacket_pressure_bar,
                        event.differential_pressure_bar,
                    )
                    if value is not None and isfinite(value)
                ),
                0.0,
            )
            tooltip = (
                f"{event.severity.upper()} | {event.event_id}\n"
                f"{event.recorded_at_utc}\n{event.error_code}: {event.description}\n"
                f"Fázis: {event.active_stage}; hardver: {event.affected_hardware}"
            )
            self._event_points.append(
                {
                    "pos": (event.elapsed_seconds, y_value),
                    "brush": pg.mkBrush(colors.get(event.severity, "#1565c0")),
                    "data": tooltip,
                }
            )
        if self._event_points:
            scatter = pg.ScatterPlotItem(
                size=12,
                symbol="d",
                hoverable=True,
                hoverSize=16,
                pen=pg.mkPen("#ffffff", width=1),
            )
            scatter.setData(self._event_points)
            self._plot.addItem(scatter)

    def _refresh_table_page(self) -> None:
        total_rows = len(self._filtered_row_indices)
        page_count = max(1, (total_rows + self.TABLE_PAGE_SIZE - 1) // self.TABLE_PAGE_SIZE)
        self._table_page_index = min(self._table_page_index, page_count - 1)
        start = self._table_page_index * self.TABLE_PAGE_SIZE
        end = min(total_rows, start + self.TABLE_PAGE_SIZE)
        page_indices = self._filtered_row_indices[start:end]
        self._table_model.set_page(self._table, page_indices)
        self._page_status.setText(
            f"{self._table_page_index + 1}/{page_count}. oldal — "
            f"{total_rows} sor; oldalanként legfeljebb {self.TABLE_PAGE_SIZE}"
        )
        self._previous_page.setEnabled(self._table_page_index > 0)
        self._next_page.setEnabled(self._table_page_index + 1 < page_count)
        if self._table.header:
            self._table_view.resizeColumnToContents(0)

    def _show_previous_table_page(self) -> None:
        if self._table_page_index <= 0:
            return
        self._table_page_index -= 1
        self._refresh_table_page()

    def _show_next_table_page(self) -> None:
        page_count = max(
            1,
            (
                len(self._filtered_row_indices)
                + self.TABLE_PAGE_SIZE
                - 1
            )
            // self.TABLE_PAGE_SIZE,
        )
        if self._table_page_index + 1 >= page_count:
            return
        self._table_page_index += 1
        self._refresh_table_page()

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


class CalibrationSettingsDialog(ResizableDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Kalibráció és biztonsági határértékek")
        self.resize(620, 520)
        layout = QVBoxLayout(self)
        self._content_scroll = QScrollArea()
        self._content_scroll.setObjectName("calibration_settings_scroll")
        self._content_scroll.setWidgetResizable(True)
        self._content_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._content_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        content = QWidget()
        content.setMinimumWidth(0)
        content_layout = QVBoxLayout(content)
        help_text = QLabel(
            "A kalibráció a mért feszültséget alakítja fizikai nyomássá. "
            "A biztonsági határértékek túllépése safe-state állapotot vált ki."
        )
        help_text.setWordWrap(True)
        help_text.setStyleSheet("padding:8px;color:#66788a")
        content_layout.addWidget(help_text)
        tabs = QTabWidget()
        tabs.setObjectName("calibration_settings_tabs")
        tabs.setMinimumHeight(500)
        content_layout.addWidget(tabs, 1)
        self._content_scroll.setWidget(content)
        layout.addWidget(self._content_scroll, 1)

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
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            form.setVerticalSpacing(8)
            for label, field in fields:
                form.addRow(input_field_label(label, field), field)
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
        safety_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        safety_form.setVerticalSpacing(8)
        self.max_jacket = self._value_spinbox(400.0, 0.1, 1000.0, " bar")
        self.max_injection = self._value_spinbox(350.0, 0.1, 1000.0, " bar")
        self.max_line = self._value_spinbox(400.0, 0.1, 1000.0, " bar")
        self.max_delta = self._value_spinbox(50.0, 0.1, 1000.0, " bar")
        self.minimum_margin = self._value_spinbox(20.0, 0.1, 1000.0, " bar")
        self.max_overshoot = self._value_spinbox(5.0, 0.1, 1000.0, " bar")
        for label, field in (
            ("Köpenypumpa maximális nyomása", self.max_jacket),
            ("Besajtolópumpa maximális nyomása", self.max_injection),
            ("Vonali nyomás maximuma", self.max_line),
            ("Differenciálnyomás maximuma", self.max_delta),
            ("Indítási köpenynyomás minimális többlete", self.minimum_margin),
            ("Célérték maximális túllövése", self.max_overshoot),
        ):
            safety_form.addRow(input_field_label(label, field), field)
        safety_layout.addWidget(safety_box)
        safety_note = QLabel(
            "A minimális köpenynyomás-többlet kizárólag a besajtolópumpa "
            "indítási engedélyfeltétele. A köpenypumpa a célérték elérése után "
            "fix CONST PRESS nyomástartásban marad; mérés közben a különbséget "
            "a program nem szabályozza és nem reteszeli."
        )
        safety_note.setWordWrap(True)
        safety_note.setStyleSheet("padding:8px;color:#8a5a00;font-weight:600")
        safety_layout.addWidget(safety_note)
        safety_layout.addStretch()
        tabs.addTab(safety_page, "Biztonsági határértékek")

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Save).setText(
            "Mentés és alkalmazás"
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Mégse")
        self._buttons.accepted.connect(self._accept_if_valid)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

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
        spinbox.setMinimumHeight(38)
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


class MeasurementOverviewDialog(ResizableDialog):
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


class MeasurementPumpStartupDialog(ResizableDialog):
    """Collect and explicitly confirm the physical pump startup targets."""

    def __init__(
        self,
        defaults: MeasurementPumpPlan,
        *,
        maximum_jacket_pressure_bar: float,
        maximum_injection_pressure_bar: float,
        minimum_jacket_margin_bar: float,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._minimum_jacket_margin_bar = minimum_jacket_margin_bar
        self.setWindowTitle("Pumpák előkészítése")
        self.resize(620, 390)
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "A köpenypumpa a megadott térfogatárammal építi fel a nyomást. "
            f"Ha a legalább {minimum_jacket_margin_bar:.3f} bar "
            "köpenynyomás-többlet a megadott ideig stabil, "
            "a besajtolópumpa is elindul, és a két pumpa együtt halad a "
            "kezdőértékek felé. A köpeny a célján STOP után nyomástartásra vált. "
            "Ha a különbség a minimum alá esik, a BES leáll, majd megfelelő "
            "nyomáselőnynél automatikusan újraindul. "
            "A két saját pumpahatárt a program RUN előtt a pumpákba írja. A "
            "mérési ciklus csak mindkét kezdőnyomás elérése után indul. A "
            "célnyomás elérésének nincs időkorlátja."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        form = QFormLayout()
        self.jacket_target_pressure = QDoubleSpinBox()
        self.jacket_target_pressure.setObjectName("startup_jacket_target_pressure")
        self.jacket_target_pressure.setRange(0.0, maximum_jacket_pressure_bar)
        self.jacket_target_pressure.setDecimals(3)
        self.jacket_target_pressure.setSuffix(" bar")
        self.jacket_target_pressure.setValue(defaults.jacket_target_pressure_bar)

        self.jacket_buildup_flow = QDoubleSpinBox()
        self.jacket_buildup_flow.setObjectName("startup_jacket_buildup_flow")
        self.jacket_buildup_flow.setRange(0.0, 600000.0)
        self.jacket_buildup_flow.setDecimals(3)
        self.jacket_buildup_flow.setSuffix(" ml/h")
        self.jacket_buildup_flow.setValue(
            defaults.jacket_buildup_flow_ml_per_hour
        )

        self.injection_start_pressure = QDoubleSpinBox()
        self.injection_start_pressure.setObjectName(
            "startup_injection_start_pressure"
        )
        self.injection_start_pressure.setRange(0.0, maximum_injection_pressure_bar)
        self.injection_start_pressure.setDecimals(3)
        self.injection_start_pressure.setSuffix(" bar")
        self.injection_start_pressure.setValue(
            defaults.injection_start_pressure_bar
        )

        self.injection_flow = QDoubleSpinBox()
        self.injection_flow.setObjectName("startup_injection_flow")
        self.injection_flow.setRange(0.0, 600000.0)
        self.injection_flow.setDecimals(3)
        self.injection_flow.setSuffix(" ml/h")
        self.injection_flow.setValue(defaults.injection_startup_flow_ml_per_hour)

        self.jacket_pressure_limit = QDoubleSpinBox()
        self.jacket_pressure_limit.setObjectName("startup_jacket_pressure_limit")
        self.jacket_pressure_limit.setRange(0.01, maximum_jacket_pressure_bar)
        self.jacket_pressure_limit.setDecimals(3)
        self.jacket_pressure_limit.setSuffix(" bar")
        self.jacket_pressure_limit.setValue(
            defaults.jacket_pressure_limit_bar or maximum_jacket_pressure_bar
        )

        self.injection_pressure_limit = QDoubleSpinBox()
        self.injection_pressure_limit.setObjectName(
            "startup_injection_pressure_limit"
        )
        self.injection_pressure_limit.setRange(
            0.01, maximum_injection_pressure_bar
        )
        self.injection_pressure_limit.setDecimals(3)
        self.injection_pressure_limit.setSuffix(" bar")
        self.injection_pressure_limit.setValue(
            defaults.injection_pressure_limit_bar
            or maximum_injection_pressure_bar
        )

        self.margin_stability = QDoubleSpinBox()
        self.margin_stability.setObjectName("startup_margin_stability_seconds")
        self.margin_stability.setRange(0.0, 60.0)
        self.margin_stability.setDecimals(1)
        self.margin_stability.setSuffix(" s")
        self.margin_stability.setValue(defaults.margin_stability_seconds)

        form.addRow("Köpeny elérendő kezdőnyomása", self.jacket_target_pressure)
        form.addRow("Köpeny nyomásfelépítési árama", self.jacket_buildup_flow)
        form.addRow(
            "Besajtoló elérendő kezdőnyomása", self.injection_start_pressure
        )
        form.addRow("BES előkészítési térfogatáram", self.injection_flow)
        form.addRow("Köpenypumpa saját nyomáshatára", self.jacket_pressure_limit)
        form.addRow(
            "Besajtolópumpa saját nyomáshatára", self.injection_pressure_limit
        )
        form.addRow(
            f"{minimum_jacket_margin_bar:.3f} bar többlet stabilitási ideje",
            self.margin_stability,
        )
        layout.addLayout(form)

        warning = QLabel(
            "A megadott értékek valódi pumpákra kerülnek. Nullás vagy hiányzó "
            "értékkel a mérés nem indítható."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color:#b00020;font-weight:700")
        layout.addWidget(warning)
        self.margin_status = QLabel()
        self.margin_status.setObjectName("startup_pressure_margin_status")
        self.margin_status.setWordWrap(True)
        layout.addWidget(self.margin_status)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.start_button = buttons.addButton(
            "Előkészítés indítása", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.start_button.setObjectName("startup_accept")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        for field in (
            self.jacket_target_pressure,
            self.jacket_buildup_flow,
            self.injection_start_pressure,
            self.injection_flow,
            self.jacket_pressure_limit,
            self.injection_pressure_limit,
            self.margin_stability,
        ):
            field.valueChanged.connect(self._refresh_start_enabled)
        self._refresh_start_enabled()

    def plan(self) -> MeasurementPumpPlan:
        return MeasurementPumpPlan(
            jacket_target_pressure_bar=self.jacket_target_pressure.value(),
            jacket_buildup_flow_ml_per_hour=self.jacket_buildup_flow.value(),
            injection_start_pressure_bar=self.injection_start_pressure.value(),
            injection_startup_flow_ml_per_hour=self.injection_flow.value(),
            jacket_pressure_limit_bar=self.jacket_pressure_limit.value(),
            injection_pressure_limit_bar=self.injection_pressure_limit.value(),
            margin_stability_seconds=self.margin_stability.value(),
        )

    def confirmation_text(self) -> str:
        # Internal service token. The only operator-facing confirmation is the
        # pressure-reached MessageBox displayed after preparation.
        return PumpControlService.START_MEASUREMENT_CONFIRMATION

    def _refresh_start_enabled(self) -> None:
        plan = self.plan()
        margin = (
            plan.jacket_target_pressure_bar
            - plan.injection_start_pressure_bar
        )
        margin_ok = margin >= self._minimum_jacket_margin_bar
        self.margin_status.setText(
            f"Tervezett köpenynyomás-többlet: {margin:.3f} bar; "
            f"szükséges: legalább {self._minimum_jacket_margin_bar:.3f} bar."
        )
        self.margin_status.setStyleSheet(
            "color:#1b7f3a;font-weight:700"
            if margin_ok
            else "color:#b00020;font-weight:700"
        )
        self.start_button.setEnabled(
            plan.jacket_target_pressure_bar > 0.0
            and plan.jacket_buildup_flow_ml_per_hour > 0.0
            and plan.injection_start_pressure_bar > 0.0
            and plan.injection_target_flow_ml_per_hour > 0.0
            and plan.jacket_pressure_limit_bar is not None
            and plan.jacket_target_pressure_bar <= plan.jacket_pressure_limit_bar
            and plan.injection_pressure_limit_bar is not None
            and plan.injection_start_pressure_bar
            <= plan.injection_pressure_limit_bar
            and margin_ok
        )


class PumpPreparationProgressDialog(ResizableDialog):
    """Show cache-only preparation state and allow explicit cancellation."""

    def __init__(self, cancel_event: Event, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cancel_event = cancel_event
        self.setWindowTitle("Pumpanyomás felépítése")
        self.resize(700, 440)
        layout = QVBoxLayout(self)
        message = QLabel(
            "Nyomásfelépítés folyamatban. A pumpák a megadott célnyomás "
            "eléréséig működnek."
        )
        message.setWordWrap(True)
        message.setStyleSheet("font-weight:700")
        layout.addWidget(message)
        form = QFormLayout()
        self._values: dict[str, QLabel] = {}
        for key, title in (
            ("phase", "Aktuális fázis"),
            ("jacket_pressure", "Köpeny nyomása / célja"),
            ("injection_pressure", "BES nyomása / célja"),
            ("margin", "Nyomáskülönbség / minimum"),
            ("jacket_state", "Köpenypumpa állapota"),
            ("injection_state", "BES pumpa állapota"),
            ("jacket_quality", "Köpeny telemetry"),
            ("injection_quality", "BES telemetry"),
            ("pending", "Függőben lévő parancs"),
        ):
            value = QLabel("—")
            value.setObjectName(f"preparation_{key}")
            value.setWordWrap(True)
            form.addRow(title, value)
            self._values[key] = value
        layout.addLayout(form)
        layout.addStretch()
        self._cancel_button = QPushButton("Előkészítés megszakítása")
        self._cancel_button.setObjectName("cancel_pump_preparation")
        self._cancel_button.clicked.connect(self._cancel)
        layout.addWidget(self._cancel_button)

    @staticmethod
    def _quality_text(quality: DataQuality, age: float | None) -> str:
        age_text = "ismeretlen kor" if age is None else f"{age:.3f} s"
        return f"{quality.value.upper()}, kor: {age_text}"

    def update_progress(self, progress: PumpPreparationProgress) -> None:
        self._values["phase"].setText(progress.phase)
        self._values["jacket_pressure"].setText(
            f"{progress.jacket_pressure_bar:.3f} / "
            f"{progress.jacket_target_pressure_bar:.3f} bar"
        )
        self._values["injection_pressure"].setText(
            f"{progress.injection_pressure_bar:.3f} / "
            f"{progress.injection_target_pressure_bar:.3f} bar"
        )
        self._values["margin"].setText(
            f"{progress.pressure_margin_bar:.3f} / "
            f"{progress.minimum_margin_bar:.3f} bar"
        )
        self._values["jacket_state"].setText(progress.jacket_state)
        self._values["injection_state"].setText(progress.injection_state)
        self._values["jacket_quality"].setText(
            self._quality_text(progress.jacket_quality, progress.jacket_age_seconds)
        )
        self._values["injection_quality"].setText(
            self._quality_text(
                progress.injection_quality,
                progress.injection_age_seconds,
            )
        )
        self._values["pending"].setText(progress.pending_command or "Nincs")

    def _cancel(self) -> None:
        self._cancel_event.set()
        self._cancel_button.setEnabled(False)
        self._cancel_button.setText("Megszakítás folyamatban…")

    def reject(self) -> None:
        self._cancel()


class PreflightDialog(ResizableDialog):
    """Operator-facing, itemized gate shown before every measurement start."""

    def __init__(
        self,
        report: PreflightReport,
        parent: QWidget | None = None,
        *,
        accept_text: str = "Tovább az előkészítéshez",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Mérés előtti kötelező ellenőrzés")
        self.resize(900, 480)
        layout = QVBoxLayout(self)
        summary = QLabel(
            "Az indítás csak akkor engedélyezett, ha nincs hibás ellenőrzési tétel."
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)

        table = QTableWidget(len(report.items), 4, self)
        table.setHorizontalHeaderLabels(("Tétel", "Állapot", "Részletek", "Teendő"))
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setWordWrap(True)
        table.verticalHeader().setVisible(False)
        for row, item in enumerate(report.items):
            values = (item.label, item.status.value, item.detail, item.remediation or "—")
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column == 1:
                    color = {
                        PreflightStatus.PASSED: "#1b7f3a",
                        PreflightStatus.WARNING: "#9a6700",
                        PreflightStatus.FAILED: "#b00020",
                    }[item.status]
                    cell.setForeground(QColor(color))
                table.setItem(row, column, cell)
        table.resizeColumnsToContents()
        table.resizeRowsToContents()
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table, stretch=1)

        self._warning_confirmation = QCheckBox(
            "A figyelmeztetéseket elolvastam, és a mérés indítását jóváhagyom."
        )
        self._warning_confirmation.setAccessibleName(
            "Előellenőrzési figyelmeztetések kezelői jóváhagyása"
        )
        self._warning_confirmation.setVisible(report.can_start and report.has_warnings)
        layout.addWidget(self._warning_confirmation)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._start_button = buttons.addButton(
            accept_text,
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self._start_button.setEnabled(report.can_start and not report.has_warnings)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._warning_confirmation.toggled.connect(
            lambda checked: self._start_button.setEnabled(report.can_start and checked)
        )
        if not report.can_start:
            self._start_button.setVisible(False)
            summary.setText(
                "A mérés nem indítható. Javítsa a pirossal jelölt tételeket, majd "
                "futtassa újra az előellenőrzést."
            )
        layout.addWidget(buttons)


class SimulationSettingsPage(QWidget):
    """Service panel for deterministic simulation and fault injection."""

    def __init__(
        self,
        *,
        jacket: SimulatedPump,
        injection: SimulatedPump,
        daq: SimulatedDataAcquisition,
        valve: SimulatedValveActuator,
        log_event: Callable[[str], None],
    ) -> None:
        super().__init__()
        self._pumps = {"jacket": jacket, "injection": injection}
        self._daq = daq
        self._valve = valve
        self._log_event = log_event
        layout = QVBoxLayout(self)
        notice = QLabel(
            "SZIMULÁCIÓ — az itt injektált hibák nem érnek el fizikai eszközt. "
            "Futó mérés közben a normál biztonsági logika reagál rájuk."
        )
        notice.setWordWrap(True)
        notice.setStyleSheet(
            "background:#fff4cf;color:#513900;padding:8px;font-weight:700"
        )
        layout.addWidget(notice)

        self._content_scroll = QScrollArea()
        self._content_scroll.setObjectName("simulation_settings_scroll")
        self._content_scroll.setWidgetResizable(True)
        self._content_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._content_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        content = QWidget()
        content.setMinimumWidth(0)
        content.setMinimumHeight(610)
        content_layout = QVBoxLayout(content)

        model = QGroupBox("Fizikai modell")
        form = QFormLayout(model)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setVerticalSpacing(8)
        self.jacket_ramp = self._number(
            jacket.pressure_ramp_bar_per_second, 0.0, 100.0, " bar/s"
        )
        self.injection_ramp = self._number(
            injection.pressure_ramp_bar_per_second, 0.0, 100.0, " bar/s"
        )
        self.response_delay = self._number(
            jacket.response_delay.maximum_seconds * 1000.0,
            0.0,
            10000.0,
            " ms",
        )
        self.jacket_limit = self._number(
            jacket.hardware_pressure_limit_bar, 0.1, 10000.0, " bar"
        )
        self.injection_limit = self._number(
            injection.hardware_pressure_limit_bar, 0.1, 10000.0, " bar"
        )
        form.addRow("Köpeny nyomásrámpa", self.jacket_ramp)
        form.addRow("Besajtoló nyomásrámpa", self.injection_ramp)
        form.addRow("Pumpaválasz késleltetése", self.response_delay)
        form.addRow("Köpeny saját nyomáshatára", self.jacket_limit)
        form.addRow("Besajtoló saját nyomáshatára", self.injection_limit)
        self.apply_model = QPushButton("Szimulációs modell alkalmazása")
        self.apply_model.clicked.connect(self._apply_model)
        form.addRow(self.apply_model)
        content_layout.addWidget(model)

        faults = QGroupBox("Hibainjektálás")
        fault_form = QFormLayout(faults)
        fault_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        fault_form.setVerticalSpacing(8)
        self.device = QComboBox()
        self.device.addItem("Köpenypumpa", "jacket")
        self.device.addItem("Besajtolópumpa", "injection")
        self.device.addItem("NI vonali nyomás", "ni_line")
        self.device.addItem("NI differenciálnyomás", "ni_differential")
        self.device.addItem("Szelep", "valve")
        self.fault = QComboBox()
        self.device.setMinimumHeight(38)
        self.fault.setMinimumHeight(38)
        self.device.currentIndexChanged.connect(self._refresh_faults)
        self.inject_fault = QPushButton("Hiba injektálása")
        self.inject_fault.setStyleSheet(
            "background:#b00020;color:white;font-weight:700;padding:8px"
        )
        self.inject_fault.clicked.connect(self._inject_fault)
        self.clear_faults = QPushButton("Minden szimulált hiba törlése")
        self.clear_faults.clicked.connect(self._clear_faults)
        fault_form.addRow("Eszköz", self.device)
        fault_form.addRow("Hiba", self.fault)
        fault_form.addRow(self.inject_fault)
        fault_form.addRow(self.clear_faults)
        self.status = QLabel("Nincs injektált hiba.")
        self.status.setWordWrap(True)
        fault_form.addRow("Állapot", self.status)
        content_layout.addWidget(faults)
        content_layout.addStretch(1)
        self._content_scroll.setWidget(content)
        layout.addWidget(self._content_scroll, 1)
        self._refresh_faults()

    @staticmethod
    def _number(
        value: float, minimum: float, maximum: float, suffix: str
    ) -> QDoubleSpinBox:
        field = QDoubleSpinBox()
        field.setRange(minimum, maximum)
        field.setDecimals(3)
        field.setValue(value)
        field.setSuffix(suffix)
        field.setMinimumHeight(38)
        return field

    def _apply_model(self) -> None:
        jacket = self._pumps["jacket"]
        injection = self._pumps["injection"]
        jacket.pressure_ramp_bar_per_second = self.jacket_ramp.value()
        injection.pressure_ramp_bar_per_second = self.injection_ramp.value()
        delay = SimulationDelay(
            self.response_delay.value() / 1000.0,
            self.response_delay.value() / 1000.0,
        )
        jacket.response_delay = delay
        injection.response_delay = delay
        jacket.hardware_pressure_limit_bar = self.jacket_limit.value()
        injection.hardware_pressure_limit_bar = self.injection_limit.value()
        self.status.setText("A szimulációs modell frissítve.")
        self._log_event("simulation model updated")

    def _refresh_faults(self) -> None:
        device = str(self.device.currentData())
        self.fault.clear()
        if device in self._pumps:
            for fault, label in (
                (SimulatedPumpFault.PRESSURE_STALE.value, "Nyomásadat fagyása / STALE"),
                (SimulatedPumpFault.DISCONNECT.value, "Kapcsolatvesztés"),
                (SimulatedPumpFault.EMPTY_CYLINDER.value, "Üres cilinder"),
                (SimulatedPumpFault.MOTOR_FAILURE.value, "Motorhiba"),
                (SimulatedPumpFault.OVERPRESSURE.value, "Saját túlnyomásvédelem"),
            ):
                self.fault.addItem(label, fault)
        elif device.startswith("ni_"):
            self.fault.addItem("Egyszeri feszültségtüske", "spike")
            self.fault.addItem("Befagyott érzékelőérték", "freeze")
            self.fault.addItem("NI kapcsolatvesztés", "disconnect")
        else:
            self.fault.addItem("Szelep beragadása", "stuck")
            self.fault.addItem("Fordított működési irány", "reverse")

    def _inject_fault(self) -> None:
        device = str(self.device.currentData())
        fault = str(self.fault.currentData())
        if device in self._pumps:
            self._pumps[device].inject_fault(SimulatedPumpFault(fault))
        elif device.startswith("ni_"):
            channel = (
                "line_pressure"
                if device == "ni_line"
                else "differential_pressure"
            )
            if fault == "spike":
                self._daq.inject_spike(channel, 10.0)
            elif fault == "freeze":
                self._daq.freeze(channel)
            else:
                self._daq.disconnect()
        elif fault == "stuck":
            self._valve.stuck = True
        else:
            self._valve.reverse_direction = True
        message = f"fault injected: device={device}; fault={fault}"
        self.status.setText(message)
        self._log_event(message)

    def _clear_faults(self) -> None:
        for pump in self._pumps.values():
            pump.clear_faults()
        self._daq.reconnect()
        self._daq.unfreeze("line_pressure")
        self._daq.unfreeze("differential_pressure")
        self._valve.stuck = False
        self._valve.reverse_direction = False
        self.status.setText(
            "A szimulált hibák törölve. Reteszelt alkalmazáshibánál zárja be "
            "a dashboard riasztását a friss biztonsági ellenőrzéshez."
        )
        self._log_event("all simulated faults cleared")


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
        obsolete_validation_keys = (
            "hardware/safe_output_validated",
            "pid/profile_validated",
        )
        if any(self._user_settings.contains(key) for key in obsolete_validation_keys):
            for key in obsolete_validation_keys:
                self._user_settings.remove(key)
            self._user_settings.sync()
        self._developer_mode = str(
            self._user_settings.value("developer/enabled", "false")
        ).lower() in {"1", "true", "yes", "on"}
        self._devices = devices
        self._control_loop = control_loop
        self._valve = valve
        self._simulation_jacket = (
            devices.jacket_pump
            if isinstance(devices.jacket_pump, SimulatedPump)
            else None
        )
        self._simulation_injection = (
            devices.injection_pump
            if isinstance(devices.injection_pump, SimulatedPump)
            else None
        )
        self._simulation_daq = (
            devices.data_acquisition
            if isinstance(devices.data_acquisition, SimulatedDataAcquisition)
            else None
        )
        self._projects = projects
        self._data_directory = data_directory
        self._measurement_writer = measurement_writer
        self._measurement_writer.set_phase_completed_callback(
            self._queue_completed_stage_export
        )
        self._nas_sync = nas_sync
        self._pending_stage_exports: Queue[tuple[Path, str]] = Queue()
        self._stage_export_active = False
        self._stage_export_thread: Thread | None = None
        self._run_mode = RunMode.SIMULATION
        self._preferred_run_mode = self._stored_run_mode()
        self._startup_mode_restore_started = False
        self._pump_control: PumpControlService | None = None
        self._pending_measurement_pump_plan: MeasurementPumpPlan | None = None
        self._pump_preparation_dialog: PumpPreparationProgressDialog | None = None
        self._pump_preparation_cancel_event: Event | None = None
        self._applied_measurement_flow_ml_per_hour: float | None = None
        self._active_hardware_configuration: HardwareConfiguration | None = None
        self._active_pump_telemetry_intervals = next(
            (
                pump.polling_intervals
                for pump in (devices.jacket_pump, devices.injection_pump)
                if isinstance(pump, PollingPump)
            ),
            None,
        )
        self._hardware_connection_result: ConnectionTestResult | None = None
        self._hardware_daq: NidaqmxDataAcquisition | None = None
        self._hardware_actuator: AnalogValveActuator | None = None
        self._measurement_time_origin: float | None = None
        self._last_cycle_result: ControlCycleResult | None = None
        self._last_hardware_status_record: MeasurementRecord | None = None
        self._diagnostic_measurement_id: str | None = None
        self._diagnostic_section_id: int | None = None
        self._hardware_status_active = False
        self._hardware_status_generation = 0
        self._preflight_active = False
        self._preflight_starts_measurement = False
        self._shutdown_started = False
        self._critical_hardware_recovery_active = False
        self._overview_dialog: MeasurementOverviewDialog | None = None
        self._active_alarm_text = "Nincs aktív riasztás"
        self._active_alarm_reason: str | None = None
        self._current_mode_message = ""
        self._last_notification_key: str | None = None
        self._tray_icon = QSystemTrayIcon(application_icon(), self)
        self._tray_icon.setToolTip("AFKI EOR mérőrendszer")
        self._tray_available = QSystemTrayIcon.isSystemTrayAvailable()
        self._diagnostics = DiagnosticLogger(
            data_directory / "logs" / "application.html",
            hardware_path=data_directory / "logs" / "hardware_communication.html",
        )
        self._restore_logging_settings()
        self._diagnostics.set_context_provider(
            lambda: {
                "measurement_id": self._diagnostic_measurement_id,
                "section_id": self._diagnostic_section_id,
            }
        )
        self._log_maintenance_timer = QTimer(self)
        self._log_maintenance_timer.setInterval(24 * 60 * 60 * 1000)
        self._log_maintenance_timer.timeout.connect(
            self._run_scheduled_log_maintenance
        )
        self._log_maintenance_timer.start()
        if self._diagnostics.retention_settings.automatic_cleanup_enabled:
            self._diagnostics.cleanup_logs_async()
        self._restore_nas_settings()
        self.setWindowIcon(application_icon())
        maximum_plot_points = int(
            str(
                self._user_settings.value(
                    "ui/maximum_visible_plot_points_per_series", 2000
                )
            )
        )
        maximum_plot_points = min(100_000, max(100, maximum_plot_points))
        self._times: deque[float] = deque(maxlen=maximum_plot_points)
        self._jacket_pressures: deque[float] = deque(maxlen=maximum_plot_points)
        self._injection_pressures: deque[float] = deque(maxlen=maximum_plot_points)
        self._injection_flows: deque[float] = deque(maxlen=maximum_plot_points)
        self._line_pressures: deque[float] = deque(maxlen=maximum_plot_points)
        self._alarm_points: list[dict[str, object]] = []
        self._runtime_bridge = RuntimeBridge(self)
        self._runtime_bridge.cycle_completed.connect(self._handle_cycle)
        self._runtime_bridge.fault_raised.connect(self._handle_runtime_fault)
        self._runtime_bridge.preflight_completed.connect(
            self._measurement_preflight_completed
        )
        self._runtime_bridge.preflight_failed.connect(
            self._measurement_preflight_failed
        )
        self._runtime_bridge.pump_startup_progress.connect(
            self._measurement_pump_startup_progress
        )
        self._runtime_bridge.pump_preparation_progress.connect(
            self._pump_preparation_progress
        )
        self._runtime_bridge.pump_startup_completed.connect(
            self._measurement_pump_startup_completed
        )
        self._runtime_bridge.flow_change_completed.connect(
            self._measurement_flow_change_completed
        )
        self._runtime_bridge.flow_change_failed.connect(
            self._measurement_flow_change_failed
        )
        self._runtime_bridge.pump_startup_failed.connect(
            self._measurement_pump_startup_failed
        )
        self._runtime_bridge.stage_export_batch_finished.connect(
            self._stage_export_batch_finished
        )
        self._runtime_bridge.hardware_status_completed.connect(
            self._hardware_status_completed
        )
        self._runtime_bridge.hardware_status_failed.connect(
            self._hardware_status_failed
        )
        self._runtime = self._make_runtime(control_loop)
        self._build_ui()
        self._hardware_status_timer = QTimer(self)
        hardware_status_seconds = float(
            str(
                self._user_settings.value(
                    "hardware/status_poll_interval_seconds", 1.0
                )
            )
        )
        self._hardware_status_timer.setInterval(
            max(100, int(hardware_status_seconds * 1000.0))
        )
        self._hardware_status_timer.timeout.connect(
            self._refresh_active_hardware_status
        )
        self._hardware_status_timer.start()
        self._build_menu()
        self._build_tray_menu()
        if self._tray_available:
            self._tray_icon.show()
        self._restore_theme()
        self._restore_control_settings()
        self._project_selector_required = not self._restore_project_selection()
        self._project_selector_prompted = False
        if (
            self._run_mode is RunMode.SIMULATION
            and self._devices.status.state is ApplicationState.IDLE
        ):
            self._devices.connect()
        if self._run_mode is RunMode.SIMULATION and self._pump_control is None:
            self._pump_control = self._make_simulation_pump_control()
        self._refresh_state()

    def _build_tray_menu(self) -> None:
        self._tray_menu = QMenu(self)
        self._tray_menu.setObjectName("system_tray_menu")
        self._tray_show_action = QAction("Ablak megnyitása", self)
        self._tray_show_action.triggered.connect(self._restore_from_tray)
        self._tray_menu.addAction(self._tray_show_action)
        self._tray_menu.addSeparator()
        self._tray_quit_action = QAction("Program bezárása", self)
        self._tray_quit_action.setObjectName("system_tray_quit_action")
        self._tray_quit_action.triggered.connect(self._quit_from_tray)
        self._tray_menu.addAction(self._tray_quit_action)
        self._tray_icon.setContextMenu(self._tray_menu)
        self._tray_icon.activated.connect(self._tray_activated)

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._restore_from_tray()

    def _restore_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_from_tray(self) -> None:
        self._diagnostics.emit(
            DiagnosticCategory.SYSTEM,
            "EXIT",
            "operator requested application shutdown from system tray",
        )
        if not self.close():
            return
        application = QApplication.instance()
        if isinstance(application, QApplication):
            application.quit()

    def _dashboard_box(
        self, key: str, title: str, side: str
    ) -> EditableDashboardBox:
        box = EditableDashboardBox(key, title)
        box.hide_requested.connect(self._hide_dashboard_box)
        self._dashboard_boxes[key] = box
        self._dashboard_box_titles[key] = title
        self._dashboard_box_sides[key] = side
        return box

    def _build_ui(self) -> None:
        self.setWindowTitle("AFKI EOR mérőrendszer — szimuláció")
        self.resize(1100, 720)
        self._dashboard_boxes: dict[str, EditableDashboardBox] = {}
        self._dashboard_box_titles: dict[str, str] = {}
        self._dashboard_box_sides: dict[str, str] = {}
        self._dashboard_box_visibility: dict[str, bool] = {}
        self._layout_editor_active = False
        root = QWidget()
        layout = QVBoxLayout(root)
        self._alarm_container = self._create_alarm_banner_component()
        layout.addWidget(self._alarm_container)
        self._refresh_mode_label()
        self._refresh_alarm_banner()
        status_container = self._create_status_sidebar_component()
        right_container = self._create_control_sidebar_component()
        self._measurement_tabs = self._create_measurement_tabs_component()
        splitter = self._create_dashboard_splitter_component(
            status_container,
            right_container,
        )
        layout.addWidget(splitter, stretch=1)
        self._layout_editor_bar = self._create_layout_editor_bar()
        layout.addWidget(self._layout_editor_bar)
        self._restore_dashboard_layout()
        self.setCentralWidget(root)

    def _create_dashboard_splitter_component(
        self,
        status_container: QWidget,
        control_container: QWidget,
    ) -> QSplitter:
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
        right_scroll.setWidget(control_container)
        self._dashboard_sidebars = {
            "left": left_scroll,
            "right": right_scroll,
        }
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
        self._dashboard_splitter = splitter
        return splitter

    def _create_control_sidebar_component(self) -> QWidget:
        right_container = QWidget()
        right_container.setObjectName("control_sidebar")
        right_container.setMinimumWidth(0)
        right_container.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.MinimumExpanding
        )
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(4, 0, 4, 4)

        right_layout.addWidget(self._create_measurement_controls_component())
        right_layout.addWidget(self._create_measurement_flow_component())
        right_layout.addWidget(self._create_recording_status_component())
        right_layout.addWidget(self._create_startup_summary_component())
        project_editor, project_summary = self._create_project_component()
        right_layout.addWidget(project_editor)
        right_layout.addWidget(project_summary)
        right_layout.addWidget(self._create_valve_control_component())
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
        return right_container

    def _create_valve_control_component(self) -> QWidget:
        settings = self._dashboard_box(
            "valve_control", "Szelepvezérlés", "right"
        )
        settings.setObjectName("valve_control_settings")
        form = QFormLayout(settings)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint
        )
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
        self._pid_deadband = self._pid_spinbox(0.5)
        self._pid_deadband.setSuffix(" bar")
        self._pid_output_rate = self._pid_spinbox(10.0)
        self._pid_output_rate.setSuffix(" %/s")
        self._pid_filter_alpha = self._pid_spinbox(0.25)
        self._pid_filter_alpha.setRange(0.01, 1.0)
        self._pid_reversal_interval = self._pid_spinbox(1.0)
        self._pid_reversal_interval.setSuffix(" s")
        self._pid_reversal_deadband = self._percent_spinbox(0.5)
        self._pid_max_reversals = QSpinBox()
        self._pid_max_reversals.setRange(1, 100)
        self._pid_max_reversals.setValue(6)
        self._direction = QComboBox()
        self._direction.addItem("Fordított", ControlDirection.REVERSE)
        self._direction.addItem("Közvetlen", ControlDirection.DIRECT)
        self._loading_pid_profile = False
        self._pid_profile = QComboBox()
        self._pid_profile.setObjectName("pid_profile_selector")
        self._save_pid_profile_button = QPushButton("Mentés…")
        self._delete_pid_profile_button = QPushButton("Törlés")
        profile_actions = QWidget()
        profile_actions_layout = QVBoxLayout(profile_actions)
        profile_actions_layout.setContentsMargins(0, 0, 0, 0)
        profile_actions_layout.addWidget(self._save_pid_profile_button)
        profile_actions_layout.addWidget(self._delete_pid_profile_button)
        self._apply_pid_button = QPushButton("PID beállítások alkalmazása")
        self._apply_pid_button.clicked.connect(self._apply_pid)
        self._pid_application_status_block = QWidget()
        self._pid_application_status_block.setObjectName(
            "pid_application_status_block"
        )
        self._pid_application_status_block.setMinimumWidth(0)
        status_block_policy = self._pid_application_status_block.sizePolicy()
        status_block_policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
        status_block_policy.setVerticalPolicy(QSizePolicy.Policy.Minimum)
        self._pid_application_status_block.setSizePolicy(status_block_policy)
        pid_status_layout = QVBoxLayout(self._pid_application_status_block)
        pid_status_layout.setContentsMargins(0, 4, 0, 4)
        pid_status_layout.setSpacing(4)
        self._pid_application_status_title = QLabel("PID alkalmazási állapot")
        self._pid_application_status_title.setWordWrap(True)
        self._pid_application_status = QTextEdit(
            "A PID-paraméterek mérés közben automatikusan frissülnek."
        )
        self._pid_application_status.setReadOnly(True)
        self._pid_application_status.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._pid_application_status.setFrameStyle(0)
        self._pid_application_status.setLineWrapMode(
            QTextEdit.LineWrapMode.WidgetWidth
        )
        self._pid_application_status.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._pid_application_status.setMinimumWidth(0)
        self._pid_application_status.setFixedHeight(
            self._pid_application_status.fontMetrics().lineSpacing() * 3 + 12
        )
        pid_status_policy = self._pid_application_status.sizePolicy()
        pid_status_policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
        pid_status_policy.setVerticalPolicy(QSizePolicy.Policy.Fixed)
        self._pid_application_status.setSizePolicy(pid_status_policy)
        pid_status_layout.addWidget(self._pid_application_status_title)
        pid_status_layout.addWidget(self._pid_application_status)
        self._pid_live_update_timer = QTimer(self)
        self._pid_live_update_timer.setSingleShot(True)
        self._pid_live_update_timer.setInterval(100)
        self._pid_live_update_timer.timeout.connect(self._apply_live_pid_update)
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
        form.addRow("Nyomásholtsáv", self._pid_deadband)
        form.addRow("Maximális kimeneti sebesség", self._pid_output_rate)
        form.addRow("Nyomásszűrő alfa", self._pid_filter_alpha)
        form.addRow("Minimális irányváltási idő", self._pid_reversal_interval)
        form.addRow("Ellenirányú korrekció holtsávja", self._pid_reversal_deadband)
        form.addRow("Maximális irányváltásszám / 10 s", self._pid_max_reversals)
        form.addRow(self._pid_application_status_block)
        form.addRow(self._apply_pid_button)
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
            self._pid_deadband,
            self._pid_output_rate,
            self._pid_filter_alpha,
            self._pid_reversal_interval,
            self._pid_reversal_deadband,
            self._pid_max_reversals,
        ):
            field.setMinimumWidth(160)
            field.setMaximumWidth(240)
            policy = field.sizePolicy()
            policy.setHorizontalPolicy(QSizePolicy.Policy.Preferred)
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
            self._pid_deadband,
            self._pid_output_rate,
            self._pid_filter_alpha,
            self._pid_reversal_interval,
            self._pid_reversal_deadband,
            self._pid_max_reversals,
        ):
            if isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self._pid_values_changed)
            else:
                widget.valueChanged.connect(self._pid_values_changed)
        self._reload_pid_profiles()
        self._service_pid_fields = (
            self._pid_profile,
            profile_actions,
            self._kp,
            self._ki,
            self._kd,
            self._direction,
            self._output_min,
            self._output_max,
            self._pid_deadband,
            self._pid_output_rate,
            self._pid_filter_alpha,
            self._pid_reversal_interval,
            self._pid_reversal_deadband,
            self._pid_max_reversals,
            self._pid_application_status_block,
            self._apply_pid_button,
        )
        self._service_pid_form = form
        self._live_measurement_fields = (
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
            self._pid_deadband,
            self._pid_output_rate,
            self._pid_filter_alpha,
            self._pid_reversal_interval,
            self._pid_reversal_deadband,
            self._pid_max_reversals,
        )
        self._configure_control_tooltips()
        self._set_service_controls_visible(self._developer_mode)
        return settings

    def _create_project_component(self) -> tuple[QWidget, QWidget]:
        project_box = QGroupBox("Mérési projekt és szakasz")
        project_layout = QGridLayout(project_box)
        self._project = QComboBox()
        self._project.setObjectName("project_selector")
        self._stage = QComboBox()
        self._stage.setObjectName("stage_selector")
        self._stage.setMinimumWidth(160)
        self._stage.setMaximumWidth(240)
        self._stage.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        self._last_selected_stage_id: int | None = None
        new_project = QPushButton("Új projekt")
        add_stage = QPushButton("Új szakasz")
        rename_stage = QPushButton("Szakasz átnevezése")
        self._project.currentIndexChanged.connect(self._reload_stages)
        self._stage.currentIndexChanged.connect(self._stage_changed)
        new_project.clicked.connect(self._create_project)
        add_stage.clicked.connect(self._add_stage)
        rename_stage.clicked.connect(self._rename_stage)
        project_layout.addWidget(input_field_label("Projekt", self._project), 0, 0)
        project_layout.addWidget(self._project, 0, 1, 1, 2)
        project_layout.addWidget(new_project, 1, 0, 1, 3)
        project_layout.addWidget(add_stage, 3, 0)
        project_layout.addWidget(rename_stage, 3, 1, 1, 2)
        project_box.setVisible(False)
        project_summary = self._dashboard_box(
            "active_project", "Aktív projekt", "right"
        )
        project_summary.setObjectName("active_project_summary")
        project_summary_layout = QFormLayout(project_summary)
        project_summary_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        project_summary_layout.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint
        )
        self._active_project_label = QLabel("Nincs kiválasztva")
        self._active_project_label.setWordWrap(True)
        self._active_project_label.setMinimumWidth(0)
        self._active_project_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
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
        return project_box, project_summary

    def _create_measurement_controls_component(self) -> QWidget:
        control_box = self._dashboard_box(
            "measurement_controls", "Mérésvezérlés", "right"
        )
        controls = QGridLayout(control_box)
        self._connect_button = QPushButton("Csatlakozás")
        self._disconnect_button = QPushButton("Leválasztás")
        self._connect_button.hide()
        self._disconnect_button.hide()
        self._start_button = QPushButton("Mérés indítása")
        self._prepare_button = QPushButton("Előkészítés")
        self._pause_button = QPushButton("Mérés szüneteltetése")
        self._stop_button = QPushButton("Mérés leállítása")
        self._emergency_button = QPushButton("VÉSZLEÁLLÍTÁS")
        self._emergency_button.setStyleSheet(
            "background:#b00020;color:white;font-weight:700;padding:10px"
        )
        self._connect_button.clicked.connect(self._connect_devices)
        self._disconnect_button.clicked.connect(self._disconnect_devices)
        self._start_button.clicked.connect(self._start)
        self._prepare_button.clicked.connect(self._prepare)
        self._pause_button.clicked.connect(self._pause_measurement)
        self._stop_button.clicked.connect(self._stop)
        self._emergency_button.clicked.connect(self._emergency_stop)
        self._primary_control_buttons = (
            self._start_button,
            self._prepare_button,
            self._pause_button,
            self._stop_button,
            self._emergency_button,
        )
        for row, button in enumerate(self._primary_control_buttons):
            button.setMinimumWidth(0)
            button.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            controls.addWidget(button, row, 0)
        controls.setColumnStretch(0, 1)
        return control_box

    def _create_measurement_flow_component(self) -> QWidget:
        flow_box = self._dashboard_box(
            "measurement_flow", "BES mérési térfogatáram", "right"
        )
        flow_layout = QFormLayout(flow_box)
        flow_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        flow_layout.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint
        )
        self._current_measurement_flow = QLabel("— ml/h")
        self._current_measurement_flow.setObjectName("current_measurement_flow")
        self._new_measurement_flow = QDoubleSpinBox()
        self._new_measurement_flow.setObjectName("new_measurement_flow")
        self._new_measurement_flow.setRange(0.001, 600000.0)
        self._new_measurement_flow.setDecimals(3)
        self._new_measurement_flow.setSuffix(" ml/h")
        self._new_measurement_flow.setMinimumWidth(160)
        self._new_measurement_flow.setMaximumWidth(240)
        self._new_measurement_flow.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        self._apply_measurement_flow_button = QPushButton("Alkalmazás")
        self._apply_measurement_flow_button.setMaximumWidth(240)
        self._apply_measurement_flow_button.clicked.connect(
            self._apply_running_measurement_flow
        )
        flow_layout.addRow("Aktuális", self._current_measurement_flow)
        flow_layout.addRow("Új érték", self._new_measurement_flow)
        flow_layout.addRow(self._apply_measurement_flow_button)
        return flow_box

    def _create_recording_status_component(self) -> QWidget:
        recording_box = self._dashboard_box(
            "measurement_recording", "Mérési adatrögzítés", "right"
        )
        recording_layout = QVBoxLayout(recording_box)
        self._recording_status_label = QLabel("RÖGZÍTÉS NEM AKTÍV")
        self._recording_status_label.setObjectName("recording_status_label")
        self._recording_status_label.setWordWrap(True)
        self._recording_details_label = QLabel("Nincs aktív mérési fájl.")
        self._recording_details_label.setWordWrap(True)
        self._recording_details_label.setMinimumWidth(0)
        self._recording_details_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self._recording_details_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._nas_runtime_label = QLabel("NAS: kikapcsolva")
        self._nas_runtime_label.setWordWrap(True)
        self._nas_runtime_label.setMinimumWidth(0)
        self._nas_runtime_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        recording_layout.addWidget(self._recording_status_label)
        recording_layout.addWidget(self._recording_details_label)
        recording_layout.addWidget(self._nas_runtime_label)
        return recording_box

    def _create_startup_summary_component(self) -> QWidget:
        configuration_box = self._dashboard_box(
            "startup_configuration", "Indulási konfiguráció", "right"
        )
        configuration_layout = QVBoxLayout(configuration_box)
        self._configuration_summary_label = QLabel()
        self._configuration_summary_label.setObjectName("configuration_summary")
        self._configuration_summary_label.setWordWrap(True)
        self._configuration_summary_label.setMinimumWidth(0)
        self._configuration_summary_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        configuration_layout.addWidget(self._configuration_summary_label)
        return configuration_box

    def _create_alarm_banner_component(self) -> QWidget:
        self._alarm_container = QWidget()
        self._alarm_container.setObjectName("dashboard_alarm_container")
        alarm_layout = QHBoxLayout(self._alarm_container)
        alarm_layout.setContentsMargins(9, 6, 6, 6)
        self._alarm_label = QLabel()
        self._alarm_label.setObjectName("dashboard_alarm_label")
        self._alarm_label.setWordWrap(True)
        self._alarm_label.setAccessibleName("Aktív biztonsági riasztás")
        alarm_layout.addWidget(self._alarm_label, 1)
        self._alarm_close_button = QPushButton("×")
        self._alarm_close_button.setObjectName("dashboard_alarm_close")
        self._alarm_close_button.setAccessibleName("Riasztás bezárása")
        self._alarm_close_button.setToolTip(
            "Friss biztonsági ellenőrzés után bezárja a riasztást"
        )
        self._alarm_close_button.setFixedSize(32, 32)
        self._alarm_close_button.clicked.connect(self._dismiss_alarm)
        alarm_layout.addWidget(self._alarm_close_button)
        self._alarm_label.hide()
        self._alarm_close_button.hide()
        self._alarm_container.hide()
        return self._alarm_container

    def _create_measurement_tabs_component(self) -> QTabWidget:

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
        self._alarm_scatter = pg.ScatterPlotItem(
            size=12,
            symbol="o",
            pen=pg.mkPen("#ffffff", width=1),
            hoverable=True,
            name="Riasztás",
        )
        self._alarm_scatter.sigHovered.connect(self._alarm_points_hovered)
        self._plot.addItem(self._alarm_scatter)
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
        return self._measurement_tabs

    def _create_status_sidebar_component(self) -> QWidget:

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
        self._pressure_margin_label = QLabel("— bar")
        labels = (
            ("system_state", "Rendszerállapot", self._state_label),
            ("jacket_pump", "Köpenypumpa", self._jacket_label),
            ("injection_pump", "Besajtolópumpa", self._injection_label),
            ("line_pressure", "Vonali nyomás", self._line_label),
            (
                "differential_pressure",
                "Differenciálnyomás",
                self._delta_label,
            ),
            (
                "pressure_margin",
                "Nyomáskülönbség (tájékoztató)",
                self._pressure_margin_label,
            ),
            ("valve_status", "Szelep", self._valve_label),
        )
        self._connection_labels: dict[str, QLabel] = {}
        connection_keys = (
            None,
            "jacket",
            "injection",
            "line_daq",
            "delta_daq",
            None,
            "valve",
        )
        for index, (key, title, value) in enumerate(labels):
            box = self._dashboard_box(key, title, "left")
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

        return status_container

    def _create_layout_editor_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("dashboard_layout_editor_bar")
        bar.setStyleSheet(
            "#dashboard_layout_editor_bar { border:1px solid #66788a;"
            "border-radius:6px;padding:4px; }"
        )
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(8, 6, 8, 6)
        header = QHBoxLayout()
        title = QLabel(
            "ELRENDEZÉSSZERKESZTŐ — a kijelölt elemek láthatók; az × elrejti a kártyát"
        )
        title.setWordWrap(True)
        title.setStyleSheet("font-weight:700")
        finish = QPushButton("Szerkesztés befejezése")
        finish.clicked.connect(self._leave_layout_editor)
        reset = QPushButton("Minden elem visszaállítása")
        reset.clicked.connect(self._reset_dashboard_layout)
        header.addWidget(title, 1)
        header.addWidget(reset)
        header.addWidget(finish)
        outer.addLayout(header)

        available_scroll = QScrollArea()
        available_scroll.setObjectName("layout_available_elements_scroll")
        available_scroll.setWidgetResizable(True)
        available_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        available_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        available_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        available = QWidget()
        available.setObjectName("layout_available_elements")
        available_layout = QHBoxLayout(available)
        available_layout.setContentsMargins(0, 0, 0, 0)
        available_layout.setSpacing(6)
        self._layout_element_buttons: dict[str, QPushButton] = {}
        for key, label in (
            ("sidebar:left", "Bal oldali menü"),
            ("sidebar:right", "Jobb oldali menü"),
            *tuple(
                (f"box:{key}", title)
                for key, title in self._dashboard_box_titles.items()
            ),
        ):
            button = QPushButton(label)
            button.setObjectName(f"layout_element_{key.replace(':', '_')}")
            button.setCheckable(True)
            button.setToolTip(
                "Bekapcsolva az elem látható; kikapcsolva el van rejtve."
            )
            button.toggled.connect(
                lambda visible, element_key=key: self._layout_element_toggled(
                    element_key, visible
                )
            )
            available_layout.addWidget(button)
            self._layout_element_buttons[key] = button
        available_layout.addStretch(1)
        available_scroll.setWidget(available)
        outer.addWidget(available_scroll)
        bar.hide()
        return bar

    def _restore_dashboard_layout(self) -> None:
        self._dashboard_sidebar_visibility = {
            side: self._setting_bool(
                f"appearance/dashboard/sidebar_{side}_visible", True
            )
            for side in ("left", "right")
        }
        for side, visible in self._dashboard_sidebar_visibility.items():
            self._dashboard_sidebars[side].setVisible(visible)
        for key, box in self._dashboard_boxes.items():
            visible = self._setting_bool(
                f"appearance/dashboard/box_{key}_visible", True
            )
            self._dashboard_box_visibility[key] = visible
            box.setVisible(visible)
        self._sync_layout_editor_buttons()

    def _enter_layout_editor(self) -> None:
        self._layout_editor_active = True
        self._layout_editor_bar.show()
        for key, box in self._dashboard_boxes.items():
            box.set_editor_active(self._dashboard_box_visibility.get(key, True))
        self._sync_layout_editor_buttons()

    def _leave_layout_editor(self) -> None:
        self._layout_editor_active = False
        for box in self._dashboard_boxes.values():
            box.set_editor_active(False)
        self._layout_editor_bar.hide()

    def _hide_dashboard_box(self, key: str) -> None:
        self._set_dashboard_box_visible(key, False)

    def _set_dashboard_box_visible(self, key: str, visible: bool) -> None:
        box = self._dashboard_boxes.get(key)
        if box is None:
            return
        if (
            key == "measurement_controls"
            and not visible
            and self._devices.status.state is ApplicationState.RUNNING
        ):
            self._show_error(
                "A mérésvezérlés futó vagy szüneteltetett mérés közben nem "
                "rejthető el."
            )
            self._sync_layout_editor_buttons()
            return
        self._dashboard_box_visibility[key] = visible
        box.setVisible(visible)
        box.set_editor_active(self._layout_editor_active and visible)
        self._user_settings.setValue(
            f"appearance/dashboard/box_{key}_visible", visible
        )
        self._user_settings.sync()
        self._sync_layout_editor_buttons()

    def _set_dashboard_sidebar_visible(self, side: str, visible: bool) -> None:
        if side not in self._dashboard_sidebars:
            return
        if (
            side == "right"
            and not visible
            and self._devices.status.state is ApplicationState.RUNNING
        ):
            self._show_error(
                "A jobb oldali mérésvezérlés futó vagy szüneteltetett mérés "
                "közben nem rejthető el."
            )
            self._sync_layout_editor_buttons()
            return
        self._dashboard_sidebar_visibility[side] = visible
        self._dashboard_sidebars[side].setVisible(visible)
        self._user_settings.setValue(
            f"appearance/dashboard/sidebar_{side}_visible", visible
        )
        self._user_settings.sync()
        self._sync_layout_editor_buttons()

    def _layout_element_toggled(self, key: str, visible: bool) -> None:
        if key.startswith("sidebar:"):
            self._set_dashboard_sidebar_visible(key.removeprefix("sidebar:"), visible)
            return
        if key.startswith("box:"):
            self._set_dashboard_box_visible(key.removeprefix("box:"), visible)

    def _sync_layout_editor_buttons(self) -> None:
        buttons = getattr(self, "_layout_element_buttons", {})
        states = {
            **{
                f"sidebar:{side}": visible
                for side, visible in getattr(
                    self, "_dashboard_sidebar_visibility", {}
                ).items()
            },
            **{
                f"box:{key}": visible
                for key, visible in self._dashboard_box_visibility.items()
            },
        }
        for key, button in buttons.items():
            button.blockSignals(True)
            button.setChecked(states.get(key, True))
            button.blockSignals(False)

    def _reset_dashboard_layout(self) -> None:
        for side in self._dashboard_sidebars:
            self._set_dashboard_sidebar_visible(side, True)
        for key in self._dashboard_boxes:
            self._set_dashboard_box_visible(key, True)

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
        device_settings.triggered.connect(
            lambda: self._open_settings_hub("devices")
        )
        settings_menu.addAction(device_settings)
        logging_settings = QAction("Naplózás…", self)
        logging_settings.triggered.connect(
            lambda: self._open_settings_hub("logging")
        )
        settings_menu.addAction(logging_settings)
        settings_menu.addSeparator()
        self._measurement_settings_action = QAction(
            "Kalibráció és biztonsági határértékek…", self
        )
        self._measurement_settings_action.triggered.connect(
            lambda: self._open_settings_hub("calibration")
        )
        settings_menu.addAction(self._measurement_settings_action)
        settings_menu.addSeparator()

        appearance_settings = QAction("Megjelenés…", self)
        appearance_settings.triggered.connect(
            lambda: self._open_settings_hub("appearance")
        )
        settings_menu.addAction(appearance_settings)
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
            "Szimulációban nincs fizikai kimenet; a mérési adatok külön, "
            "szimulációként jelölt fájlba kerülnek. "
            "Kikapcsoláskor az Eszközbeállítások ablakban aktiválható az éles mód."
        )
        self._simulation_mode_action.setVisible(self._developer_mode)
        self._simulation_mode_action.toggled.connect(self._simulation_mode_toggled)
        developer_menu.addAction(self._simulation_mode_action)
        self._control_cycle_settings_action = QAction(
            "Vezérlési ciklus és watchdog…", self
        )
        self._control_cycle_settings_action.setVisible(self._developer_mode)
        self._control_cycle_settings_action.triggered.connect(
            lambda: self._open_settings_hub("control_cycle")
        )
        developer_menu.addAction(self._control_cycle_settings_action)
        self._pump_telemetry_settings_action = QAction(
            "Pumpatelemetria és STALE…", self
        )
        self._pump_telemetry_settings_action.setVisible(self._developer_mode)
        self._pump_telemetry_settings_action.triggered.connect(
            lambda: self._open_settings_hub("pump_telemetry")
        )
        developer_menu.addAction(self._pump_telemetry_settings_action)
        self._simulation_settings_action = QAction(
            "Szimuláció és hibateszt…", self
        )
        self._simulation_settings_action.setVisible(self._developer_mode)
        self._simulation_settings_action.setEnabled(
            self._run_mode is RunMode.SIMULATION
        )
        self._simulation_settings_action.triggered.connect(
            lambda: self._open_settings_hub("simulation")
        )
        developer_menu.addAction(self._simulation_settings_action)
        self._developer_view_action = QAction("Eszközkommunikáció…", self)
        self._developer_view_action.setShortcut("Ctrl+Shift+L")
        self._developer_view_action.setVisible(self._developer_mode)
        self._developer_view_action.triggered.connect(self._open_developer_view)
        developer_menu.addAction(self._developer_view_action)

    def _restore_logging_settings(self) -> None:
        enabled_value = str(
            self._user_settings.value("logging/enabled", "true")
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
        defaults = LogRetentionSettings()

        def integer(key: str, fallback: int) -> int:
            try:
                value = int(str(self._user_settings.value(f"logging/{key}", fallback)))
            except (TypeError, ValueError):
                return fallback
            return value if value > 0 else fallback

        def boolean(key: str, fallback: bool) -> bool:
            value = str(
                self._user_settings.value(
                    f"logging/{key}", "true" if fallback else "false"
                )
            ).lower()
            return value in {"1", "true", "yes"}

        retention = LogRetentionSettings(
            retention_days=integer("retention_days", defaults.retention_days),
            measurement_retention_days=integer(
                "measurement_retention_days",
                defaults.measurement_retention_days,
            ),
            maximum_file_size_mb=integer(
                "maximum_file_size_mb", defaults.maximum_file_size_mb
            ),
            maximum_rotated_files=integer(
                "maximum_rotated_files", defaults.maximum_rotated_files
            ),
            total_storage_limit_mb=integer(
                "total_storage_limit_mb", defaults.total_storage_limit_mb
            ),
            compression_enabled=boolean(
                "compression_enabled", defaults.compression_enabled
            ),
            automatic_cleanup_enabled=boolean(
                "automatic_cleanup_enabled",
                defaults.automatic_cleanup_enabled,
            ),
        )
        self._diagnostics.configure_retention(retention)

    def _run_scheduled_log_maintenance(self) -> None:
        if self._diagnostics.retention_settings.automatic_cleanup_enabled:
            self._diagnostics.cleanup_logs_async()

    def _open_settings_hub(self, initial_page: str = "devices") -> None:
        if self._runtime.running and initial_page in {
            "devices",
            "calibration",
            "control_cycle",
            "pump_telemetry",
        }:
            self._show_error(
                "Futó vagy szüneteltetett mérés közben ez a beállítás nem módosítható."
            )
            return
        reconnect_state = {"required": False, "activated": False}
        hub: SettingsHubDialog

        def device_page() -> QWidget:
            return self._create_device_settings_page(hub, reconnect_state)

        pages: list[tuple[str, str, str, Callable[[], QWidget]]] = [
            (
                "devices",
                "Eszközök",
                "Pumpák, NI csatornák, kapcsolatpróba és közvetlen "
                "eszközkezelés.",
                device_page,
            ),
            (
                "calibration",
                "Kalibráció és biztonság",
                "Érzékelő-kalibrációk, nyomáshatárok és biztonsági "
                "tartalékok.",
                self._create_calibration_settings_page,
            ),
            (
                "logging",
                "Naplózás",
                "Kommunikációs és vezérlési naplók, valamint a naplózott "
                "eszközcsoportok.",
                self._create_logging_settings_page,
            ),
            (
                "nas",
                "NAS és tárhely",
                "NAS célmappa, Windows-hitelesítésű kapcsolat- és írhatósági "
                "teszt, várólista és távoli fájlrendszer.",
                self._create_nas_settings_page,
            ),
            (
                "appearance",
                "Megjelenés",
                "Téma, oldalsávok és a dashboard kártyáinak láthatósága.",
                lambda: self._create_appearance_settings_page(hub),
            ),
        ]
        if self._developer_mode:
            pages.extend(
                (
                    (
                        "control_cycle",
                        "Vezérlési ciklus",
                        "PID-ciklusidő és watchdog-tűrés. Ezek módosítása csak "
                        "leállított mérésnél engedélyezett.",
                        self._create_control_cycle_settings_page,
                    ),
                    (
                        "pump_telemetry",
                        "Pumpatelemetria / STALE",
                        "Nyomás- és lassú telemetria polling, mezőnkénti "
                        "STALE-határok és startup timeout.",
                        self._create_pump_telemetry_settings_page,
                    ),
                )
            )
            if self._run_mode is RunMode.SIMULATION:
                pages.append(
                    (
                        "simulation",
                        "Szimuláció és hibateszt",
                        "Időfüggő pumpa-, NI- és szelepmodell, valamint "
                        "biztonsági hibák célzott injektálása.",
                        self._create_simulation_settings_page,
                    )
                )
        hub = SettingsHubDialog(tuple(pages), parent=self)
        hub.select_page(initial_page)
        hub.exec()
        if (
            reconnect_state["required"]
            and not reconnect_state["activated"]
            and self._devices.status.state is ApplicationState.IDLE
        ):
            self._reconnect_active_mode()

    @staticmethod
    def _embedded_settings_dialog(dialog: QDialog) -> QDialog:
        dialog.setWindowFlags(Qt.WindowType.Widget)
        if isinstance(dialog, ResizableDialog):
            dialog.setSizeGripEnabled(False)
        dialog.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        return dialog

    def _create_logging_settings_page(self) -> QWidget:
        dialog = LoggingSettingsDialog(
            self._diagnostics, self._user_settings
        )
        self._embedded_settings_dialog(dialog)
        dialog.accepted.connect(lambda: QTimer.singleShot(0, dialog.show))
        dialog.rejected.connect(lambda: QTimer.singleShot(0, dialog.show))
        return dialog

    def _create_nas_settings_page(self) -> QWidget:
        return NasSettingsPage(
            self._nas_sync, self._user_settings, self._data_directory
        )

    def _create_calibration_settings_page(self) -> QWidget:
        if self._runtime.running:
            return QLabel(
                "A kalibráció és a biztonsági határértékek futó vagy "
                "szüneteltetett mérés közben nem módosíthatók."
            )
        dialog = CalibrationSettingsDialog()
        dialog.restore_snapshot(self._measurement_settings.snapshot())
        self._embedded_settings_dialog(dialog)

        def apply() -> None:
            self._measurement_settings.restore_snapshot(dialog.snapshot())
            self._apply_measurement_settings()
            self._save_user_settings()
            if self._overview_dialog is not None:
                self._overview_dialog.refresh()
            QTimer.singleShot(0, dialog.show)

        def restore() -> None:
            dialog.restore_snapshot(self._measurement_settings.snapshot())
            QTimer.singleShot(0, dialog.show)

        dialog.accepted.connect(apply)
        dialog.rejected.connect(restore)
        return dialog

    def _create_appearance_settings_page(
        self, settings_hub: SettingsHubDialog | None = None
    ) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        help_text = QLabel(
            "Az oldalsávok közvetlenül kapcsolhatók. A szerkesztő nézetben minden "
            "dashboard-kártya × gombbal elrejthető, az ablak alján pedig "
            "vízszintesen visszakapcsolható."
        )
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        form = QFormLayout()
        theme = QComboBox()
        theme.setObjectName("settings_theme")
        for key, label in (
            ("system", "Rendszerbeállítás"),
            ("light", "Világos mód"),
            ("dark", "Sötét mód"),
        ):
            theme.addItem(label, key)
        selected = next(
            (
                key
                for key, action in self._theme_actions.items()
                if action.isChecked()
            ),
            "system",
        )
        theme.setCurrentIndex(max(0, theme.findData(selected)))
        form.addRow(input_field_label("Alkalmazás témája", theme), theme)
        left_sidebar = QCheckBox("Bal oldali menü megjelenítése")
        left_sidebar.setObjectName("appearance_left_sidebar_visible")
        left_sidebar.setChecked(
            self._dashboard_sidebar_visibility.get("left", True)
        )
        right_sidebar = QCheckBox("Jobb oldali menü megjelenítése")
        right_sidebar.setObjectName("appearance_right_sidebar_visible")
        right_sidebar.setChecked(
            self._dashboard_sidebar_visibility.get("right", True)
        )
        right_sidebar.setEnabled(
            self._devices.status.state is not ApplicationState.RUNNING
        )
        if not right_sidebar.isEnabled():
            right_sidebar.setToolTip(
                "A mérésvezérlést tartalmazó jobb oldalsáv futó mérés közben "
                "nem rejthető el."
            )
        form.addRow(left_sidebar)
        form.addRow(right_sidebar)
        layout.addLayout(form)
        apply_button = QPushButton("Alkalmazás")
        apply_button.setObjectName("apply_appearance_settings")

        def apply_appearance() -> None:
            selected_theme = str(theme.currentData())
            current_theme = next(
                (
                    key
                    for key, action in self._theme_actions.items()
                    if action.isChecked()
                ),
                "system",
            )
            if selected_theme != current_theme:
                self._theme_actions[selected_theme].setChecked(True)
                self._set_theme(selected_theme)
            self._set_dashboard_sidebar_visible("left", left_sidebar.isChecked())
            self._set_dashboard_sidebar_visible("right", right_sidebar.isChecked())

        apply_button.clicked.connect(apply_appearance)
        layout.addWidget(apply_button)
        editor = QPushButton("Dashboard elrendezésének szerkesztése…")
        editor.setObjectName("open_dashboard_layout_editor")

        def open_editor() -> None:
            apply_appearance()
            if settings_hub is not None:
                settings_hub.accept()
            QTimer.singleShot(0, self._enter_layout_editor)

        editor.clicked.connect(open_editor)
        layout.addWidget(editor)
        layout.addStretch()
        return page

    def _create_simulation_settings_page(self) -> QWidget:
        if (
            self._run_mode is not RunMode.SIMULATION
            or self._simulation_jacket is None
            or self._simulation_injection is None
            or self._simulation_daq is None
        ):
            return QLabel("A hibatesztelő panel csak szimulációs módban érhető el.")
        return SimulationSettingsPage(
            jacket=self._simulation_jacket,
            injection=self._simulation_injection,
            daq=self._simulation_daq,
            valve=self._valve,
            log_event=lambda message: self._diagnostics.emit(
                DiagnosticCategory.SYSTEM, "SIMULATION", message
            ),
        )

    def _create_control_cycle_settings_page(self) -> QWidget:
        if self._runtime.running:
            return QLabel(
                "A vezérlési ciklus futó vagy szüneteltetett mérés közben "
                "nem módosítható."
            )
        dialog = ControlCycleSettingsDialog(self._user_settings)
        self._embedded_settings_dialog(dialog)

        def apply() -> None:
            self._runtime = self._make_runtime(self._control_loop)
            self._diagnostics.emit(
                DiagnosticCategory.RUNTIME,
                "CONFIG",
                "control interval and watchdog tolerance updated: "
                f"interval={dialog.control_interval.value():.3f}s, "
                f"tolerance={dialog.watchdog_tolerance.value():.3f}s",
            )
            QTimer.singleShot(0, dialog.show)

        dialog.accepted.connect(apply)
        dialog.rejected.connect(lambda: QTimer.singleShot(0, dialog.show))
        return dialog

    def _create_pump_telemetry_settings_page(self) -> QWidget:
        if self._runtime.running:
            return QLabel(
                "A pumpatelemetria időzítése futó vagy szüneteltetett mérés "
                "közben nem módosítható."
            )
        dialog = PumpTelemetrySettingsDialog(
            self._user_settings,
            active_intervals=self._active_pump_telemetry_intervals,
        )
        self._embedded_settings_dialog(dialog)

        def applied() -> None:
            intervals = dialog.selected_intervals()
            self._diagnostics.emit(
                DiagnosticCategory.SYSTEM,
                "CONFIG",
                "pump telemetry settings updated; effective on next hardware "
                f"activation: pressure_poll={intervals.pressure_seconds:.3f}s; "
                f"status_poll={intervals.status_poll_seconds:.3f}s; "
                f"slow_gap={intervals.slow_telemetry_seconds:.3f}s; "
                f"pressure_stale={intervals.pressure_stale_seconds:.3f}s; "
                "slow_stale="
                f"{intervals.slow_telemetry_stale_seconds:.3f}s; "
                f"status_stale={intervals.status_stale_seconds:.3f}s; "
                f"startup_timeout={intervals.startup_timeout_seconds:.3f}s",
                level="WARNING",
            )
            QTimer.singleShot(0, dialog.show)

        dialog.accepted.connect(applied)
        dialog.rejected.connect(lambda: QTimer.singleShot(0, dialog.show))
        return dialog

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
        return self._select_project_for_writer(self._measurement_writer, self._run_mode)

    def _new_project_measurement_writer(
        self, mode: RunMode
    ) -> ProjectMeasurementWriter:
        """Build the identical durable data pipeline for either measurement source."""
        return ProjectMeasurementWriter(
            self._data_directory,
            self._nas_sync,
            measurement_kind=(
                "live" if mode is RunMode.HARDWARE else "simulation"
            ),
            phase_completed=self._queue_completed_stage_export,
        )

    def _select_project_for_writer(
        self,
        writer: ProjectMeasurementWriter,
        mode: RunMode,
    ) -> Path | None:
        project_id = self._project.currentData()
        stage_name = self._stage.currentText().strip()
        if not isinstance(project_id, int) or not stage_name:
            return None
        project = self._projects.get_project(project_id)
        configuration_snapshot = dict(project.configuration)
        configuration_snapshot.update(self._current_configuration())
        configuration_snapshot["mode"] = (
            "hardware" if mode is RunMode.HARDWARE else "simulation"
        )
        configuration_snapshot["measurement_kind"] = (
            "live" if mode is RunMode.HARDWARE else "simulation"
        )
        return writer.select_project_with_metadata(
            project.id,
            project.name,
            created_at=project.created_at,
            notes=project.notes,
            configuration=configuration_snapshot,
            calibration_snapshot=project.calibration_snapshot,
            stages=stage_snapshots(project),
            stage_name=stage_name,
        )

    def _queue_completed_stage_export(
        self, source_path: Path, stage_name: str
    ) -> None:
        """Receive stage completion from either the UI or the control thread."""
        self._pending_stage_exports.put((source_path, stage_name))
        self._diagnostics.emit(
            DiagnosticCategory.SYSTEM,
            "EXPORT",
            f"completed stage queued for Excel export: {stage_name}; {source_path}",
        )

    def _start_pending_stage_exports(self) -> None:
        if (
            self._stage_export_active
            or self._runtime.running
            or self._pending_stage_exports.empty()
        ):
            return
        self._stage_export_active = True

        def execute() -> None:
            pending: dict[Path, str] = {}
            try:
                while True:
                    try:
                        source_path, stage_name = (
                            self._pending_stage_exports.get_nowait()
                        )
                    except Empty:
                        break
                    pending[source_path] = stage_name
                for source_path, stage_name in pending.items():
                    destination = project_excel_path(source_path, stage_name)
                    try:
                        export_measurement_excel(
                            source_path,
                            destination,
                            stage_name=stage_name,
                        )
                        relative = destination.relative_to(self._data_directory)
                        nas_relative = (
                            Path(*relative.parts[1:])
                            if relative.parts and relative.parts[0] == "projects"
                            else relative
                        )
                        self._nas_sync.enqueue(destination, nas_relative)
                    except Exception as error:
                        self._diagnostics.emit(
                            DiagnosticCategory.SYSTEM,
                            "EXPORT",
                            f"Excel export failed for stage {stage_name}: {error}",
                            level="ERROR",
                        )
                    else:
                        self._diagnostics.emit(
                            DiagnosticCategory.SYSTEM,
                            "EXPORT",
                            f"Excel export completed for stage {stage_name}: "
                            f"{destination}",
                        )
            finally:
                self._runtime_bridge.stage_export_batch_finished.emit()

        self._stage_export_thread = Thread(
            target=execute,
            name="eor-stage-excel-export",
            daemon=True,
        )
        self._stage_export_thread.start()

    def _stage_export_batch_finished(self) -> None:
        self._stage_export_active = False
        self._start_pending_stage_exports()

    def _open_data_management(self) -> None:
        source_path = self._current_project_file()
        if source_path is None:
            self._show_error("Az adatkezeléshez előbb válassz projektet.")
            return
        if not source_path.is_file():
            self._show_error(
                "Ehhez a mérési fázishoz még nincs megnyitható nyers adatfájl."
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
        self._open_settings_hub("calibration")

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
        record = (
            latest.record
            if latest is not None
            else self._last_hardware_status_record
        )
        snapshot = record.snapshot if record is not None else None
        return {
            "state": self._state_label.text() or "—",
            "mode": (
                "ÉLES MÉRÉS (HARDVER, ADATMENTÉS AKTÍV)"
                if self._run_mode is RunMode.HARDWARE
                else "SZIMULÁCIÓ (ADATMENTÉS AKTÍV, SZIMULÁLT FORRÁS)"
            ),
            "project": self._active_project_label.text(),
            "stage": self._active_stage_label.text(),
            "control_mode": self._mode.currentText(),
            "pressure_source": self._source.currentText(),
            "setpoint": f"{self._setpoint.value():.3f} bar",
            "recording_interval": f"{self._recording_interval.value()} s",
            "last_update": (
                format_hungarian_time(snapshot.recorded_at, "%Y-%m-%d %H:%M:%S")
                if snapshot is not None
                else "Nincs eszközadat"
            ),
            "data_quality": (
                snapshot.quality.value if snapshot is not None else "Nincs eszközadat"
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
                f"{line[0]:g}–{line[1]:g} V → {line[2]:.3f}–{line[3]:.3f} bar"
            ),
            "delta_calibration": (
                f"{delta[0]:g}–{delta[1]:g} V → "
                f"{delta[2]:.3f}–{delta[3]:.3f} bar"
            ),
            "max_jacket": f"{self._max_jacket.value():.3f} bar",
            "max_injection": f"{self._max_injection.value():.3f} bar",
            "max_line": f"{self._max_line.value():.3f} bar",
            "max_delta": f"{self._max_delta.value():.3f} bar",
            "minimum_margin": f"{self._minimum_margin.value():.3f} bar",
            "max_overshoot": f"{self._max_overshoot.value():.3f} bar",
        }

    def _refresh_active_hardware_status(self) -> None:
        if (
            self._run_mode is not RunMode.HARDWARE
            or not self._hardware_status_monitoring_allowed()
            or self._runtime.running
            or self._preflight_active
            or self._hardware_status_active
        ):
            return
        self._hardware_status_active = True
        generation = self._hardware_status_generation
        control_loop = self._control_loop
        jacket = self._devices.jacket_pump
        injection = self._devices.injection_pump
        active_stage = self._stage.currentText().strip() or "Előkészítés"

        def execute() -> None:
            try:
                record = control_loop.observe_pump_startup_once(
                    active_stage=active_stage
                )
                jacket_text, jacket_ok = self._pump_telemetry_summary(jacket)
                injection_text, injection_ok = self._pump_telemetry_summary(
                    injection
                )
                result = HardwareDashboardStatus(
                    generation,
                    record,
                    jacket_text,
                    jacket_ok,
                    injection_text,
                    injection_ok,
                )
            except Exception as error:
                self._runtime_bridge.hardware_status_failed.emit(
                    (generation, str(error))
                )
            else:
                self._runtime_bridge.hardware_status_completed.emit(result)

        Thread(
            target=execute,
            name="eor-hardware-dashboard-status",
            daemon=True,
        ).start()

    def _hardware_status_monitoring_allowed(self) -> bool:
        status = self._devices.status
        return status.state is ApplicationState.READY or (
            status.state is ApplicationState.RUNNING
            and status.measurement is MeasurementState.WAITING_CONFIRMATION
        )

    @staticmethod
    def _pump_telemetry_summary(pump: object) -> tuple[str, bool | None]:
        reader = getattr(pump, "read_telemetry", None)
        if not callable(reader):
            return "KAPCSOLÓDVA", True
        telemetry = reader()
        if not isinstance(telemetry, PumpTelemetrySnapshot):
            raise TypeError("invalid pump telemetry snapshot")
        age = telemetry.pressure.age_seconds
        pressure_age = "nincs adat" if age is None else f"{age:.2f} s"
        slow_issues = [
            name
            for name, field in (
                ("FLOW", telemetry.flow),
                ("VOLA", telemetry.volume),
                ("STATUS", telemetry.operating_status),
            )
            if field.quality is not DataQuality.GOOD
        ]
        details = (
            f"{telemetry.connection_state.value} | nyomás kora: {pressure_age}"
        )
        if slow_issues:
            details += " | lassú adat: " + ", ".join(slow_issues)
        if telemetry.connection_state.value == "READY":
            return details, True
        if telemetry.connection_state.value == "DISCONNECTED":
            return details, False
        return details, None

    def _hardware_status_completed(self, result: object) -> None:
        if not isinstance(result, HardwareDashboardStatus):
            return
        if result.generation == self._hardware_status_generation:
            self._hardware_status_active = False
        if (
            result.generation != self._hardware_status_generation
            or self._run_mode is not RunMode.HARDWARE
            or not self._hardware_status_monitoring_allowed()
        ):
            return
        self._last_hardware_status_record = result.record
        self._set_connection_status(
            "jacket", result.jacket_connection, result.jacket_connection_ok
        )
        self._set_connection_status(
            "injection",
            result.injection_connection,
            result.injection_connection_ok,
        )
        self._apply_idle_hardware_record(result.record)
        if result.record.safety_reasons:
            reason = "; ".join(result.record.safety_reasons)
            self._set_active_alarm(f"RETESSZELT BIZTONSÁGI HIBA: {reason}")
            self._handle_critical_hardware_fault(reason)

    def _hardware_status_failed(self, payload: object) -> None:
        if not (
            isinstance(payload, tuple)
            and len(payload) == 2
            and isinstance(payload[0], int)
            and isinstance(payload[1], str)
        ):
            return
        generation, message = payload
        if generation == self._hardware_status_generation:
            self._hardware_status_active = False
        if (
            generation != self._hardware_status_generation
            or self._run_mode is not RunMode.HARDWARE
            or not self._hardware_status_monitoring_allowed()
        ):
            return
        self._handle_critical_hardware_fault(
            f"dashboard hardverállapot-frissítési hiba: {message}"
        )

    def _apply_idle_hardware_record(self, record: MeasurementRecord) -> None:
        snapshot = record.snapshot
        self._jacket_label.setText(
            format_dashboard_pressure(snapshot.jacket_pump.pressure_bar)
        )
        self._injection_label.setText(
            format_dashboard_pressure(snapshot.injection_pump.pressure_bar)
        )
        self._jacket_remaining_label.setText(
            f"Maradék folyadék: "
            f"{snapshot.jacket_pump.remaining_volume_ml:.1f} ml"
        )
        self._injection_remaining_label.setText(
            f"Maradék folyadék: "
            f"{snapshot.injection_pump.remaining_volume_ml:.1f} ml"
        )
        self._injection_flow_label.setText(
            f"Besajtolási sebesség: "
            f"{snapshot.injection_pump.flow_ml_per_hour:.1f} ml/h"
        )
        self._jacket_net_volume_label.setText(
            "Indítás óta nettó köpenytérfogat: a mérés nem fut"
        )
        self._injected_volume_label.setText(
            "Indítás óta nettó besajtolt: a mérés nem fut"
        )
        configuration = self._active_hardware_configuration
        line_enabled = (
            snapshot.line_pressure_bar is not None
            if configuration is None
            else configuration.line_pressure_enabled
        )
        delta_enabled = (
            snapshot.differential_pressure_bar is not None
            if configuration is None
            else configuration.differential_pressure_enabled
        )
        valve_enabled = (
            True if configuration is None else configuration.valve_output_enabled
        )
        self._line_label.setText(
            format_dashboard_pressure(snapshot.line_pressure_bar)
            if line_enabled and snapshot.line_pressure_bar is not None
            else "Nincs hozzáadva"
        )
        self._delta_label.setText(
            format_dashboard_pressure(snapshot.differential_pressure_bar)
            if delta_enabled and snapshot.differential_pressure_bar is not None
            else "Nincs hozzáadva"
        )
        self._set_connection_status(
            "line_daq",
            "KAPCSOLÓDVA — ÉLŐ"
            if line_enabled
            else "NINCS HOZZÁADVA",
            True if line_enabled else None,
        )
        self._set_connection_status(
            "delta_daq",
            "KAPCSOLÓDVA — ÉLŐ"
            if delta_enabled
            else "NINCS HOZZÁADVA",
            True if delta_enabled else None,
        )
        self._set_connection_status(
            "valve",
            "KAPCSOLÓDVA — SAFE"
            if valve_enabled
            else "NINCS HOZZÁADVA",
            True if valve_enabled else None,
        )
        self._valve_label.setText(
            "SAFE — mérés nem fut" if valve_enabled else "Nincs hozzáadva"
        )
        margin = (
            snapshot.jacket_pump.pressure_bar
            - snapshot.injection_pump.pressure_bar
        )
        self._pressure_margin_label.setText(format_dashboard_pressure(margin))
        self._pressure_margin_label.setStyleSheet(
            "background:transparent;font-size:20px;font-weight:700;color:#66788a"
        )

    def _open_logging_settings(self) -> None:
        self._open_settings_hub("logging")

    def _open_developer_view(self) -> None:
        DeveloperViewDialog(self._diagnostics, parent=self).exec()

    def _open_control_cycle_settings(self) -> None:
        if not self._developer_mode:
            self._show_error("A vezérlési ciklus beállításához Developer mód szükséges.")
            return
        if self._runtime.running:
            self._show_error(
                "A vezérlési ciklus futó mérés közben nem módosítható. "
                "Előbb állítsd le a mérést."
            )
            return
        self._open_settings_hub("control_cycle")

    def _set_developer_mode(self, enabled: bool) -> None:
        self._developer_mode = enabled
        self._set_service_controls_visible(enabled)
        self._simulation_mode_action.setVisible(enabled)
        self._control_cycle_settings_action.setVisible(enabled)
        self._pump_telemetry_settings_action.setVisible(enabled)
        self._simulation_settings_action.setVisible(enabled)
        self._simulation_settings_action.setEnabled(
            enabled and self._run_mode is RunMode.SIMULATION
        )
        self._developer_view_action.setVisible(enabled)
        self._user_settings.setValue("developer/enabled", enabled)
        self._user_settings.sync()

    def _set_service_controls_visible(self, visible: bool) -> None:
        fields = getattr(self, "_service_pid_fields", ())
        form = getattr(self, "_service_pid_form", None)
        for field in fields:
            field.setVisible(visible)
            if isinstance(form, QFormLayout):
                label = form.labelForField(field)
                if label is not None:
                    label.setVisible(visible)

    def _configure_control_tooltips(self) -> None:
        self._stage_control_tooltip = (
            "Az aktív mérési szakaszt választja ki. Leállított mérésnél a "
            "következő mérés szakasza változik. Futás közbeni váltás új "
            "szakaszfájlt nyit, és az előző szakaszt lezárja, majd exportálja."
        )
        tooltips: tuple[tuple[QWidget, str], ...] = (
            (
                self._stage,
                self._stage_control_tooltip,
            ),
            (
                self._new_measurement_flow,
                "A BES pumpa kért mérési térfogatárama. Az érték átírása nem "
                "változtatja meg azonnal a pumpát; csak az Alkalmazás gomb indítja "
                "el a felügyelt STOP → FLOW → visszaolvasás → RUN műveletet. "
                "Kizárólag futó hardvermérésnél használható.",
            ),
            (
                self._mode,
                "Kézi vagy automata szelepvezérlést választ. A legördülő "
                "önmagában nem mozgatja a szelepet: futó mérésnél a következő "
                "vezérlési ciklustól hat, leállított mérésnél a következő indításra "
                "készíti elő a módot.",
            ),
            (
                self._source,
                "Automata módban ebből a nyomásértékből számol a PID: a "
                "besajtolópumpa visszajelzéséből vagy a vonali érzékelőből. Futó "
                "mérésnél a következő ciklustól hat. Nem elérhető forrás hibát és "
                "SAFE állapotot vált ki.",
            ),
            (
                self._manual_output,
                "Kézi módban a szelep kért kimenete 0–100% között. Futó mérésnél "
                "a következő vezérlési ciklusban kerül alkalmazásra. Automata "
                "módban az értéket a PID figyelmen kívül hagyja.",
            ),
            (
                self._setpoint,
                "Automata módban tartandó nyomáscél. Futó mérésnél a következő "
                "PID-ciklustól módosítja a szelep kimenetét. Kézi módban nem "
                "befolyásolja a szelepet.",
            ),
            (
                self._recording_interval,
                "A nyers mérési rekordok mentési időköze 1–3600 másodperc között. "
                "A módosítás futó mérésnél a következő mentési ütemezésben lép "
                "életbe; a biztonsági felügyelet és a PID ciklusideje nem változik.",
            ),
            (
                self._pid_profile,
                "Mentett PID-profilt választ. A profil kiválasztása betölti és "
                "azonnal alkalmazza a tárolt Kp, Ki, Kd, hatásirány, kimeneti "
                "határok és nyomásforrás értékeit. Az Egyéni beállítások nem tölt "
                "be profilt.",
            ),
            (
                self._kp,
                "Arányos erősítés: a pillanatnyi nyomáshiba közvetlen hatása a "
                "szelepkimenetre. Nagyobb érték erősebb reakciót okoz. Futó "
                "mérésnél automatikusan, a következő vezérlési ciklusban lép életbe.",
            ),
            (
                self._ki,
                "Integráló erősítés: a tartós nyomáshibát időben összegzi. Nagyobb "
                "érték gyorsabban szünteti meg a maradó hibát, de lengést okozhat. "
                "Futó mérésnél automatikusan frissül.",
            ),
            (
                self._kd,
                "Deriváló erősítés: a nyomás változási sebességére reagál, és "
                "csillapíthatja a gyors változásokat. Zajos jelre érzékeny. Futó "
                "mérésnél automatikusan frissül.",
            ),
            (
                self._direction,
                "A PID hatásiránya. Fordított módban a nagyobb szelepnyitás "
                "csökkenti a nyomást; ez a rendszer alapértelmezett fizikai "
                "modellje. Futó mérésnél automatikusan frissül; hardveren az irány "
                "fizikai validálása továbbra is kötelező.",
            ),
            (
                self._output_min,
                "A PID által kiadható legkisebb szelepérték. A PID nem vezérel e "
                "százalék alá. Futó mérésnél automatikusan frissül; a módosítás "
                "korlátozhatja a szabályozási tartományt.",
            ),
            (
                self._output_max,
                "A PID által kiadható legnagyobb szelepérték. A PID nem vezérel e "
                "százalék fölé. Futó mérésnél automatikusan frissül; a módosítás "
                "korlátozhatja a szabályozási tartományt.",
            ),
            (
                self._pid_deadband,
                "Nyomásholtsáv a célérték körül. Ezen belül a PID megtartja az "
                "előző szelepkimenetet. Nagyobb érték kevesebb mozgást, de nagyobb "
                "megengedett eltérést eredményez. Futó mérésnél automatikusan frissül.",
            ),
            (
                self._pid_output_rate,
                "A szelepkimenet megengedett legnagyobb változási sebessége. "
                "Kisebb érték lassabb és kíméletesebb szelepmozgást ad. Futó "
                "mérésnél automatikusan frissül.",
            ),
            (
                self._pid_filter_alpha,
                "A PID nyomásszűrőjének súlya. 1,0 esetén nincs simítás; kisebb "
                "érték erősebben simít, de késlelteti a reakciót. Futó mérésnél "
                "automatikusan frissül.",
            ),
            (
                self._pid_reversal_interval,
                "Két ellentétes irányú szelepkorrekció közötti minimális idő. "
                "Növelése csökkenti a gyors irányváltásokat. Futó mérésnél "
                "automatikusan frissül.",
            ),
            (
                self._pid_reversal_deadband,
                "Az ennél kisebb ellenirányú szelepkorrekciót a vezérlés elnyomja. "
                "Növelése mérsékli az apró oda-vissza mozgásokat. Futó mérésnél "
                "automatikusan frissül.",
            ),
            (
                self._pid_max_reversals,
                "Tíz másodpercen belül megengedett maximális szelepkorrekció-"
                "irányváltások száma. Túllépése VALVE_OSCILLATION hibát és SAFE "
                "állapotot vált ki. Futó mérésnél automatikusan frissül.",
            ),
        )
        for field, tooltip in tooltips:
            field.setToolTip(tooltip)

        self._apply_measurement_flow_button.setToolTip(
            "A megadott BES térfogatáramot felügyelt hardverművelettel alkalmazza. "
            "A művelet sikertelensége biztonsági leállítást vált ki."
        )
        self._apply_pid_button.setToolTip(
            "Érvényesíti a kézzel módosított PID-paramétereket. Futó mérésnél a "
            "következő vezérlési ciklus már az új beállításokat használja."
        )

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

    def _stored_run_mode(self) -> RunMode:
        stored = str(
            self._user_settings.value(
                "application/last_run_mode", RunMode.SIMULATION.value
            )
        )
        try:
            return RunMode(stored)
        except ValueError:
            return RunMode.SIMULATION

    def _setting_bool(self, key: str, default: bool) -> bool:
        value = self._user_settings.value(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _remember_run_mode(self, mode: RunMode) -> None:
        self._preferred_run_mode = mode
        self._user_settings.setValue("application/last_run_mode", mode.value)
        self._user_settings.sync()

    def _restore_startup_mode(self) -> None:
        if self._startup_mode_restore_started:
            return
        self._startup_mode_restore_started = True
        if self._project_selector_required:
            self._project_selector_prompted = True
            self._open_project_selector()
        if (
            not self._project_selector_required
            and self._preferred_run_mode is RunMode.HARDWARE
            and self._run_mode is RunMode.SIMULATION
        ):
            self._open_device_settings()

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
            message = "HARDVER – fizikai berendezés vezérlése és mérési adatmentés"
            title = "Éles mérési mód"
        else:
            self.setWindowTitle("AFKI EOR mérőrendszer — szimuláció")
            message = (
                "SZIMULÁCIÓ – nincs fizikai kimenet; az adatmentés aktív, "
                "szimulált eredetjelöléssel"
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
        if message == self._active_alarm_reason:
            return
        self._active_alarm_reason = message
        timestamp = format_hungarian_time(datetime.now(UTC))
        self._active_alarm_text = (
            f"⛔ LEÁLLÍTÁST OKOZÓ HIBA | {timestamp} | {message} | "
            "Automatikus művelet: pumpa STOP és szelep SAFE megkísérelve. | "
            "Következő lépés: ellenőrizze a fizikai rendszert, majd zárja be "
            "a riasztást. A program előtte friss biztonsági ellenőrzést végez."
        )
        self._refresh_alarm_banner()
        self._notify_user(
            "EOR biztonsági riasztás",
            self._active_alarm_text,
            critical=True,
            notification_key=f"alarm:{self._active_alarm_text}",
        )

    def _clear_active_alarm(self) -> None:
        self._active_alarm_text = "Nincs aktív riasztás"
        self._active_alarm_reason = None
        self._refresh_alarm_banner()
        if self._last_notification_key is not None and self._last_notification_key.startswith(
            "alarm:"
        ):
            self._last_notification_key = None

    def _refresh_alarm_banner(self) -> None:
        if not hasattr(self, "_alarm_label"):
            return
        if self._active_alarm_text == "Nincs aktív riasztás":
            self._alarm_label.clear()
            self._alarm_label.hide()
            self._alarm_close_button.hide()
            self._alarm_container.hide()
            return
        self._alarm_label.setText(self._active_alarm_text)
        self._alarm_label.setStyleSheet(
            "background:transparent;color:white;font-weight:800"
        )
        self._alarm_container.setStyleSheet(
            "background:#b00020;color:white;border-radius:6px"
        )
        self._alarm_close_button.setStyleSheet(
            "background:#7f0016;color:white;border:1px solid white;"
            "border-radius:5px;font-size:20px;font-weight:800;padding:0"
        )
        self._alarm_label.show()
        self._alarm_close_button.show()
        self._alarm_container.show()

    def _open_device_settings(self) -> None:
        self._open_settings_hub("devices")

    def _create_device_settings_page(
        self,
        hub: SettingsHubDialog,
        reconnect_state: dict[str, bool],
    ) -> QWidget:
        if self._runtime.running:
            return QLabel(
                "Az eszközbeállítások futó vagy szüneteltetett mérés közben "
                "nem módosíthatók."
            )
        reconnect_state["required"] = (
            self._devices.status.state is ApplicationState.READY
        )
        if self._devices.status.state is ApplicationState.READY:
            try:
                self._devices.disconnect()
                if self._pump_control is not None:
                    self._pump_control.observe_disconnected(*tuple(PumpRole))
            except Exception as error:
                self._show_error(
                    "Az eszközbeállítások előtt a meglévő kapcsolat nem "
                    f"zárható le biztonságosan: {error}"
                )
                return QLabel(f"A hardverkapcsolat nem zárható le: {error}")
        elif self._devices.status.state is not ApplicationState.IDLE:
            return QLabel(
                "Az eszközbeállítások csak leállított mérésből nyithatók meg."
            )
        dialog = DeviceSettingsDialog(
            PhysicalHardwareConnectionTester(diagnostics=self._diagnostics),
            settings=self._user_settings,
            current_mode=self._run_mode,
            device_profile=self._active_project_device_profile(),
            diagnostics=self._diagnostics,
            developer_mode=self._developer_mode,
            line_voltage_range=(
                self._line_voltage_min.value(),
                self._line_voltage_max.value(),
            ),
            differential_voltage_range=(
                self._delta_voltage_min.value(),
                self._delta_voltage_max.value(),
            ),
            functional_test_opener=self._open_functional_device_test,
            direct_control_opener=self._open_direct_device_control,
        )
        self._embedded_settings_dialog(dialog)

        def activate() -> None:
            if dialog.configuration is None:
                QTimer.singleShot(0, dialog.show)
                return
            self._save_active_project_device_profile(dialog.configuration)
            try:
                self._activate_hardware(
                    dialog.configuration,
                    dialog.connection_result,
                )
            except Exception as error:
                self._show_error(f"A hardvermód aktiválása sikertelen: {error}")
                QTimer.singleShot(0, dialog.show)
                return
            reconnect_state["activated"] = True
            hub.accept()

        dialog.accepted.connect(activate)
        dialog.rejected.connect(hub.reject)
        return dialog

    def _reconnect_active_mode(self) -> None:
        try:
            if self._devices.status.mode is RunMode.HARDWARE:
                answer = QMessageBox.question(
                    self,
                    "Fizikai hardver újraaktiválása",
                    "A hardvermód és az NI fizikai kimenet ismételt "
                    "engedélyezése után az alkalmazás fizikai eszközöket vezérelhet.\n\n"
                    "Engedélyezed a hardver újraaktiválását?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    self._set_all_connections(
                        "LEVÁLASZTVA — ÚJRAAKTIVÁLÁS SZÜKSÉGES",
                        ok=None,
                    )
                    self._refresh_state()
                    return
                if (
                    self._active_hardware_configuration is None
                    or self._hardware_daq is None
                ):
                    raise RuntimeError("az aktív NI hardverkonfiguráció hiányzik")
                _authorize_physical_hardware(
                    self._devices,
                    self._hardware_daq,
                    valve_output_enabled=(
                        self._active_hardware_configuration.valve_output_enabled
                    ),
                    hardware_confirmation=DeviceControlService.HARDWARE_CONFIRMATION,
                )
                if self._pump_control is not None:
                    self._pump_control.authorize(PumpControlService.AUTHORIZATION)
            self._devices.connect()
            if self._pump_control is not None:
                observe_connected = getattr(
                    self._pump_control, "observe_connected", None
                )
                if callable(observe_connected):
                    observe_connected(*tuple(PumpRole))
            self._set_all_connections("KAPCSOLÓDVA", ok=True)
        except Exception as error:
            self._show_error(
                "A korábbi hardverkapcsolat nem állítható vissza: "
                f"{error}. Nyisd meg újra az Eszközbeállításokat."
            )
        self._refresh_state()

    def _open_direct_device_control(
        self, hardware: HardwareConfiguration
    ) -> None:
        enabled_pumps = frozenset(
            role
            for role, enabled in (
                (PumpRole.JACKET, hardware.jacket_pump_enabled),
                (PumpRole.INJECTION, hardware.injection_pump_enabled),
            )
            if enabled
        )
        enabled_pressure_inputs = frozenset(
            key
            for key, enabled in (
                ("line_pressure", hardware.line_pressure_enabled),
                ("differential_pressure", hardware.differential_pressure_enabled),
            )
            if enabled
        )

        jacket = (
            PollingPump(
                open_isco_pump(
                    hardware.jacket_config(),
                    diagnostics=self._diagnostics,
                    diagnostic_category=DiagnosticCategory.JACKET_PUMP,
                ),
                name="jacket-direct",
                intervals=PumpTelemetrySettingsDialog.intervals(
                    self._user_settings,
                    command_timeout_seconds=hardware.serial_command_timeout_seconds,
                    command_attempts=hardware.serial_command_retries,
                ),
            )
            if hardware.jacket_pump_enabled
            else DisabledPump("jacket")
        )
        try:
            injection = (
                PollingPump(
                    open_isco_pump(
                        hardware.injection_config(),
                        diagnostics=self._diagnostics,
                        diagnostic_category=DiagnosticCategory.INJECTION_PUMP,
                    ),
                    name="injection-direct",
                    intervals=PumpTelemetrySettingsDialog.intervals(
                        self._user_settings,
                        command_timeout_seconds=(
                            hardware.serial_command_timeout_seconds
                        ),
                        command_attempts=hardware.serial_command_retries,
                    ),
                )
                if hardware.injection_pump_enabled
                else DisabledPump("injection")
            )
        except Exception:
            jacket.disconnect()
            raise

        daq = NidaqmxDataAcquisition(
            NidaqmxBackend(hardware.ni_terminal_configuration),
            hardware.ni_config(),
            self._diagnostics,
        )
        if hardware.valve_output_enabled:
            daq.authorize_output(NidaqmxDataAcquisition.HARDWARE_CONFIRMATION)
        actuator = AnalogValveActuator(
            daq,
            voltage_at_zero_percent=hardware.valve_zero_percent_voltage,
            voltage_at_hundred_percent=hardware.valve_hundred_percent_voltage,
        )
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
            channels=MeasurementChannels(
                line_pressure=(
                    "line_pressure" if hardware.line_pressure_enabled else None
                ),
                differential_pressure=(
                    "differential_pressure"
                    if hardware.differential_pressure_enabled
                    else None
                ),
            ),
            persistence_enabled=False,
        )
        direct_loop = ControlLoop(
            measurement=measurement,
            controller=ValveController(PidController(self._pid_parameters())),
            actuator=actuator,
        )
        direct_service = PumpControlService(
            jacket_pump=jacket,
            injection_pump=injection,
            minimum_jacket_margin_bar=self._minimum_margin.value(),
            diagnostics=self._diagnostics,
            manual_safety_check=lambda role, status: ManualSafetyMonitor.evaluate_pump(
                status,
                maximum_pressure_bar=(
                    self._max_jacket.value()
                    if role is PumpRole.JACKET
                    else self._max_injection.value()
                ),
            ).reasons,
            enforce_injection_margin=False,
        )
        direct_service.authorize(PumpControlService.AUTHORIZATION)
        try:
            PumpControlDialog(
                direct_service,
                direct_loop,
                lambda: self._stage.currentText(),
                enabled_pumps=enabled_pumps,
                enabled_pressure_inputs=enabled_pressure_inputs,
                valve_enabled=hardware.valve_output_enabled,
                parent=self,
            ).exec()
        finally:
            direct_service.shutdown_connections()
            direct_loop.close()

    def _open_functional_device_test(
        self,
        configuration: HardwareConfiguration,
        connection_result: ConnectionTestResult,
    ) -> None:
        if self._run_mode is not RunMode.HARDWARE:
            self._show_error("A funkcionális eszközteszt csak hardvermódban indítható.")
            return
        if configuration != self._active_hardware_configuration:
            self._show_error(
                "A módosított konfigurációt előbb aktiválni kell hardvermódban."
            )
            return
        if self._runtime.running or self._devices.status.state is not ApplicationState.IDLE:
            self._show_error("Funkcionális teszt csak leállított, IDLE rendszerből indítható.")
            return
        if (
            self._pump_control is None
            or self._hardware_daq is None
            or self._hardware_actuator is None
        ):
            self._show_error("A funkcionális teszt hardveradapterei nem érhetők el.")
            return
        try:
            self._devices.connect()
        except Exception as error:
            self._show_error(f"A funkcionális teszt csatlakoztatása sikertelen: {error}")
            return
        pump_control = self._pump_control
        daq = self._hardware_daq
        actuator = self._hardware_actuator
        report = DeviceTestReport.create(
            application_version=__version__,
            configuration_hash=configuration_hash(configuration.to_settings()),
        )
        report_path = (
            self._data_directory
            / "device-tests"
            / f"device-test-{report.test_id}.json"
        )
        session = FunctionalDeviceTestSession(
            run_mode=self._run_mode,
            application_state=lambda: self._devices.status.state,
            runtime_running=lambda: self._runtime.running,
            pumps_running=lambda: any(
                pump_control.state(role).running for role in PumpRole
            ),
            active_fault=lambda: (
                self._devices.status.state is ApplicationState.FAULT
                or self._active_alarm_text != "Nincs aktív riasztás"
            ),
            connection_result=connection_result,
            stop_pumps=pump_control.stop_all,
            set_safe_output=self._control_loop.request_safe_state,
            write_voltage=lambda voltage: daq.write_voltage(
                "valve_output", voltage
            ),
            write_valve_percent=actuator.write_percent,
            report=report,
            diagnostics=self._diagnostics,
            safe_output_voltage=configuration.safe_output_voltage,
            valve_dwell_seconds=float(
                str(self._user_settings.value("hardware/valve_test_dwell_seconds", 1.0))
            ),
            maximum_valve_rate_percent_per_second=float(
                str(
                    self._user_settings.value(
                        "hardware/valve_test_maximum_rate_percent_per_second",
                        10.0,
                    )
                )
            ),
            latch_fault=lambda reason: self._devices.emergency_stop(
                f"guided device test safe-state failure: {reason}"
            ),
        )

        def pump_stop_operation(role: PumpRole) -> DeviceTestResult:
            pump_control.stop(role)
            status = pump_control.statuses()[role]
            stopped = not pump_control.state(role).running
            return session.record_result(
                FunctionalTestDevice.JACKET_PUMP
                if role is PumpRole.JACKET
                else FunctionalTestDevice.INJECTION_PUMP,
                test_type="stop_and_status",
                passed=stopped,
                measurements={
                    "pressure_bar": status.pressure_bar,
                    "flow_ml_per_hour": status.flow_ml_per_hour,
                    "remaining_volume_ml": status.remaining_volume_ml,
                },
                failure_reason=None if stopped else "pump still reports RUN state",
            )

        def sensor_operation(
            device: FunctionalTestDevice,
            channel: str,
            calibration: LinearCalibration,
        ) -> DeviceTestResult:
            statistics = acquire_sensor_statistics(
                lambda: daq.read_voltage(channel),
                calibration,
                sample_rate_hz=10.0,
                duration_seconds=10.0,
            )
            maximum_noise = float(
                str(
                    self._user_settings.value(
                        "hardware/sensor_noise_limit_voltage", 0.05
                    )
                )
            )
            passed = statistics.standard_deviation_voltage <= maximum_noise
            return session.record_result(
                device,
                test_type="ten_second_stability",
                passed=passed,
                measurements=asdict(statistics),
                failure_reason=(
                    None
                    if passed
                    else "sensor idle variation exceeds configured limit"
                ),
            )

        operations = {
            FunctionalTestDevice.JACKET_PUMP: lambda: pump_stop_operation(
                PumpRole.JACKET
            ),
            FunctionalTestDevice.INJECTION_PUMP: lambda: pump_stop_operation(
                PumpRole.INJECTION
            ),
            FunctionalTestDevice.LINE_PRESSURE: lambda: sensor_operation(
                FunctionalTestDevice.LINE_PRESSURE,
                "line_pressure",
                LinearCalibration(*self._line_calibration_values()),
            ),
            FunctionalTestDevice.DIFFERENTIAL_PRESSURE: lambda: sensor_operation(
                FunctionalTestDevice.DIFFERENTIAL_PRESSURE,
                "differential_pressure",
                LinearCalibration(*self._delta_calibration_values()),
            ),
        }
        try:
            wizard = DeviceTestWizard(
                session,
                report_path=report_path,
                operations=operations,
                ao_tolerance_voltage=float(
                    str(
                        self._user_settings.value(
                            "hardware/ao_test_tolerance_voltage", 0.05
                        )
                    )
                ),
                parent=self,
            )
            wizard.exec()
            self._store_valve_direction_test_result(configuration, session)
            emergency_passed = any(
                result.device
                == FunctionalTestDevice.EMERGENCY_AND_COMMUNICATION.value
                and result.status is DeviceTestStatus.PASSED
                for result in report.device_results
            )
            self._user_settings.setValue(
                "hardware/cable_disconnect_test_completed", emergency_passed
            )
            self._user_settings.setValue(
                "hardware/emergency_stop_test_completed", emergency_passed
            )
            self._user_settings.setValue(
                "hardware/supervised_test_completed",
                report.overall_status is DeviceTestStatus.PASSED,
            )
            self._user_settings.sync()
        finally:
            if self._devices.status.state in (
                ApplicationState.READY,
                ApplicationState.RUNNING,
            ):
                self._devices.stop()
            if self._devices.status.state is not ApplicationState.IDLE:
                self._devices.disconnect()
            self._refresh_state()

    @staticmethod
    def _valve_direction_configuration_hash(
        configuration: HardwareConfiguration,
    ) -> str:
        """Identify the physical AO mapping whose direction was verified."""
        return configuration_hash(
            {
                "valve_output_channel": configuration.valve_output_channel,
                "safe_output_voltage": configuration.safe_output_voltage,
                "valve_zero_percent_voltage": (
                    configuration.valve_zero_percent_voltage
                ),
                "valve_hundred_percent_voltage": (
                    configuration.valve_hundred_percent_voltage
                ),
            }
        )

    def _store_valve_direction_test_result(
        self,
        configuration: HardwareConfiguration,
        session: FunctionalDeviceTestSession,
    ) -> None:
        valve_attempted = any(
            result.device == FunctionalTestDevice.HANBAY_VALVE.value
            for result in session.report.device_results
        )
        if session.valve_complete:
            self._user_settings.setValue(
                "hardware/valve_direction_validated", True
            )
            self._user_settings.setValue(
                "hardware/valve_direction_validation_hash",
                self._valve_direction_configuration_hash(configuration),
            )
        elif valve_attempted:
            self._user_settings.setValue(
                "hardware/valve_direction_validated", False
            )
            self._user_settings.remove(
                "hardware/valve_direction_validation_hash"
            )

    def _valve_direction_is_validated(self) -> bool:
        configuration = self._active_hardware_configuration
        if configuration is None or not self._setting_bool(
            "hardware/valve_direction_validated", False
        ):
            return False
        stored_hash = str(
            self._user_settings.value(
                "hardware/valve_direction_validation_hash", ""
            )
        )
        return stored_hash == self._valve_direction_configuration_hash(
            configuration
        )

    def _activate_hardware(
        self,
        configuration: HardwareConfiguration,
        connection_result: ConnectionTestResult | None = None,
    ) -> None:
        if self._pump_control is not None:
            cleanup_errors = self._pump_control.shutdown_connections()
            if cleanup_errors:
                raise RuntimeError(
                    "A korábbi hardverkapcsolatok lezárása sikertelen: "
                    + "; ".join(cleanup_errors)
                )
        pump_telemetry_intervals = PumpTelemetrySettingsDialog.intervals(
            self._user_settings,
            command_timeout_seconds=configuration.serial_command_timeout_seconds,
            command_attempts=configuration.serial_command_retries,
        )
        jacket = (
            PollingPump(
                open_isco_pump(
                    configuration.jacket_config(),
                    diagnostics=self._diagnostics,
                    diagnostic_category=DiagnosticCategory.JACKET_PUMP,
                ),
                name="jacket",
                intervals=pump_telemetry_intervals,
                diagnostics=self._diagnostics,
                diagnostic_category=DiagnosticCategory.JACKET_PUMP,
            )
            if configuration.jacket_pump_enabled
            else DisabledPump("jacket")
        )
        try:
            injection = (
                PollingPump(
                    open_isco_pump(
                        configuration.injection_config(),
                        diagnostics=self._diagnostics,
                        diagnostic_category=DiagnosticCategory.INJECTION_PUMP,
                    ),
                    name="injection",
                    intervals=pump_telemetry_intervals,
                    diagnostics=self._diagnostics,
                    diagnostic_category=DiagnosticCategory.INJECTION_PUMP,
                )
                if configuration.injection_pump_enabled
                else DisabledPump("injection")
            )
        except Exception:
            jacket.disconnect()
            raise
        daq = NidaqmxDataAcquisition(
            NidaqmxBackend(configuration.ni_terminal_configuration),
            configuration.ni_config(),
            self._diagnostics,
        )
        actuator = AnalogValveActuator(
            daq,
            voltage_at_zero_percent=configuration.valve_zero_percent_voltage,
            voltage_at_hundred_percent=configuration.valve_hundred_percent_voltage,
        )
        writer = self._new_project_measurement_writer(RunMode.HARDWARE)
        self._select_project_for_writer(writer, RunMode.HARDWARE)
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
            channels=MeasurementChannels(
                line_pressure=(
                    "line_pressure" if configuration.line_pressure_enabled else None
                ),
                differential_pressure=(
                    "differential_pressure"
                    if configuration.differential_pressure_enabled
                    else None
                ),
            ),
        )
        controller = ValveController(PidController(self._pid_parameters()))
        new_loop = ControlLoop(
            measurement=measurement, controller=controller, actuator=actuator
        )
        new_devices = DeviceControlService(
            jacket_pump=jacket,
            injection_pump=injection,
            daq=daq,
            mode=RunMode.HARDWARE,
            diagnostics=self._diagnostics,
        )
        _authorize_physical_hardware(
            new_devices,
            daq,
            valve_output_enabled=configuration.valve_output_enabled,
            hardware_confirmation=DeviceControlService.HARDWARE_CONFIRMATION,
        )
        pump_control = PumpControlService(
            jacket_pump=jacket,
            injection_pump=injection,
            minimum_jacket_margin_bar=self._minimum_margin.value(),
            diagnostics=self._diagnostics,
            manual_safety_check=lambda role, status: ManualSafetyMonitor.evaluate_pump(
                status,
                maximum_pressure_bar=(
                    self._max_jacket.value()
                    if role is PumpRole.JACKET
                    else self._max_injection.value()
                ),
            ).reasons,
            enforce_injection_margin=True,
        )
        pump_control.authorize(PumpControlService.AUTHORIZATION)
        try:
            new_devices.connect()
            _observe_hardware_pump_connections(
                pump_control,
                jacket_enabled=configuration.jacket_pump_enabled,
                injection_enabled=configuration.injection_pump_enabled,
            )
        except Exception:
            with suppress(Exception):
                new_devices.disconnect()
            pump_control.shutdown_connections()
            new_loop.close()
            raise
        self._control_loop.close()
        self._control_loop = new_loop
        self._measurement_writer = writer
        self._devices = new_devices
        self._pump_control = pump_control
        self._hardware_status_generation += 1
        self._hardware_status_active = False
        self._last_hardware_status_record = None
        self._active_hardware_configuration = configuration
        self._active_pump_telemetry_intervals = pump_telemetry_intervals
        self._hardware_connection_result = connection_result
        self._hardware_daq = daq
        self._hardware_actuator = actuator
        self._runtime = self._make_runtime(new_loop)
        self._run_mode = RunMode.HARDWARE
        self._remember_run_mode(RunMode.HARDWARE)
        self._set_line_pressure_source_available(configuration.line_pressure_enabled)
        self._sync_simulation_mode_action()
        self._simulation_settings_action.setEnabled(False)
        self._diagnostics.emit(
            DiagnosticCategory.SYSTEM,
            "MODE",
            "hardware mode activated",
        )
        self._user_settings.setValue(
            "hardware/last_test_succeeded",
            connection_result is not None
            and connection_result.successful_for(
                configuration.enabled_test_devices()
            ),
        )
        self._clear_active_alarm()
        self._refresh_mode_label()
        self._set_all_connections("KAPCSOLÓDVA", ok=True)
        self._refresh_state()


    def _activate_simulation(
        self,
        *,
        preserve_preferred_mode: bool = False,
        ignore_cleanup_errors: bool = False,
    ) -> None:
        if self._run_mode is RunMode.SIMULATION:
            return
        self._set_line_pressure_source_available(True)
        if self._devices.status.state is ApplicationState.READY:
            try:
                self._devices.disconnect()
                if self._pump_control is not None:
                    self._pump_control.observe_disconnected(*tuple(PumpRole))
            except Exception as error:
                self._show_error(
                    f"A hardverkapcsolat nem zárható le a módváltáshoz: {error}"
                )
                return
        elif self._devices.status.state is not ApplicationState.IDLE:
            self._show_error(
                "Szimulációs módra futó mérés vagy aktív hiba mellett nem lehet váltani."
            )
            return

        if self._pump_control is not None:
            cleanup_errors = self._pump_control.shutdown_connections()
            if cleanup_errors and not ignore_cleanup_errors:
                self._show_error(
                    "A szimuláció nem aktiválható, mert a "
                    "hardverkapcsolatok biztonságos lezárása sikertelen: "
                    + "; ".join(cleanup_errors)
                )
                return

        jacket = SimulatedPump(pressure_bar=120.0, flow_ml_per_hour=10.0)
        injection = SimulatedPump(
            pressure_bar=100.0,
            flow_ml_per_hour=10.0,
            remaining_volume_ml=260.0,
        )
        daq = SimulatedDataAcquisition()
        daq.inputs.update(line_pressure=2.0, differential_pressure=1.5)
        actuator = SimulatedValveActuator()
        writer = self._new_project_measurement_writer(RunMode.SIMULATION)
        self._select_project_for_writer(writer, RunMode.SIMULATION)
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
            persistence_enabled=True,
        )
        controller = ValveController(PidController(self._pid_parameters()))
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
        self._hardware_status_generation += 1
        self._hardware_status_active = False
        self._last_hardware_status_record = None
        self._simulation_jacket = jacket
        self._simulation_injection = injection
        self._simulation_daq = daq
        self._devices.connect()
        self._pump_control = self._make_simulation_pump_control()
        self._active_hardware_configuration = None
        self._active_pump_telemetry_intervals = None
        self._hardware_connection_result = None
        self._hardware_daq = None
        self._hardware_actuator = None
        self._runtime = self._make_runtime(new_loop)
        self._run_mode = RunMode.SIMULATION
        if preserve_preferred_mode:
            self._remember_run_mode(RunMode.HARDWARE)
        else:
            self._remember_run_mode(RunMode.SIMULATION)
        self._sync_simulation_mode_action()
        self._simulation_settings_action.setEnabled(self._developer_mode)
        self._diagnostics.emit(
            DiagnosticCategory.SYSTEM, "MODE", "simulation mode activated"
        )
        self._refresh_mode_label()
        self._set_all_connections("LEVÁLASZTVA", ok=None)
        self._refresh_state()

    def _make_simulation_pump_control(self) -> PumpControlService:
        jacket = self._simulation_jacket
        injection = self._simulation_injection
        if jacket is None or injection is None:
            raise RuntimeError("a szimulált pumpák nem érhetők el")
        service = PumpControlService(
            jacket_pump=jacket,
            injection_pump=injection,
            minimum_jacket_margin_bar=self._minimum_margin.value(),
            diagnostics=self._diagnostics,
            enforce_injection_margin=True,
        )
        service.authorize(PumpControlService.AUTHORIZATION)
        service.observe_connected(*tuple(PumpRole))
        return service

    def _set_line_pressure_source_available(self, available: bool) -> None:
        line_index = self._source.findData(PressureSource.LINE_SENSOR)
        if available and line_index < 0:
            self._source.addItem("Vonali nyomásmérő", PressureSource.LINE_SENSOR)
        elif not available and line_index >= 0:
            self._source.removeItem(line_index)
            injection_index = self._source.findData(PressureSource.INJECTION_PUMP)
            self._source.setCurrentIndex(max(0, injection_index))

    def _make_runtime(self, control_loop: ControlLoop) -> BackgroundControlRunner:
        control_interval = ControlCycleSettingsDialog.setting_value(
            self._user_settings,
            "developer/control_interval_seconds",
            ControlCycleSettingsDialog.DEFAULT_INTERVAL_SECONDS,
        )
        watchdog_tolerance = ControlCycleSettingsDialog.setting_value(
            self._user_settings,
            "developer/watchdog_tolerance_seconds",
            ControlCycleSettingsDialog.DEFAULT_WATCHDOG_TOLERANCE_SECONDS,
        )
        return BackgroundControlRunner(
            control_loop,
            control_interval_seconds=control_interval,
            watchdog_tolerance_seconds=watchdog_tolerance,
            on_cycle=self._runtime_bridge.cycle_completed.emit,
            on_fault=self._runtime_bridge.fault_raised.emit,
        )

    def _restore_control_settings(self) -> None:
        # Version 1 records the verified physical relationship:
        # 0% = closed, 100% = open, therefore the pressure PID is reverse acting.
        # Migrate the previously shipped DIRECT default once without modifying
        # separately saved PID profiles.
        if self._user_settings.value("pid/valve_direction_model_version") is None:
            self._user_settings.setValue(
                "pid/direction", ControlDirection.REVERSE.value
            )
            self._user_settings.setValue("pid/valve_direction_model_version", 1)
            self._user_settings.sync()
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
            (self._pid_deadband, "pid/deadband_bar"),
            (self._pid_output_rate, "pid/output_rate_percent_per_second"),
            (self._pid_filter_alpha, "pid/filter_alpha"),
            (self._pid_reversal_interval, "pid/reversal_interval_seconds"),
            (self._pid_reversal_deadband, "pid/reversal_deadband_percent"),
            (self._pid_max_reversals, "pid/maximum_reversals"),
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
            "pid/deadband_bar": self._pid_deadband.value(),
            "pid/output_rate_percent_per_second": self._pid_output_rate.value(),
            "pid/filter_alpha": self._pid_filter_alpha.value(),
            "pid/reversal_interval_seconds": self._pid_reversal_interval.value(),
            "pid/reversal_deadband_percent": self._pid_reversal_deadband.value(),
            "pid/maximum_reversals": self._pid_max_reversals.value(),
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
        if self._devices.status.state is ApplicationState.READY:
            self._refresh_state()
            return
        try:
            if (
                self._run_mode is RunMode.HARDWARE
                and not self._devices.status.hardware_authorized
            ):
                answer = QMessageBox.question(
                    self,
                    "Fizikai hardver újracsatlakoztatása",
                    "A COM-portok újranyitásához és a fizikai hardver ismételt "
                    "engedélyezéséhez kezelői jóváhagyás szükséges.\n\n"
                    "Engedélyezed a fizikai hardver újracsatlakoztatását?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
                if (
                    self._active_hardware_configuration is not None
                    and self._hardware_daq is not None
                ):
                    _authorize_physical_hardware(
                        self._devices,
                        self._hardware_daq,
                        valve_output_enabled=(
                            self._active_hardware_configuration.valve_output_enabled
                        ),
                        hardware_confirmation=(
                            DeviceControlService.HARDWARE_CONFIRMATION
                        ),
                    )
                else:
                    self._devices.authorize_hardware(
                        DeviceControlService.HARDWARE_CONFIRMATION
                    )
                if self._pump_control is not None:
                    self._pump_control.authorize(PumpControlService.AUTHORIZATION)
            self._devices.connect()
            if self._pump_control is not None:
                self._pump_control.observe_connected(*tuple(PumpRole))
        except Exception as error:
            self._show_error(str(error))
        self._refresh_state()

    def _disconnect_devices(self) -> None:
        try:
            if self._runtime.running:
                self._runtime.stop()
            self._devices.disconnect()
            if self._pump_control is not None:
                self._pump_control.observe_disconnected(*tuple(PumpRole))
                self._pump_control.revoke()
        except Exception as error:
            self._show_error(str(error))
        self._set_all_connections("LEVÁLASZTVA", ok=None)
        self._refresh_state()

    def _dismiss_alarm(self) -> None:
        if self._active_alarm_text == "Nincs aktív riasztás":
            return
        hardware_fault_acknowledged = False
        try:
            if self._devices.status.state is ApplicationState.FAULT:
                decision = self._control_loop.verify_safe_fault_clear(
                    active_stage=self._stage.currentText() or "Biztonsági ellenőrzés"
                )
                if not decision.safe:
                    self._show_error(
                        "A riasztás nem zárható be, mert a friss biztonsági "
                        "ellenőrzés továbbra is hibát jelez:\n- "
                        + "\n- ".join(decision.reasons)
                    )
                    return
                self._devices.acknowledge_fault()
                hardware_fault_acknowledged = self._run_mode is RunMode.HARDWARE
                if self._run_mode is RunMode.SIMULATION:
                    self._set_all_connections("SZIMULÁCIÓ — KÉSZ", ok=True)
            self._clear_active_alarm()
        except Exception as error:
            self._show_error(
                "A riasztás nem zárható be biztonságosan: "
                f"{type(error).__name__}: {error}"
            )
        self._refresh_state()
        if hardware_fault_acknowledged:
            QTimer.singleShot(0, self._refresh_active_hardware_status)

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
            self._start_pending_stage_exports()
            stage = self._projects.get_stage(stage_id)
            details: list[str] = []
            if stage.fluid:
                details.append(stage.fluid)
            if stage.target_pressure_bar is not None:
                self._setpoint.setValue(stage.target_pressure_bar)
                details.append(f"cél: {stage.target_pressure_bar:.3f} bar")
            if stage.target_flow_ml_per_hour is not None:
                details.append(f"áram: {stage.target_flow_ml_per_hour:g} ml/h")
            self._active_stage_label.setToolTip("; ".join(details))
            detail_text = "; ".join(details)
            base_tooltip = getattr(self, "_stage_control_tooltip", "")
            self._stage.setToolTip(
                f"{base_tooltip}\n\nAktuális szakasz: {detail_text}"
                if detail_text
                else base_tooltip
            )
        else:
            self._active_stage_label.setToolTip("")
            self._stage.setToolTip(
                getattr(self, "_stage_control_tooltip", "")
            )
        if self._measurement_tabs.currentWidget() is self._history_view:
            if isinstance(stage_id, int):
                self._refresh_measurement_history()
            else:
                self._history_view.set_sources(())
        self._update_runtime_settings()
        if hasattr(self, "_start_button"):
            self._refresh_state()

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
        self._refresh_state()

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
        if self._loading_pid_profile:
            return
        if isinstance(self._pid_profile.currentData(), int):
            self._pid_profile.blockSignals(True)
            self._pid_profile.setCurrentIndex(0)
            self._pid_profile.blockSignals(False)
            self._delete_pid_profile_button.setEnabled(False)
        if self._runtime.running:
            self._pid_application_status.setText(
                "PID-frissítés előkészítve a következő vezérlési ciklushoz…"
            )
            self._pid_application_status.setStyleSheet(
                "color:#9a6700;font-weight:700"
            )
            self._pid_live_update_timer.start()

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

    def _apply_pid(self, _checked: bool = False) -> None:
        del _checked
        try:
            parameters = self._pid_parameters()
        except ValueError as error:
            self._pid_application_status.setText(
                f"A módosítás nem alkalmazható: {error}"
            )
            self._pid_application_status.setStyleSheet(
                "color:#b00020;font-weight:700"
            )
            self._show_error(str(error))
            return
        if self._runtime.running:
            self._runtime.update_pid(parameters)
            message = "PID-frissítés sorba állítva a következő vezérlési ciklushoz."
        else:
            self._control_loop.configure_pid(parameters)
            message = "PID-beállítások alkalmazva; a következő mérés ezt használja."
        self._pid_application_status.setText(message)
        self._pid_application_status.setStyleSheet(
            "color:#1b7f3a;font-weight:700"
        )

    def _apply_live_pid_update(self) -> None:
        try:
            parameters = self._pid_parameters()
        except ValueError as error:
            self._pid_application_status.setText(
                "Az érvénytelen átmeneti érték nem került alkalmazásra; az előző "
                f"PID maradt aktív. Részlet: {error}"
            )
            self._pid_application_status.setStyleSheet(
                "color:#b00020;font-weight:700"
            )
            return
        if not self._runtime.running:
            self._pid_application_status.setText(
                "A mérés leállt; a módosítás a következő indításkor lép életbe."
            )
            self._pid_application_status.setStyleSheet(
                "color:#66788a;font-weight:700"
            )
            return
        self._runtime.update_pid(parameters)
        self._pid_application_status.setText(
            "PID-frissítés sorba állítva; a következő vezérlési ciklus alkalmazza."
        )
        self._pid_application_status.setStyleSheet(
            "color:#1b7f3a;font-weight:700"
        )
        self._diagnostics.emit(
            DiagnosticCategory.RUNTIME,
            "PID_LIVE_UPDATE",
            "live PID parameters queued for next control cycle",
            level="WARNING" if self._run_mode is RunMode.HARDWARE else "INFO",
        )

    def _pid_parameters(self) -> PidParameters:
        return PidParameters(
            self._kp.value(),
            self._ki.value(),
            self._kd.value(),
            output_min_percent=self._output_min.value(),
            output_max_percent=self._output_max.value(),
            direction=ControlDirection(self._direction.currentData()),
            deadband_bar=self._pid_deadband.value(),
            maximum_output_rate_percent_per_second=self._pid_output_rate.value(),
            measurement_filter_alpha=self._pid_filter_alpha.value(),
            minimum_reversal_interval_seconds=self._pid_reversal_interval.value(),
            reversal_deadband_percent=self._pid_reversal_deadband.value(),
            maximum_reversals=self._pid_max_reversals.value(),
        )

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
            if self._pump_control is not None:
                self._pump_control.set_minimum_jacket_margin_bar(
                    self._minimum_margin.value()
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
            "devices": self._active_project_device_profile(),
        }

    def _active_project_device_profile(self) -> dict[str, bool]:
        project_id = self._project.currentData()
        if isinstance(project_id, int):
            return project_device_profile(
                self._projects.get_project(project_id).configuration
            )
        if self._active_hardware_configuration is not None:
            return hardware_device_profile(self._active_hardware_configuration)
        return {key: True for key, _label in PROJECT_DEVICE_FIELDS}

    def _save_active_project_device_profile(
        self, hardware: HardwareConfiguration
    ) -> None:
        project_id = self._project.currentData()
        if not isinstance(project_id, int):
            return
        project = self._projects.get_project(project_id)
        configuration = dict(project.configuration)
        configuration["devices"] = hardware_device_profile(hardware)
        self._projects.update_project_configuration(project_id, configuration)

    def _runtime_settings(self) -> RuntimeSettings:
        return RuntimeSettings(
            active_stage=self._stage.currentText(),
            mode=ControlMode(self._mode.currentData()),
            manual_output_percent=self._manual_output.value(),
            source=PressureSource(self._source.currentData()),
            setpoint_bar=self._setpoint.value(),
            recording_interval_seconds=float(self._recording_interval.value()),
        )

    def _hardware_matches_active_project(self) -> bool:
        if self._run_mode is not RunMode.HARDWARE:
            return True
        if self._active_hardware_configuration is None:
            return False
        return hardware_device_profile(
            self._active_hardware_configuration
        ) == self._active_project_device_profile()

    def _update_runtime_settings(self, *_args: object) -> None:
        if self._runtime.running and self._stage.currentData() is not None:
            try:
                self._runtime.update_settings(self._runtime_settings())
            except ValueError as error:
                self._handle_runtime_fault(str(error))

    def _start(self) -> None:
        if self._devices.status.state is ApplicationState.READY:
            self._start_measurement_preflight(start_measurement=True)
            return
        if (
            self._devices.status.state is not ApplicationState.RUNNING
            or self._devices.status.measurement
            is not MeasurementState.WAITING_CONFIRMATION
            or self._pending_measurement_pump_plan is None
        ):
            self._show_error(
                "A mérés csak Kész vagy Előkészítve állapotból indítható."
            )
            self._refresh_state()
            return
        self._start_measurement_runtime()

    def _prepare(self) -> None:
        self._start_measurement_preflight(start_measurement=False)

    def _start_measurement_preflight(self, *, start_measurement: bool) -> None:
        if self._stage.currentData() is None:
            self._show_error("A méréshez válassz projektet és mérési szakaszt.")
            return
        if not self._hardware_matches_active_project():
            self._show_error(
                "A projekt eszközprofilja eltér az aktív hardverprofiltól. "
                "Nyisd meg az Eszközbeállításokat, majd aktiváld a projekthez "
                "hozzáadott eszközöket."
            )
            self._refresh_state()
            return
        try:
            if self._current_project_file() is None:
                raise RuntimeError("A projektspecifikus mérési fájl nem érhető el.")
        except Exception as error:
            self._show_error(f"A mérés nem indítható: {error}")
            self._refresh_state()
            return
        if self._preflight_active:
            return
        self._preflight_starts_measurement = start_measurement
        self._preflight_active = True
        self._refresh_state()
        active_stage = self._stage.currentText()

        def execute() -> None:
            try:
                result = (
                    self._control_loop.observe_pump_startup_once(
                        active_stage=active_stage
                    )
                    if self._run_mode is RunMode.HARDWARE and not start_measurement
                    else self._control_loop.observe_once(active_stage=active_stage)
                )
            except Exception as error:
                self._runtime_bridge.preflight_failed.emit(str(error))
            else:
                self._runtime_bridge.preflight_completed.emit(result)

        Thread(target=execute, name="eor-measurement-preflight", daemon=True).start()

    def _measurement_preflight_completed(self, result: object) -> None:
        self._preflight_active = False
        start_measurement = self._preflight_starts_measurement
        if not isinstance(result, MeasurementRecord):
            self._measurement_preflight_failed("érvénytelen előellenőrzési eredmény")
            return
        report = self._build_preflight_report(result)
        dialog = PreflightDialog(
            report,
            self,
            accept_text=(
                "Mérés indítása"
                if start_measurement
                else "Tovább az előkészítéshez"
            ),
        )
        accepted = (
            dialog.exec() == QDialog.DialogCode.Accepted
            if self.isVisible()
            else report.can_start
        )
        if not accepted:
            self._preflight_starts_measurement = False
            self._refresh_state()
            return
        if start_measurement:
            self._begin_direct_measurement_after_preflight()
        else:
            self._begin_measurement_after_preflight()

    def _begin_direct_measurement_after_preflight(self) -> None:
        """Start acquisition and valve control without changing either pump."""
        self._preflight_starts_measurement = False
        self._pending_measurement_pump_plan = None
        try:
            # This only advances the application state. With simulated device
            # autostart disabled it sends no FLOW/PRESS/RUN/STOP pump command.
            self._devices.start(start_simulated_devices=False)
            self._devices.set_measurement_state(MeasurementState.WAITING_CONFIRMATION)
        except Exception as error:
            self._show_error(f"A mérés nem indítható: {error}")
            self._refresh_state()
            return
        self._start_measurement_runtime()

    def _begin_measurement_after_preflight(self) -> None:
        plan: MeasurementPumpPlan | None = None
        confirmation = ""
        if self._run_mode in (RunMode.HARDWARE, RunMode.SIMULATION):
            dialog = MeasurementPumpStartupDialog(
                self._default_measurement_pump_plan(),
                maximum_jacket_pressure_bar=self._max_jacket.value(),
                maximum_injection_pressure_bar=self._max_injection.value(),
                minimum_jacket_margin_bar=self._minimum_margin.value(),
                parent=self,
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                self._refresh_state()
                return
            plan = dialog.plan()
            confirmation = dialog.confirmation_text()
            self._remember_measurement_pump_plan(plan)
            self._pending_measurement_pump_plan = plan
        try:
            runtime_settings = self._runtime_settings()
            self._devices.start(start_simulated_devices=False)
            self._devices.set_measurement_state(MeasurementState.PREPARING)
            if plan is None:
                raise RuntimeError("az előkészítési pumpaterv nem érhető el")
            pump_control = self._pump_control
            if pump_control is None:
                raise RuntimeError("a pumpavezérlés nem érhető el")
            self._preflight_active = True
            cancel_event = Event()
            self._pump_preparation_cancel_event = cancel_event
            self._pump_preparation_dialog = PumpPreparationProgressDialog(
                cancel_event,
                self,
            )
            self._pump_preparation_dialog.show()
            active_stage = runtime_settings.active_stage
            control_interval_seconds = self._runtime.control_interval_seconds
            control_watchdog_tolerance_seconds = (
                self._runtime.watchdog_tolerance_seconds
            )

            def execute() -> None:
                def startup_safety_check() -> tuple[str, ...]:
                    if self._devices.status.state is not ApplicationState.RUNNING:
                        return ("measurement pump startup was cancelled",)
                    record = self._control_loop.observe_pump_startup_once(
                        active_stage=active_stage
                    )
                    self._runtime_bridge.pump_startup_progress.emit(record)
                    return record.safety_reasons

                try:
                    pump_control.prepare_measurement_pumps(
                        plan,
                        timing=PumpControlTiming(
                            control_interval_seconds=control_interval_seconds,
                            watchdog_tolerance_seconds=(
                                control_watchdog_tolerance_seconds
                            ),
                        ),
                        confirmation=confirmation,
                        startup_safety_check=startup_safety_check,
                        progress_callback=(
                            self._runtime_bridge.pump_preparation_progress.emit
                        ),
                        cancel_check=cancel_event.is_set,
                    )
                except Exception as error:
                    self._runtime_bridge.pump_startup_failed.emit(str(error))
                else:
                    self._runtime_bridge.pump_startup_completed.emit()

            Thread(
                target=execute,
                name="eor-measurement-pump-startup",
                daemon=True,
            ).start()
        except Exception as error:
            self._show_error(f"A mérés nem indítható: {error}")
        self._refresh_state()

    def _complete_measurement_start(self, settings: RuntimeSettings) -> None:
        self._diagnostic_measurement_id = uuid4().hex
        section_id = self._stage.currentData()
        self._diagnostic_section_id = (
            section_id if isinstance(section_id, int) else None
        )
        self._control_loop.reset_injected_volume_tracking()
        self._measurement_time_origin = None
        self._last_cycle_result = None
        self._times.clear()
        self._jacket_pressures.clear()
        self._injection_pressures.clear()
        self._injection_flows.clear()
        self._line_pressures.clear()
        self._alarm_points.clear()
        self._alarm_scatter.setData([])
        self._runtime.start(settings)
        # RUNNING is the terminal state of startup: the acquisition/control
        # worker must be active before the public measurement state advances.
        self._devices.set_measurement_state(MeasurementState.RUNNING)

    def _measurement_pump_startup_progress(self, result: object) -> None:
        if not isinstance(result, MeasurementRecord):
            return
        status = self._devices.status
        if (
            not self._preflight_active
            or status.state is not ApplicationState.RUNNING
            or status.measurement is not MeasurementState.PREPARING
        ):
            return
        self._last_hardware_status_record = result
        self._apply_idle_hardware_record(result)

    def _pump_preparation_progress(self, result: object) -> None:
        if not isinstance(result, PumpPreparationProgress):
            return
        dialog = self._pump_preparation_dialog
        if dialog is not None:
            dialog.update_progress(result)

    def _close_pump_preparation_dialog(self) -> None:
        dialog = self._pump_preparation_dialog
        self._pump_preparation_dialog = None
        self._pump_preparation_cancel_event = None
        if dialog is not None:
            dialog.accept()

    def _measurement_pump_startup_completed(self) -> None:
        self._close_pump_preparation_dialog()
        if (
            not self._preflight_active
            or self._devices.status.state is not ApplicationState.RUNNING
        ):
            return
        plan = self._pending_measurement_pump_plan
        pump_control = self._pump_control
        if plan is None or pump_control is None:
            self._measurement_pump_startup_failed(
                "az előkészített pumpaállapot nem érhető el"
            )
            return
        self._devices.set_measurement_state(MeasurementState.WAITING_CONFIRMATION)
        self._preflight_active = False
        self._refresh_state()

    def _start_measurement_runtime(self) -> None:
        """Start valve control and recording from prepared or manual pump state."""
        self._preflight_active = True
        self._refresh_state()
        try:
            readiness = self._control_loop.observe_once(
                active_stage=self._stage.currentText()
            )
            if readiness.safety_reasons:
                raise PermissionError(
                    "a friss PID-bemenet biztonsági hibás: "
                    + "; ".join(readiness.safety_reasons)
                )
            if (
                PressureSource(self._source.currentData())
                is PressureSource.LINE_SENSOR
                and readiness.snapshot.line_pressure_bar is None
            ):
                raise ValueError("a kiválasztott vonali PID-forrás nem elérhető")
            # Reconfigure/reset immediately before enabling the runtime. This
            # preserves the configured rate/output limits and prevents startup
            # windup while the operator dialog is open.
            self._control_loop.configure_pid(self._pid_parameters())
            # Measurement start owns only valve control and acquisition. Pump
            # state is the result of preparation or explicit manual operation;
            # do not issue STOP/FLOW/RUN here.
            self._complete_measurement_start(self._runtime_settings())
        except Exception as error:
            if self._run_mode is RunMode.HARDWARE:
                self._handle_critical_hardware_fault(
                    f"a mérési runtime indítása sikertelen: {error}"
                )
                return
            self._devices.emergency_stop(f"runtime startup failed: {error}")
            self._show_error(f"A mérés nem indítható: {error}")
        self._pending_measurement_pump_plan = None
        self._preflight_active = False
        self._refresh_state()

    def _apply_running_measurement_flow(self) -> None:
        if (
            self._run_mode is not RunMode.HARDWARE
            or self._devices.status.measurement is not MeasurementState.RUNNING
            or self._pump_control is None
        ):
            self._show_error("A BES flow csak futó hardvermérés közben módosítható.")
            return
        requested = self._new_measurement_flow.value()
        self._apply_measurement_flow_button.setEnabled(False)
        pump_control = self._pump_control

        def execute() -> None:
            try:
                applied = pump_control.apply_measurement_flow(requested)
            except Exception as error:
                self._runtime_bridge.flow_change_failed.emit(str(error))
            else:
                self._runtime_bridge.flow_change_completed.emit(applied)

        Thread(target=execute, name="eor-measurement-flow-change", daemon=True).start()

    def _measurement_flow_change_completed(self, applied: float) -> None:
        previous = self._applied_measurement_flow_ml_per_hour
        self._applied_measurement_flow_ml_per_hour = applied
        self._current_measurement_flow.setText(f"{applied:.3f} ml/h")
        self._new_measurement_flow.setValue(applied)
        self._apply_measurement_flow_button.setEnabled(True)
        unique_event_id = uuid4().hex
        self._diagnostics.emit_event(
            DiagnosticCategory.INJECTION_PUMP,
            "MEASUREMENT_FLOW_CHANGED",
            fields={
                "unique_event_id": unique_event_id,
                "previous_flow_ml_per_hour": previous,
                "target_flow_ml_per_hour": applied,
                "measurement_state": self._devices.status.measurement.value,
                "action": "operator flow change",
                "action_result": "SUCCESS",
            },
            direction="OPERATOR",
            level="INFO",
        )
        event = self._create_measurement_event(
            event_id=unique_event_id,
            event_type="operator",
            severity="operator",
            error_code="MEASUREMENT_FLOW_CHANGED",
            description="BES mérési flow kezelő által módosítva",
            affected_hardware="injection_pump",
            current_flow_ml_per_hour=previous,
            target_flow_ml_per_hour=applied,
        )
        self._store_measurement_event(event)
        self._add_current_event_marker(event)

    def _measurement_flow_change_failed(self, message: str) -> None:
        self._apply_measurement_flow_button.setEnabled(True)
        previous = self._applied_measurement_flow_ml_per_hour
        if previous is not None:
            self._current_measurement_flow.setText(f"{previous:.3f} ml/h")
        self._handle_critical_hardware_fault(
            f"a BES mérési flow módosítása sikertelen: {message}"
        )

    def _measurement_pump_startup_failed(self, message: str) -> None:
        self._close_pump_preparation_dialog()
        if not self._preflight_active:
            return
        self._preflight_active = False
        if self._run_mode is RunMode.HARDWARE:
            self._handle_critical_hardware_fault(
                f"a pumpák indítása sikertelen: {message}"
            )
            return
        if self._devices.status.state is ApplicationState.RUNNING:
            try:
                self._devices.emergency_stop(f"pump startup failed: {message}")
            except Exception as stop_error:
                message = f"{message}; biztonsági leállítási hiba: {stop_error}"
        self._show_error(f"A pumpák indítása sikertelen: {message}")
        self._refresh_state()

    def _default_measurement_pump_plan(self) -> MeasurementPumpPlan:
        stage_id = self._stage.currentData()
        stage_flow = 0.0
        if isinstance(stage_id, int):
            stored_stage_flow = self._projects.get_stage(
                stage_id
            ).target_flow_ml_per_hour
            if stored_stage_flow is not None and stored_stage_flow > 0.0:
                stage_flow = stored_stage_flow

        def stored_float(key: str, fallback: float) -> float:
            try:
                value = float(str(self._user_settings.value(key, fallback)))
            except (TypeError, ValueError):
                return fallback
            return value if isfinite(value) and value >= 0.0 else fallback

        default_jacket_pressure = min(
            self._setpoint.value() + self._minimum_margin.value(),
            self._max_jacket.value(),
        )
        default_injection_pressure = min(
            self._setpoint.value(),
            self._max_injection.value(),
        )
        return MeasurementPumpPlan(
            jacket_target_pressure_bar=stored_float(
                "pump_startup/jacket_target_pressure_bar",
                default_jacket_pressure,
            ),
            jacket_buildup_flow_ml_per_hour=stored_float(
                "pump_startup/jacket_buildup_flow_ml_per_hour",
                0.0,
            ),
            injection_start_pressure_bar=stored_float(
                "pump_startup/injection_start_pressure_bar",
                default_injection_pressure,
            ),
            injection_startup_flow_ml_per_hour=stored_float(
                "pump_startup/injection_startup_flow_ml_per_hour",
                stored_float(
                    "pump_startup/injection_target_flow_ml_per_hour",
                    stage_flow,
                ),
            ),
            injection_measurement_flow_ml_per_hour=stored_float(
                "pump_startup/injection_measurement_flow_ml_per_hour",
                stage_flow,
            ),
            jacket_pressure_limit_bar=stored_float(
                "pump_startup/jacket_pressure_limit_bar",
                self._max_jacket.value(),
            ),
            injection_pressure_limit_bar=stored_float(
                "pump_startup/injection_pressure_limit_bar",
                self._max_injection.value(),
            ),
            margin_stability_seconds=stored_float(
                "pump_startup/margin_stability_seconds",
                2.0,
            ),
        )

    def _remember_measurement_pump_plan(self, plan: MeasurementPumpPlan) -> None:
        values = {
            "pump_startup/jacket_target_pressure_bar": (
                plan.jacket_target_pressure_bar
            ),
            "pump_startup/jacket_buildup_flow_ml_per_hour": (
                plan.jacket_buildup_flow_ml_per_hour
            ),
            "pump_startup/injection_start_pressure_bar": (
                plan.injection_start_pressure_bar
            ),
            "pump_startup/injection_startup_flow_ml_per_hour": (
                plan.injection_startup_flow_ml_per_hour
            ),
            "pump_startup/injection_measurement_flow_ml_per_hour": (
                plan.effective_measurement_flow_ml_per_hour
            ),
            "pump_startup/jacket_pressure_limit_bar": (
                plan.jacket_pressure_limit_bar
            ),
            "pump_startup/injection_pressure_limit_bar": (
                plan.injection_pressure_limit_bar
            ),
            "pump_startup/margin_stability_seconds": (
                plan.margin_stability_seconds
            ),
        }
        for key, value in values.items():
            self._user_settings.setValue(key, value)
        self._user_settings.sync()

    def _build_preflight_report(self, record: MeasurementRecord) -> PreflightReport:
        items: list[PreflightItem] = []

        def add(
            key: str,
            label: str,
            status: PreflightStatus,
            detail: str,
            remediation: str = "",
        ) -> None:
            items.append(PreflightItem(key, label, status, detail, remediation))

        project_id = self._project.currentData()
        stage_name = self._stage.currentText().strip()
        project_ok = isinstance(project_id, int) and bool(stage_name)
        add(
            "project",
            "Aktív projekt és mérési szakasz",
            PreflightStatus.PASSED if project_ok else PreflightStatus.FAILED,
            (
                f"{self._active_project_label.text()} / {stage_name}"
                if project_ok
                else "Nincs teljes projekt- és szakaszkiválasztás."
            ),
            "Válasszon projektet és mérési szakaszt." if not project_ok else "",
        )

        ready = (
            self._devices.status.state is ApplicationState.READY
            and (
                self._run_mode is RunMode.SIMULATION
                or self._devices.status.hardware_authorized
            )
        )
        add(
            "state",
            "Rendszerállapot",
            PreflightStatus.PASSED if ready else PreflightStatus.FAILED,
            self._devices.status.state.value.upper(),
            "Csatlakoztassa az eszközöket, és szüntesse meg a hibát."
            if not ready
            else "",
        )

        if self._run_mode is RunMode.SIMULATION:
            add(
                "connections",
                "Eszközkapcsolatok",
                PreflightStatus.WARNING,
                "Szimulált eszközök; nincs fizikai kapcsolat.",
                "Csak gyakorláshoz használja. Éles méréshez aktiválja a hardvermódot.",
            )
        else:
            connection = self._hardware_connection_result
            hardware = self._active_hardware_configuration
            required_devices = (
                hardware.enabled_test_devices() if hardware is not None else ()
            )
            connection_ok = (
                connection is not None
                and bool(required_devices)
                and connection.successful_for(required_devices)
            )
            details = (
                "; ".join(
                    f"{item.device.value}: {'OK' if item.successful else item.detail}"
                    for item in connection.devices
                )
                if connection is not None
                else "Nincs érvényes, teljes kapcsolatteszt."
            )
            add(
                "connections",
                "Eszközkapcsolatok",
                PreflightStatus.PASSED if connection_ok else PreflightStatus.FAILED,
                details,
                "Futtassa újra az egyedi eszközkapcsolati teszteket."
                if not connection_ok
                else "",
            )
            physical_flags = {
                "szelepirány": self._valve_direction_is_validated(),
                "biztonsági nyomáshatárok": self._setting_bool(
                    "safety/limits_validated", False
                ),
                "szenzorkalibrációk": self._setting_bool(
                    "calibration/profile_validated", False
                ),
                "pumpa MAX PRESS/SHUTDOWN": self._setting_bool(
                    "hardware/pump_shutdown_validated", False
                ),
            }
            missing_physical = tuple(
                label for label, validated in physical_flags.items() if not validated
            )
            add(
                "physical_validation",
                "Fizikai biztonsági validáció",
                (
                    PreflightStatus.PASSED
                    if not missing_physical
                    else PreflightStatus.WARNING
                ),
                (
                    "Minden kötelező fizikai paraméter validált."
                    if not missing_physical
                    else "Nincs validálva: " + ", ".join(missing_physical)
                ),
                (
                    "Tájékoztató figyelmeztetés: végezze el és dokumentálja "
                    "a helyszíni ellenőrzéseket. A hiányzó validációs jelzők "
                    "nem blokkolják a mérés indítását."
                    if missing_physical
                    else ""
                ),
            )

        snapshot = record.snapshot
        line_pressure_text = (
            "nincs hozzáadva"
            if snapshot.line_pressure_bar is None
            else f"{snapshot.line_pressure_bar:.3f} bar"
        )
        sensors_ok = (
            snapshot.jacket_pump.connected
            and snapshot.injection_pump.connected
            and snapshot.quality.value == "good"
        )
        add(
            "sensors",
            "Aktuális szenzoradatok",
            PreflightStatus.PASSED if sensors_ok else PreflightStatus.FAILED,
            f"Minőség: {snapshot.quality.value}; köpeny: "
            f"{snapshot.jacket_pump.pressure_bar:.3f} bar; besajtolás: "
            f"{snapshot.injection_pump.pressure_bar:.3f} bar; vonali: "
            f"{line_pressure_text}.",
            "Ellenőrizze a szenzorokat és a pumpakapcsolatokat."
            if not sensors_ok
            else "",
        )

        try:
            LinearCalibration(*self._line_calibration_values())
            LinearCalibration(*self._delta_calibration_values())
            SafetyLimits(
                self._max_jacket.value(),
                self._max_injection.value(),
                self._max_delta.value(),
                self._minimum_margin.value(),
                self._max_overshoot.value(),
                self._max_line.value(),
            )
        except ValueError as error:
            add(
                "configuration",
                "Kalibráció és biztonsági határértékek",
                PreflightStatus.FAILED,
                str(error),
                "Javítsa a kalibrációt vagy a biztonsági határértékeket.",
            )
        else:
            add(
                "configuration",
                "Kalibráció és biztonsági határértékek",
                PreflightStatus.PASSED,
                "A beállítások szerkezetileg érvényesek.",
            )

        if self._run_mode is RunMode.HARDWARE:
            add(
                "pump_startup",
                "Mérésindítási pumpaértékek",
                PreflightStatus.PASSED,
                "Mindkét pumpa kezdőnyomását, a köpeny nyomásfelépítési "
                "térfogatáramát és a besajtoló térfogatáramát a következő "
                "kötelező ablakban kell megadni és külön megerősíteni.",
            )

        if self._run_mode is RunMode.HARDWARE:
            hardware = self._active_hardware_configuration
            if hardware is None:
                add(
                    "valve_configuration",
                    "Szelepkimenet és funkcionális teszt",
                    PreflightStatus.FAILED,
                    "Nincs aktív hardverkonfiguráció.",
                    "Aktiválja újra a hardvermódot sikeres kapcsolatteszt után.",
                )
            else:
                evidence_keys = (
                    "hardware/cable_disconnect_test_completed",
                    "hardware/emergency_stop_test_completed",
                    "hardware/supervised_test_completed",
                )
                evidence_complete = all(
                    str(self._user_settings.value(key, "false")).lower()
                    in {"1", "true", "yes", "on"}
                    for key in evidence_keys
                )
                add(
                    "valve_configuration",
                    "Szelepkimenet és funkcionális teszt",
                    (
                        PreflightStatus.PASSED
                        if evidence_complete
                        else PreflightStatus.WARNING
                    ),
                    f"SAFE: {hardware.safe_output_voltage:g} V; 0%: "
                    f"{hardware.valve_zero_percent_voltage:g} V; 100%: "
                    f"{hardware.valve_hundred_percent_voltage:g} V; vezetett "
                    f"teszt: {'teljes' if evidence_complete else 'nem teljes'}.",
                    "Végezze el a vezetett funkcionális eszköztesztet."
                    if not evidence_complete
                    else "",
                )

        required_margin = self._minimum_margin.value()
        actual_margin = (
            snapshot.jacket_pump.pressure_bar
            - snapshot.injection_pump.pressure_bar
        )
        margin_ok = actual_margin >= required_margin
        margin_status = (
            PreflightStatus.PASSED if margin_ok else PreflightStatus.WARNING
        )
        add(
            "pressure_margin",
            "Köpeny–besajtolás nyomáskülönbség",
            margin_status,
            f"Aktuális: {actual_margin:.3f} bar; szükséges: legalább "
            f"{required_margin:.3f} bar.",
            (
                "A program először csak a köpenypumpát indítja el, és a "
                "besajtolópumpát a szükséges különbség eléréséig tiltja."
                if not margin_ok
                else "Növelje biztonságosan a köpenynyomást vagy csökkentse a "
                "besajtolási nyomást."
                if not margin_ok
                else ""
            ),
        )

        if record.safety_reasons:
            add(
                "safety",
                "Biztonsági kapcsolók és reteszek",
                PreflightStatus.FAILED,
                "; ".join(record.safety_reasons),
                "Szüntesse meg a felsorolt okokat; a reteszek nem kerülhetők meg.",
            )
        elif self._active_alarm_text != "Nincs aktív riasztás":
            add(
                "safety",
                "Biztonsági kapcsolók és reteszek",
                PreflightStatus.FAILED,
                self._active_alarm_text,
                "Ellenőrzés után zárja be a riasztást.",
            )
        else:
            add(
                "safety",
                "Biztonsági kapcsolók és reteszek",
                PreflightStatus.PASSED,
                "Nincs aktív szoftveres biztonsági tiltás.",
            )

        try:
            free_bytes = shutil.disk_usage(self._data_directory).free
            writable = os.access(self._data_directory, os.W_OK)
        except OSError as error:
            add(
                "storage",
                "Mérési fájl és tárhely",
                PreflightStatus.FAILED,
                str(error),
                "Ellenőrizze az adatmappa elérhetőségét és jogosultságát.",
            )
        else:
            storage_ok = writable and free_bytes >= 1024**3
            provenance = (
                "szimulált forrás; "
                if self._run_mode is RunMode.SIMULATION
                else "hardveres forrás; "
            )
            add(
                "storage",
                "Mérési fájl és tárhely",
                PreflightStatus.PASSED if storage_ok else PreflightStatus.FAILED,
                f"{provenance}szabad hely: {free_bytes / 1024**3:.1f} GiB; "
                f"írható: {'igen' if writable else 'nem'}; fájl: "
                f"{self._measurement_writer.current_path or 'nincs'}.",
                "Szabadítson fel legalább 1 GiB helyet, és ellenőrizze az "
                "adatmappa jogosultságát."
                if not storage_ok
                else "",
            )

        return PreflightReport(tuple(items))

    def _measurement_preflight_failed(self, message: str) -> None:
        self._preflight_active = False
        self._preflight_starts_measurement = False
        self._show_error(f"A mérés nem indítható: {message}")
        self._refresh_state()

    def _stop(self) -> None:
        try:
            if self._pump_control is not None:
                self._pump_control.set_remote_supervision_active(False)
            self._runtime.stop()
            self._measurement_writer.complete_current_phase()
            self._devices.stop()
            if self._pump_control is not None:
                self._pump_control.observe_safe_stop()
        except Exception as error:
            self._show_error(str(error))
        if self._devices.status.state is ApplicationState.FAULT:
            self._handle_critical_hardware_fault(
                self._devices.status.fault_reason
                or "a mérés biztonságos leállítása sikertelen"
            )
            return
        self._start_pending_stage_exports()
        self._pending_measurement_pump_plan = None
        self._preflight_active = False
        self._reset_measurement_dashboard()
        self._refresh_state()

    def _pause_measurement(self) -> None:
        try:
            if self._runtime.paused:
                self._runtime.resume()
                self._devices.set_measurement_state(MeasurementState.RUNNING)
            else:
                self._runtime.pause()
                self._devices.set_measurement_state(MeasurementState.PAUSED)
        except RuntimeError as error:
            self._show_error(str(error))
        self._refresh_state()

    def _reset_measurement_dashboard(self) -> None:
        self._measurement_time_origin = None
        self._last_cycle_result = None
        self._times.clear()
        self._jacket_pressures.clear()
        self._injection_pressures.clear()
        self._injection_flows.clear()
        self._line_pressures.clear()
        self._alarm_points.clear()
        self._alarm_scatter.setData([])
        for curve in (
            self._jacket_curve,
            self._injection_curve,
            self._line_curve,
            self._flow_curve,
        ):
            curve.setData([], [])
        self._plot.setXRange(0.0, 1.0, padding=0.0)
        self._flow_plot.setXRange(0.0, 1.0, padding=0.0)
        self._jacket_label.setText("— bar")
        self._injection_label.setText("— bar")
        self._jacket_remaining_label.setText("Maradék folyadék: — ml")
        self._jacket_net_volume_label.setText(
            "Indítás óta nettó köpenytérfogat: — ml"
        )
        self._injection_remaining_label.setText("Maradék folyadék: — ml")
        self._injection_flow_label.setText("Besajtolási sebesség: — ml/h")
        self._injected_volume_label.setText(
            "Indítás óta nettó besajtolt: — ml"
        )
        self._line_label.setText("— bar")
        self._delta_label.setText("— bar")
        self._pressure_margin_label.setText("— bar")
        self._refresh_valve_status()
        self._history_view.set_sources(())
        self._refresh_recording_status()

    def _emergency_stop(self) -> None:
        if self._runtime.running:
            self._runtime.stop()
        self._measurement_writer.complete_current_phase()
        self._set_active_alarm("RETESSZELT HIBA: kézi vészleállítás")
        if self._run_mode is RunMode.HARDWARE:
            self._handle_critical_hardware_fault("kézi vészleállítás")
        else:
            self._devices.emergency_stop()
            self._start_pending_stage_exports()
            self._refresh_state()

    def _handle_cycle(self, result: object) -> None:
        if self._shutdown_started:
            return
        if not isinstance(result, ControlCycleResult):
            self._handle_runtime_fault("invalid result from control thread")
            return
        if self._runtime.paused and not result.record.safety_reasons:
            return
        self._last_cycle_result = result
        self._last_hardware_status_record = result.record
        snapshot = result.record.snapshot
        self._diagnostics.emit(
            DiagnosticCategory.RUNTIME,
            "CYCLE",
            f"jacket={snapshot.jacket_pump.pressure_bar:.3f} bar; "
            f"injection={snapshot.injection_pump.pressure_bar:.3f} bar; "
            "line="
            + (
                "N/A; "
                if snapshot.line_pressure_bar is None
                else f"{snapshot.line_pressure_bar:.3f} bar; "
            )
            + f"valve={result.command.output_percent}",
        )
        if result.command.reason == "bumpless manual-to-automatic transfer":
            self._diagnostics.emit(
                DiagnosticCategory.RUNTIME,
                "MODE",
                result.command.reason,
            )
        self._set_connection("jacket", snapshot.jacket_pump.connected)
        self._set_connection("injection", snapshot.injection_pump.connected)
        self._set_connection("line_daq", True)
        self._set_connection("delta_daq", True)
        self._set_connection("valve", result.command.enabled)
        self._jacket_label.setText(
            format_dashboard_pressure(snapshot.jacket_pump.pressure_bar)
        )
        self._injection_label.setText(
            format_dashboard_pressure(snapshot.injection_pump.pressure_bar)
        )
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
        self._line_label.setText(
            "Nincs hozzáadva"
            if snapshot.line_pressure_bar is None
            else format_dashboard_pressure(snapshot.line_pressure_bar)
        )
        self._delta_label.setText(
            "Nincs hozzáadva"
            if snapshot.differential_pressure_bar is None
            else format_dashboard_pressure(snapshot.differential_pressure_bar)
        )
        margin = (
            snapshot.jacket_pump.pressure_bar
            - snapshot.injection_pump.pressure_bar
        )
        self._pressure_margin_label.setText(format_dashboard_pressure(margin))
        self._pressure_margin_label.setStyleSheet(
            "background:transparent;font-size:20px;font-weight:700;color:#66788a"
        )
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
        self._line_pressures.append(
            float("nan")
            if snapshot.line_pressure_bar is None
            else snapshot.line_pressure_bar
        )
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
            reason = "; ".join(result.record.safety_reasons)
            safety_rule = (
                "INJECTION_PRESSURE_ABOVE_JACKET"
                if "injection pressure exceeds jacket pressure"
                in result.record.safety_reasons
                else (
                    "PUMP_TELEMETRY_STALE"
                    if snapshot.quality is DataQuality.STALE
                    else (
                        "PUMP_TELEMETRY_INVALID"
                        if snapshot.quality is DataQuality.INVALID
                        else "SAFETY_INTERLOCK"
                    )
                )
            )
            coded_reason = f"[{safety_rule}] {reason}"
            self._diagnostics.emit_event(
                DiagnosticCategory.RUNTIME,
                "SAFETY_RULE_TRIGGERED",
                fields={
                    "device": "measurement_control",
                    "field": "safety",
                    "previous_quality": DataQuality.GOOD.value,
                    "new_quality": snapshot.quality.value,
                    "safety_rule": safety_rule,
                    "selected_fault_strategy": "FULL_SAFE_STOP",
                    "action": "request_full_safe_state",
                    "action_result": "REQUESTED",
                },
                level="CRITICAL",
            )
            self._add_alarm_point(latest, result, "critical", coded_reason)
            self._set_active_alarm(
                f"RETESSZELT BIZTONSÁGI HIBA: {coded_reason}"
            )
            if self._run_mode is RunMode.HARDWARE:
                self._handle_critical_hardware_fault(coded_reason)
                return
            if self._runtime.running:
                self._runtime.stop()
            if self._devices.status.state is not ApplicationState.FAULT:
                self._devices.emergency_stop(
                    f"simulation safety interlock: {coded_reason}"
                )
            self._measurement_writer.complete_current_phase()
            self._start_pending_stage_exports()
            self._set_all_connections("HIBA", ok=False)
            self._refresh_state()
        elif snapshot.quality is not DataQuality.GOOD:
            self._add_alarm_point(
                latest,
                result,
                "warning",
                f"Adatminőség: {snapshot.quality.value}",
            )
        self._refresh_recording_status(snapshot.recorded_at)

    def _add_alarm_point(
        self,
        elapsed_seconds: float,
        result: ControlCycleResult,
        severity: str,
        reason: str,
    ) -> None:
        snapshot = result.record.snapshot
        finite_values = [
            value
            for value in (
                snapshot.jacket_pump.pressure_bar,
                snapshot.injection_pump.pressure_bar,
                snapshot.line_pressure_bar,
                snapshot.differential_pressure_bar,
            )
            if value is not None and isfinite(value)
        ]
        y_value = max(finite_values, default=0.0)
        level = "KRITIKUS" if severity == "critical" else "FIGYELMEZTETÉS"
        event = self._create_measurement_event(
            event_type="fault" if severity == "critical" else "warning",
            severity=severity,
            error_code=(
                reason.split("]", 1)[0].lstrip("[")
                if reason.startswith("[")
                else "RUNTIME_WARNING"
            ),
            description=reason,
            affected_hardware="measurement_control",
            result=result,
        )
        tooltip = (
            f"{level}\n"
            f"Eseményazonosító: {event.event_id}\n"
            f"Idő: {format_hungarian_time(snapshot.recorded_at, '%Y-%m-%d %H:%M:%S %Z')}\n"
            f"Szakasz: {result.record.active_stage}\n"
            f"Ok: {reason}\n"
            f"Köpeny: {snapshot.jacket_pump.pressure_bar:.3f} bar\n"
            f"Besajtolás: {snapshot.injection_pump.pressure_bar:.3f} bar"
        )
        self._alarm_points.append(
            {
                "pos": (elapsed_seconds, y_value),
                "brush": pg.mkBrush(
                    "#d50000" if severity == "critical" else "#f9a825"
                ),
                "data": tooltip,
            }
        )
        if len(self._alarm_points) > 500:
            del self._alarm_points[:-500]
        self._alarm_scatter.setData(self._alarm_points)
        self._store_measurement_event(event)

    def _create_measurement_event(
        self,
        *,
        event_type: str,
        severity: str,
        error_code: str,
        description: str,
        affected_hardware: str,
        event_id: str | None = None,
        result: ControlCycleResult | None = None,
        current_flow_ml_per_hour: float | None = None,
        target_flow_ml_per_hour: float | None = None,
    ) -> MeasurementEvent:
        cycle = result or self._last_cycle_result
        snapshot = cycle.record.snapshot if cycle is not None else None
        monotonic_seconds = (
            snapshot.monotonic_seconds
            if snapshot is not None
            else self._times[-1]
            if self._times
            else 0.0
        )
        elapsed = (
            monotonic_seconds - self._measurement_time_origin
            if self._measurement_time_origin is not None
            else 0.0
        )
        return MeasurementEvent(
            event_id=event_id or uuid4().hex,
            recorded_at_utc=(
                snapshot.recorded_at if snapshot is not None else datetime.now(UTC)
            ).isoformat(),
            elapsed_seconds=max(0.0, elapsed),
            event_type=event_type,
            severity=severity,
            error_code=error_code,
            description=description,
            active_stage=(
                cycle.record.active_stage
                if cycle is not None
                else self._stage.currentText()
            ),
            affected_hardware=affected_hardware,
            jacket_pressure_bar=(
                snapshot.jacket_pump.pressure_bar if snapshot is not None else None
            ),
            injection_pressure_bar=(
                snapshot.injection_pump.pressure_bar if snapshot is not None else None
            ),
            line_pressure_bar=(snapshot.line_pressure_bar if snapshot is not None else None),
            differential_pressure_bar=(
                snapshot.differential_pressure_bar if snapshot is not None else None
            ),
            current_flow_ml_per_hour=(
                current_flow_ml_per_hour
                if current_flow_ml_per_hour is not None
                else snapshot.injection_pump.flow_ml_per_hour
                if snapshot is not None
                else self._applied_measurement_flow_ml_per_hour
            ),
            target_flow_ml_per_hour=target_flow_ml_per_hour,
            valve_output_percent=(snapshot.valve_percent if snapshot is not None else None),
            measurement_state=self._devices.status.measurement.value,
        )

    def _store_measurement_event(self, event: MeasurementEvent) -> None:
        try:
            self._measurement_writer.write_event(event)
        except (OSError, RuntimeError, ValueError) as error:
            self._diagnostics.emit(
                DiagnosticCategory.SYSTEM,
                "EVENT_PERSISTENCE_FAILED",
                f"{event.event_id}: {error}",
                level="ERROR",
            )
        self._history_view.add_event(event)

    def _add_current_event_marker(self, event: MeasurementEvent) -> None:
        y_value = next(
            (
                value
                for value in (
                    event.injection_pressure_bar,
                    event.line_pressure_bar,
                    event.jacket_pressure_bar,
                    event.differential_pressure_bar,
                )
                if value is not None and isfinite(value)
            ),
            0.0,
        )
        colors = {"critical": "#d50000", "warning": "#f9a825", "operator": "#1565c0"}
        self._alarm_points.append(
            {
                "pos": (event.elapsed_seconds, y_value),
                "brush": pg.mkBrush(colors.get(event.severity, "#1565c0")),
                "data": f"{event.event_id}\n{event.error_code}: {event.description}",
            }
        )
        self._alarm_scatter.setData(self._alarm_points)

    def _alarm_points_hovered(
        self, _item: object, points: list[object], event: object
    ) -> None:
        if not points:
            QToolTip.hideText()
            return
        data_reader = getattr(points[0], "data", None)
        screen_position_reader = getattr(event, "screenPos", None)
        if not callable(data_reader) or not callable(screen_position_reader):
            return
        screen_position = screen_position_reader()
        to_point = getattr(screen_position, "toPoint", None)
        if callable(to_point):
            screen_position = to_point()
        QToolTip.showText(screen_position, str(data_reader()), self._plot)

    def _handle_runtime_fault(self, message: str) -> None:
        if self._shutdown_started:
            return
        event = self._create_measurement_event(
            event_type="fault",
            severity="critical",
            error_code="CONTROL_RUNTIME_FAULT",
            description=message,
            affected_hardware="valve_pid_or_acquisition",
        )
        self._diagnostics.emit_event(
            DiagnosticCategory.RUNTIME,
            "CONTROL_RUNTIME_FAULT",
            fields={"unique_event_id": event.event_id, "action_result": "FAULT"},
            level="ERROR",
        )
        self._store_measurement_event(event)
        self._add_current_event_marker(event)
        self._set_active_alarm(f"RETESSZELT VEZÉRLÉSI HIBA: {message}")
        if self._run_mode is RunMode.HARDWARE:
            self._handle_critical_hardware_fault(message)
            return
        if self._devices.status.state is not ApplicationState.FAULT:
            self._devices.emergency_stop(f"control runtime failed: {message}")
        self._measurement_writer.complete_current_phase()
        self._start_pending_stage_exports()
        self._set_all_connections("HIBA", ok=False)
        self._refresh_state()

    def _handle_critical_hardware_fault(self, message: str) -> None:
        if self._critical_hardware_recovery_active:
            return
        if self._run_mode is RunMode.HARDWARE:
            self._recover_hardware_fault_in_place(message)
            return
        if self._active_alarm_text == "Nincs aktív riasztás":
            self._set_active_alarm(f"KRITIKUS HARDVERHIBA: {message}")
        self._critical_hardware_recovery_active = True
        self._preflight_active = False
        release_errors: list[str] = []
        try:
            if self._runtime.running:
                try:
                    self._runtime.stop()
                except Exception as error:
                    release_errors.append(f"vezérlési ciklus: {error}")
            self._measurement_writer.complete_current_phase()
            if self._devices.status.state is not ApplicationState.FAULT:
                try:
                    self._devices.emergency_stop(
                        f"critical hardware fault: {message}"
                    )
                except Exception as error:
                    release_errors.append(f"biztonsági leállítás: {error}")
            try:
                self._devices.disconnect()
            except Exception as error:
                release_errors.append(f"portok lezárása: {error}")
            if self._pump_control is not None:
                self._pump_control.observe_disconnected(*tuple(PumpRole))
            if self._devices.status.state is ApplicationState.FAULT:
                try:
                    self._devices.acknowledge_fault()
                except Exception as error:
                    release_errors.append(f"belső hibaállapot lezárása: {error}")
            try:
                self._activate_simulation(
                    preserve_preferred_mode=True,
                    ignore_cleanup_errors=True,
                )
            except Exception as error:
                release_errors.append(f"hardvermód eldobása: {error}")
                self._remember_run_mode(RunMode.HARDWARE)
            self._start_pending_stage_exports()
            self._set_all_connections("LEVÁLASZTVA — KRITIKUS HIBA", ok=False)
            details = (
                "\n\nA portlezárás közben észlelt további hibák:\n- "
                + "\n- ".join(release_errors)
                if release_errors
                else ""
            )
            self._show_error(
                "Kritikus hardverhiba történt, ezért a program biztonsági "
                "állapotot kért és elengedte a hardverkapcsolatokat.\n\n"
                f"Hiba: {message}\n\n"
                "Ellenőrizd a berendezést és a kábeleket. Az üzenet bezárása "
                "után megnyílik az Eszközbeállítások ablaka."
                f"{details}"
            )
        finally:
            self._critical_hardware_recovery_active = False
            self._refresh_state()
        QTimer.singleShot(0, self._open_device_settings)

    def _recover_hardware_fault_in_place(self, message: str) -> None:
        """Safe-stop a measurement without discarding the hardware session."""
        self._critical_hardware_recovery_active = True
        self._preflight_active = False
        errors: list[str] = []
        try:
            if self._pump_control is not None:
                self._pump_control.set_remote_supervision_active(False)
            if self._active_alarm_text == "Nincs aktív riasztás":
                self._set_active_alarm(f"KRITIKUS HARDVERHIBA: {message}")
            if self._runtime.running:
                try:
                    self._runtime.stop()
                except Exception as error:
                    errors.append(f"vezérlési ciklus: {error}")
            self._measurement_writer.complete_current_phase()
            if self._devices.status.state is not ApplicationState.FAULT:
                try:
                    self._devices.emergency_stop(f"critical hardware fault: {message}")
                except Exception as error:
                    errors.append(f"biztonsági leállítás: {error}")
            if self._pump_control is not None:
                self._pump_control.observe_safe_stop()
            self._start_pending_stage_exports()
            self._set_all_connections("VÉSZLEÁLLÍTÁS — RETESZELVE", ok=False)
            details = "" if not errors else "\n- " + "\n- ".join(errors)
            self._show_error(
                "A mérés biztonságosan leállt. A HARDWARE mód, a "
                "hardverprofil és a munkamenet engedélye megmaradt. A program "
                "FAULT állapotban marad a riasztás kezelői nyugtázásáig; "
                "ezután csak olvasási hardverellenőrzést indít.\n\n"
                f"Hiba: {message}{details}"
            )
        finally:
            self._critical_hardware_recovery_active = False
            self._refresh_state()

    def _set_connection(self, key: str, connected: bool) -> None:
        self._set_connection_status(
            key,
            "KAPCSOLÓDVA" if connected else "HIBA",
            connected,
        )

    def _set_connection_status(
        self, key: str, text: str, ok: bool | None
    ) -> None:
        label = self._connection_labels[key]
        label.setText(text)
        color = "#1b7f3a" if ok is True else "#b00020" if ok is False else "#9a6700"
        label.setStyleSheet(
            f"background:transparent;color:{color};"
            "font-size:11px;font-weight:700"
        )

    def _set_all_connections(self, text: str, *, ok: bool | None) -> None:
        color = "#1b7f3a" if ok is True else "#b00020" if ok is False else "#66788a"
        for label in self._connection_labels.values():
            label.setText(text)
            label.setStyleSheet(
                f"background:transparent;color:{color};font-size:11px;font-weight:700"
            )

    def _refresh_valve_status(self) -> None:
        """Keep the valve card meaningful between measurement cycles as well."""
        configuration = self._active_hardware_configuration
        valve_enabled = (
            True
            if self._run_mode is RunMode.SIMULATION or configuration is None
            else configuration.valve_output_enabled
        )
        if not valve_enabled:
            self._valve_label.setText("Nincs hozzáadva")
            self._set_connection_status("valve", "NINCS HOZZÁADVA", None)
            return

        state = self._devices.status.state
        if state is ApplicationState.FAULT:
            self._valve_label.setText("ISMERETLEN — HIBA")
            self._set_connection_status("valve", "SAFE NEM IGAZOLT", False)
            return

        if state is ApplicationState.RUNNING:
            result = self._last_cycle_result
            if result is None:
                self._valve_label.setText("SAFE — indítás folyamatban")
                status = (
                    "SZIMULÁCIÓ — SAFE"
                    if self._run_mode is RunMode.SIMULATION
                    else "KAPCSOLÓDVA — SAFE"
                )
                self._set_connection_status("valve", status, True)
                return
            output = result.command.output_percent
            if not result.command.enabled or output is None:
                self._valve_label.setText("SAFE — biztonsági tiltás")
                self._set_connection_status("valve", "BIZTONSÁGI TILTÁS — SAFE", True)
                return
            self._valve_label.setText(f"{output:.1f} %")
            status = (
                "SZIMULÁCIÓ — AKTÍV"
                if self._run_mode is RunMode.SIMULATION
                else "KAPCSOLÓDVA — AKTÍV"
            )
            self._set_connection_status("valve", status, True)
            return

        if state is ApplicationState.READY:
            self._valve_label.setText("SAFE — mérés nem fut")
            status = (
                "SZIMULÁCIÓ — SAFE"
                if self._run_mode is RunMode.SIMULATION
                else "KAPCSOLÓDVA — SAFE"
            )
            self._set_connection_status("valve", status, True)
            return

        self._valve_label.setText("NINCS KIMENET — leválasztva")
        self._set_connection_status("valve", "LEVÁLASZTVA", None)

    def _refresh_state(self) -> None:
        device_status = self._devices.status
        state = device_status.state
        self._state_label.setText(
            "SZÜNETEL"
            if self._runtime.paused
            else "ELŐKÉSZÍTÉS"
            if device_status.measurement is MeasurementState.PREPARING
            else "ELŐKÉSZÍTVE"
            if device_status.measurement is MeasurementState.WAITING_CONFIRMATION
            else state.value.upper()
        )
        self._connect_button.setEnabled(False)
        self._disconnect_button.setEnabled(False)
        project_selected = (
            isinstance(self._project.currentData(), int)
            and bool(self._stage.currentText().strip())
        )
        no_alarm = self._active_alarm_text == "Nincs aktív riasztás"
        project_hardware_matches = self._hardware_matches_active_project()
        connections_ready = (
            self._run_mode is RunMode.SIMULATION
            or (
                self._hardware_connection_result is not None
                and self._hardware_connection_result.all_successful
                and project_hardware_matches
                and self._devices.status.hardware_authorized
            )
        )
        common_start_conditions = (
            not self._preflight_active
            and project_selected
            and no_alarm
            and connections_ready
        )
        preparation_ready = (
            state is ApplicationState.READY and common_start_conditions
        )
        direct_start = state is ApplicationState.READY and common_start_conditions
        prepared_start = (
            state is ApplicationState.RUNNING
            and self._devices.status.measurement
            is MeasurementState.WAITING_CONFIRMATION
            and self._pending_measurement_pump_plan is not None
            and common_start_conditions
        )
        measurement_start_ready = direct_start or prepared_start
        self._start_button.setEnabled(measurement_start_ready)
        if not project_selected:
            self._start_button.setToolTip(
                "Válasszon aktív projektet és mérési szakaszt."
            )
        elif not no_alarm:
            self._start_button.setToolTip(
                "Aktív riasztás mellett a mérés nem indítható; ellenőrzés után "
                "zárja be a riasztást."
            )
        elif not project_hardware_matches:
            self._start_button.setToolTip(
                "A projekt eszközprofilja megváltozott. Aktiválja újra a "
                "projekthez hozzáadott hardvereket."
            )
        elif (
            self._run_mode is RunMode.HARDWARE
            and not self._devices.status.hardware_authorized
        ):
            self._start_button.setToolTip(
                "A biztonságos leállítás visszavonta az NI fizikai kimenet "
                "engedélyét. Aktiválja újra a hardvermódot az "
                "Eszközbeállításokban."
            )
        elif not connections_ready:
            self._start_button.setToolTip(
                "Futtassa le sikeresen minden konfigurált eszköz kapcsolati tesztjét."
            )
        elif self._preflight_active:
            self._start_button.setToolTip("A mérés előtti ellenőrzés folyamatban van.")
        elif direct_start:
            self._start_button.setToolTip(
                "Friss biztonsági ellenőrzés után elindítja a szelepvezérlést "
                "és az adatrögzítést. A kézzel beállított pumpákat nem módosítja."
            )
        elif prepared_start:
            self._start_button.setToolTip(
                "Elindítja a szelepvezérlést és az adatrögzítést az előkészített "
                "pumpaállapot módosítása nélkül."
            )
        else:
            self._start_button.setToolTip(
                "A mérés csak Kész vagy Előkészítve állapotból indítható."
            )
        self._prepare_button.setEnabled(preparation_ready)
        if self._preflight_active:
            self._prepare_button.setToolTip("Az előkészítés folyamatban van.")
        elif state is ApplicationState.RUNNING:
            self._prepare_button.setToolTip(
                "Az előkészítés elkészült vagy a mérés már fut."
            )
        else:
            self._prepare_button.setToolTip(
                "Bekéri az előkészítési adatokat és felkészíti a pumpákat."
            )
        self._pause_button.setEnabled(
            state is ApplicationState.RUNNING
            and self._runtime.running
            and not self._preflight_active
        )
        self._pause_button.setText(
            "Mérés folytatása"
            if self._runtime.paused
            else "Mérés szüneteltetése"
        )
        self._pause_button.setToolTip(
            "Folytatja a PID- és adatrögzítési ciklust."
            if self._runtime.paused
            else "Megállítja a PID-et és az adatmentést, miközben a "
            "biztonsági felügyelet és a jelenlegi fizikai kimenet megmarad."
        )
        self._stop_button.setEnabled(
            state is ApplicationState.RUNNING and not self._preflight_active
        )
        self._measurement_settings_action.setEnabled(
            state is not ApplicationState.RUNNING
        )
        self._apply_measurement_flow_button.setEnabled(
            self._run_mode is RunMode.HARDWARE
            and self._devices.status.measurement is MeasurementState.RUNNING
            and not self._preflight_active
        )
        self._refresh_measurement_field_editability(state)
        self._configuration_summary_label.setText(
            self._configuration_summary_text()
        )
        self._refresh_valve_status()
        self._refresh_recording_status()

    def _refresh_measurement_field_editability(
        self, state: ApplicationState
    ) -> None:
        measurement_active = state is ApplicationState.RUNNING
        live_editing_available = self._runtime.running and not self._preflight_active
        for field in self._live_measurement_fields:
            field.setEnabled(not measurement_active or live_editing_available)
        self._stage.setEnabled(
            isinstance(self._project.currentData(), int)
            and (not measurement_active or live_editing_available)
        )

        # This value has a supervised physical apply path, but no simulation-side
        # runtime operation. Keep it editable before a measurement and only while
        # that physical operation is actually available during a measurement.
        flow_change_available = (
            self._run_mode is RunMode.HARDWARE
            and self._devices.status.measurement is MeasurementState.RUNNING
            and not self._preflight_active
        )
        self._new_measurement_flow.setEnabled(
            not measurement_active or flow_change_available
        )

    def _configuration_summary_text(self) -> str:
        hardware = self._active_hardware_configuration
        connection = self._hardware_connection_result
        hardware_complete = hardware is not None and hardware.measurement_ready
        pump_ready = (
            hardware is not None
            and connection is not None
            and connection.successful_for(
                tuple(
                    device
                    for device in (
                        HardwareTestDevice.JACKET_PUMP,
                        HardwareTestDevice.INJECTION_PUMP,
                    )
                    if device in hardware.enabled_test_devices()
                )
            )
        )
        ni_ready = (
            hardware is not None
            and connection is not None
            and connection.successful_for(
                tuple(
                    device
                    for device in (
                        HardwareTestDevice.LINE_PRESSURE,
                        HardwareTestDevice.DIFFERENTIAL_PRESSURE,
                    )
                    if device in hardware.enabled_test_devices()
                )
            )
        )
        valve_direction_validated = self._valve_direction_is_validated()
        safety_validated = self._setting_bool("safety/limits_validated", False)
        calibration_validated = self._setting_bool(
            "calibration/profile_validated", False
        )
        pump_shutdown_validated = self._setting_bool(
            "hardware/pump_shutdown_validated", False
        )
        try:
            self._pid_parameters()
        except ValueError:
            pid_parameters_valid = False
        else:
            pid_parameters_valid = True
        storage_ready = self._data_directory.exists() and os.access(
            self._data_directory, os.W_OK
        )
        measurement_ready = all(
            (
                hardware_complete,
                pump_ready,
                ni_ready,
                pid_parameters_valid,
                storage_ready,
            )
        )
        return "\n".join(
            (
                "Alkalmazás: elindítható",
                f"Hardverprofil: {'teljes' if hardware_complete else 'hiányos'}",
                f"Pumpakapcsolatok: {'kész' if pump_ready else 'hibás/ellenőrizetlen'}",
                f"NI bemenetek: {'kész' if ni_ready else 'hibás/ellenőrizetlen'}",
                "Szelepirány: "
                + (
                    "validált"
                    if valve_direction_validated
                    else "figyelmeztetés — nincs validálva"
                ),
                "Biztonsági határok: "
                + (
                    "kész"
                    if safety_validated
                    else "figyelmeztetés — nincs validálva"
                ),
                "Kalibrációk: "
                + (
                    "kész"
                    if calibration_validated
                    else "figyelmeztetés — nincs validálva"
                ),
                "Pumpa MAX PRESS/SHUTDOWN: "
                + (
                    "validált"
                    if pump_shutdown_validated
                    else "figyelmeztetés — nincs validálva"
                ),
                "PID-paraméterek: "
                + ("számszakilag érvényes" if pid_parameters_valid else "hibás"),
                f"Adatmentés: {'kész' if storage_ready else 'hibás'}",
                "Mérésindítás: "
                + (
                    "engedélyezett (validációs figyelmeztetésekkel)"
                    if measurement_ready
                    and not all(
                        (
                            valve_direction_validated,
                            safety_validated,
                            calibration_validated,
                            pump_shutdown_validated,
                        )
                    )
                    else "engedélyezett"
                    if measurement_ready
                    else "blokkolt"
                ),
            )
        )

    def _refresh_recording_status(self, recorded_at: datetime | None = None) -> None:
        if self._shutdown_started or not hasattr(self, "_recording_status_label"):
            return
        pending = self._nas_sync.pending_count
        if self._nas_sync.enabled:
            target = self._nas_sync.target_root
            self._nas_runtime_label.setText(
                f"NAS: bekapcsolva; várakozó fájlok: {pending}; cél: {target}"
            )
        else:
            self._nas_runtime_label.setText(
                f"NAS: kikapcsolva; várakozó fájlok: {pending}"
            )

        path = self._measurement_writer.current_path
        if self._runtime.running and path is not None:
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            updated = (
                format_hungarian_time(recorded_at, "%Y-%m-%d %H:%M:%S")
                if recorded_at is not None
                else "első ciklusra vár"
            )
            if self._runtime.paused:
                self._recording_status_label.setText("Ⅱ RÖGZÍTÉS SZÜNETEL")
                self._recording_status_label.setStyleSheet(
                    "color:#9a6700;font-weight:800"
                )
            else:
                self._recording_status_label.setText(
                    "● SZIMULÁCIÓ RÖGZÍTÉS AKTÍV"
                    if self._run_mode is RunMode.SIMULATION
                    else "● RÖGZÍTÉS AKTÍV"
                )
                self._recording_status_label.setStyleSheet(
                    "color:#1b7f3a;font-weight:800"
                )
            self._recording_details_label.setText(
                f"Fájl: {path}\nMéret: {size / 1024:.1f} KiB; "
                f"utolsó rögzítési ciklus: {updated}"
            )
            return

        self._recording_status_label.setText("RÖGZÍTÉS NEM AKTÍV")
        self._recording_status_label.setStyleSheet("color:#66788a;font-weight:800")
        self._recording_details_label.setText(
            (
                "Előkészített szimulációs mérési fájl: "
                if self._run_mode is RunMode.SIMULATION
                else "Előkészített mérési fájl: "
            )
            + str(path)
            if path is not None
            else "Nincs előkészített mérési fájl."
        )

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "EOR hiba", message)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._startup_mode_restore_started:
            QTimer.singleShot(0, self._restore_startup_mode)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._shutdown_started = True
        self._save_user_settings()
        self._hardware_status_timer.stop()
        self._hardware_status_generation += 1
        application_managed_connection = (
            self._devices.status.state is not ApplicationState.IDLE
        )
        if self._runtime.running:
            self._runtime.stop()
        if self._devices.status.state in (
            ApplicationState.READY,
            ApplicationState.RUNNING,
        ):
            self._devices.stop()
        self._measurement_writer.complete_current_phase()
        self._start_pending_stage_exports()
        export_thread = self._stage_export_thread
        if export_thread is not None and export_thread.is_alive():
            export_thread.join(timeout=30.0)
        if self._devices.status.state is not ApplicationState.IDLE:
            self._devices.disconnect()
        if self._pump_control is not None:
            if application_managed_connection:
                self._pump_control.observe_disconnected(*tuple(PumpRole))
                self._pump_control.revoke()
            else:
                self._pump_control.shutdown_connections()
        self._control_loop.close()
        self._nas_sync.close()
        self._projects.close()
        self._tray_icon.hide()
        event.accept()


def build_simulated_dashboard(
    data_path: Path,
    project_path: Path | None = None,
    *,
    settings: QSettings | None = None,
) -> DashboardWindow:
    jacket = SimulatedPump(pressure_bar=120.0, flow_ml_per_hour=10.0)
    injection = SimulatedPump(
        pressure_bar=100.0, flow_ml_per_hour=10.0, remaining_volume_ml=260.0
    )
    daq = SimulatedDataAcquisition()
    daq.inputs.update(line_pressure=2.0, differential_pressure=1.5)
    valve = SimulatedValveActuator()
    safety = SafetyMonitor(SafetyLimits(400.0, 350.0, 50.0))
    queue = NasSyncQueue(data_path.parent / "nas_sync_queue.sqlite3")
    nas_sync = BackgroundNasSynchronizer(queue)
    writer = ProjectMeasurementWriter(
        data_path.parent, nas_sync, measurement_kind="simulation"
    )
    measurement = MeasurementService(
        jacket_pump=jacket,
        injection_pump=injection,
        daq=daq,
        line_calibration=LinearCalibration(1.0, 5.0, 0.0, 400.0),
        differential_calibration=LinearCalibration(1.0, 5.0, 0.0, 40.0),
        safety_monitor=safety,
        writer=writer,
        persistence_enabled=True,
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
    user_data_root = user_data_root_path()
    settings = portable_user_settings(root, user_data_root=user_data_root)
    project_path = user_data_root / "projects.sqlite3"
    migrate_legacy_project_database(
        root / "data" / "projects.sqlite3", project_path
    )
    migrate_legacy_project_files(
        root / "data" / "projects", user_data_root / "projects"
    )
    configure_windows_application_identity()
    instance = QApplication.instance()
    application = instance if isinstance(instance, QApplication) else QApplication(sys.argv)
    application.setWindowIcon(application_icon())
    window = build_simulated_dashboard(
        user_data_root / "simulated_measurements.csv",
        project_path=project_path,
        settings=settings,
    )
    window.show()
    return application.exec()
