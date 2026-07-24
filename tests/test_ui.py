import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import sleep

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractItemView,
    QAbstractScrollArea,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QWidget,
)

from eor_control.application import (  # noqa: E402
    ApplicationState,
    DeviceControlService,
    RunMode,
)
from eor_control.control import ControlMode, PressureSource, ValveCommand  # noqa: E402
from eor_control.control_loop import ControlCycleResult  # noqa: E402
from eor_control.data_management import MeasurementTable  # noqa: E402
from eor_control.device_testing import (  # noqa: E402
    DeviceTestReport,
    FunctionalDeviceTestSession,
)
from eor_control.diagnostics import DiagnosticCategory, DiagnosticLogger  # noqa: E402
from eor_control.domain import (  # noqa: E402
    DataQuality,
    MeasurementRecord,
    MeasurementSnapshot,
    PumpStatus,
)
from eor_control.hardware import (  # noqa: E402
    ConnectionTestResult,
    DeviceConnectionResult,
    HardwareConfiguration,
    HardwareDiscovery,
    HardwareTestDevice,
    NiPhysicalChannelInfo,
    SerialPortInfo,
)
from eor_control.ni import NidaqmxDataAcquisition  # noqa: E402
from eor_control.projects import ProjectRepository  # noqa: E402
from eor_control.pump_control import PumpRole  # noqa: E402
from eor_control.simulators import (  # noqa: E402
    SimulatedDataAcquisition,
    SimulatedPump,
    SimulatedValveActuator,
)
from eor_control.storage import CsvMeasurementWriter  # noqa: E402
from eor_control.ui import (  # noqa: E402
    ADD_STAGE_ACTION_DATA,
    DARK_STYLESHEET,
    LIGHT_STYLESHEET,
    SYSTEM_STYLESHEET,
    WINDOWS_APP_USER_MODEL_ID,
    CalibrationSettingsDialog,
    ControlCycleSettingsDialog,
    DataManagementDialog,
    DeveloperViewDialog,
    DeviceSettingsDialog,
    DeviceTestWizard,
    LoggingSettingsDialog,
    MeasurementHistoryView,
    MeasurementOverviewDialog,
    MeasurementPumpPlan,
    MeasurementPumpStartupDialog,
    MeasurementTableModel,
    PreflightDialog,
    ProjectSelectionDialog,
    ProjectSettingsDialog,
    PumpControlDialog,
    PumpTelemetrySettingsDialog,
    ResizableDialog,
    SettingsHubDialog,
    SimulationSettingsPage,
    StageSettingsDialog,
    _authorize_physical_hardware,
    application_icon,
    application_icon_path,
    build_simulated_dashboard,
    configure_windows_application_identity,
    resolved_theme_stylesheet,
)


def application() -> QApplication:
    instance = QApplication.instance()
    return instance if isinstance(instance, QApplication) else QApplication([])


def assert_input_fields_are_labelled(root: QWidget) -> None:
    labels = root.findChildren(QLabel)
    fields = [
        *root.findChildren(QComboBox),
        *root.findChildren(QDoubleSpinBox),
        *root.findChildren(QSpinBox),
        *root.findChildren(QLineEdit),
        *root.findChildren(QCheckBox),
    ]
    unlabelled: list[str] = []
    for field in fields:
        if isinstance(field, QLineEdit) and isinstance(
            field.parent(), (QComboBox, QDoubleSpinBox, QSpinBox)
        ):
            continue
        if field.accessibleName().strip():
            continue
        if isinstance(field, QCheckBox) and field.text().strip():
            continue
        if any(label.buddy() is field and label.text().strip() for label in labels):
            continue
        unlabelled.append(field.objectName() or type(field).__name__)
    assert unlabelled == []


def test_application_dialogs_are_resizable() -> None:
    application()
    dialog = ResizableDialog()

    assert dialog.isSizeGripEnabled()
    assert dialog.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint
    assert dialog.sizePolicy().horizontalPolicy() is QSizePolicy.Policy.Expanding
    assert dialog.sizePolicy().verticalPolicy() is QSizePolicy.Policy.Expanding
    for dialog_type in (
        StageSettingsDialog,
        ProjectSelectionDialog,
        ProjectSettingsDialog,
        DeviceSettingsDialog,
        DeviceTestWizard,
        PumpControlDialog,
        LoggingSettingsDialog,
        ControlCycleSettingsDialog,
        PumpTelemetrySettingsDialog,
        DeveloperViewDialog,
        DataManagementDialog,
        CalibrationSettingsDialog,
        MeasurementOverviewDialog,
        PreflightDialog,
        SettingsHubDialog,
    ):
        assert issubclass(dialog_type, ResizableDialog)
    dialog.close()


def test_hardware_authorization_precedes_ni_output_authorization() -> None:
    calls: list[str] = []

    class Devices:
        def authorize_hardware(self, confirmation: str) -> None:
            assert confirmation == DeviceControlService.HARDWARE_CONFIRMATION
            calls.append("hardware")

    class Daq:
        def authorize_output(self, confirmation: str) -> None:
            assert confirmation == NidaqmxDataAcquisition.HARDWARE_CONFIRMATION
            calls.append("ni_output")

    _authorize_physical_hardware(
        Devices(),  # type: ignore[arg-type]
        Daq(),  # type: ignore[arg-type]
        valve_output_enabled=True,
        hardware_confirmation=DeviceControlService.HARDWARE_CONFIRMATION,
    )

    assert calls == ["hardware", "ni_output"]


def test_log_retention_service_settings_are_persisted(tmp_path: Path) -> None:
    application()
    settings = QSettings(
        str(tmp_path / "logging.ini"), QSettings.Format.IniFormat
    )
    logger = DiagnosticLogger(
        tmp_path / "logs" / "application.html",
        hardware_path=tmp_path / "logs" / "hardware_communication.html",
    )
    dialog = LoggingSettingsDialog(logger, settings)
    dialog.retention_days.setValue(45)
    dialog.measurement_retention_days.setValue(90)
    dialog.maximum_file_size.setValue(12)
    dialog.maximum_rotated_files.setValue(18)
    dialog.total_storage_limit.setValue(512)
    dialog.compression_enabled.setChecked(False)
    dialog.automatic_cleanup.setChecked(False)

    dialog._save()

    retention = logger.retention_settings
    assert retention.retention_days == 45
    assert retention.measurement_retention_days == 90
    assert retention.maximum_file_size_mb == 12
    assert retention.maximum_rotated_files == 18
    assert retention.total_storage_limit_mb == 512
    assert not retention.compression_enabled
    assert not retention.automatic_cleanup_enabled
    assert int(settings.value("logging/retention_days")) == 45
    assert int(settings.value("logging/measurement_retention_days")) == 90


def test_settings_hub_uses_resizable_left_navigation() -> None:
    application()
    opened: list[str] = []

    def page(key: str) -> QWidget:
        opened.append(key)
        editor = QWidget()
        editor.setObjectName(f"embedded_{key}")
        return editor

    dialog = SettingsHubDialog(
        (
            ("devices", "Eszközök", "Eszközleírás", lambda: page("devices")),
            ("logging", "Naplózás", "Naplóleírás", lambda: page("logging")),
        ),
    )
    dialog.select_page("logging")

    assert dialog.isSizeGripEnabled()
    assert dialog.minimumWidth() >= 720
    assert dialog.navigation.count() == 2
    assert dialog.navigation.currentItem().data(Qt.ItemDataRole.UserRole) == "logging"
    assert dialog.pages.currentIndex() == 1
    assert dialog.findChild(QWidget, "embedded_logging") is not None
    assert dialog.findChild(QPushButton, "open_settings_logging") is None
    assert opened == ["logging"]
    dialog.navigation.setCurrentRow(0)
    assert dialog.findChild(QWidget, "embedded_devices") is not None
    assert opened == ["logging", "devices"]
    dialog.close()


def test_dashboard_settings_hub_embeds_real_editors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application()
    window = build_simulated_dashboard(
        tmp_path / "raw.csv",
        tmp_path / "projects.sqlite3",
        settings=QSettings(
            str(tmp_path / "settings.ini"), QSettings.Format.IniFormat
        ),
    )
    window._set_developer_mode(True)
    hubs: list[SettingsHubDialog] = []
    monkeypatch.setattr(
        SettingsHubDialog,
        "exec",
        lambda dialog: hubs.append(dialog) or QDialog.DialogCode.Rejected,
    )

    for key, editor_type in (
        ("devices", DeviceSettingsDialog),
        ("logging", LoggingSettingsDialog),
        ("calibration", CalibrationSettingsDialog),
        ("control_cycle", ControlCycleSettingsDialog),
        ("pump_telemetry", PumpTelemetrySettingsDialog),
        ("simulation", SimulationSettingsPage),
    ):
        window._open_settings_hub(key)
        hub = hubs[-1]
        assert hub.navigation.currentItem().data(Qt.ItemDataRole.UserRole) == key
        editor = hub.findChild(editor_type)
        assert editor is not None
        assert editor.windowType() == Qt.WindowType.Widget
        assert editor.parentWidget() is not window

    window._open_settings_hub("appearance")
    appearance_hub = hubs[-1]
    assert appearance_hub.findChild(QComboBox, "settings_theme") is not None
    assert not any(
        button.text().endswith("megnyitása")
        for hub in hubs
        for button in hub.findChildren(QPushButton)
    )
    window.close()


def test_simulation_settings_page_applies_model_and_injects_faults() -> None:
    application()
    jacket = SimulatedPump(pressure_bar=120.0)
    injection = SimulatedPump(pressure_bar=100.0)
    daq = SimulatedDataAcquisition()
    daq.inputs.update(line_pressure=2.0, differential_pressure=1.5)
    valve = SimulatedValveActuator()
    events: list[str] = []
    page = SimulationSettingsPage(
        jacket=jacket,
        injection=injection,
        daq=daq,
        valve=valve,
        log_event=events.append,
    )
    jacket.connect()
    injection.connect()
    page.jacket_ramp.setValue(3.5)
    page.response_delay.setValue(250.0)
    next(
        button
        for button in page.findChildren(QPushButton)
        if button.text() == "Szimulációs modell alkalmazása"
    ).click()

    assert jacket.pressure_ramp_bar_per_second == pytest.approx(3.5)
    assert jacket.response_delay.maximum_seconds == pytest.approx(0.25)

    next(
        button
        for button in page.findChildren(QPushButton)
        if button.text() == "Hiba injektálása"
    ).click()
    _, quality = jacket.read_cached_status()
    assert quality is DataQuality.STALE
    assert "pressure_stale" in events[-1]

    next(
        button
        for button in page.findChildren(QPushButton)
        if button.text() == "Minden szimulált hiba törlése"
    ).click()
    _, quality = jacket.read_cached_status()
    assert quality is DataQuality.GOOD
    page.close()


