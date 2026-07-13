import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from eor_control.projects import ProjectRepository


def create_project(repository: ProjectRepository) -> int:
    project = repository.create_project(
        name=" EOR-001 ",
        operator=" Operator ",
        notes="Initial run",
        configuration={"interval_seconds": 5, "mode": "simulation"},
        calibration_snapshot={"line": {"voltage_min": 1.0, "voltage_max": 5.0}},
        created_at=datetime(2026, 7, 13, 12, 0, tzinfo=timezone(timedelta(hours=2))),
    )
    return project.id


def test_project_round_trip_preserves_snapshots_and_utc_time(tmp_path: Path) -> None:
    path = tmp_path / "projects.sqlite3"
    with ProjectRepository(path) as repository:
        project_id = create_project(repository)
        project = repository.get_project(project_id)

    assert project.name == "EOR-001"
    assert project.operator == "Operator"
    assert project.created_at == datetime(2026, 7, 13, 10, 0, tzinfo=UTC)
    assert project.configuration == {"interval_seconds": 5, "mode": "simulation"}
    assert project.calibration_snapshot["line"] == {
        "voltage_min": 1.0,
        "voltage_max": 5.0,
    }


def test_database_can_be_reopened(tmp_path: Path) -> None:
    path = tmp_path / "projects.sqlite3"
    with ProjectRepository(path) as repository:
        project_id = create_project(repository)
    with ProjectRepository(path) as repository:
        assert repository.get_project(project_id).name == "EOR-001"


def test_stages_are_added_in_order_and_can_be_renamed(tmp_path: Path) -> None:
    with ProjectRepository(tmp_path / "projects.sqlite3") as repository:
        project_id = create_project(repository)
        first = repository.add_stage(project_id, "Cold water")
        second = repository.add_stage(project_id, "Oil")
        renamed = repository.rename_stage(second.id, "Hot water")
        project = repository.get_project(project_id)

    assert first.position == 0
    assert renamed.position == 1
    assert [stage.name for stage in project.stages] == ["Cold water", "Hot water"]


def test_stage_metadata_can_be_edited_reordered_and_deleted(tmp_path: Path) -> None:
    with ProjectRepository(tmp_path / "projects.sqlite3") as repository:
        project_id = create_project(repository)
        water = repository.add_stage(
            project_id,
            "Cold water",
            stage_type="cold_water",
            fluid="water",
            target_pressure_bar=100.0,
            target_flow_ml_per_hour=10.0,
            notes="baseline",
        )
        chemical = repository.add_stage(
            project_id, "Chemical A", stage_type="chemical", fluid="A"
        )
        repository.move_stage(chemical.id, -1)
        repository.update_stage(
            chemical.id,
            name="Chemical A updated",
            stage_type="chemical",
            fluid="Chemical A",
            target_pressure_bar=120.0,
            target_flow_ml_per_hour=8.0,
            notes="second phase",
        )
        repository.delete_stage(water.id)
        stages = repository.list_stages(project_id)

    assert len(stages) == 1
    assert stages[0].position == 0
    assert stages[0].name == "Chemical A updated"
    assert stages[0].fluid == "Chemical A"
    assert stages[0].target_pressure_bar == 120.0
    assert stages[0].target_flow_ml_per_hour == 8.0
    assert stages[0].notes == "second phase"


@pytest.mark.parametrize("name", ["", "   "])
def test_empty_project_and_stage_names_are_rejected(tmp_path: Path, name: str) -> None:
    with ProjectRepository(tmp_path / "projects.sqlite3") as repository:
        with pytest.raises(ValueError, match="project name"):
            repository.create_project(
                name=name,
                operator="Operator",
                configuration={},
                calibration_snapshot={},
            )
        project_id = create_project(repository)
        with pytest.raises(ValueError, match="stage name"):
            repository.add_stage(project_id, name)


def test_naive_timestamp_and_non_finite_snapshot_are_rejected(tmp_path: Path) -> None:
    with ProjectRepository(tmp_path / "projects.sqlite3") as repository:
        with pytest.raises(ValueError, match="timezone"):
            repository.create_project(
                name="Project",
                operator="Operator",
                configuration={},
                calibration_snapshot={},
                created_at=datetime(2026, 7, 13),
            )
        with pytest.raises(ValueError, match="finite JSON-compatible"):
            repository.create_project(
                name="Project",
                operator="Operator",
                configuration={"invalid": float("nan")},
                calibration_snapshot={},
            )


def test_newer_database_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "projects.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version = 99")
    connection.close()

    with pytest.raises(RuntimeError, match="not supported"):
        ProjectRepository(path)


def test_version_one_database_is_migrated_with_stage_metadata(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, created_at_utc TEXT NOT NULL,
            operator TEXT NOT NULL, notes TEXT NOT NULL, configuration_json TEXT NOT NULL,
            calibration_snapshot_json TEXT NOT NULL
        );
        CREATE TABLE measurement_stages (
            id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
            name TEXT NOT NULL, position INTEGER NOT NULL CHECK(position >= 0),
            created_at_utc TEXT NOT NULL, UNIQUE(project_id, position)
        );
        PRAGMA user_version = 1;
        """
    )
    connection.close()

    with ProjectRepository(path) as repository:
        project_id = create_project(repository)
        stage = repository.add_stage(
            project_id, "Chemical", stage_type="chemical", fluid="A"
        )

    assert stage.stage_type == "chemical"
    assert stage.fluid == "A"
    check = sqlite3.connect(path)
    assert check.execute("PRAGMA user_version").fetchone()[0] == 2
    check.close()
