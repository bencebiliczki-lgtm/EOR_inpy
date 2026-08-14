import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter

import pytest

from eor_control.data_management import (
    EXCEL_MEASUREMENT_HEADERS,
    ProjectMeasurementWriter,
    export_measurement_excel,
    migrate_legacy_measurement_csvs,
    project_excel_path,
)
from eor_control.domain import MeasurementRecord, MeasurementSnapshot, PumpStatus
from eor_control.project_database import (
    MeasurementQuery,
    PersistenceQueueFullError,
    ProjectSQLiteWriter,
    create_database_snapshot,
    database_query_plan,
    initialize_project_database,
    list_phases,
    query_measurements,
)
from eor_control.storage import CsvMeasurementWriter


def record(stage: str, second: int = 0) -> MeasurementRecord:
    return MeasurementRecord(
        MeasurementSnapshot(
            datetime(2026, 7, 13, 12, 30, tzinfo=UTC) + timedelta(seconds=second),
            float(second),
            PumpStatus(120.5, 60.0, 250.0),
            PumpStatus(100.25, 120.0, 240.0),
            101.75,
            2.5,
            45.0,
        ),
        10.0,
        stage,
        4.0,
    )


def test_project_writer_creates_complete_database_and_flushes_queue(tmp_path: Path) -> None:
    writer = ProjectMeasurementWriter(tmp_path)
    path = writer.select_project(1, "Próba", stage_name="víz")
    writer.write(record("víz"))
    writer.close()

    assert path.name == "project.sqlite"
    assert (path.parent / "exports").is_dir()
    assert (path.parent / "logs").is_dir()
    connection = sqlite3.connect(path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()
    assert {
        "schema_info",
        "project",
        "phases",
        "measurements",
        "events",
        "export_history",
    } <= tables
    assert {
        "idx_measurements_phase_time",
        "idx_measurements_recorded_at",
        "idx_events_phase_time",
        "idx_events_type_time",
    } <= indexes
    assert len(query_measurements(path, MeasurementQuery()).rows) == 1


def test_repeated_phase_names_are_separate_instances(tmp_path: Path) -> None:
    writer = ProjectMeasurementWriter(tmp_path)
    path = writer.select_project(1, "Próba", stage_name="víz")
    writer.write(record("víz", 0))
    assert writer.complete_current_phase() == path
    writer.write(record("víz", 1))
    writer.close()

    phases = list_phases(path)
    assert [(phase.sequence, phase.name) for phase in phases] == [(1, "víz"), (2, "víz")]
    rows = query_measurements(
        path, MeasurementQuery(columns=("id", "phase_id"))
    ).rows
    assert [row[1] for row in rows] == [1, 2]


def test_writer_converts_hour_flow_to_minute_and_enqueue_is_fast(tmp_path: Path) -> None:
    path = tmp_path / "project.sqlite"
    initialize_project_database(
        path, project_id=1, project_name="P", created_at=datetime.now(UTC)
    )
    writer = ProjectSQLiteWriter(path)
    started = perf_counter()
    writer.write(record("A"))
    enqueue_seconds = perf_counter() - started
    writer.close()

    row = query_measurements(
        path,
        MeasurementQuery(columns=("jacket_flow_ml_min", "injection_flow_ml_min")),
    ).rows[0]
    assert row == (1.0, 2.0)
    assert enqueue_seconds < 0.05
    assert writer.metrics.committed_count == 1


def test_wal_reader_works_while_writer_is_active(tmp_path: Path) -> None:
    path = tmp_path / "project.sqlite"
    initialize_project_database(
        path, project_id=1, project_name="P", created_at=datetime.now(UTC)
    )
    writer = ProjectSQLiteWriter(path, batch_interval_seconds=0.05)
    writer.write(record("A"))
    writer.flush()

    assert len(query_measurements(path, MeasurementQuery()).rows) == 1
    writer.close()


def test_schema_v1_is_migrated_without_losing_measurements(tmp_path: Path) -> None:
    path = tmp_path / "project.sqlite"
    initialize_project_database(
        path, project_id=1, project_name="P", created_at=datetime.now(UTC)
    )
    writer = ProjectSQLiteWriter(path)
    writer.write(record("A"))
    writer.close()
    connection = sqlite3.connect(path)
    try:
        for column in (
            "median_line_voltage",
            "filtered_line_voltage",
            "line_data_quality",
            "line_quality_reason",
            "line_sample_age_s",
            "median_differential_voltage",
            "filtered_differential_voltage",
            "differential_data_quality",
            "differential_quality_reason",
            "differential_sample_age_s",
        ):
            connection.execute(f"ALTER TABLE measurements DROP COLUMN {column}")
        connection.execute(
            "UPDATE schema_info SET schema_version=1, last_migration_version=1"
        )
        connection.commit()
    finally:
        connection.close()

    initialize_project_database(
        path, project_id=1, project_name="P", created_at=datetime.now(UTC)
    )
    migrated = query_measurements(
        path,
        MeasurementQuery(
            columns=("line_pressure_bar", "median_line_voltage", "line_data_quality")
        ),
    ).rows

    assert migrated == ((101.75, None, "good"),)


def test_schema_v2_adds_differential_traceability_without_data_loss(
    tmp_path: Path,
) -> None:
    path = tmp_path / "project.sqlite"
    initialize_project_database(
        path, project_id=1, project_name="P", created_at=datetime.now(UTC)
    )
    writer = ProjectSQLiteWriter(path)
    writer.write(record("A"))
    writer.close()
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "UPDATE measurements SET raw_differential_voltage=1.5"
        )
        for column in (
            "median_differential_voltage",
            "filtered_differential_voltage",
            "differential_data_quality",
            "differential_quality_reason",
            "differential_sample_age_s",
        ):
            connection.execute(f"ALTER TABLE measurements DROP COLUMN {column}")
        connection.execute(
            "UPDATE schema_info SET schema_version=2, last_migration_version=2"
        )
        connection.commit()
    finally:
        connection.close()

    initialize_project_database(
        path, project_id=1, project_name="P", created_at=datetime.now(UTC)
    )
    migrated = query_measurements(
        path,
        MeasurementQuery(
            columns=(
                "differential_pressure_bar",
                "median_differential_voltage",
                "differential_data_quality",
            )
        ),
    ).rows

    assert migrated == ((2.5, 1.5, "good"),)