def test_developer_control_cycle_settings_are_persisted(tmp_path: Path) -> None:
    application()
    settings = QSettings(str(tmp_path / "cycle.ini"), QSettings.Format.IniFormat)
    dialog = ControlCycleSettingsDialog(settings)
    dialog.control_interval.setValue(1.0)
    dialog.watchdog_tolerance.setValue(0.2)

    dialog._save()

    assert float(settings.value("developer/control_interval_seconds")) == 1.0
    assert float(settings.value("developer/watchdog_tolerance_seconds")) == 0.2
    assert "1.200 s" in dialog.deadline.text()


def test_pump_telemetry_stale_settings_are_validated_and_persisted(
    tmp_path: Path,
) -> None:
    application()
    settings = QSettings(
        str(tmp_path / "pump-telemetry.ini"), QSettings.Format.IniFormat
    )
    dialog = PumpTelemetrySettingsDialog(settings)
    dialog.pressure_poll.setValue(0.5)
    dialog.slow_poll.setValue(2.0)
    dialog.pressure_stale.setValue(2.5)
    dialog.slow_stale.setValue(5.0)
    dialog.startup_timeout.setValue(8.0)

    dialog._save()
    intervals = PumpTelemetrySettingsDialog.intervals(settings)

    assert intervals.pressure_seconds == pytest.approx(0.5)
    assert intervals.slow_telemetry_seconds == pytest.approx(2.0)
    assert intervals.pressure_stale_seconds == pytest.approx(2.5)
    assert intervals.slow_telemetry_stale_seconds == pytest.approx(5.0)
    assert intervals.startup_timeout_seconds == pytest.approx(8.0)


def test_pump_telemetry_menu_rejects_stale_limit_below_poll_interval(
    tmp_path: Path,
) -> None:
    application()
    settings = QSettings(
        str(tmp_path / "invalid-pump-telemetry.ini"),
        QSettings.Format.IniFormat,
    )
    dialog = PumpTelemetrySettingsDialog(settings)
    dialog.pressure_poll.setValue(2.0)
    dialog.pressure_stale.setValue(1.0)

    assert not dialog._save_button.isEnabled()
    assert "HIBÁS BEÁLLÍTÁS" in dialog.validation.text()
    dialog.close()


def test_dashboard_runtime_uses_developer_cycle_settings(tmp_path: Path) -> None:
    application()
    settings = QSettings(str(tmp_path / "runtime.ini"), QSettings.Format.IniFormat)
    settings.setValue("developer/control_interval_seconds", 1.0)
    settings.setValue("developer/watchdog_tolerance_seconds", 0.2)

    window = build_simulated_dashboard(
        tmp_path / "raw.csv", tmp_path / "projects.sqlite3", settings=settings
    )

    assert window._runtime._interval == pytest.approx(1.0)
    assert window._runtime._watchdog_tolerance == pytest.approx(0.2)
    window.close()


def test_stage_settings_input_fields_are_labelled() -> None:
    application()
    dialog = StageSettingsDialog()
    assert_input_fields_are_labelled(dialog)
    dialog.close()


def test_manual_control_queues_command_during_telemetry_and_shows_success() -> None:
    app = application()
    dialog = PumpControlDialog(  # type: ignore[arg-type]
        object(),
        object(),
        lambda: "Teszt szakasz",
    )
    dialog._telemetry_timer.stop()
    executed: list[str] = []
    dialog._telemetry_active = True

    dialog._execute(lambda: executed.append("RUN"), "tesztparancs")

    assert executed == []
    assert len(dialog._pending_commands) == 1
    assert "várakozik" in dialog._operation_status.text()
    dialog._telemetry_failed("teszt telemetriahiba")
    for _ in range(20):
        app.processEvents()
        if executed:
            break
        sleep(0.01)
    for _ in range(20):
        app.processEvents()
        if "SIKERES" in dialog._operation_status.text():
            break
        sleep(0.01)

    assert executed == ["RUN"]
    assert dialog._operation_status.text() == "SIKERES — tesztparancs"
    assert all(button.isEnabled() for button in dialog._buttons)
    dialog.close()


def test_manual_control_queues_multiple_commands_in_order() -> None:
    app = application()
    dialog = PumpControlDialog(  # type: ignore[arg-type]
        object(),
        object(),
        lambda: "Teszt szakasz",
    )
    dialog._telemetry_timer.stop()
    executed: list[str] = []
    dialog._telemetry_active = True

    dialog._execute(lambda: executed.append("REMOTE"), "remote")
    dialog._execute(lambda: executed.append("CONFIGURE"), "configure")
    dialog._execute(lambda: executed.append("RUN"), "run")

    assert len(dialog._pending_commands) == 3
    dialog._telemetry_failed("teszt telemetriahiba")
    for _ in range(100):
        app.processEvents()
        if executed == ["REMOTE", "CONFIGURE", "RUN"]:
            break
        sleep(0.01)

    assert executed == ["REMOTE", "CONFIGURE", "RUN"]
    assert not dialog._pending_commands
    dialog.close()


def test_manual_control_has_combined_remote_connect_and_closes_ports() -> None:
    app = application()

    class ManualService:
        def __init__(self) -> None:
            self.connect_remote_calls: list[PumpRole] = []
            self.shutdown_calls = 0

        def connect_remote(self, role: PumpRole) -> PumpStatus:
            self.connect_remote_calls.append(role)
            return PumpStatus(10.0, 0.0, 100.0)

        def shutdown_connections(self) -> tuple[str, ...]:
            self.shutdown_calls += 1
            return ()

    service = ManualService()
    dialog = PumpControlDialog(  # type: ignore[arg-type]
        service,
        object(),
        lambda: "Teszt szakasz",
    )
    dialog._telemetry_timer.stop()
    button_texts = {button.text() for button in dialog.findChildren(QPushButton)}

    assert "CSATLAKOZÁS + REMOTE" in button_texts
    assert "REMOTE" not in button_texts
    assert "LOCAL" not in button_texts

    dialog._connect_pump(PumpRole.JACKET)
    for _ in range(100):
        app.processEvents()
        if service.connect_remote_calls:
            break
        sleep(0.01)
    assert service.connect_remote_calls == [PumpRole.JACKET]

    dialog._request_close()
    for _ in range(100):
        app.processEvents()
        if dialog._shutdown_complete:
            break
        sleep(0.01)
    assert service.shutdown_calls == 1
    assert dialog._shutdown_complete


def test_manual_control_retains_partial_pump_status_when_sensor_is_missing() -> None:
    app = application()
    full_safety_checks: list[str] = []

    class PartialService:
        @staticmethod
        def read_available_statuses() -> tuple[
            dict[PumpRole, PumpStatus], dict[PumpRole, str]
        ]:
            return (
                {PumpRole.JACKET: PumpStatus(120.0, 0.0, 200.0)},
                {PumpRole.INJECTION: "nincs csatlakoztatva"},
            )

    class MissingSensorLoop:
        @staticmethod
        def read_pressure_inputs_individually() -> tuple[
            dict[str, float], dict[str, str]
        ]:
            return (
                {"line_pressure": 12.5},
                {"differential_pressure": "sensor is not connected"},
            )

        @staticmethod
        def observe_once(*, active_stage: str) -> object:
            full_safety_checks.append(active_stage)
            raise ConnectionError(
                f"{active_stage}: differential pressure sensor is not connected"
            )

    dialog = PumpControlDialog(  # type: ignore[arg-type]
        PartialService(),
        MissingSensorLoop(),
        lambda: "Teszt szakasz",
    )
    dialog._telemetry_timer.stop()

    dialog._refresh_statuses()
    for _ in range(100):
        app.processEvents()
        if not dialog._telemetry_active:
            break
        sleep(0.01)

    assert "KAPCSOLÓDVA" in dialog._status_labels[PumpRole.JACKET].text()
    assert "120.00 bar" in dialog._status_labels[PumpRole.JACKET].text()
    assert "NINCS KAPCSOLAT" in dialog._status_labels[PumpRole.INJECTION].text()
    assert dialog._line_pressure_status.text() == "KAPCSOLÓDVA | 12.50 bar"
    assert "sensor is not connected" in dialog._differential_pressure_status.text()
    assert "RÉSZLEGES KAPCSOLAT" in dialog._safety_status.text()
    assert "manuális biztonsági profil" in dialog._safety_status.text()
    assert full_safety_checks == []
    dialog.close()


def test_developer_menu_does_not_duplicate_direct_device_control(
    tmp_path: Path,
) -> None:
    application()
    window = build_simulated_dashboard(
        tmp_path / "raw.csv", tmp_path / "projects.sqlite3"
    )
    window._set_developer_mode(True)
    developer_action = next(
        action for action in window.menuBar().actions() if action.text() == "Developer"
    )
    developer_menu = developer_action.menu()

    assert developer_menu is not None
    assert "Közvetlen eszközkezelés…" not in (
        action.text() for action in developer_menu.actions()
    )
    window.close()


def test_guided_device_test_window_close_uses_central_safe_shutdown(
    tmp_path: Path,
) -> None:
    application()
    safe_actions: list[str] = []
    connections = ConnectionTestResult(
        tuple(
            DeviceConnectionResult(device, True, device.value)
            for device in HardwareTestDevice
        )
    )
    session = FunctionalDeviceTestSession(
        run_mode=RunMode.HARDWARE,
        application_state=lambda: ApplicationState.READY,
        runtime_running=lambda: False,
        pumps_running=lambda: False,
        active_fault=lambda: False,
        connection_result=connections,
        stop_pumps=lambda: safe_actions.append("STOP ALL") or (),
        set_safe_output=lambda: safe_actions.append("SAFE OUTPUT"),
        write_voltage=lambda _value: None,
        write_valve_percent=lambda _value: None,
        report=DeviceTestReport.create(
            application_version="test", configuration_hash="abc"
        ),
    )
    dialog = DeviceTestWizard(
        session, report_path=tmp_path / "guided-test.json"
    )
    for checkbox in dialog._checklist:
        checkbox.setChecked(True)
    dialog._begin()

    dialog.close()

    assert safe_actions == ["STOP ALL", "SAFE OUTPUT"]
    assert (tmp_path / "guided-test.json").is_file()


def test_text_labels_have_no_theme_background_fill() -> None:
    transparent_rule = "QLabel { background: transparent; }"
    for stylesheet in (LIGHT_STYLESHEET, DARK_STYLESHEET, SYSTEM_STYLESHEET):
        assert transparent_rule in stylesheet
        assert "QLineEdit, QTextEdit, QPlainTextEdit" in stylesheet
        assert "background: transparent" in stylesheet


