import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from typing import cast


@dataclass(frozen=True, slots=True)
class MeasurementStage:
    id: int
    project_id: int
    name: str
    position: int
    created_at: datetime
    stage_type: str = "custom"
    fluid: str = ""
    target_pressure_bar: float | None = None
    target_flow_ml_per_hour: float | None = None
    notes: str = ""


@dataclass(frozen=True, slots=True)
class MeasurementProject:
    id: int
    name: str
    created_at: datetime
    operator: str
    notes: str
    configuration: dict[str, object]
    calibration_snapshot: dict[str, object]
    stages: tuple[MeasurementStage, ...] = ()


class ProjectRepository:
    SCHEMA_VERSION = 2

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def _migrate(self) -> None:
        version = cast(int, self._connection.execute("PRAGMA user_version").fetchone()[0])
        if version > self.SCHEMA_VERSION:
            self._connection.close()
            raise RuntimeError(f"database schema version {version} is not supported")
        if version == 0:
            with self._connection:
                self._connection.executescript(
                    """
                    CREATE TABLE projects (
                        id INTEGER PRIMARY KEY,
                        name TEXT NOT NULL CHECK(length(trim(name)) > 0),
                        created_at_utc TEXT NOT NULL,
                        operator TEXT NOT NULL,
                        notes TEXT NOT NULL,
                        configuration_json TEXT NOT NULL,
                        calibration_snapshot_json TEXT NOT NULL
                    );
                    CREATE TABLE measurement_stages (
                        id INTEGER PRIMARY KEY,
                        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                        name TEXT NOT NULL CHECK(length(trim(name)) > 0),
                        position INTEGER NOT NULL CHECK(position >= 0),
                        created_at_utc TEXT NOT NULL,
                        stage_type TEXT NOT NULL DEFAULT 'custom',
                        fluid TEXT NOT NULL DEFAULT '',
                        target_pressure_bar REAL,
                        target_flow_ml_per_hour REAL,
                        notes TEXT NOT NULL DEFAULT '',
                        UNIQUE(project_id, position)
                    );
                    CREATE INDEX measurement_stages_project
                    ON measurement_stages(project_id, position);
                    PRAGMA user_version = 2;
                    """
                )
            version = 2
        if version == 1:
            with self._connection:
                self._connection.executescript(
                    """
                    ALTER TABLE measurement_stages
                    ADD COLUMN stage_type TEXT NOT NULL DEFAULT 'custom';
                    ALTER TABLE measurement_stages
                    ADD COLUMN fluid TEXT NOT NULL DEFAULT '';
                    ALTER TABLE measurement_stages
                    ADD COLUMN target_pressure_bar REAL;
                    ALTER TABLE measurement_stages
                    ADD COLUMN target_flow_ml_per_hour REAL;
                    ALTER TABLE measurement_stages
                    ADD COLUMN notes TEXT NOT NULL DEFAULT '';
                    PRAGMA user_version = 2;
                    """
                )

    def create_project(
        self,
        *,
        name: str,
        operator: str,
        notes: str = "",
        configuration: Mapping[str, object],
        calibration_snapshot: Mapping[str, object],
        created_at: datetime | None = None,
    ) -> MeasurementProject:
        cleaned_name = self._validate_name(name, "project name")
        timestamp = self._as_utc(created_at or datetime.now(UTC))
        configuration_json = self._serialize_snapshot(configuration)
        calibration_json = self._serialize_snapshot(calibration_snapshot)
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO projects (
                    name, created_at_utc, operator, notes,
                    configuration_json, calibration_snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    cleaned_name,
                    timestamp.isoformat(),
                    operator.strip(),
                    notes,
                    configuration_json,
                    calibration_json,
                ),
            )
        project_id = cast(int, cursor.lastrowid)
        return self.get_project(project_id)

    def get_project(self, project_id: int) -> MeasurementProject:
        row = self._connection.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"project {project_id} does not exist")
        return self._project_from_row(row, self.list_stages(project_id))

    def list_projects(self) -> tuple[MeasurementProject, ...]:
        rows = self._connection.execute(
            "SELECT * FROM projects ORDER BY created_at_utc, id"
        ).fetchall()
        return tuple(self._project_from_row(row, ()) for row in rows)

    def add_stage(
        self,
        project_id: int,
        name: str,
        *,
        stage_type: str = "custom",
        fluid: str = "",
        target_pressure_bar: float | None = None,
        target_flow_ml_per_hour: float | None = None,
        notes: str = "",
        created_at: datetime | None = None,
    ) -> MeasurementStage:
        self._require_project(project_id)
        cleaned_name = self._validate_name(name, "stage name")
        timestamp = self._as_utc(created_at or datetime.now(UTC))
        cleaned_type = self._validate_name(stage_type, "stage type")
        self._validate_optional_target(target_pressure_bar, "target pressure")
        self._validate_optional_target(target_flow_ml_per_hour, "target flow")
        row = self._connection.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM measurement_stages WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        position = cast(int, row[0])
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO measurement_stages (
                    project_id, name, position, created_at_utc, stage_type, fluid,
                    target_pressure_bar, target_flow_ml_per_hour, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    cleaned_name,
                    position,
                    timestamp.isoformat(),
                    cleaned_type,
                    fluid.strip(),
                    target_pressure_bar,
                    target_flow_ml_per_hour,
                    notes,
                ),
            )
        return MeasurementStage(
            id=cast(int, cursor.lastrowid),
            project_id=project_id,
            name=cleaned_name,
            position=position,
            created_at=timestamp,
            stage_type=cleaned_type,
            fluid=fluid.strip(),
            target_pressure_bar=target_pressure_bar,
            target_flow_ml_per_hour=target_flow_ml_per_hour,
            notes=notes,
        )

    def rename_stage(self, stage_id: int, name: str) -> MeasurementStage:
        stage = self._require_stage(stage_id)
        return self.update_stage(
            stage_id,
            name=name,
            stage_type=stage.stage_type,
            fluid=stage.fluid,
            target_pressure_bar=stage.target_pressure_bar,
            target_flow_ml_per_hour=stage.target_flow_ml_per_hour,
            notes=stage.notes,
        )

    def update_stage(
        self,
        stage_id: int,
        *,
        name: str,
        stage_type: str,
        fluid: str,
        target_pressure_bar: float | None,
        target_flow_ml_per_hour: float | None,
        notes: str,
    ) -> MeasurementStage:
        cleaned_name = self._validate_name(name, "stage name")
        cleaned_type = self._validate_name(stage_type, "stage type")
        self._validate_optional_target(target_pressure_bar, "target pressure")
        self._validate_optional_target(target_flow_ml_per_hour, "target flow")
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE measurement_stages SET
                    name = ?, stage_type = ?, fluid = ?, target_pressure_bar = ?,
                    target_flow_ml_per_hour = ?, notes = ?
                WHERE id = ?
                """,
                (
                    cleaned_name,
                    cleaned_type,
                    fluid.strip(),
                    target_pressure_bar,
                    target_flow_ml_per_hour,
                    notes,
                    stage_id,
                ),
            )
        if cursor.rowcount == 0:
            raise KeyError(f"stage {stage_id} does not exist")
        row = self._connection.execute(
            "SELECT * FROM measurement_stages WHERE id = ?", (stage_id,)
        ).fetchone()
        if row is None:
            raise RuntimeError("renamed stage disappeared")
        return self._stage_from_row(row)

    def move_stage(self, stage_id: int, offset: int) -> MeasurementStage:
        if offset not in {-1, 1}:
            raise ValueError("stage move offset must be -1 or 1")
        stage = self._require_stage(stage_id)
        stages = list(self.list_stages(stage.project_id))
        current = next(index for index, item in enumerate(stages) if item.id == stage_id)
        target = current + offset
        if not 0 <= target < len(stages):
            return stage
        stages[current], stages[target] = stages[target], stages[current]
        self._write_stage_order(stages)
        return self._require_stage(stage_id)

    def delete_stage(self, stage_id: int) -> None:
        stage = self._require_stage(stage_id)
        with self._connection:
            self._connection.execute("DELETE FROM measurement_stages WHERE id = ?", (stage_id,))
        self._write_stage_order(list(self.list_stages(stage.project_id)))

    def list_stages(self, project_id: int) -> tuple[MeasurementStage, ...]:
        self._require_project(project_id)
        rows = self._connection.execute(
            "SELECT * FROM measurement_stages WHERE project_id = ? ORDER BY position",
            (project_id,),
        ).fetchall()
        return tuple(self._stage_from_row(row) for row in rows)

    def get_stage(self, stage_id: int) -> MeasurementStage:
        return self._require_stage(stage_id)

    def _require_project(self, project_id: int) -> None:
        row = self._connection.execute(
            "SELECT 1 FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"project {project_id} does not exist")

    def _require_stage(self, stage_id: int) -> MeasurementStage:
        row = self._connection.execute(
            "SELECT * FROM measurement_stages WHERE id = ?", (stage_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"stage {stage_id} does not exist")
        return self._stage_from_row(row)

    def _write_stage_order(self, stages: list[MeasurementStage]) -> None:
        with self._connection:
            for stage in stages:
                self._connection.execute(
                    "UPDATE measurement_stages SET position = position + 100000 WHERE id = ?",
                    (stage.id,),
                )
            for position, stage in enumerate(stages):
                self._connection.execute(
                    "UPDATE measurement_stages SET position = ? WHERE id = ?",
                    (position, stage.id),
                )

    @staticmethod
    def _project_from_row(
        row: sqlite3.Row, stages: tuple[MeasurementStage, ...]
    ) -> MeasurementProject:
        configuration = json.loads(cast(str, row["configuration_json"]))
        calibration = json.loads(cast(str, row["calibration_snapshot_json"]))
        if not isinstance(configuration, dict) or not isinstance(calibration, dict):
            raise ValueError("project snapshots must be JSON objects")
        return MeasurementProject(
            id=cast(int, row["id"]),
            name=cast(str, row["name"]),
            created_at=datetime.fromisoformat(cast(str, row["created_at_utc"])),
            operator=cast(str, row["operator"]),
            notes=cast(str, row["notes"]),
            configuration=cast(dict[str, object], configuration),
            calibration_snapshot=cast(dict[str, object], calibration),
            stages=stages,
        )

    @staticmethod
    def _stage_from_row(row: sqlite3.Row) -> MeasurementStage:
        return MeasurementStage(
            id=cast(int, row["id"]),
            project_id=cast(int, row["project_id"]),
            name=cast(str, row["name"]),
            position=cast(int, row["position"]),
            created_at=datetime.fromisoformat(cast(str, row["created_at_utc"])),
            stage_type=cast(str, row["stage_type"]),
            fluid=cast(str, row["fluid"]),
            target_pressure_bar=cast(float | None, row["target_pressure_bar"]),
            target_flow_ml_per_hour=cast(float | None, row["target_flow_ml_per_hour"]),
            notes=cast(str, row["notes"]),
        )

    @staticmethod
    def _validate_name(name: str, label: str) -> str:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError(f"{label} must not be empty")
        return cleaned

    @staticmethod
    def _validate_optional_target(value: float | None, label: str) -> None:
        if value is not None and (not isfinite(value) or value < 0.0):
            raise ValueError(f"{label} must be nonnegative and finite")

    @staticmethod
    def _as_utc(timestamp: datetime) -> datetime:
        if timestamp.tzinfo is None:
            raise ValueError("timestamp must include timezone information")
        return timestamp.astimezone(UTC)

    @staticmethod
    def _serialize_snapshot(snapshot: Mapping[str, object]) -> str:
        try:
            return json.dumps(snapshot, ensure_ascii=False, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError("snapshot must contain finite JSON-compatible values") from error

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "ProjectRepository":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
