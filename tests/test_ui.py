import os
from pathlib import Path
from time import sleep

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox, QSplitter  # noqa: E402

from eor_control.application import RunMode  # noqa: E402
from eor_control.diagnostics import DiagnosticCategory  # noqa: E402
from eor_control.hardware import ConnectionTestResult, HardwareDiscovery  # noqa: E402
from eor_control.projects import ProjectRepository  # noqa: E402
from eor_control.ui import (  # noqa: E402
    DeveloperViewDialog,
    DeviceSettingsDialog,
    LoggingSettingsDialog,
    MeasurementHistoryDialog,
    ProjectSettingsDialog,
    application_icon,
    application_icon_path,
    build_simulated_dashboard,
)


def application() -> QApplication:
    instance = QApplication.instance()
    return instance if isinstance(instance, QApplication) else QApplication([])


class UnusedTester:
    def test(self, configuration: object) -> ConnectionTestResult:
        raise AssertionError("test should not be called directly")


def test_device_settings_discovers_dropdown_choices(tmp_path: Path) -> None:
    application()
    settings = QSettings(str(tmp_path / "hardware.ini"), QSettings.Format.IniFormat)
    dialog = DeviceSettingsDialog(
        UnusedTester(),  # type: ignore[arg-type]
        settings=settings,
        current_mode=RunMode.SIMULATION,
        discoverer=lambda: HardwareDiscovery(
            serial_ports=("COM8", "COM9"),
            ni_input_channels=("Dev2/ai0", "Dev2/ai1"),
            ni_output_channels=("Dev2/ao0",),
        ),
    )

    assert isinstance(dialog.jacket_port, QComboBox)
    assert dialog.jacket_port.isEditable()
    assert dialog.jacket_port.findText("COM8") >= 0
    assert dialog.injection_port.findText("COM9") >= 0
    assert dialog.line_channel.findText("Dev2/ai0") >= 0
    assert dialog.delta_channel.findText("Dev2/ai1") >= 0
    assert dialog.valve_channel.findText("Dev2/ao0") >= 0
    assert "2 COM-port" in dialog._discovery_status.text()
    dialog.close()