def test_measurement_pump_startup_dialog_requires_all_values_and_confirmation() -> None:
    application()
    dialog = MeasurementPumpStartupDialog(
        MeasurementPumpPlan(120.0, 0.0, 100.0, 10.0),
        maximum_jacket_pressure_bar=400.0,
        maximum_injection_pressure_bar=350.0,
        minimum_jacket_margin_bar=20.0,
    )

    assert not dialog.start_button.isEnabled()
    dialog.jacket_buildup_flow.setValue(60.0)
    assert not dialog.start_button.isEnabled()
    dialog.confirmation.setText("START MEASUREMENT PUMPS")

    assert dialog.start_button.isEnabled()
    assert dialog.plan() == MeasurementPumpPlan(
        120.0,
        60.0,
        100.0,
        10.0,
        jacket_pressure_limit_bar=400.0,
        injection_pressure_limit_bar=350.0,
    )
    dialog.injection_start_pressure.setValue(101.0)
    assert not dialog.start_button.isEnabled()


def test_measurement_table_model_uses_excel_columns_and_hungarian_time() -> None:
    application()
    header = CsvMeasurementWriter.HEADER
    values = ["1"] * len(header)
    values[0] = "2026-07-13T10:30:00+00:00"
    values[header.index("active_stage")] = "víz"
    table = MeasurementTable(header, (tuple(values),))
    model = MeasurementTableModel()

    model.set_page(table, (0,))

    assert model.columnCount() == len(header)
    assert tuple(
        model.headerData(index, Qt.Orientation.Horizontal)
        for index in range(model.columnCount())
    ) == header
    assert model.data(model.index(0, 0)) == "2026-07-13 12:30:00.000 CEST"


def test_measurement_history_table_shares_stage_and_time_filters() -> None:
    application()
    header = CsvMeasurementWriter.HEADER
    stage_index = header.index("active_stage")
    start = datetime(2026, 7, 13, 10, 0, tzinfo=UTC)
    rows: list[tuple[str, ...]] = []
    for index in range(1001):
        values = ["1"] * len(header)
        values[0] = (start + timedelta(seconds=index)).isoformat()
        values[stage_index] = "víz" if index % 2 == 0 else "olaj"
        rows.append(tuple(values))
    view = MeasurementHistoryView()
    view._table = MeasurementTable(header, tuple(rows))
    view._stage_filter.addItem("víz", "víz")
    view._stage_filter.addItem("olaj", "olaj")

    view._refresh_plot()

    assert view._content_tabs.tabText(0) == "Grafikon"
    assert view._content_tabs.tabText(1) == "Táblázat"
    assert view._table_model.rowCount() == view.TABLE_PAGE_SIZE
    assert view._next_page.isEnabled()
    assert view._table_model.columnCount() == len(CsvMeasurementWriter.HEADER)

    view._stage_filter.setCurrentIndex(view._stage_filter.findData("olaj"))

    assert len(view._filtered_row_indices) == 500
    assert view._table_model.rowCount() == 500
    assert not view._next_page.isEnabled()

    view._time_range.setCurrentIndex(view._time_range.findData(600.0))

    assert len(view._filtered_row_indices) == 300
    assert view._table_model.rowCount() == 300