def test_phase_time_query_uses_declared_index(tmp_path: Path) -> None:
    writer = ProjectMeasurementWriter(tmp_path)
    path = writer.select_project(1, "P")
    writer.write(record("A"))
    writer.close()
    plan = database_query_plan(path, phase_id=1, start_at="2026-01-01")
    assert any("idx_measurements_phase_time" in item for item in plan)


def test_query_filters_time_columns_and_downsamples(tmp_path: Path) -> None:
    writer = ProjectMeasurementWriter(tmp_path)
    path = writer.select_project(1, "P")
    for second in range(20):
        writer.write(record("A", second))
    writer.close()

    rows = query_measurements(
        path,
        MeasurementQuery(
            start_at="2026-07-13T12:30:05+00:00",
            end_at="2026-07-13T12:30:15+00:00",
            columns=("recorded_at", "line_pressure_bar"),
            max_points=4,
        ),
    )
    assert rows.columns == ("recorded_at", "line_pressure_bar")
    assert len(rows.rows) <= 4
    assert all("12:30:05" <= str(row[0])[11:19] <= "12:30:15" for row in rows.rows)


def test_queue_critical_limit_is_explicit(tmp_path: Path) -> None:
    path = tmp_path / "project.sqlite"
    initialize_project_database(
        path, project_id=1, project_name="P", created_at=datetime.now(UTC)
    )
    writer = ProjectSQLiteWriter(path, queue_capacity=4, batch_interval_seconds=1.0)
    try:
        raised = False
        for second in range(100):
            try:
                writer.write(record("A", second))
            except PersistenceQueueFullError:
                raised = True
                break
        assert raised
    finally:
        writer.close()


def test_excel_is_rebuilt_with_exact_columns_and_missing_values(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    writer = ProjectMeasurementWriter(tmp_path)
    path = writer.select_project(1, "Excel")
    writer.write(record("azonos", 0))
    writer.complete_current_phase()
    missing = record("azonos", 90_001)
    missing = MeasurementRecord(
        MeasurementSnapshot(
            missing.snapshot.recorded_at,
            missing.snapshot.monotonic_seconds,
            missing.snapshot.jacket_pump,
            missing.snapshot.injection_pump,
            None,
            None,
            missing.snapshot.valve_percent,
        ),
        missing.injected_volume_ml,
        missing.active_stage,
    )
    writer.write(missing)
    writer.close()
    destination = project_excel_path(path, "")

    export_measurement_excel(path, destination)

    workbook = openpyxl.load_workbook(destination, read_only=False)
    assert workbook.sheetnames == ["01 azonos", "02 azonos"]
    assert tuple(cell.value for cell in workbook["01 azonos"][1]) == EXCEL_MEASUREMENT_HEADERS
    assert workbook["02 azonos"]["K2"].value is None
    assert workbook["02 azonos"]["B2"].number_format == "[h]:mm:ss"
    assert workbook["01 azonos"].max_column == 13


def test_failed_excel_rebuild_keeps_previous_file(tmp_path: Path) -> None:
    destination = tmp_path / "existing.xlsx"
    destination.write_bytes(b"previous")
    with pytest.raises(sqlite3.OperationalError):
        export_measurement_excel(tmp_path / "missing" / "project.sqlite", destination)
    assert destination.read_bytes() == b"previous"


def test_consistent_sqlite_backup_can_be_opened(tmp_path: Path) -> None:
    writer = ProjectMeasurementWriter(tmp_path)
    path = writer.select_project(1, "P")
    writer.write(record("A"))
    writer.complete_current_phase()
    snapshot = create_database_snapshot(path, tmp_path / "snapshot.sqlite")
    writer.close()

    connection = sqlite3.connect(snapshot)
    try:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT COUNT(*) FROM measurements").fetchone()[0] == 1
    finally:
        connection.close()


def test_csv_migration_is_idempotent(tmp_path: Path) -> None:
    csv_path = tmp_path / "legacy.csv"
    csv_writer = CsvMeasurementWriter(csv_path)
    csv_writer.write(record("régi"))
    csv_writer.close()
    database = tmp_path / "project.sqlite"
    initialize_project_database(
        database,
        project_id=1,
        project_name="P",
        created_at=datetime(2026, 7, 1, tzinfo=UTC),
    )

    first = migrate_legacy_measurement_csvs(
        database, project_id=1, sources=(csv_path,), phase_types={"régi": "water"}
    )
    second = migrate_legacy_measurement_csvs(
        database, project_id=1, sources=(csv_path,), phase_types={"régi": "water"}
    )

    assert first.inserted_rows == 1
    assert second.inserted_rows == 0
    assert second.duplicate_rows == 1
    assert len(query_measurements(database, MeasurementQuery()).rows) == 1
