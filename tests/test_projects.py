import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from eor_control.projects import ProjectRepository


def create_project(repository: ProjectRepository) -> int:
    project = repository.create_project(
        name=" EOR-001 ",
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
    assert project.created_at == datetime(2026, 7, 13, 10, 0, tzinfo=UTC)
    assert project.configuration == {"interval_seconds": 5, "mode": "simulation"}
    assert project.calibration_snapshot["line"] == {
        "voltage_min": 1.0,
        "voltage_max": 5.0,
    }


def test_project_configuration_can_update_modular_device_profile(tmp_path: Path) -> None:
    with ProjectRepository(tmp_path / "projects.sqlite3") as repository:
        project_id = create_project(repository)
        project = repository.get_project(project_id)
        configuration = dict(project.configuration)
        configuration["devices"] = {
            "jacket_pump_enabled": True,
            "injection_pump_enabled": True,
            "line_pressure_enabled": False,
            "differential_pressure_enabled": True,
            "valve_output_enabled": True,
        }

        updated = repository.update_project_configuration(
            project_id, configuration
        )

    assert updated.configuration["interval_seconds"] == 5
    assert updated.configuration["devices"] == configuration["devices"]


def test_database_can_be_reopened(tmp_path: Path) -> None:
    path = tmp_path / "projects.sqlite3"
    with ProjectRepository(path) as repository:
        project_id = create_project(repository)
    with ProjectRepository(path) as repository:
        assert repository.get_project(project_id).name == "EOR-001"


def test_pid_profiles_can_be_saved_updated_and_deleted(tmp_path: Path) -> None:
    path = tmp_path / "profiles.sqlite3"
    with ProjectRepository(path) as repository:
        profile = repository.save_pid_profile(
            name="Viszkózus olaj",
            kp=2.5,
            ki=0.15,
            kd=0.01,
            direction="reverse",
            output_min_percent=5.0,
            output_max_percent=70.0,
            pressure_source="line_sensor",
        )
        updated = repository.save_pid_profile(
            name="Viszkózus olaj",
            kp=3.0,
            ki=0.2,
            kd=0.02,
            direction="direct",
            output_min_percent=10.0,
            output_max_percent=80.0,
            pressure_source="injection_pump",
        )

        assert updated.id == profile.id
        assert repository.list_pid_profiles() == (updated,)
        assert repository.get_pid_profile_by_name("viszkózus OLAJ") == updated
        repository.delete_pid_profile(updated.id)
        assert repository.list_pid_profiles() == ()


def test_invalid_pid_profile_is_rejected(tmp_path: Path) -> None:
    with (
        ProjectRepository(tmp_path / "profiles.sqlite3") as repository,
        pytest.raises(ValueError, match="output limits"),
    ):
        repository.save_pid_profile(
            name="Hibás",
            kp=1.0,
            ki=0.0,
            kd=0.0,
            direction="direct",
            output_min_percent=70.0,
            output_max_percent=20.0,
            pressure_source="injection_pump",
        )


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
            fluid="water",
            target_pressure_bar=100.0,
            target_flow_ml_per_hour=10.0,
            notes="baseline",
        )
        chemical = repository.add_stage(project_id, "Chemical A", fluid="A")
        repository.move_stage(chemical.id, -1)
        repository.update_stage(
            chemical.id,
            name="Chemical A updated",
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


def test_project_can_be_deleted_with_all_stage_metadata(tmp_path: Path) -> None:
    with ProjectRepository(tmp_path / "projects.sqlite3") as repository:
        project_id = create_project(repository)
        stage = repository.add_stage(project_id, "Cold water")

        repository.delete_project(project_id)

        assert repository.list_projects() == ()
        with pytest.raises(KeyError, match=f"project {project_id}"):
            repository.get_project(project_id)
        with pytest.raises(KeyError, match=f"stage {stage.id}"):
            repository.get_stage(stage.id)


@pytest.mark.parametrize("name", ["", "   "])
def test_empty_project_and_stage_names_are_rejected(tmp_path: Path, name: str) -> None:
    with ProjectRepository(tmp_path / "projects.sqlite3") as repository:
        with pytest.raises(ValueError, match="project name"):
            repository.create_project(
                name=name,
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
                configuration={},
                calibration_snapshot={},
                created_at=datetime(2026, 7, 13),
            )
        with pytest.raises(ValueError, match="finite JSON-compatible"):
            repository.create_project(
                name="Project",
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
        stage = repository.add_stage(project_id, "Chemical", fluid="A")

    assert stage.name == "Chemical"
    assert stage.fluid == "A"
    check = sqlite3.connect(path)
    assert check.execute("PRAGMA user_version").fetchone()[0] == 4
    assert check.execute(
        "SELECT stage_type FROM measurement_stages WHERE id = ?", (stage.id,)
    ).fetchone()[0] == "Chemical"
    check.close()