def test_completed_stage_excel_export_runs_in_background(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application()
    window = build_simulated_dashboard(
        tmp_path / "data" / "measurement.csv",
        tmp_path / "projects.sqlite3",
    )
    source = (
        window._data_directory
        / "projects"
        / "2026"
        / "project"
        / "Projekt_víz_live_raw.csv"
    )
    source.parent.mkdir(parents=True)
    source.write_text("raw", encoding="utf-8")
    calls: list[tuple[Path, Path, str]] = []

    def export(
        source_path: Path, destination: Path, *, stage_name: str
    ) -> None:
        calls.append((source_path, destination, stage_name))
        destination.write_text("excel", encoding="utf-8")

    monkeypatch.setattr("eor_control.ui.export_measurement_excel", export)
    window._queue_completed_stage_export(source, "víz")
    window._start_pending_stage_exports()
    destination = source.with_name("Projekt.xlsx")
    for _ in range(100):
        application().processEvents()
        if destination.is_file() and not window._stage_export_active:
            break
        sleep(0.01)

    assert calls == [(source, destination, "víz")]
    assert destination.read_text(encoding="utf-8") == "excel"
    window.close()


def test_windows_application_identity_is_set_for_taskbar_icon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FakeSetter:
        argtypes: list[object] = []
        restype: object = None

        def __call__(self, app_id: str) -> None:
            calls.append(app_id)

    class FakeShell:
        SetCurrentProcessExplicitAppUserModelID = FakeSetter()

    monkeypatch.setattr("eor_control.ui.sys.platform", "win32")
    monkeypatch.setattr(
        "eor_control.ui.ctypes.WinDLL",
        lambda *_args, **_kwargs: FakeShell(),
        raising=False,
    )

    configure_windows_application_identity()

    assert calls == [WINDOWS_APP_USER_MODEL_ID]


def test_form_control_subbuttons_are_covered_by_both_themes() -> None:
    for theme, stylesheet in (("light", LIGHT_STYLESHEET), ("dark", DARK_STYLESHEET)):
        assert "border-top-left-radius: 6px" in stylesheet
        assert "border-top-right-radius: 6px" in stylesheet
        assert "QComboBox::drop-down" in stylesheet
        assert "QComboBox QAbstractItemView" in stylesheet
        assert "QDoubleSpinBox::up-button" in stylesheet
        assert "QDoubleSpinBox::down-button" in stylesheet
        assert "QSpinBox::up-button" in stylesheet
        assert "QSpinBox::down-button" in stylesheet
        assert "QComboBox:focus" in stylesheet
        assert "QDoubleSpinBox:disabled" in stylesheet
        resolved = resolved_theme_stylesheet(stylesheet, theme)
        assert "__THEME_" not in resolved
        assert f"arrow-down-{theme}.svg" in resolved
        assert f"arrow-up-{theme}.svg" in resolved


def test_scrollbars_are_covered_by_both_themes() -> None:
    for stylesheet in (LIGHT_STYLESHEET, DARK_STYLESHEET):
        assert "QScrollBar:vertical" in stylesheet
        assert "QScrollBar:horizontal" in stylesheet
        assert "QScrollBar::handle:vertical" in stylesheet
        assert "QTableWidget" in stylesheet
        assert "QHeaderView::section" in stylesheet
        assert "QScrollBar::handle:horizontal" in stylesheet
        assert "QScrollBar::add-line" in stylesheet
        assert "QScrollBar::sub-line" in stylesheet
        assert "QAbstractScrollArea::corner" in stylesheet
        assert "QSplitter::handle" in stylesheet


class UnusedTester:
    def test(self, configuration: object) -> ConnectionTestResult:
        raise AssertionError("test should not be called directly")


def test_device_settings_discovers_dropdown_choices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = application()
    settings = QSettings(str(tmp_path / "hardware.ini"), QSettings.Format.IniFormat)
    log_path = tmp_path / "communication.html"
    diagnostics = DiagnosticLogger(log_path)
    diagnostics.configure(enabled=True, categories=[DiagnosticCategory.SYSTEM])
    direct_configurations: list[HardwareConfiguration] = []
    dialog = DeviceSettingsDialog(
        UnusedTester(),  # type: ignore[arg-type]
        settings=settings,
        current_mode=RunMode.SIMULATION,
        diagnostics=diagnostics,
        developer_mode=True,
        direct_control_opener=direct_configurations.append,
        discoverer=lambda: HardwareDiscovery(
            serial_ports=(
                SerialPortInfo("COM8", "USB Serial Port", "FTDI", "FT232R"),
                SerialPortInfo("COM9", "PCI Serial Port"),
            ),
            ni_input_channels=(
                NiPhysicalChannelInfo("Dev2/ai0", "Dev2", "NI USB-6001", "1234"),
                NiPhysicalChannelInfo("Dev2/ai1", "Dev2", "NI USB-6001", "1234"),
            ),
            ni_output_channels=(
                NiPhysicalChannelInfo("Dev2/ao0", "Dev2", "NI USB-6001", "1234"),
            ),
        ),
    )

    assert isinstance(dialog.jacket_port, QComboBox)
    assert_input_fields_are_labelled(dialog)
    dialog.show()
    app.processEvents()
    expected_pump_labels = {
        dialog.jacket_port: "Köpenypumpa soros portja (COM)",
        dialog.jacket_id: "Köpenypumpa DASNET eszközazonosítója (0–9)",
        dialog.jacket_channel: "Köpenypumpa pumpacsatornája (A–D)",
        dialog.injection_port: "Besajtolópumpa soros portja (COM)",
        dialog.injection_id: "Besajtolópumpa DASNET eszközazonosítója (0–9)",
        dialog.injection_channel: "Besajtolópumpa pumpacsatornája (A–D)",
        dialog.baud_rate: "Soros kommunikáció sebessége (baud)",
        dialog.pump_cabling_notes: "Pumpák kábelezési megjegyzése",
    }
    all_labels = dialog.findChildren(QLabel)
    for field, expected_text in expected_pump_labels.items():
        label = next(item for item in all_labels if item.buddy() is field)
        assert label.text() == expected_text
        assert label.isVisible()
        assert label.width() > 0
        assert label.height() > 0
    assert dialog.device_tabs.count() == 2
    assert dialog.device_tabs.tabText(0) == "Pumpák"
    assert dialog.device_tabs.tabText(1) == "NI mérés és szelep"
    assert not dialog.jacket_port.isEditable()
    assert dialog.jacket_port.findData("COM8") >= 0
    assert dialog.jacket_port.itemText(dialog.jacket_port.findData("COM8")) == (
        "USB Serial Port (COM8)"
    )
    assert dialog.injection_port.findData("COM9") >= 0
    dialog.jacket_port.setCurrentIndex(dialog.jacket_port.findData("COM8"))
    dialog.injection_port.setCurrentIndex(dialog.injection_port.findData("COM9"))
    assert dialog.ni_device.currentData() is None
    assert not dialog.line_channel.isEnabled()
    assert not dialog.delta_channel.isEnabled()
    assert not dialog.valve_channel.isEnabled()
    dialog.ni_device.setCurrentIndex(dialog.ni_device.findData("Dev2"))
    assert dialog.line_channel.isEnabled()
    assert dialog.delta_channel.isEnabled()
    assert dialog.valve_channel.isEnabled()
    dialog.delta_channel.setCurrentIndex(dialog.delta_channel.findData("Dev2/ai0"))
    assert dialog.line_channel.currentData() != dialog.delta_channel.currentData()
    assert dialog._read_configuration().jacket_port == "COM8"
    assert dialog._read_configuration().injection_port == "COM9"
    assert dialog.line_channel.findData("Dev2/ai0") >= 0
    assert dialog.line_channel.itemText(dialog.line_channel.findData("Dev2/ai0")) == (
        "1. analóg bemenet (AI0)"
    )
    assert dialog.delta_channel.findData("Dev2/ai1") >= 0
    assert dialog.valve_channel.findData("Dev2/ao0") >= 0
    dialog._replace_ni_choices(
        dialog.line_channel,
        dialog._discovered_ni_inputs,
        " dev2/AI0 ",
        0,
    )
    assert dialog.line_channel.currentData() == "Dev2/ai0"
    assert dialog.line_channel.currentText() == "1. analóg bemenet (AI0)"
    assert dialog.line_channel.findText(" dev2/AI0 ") == -1
    dialog.line_channel.setCurrentIndex(dialog.line_channel.findData("Dev2/ai0"))
    dialog.delta_channel.setCurrentIndex(dialog.delta_channel.findData("Dev2/ai1"))
    dialog.valve_channel.setCurrentIndex(dialog.valve_channel.findData("Dev2/ao0"))
    assert dialog._read_configuration().line_pressure_channel == "Dev2/ai0"
    assert "2 soros csatlakozó" in dialog._discovery_status.text()
    assert not dialog._discovery_status.isHidden()
    discovery_log = log_path.read_text(encoding="utf-8")
    assert 'data-category="system"' in discovery_log
    assert "2 COM-port" in discovery_log
    assert set(dialog._device_test_buttons) == set(HardwareTestDevice)
    dialog._test_passed(
        ConnectionTestResult(
            (
                DeviceConnectionResult(
                    HardwareTestDevice.JACKET_PUMP, False, "pump unavailable"
                ),
                DeviceConnectionResult(
                    HardwareTestDevice.INJECTION_PUMP, True, "260D injection"
                ),
                DeviceConnectionResult(
                    HardwareTestDevice.LINE_PRESSURE, True, "Dev2/ai0: 2.0 V", 2.0
                ),
                DeviceConnectionResult(
                    HardwareTestDevice.DIFFERENTIAL_PRESSURE,
                    True,
                    "Dev2/ai1: 1.5 V",
                    1.5,
                ),
            )
        )
    )
    assert not dialog._activate_button.isEnabled()
    assert dialog._direct_control_button.isEnabled()
    assert "SIKERTELEN" in dialog._connection_result_labels[
        HardwareTestDevice.JACKET_PUMP
    ].text()
    assert "SIKERES" in dialog._connection_result_labels[
        HardwareTestDevice.INJECTION_PUMP
    ].text()
    assert "Sikeres kapcsolatok: 3/4" in dialog._result_label.text()
    dialog._open_direct_control()
    assert direct_configurations == [dialog._read_configuration()]
    assert dialog.result() != QDialog.DialogCode.Accepted
    dialog.close()


def test_device_settings_shows_when_no_device_is_available(tmp_path: Path) -> None:
    application()
    dialog = DeviceSettingsDialog(
        UnusedTester(),  # type: ignore[arg-type]
        settings=QSettings(str(tmp_path / "empty.ini"), QSettings.Format.IniFormat),
        current_mode=RunMode.SIMULATION,
        discoverer=HardwareDiscovery,
    )

    assert dialog.jacket_port.currentText() == "Nincs elérhető soros eszköz"
    assert not dialog.jacket_port.isEnabled()
    assert dialog.injection_port.currentText() == "Nincs elérhető soros eszköz"
    assert dialog.ni_device.currentText() == "Nincs elérhető NI eszköz"
    assert not dialog.ni_device.isEnabled()
    assert dialog.line_channel.currentText() == "Előbb válassz NI eszközt"
    assert not dialog.line_channel.isEnabled()
    assert dialog._discovery_status.isHidden()
    dialog.close()


def test_device_settings_can_remove_optional_line_pressure_device(
    tmp_path: Path,
) -> None:
    application()
    dialog = DeviceSettingsDialog(
        UnusedTester(),  # type: ignore[arg-type]
        settings=QSettings(str(tmp_path / "modular.ini"), QSettings.Format.IniFormat),
        current_mode=RunMode.SIMULATION,
        discoverer=lambda: HardwareDiscovery(
            serial_ports=(SerialPortInfo("COM1"), SerialPortInfo("COM2")),
            ni_input_channels=(NiPhysicalChannelInfo("Dev1/ai1", "Dev1"),),
            ni_output_channels=(NiPhysicalChannelInfo("Dev1/ao0", "Dev1"),),
        ),
    )
    dialog.jacket_port.setCurrentIndex(dialog.jacket_port.findData("COM1"))
    dialog.injection_port.setCurrentIndex(dialog.injection_port.findData("COM2"))
    dialog.ni_device.setCurrentIndex(dialog.ni_device.findData("Dev1"))

    dialog.line_enabled.setChecked(False)
    configuration = dialog._read_configuration()

    assert not configuration.line_pressure_enabled
    assert configuration.line_pressure_channel == ""
    assert configuration.measurement_ready
    assert not dialog.line_channel.isEnabled()
    assert not dialog._device_test_buttons[HardwareTestDevice.LINE_PRESSURE].isEnabled()
    assert "NINCS HOZZÁADVA" in dialog._connection_result_labels[
        HardwareTestDevice.LINE_PRESSURE
    ].text()
    dialog.close()


def test_device_settings_uses_selected_project_device_profile(tmp_path: Path) -> None:
    application()
    dialog = DeviceSettingsDialog(
        UnusedTester(),  # type: ignore[arg-type]
        settings=QSettings(str(tmp_path / "project-profile.ini"), QSettings.Format.IniFormat),
        current_mode=RunMode.SIMULATION,
        device_profile={
            "jacket_pump_enabled": True,
            "injection_pump_enabled": False,
            "line_pressure_enabled": False,
            "differential_pressure_enabled": True,
            "valve_output_enabled": True,
        },
        discoverer=HardwareDiscovery,
    )

    assert dialog.jacket_enabled.isChecked()
    assert not dialog.injection_enabled.isChecked()
    assert not dialog.line_enabled.isChecked()
    assert dialog.delta_enabled.isChecked()
    assert dialog.valve_enabled.isChecked()
    dialog.close()


def test_valve_only_profile_can_open_independent_direct_control(
    tmp_path: Path,
) -> None:
    application()
    direct_configurations: list[HardwareConfiguration] = []
    dialog = DeviceSettingsDialog(
        UnusedTester(),  # type: ignore[arg-type]
        settings=QSettings(str(tmp_path / "valve-only.ini"), QSettings.Format.IniFormat),
        current_mode=RunMode.SIMULATION,
        developer_mode=True,
        direct_control_opener=direct_configurations.append,
        device_profile={
            "jacket_pump_enabled": False,
            "injection_pump_enabled": False,
            "line_pressure_enabled": False,
            "differential_pressure_enabled": False,
            "valve_output_enabled": True,
        },
        discoverer=lambda: HardwareDiscovery(
            ni_output_channels=(NiPhysicalChannelInfo("Dev1/ao0", "Dev1"),)
        ),
    )
    dialog.ni_device.setCurrentIndex(dialog.ni_device.findData("Dev1"))

    assert dialog._direct_control_button.isEnabled()
    assert "ÖNÁLLÓAN KEZELHETŐ" in dialog._valve_test_status.text()
    dialog._open_direct_control()

    assert dialog.result() != QDialog.DialogCode.Accepted
    assert len(direct_configurations) == 1
    assert direct_configurations[0].valve_output_enabled
    assert direct_configurations[0].enabled_test_devices() == ()


def test_device_settings_rejects_successful_ni_read_outside_calibration(
    tmp_path: Path,
) -> None:
    application()
    dialog = DeviceSettingsDialog(
        UnusedTester(),  # type: ignore[arg-type]
        settings=QSettings(str(tmp_path / "range.ini"), QSettings.Format.IniFormat),
        current_mode=RunMode.SIMULATION,
        discoverer=HardwareDiscovery,
    )

    dialog._test_passed(
        ConnectionTestResult(
            (
                DeviceConnectionResult(
                    HardwareTestDevice.JACKET_PUMP, True, "260D jacket"
                ),
                DeviceConnectionResult(
                    HardwareTestDevice.INJECTION_PUMP, True, "260D injection"
                ),
                DeviceConnectionResult(
                    HardwareTestDevice.LINE_PRESSURE,
                    True,
                    "Dev1/ai0: -1.188 V",
                    -1.188,
                ),
                DeviceConnectionResult(
                    HardwareTestDevice.DIFFERENTIAL_PRESSURE,
                    True,
                    "Dev1/ai1: -0.806 V",
                    -0.806,
                ),
            )
        )
    )

    assert not dialog._activate_button.isEnabled()
    assert "Sikeres kapcsolatok: 2/4" in dialog._result_label.text()
    assert "1–5 V" in dialog._connection_result_labels[
        HardwareTestDevice.LINE_PRESSURE
    ].text()
    assert "kapocsmódot" in dialog._connection_result_labels[
        HardwareTestDevice.DIFFERENTIAL_PRESSURE
    ].text()
    dialog.close()


def test_device_settings_keeps_actions_visible_on_small_screen(tmp_path: Path) -> None:
    app = application()
    dialog = DeviceSettingsDialog(
        UnusedTester(),  # type: ignore[arg-type]
        settings=QSettings(str(tmp_path / "small-screen.ini"), QSettings.Format.IniFormat),
        current_mode=RunMode.SIMULATION,
        discoverer=HardwareDiscovery,
        developer_mode=True,
        direct_control_opener=lambda _configuration: None,
    )
    dialog.resize(480, 420)
    dialog.show()
    app.processEvents()

    assert dialog._content_scroll.verticalScrollBar().maximum() > 0
    assert dialog._content_scroll.horizontalScrollBar().maximum() == 0
    assert dialog._content_widget.minimumWidth() == 0
    assert dialog._content_widget.sizePolicy().horizontalPolicy() == (
        QSizePolicy.Policy.Ignored
    )
    action_buttons = (
        dialog._save_button,
        dialog._test_button,
        dialog._activate_button,
        dialog._direct_control_button,
        dialog._cancel_button,
    )
    for button in action_buttons:
        assert button.isVisible()
        assert button.geometry().bottom() <= dialog.contentsRect().bottom()
    assert len({button.geometry().center().y() for button in action_buttons}) == 1
    assert [
        button.geometry().left() for button in action_buttons
    ] == sorted(button.geometry().left() for button in action_buttons)

    dialog.close()


def test_project_selector_lists_last_stage_and_can_create_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application()
    settings = QSettings(str(tmp_path / "selector.ini"), QSettings.Format.IniFormat)
    with ProjectRepository(tmp_path / "selector.sqlite3") as repository:
        older = repository.create_project(
            name="Korábbi projekt",
            configuration={},
            calibration_snapshot={},
        )
        repository.add_stage(older.id, "Első fázis")
        last_stage = repository.add_stage(older.id, "Olajkiszorítás")
        repository.create_project(
            name="Újabb projekt",
            configuration={},
            calibration_snapshot={},
        )
        settings.setValue(
            f"project/last_stage_by_project/{older.id}", last_stage.id
        )
        dialog = ProjectSelectionDialog(
            repository,
            settings=settings,
            selected_project_id=older.id,
            selected_stage_id=last_stage.id,
            configuration={"mode": "simulation"},
            calibration_snapshot={},
        )

        older_row = next(
            row
            for row in range(dialog.project_table.rowCount())
            if dialog.project_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            == older.id
        )
        assert dialog.project_table.rowCount() == 2
        assert dialog.project_table.item(older_row, 1).text() == "Olajkiszorítás"
        assert dialog.selected_project_id == older.id
        assert dialog.selected_stage_id == last_stage.id

        monkeypatch.setattr(
            QInputDialog, "getText", lambda *_args, **_kwargs: ("Új projekt", True)
        )
        monkeypatch.setattr(
            QInputDialog,
            "getMultiLineText",
            lambda *_args, **_kwargs: ("Megjegyzés", True),
        )
        dialog._create_project()
        assert dialog.project_table.item(dialog.project_table.currentRow(), 0).text() == (
            "Új projekt"
        )
        assert dialog.stage_selector.currentText() == "Hidegvizes mérés"
        dialog.close()


def test_project_selector_can_delete_project_but_keeps_raw_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application()
    settings = QSettings(str(tmp_path / "delete.ini"), QSettings.Format.IniFormat)
    raw_file = tmp_path / "data" / "projects" / "archived_raw.csv"
    raw_file.parent.mkdir(parents=True)
    raw_file.write_text("measurement", encoding="utf-8")
    with ProjectRepository(tmp_path / "delete.sqlite3") as repository:
        project = repository.create_project(
            name="Törlendő projekt",
            configuration={},
            calibration_snapshot={},
        )
        stage = repository.add_stage(project.id, "Mérési fázis")
        settings.setValue("project/last_project_id", project.id)
        settings.setValue("project/last_stage_id", stage.id)
        settings.setValue(f"project/last_stage_by_project/{project.id}", stage.id)
        dialog = ProjectSelectionDialog(
            repository,
            settings=settings,
            selected_project_id=project.id,
            selected_stage_id=stage.id,
            configuration={},
            calibration_snapshot={},
        )
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
        )

        dialog._delete_project()

        assert repository.list_projects() == ()
        assert dialog.project_table.rowCount() == 0
        assert settings.value("project/last_project_id") is None
        assert settings.value("project/last_stage_id") is None
        assert settings.value(f"project/last_stage_by_project/{project.id}") is None
        assert raw_file.read_text(encoding="utf-8") == "measurement"
        dialog.close()


