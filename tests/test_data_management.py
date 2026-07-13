import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from eor_control.data_management import (
    BackgroundNasSynchronizer,
    NasSyncQueue,
    ProjectMeasurementWriter,
    export_measurement_csv,
    export_measurement_excel,
    read_measurement_table,
    safe_filename,
)
from eor_control.domain import MeasurementRecord, MeasurementSnapshot, PumpStatus


def record() -> MeasurementRecord:
    return MeasurementRecord(
        snapshot=MeasurementSnapshot(
            recorded_at=datetime(2026, 7, 13, 12, 30, tzinfo=UTC),
            monotonic_seconds=10.5,
            jacket_pump=PumpStatus(120.5, 1.0, 250.0),
            injection_pump=PumpStatus(100.25, 12.0, 240.0),
            line_pressure_bar=101.75,
            differential_pressure_bar=2.5,
            valve_percent=45.0,
        ),
        injected_volume_ml=10.0,
        active_stage="víz",
    )


def test_project_writer_uses_separate_safe_project_paths(tmp_path: Path) -> None:
    writer = ProjectMeasurementWriter(tmp_path)
    first = writer.select_project(1, 'Első: projekt?')
    writer.write(record())
    second = writer.select_project(2, "Második projekt")
    writer.write(record())
    writer.close()

    assert first != second
    assert first.name == "Első_projekt_raw.csv"
    assert first.parent.parent.name.isdigit()
    assert len(read_measurement_table(first).rows) == 1
    assert len(read_measurement_table(second).rows) == 1
    assert safe_filename('  hibás<>:"/\\|?* név  ') == "hibás_név"


def test_project_writer_creates_portable_json_snapshots(tmp_path: Path) -> None:
    writer = ProjectMeasurementWriter(tmp_path)
    source = writer.select_project_with_metadata(
        7,
        "Kőzet A",
        created_at=datetime(2025, 3, 4, tzinfo=UTC),
        operator="Kezelő",
        notes="Megjegyzés",
        configuration={"interval": 5},
        calibration_snapshot={"inlet": [1.0, 5.0, 0.0, 400.0]},
        stages=[{"name": "Hidegvizes", "type": "cold_water"}],
    )
    writer.close()

    assert source.parent.parent.name == "2025"
    project = json.loads((source.parent / "project.json").read_text(encoding="utf-8"))
    assert project["name"] == "Kőzet A"
    assert project["stages"] == [{"name": "Hidegvizes", "type": "cold_water"}]
    assert json.loads(
        (source.parent / "config_snapshot.json").read_text(encoding="utf-8")
    ) == {"interval": 5}


def test_user_csv_export_uses_semicolon_and_decimal_comma(tmp_path: Path) -> None:
    writer = ProjectMeasurementWriter(tmp_path)
    source = writer.select_project(1, "Export")
    writer.write(record())
    writer.close()
    destination = tmp_path / "export.csv"

    export_measurement_csv(source, destination, decimal_comma=True, delimiter=";")

    with destination.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.reader(file, delimiter=";"))
    assert rows[1][2] == "120,5"
    assert rows[1][9] == "101,75"


def test_reader_keeps_legacy_comma_delimited_raw_files_compatible(tmp_path: Path) -> None:
    source = tmp_path / "legacy.csv"
    with source.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(
            (
                "recorded_at_utc",
                "monotonic_seconds",
                "jacket_pressure_bar",
                "jacket_flow_ml_per_hour",
                "jacket_remaining_volume_ml",
                "injection_pressure_bar",
                "injection_flow_ml_per_hour",
                "injection_remaining_volume_ml",
                "injected_volume_ml",
                "line_pressure_bar",
                "differential_pressure_bar",
                "inlet_pressure_bar",
                "valve_percent",
                "active_stage",
                "quality",
                "safety_reasons",
            )
        )
        writer.writerow(["2026-01-01T00:00:00+00:00", *(["1.5"] * 12), "stage", "good", ""])

    table = read_measurement_table(source)

    assert table.rows[0][2] == "1.5"


def test_excel_export_contains_data_and_chart_sheet(tmp_path: Path) -> None:
    from openpyxl import load_workbook

    writer = ProjectMeasurementWriter(tmp_path)
    source = writer.select_project(1, "Excel")
    writer.write(record())
    writer.close()
    destination = tmp_path / "export.xlsx"

    export_measurement_excel(source, destination)

    workbook = load_workbook(destination, read_only=False)
    assert workbook.sheetnames == ["Mérési adatok", "Diagram"]
    assert workbook["Mérési adatok"]["C2"].value == 120.5
    assert len(workbook["Diagram"]._charts) == 1


def test_nas_queue_survives_failure_and_resynchronizes(tmp_path: Path) -> None:
    source = tmp_path / "local" / "measurement.csv"
    source.parent.mkdir()
    source.write_text("first", encoding="utf-8")
    queue = NasSyncQueue(tmp_path / "queue.sqlite3")
    synchronizer = BackgroundNasSynchronizer(queue, retry_interval_seconds=60.0)
    blocked_target = tmp_path / "blocked"
    blocked_target.write_text("not a directory", encoding="utf-8")
    synchronizer.configure(enabled=True, target_root=blocked_target)
    synchronizer.enqueue(source, Path("projects") / "measurement.csv")

    assert synchronizer.sync_pending_once() == 0
    assert synchronizer.pending_count == 1
    assert queue.pending()[0].attempts >= 1
    synchronizer.configure(enabled=False, target_root=None)
    synchronizer.close()

    nas_target = tmp_path / "nas"
    reopened_queue = NasSyncQueue(tmp_path / "queue.sqlite3")
    synchronizer = BackgroundNasSynchronizer(reopened_queue, retry_interval_seconds=60.0)
    synchronizer.configure(enabled=True, target_root=nas_target)
    synchronizer.sync_pending_once()
    assert synchronizer.pending_count == 0
    assert (nas_target / "projects" / "measurement.csv").read_text(encoding="utf-8") == "first"
    synchronizer.close()