def test_dashboard_loads_projects_and_stages_from_sqlite(tmp_path: Path) -> None:
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
        repository.add_stage(
            project.id,
            "Water stage",
            fluid="water",
            target_pressure_bar=88.0,
            target_flow_ml_per_hour=10.0,
        )

    settings_path = tmp_path / "config" / "AFKI" / "EORControl.ini"
    user_settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
    window = build_simulated_dashboard(
        tmp_path / "raw.csv", project_path, settings=user_settings
    )
    project_selector = window.findChild(QComboBox, "project_selector")
    stage_selector = window.findChild(QComboBox, "stage_selector")

    assert project_selector is not None
    assert project_selector.currentText() == "UI project"
    assert stage_selector is not None
    assert stage_selector.currentText() == "Water stage"
    assert window._active_project_label.text() == "UI project"
    assert window._active_stage_label.text() == "Water stage"
    assert window._setpoint.value() == 88.0
    assert "water" in window._active_stage_label.toolTip()
    assert "Projekt" in [action.text() for action in window.menuBar().actions()]
    assert "Megjelenítés" in [action.text() for action in window.menuBar().actions()]
    splitter = window.findChild(QSplitter, "dashboard_splitter")
    assert splitter is not None
    assert splitter.count() == 3
    assert splitter.widget(0).objectName() == "status_scroll_area"
    assert splitter.widget(1).objectName() == "live_chart_splitter"
    assert splitter.widget(2).objectName() == "control_scroll_area"
    chart_splitter = window.findChild(QSplitter, "live_chart_splitter")
    assert chart_splitter is not None
    assert chart_splitter.count() == 2
    assert chart_splitter.widget(0).objectName() == "live_measurement_plot"
    assert chart_splitter.widget(1).objectName() == "live_injection_flow_plot"
    configuration = window._current_configuration()
    assert configuration["pid"]["direction"] == "direct"
    window._apply_pid()
    window._devices.connect()
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
    assert "Mérés óta besajtolt:" in window._injected_volume_label.text()
    x_values, _ = window._jacket_curve.getData()
    assert x_values is not None
    assert all(value >= 0.0 for value in x_values)
    assert window._plot.viewRange()[0][0] >= 0.0
    flow_x_values, flow_values = window._flow_curve.getData()
    assert flow_x_values is not None
    assert flow_values is not None
    assert len(flow_values) == len(flow_x_values)
    assert all(value >= 0.0 for value in flow_x_values)
    window._runtime.stop()
    window._devices.stop()
    measurement_path = window._measurement_writer.current_path
    assert measurement_path is not None
    assert "000001_UI_project" in str(measurement_path)
    history = MeasurementHistoryDialog(measurement_path, "UI project", parent=window)
    assert len(history._table.rows) >= 1
    assert history._checks["jacket_pressure_bar"].isChecked()
    history._time_range.setCurrentIndex(4)
    assert history._custom_minutes.isEnabled()
    history._auto_y.setChecked(False)
    assert history._y_min.isEnabled()
    history.close()

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
    dialog.close()

    window._measurement_settings_action.setChecked(True)
    assert not window._measurement_settings.isHidden()
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
    assert application().styleSheet() == ""

    device_dialog = DeviceSettingsDialog(
        UnusedTester(),  # type: ignore[arg-type]
        settings=restored._user_settings,
        current_mode=RunMode.SIMULATION,
        parent=restored,
    )
    device_dialog.line_channel.setText("Dev7/ai3")
    device_dialog.delta_channel.setText("Dev7/ai4")
    device_dialog.valve_channel.setText("Dev7/ao1")
    device_dialog.terminal_configuration.setCurrentIndex(
        device_dialog.terminal_configuration.findData("DIFFERENTIAL")
    )
    device_dialog.pump_cabling_notes.setText("Gyári null-modem kábel")
    device_dialog.ni_wiring_notes.setText("Differenciális, közös föld nélkül")
    device_dialog.supervised_test_minutes.setValue(90)
    device_dialog.cable_disconnect_test.setChecked(True)
    device_dialog.emergency_stop_test.setChecked(True)
    device_dialog.supervised_test.setChecked(True)
    device_dialog._save_only()
    reopened_devices = DeviceSettingsDialog(
        UnusedTester(),  # type: ignore[arg-type]
        settings=restored._user_settings,
        current_mode=RunMode.SIMULATION,
        parent=restored,
    )
    assert reopened_devices.line_channel.text() == "Dev7/ai3"
    assert reopened_devices.delta_channel.text() == "Dev7/ai4"
    assert reopened_devices.valve_channel.text() == "Dev7/ao1"
    assert reopened_devices.terminal_configuration.currentData() == "DIFFERENTIAL"
    assert reopened_devices.pump_cabling_notes.text() == "Gyári null-modem kábel"
    assert reopened_devices.ni_wiring_notes.text() == "Differenciális, közös föld nélkül"
    assert reopened_devices.supervised_test_minutes.value() == 90
    assert reopened_devices.cable_disconnect_test.isChecked()
    assert reopened_devices.emergency_stop_test.isChecked()
    assert reopened_devices.supervised_test.isChecked()
    reopened_devices.close()
    device_dialog._configuration = device_dialog._read_configuration()
    device_dialog._test_passed(
        ConnectionTestResult("260D jacket", "260D injection", 2.0, 1.5)
    )
    assert device_dialog._activate_button.isEnabled()
    device_dialog._activate()
    assert device_dialog.result() == device_dialog.DialogCode.Accepted

    logging_dialog = LoggingSettingsDialog(
        restored._diagnostics, restored._user_settings, parent=restored
    )
    logging_dialog.enabled.setChecked(True)
    for category, checkbox in logging_dialog.category_checks.items():
        checkbox.setChecked(category is DiagnosticCategory.JACKET_PUMP)
    logging_dialog._save()
    restored._diagnostics.emit(DiagnosticCategory.NI_LINE, "RX", "hidden")
    restored._diagnostics.emit(DiagnosticCategory.JACKET_PUMP, "TX", "visible")
    developer = DeveloperViewDialog(restored._diagnostics, parent=restored)
    developer._refresh()
    assert developer._table.rowCount() == 1
    assert developer._table.item(0, 3).text() == "jacket_pump"
    assert developer._table.item(0, 5).text() == "visible"
    developer.close()
    restored.close()