def test_project_settings_adds_and_removes_devices_without_validation(
    tmp_path: Path,
) -> None:
    application()
    with ProjectRepository(tmp_path / "project-devices.sqlite3") as repository:
        project = repository.create_project(
            name="Moduláris projekt",
            configuration={
                "mode": "simulation",
                "devices": {
                    "jacket_pump_enabled": True,
                    "injection_pump_enabled": True,
                    "line_pressure_enabled": False,
                    "differential_pressure_enabled": True,
                    "valve_output_enabled": True,
                },
            },
            calibration_snapshot={},
        )
        stage = repository.add_stage(project.id, "Víz")
        dialog = ProjectSettingsDialog(
            repository,
            selected_project_id=project.id,
            selected_stage_id=stage.id,
            configuration={},
            calibration_snapshot={},
        )

        assert not dialog.device_checks["line_pressure_enabled"].isChecked()
        dialog.device_checks["injection_pump_enabled"].setChecked(False)
        dialog.device_checks["line_pressure_enabled"].setChecked(True)
        dialog._save_device_profile()

        saved = repository.get_project(project.id).configuration["devices"]
        assert isinstance(saved, dict)
        assert saved["injection_pump_enabled"] is False
        assert saved["line_pressure_enabled"] is True
        dialog.close()


def test_dashboard_requires_project_selector_without_last_project(tmp_path: Path) -> None:
    application()
    project_path = tmp_path / "startup-projects.sqlite3"
    with ProjectRepository(project_path) as repository:
        project = repository.create_project(
            name="Választható projekt",
            configuration={},
            calibration_snapshot={},
        )
        repository.add_stage(project.id, "Hidegvizes mérés")
    window = build_simulated_dashboard(
        tmp_path / "startup.csv",
        project_path,
        settings=QSettings(
            str(tmp_path / "startup.ini"), QSettings.Format.IniFormat
        ),
    )

    assert window._project_selector_required
    assert window._project.currentData() is None
    assert window._active_project_label.text() == "Nincs kiválasztva"
    assert window._stage.currentText() == "Nincs kiválasztott projekt"
    assert not window._stage.isEnabled()
    window.close()


def test_stage_selector_last_item_creates_stage_with_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application()
    project_path = tmp_path / "stage-projects.sqlite3"
    with ProjectRepository(project_path) as repository:
        project = repository.create_project(
            name="Szakasz projekt", configuration={}, calibration_snapshot={}
        )
        original = repository.add_stage(project.id, "Első szakasz")
    settings = QSettings(str(tmp_path / "stage.ini"), QSettings.Format.IniFormat)
    settings.setValue("project/last_project_id", project.id)
    settings.setValue("project/last_stage_id", original.id)
    window = build_simulated_dashboard(
        tmp_path / "raw.csv", project_path, settings=settings
    )
    selector = window._stage

    assert selector.itemData(selector.count() - 1) == ADD_STAGE_ACTION_DATA
    assert selector.itemText(selector.count() - 1) == "+ Új szakasz hozzáadása…"
    monkeypatch.setattr(
        StageSettingsDialog,
        "exec",
        lambda _dialog: QDialog.DialogCode.Accepted,
    )
    monkeypatch.setattr(
        StageSettingsDialog,
        "values",
        lambda _dialog: {
            "name": "Új tesztszakasz",
            "fluid": "tesztfolyadék",
            "target_pressure_bar": 75.0,
            "target_flow_ml_per_hour": 4.0,
            "notes": "Operátori megjegyzés",
        },
    )

    selector.setCurrentIndex(selector.count() - 1)

    created_id = selector.currentData()
    assert isinstance(created_id, int)
    created = window._projects.get_stage(created_id)
    assert created.name == "Új tesztszakasz"
    assert created.notes == "Operátori megjegyzés"
    assert selector.itemData(selector.count() - 1) == ADD_STAGE_ACTION_DATA

    monkeypatch.setattr(
        StageSettingsDialog,
        "exec",
        lambda _dialog: QDialog.DialogCode.Rejected,
    )
    selector.setCurrentIndex(selector.count() - 1)
    assert selector.currentData() == created_id
    window.close()


def test_pid_profile_can_be_saved_loaded_overwritten_and_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application()
    project_path = tmp_path / "pid-projects.sqlite3"
    window = build_simulated_dashboard(
        tmp_path / "raw.csv",
        project_path,
        settings=QSettings(str(tmp_path / "pid.ini"), QSettings.Format.IniFormat),
    )
    window._kp.setValue(2.5)
    window._ki.setValue(0.15)
    window._kd.setValue(0.01)
    window._output_min.setValue(5.0)
    window._output_max.setValue(75.0)
    window._direction.setCurrentIndex(
        window._direction.findData("reverse")
    )
    window._source.setCurrentIndex(window._source.findData("line_sensor"))
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        lambda *_args, **_kwargs: ("Olaj profil", True),
    )

    window._save_pid_profile()

    profile_id = window._pid_profile.currentData()
    assert isinstance(profile_id, int)
    saved = window._projects.get_pid_profile(profile_id)
    assert saved.name == "Olaj profil"
    assert saved.kp == 2.5
    assert saved.direction == "reverse"
    assert saved.pressure_source == "line_sensor"

    window._kp.setValue(9.0)
    assert window._pid_profile.currentData() is None
    window._pid_profile.setCurrentIndex(window._pid_profile.findData(profile_id))
    assert window._kp.value() == 2.5
    assert window._source.currentData() == "line_sensor"

    window._kp.setValue(3.5)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    window._save_pid_profile(profile_name="Olaj profil")
    assert window._projects.get_pid_profile(profile_id).kp == 3.5
    settings_path = Path(window._user_settings.fileName())
    window.close()

    restored = build_simulated_dashboard(
        tmp_path / "restored.csv",
        project_path,
        settings=QSettings(str(settings_path), QSettings.Format.IniFormat),
    )
    assert restored._pid_profile.currentData() == profile_id
    assert restored._kp.value() == 3.5
    assert restored._source.currentData() == "line_sensor"

    restored._delete_pid_profile()
    assert restored._projects.list_pid_profiles() == ()
    assert restored._pid_profile.currentData() is None
    restored.close()


def test_developer_can_switch_back_to_non_persistent_simulation(tmp_path: Path) -> None:
    application()
    settings = QSettings(str(tmp_path / "simulation.ini"), QSettings.Format.IniFormat)
    window = build_simulated_dashboard(
        tmp_path / "raw.csv", tmp_path / "projects.sqlite3", settings=settings
    )
    window._set_developer_mode(True)
    window._run_mode = RunMode.HARDWARE
    window._sync_simulation_mode_action()

    window._simulation_mode_action.setChecked(True)

    assert window._run_mode is RunMode.SIMULATION
    assert window._devices.status.mode is RunMode.SIMULATION
    assert not window._measurement_writer.persistence_enabled
    assert window._measurement_writer.current_path is None
    assert "nincs mérési adatmentés" in window._current_mode_message
    assert "szimuláció" in window.windowTitle().lower()
    assert not (tmp_path / "projects").exists()
    assert settings.value("application/last_run_mode") == RunMode.SIMULATION.value
    window.close()


def test_last_hardware_mode_opens_safe_activation_flow_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application()
    settings = QSettings(str(tmp_path / "hardware.ini"), QSettings.Format.IniFormat)
    settings.setValue("application/last_run_mode", RunMode.HARDWARE.value)
    window = build_simulated_dashboard(
        tmp_path / "raw.csv", tmp_path / "projects.sqlite3", settings=settings
    )
    window._project_selector_required = False
    opened: list[bool] = []
    monkeypatch.setattr(window, "_open_device_settings", lambda: opened.append(True))

    window._restore_startup_mode()
    window._restore_startup_mode()

    assert opened == [True]
    assert window._run_mode is RunMode.SIMULATION
    window.close()


def test_invalid_last_run_mode_falls_back_to_simulation(tmp_path: Path) -> None:
    application()
    settings = QSettings(str(tmp_path / "invalid.ini"), QSettings.Format.IniFormat)
    settings.setValue("application/last_run_mode", "unknown")

    window = build_simulated_dashboard(tmp_path / "raw.csv", settings=settings)

    assert window._preferred_run_mode is RunMode.SIMULATION
    window.close()


def test_hardware_disconnect_releases_and_reconnects_without_settings_dialog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application()
    window = build_simulated_dashboard(
        tmp_path / "raw.csv", tmp_path / "projects.sqlite3"
    )
    jacket = SimulatedPump()
    injection = SimulatedPump()
    daq = SimulatedDataAcquisition()
    devices = DeviceControlService(
        jacket_pump=jacket,
        injection_pump=injection,
        daq=daq,
        mode=RunMode.HARDWARE,
    )
    devices.authorize_hardware(DeviceControlService.HARDWARE_CONFIRMATION)
    devices.connect()
    window._devices = devices
    window._run_mode = RunMode.HARDWARE

    window._disconnect_devices()

    assert not jacket.connected
    assert not injection.connected
    assert not devices.status.hardware_authorized
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        lambda *_args, **_kwargs: (
            DeviceControlService.HARDWARE_CONFIRMATION,
            True,
        ),
    )

    window._connect_devices()

    assert jacket.connected
    assert injection.connected
    assert devices.status.hardware_authorized
    window._disconnect_devices()
    window.close()


def test_critical_hardware_fault_releases_ports_and_opens_device_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application()
    window = build_simulated_dashboard(
        tmp_path / "raw.csv", tmp_path / "projects.sqlite3"
    )
    jacket = SimulatedPump()
    injection = SimulatedPump()
    devices = DeviceControlService(
        jacket_pump=jacket,
        injection_pump=injection,
        daq=SimulatedDataAcquisition(),
        mode=RunMode.HARDWARE,
    )
    devices.authorize_hardware(DeviceControlService.HARDWARE_CONFIRMATION)
    devices.connect()

    class PumpControl:
        disconnected = False

        def observe_disconnected(self, *_roles: PumpRole) -> None:
            self.disconnected = True

    pump_control = PumpControl()
    window._devices = devices
    window._pump_control = pump_control  # type: ignore[assignment]
    window._run_mode = RunMode.HARDWARE
    window._preferred_run_mode = RunMode.HARDWARE
    simulation_calls: list[tuple[bool, bool]] = []
    errors: list[str] = []
    opened: list[bool] = []
    monkeypatch.setattr(
        window,
        "_activate_simulation",
        lambda *, preserve_preferred_mode, ignore_cleanup_errors: (
            simulation_calls.append(
                (preserve_preferred_mode, ignore_cleanup_errors)
            )
        ),
    )
    monkeypatch.setattr(window, "_show_error", errors.append)
    monkeypatch.setattr(
        window, "_open_device_settings", lambda: opened.append(True)
    )

    window._handle_critical_hardware_fault("megszakadt a pumpakapcsolat")
    application().processEvents()

    assert not jacket.connected
    assert not injection.connected
    assert devices.status.state is ApplicationState.IDLE
    assert not devices.status.hardware_authorized
    assert pump_control.disconnected
    assert simulation_calls == [(True, True)]
    assert errors and "elengedte a hardverkapcsolatokat" in errors[0]
    assert opened == [True]
    assert not window._alarm_label.isHidden()
    window._alarm_close_button.click()
    assert window._alarm_label.isHidden()
    window._pump_control = None
    window._run_mode = RunMode.SIMULATION
    window.close()


def test_alarm_close_restores_simulation_ready_state(tmp_path: Path) -> None:
    application()
    window = build_simulated_dashboard(
        tmp_path / "raw.csv", tmp_path / "projects.sqlite3"
    )

    assert all(
        button.text() != "Hiba nyugtázása"
        for button in window.findChildren(QPushButton)
    )
    window._emergency_stop()

    assert window._devices.status.state is ApplicationState.FAULT
    assert not window._alarm_close_button.isHidden()

    window._alarm_close_button.click()

    assert window._devices.status.state is ApplicationState.READY
    assert window._alarm_label.isHidden()
    assert window._active_alarm_text == "Nincs aktív riasztás"
    window.close()


def test_alarm_close_refuses_unsafe_fresh_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application()
    window = build_simulated_dashboard(
        tmp_path / "raw.csv", tmp_path / "projects.sqlite3"
    )
    injection = window._devices._injection_pump
    assert isinstance(injection, SimulatedPump)
    injection.pressure_bar = 500.0
    errors: list[str] = []
    monkeypatch.setattr(window, "_show_error", errors.append)
    window._emergency_stop()

    window._alarm_close_button.click()

    assert window._devices.status.state is ApplicationState.FAULT
    assert not window._alarm_label.isHidden()
    assert errors and "továbbra is hibát jelez" in errors[-1]

    application().processEvents()
    injection.pressure_bar = 100.0
    window._alarm_close_button.click()
    application().processEvents()

    assert window._devices.status.state is ApplicationState.READY
    assert window._alarm_label.isHidden()
    window.close()


def test_background_alert_flashes_taskbar_once_per_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application()
    window = build_simulated_dashboard(
        tmp_path / "raw.csv", tmp_path / "projects.sqlite3"
    )
    taskbar_alerts: list[bool] = []
    window._last_notification_key = None
    monkeypatch.setattr(window, "isMinimized", lambda: True)
    monkeypatch.setattr(
        window, "_request_taskbar_attention", lambda: taskbar_alerts.append(True)
    )

    window._notify_user(
        "Biztonsági riasztás",
        "Teszt hiba",
        critical=True,
        notification_key="alarm:test",
    )
    window._notify_user(
        "Biztonsági riasztás",
        "Teszt hiba",
        critical=True,
        notification_key="alarm:test",
    )

    assert taskbar_alerts == [True]
    window.close()


def test_system_tray_menu_offers_safe_application_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application()
    window = build_simulated_dashboard(
        tmp_path / "raw.csv", tmp_path / "projects.sqlite3"
    )

    assert window._tray_icon.contextMenu() is window._tray_menu
    assert [
        action.text() for action in window._tray_menu.actions() if not action.isSeparator()
    ] == ["Ablak megnyitása", "Program bezárása"]
    assert window._tray_quit_action.objectName() == "system_tray_quit_action"
    close_requests: list[bool] = []
    monkeypatch.setattr(window, "close", lambda: close_requests.append(True) or False)

    window._quit_from_tray()

    assert close_requests == [True]
    window._control_loop.close()
    window._projects.close()
    window._nas_sync.close()
    window._tray_icon.hide()


def test_window_shutdown_requests_safe_state_from_ready_devices(tmp_path: Path) -> None:
    application()
    window = build_simulated_dashboard(
        tmp_path / "raw.csv", tmp_path / "projects.sqlite3"
    )
    jacket = window._devices._jacket_pump
    injection = window._devices._injection_pump
    daq = window._devices._daq

    window.close()

    assert window._devices.status.state is ApplicationState.IDLE
    assert jacket.stop_requested
    assert injection.stop_requested
    assert daq.safe_state_requested
    assert window._valve.safe_state_requested


def test_measurement_start_preflight_rejects_invalid_sensor_voltage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application()
    project_path = tmp_path / "projects.sqlite3"
    with ProjectRepository(project_path) as repository:
        project = repository.create_project(
            name="Preflight project",
            configuration={},
            calibration_snapshot={},
        )
        stage = repository.add_stage(project.id, "Preflight stage")
    settings = QSettings(
        str(tmp_path / "preflight.ini"), QSettings.Format.IniFormat
    )
    settings.setValue("project/last_project_id", project.id)
    settings.setValue("project/last_stage_id", stage.id)
    window = build_simulated_dashboard(
        tmp_path / "raw.csv", project_path, settings=settings
    )
    window._devices._daq.inputs["line_pressure"] = -1.188189
    errors: list[str] = []
    monkeypatch.setattr(window, "_show_error", errors.append)

    window._start()

    for _ in range(100):
        application().processEvents()
        if errors:
            break
        sleep(0.01)

    assert window._devices.status.state is ApplicationState.READY
    assert not window._runtime.running
    assert window._devices._jacket_pump.stop_requested
    assert window._devices._injection_pump.stop_requested
    assert window._devices._daq.safe_state_requested
    assert errors and "line pressure input" in errors[0]
    assert "-1.18819 V" in errors[0]
    window.close()


def test_active_hardware_ready_state_refreshes_dashboard_without_measurement(
    tmp_path: Path,
) -> None:
    app = application()
    window = build_simulated_dashboard(
        tmp_path / "raw.csv",
        tmp_path / "projects.sqlite3",
    )
    window._hardware_status_timer.stop()
    window._run_mode = RunMode.HARDWARE
    window._hardware_status_generation += 1

    window._refresh_active_hardware_status()
    for _ in range(100):
        app.processEvents()
        if window._last_hardware_status_record is not None:
            break
        sleep(0.01)

    assert window._devices.status.state is ApplicationState.READY
    assert not window._runtime.running
    assert window._last_cycle_result is None
    assert window._jacket_label.text() == "120.0 bar"
    assert window._injection_label.text() == "100.0 bar"
    assert window._connection_labels["jacket"].text() == "KAPCSOLÓDVA"
    assert window._connection_labels["line_daq"].text() == "KAPCSOLÓDVA — ÉLŐ"
    assert window._valve_label.text() == "SAFE — mérés nem fut"
    assert len(window._times) == 0
    window._run_mode = RunMode.SIMULATION
    window.close()


def test_dashboard_loads_projects_and_stages_from_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    application()
    assert application_icon_path().name == "icon.png"
    assert not application_icon().isNull()
    project_path = tmp_path / "projects.sqlite3"
    with ProjectRepository(project_path) as repository:
        project = repository.create_project(
            name="UI project",
            configuration={},
            calibration_snapshot={},
        )
        stage = repository.add_stage(
            project.id,
            "Water stage",
            fluid="water",
            target_pressure_bar=88.0,
            target_flow_ml_per_hour=10.0,
        )
        alternate_stage = repository.add_stage(project.id, "Oil stage", fluid="oil")

    settings_path = tmp_path / "config" / "AFKI" / "EORControl.ini"
    user_settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
    user_settings.setValue("project/last_project_id", project.id)
    user_settings.setValue("project/last_stage_id", stage.id)
    window = build_simulated_dashboard(
        tmp_path / "raw.csv", project_path, settings=user_settings
    )
    project_selector = window.findChild(QComboBox, "project_selector")
    stage_selector = window.findChild(QComboBox, "stage_selector")

    assert project_selector is not None
    assert project_selector.currentText() == "UI project"
    assert stage_selector is not None
    assert stage_selector.currentText() == "Water stage"
    project_summary = window.findChild(QGroupBox, "active_project_summary")
    assert project_summary is not None
    assert project_summary.findChild(QComboBox, "stage_selector") is stage_selector
    assert stage_selector.isEnabled()
    stage_selector.setCurrentIndex(stage_selector.findData(alternate_stage.id))
    assert window._runtime_settings().active_stage == "Oil stage"
    assert window._active_stage_label.text() == "Oil stage"
    stage_selector.setCurrentIndex(stage_selector.findData(stage.id))
    assert window._active_project_label.text() == "UI project"
    assert window._active_stage_label.text() == "Water stage"
    pump_plan = window._default_measurement_pump_plan()
    assert pump_plan.jacket_target_pressure_bar == 108.0
    assert pump_plan.jacket_buildup_flow_ml_per_hour == 0.0
    assert pump_plan.injection_start_pressure_bar == 88.0
    assert pump_plan.injection_target_flow_ml_per_hour == 10.0
    remembered_plan = MeasurementPumpPlan(
        115.0,
        45.0,
        90.0,
        8.0,
        jacket_pressure_limit_bar=155.0,
        injection_pressure_limit_bar=135.0,
    )
    window._remember_measurement_pump_plan(remembered_plan)
    assert window._default_measurement_pump_plan() == remembered_plan
    assert not window._project_selector_required
    assert "SZIMULÁCIÓ" in window._current_mode_message
    assert "nincs mérési adatmentés" in window._current_mode_message
    mode_banner = window.findChild(QLabel, "dashboard_mode_label")
    alarm_banner = window.findChild(QLabel, "dashboard_alarm_label")
    assert mode_banner is None
    assert alarm_banner is not None
    assert alarm_banner.isHidden()
    assert alarm_banner.text() == ""
    assert window._alarm_close_button.isHidden()
    assert window._active_alarm_text == "Nincs aktív riasztás"
    window._set_active_alarm("teszt biztonsági ok")
    retained_alarm = alarm_banner.text()
    assert not alarm_banner.isHidden()
    assert not window._alarm_close_button.isHidden()
    application().processEvents()
    window._set_active_alarm("teszt biztonsági ok")
    assert alarm_banner.text() == retained_alarm
    assert "Automatikus művelet" in retained_alarm
    assert "Következő lépés" in retained_alarm
    window._alarm_close_button.click()
    assert alarm_banner.isHidden()
    assert alarm_banner.text() == ""
    assert not window._developer_mode
    assert window._kp.isHidden()
    window._set_developer_mode(True)
    assert not window._kp.isHidden()
    assert window._developer_view_action.isVisible()
    assert window._simulation_mode_action.isVisible()
    assert window._simulation_mode_action.isChecked()
    assert not any(
        button.text() == "Pumpavezérlés…"
        for button in window.findChildren(QPushButton)
    )
    settings_action = next(
        action for action in window.menuBar().actions() if action.text() == "Beállítások"
    )
    assert settings_action.menu() is not None
    assert "Felügyelt pumpavezérlés…" not in (
        action.text() for action in settings_action.menu().actions()
    )
    assert user_settings.value("developer/enabled") is True
    assert window._setpoint.value() == 88.0
    assert "water" in window._active_stage_label.toolTip()
    assert "Projekt" in [action.text() for action in window.menuBar().actions()]
    assert "Megjelenítés" in [action.text() for action in window.menuBar().actions()]
    splitter = window.findChild(QSplitter, "dashboard_splitter")
    assert splitter is not None
    assert splitter.count() == 3
    assert splitter.handleWidth() == 5
    assert splitter.opaqueResize()
    assert splitter.widget(0).objectName() == "status_scroll_area"
    status_scroll = splitter.widget(0)
    assert isinstance(status_scroll, QScrollArea)
    assert status_scroll.widgetResizable()
    assert status_scroll.sizeAdjustPolicy() == (
        QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored
    )
    assert status_scroll.minimumWidth() == 170
    assert status_scroll.verticalScrollBarPolicy() == (
        Qt.ScrollBarPolicy.ScrollBarAsNeeded
    )
    assert status_scroll.horizontalScrollBarPolicy() == (
        Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert status_scroll.widget() is not None
    assert status_scroll.widget().minimumWidth() == 0
    assert window._injection_flow_label.wordWrap()
    for label in (
        window._state_label,
        window._jacket_label,
        window._jacket_remaining_label,
        window._jacket_net_volume_label,
        window._injection_label,
        window._injection_remaining_label,
        window._injection_flow_label,
        window._injected_volume_label,
        window._line_label,
        window._delta_label,
        window._valve_label,
        *window._connection_labels.values(),
    ):
        assert "background:transparent" in label.styleSheet()
    banner_labels = {alarm_banner}
    for label in window.findChildren(QLabel):
        if label in banner_labels:
            continue
        assert "background:#" not in label.styleSheet().replace(" ", "").lower()
    assert splitter.widget(1).objectName() == "dashboard_measurement_tabs"
    assert splitter.widget(2).objectName() == "control_scroll_area"
    control_scroll = splitter.widget(2)
    assert isinstance(control_scroll, QScrollArea)
    assert control_scroll.widgetResizable()
    assert control_scroll.sizeAdjustPolicy() == (
        QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored
    )
    assert control_scroll.minimumWidth() == 260
    assert control_scroll.horizontalScrollBarPolicy() == (
        Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert control_scroll.verticalScrollBarPolicy() == (
        Qt.ScrollBarPolicy.ScrollBarAsNeeded
    )
    assert control_scroll.widget() is not None
    assert control_scroll.widget().minimumWidth() == 0
    valve_settings = window.findChild(QGroupBox, "valve_control_settings")
    assert valve_settings is not None
    control_form = valve_settings.layout()
    assert isinstance(control_form, QFormLayout)
    assert control_form.rowWrapPolicy() == QFormLayout.RowWrapPolicy.WrapAllRows
    window.resize(1200, 420)
    window.show()
    application().processEvents()
    assert_input_fields_are_labelled(window)
    control_fields = (
        window._mode,
        window._source,
        window._manual_output,
        window._setpoint,
        window._recording_interval,
        window._pid_profile,
        window._kp,
        window._ki,
        window._kd,
        window._direction,
        window._output_min,
        window._output_max,
    )
    assert len({field.width() for field in control_fields}) == 1
    assert len({button.width() for button in window._primary_control_buttons}) == 1
    assert all(
        button.width() >= control_scroll.viewport().width() - 40
        for button in window._primary_control_buttons
    )
    assert status_scroll.verticalScrollBar().isVisible()
    assert control_scroll.verticalScrollBar().isVisible()
    left_width = status_scroll.width()
    splitter.moveSplitter(splitter.handle(1).x() + 50, 1)
    application().processEvents()
    assert status_scroll.width() > left_width
    right_width = control_scroll.width()
    splitter.moveSplitter(splitter.handle(2).x() - 50, 2)
    application().processEvents()
    assert control_scroll.width() > right_width
    assert len({field.width() for field in control_fields}) == 1
    assert len({button.width() for button in window._primary_control_buttons}) == 1
    chart_splitter = window.findChild(QSplitter, "live_chart_splitter")
    assert chart_splitter is not None
    assert chart_splitter.count() == 2
    assert chart_splitter.widget(0).objectName() == "live_measurement_plot"
    assert chart_splitter.widget(1).objectName() == "live_injection_flow_plot"
    measurement_tabs = window.findChild(QTabWidget, "dashboard_measurement_tabs")
    assert measurement_tabs is not None
    assert measurement_tabs.count() == 2
    assert measurement_tabs.tabText(0) == "Élő mérés"
    assert measurement_tabs.tabText(1) == "Teljes mérés"
    assert isinstance(measurement_tabs.widget(1), MeasurementHistoryView)
    configuration = window._current_configuration()
    assert configuration["pid"]["direction"] == "direct"
    window._apply_pid()
    window._devices.start()
    window._runtime.start(window._runtime_settings())
    sleep(0.15)
    application().processEvents()
    assert window._valve.output_percent is not None
    assert window._connection_labels["jacket"].text() == "KAPCSOLÓDVA"
    assert window._connection_labels["line_daq"].text() == "KAPCSOLÓDVA"
    assert "ml" in window._jacket_remaining_label.text()
    assert "ml" in window._injection_remaining_label.text()
    assert "ml/h" in window._injection_flow_label.text()
    assert "Indítás óta nettó besajtolt:" in window._injected_volume_label.text()
    assert "Indítás óta nettó köpenytérfogat:" in (
        window._jacket_net_volume_label.text()
    )
    x_values, _ = window._jacket_curve.getData()
    assert x_values is not None
    assert all(value >= 0.0 for value in x_values)
    assert window._plot.viewRange()[0][0] >= 0.0
    flow_x_values, flow_values = window._flow_curve.getData()
    assert flow_x_values is not None
    assert flow_values is not None
    assert len(flow_values) == len(flow_x_values)
    assert all(value >= 0.0 for value in flow_x_values)
    assert window._connect_button.isHidden()
    assert window._disconnect_button.isHidden()
    window._refresh_state()
    window._pause_measurement()
    paused_points = len(window._times)
    sleep(0.12)
    application().processEvents()
    assert window._runtime.paused
    assert len(window._times) == paused_points
    assert window._pause_button.text() == "Mérés folytatása"
    window._pause_measurement()
    assert not window._runtime.paused
    window._stop()
    assert window._devices.status.state is ApplicationState.READY
    assert not window._runtime.running
    assert len(window._times) == 0
    assert window._jacket_label.text() == "— bar"
    assert window._history_view._table.rows == ()
    measurement_path = window._measurement_writer.current_path
    assert measurement_path is None
    assert not tuple((tmp_path / "projects").rglob("*_raw.csv"))
    window._open_measurement_history()
    assert window._measurement_tabs.currentWidget() is window._history_view
    history = window._history_view
    assert history._table.rows == ()
    assert "Nincs rögzített minta" in history._status.text()
    assert history._stage_plot.objectName() == "measurement_stage_timeline"
    assert history._checks["jacket_pressure_bar"].isChecked()
    assert not history._settings_panel.isHidden()
    history._settings_toggle.setChecked(False)
    assert not history._settings_panel.isVisible()
    assert history._settings_toggle.text() == "Beállítások megjelenítése ▼"
    history._settings_toggle.setChecked(True)
    assert not history._settings_panel.isHidden()
    assert history._settings_toggle.text() == "Beállítások elrejtése ▲"
    history._time_range.setCurrentIndex(4)
    assert history._custom_minutes.isEnabled()
    history._auto_y.setChecked(False)
    assert history._y_min.isEnabled()

    dialog = ProjectSettingsDialog(
        window._projects,
        selected_project_id=project.id,
        selected_stage_id=stage_selector.currentData(),
        configuration={},
        calibration_snapshot={},
        parent=window,
    )
    assert dialog.project_selector.currentText() == "UI project"
    assert dialog.stage_selector.currentText() == "Water stage"
    assert_input_fields_are_labelled(dialog)
    dialog.close()

    assert not window._measurement_settings_action.isCheckable()
    calibration_tabs = window._measurement_settings.findChild(
        QTabWidget, "calibration_settings_tabs"
    )
    assert calibration_tabs is not None
    assert calibration_tabs.count() == 2
    assert calibration_tabs.tabText(0) == "Érzékelők kalibrációja"
    assert calibration_tabs.tabText(1) == "Biztonsági határértékek"
    assert window._minimum_margin.minimum() == 0.1
    window._minimum_margin.setValue(10.0)
    assert window._minimum_margin.value() == 10.0
    window._open_measurement_overview()
    overview = window._overview_dialog
    assert overview is not None
    overview.refresh()
    assert overview.value_labels["project"].text() == "UI project"
    assert overview.value_labels["stage"].text() == "Water stage"
    assert overview.value_labels["line_calibration"].text() == (
        "1–5 V → 0–400 bar"
    )
    assert overview.value_labels["minimum_margin"].text() == "10 bar"
    overview.close()
    window._manual_output.setValue(33.0)
    window._recording_interval.setValue(7)
    window._set_theme("dark")
    assert "#11151a" in application().styleSheet()
    settings_path = Path(window._user_settings.fileName())
    assert settings_path.is_file()
    persisted_settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
    assert persisted_settings.value("theme") == "dark"
    window.close()
    restored = build_simulated_dashboard(
        tmp_path / "restored.csv",
        project_path,
        settings=QSettings(str(settings_path), QSettings.Format.IniFormat),
    )
    assert restored._active_project_label.text() == "UI project"
    assert restored._active_stage_label.text() == "Water stage"
    assert restored._manual_output.value() == 33.0
    assert restored._recording_interval.value() == 7
    assert restored._theme_actions["dark"].isChecked()
    restored._set_theme("light")
    assert "#f5f7fa" in application().styleSheet()
    restored._set_theme("system")
    assert application().styleSheet() == SYSTEM_STYLESHEET

    def dev7_discovery() -> HardwareDiscovery:
        return HardwareDiscovery(
            serial_ports=(
                SerialPortInfo("COM3", "Kommunikációs port"),
                SerialPortInfo("COM4", "PCI Serial Port"),
            ),
            ni_input_channels=(
                NiPhysicalChannelInfo("Dev7/ai3", "Dev7", "NI USB-6001"),
                NiPhysicalChannelInfo("Dev7/ai4", "Dev7", "NI USB-6001"),
            ),
            ni_output_channels=(
                NiPhysicalChannelInfo("Dev7/ao1", "Dev7", "NI USB-6001"),
            ),
        )
    device_dialog = DeviceSettingsDialog(
        UnusedTester(),  # type: ignore[arg-type]
        settings=restored._user_settings,
        current_mode=RunMode.SIMULATION,
        discoverer=dev7_discovery,
        parent=restored,
    )
    device_dialog.jacket_port.setCurrentIndex(
        device_dialog.jacket_port.findData("COM3")
    )
    device_dialog.injection_port.setCurrentIndex(
        device_dialog.injection_port.findData("COM4")
    )
    device_dialog.ni_device.setCurrentIndex(device_dialog.ni_device.findData("Dev7"))
    device_dialog.line_channel.setText("Dev7/ai3")
    device_dialog.delta_channel.setText("Dev7/ai4")
    device_dialog.valve_channel.setText("Dev7/ao1")
    device_dialog.terminal_configuration.setCurrentIndex(
        device_dialog.terminal_configuration.findData("DIFFERENTIAL")
    )
    device_dialog.pump_cabling_notes.setText("Gyári null-modem kábel")
    device_dialog.ni_wiring_notes.setText("Differenciális, közös föld nélkül")
    device_dialog._save_only()
    reopened_devices = DeviceSettingsDialog(
        UnusedTester(),  # type: ignore[arg-type]
        settings=restored._user_settings,
        current_mode=RunMode.SIMULATION,
        discoverer=dev7_discovery,
        parent=restored,
    )
    assert reopened_devices.line_channel.text() == "Dev7/ai3"
    assert reopened_devices.delta_channel.text() == "Dev7/ai4"
    assert reopened_devices.valve_channel.text() == "Dev7/ao1"
    assert reopened_devices.terminal_configuration.currentData() == "DIFFERENTIAL"
    assert reopened_devices.pump_cabling_notes.text() == "Gyári null-modem kábel"
    assert reopened_devices.ni_wiring_notes.text() == "Differenciális, közös föld nélkül"
    assert all(
        group.title() != "Helyszíni validáció felhasználói adatai"
        for group in reopened_devices.findChildren(QGroupBox)
    )
    assert reopened_devices._functional_test_button.isHidden()
    reopened_devices.close()
    device_dialog._configuration = device_dialog._read_configuration()
    device_dialog._test_passed(
        ConnectionTestResult(
            (
                DeviceConnectionResult(
                    HardwareTestDevice.JACKET_PUMP, True, "260D jacket"
                ),
                DeviceConnectionResult(
                    HardwareTestDevice.INJECTION_PUMP, True, "260D injection"
                ),
                DeviceConnectionResult(
                    HardwareTestDevice.LINE_PRESSURE, True, "Dev7/ai3: 2.0 V", 2.0
                ),
                DeviceConnectionResult(
                    HardwareTestDevice.DIFFERENTIAL_PRESSURE,
                    True,
                    "Dev7/ai4: 1.5 V",
                    1.5,
                ),
            )
        )
    )
    assert device_dialog._activate_button.isEnabled()
    device_dialog._activate()
    assert device_dialog.result() == device_dialog.DialogCode.Accepted

    logging_dialog = LoggingSettingsDialog(
        restored._diagnostics, restored._user_settings, parent=restored
    )
    assert_input_fields_are_labelled(logging_dialog)
    logging_dialog.enabled.setChecked(True)
    for category, checkbox in logging_dialog.category_checks.items():
        checkbox.setChecked(category is DiagnosticCategory.JACKET_PUMP)
    logging_dialog._save()
    restored._diagnostics.emit(DiagnosticCategory.NI_LINE, "RX", "hidden")
    restored._diagnostics.emit(DiagnosticCategory.JACKET_PUMP, "TX", "visible")
    restored._diagnostics.emit(DiagnosticCategory.JACKET_PUMP, "RX", "READY")
    developer = DeveloperViewDialog(restored._diagnostics, parent=restored)
    assert_input_fields_are_labelled(developer)
    developer.resize(700, 420)
    developer.show()
    application().processEvents()
    developer._refresh()
    assert developer.objectName() == "device_communication_dialog"
    assert developer._table.objectName() == "device_communication_table"
    assert developer._table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
    assert developer._table.selectionBehavior() == (
        QAbstractItemView.SelectionBehavior.SelectRows
    )
    assert developer._table.sizeAdjustPolicy() == (
        QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored
    )
    assert developer._table.minimumWidth() == 0
    assert developer._table.horizontalHeader().sectionResizeMode(3) == (
        developer._table.horizontalHeader().ResizeMode.Stretch
    )
    assert developer._buttons.isVisible()
    assert developer._buttons.geometry().bottom() <= developer.contentsRect().bottom()
    assert developer._table.rowCount() == 2
    assert developer._table.item(0, 3).text() == "EOR vezérlőalkalmazás"
    assert developer._table.item(0, 4).text() == "TX"
    assert developer._table.item(0, 5).text() == "Köpenypumpa"
    assert developer._table.item(0, 6).text() == "DASNET / RS-232"
    assert developer._table.item(0, 7).text() == "visible"
    assert developer._table.item(1, 3).text() == "Köpenypumpa"
    assert developer._table.item(1, 4).text() == "RX"
    assert developer._table.item(1, 5).text() == "EOR vezérlőalkalmazás"
    assert developer._table.item(1, 7).text() == "READY"
    assert "Köpenypumpa → EOR vezérlőalkalmazás" in (
        developer._table.item(1, 7).toolTip()
    )
    developer.close()
    restored.close()


def test_dashboard_marks_warning_and_critical_alarms_on_graph(
    tmp_path: Path,
) -> None:
    application()
    window = build_simulated_dashboard(
        tmp_path / "raw.csv", tmp_path / "projects.sqlite3"
    )
    snapshot = MeasurementSnapshot(
        recorded_at=datetime(2026, 7, 23, 10, 30, tzinfo=UTC),
        monotonic_seconds=15.0,
        jacket_pump=PumpStatus(120.0, 0.0, 250.0),
        injection_pump=PumpStatus(90.0, 8.0, 240.0),
        line_pressure_bar=91.0,
        differential_pressure_bar=2.0,
        valve_percent=20.0,
        quality=DataQuality.STALE,
    )
    record = MeasurementRecord(snapshot, 1.0, "Teszt szakasz")
    command = ValveCommand(
        True, 20.0, ControlMode.MANUAL, PressureSource.INJECTION_PUMP
    )

    window._handle_cycle(ControlCycleResult(record, command))

    assert len(window._alarm_points) == 1
    assert "FIGYELMEZTETÉS" in str(window._alarm_points[0]["data"])
    assert "Adatminőség: stale" in str(window._alarm_points[0]["data"])
    assert "Teszt szakasz" in str(window._alarm_points[0]["data"])

    critical_record = replace(record, safety_reasons=("nyomáshatár túllépve",))
    window._handle_cycle(ControlCycleResult(critical_record, command))

    assert len(window._alarm_points) == 2
    assert "KRITIKUS" in str(window._alarm_points[-1]["data"])
    assert "nyomáshatár túllépve" in str(window._alarm_points[-1]["data"])
    window._reset_measurement_dashboard()
    assert window._alarm_points == []
    window.close()
